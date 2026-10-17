from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from openbachelors.bp import bp_quest
from openbachelors.bp import bp_mission
from openbachelors.util import player_data as player_data_module

from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    SESSION_KEY,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
    reconcile_pending_session,
    repair_missing_main_one_mission,
)
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
    PlayerData,
)
from openbachelors.util.tutorial_debug import player_snapshot


class FakeExtra:
    def __init__(self, value=None):
        self.save_obj = value or {}
        self.save_count = 0

    async def save(self):
        self.save_count += 1


def player(delta=None, extra=None):
    value = OverlayJson(post_tutorial_player_data_template, delta or DeltaJson())
    value.extra_save = extra or FakeExtra()
    value.preset = "post_tutorial"
    return value


def start(value, start_ts=100, stage_id="main_00-01"):
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": stage_id,
            "startTs": start_ts,
            "battleType": 0,
            "usePracticeTicket": 0,
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    return session


def decoded(state, *, interrupt=0, give_up=0, stage_id="main_00-01"):
    return {
        "battleId": CLIENT_BATTLE_ID,
        "stageId": stage_id,
        "interrupt": interrupt,
        "giveUp": give_up,
        "completeState": state,
        "percent": 100 if state in (2, 3) else 0,
        "killCnt": 11 if state in (2, 3) else 0,
        "platform": 2,
        "battleData": {"beginTs": 100, "endTs": 200},
    }


def finish(value, result):
    prepared = prepare_battle_finish(value, result, "ignored-ciphertext")
    assert prepared["kind"] == "prepared"
    commit_battle_finish(value, prepared["session"])
    return prepared


def total_item(response, item_id):
    return sum(
        item["count"]
        for key in ("rewards", "firstRewards", "additionalRewards")
        for item in response[key]
        if item["id"] == item_id
    )


def snapshot(value):
    value.delta_json.prune_empty_branches()
    return value.copy()


def request(payload, secret="battle-e2e"):
    body = json.dumps(payload).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/quest/test",
            "headers": [
                (b"content-type", b"application/json"),
                (b"secret", secret.encode()),
            ],
        },
        receive,
    )


@pytest.mark.parametrize("stars", [2, 3])
def test_first_clear_records_result_rewards_and_unlocks_training(stars):
    value = player()
    initial_ap = value["status"]["ap"]
    initial_gold = value["status"]["gold"]
    start(value)

    settled = finish(value, decoded(stars))
    response = settled["response"]
    stage = value["dungeon"]["stages"]["main_00-01"]

    assert stage["state"] == stars
    assert stage["startTimes"] == 1
    assert stage["completeTimes"] == 1
    assert value["status"]["ap"] == initial_ap - 6
    assert value["status"]["lastApAddTime"] != 1700000000
    assert value["status"]["exp"] == 60
    assert value["status"]["gold"] == initial_gold + 60
    assert value["status"]["mainStageProgress"] == "main_00-01"
    assert value["status"]["recruitLicense"] == 1
    assert response["unlockStages"] == ["tr_01"]
    assert value["mission"]["missions"]["MAIN"]["main_1"].copy() == {
        "progress": [{"target": 1, "value": 1}],
        "state": 2,
    }
    assert value["mission"]["missionGroups"]["main_1"] == 0
    assert value["dungeon"]["stages"]["tr_01"]["state"] == 0
    assert total_item(response, "2001") in (4, 5)
    assert value["inventory"]["2001"] == total_item(response, "2001")
    assert value["status"]["androidDiamond"] == int(stars == 3)
    value["status"]["progress"] = 100
    for story_id in (
        "obt/guide/l0-0/0_home_ui",
        "obt/guide/l0-0/1_recruit_adv",
        "obt/guide/l0-0/2_make_squad",
        "obt/guide/l0-0/3_battle_ui",
    ):
        value["status"]["flags"][story_id] = 1
    assert (
        player_snapshot(value)["nextGuideStory"]
        == "obt/guide/l0-1/0_mission_main"
    )


def test_two_star_then_three_star_upgrades_without_repeating_first_pass_reward():
    value = player()
    start(value, 100)
    first = finish(value, decoded(2))
    start(value, 300)
    upgraded_result = decoded(3)
    upgraded_result["battleData"] = {"beginTs": 300, "endTs": 400}
    upgraded = finish(value, upgraded_result)

    stage = value["dungeon"]["stages"]["main_00-01"]
    assert stage["state"] == 3
    assert stage["completeTimes"] == 2
    assert value["status"]["recruitLicense"] == 1
    assert value["status"]["androidDiamond"] == 1
    assert any(item["id"] == "7001" for item in first["response"]["firstRewards"])
    assert [item["id"] for item in upgraded["response"]["firstRewards"]] == ["4002"]


def test_later_lower_star_result_never_reduces_best_state():
    value = player()
    start(value, 100)
    finish(value, decoded(3))
    start(value, 300)
    lower = decoded(2)
    lower["battleData"] = {"beginTs": 300, "endTs": 400}
    response = finish(value, lower)["response"]

    assert value["dungeon"]["stages"]["main_00-01"]["state"] == 3
    assert response["firstRewards"] == []


@pytest.mark.parametrize(
    "result",
    [decoded(1, interrupt=1), decoded(1, give_up=1), decoded(1)],
)
def test_interruption_give_up_and_known_failure_refund_without_progress(result):
    value = player()
    initial_ap = value["status"]["ap"]
    start(value)
    settled = finish(value, result)

    stage = value["dungeon"]["stages"]["main_00-01"]
    assert stage["state"] == 0
    assert stage["completeTimes"] == 0
    assert value["status"]["ap"] == initial_ap
    assert settled["response"]["apFailReturn"] == 6
    assert settled["response"]["firstRewards"] == []


def test_duplicate_finish_is_cached_and_does_not_duplicate_assets():
    value = player()
    start(value)
    result = decoded(3)
    first = finish(value, result)
    before_duplicate = snapshot(value)

    duplicate = prepare_battle_finish(value, deepcopy(result), "new-iv")

    assert duplicate["kind"] == "duplicate"
    assert duplicate["response"] == first["response"]
    assert snapshot(value) == before_duplicate


def test_finish_without_active_session_is_rejected_without_mutation():
    value = player()
    before = snapshot(value)

    rejected = prepare_battle_finish(value, decoded(3), "cipher")

    assert rejected == {"kind": "rejected", "reason": "missing_active_session"}
    assert snapshot(value) == before


def test_second_distinct_start_cannot_replace_an_unsettled_session():
    value = player()
    first = start(value, 100)
    before = snapshot(value)

    second, reason = prepare_battle_start(
        value,
        {
            "stageId": "main_00-01",
            "startTs": 101,
            "battleType": 0,
            "usePracticeTicket": 0,
        },
    )

    assert second is None
    assert reason == "active_session_exists"
    assert value.extra_save.save_obj["quest_battle_sessions_v1"]["active"] is first
    assert snapshot(value) == before


@pytest.mark.asyncio
async def test_main_one_mission_reward_is_claimed_once():
    value = player()
    start(value)
    finish(value, decoded(2))
    before_records = value["inventory"]["2001"]

    first = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "main_1"})
    )
    second = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "main_1"})
    )

    assert first == {"items": [{"type": "CARD_EXP", "id": "2001", "count": 5}]}
    assert second == {"items": []}
    assert value["inventory"]["2001"] == before_records + 5
    assert value["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3
    assert value["mission"]["missionGroups"]["main_1"] == 1


def test_passed_legacy_save_gets_unclaimed_main_one_without_reward():
    value = player()
    value["dungeon"]["stages"]["main_00-01"]["state"] = 2
    before_records = (
        value["inventory"]["2001"] if "2001" in value["inventory"] else 0
    )

    assert repair_missing_main_one_mission(value) is True
    assert repair_missing_main_one_mission(value) is False
    assert value["mission"]["missions"]["MAIN"]["main_1"].copy() == {
        "progress": [{"target": 1, "value": 1}],
        "state": 2,
    }
    assert value["mission"]["missionGroups"]["main_1"] == 0
    after_records = (
        value["inventory"]["2001"] if "2001" in value["inventory"] else 0
    )
    assert after_records == before_records


@pytest.mark.asyncio
async def test_training_one_settlement_grants_operator_unlocks_and_creates_mission():
    value = player()
    start(value)
    finish(value, decoded(2))
    ap_after_zero_one = value["status"]["ap"]
    ap_clock_after_zero_one = value["status"]["lastApAddTime"]
    gold_before_claim = value["status"]["gold"]

    start(value, 300, "tr_01")
    training_result = decoded(3, stage_id="tr_01")
    training_result["battleData"] = {"beginTs": 300, "endTs": 400}
    settled = finish(value, training_result)

    assert value["status"]["ap"] == ap_after_zero_one
    assert value["status"]["lastApAddTime"] == ap_clock_after_zero_one
    assert value["dungeon"]["stages"]["tr_01"]["state"] == 3
    assert settled["response"]["unlockStages"] == ["main_00-02"]
    assert value["dungeon"]["stages"]["main_00-02"]["state"] == 0
    hibiscus = next(
        char
        for _, char in value["troop"]["chars"]
        if char["charId"] == "char_120_hibisc"
    )
    hibiscus_inst_id = str(hibiscus["instId"])
    assert hibiscus["equip"].copy() == {}
    assert hibiscus_inst_id in value["building"]["chars"]
    assert value["dexNav"]["character"]["char_120_hibisc"]["charInstId"] == int(
        hibiscus_inst_id
    )
    hibiscus_reward = next(
        item
        for item in settled["response"]["firstRewards"]
        if item["type"] == "CHAR"
    )
    assert "count" not in hibiscus_reward
    assert hibiscus_reward["charGet"] == {
        "charInstId": int(hibiscus_inst_id),
        "charId": "char_120_hibisc",
        "isNew": 1,
    }
    assert value["mission"]["missions"]["MAIN"]["main_2"]["state"] == 2
    assert value["mission"]["missionGroups"]["main_2"] == 0

    claim = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "main_2"})
    )
    assert claim == {"items": [{"type": "GOLD", "id": "4001", "count": 1000}]}
    assert value["status"]["gold"] == gold_before_claim + 1000


def test_duplicate_training_finish_repairs_legacy_cached_character_reward_shape():
    value = player()
    start(value)
    finish(value, decoded(2))
    start(value, 300, "tr_01")
    result = decoded(3, stage_id="tr_01")
    result["battleData"] = {"beginTs": 300, "endTs": 400}
    finish(value, result)
    cached = next(
        entry
        for entry in value.extra_save.save_obj[SESSION_KEY]["settled"].values()
        if any(
            item.get("type") == "CHAR"
            for item in entry["response"]["firstRewards"]
        )
    )
    cached_reward = cached["response"]["firstRewards"][0]
    cached_reward.clear()
    cached_reward.update({"type": "CHAR", "id": "char_120_hibisc", "count": 1})

    duplicate = prepare_battle_finish(value, deepcopy(result), "new-cipher")

    assert duplicate["kind"] == "duplicate"
    reward = duplicate["response"]["firstRewards"][0]
    assert "count" not in reward
    assert reward["charGet"]["charId"] == "char_120_hibisc"
    assert reward["charGet"]["charInstId"] == next(
        char["instId"]
        for _, char in value["troop"]["chars"]
        if char["charId"] == "char_120_hibisc"
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"stageId": "main_00-02"}, "stage_mismatch"),
        ({"battleId": "different"}, "battle_id_mismatch"),
        ({"completeState": 99}, "unknown_result"),
    ],
)
def test_mismatch_and_unknown_result_are_rejected_without_mutation(change, reason):
    value = player()
    start(value)
    before = snapshot(value)
    result = decoded(3)
    result.update(change)

    rejected = prepare_battle_finish(value, result, "cipher")

    assert rejected["kind"] == "rejected"
    assert rejected["reason"] == reason
    assert snapshot(value) == before


def test_restart_recovery_replays_absolute_settlement_and_duplicate_stays_idempotent():
    delta = DeltaJson()
    extra = FakeExtra()
    value = player(delta, extra)
    start(value)
    result = decoded(3)
    prepared = prepare_battle_finish(value, result, "cipher")
    assert prepared["session"]["phase"] == "settling"

    restarted = player(delta, FakeExtra(deepcopy(extra.save_obj)))
    assert reconcile_pending_session(restarted) == "settlement_recovered"
    before_duplicate = snapshot(restarted)
    assert restarted["dungeon"]["stages"]["main_00-01"]["state"] == 3

    duplicate = prepare_battle_finish(restarted, result, "different-iv")
    assert duplicate["kind"] == "duplicate"
    assert snapshot(restarted) == before_duplicate


@pytest.mark.asyncio
async def test_http_delta_and_reload_form_a_complete_zero_one_settlement(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        player_data_module, "MULTI_USER_SAV_DIRPATH", str(tmp_path / "multi_sav")
    )
    monkeypatch.setattr(bp_quest.battle_protocol_audit, "enabled", False)
    result = decoded(3)
    monkeypatch.setattr(bp_quest, "decode_battle_log", lambda *_: result)

    start_response = await bp_quest.quest_battleStart(
        request=request(
            {
                "stageId": "main_00-01",
                "startTs": 100,
                "battleType": 0,
                "usePracticeTicket": 0,
            }
        )
    )
    start_payload = json.loads(start_response.body)
    assert start_payload["result"] == 0
    assert start_payload["playerDataDelta"]["modified"]["status"]["ap"] == 76
    assert (
        start_payload["playerDataDelta"]["modified"]["status"]["lastApAddTime"]
        != 1700000000
    )

    finish_response = await bp_quest.quest_battleFinish(
        request=request({"data": "00", "battleData": {}})
    )
    finish_payload = json.loads(finish_response.body)
    modified = finish_payload["playerDataDelta"]["modified"]
    assert finish_payload["result"] == 0
    assert modified["dungeon"]["stages"]["main_00-01"]["state"] == 3
    assert modified["dungeon"]["stages"]["tr_01"]["state"] == 0
    assert modified["status"]["mainStageProgress"] == "main_00-01"
    assert finish_payload["unlockStages"] == ["tr_01"]

    reloaded = await PlayerData.create(
        request=request({}, secret="battle-e2e"), preset="post_tutorial"
    )
    assert reloaded["dungeon"]["stages"]["main_00-01"]["state"] == 3
    assert reloaded["dungeon"]["stages"]["tr_01"]["state"] == 0
    assert reloaded["status"]["recruitLicense"] == 1
    assert reloaded["status"]["androidDiamond"] == 1
    assert reloaded["inventory"]["2001"] in (4, 5)

