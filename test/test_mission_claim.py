import json

import pytest
from starlette.requests import Request

from openbachelors.bp import bp_mission
from openbachelors.util import player_data as player_data_module
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    PlayerData,
    post_tutorial_player_data_template,
)


class FakeExtra:
    def __init__(self):
        self.save_obj = {}

    async def save(self):
        pass


def player():
    value = OverlayJson(post_tutorial_player_data_template, DeltaJson())
    value.extra_save = FakeExtra()
    value.preset = "post_tutorial"
    return value


def request(payload, path="/mission/autoConfirmMissions", secret="mission-e2e"):
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
            "path": path,
            "headers": [
                (b"content-type", b"application/json"),
                (b"secret", secret.encode()),
            ],
        },
        receive,
    )


def set_mission(value, mission_type, mission_id, state=2, progress=1):
    value["mission"]["missions"][mission_type][mission_id] = {
        "progress": [{"target": 1, "value": progress}],
        "state": state,
    }


def inventory_count(value, item_id):
    return value["inventory"][item_id] if item_id in value["inventory"] else 0


@pytest.mark.asyncio
async def test_auto_confirm_claims_multiple_main_missions_and_grants_rewards():
    value = player()
    set_mission(value, "MAIN", "main_1")
    set_mission(value, "MAIN", "main_2")
    records_before = inventory_count(value, "2001")
    gold_before = value["status"]["gold"]

    response = await bp_mission.mission_autoConfirmMissions.__wrapped__(
        value, request({"type": "MAIN"})
    )

    assert response["items"] == [
        {"type": "CARD_EXP", "id": "2001", "count": 5},
        {"type": "GOLD", "id": "4001", "count": 1000},
    ]
    assert value["inventory"]["2001"] == records_before + 5
    assert value["status"]["gold"] == gold_before + 1000
    assert value["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3
    assert value["mission"]["missions"]["MAIN"]["main_2"]["state"] == 3


@pytest.mark.asyncio
async def test_auto_confirm_claims_sub_chain_in_one_request():
    value = player()
    set_mission(value, "SUB", "sub_1")
    set_mission(value, "SUB", "sub_2")
    before = inventory_count(value, "2001")

    response = await bp_mission.mission_autoConfirmMissions.__wrapped__(
        value, request({"type": "SUB"})
    )

    assert response["items"] == [
        {"type": "CARD_EXP", "id": "2001", "count": 5},
        {"type": "CARD_EXP", "id": "2001", "count": 5},
    ]
    assert value["inventory"]["2001"] == before + 10
    assert value["mission"]["missions"]["SUB"]["sub_1"]["state"] == 3
    assert value["mission"]["missions"]["SUB"]["sub_2"]["state"] == 3


@pytest.mark.asyncio
async def test_single_claim_then_auto_confirm_does_not_repeat_reward():
    value = player()
    set_mission(value, "MAIN", "main_1")
    set_mission(value, "MAIN", "main_2")
    records_before = inventory_count(value, "2001")
    gold_before = value["status"]["gold"]

    single = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "main_1"}, "/mission/confirmMission")
    )
    batch = await bp_mission.mission_autoConfirmMissions.__wrapped__(
        value, request({"type": "MAIN"})
    )

    assert single["items"] == [{"type": "CARD_EXP", "id": "2001", "count": 5}]
    assert batch["items"] == [{"type": "GOLD", "id": "4001", "count": 1000}]
    assert value["inventory"]["2001"] == records_before + 5
    assert value["status"]["gold"] == gold_before + 1000


@pytest.mark.asyncio
async def test_auto_confirm_is_idempotent_and_empty_when_nothing_is_claimable():
    value = player()
    set_mission(value, "MAIN", "main_1")
    before = inventory_count(value, "2001")

    first = await bp_mission.mission_autoConfirmMissions.__wrapped__(
        value, request({"type": "MAIN"})
    )
    second = await bp_mission.mission_autoConfirmMissions.__wrapped__(
        value, request({"type": "MAIN"})
    )

    assert first["items"] == [{"type": "CARD_EXP", "id": "2001", "count": 5}]
    assert second == {"items": []}
    assert value["inventory"]["2001"] == before + 5


@pytest.mark.asyncio
async def test_auto_confirm_skips_claimed_and_incomplete_missions():
    value = player()
    set_mission(value, "MAIN", "main_1", state=3)
    set_mission(value, "MAIN", "main_2", state=2)
    set_mission(value, "MAIN", "main_3", state=1, progress=0)
    records_before = inventory_count(value, "2001")
    gold_before = value["status"]["gold"]

    response = await bp_mission.mission_autoConfirmMissions.__wrapped__(
        value, request({"type": "MAIN"})
    )

    assert response["items"] == [{"type": "GOLD", "id": "4001", "count": 1000}]
    assert inventory_count(value, "2001") == records_before
    assert value["status"]["gold"] == gold_before + 1000
    assert value["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3
    # Current PC rendering uses state 2 for both incomplete and claimable rows.
    assert value["mission"]["missions"]["MAIN"]["main_3"]["state"] == 2


@pytest.mark.asyncio
async def test_auto_confirm_http_delta_and_reload_are_consistent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        player_data_module, "MULTI_USER_SAV_DIRPATH", str(tmp_path / "multi_sav")
    )
    initial = await PlayerData.create(
        request=request({}, secret="mission-reload"), preset="post_tutorial"
    )
    set_mission(initial, "MAIN", "main_1")
    set_mission(initial, "MAIN", "main_2")
    await initial.save()

    response = await bp_mission.mission_autoConfirmMissions(
        request=request({"type": "MAIN"}, secret="mission-reload")
    )
    payload = json.loads(response.body)
    modified = payload["playerDataDelta"]["modified"]

    assert payload["items"] == [
        {"type": "CARD_EXP", "id": "2001", "count": 5},
        {"type": "GOLD", "id": "4001", "count": 1000},
    ]
    assert modified["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3
    assert modified["mission"]["missions"]["MAIN"]["main_2"]["state"] == 3
    assert modified["inventory"]["2001"] >= 5
    assert modified["status"]["gold"] >= 1000

    reloaded = await PlayerData.create(
        request=request({}, secret="mission-reload"), preset="post_tutorial"
    )
    assert reloaded["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3
    assert reloaded["mission"]["missions"]["MAIN"]["main_2"]["state"] == 3

    duplicate = await bp_mission.mission_autoConfirmMissions(
        request=request({"type": "MAIN"}, secret="mission-reload")
    )
    assert json.loads(duplicate.body)["items"] == []


def test_auto_confirm_route_is_registered():
    paths = {route.path for route in bp_mission.router.routes}
    assert "/mission/autoConfirmMissions" in paths
