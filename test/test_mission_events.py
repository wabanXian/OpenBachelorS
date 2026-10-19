from datetime import datetime, timezone
import json

import pytest
from starlette.requests import Request

from openbachelors.bp.bp_gacha import gacha_finishNormalGacha
from openbachelors.bp.bp_mission import _claim_mission, mission_exchangeMissionRewards
from openbachelors.util.mission_lifecycle import (
    apply_mission_event,
    reconcile_visible_missions,
)
from openbachelors.util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template


class Extra:
    def __init__(self):
        self.save_obj = {}


def player(progress=1000):
    value = OverlayJson(post_tutorial_player_data_template, DeltaJson())
    value.extra_save = Extra()
    value["status"]["progress"] = progress
    return value


def ts(year, month, day, hour):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def request(payload):
    body = json.dumps(payload).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/mission/exchangeMissionRewards", "headers": []}, receive)


def endpoint_request(path, payload):
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": []},
        receive,
    )


def test_guide_four_uses_three_stars_not_stage_id():
    value = player(0)
    reconcile_visible_missions(value)
    apply_mission_event(value, "battle_success", {"stageId": "main_00-01", "stars": 2, "killCnt": 11})
    assert value["mission"]["missions"]["GUIDE"]["guide_4"]["progress"][0]["value"] == 0
    apply_mission_event(value, "battle_success", {"stageId": "main_00-01", "stars": 3, "killCnt": 11})
    assert value["mission"]["missions"]["GUIDE"]["guide_4"]["progress"][0]["value"] == 1


def test_one_battle_updates_guide_daily_and_weekly():
    value = player()
    saturday = ts(2026, 10, 17, 8)
    reconcile_visible_missions(value, saturday)
    changed = apply_mission_event(value, "battle_success", {"stageId": "main_00-02", "stars": 3, "killCnt": 20}, event_id="battle-1", now=saturday)
    ids = {row["missionId"] for row in changed}
    assert {"guide_4", "daily_7101", "weekly_701"} <= ids


def test_upgrade_updates_daily_and_weekly_counts():
    value = player()
    saturday = ts(2026, 10, 17, 8)
    reconcile_visible_missions(value, saturday)
    apply_mission_event(value, "upgrade_char", {"count": 1}, now=saturday)
    assert value["mission"]["missions"]["DAILY"]["daily_7114"]["progress"][0]["value"] == 1
    assert value["mission"]["missions"]["WEEKLY"]["weekly_714"]["progress"][0]["value"] == 1


def test_daily_claim_adds_point_once(monkeypatch):
    value = player()
    saturday = ts(2026, 10, 17, 8)
    monkeypatch.setattr("openbachelors.util.mission_lifecycle.faketime", lambda: saturday)
    reconcile_visible_missions(value, saturday)
    apply_mission_event(value, "battle_success", {"stageId": "main_00-02", "stars": 3}, now=saturday)
    first = _claim_mission(value, "daily_7101", "DAILY")
    second = _claim_mission(value, "daily_7101", "DAILY")
    assert first["reason"] is None
    assert second["reason"] == "already_claimed"
    assert value["mission"]["missionRewards"]["dailyPoint"] == 1


def test_four_am_daily_and_monday_weekly_boundaries():
    value = player()
    sunday = ts(2026, 10, 18, 19)  # Monday 03:00 China, still previous logical week
    monday = ts(2026, 10, 18, 21)  # Monday 05:00 China, new logical week
    reconcile_visible_missions(value, sunday)
    apply_mission_event(value, "upgrade_char", {"count": 1}, now=sunday)
    assert value["mission"]["missions"]["WEEKLY"]["weekly_714"]["progress"][0]["value"] == 1
    reconcile_visible_missions(value, monday)
    assert value["mission"]["missions"]["WEEKLY"]["weekly_714"]["progress"][0]["value"] == 0
    assert value["mission"]["missionRewards"]["weeklyPoint"] == 0


def test_periodic_progress_survives_overlay_reload():
    delta = DeltaJson()
    extra = Extra()
    value = OverlayJson(post_tutorial_player_data_template, delta)
    value.extra_save = extra
    value["status"]["progress"] = 1000
    saturday = ts(2026, 10, 17, 8)
    reconcile_visible_missions(value, saturday)
    apply_mission_event(value, "upgrade_char", {"count": 1}, now=saturday)

    reloaded = OverlayJson(post_tutorial_player_data_template, delta)
    reloaded.extra_save = extra
    reconcile_visible_missions(reloaded, saturday)
    assert reloaded["mission"]["missions"]["DAILY"]["daily_7114"]["progress"][0]["value"] == 1
    assert reloaded["mission"]["missions"]["WEEKLY"]["weekly_714"]["progress"][0]["value"] == 1


def test_periodic_tasks_remain_hidden_before_unlock_story():
    value = player(900)
    reconcile_visible_missions(value, ts(2026, 10, 17, 8))
    assert value["mission"]["missions"]["DAILY"].copy() == {}
    assert value["mission"]["missions"]["WEEKLY"].copy() == {}


@pytest.mark.asyncio
async def test_periodical_reward_is_granted_once(monkeypatch):
    value = player()
    saturday = ts(2026, 10, 17, 8)
    monkeypatch.setattr("openbachelors.util.mission_lifecycle.faketime", lambda: saturday)
    reconcile_visible_missions(value)
    value["mission"]["missionRewards"]["dailyPoint"] = 1
    before = value["status"]["gold"]
    first = await mission_exchangeMissionRewards.__wrapped__(value, request({"type": "DAILY", "rewardId": "reward_daily_692"}))
    second = await mission_exchangeMissionRewards.__wrapped__(value, request({"type": "DAILY", "rewardId": "reward_daily_692"}))
    assert first["items"] == [{"type": "GOLD", "id": "4001", "count": 500}]
    assert second["items"] == []
    assert value["status"]["gold"] == before + 500


@pytest.mark.asyncio
async def test_public_recruit_grants_operator_and_advances_normal_gacha_once(monkeypatch):
    value = player()
    saturday = ts(2026, 10, 17, 8)
    monkeypatch.setattr("openbachelors.util.mission_lifecycle.faketime", lambda: saturday)
    reconcile_visible_missions(value, saturday)
    slot = value["recruit"]["normal"]["slots"]["0"]
    slot["state"] = 3
    value.extra_save.save_obj["normal_gacha_0"] = "char_123_fang"
    before = len(value["troop"]["chars"])

    first = await gacha_finishNormalGacha.__wrapped__(
        value,
        endpoint_request("/gacha/finishNormalGacha", {"slotId": 0}),
    )
    second = await gacha_finishNormalGacha.__wrapped__(
        value,
        endpoint_request("/gacha/finishNormalGacha", {"slotId": 0}),
    )

    assert first["result"] == 0
    assert first["charGet"]["charId"] == "char_123_fang"
    assert len(value["troop"]["chars"]) == before + 1
    assert second == {"result": 1}
    assert value["mission"]["missions"]["DAILY"]["daily_7117"]["progress"][0]["value"] == 1
