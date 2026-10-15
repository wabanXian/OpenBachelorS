import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbachelors.bp.bp_quest import quest_battleFinish, quest_battleStart
from openbachelors.util.battle_protocol_audit import (
    BattleProtocolAudit,
    battle_protocol_audit,
)


class FakeHeaders(dict):
    pass


def fake_request(secret="sensitive-token"):
    return SimpleNamespace(headers=FakeHeaders(secret=secret))


class EndpointRequest:
    def __init__(self, body, secret="sensitive-token"):
        self.body = body
        self.headers = FakeHeaders(secret=secret)

    async def json(self):
        return self.body


class FakePlayerData(dict):
    def __init__(self, value=None):
        super().__init__(value or {})
        self.extra_save = SimpleNamespace(save_obj={})


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def audit_path(request):
    path = Path("test") / f".battle_protocol_audit_test_{request.node.name}.jsonl"
    path.unlink(missing_ok=True)
    yield path
    path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_disabled_audit_does_not_write(audit_path):
    path = audit_path
    audit = BattleProtocolAudit(enabled=False, output_path=path)
    await audit.record_battle_start(
        fake_request(), {"stageId": "main_00-01"}, {"result": 0}
    )
    assert not path.exists()


@pytest.mark.asyncio
async def test_sequence_correlation_redaction_and_repeated_finish(
    audit_path, monkeypatch
):
    path = audit_path
    audit = BattleProtocolAudit(enabled=True, output_path=path)
    request = fake_request()
    decoded = {
        "stageId": "main_00-01",
        "completeState": 3,
        "nested": {"token": "do-not-log"},
    }
    monkeypatch.setattr(
        "openbachelors.util.battle_protocol_audit.decode_battle_log",
        lambda player_data, data: decoded,
    )

    await audit.record_battle_start(
        request,
        {"stageId": "main_00-01", "secret": "request-secret"},
        {"result": 0, "battleId": "client-contract-value"},
    )
    finish_request = {"data": "raw-encrypted-payload", "isCheat": "value"}
    await audit.record_battle_finish({}, request, finish_request, {"result": 0})
    await audit.record_battle_finish({}, request, finish_request, {"result": 0})
    await audit.record_replay_save(
        request, {"battleReplay": "large-base64-replay"}, {}
    )

    events = read_events(path)
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert len({event["audit_id"] for event in events}) == 1
    assert events[0]["request"]["secret"] == "<redacted>"
    assert events[1]["request"]["data"]["omitted"] is True
    assert "raw-encrypted-payload" not in path.read_text(encoding="utf-8")
    assert "sensitive-token" not in path.read_text(encoding="utf-8")
    assert events[1]["decoded_data"]["nested"]["token"] == "<redacted>"
    assert events[1]["correlation"] == "matched_battle_start"
    assert events[2]["correlation"] == "repeated_battle_finish"
    assert events[3]["correlation"] == "matched_recent_battle"
    assert events[0]["response"]["battleId"] == "client-contract-value"


@pytest.mark.asyncio
async def test_decode_failure_and_missing_start_are_non_fatal(audit_path, monkeypatch):
    path = audit_path
    audit = BattleProtocolAudit(enabled=True, output_path=path)
    monkeypatch.setattr(
        "openbachelors.util.battle_protocol_audit.decode_battle_log",
        lambda player_data, data: (_ for _ in ()).throw(ValueError("bad data")),
    )

    await audit.record_battle_finish(
        {}, fake_request(), {"data": "not-valid"}, {"result": 0}
    )

    event = read_events(path)[0]
    assert event["decode_error"] == "ValueError"
    assert event["correlation"] == "missing_battle_start"
    assert event["decoded_data"] is None


@pytest.mark.asyncio
async def test_consecutive_starts_and_story_are_distinguishable(audit_path):
    path = audit_path
    audit = BattleProtocolAudit(enabled=True, output_path=path)
    request = fake_request()
    await audit.record_battle_start(
        request, {"stageId": "main_00-01"}, {"result": 0}
    )
    await audit.record_battle_start(
        request, {"stageId": "main_00-02"}, {"result": 0}
    )
    await audit.record_story_finish(
        request, {"stageId": "story_stage"}, {"result": 0}
    )

    first, second, story = read_events(path)
    assert first["audit_id"] != second["audit_id"]
    assert second["superseded_audit_id"] == first["audit_id"]
    assert story["correlation"] == "standalone_story_finish"
    assert story["audit_id"] != second["audit_id"]


@pytest.mark.asyncio
async def test_audit_does_not_mutate_protocol_objects(audit_path):
    audit = BattleProtocolAudit(enabled=True, output_path=audit_path)
    request_json = {
        "stageId": "main_00-01",
        "nested": {"token": "secret", "values": [1, 2]},
    }
    response = {"result": 0, "battleId": "unchanged"}
    expected_request = json.loads(json.dumps(request_json))
    expected_response = json.loads(json.dumps(response))

    await audit.record_battle_start(
        fake_request(), request_json, response
    )

    assert request_json == expected_request
    assert response == expected_response


@pytest.mark.asyncio
async def test_output_failure_is_best_effort(audit_path):
    audit = BattleProtocolAudit(enabled=True, output_path=audit_path.parent)
    await audit.record_battle_start(
        fake_request(), {"stageId": "main_00-01"}, {"result": 0}
    )


@pytest.mark.asyncio
async def test_endpoint_responses_and_existing_save_write_are_unchanged(
    audit_path, monkeypatch
):
    monkeypatch.setattr(battle_protocol_audit, "enabled", True)
    monkeypatch.setattr(battle_protocol_audit, "output_path", audit_path)
    battle_protocol_audit.reset_runtime_state()
    player = FakePlayerData()

    start_response = await quest_battleStart.__wrapped__(
        player, EndpointRequest({"stageId": "main_00-01"})
    )
    player_after_start = dict(player)
    finish_response = await quest_battleFinish.__wrapped__(
        player, EndpointRequest({"data": "invalid-encrypted-data"})
    )

    assert start_response == {
        "result": 0,
        "battleId": "00000000-0000-0000-0000-000000000000",
        "apFailReturn": 0,
        "isApProtect": 0,
        "inApProtectPeriod": False,
        "notifyPowerScoreNotEnoughIfFailed": False,
    }
    assert player.extra_save.save_obj == {"cur_stage_id": "main_00-01"}
    assert dict(player) == player_after_start
    assert finish_response["result"] == 0
    assert finish_response["rewards"] == []
    assert finish_response["firstRewards"] == []
    assert finish_response["unlockStages"] == []
    events = read_events(audit_path)
    assert [event["endpoint"] for event in events] == [
        "/quest/battleStart",
        "/quest/battleFinish",
    ]
    assert events[1]["decode_error"] is not None
