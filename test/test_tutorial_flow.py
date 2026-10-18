import json
import logging

import pytest
from starlette.requests import Request

from openbachelors.bp.bp_gacha import grant_gacha_character
from openbachelors.bp.bp_storyreview import story_finishStory
from openbachelors.util import tutorial_debug
from openbachelors.util.tutorial_debug import player_snapshot
from openbachelors.util.building_state import build_or_upgrade_room, clean_room_slot
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
)


def make_request(payload, secret="tutorial-test-secret", path="/"):
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


@pytest.mark.asyncio
async def test_finishing_tutorial_story_advances_and_persists_progress():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    story_id = "obt/guide/l0-0/0_home_ui"

    response = await story_finishStory.__wrapped__(
        player, make_request({"storyId": story_id}, path="/story/finishStory")
    )

    assert response == {"items": []}
    assert player["status"]["progress"] == 10
    assert player["status"]["flags"][story_id] == 1

    reloaded = OverlayJson(post_tutorial_player_data_template, delta)
    assert reloaded["status"]["progress"] == 10
    assert reloaded["status"]["flags"][story_id] == 1
    await story_finishStory.__wrapped__(
        reloaded, make_request({"storyId": story_id}, path="/story/finishStory")
    )
    assert reloaded["status"]["progress"] == 10


@pytest.mark.asyncio
async def test_finishing_trade_station_story_atomically_creates_tutorial_order():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    player["inventory"]["3131"] = 30
    for slot_id in ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24"):
        clean_room_slot(player, slot_id)
    for slot_id, room_type in (
        ("slot_26", "POWER"),
        ("slot_25", "MANUFACTURE"),
        ("slot_24", "TRADING"),
    ):
        build_or_upgrade_room(player, slot_id, room_type, 1)
    player["building"]["roomSlots"]["slot_24"]["charInstIds"] = [502]
    player["building"]["chars"]["502"]["roomSlotId"] = "slot_24"
    player["building"]["chars"]["502"]["index"] = 0
    player["status"]["progress"] = 1110

    story_id = "obt/guide/l0-11/3c_station_trade"
    response = await story_finishStory.__wrapped__(
        player, make_request({"storyId": story_id}, path="/story/finishStory")
    )
    assert response == {"items": []}
    assert player["status"]["progress"] == 1111
    assert player["status"]["flags"][story_id] == 1
    stock = player["building"]["rooms"]["TRADING"]["slot_24"]["stock"]
    assert len(stock) == 1

    reloaded = OverlayJson(post_tutorial_player_data_template, delta)
    assert reloaded["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == stock.copy()
    await story_finishStory.__wrapped__(
        reloaded, make_request({"storyId": story_id}, path="/story/finishStory")
    )
    assert reloaded["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == stock.copy()
    assert reloaded["status"]["flags"][story_id] == 1

    await story_finishStory.__wrapped__(
        reloaded, make_request({"storyId": story_id}, path="/story/finishStory")
    )
    assert reloaded["status"]["progress"] == 1111


def test_gacha_character_is_added_once_and_survives_reload():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    char_id = "char_120_hibisc"
    before_count = len(player["troop"]["chars"])

    first = grant_gacha_character(player, char_id)
    assert first["isNew"] == 1
    assert len(player["troop"]["chars"]) == before_count + 1
    inst_id = str(first["charInstId"])
    assert player["troop"]["chars"][inst_id]["charId"] == char_id
    assert player["troop"]["chars"][inst_id]["currentEquip"] is None
    assert player["troop"]["chars"][inst_id]["equip"].copy() == {}
    assert char_id in player["troop"]["charGroup"]
    assert inst_id in player["building"]["chars"]
    assert player["dexNav"]["character"][char_id]["charInstId"] == int(inst_id)

    reloaded = OverlayJson(post_tutorial_player_data_template, delta)
    assert reloaded["troop"]["chars"][inst_id]["charId"] == char_id

    duplicate = grant_gacha_character(reloaded, char_id)
    assert duplicate["isNew"] == 0
    assert duplicate["charInstId"] == int(inst_id)
    assert len(reloaded["troop"]["chars"]) == before_count + 1


def test_initial_stage_is_enterable_but_not_cleared_and_later_stages_stay_locked():
    preset = post_tutorial_player_data_template.copy()
    stages = preset["dungeon"]["stages"]

    assert set(stages) == {"main_00-01"}
    assert stages["main_00-01"] == {
        "stageId": "main_00-01",
        "completeTimes": 0,
        "startTimes": 0,
        "practiceTimes": 0,
        "state": 0,
        "hasBattleReplay": 0,
        "noCostCnt": 0,
    }
    assert preset["status"]["mainStageProgress"] is None


def test_existing_overlay_is_not_reset_by_initial_stage_default():
    delta = DeltaJson()
    player = OverlayJson(post_tutorial_player_data_template, delta)
    player["status"]["gold"] = 123
    stage = player["dungeon"]["stages"]["main_00-01"]
    stage["state"] = 2
    stage["completeTimes"] = 1
    stage["startTimes"] = 1

    reloaded = OverlayJson(post_tutorial_player_data_template, delta)

    assert reloaded["status"]["gold"] == 123
    assert reloaded["dungeon"]["stages"]["main_00-01"]["state"] == 2
    assert reloaded["dungeon"]["stages"]["main_00-01"]["completeTimes"] == 1


def test_read_only_nested_access_does_not_emit_empty_delta_maps():
    delta = DeltaJson()
    pending = DeltaJson()
    base = OverlayJson(post_tutorial_player_data_template, delta)
    player = OverlayJson(base, pending)

    assert player["status"]["progress"] == 0
    assert "main_00-01" in player["dungeon"]["stages"]
    delta.prune_empty_branches()
    pending.prune_empty_branches()

    assert delta.modified_dict == {}
    assert delta.deleted_dict == {}
    assert pending.modified_dict == {}
    assert pending.deleted_dict == {}


def test_progress_30_diagnostics_point_to_battle_guide_and_unpassed_first_stage():
    player = OverlayJson(post_tutorial_player_data_template, DeltaJson())
    player["status"]["progress"] = 30
    for story_id in (
        "obt/guide/l0-0/0_home_ui",
        "obt/guide/l0-0/1_recruit_adv",
        "obt/guide/l0-0/2_make_squad",
    ):
        player["status"]["flags"][story_id] = 1

    snapshot = player_snapshot(player)

    assert snapshot["firstUnlockedStage"] == "main_00-01"
    assert snapshot["stageState"] == 0
    assert snapshot["mainStageProgress"] is None
    assert snapshot["nextGuideStory"] == "obt/guide/l0-0/3_battle_ui"


def test_tutorial_debug_log_is_structured_and_redacts_account(monkeypatch):
    monkeypatch.setattr(tutorial_debug, "_enabled", lambda: True)
    request = make_request({}, secret="never-log-this", path="/account/syncData")

    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    capture = Capture()
    tutorial_debug.logger.addHandler(capture)
    tutorial_debug.logger.setLevel(logging.DEBUG)
    try:
        tutorial_debug.event(
            request,
            "test_event",
            stageId="main_00-01",
            clientInternalCondition="unknown",
        )
    finally:
        tutorial_debug.logger.removeHandler(capture)

    payload = json.loads(capture.messages[-1])
    assert payload["event"] == "test_event"
    assert payload["stageId"] == "main_00-01"
    assert payload["clientInternalCondition"] == "unknown"
    assert payload["account"].startswith("acct_")
    assert "never-log-this" not in capture.messages[-1]
