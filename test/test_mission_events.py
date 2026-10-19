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
    assert value["mission"]["missions"]["WEEKLY"]["weekly_701"]["state"] == 2


def test_partial_and_complete_periodic_rows_remain_client_visible():
    value = player()
    saturday = ts(2026, 10, 17, 8)
    reconcile_visible_missions(value, saturday)
    daily = value["mission"]["missions"]["DAILY"]
    assert daily["daily_7101"]["state"] == 2

    apply_mission_event(
        value,
        "battle_success",
        {"stageId": "main_00-02", "stars": 3, "killCnt": 20},
        now=saturday,
    )
    apply_mission_event(
        value,
        "battle_result",
        {"stageId": "main_00-02", "stars": 3, "killCnt": 20},
        now=saturday,
    )
    assert daily["daily_7101"]["state"] == 2
    assert daily["daily_7108"]["progress"][0]["value"] == 20
    assert daily["daily_7108"]["state"] == 2


def test_legacy_state_one_is_repaired_without_changing_progress_or_claims():
    value = player()
    saturday = ts(2026, 10, 17, 8)
    reconcile_visible_missions(value, saturday)
    daily = value["mission"]["missions"]["DAILY"]
    partial = daily["daily_7108"].copy()
    partial_rows = partial["progress"].copy()
    partial_rows[0]["value"] = 56
    partial["progress"] = partial_rows
    partial["state"] = 1
    daily["daily_7108"] = partial
    complete = daily["daily_7101"].copy()
    complete_rows = complete["progress"].copy()
    complete_rows[0]["value"] = 1
    complete["progress"] = complete_rows
    complete["state"] = 2
    daily["daily_7101"] = complete

    changes = reconcile_visible_missions(value, saturday)

    assert daily["daily_7108"]["progress"][0]["value"] == 56
    assert daily["daily_7108"]["state"] == 2
    assert daily["daily_7101"]["state"] == 2
    assert any(
        row["kind"] == "mission_progress_reconciled"
        and row["missionId"] == "daily_7108"
        for row in changes
    )


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
    first = await mission_exchangeMissionRewards.__wrapped__(value, request({"targetRewardsId": "reward_daily_692"}))
    second = await mission_exchangeMissionRewards.__wrapped__(value, request({"targetRewardsId": "reward_daily_692"}))
    assert first["items"] == [{"type": "GOLD", "id": "4001", "count": 500}]
    assert second["items"] == []
    assert value["status"]["gold"] == before + 500
    assert value["mission"]["missionRewards"]["rewards"]["DAILY"]["reward_daily_692"] == 1


def test_active_periodic_reward_states_are_initialized_for_client_list():
    value = player()
    saturday = ts(2026, 10, 17, 8)
    changes = reconcile_visible_missions(value, saturday)

    daily = value["mission"]["missionRewards"]["rewards"]["DAILY"]
    weekly = value["mission"]["missionRewards"]["rewards"]["WEEKLY"]
    assert daily["reward_daily_692"] == 0
    assert daily["reward_daily_701"] == 0
    assert weekly["reward_weekly_401"] == 0
    assert weekly["reward_weekly_413"] == 0
    assert sum(row["kind"] == "periodic_reward_state_initialized" for row in changes) == 23


@pytest.mark.asyncio
async def test_weekly_task_and_milestone_claims_are_independent_and_idempotent(monkeypatch):
    value = player()
    saturday = ts(2026, 10, 17, 8)
    monkeypatch.setattr("openbachelors.util.mission_lifecycle.faketime", lambda: saturday)
    reconcile_visible_missions(value, saturday)
    weekly_missions = value["mission"]["missions"]["WEEKLY"]
    weekly = weekly_missions["weekly_714"].copy()
    weekly_rows = weekly["progress"].copy()
    weekly_rows[0]["value"] = weekly_rows[0]["target"]
    weekly["progress"] = weekly_rows
    weekly["state"] = 2
    weekly_missions["weekly_714"] = weekly

    first_task = _claim_mission(value, "weekly_714", "WEEKLY")
    second_task = _claim_mission(value, "weekly_714", "WEEKLY")
    assert first_task["reason"] is None
    assert second_task["reason"] == "already_claimed"
    assert value["mission"]["missionRewards"]["weeklyPoint"] == 1

    before = value["status"]["gold"]
    first_reward = await mission_exchangeMissionRewards.__wrapped__(
        value, request({"targetRewardsId": "reward_weekly_401"})
    )
    second_reward = await mission_exchangeMissionRewards.__wrapped__(
        value, request({"targetRewardsId": "reward_weekly_401"})
    )
    assert first_reward["items"] == [
        {"type": "GOLD", "id": "4001", "count": 1000},
        {"type": "CARD_EXP", "id": "2001", "count": 4},
    ]
    assert second_reward["items"] == []
    assert value["status"]["gold"] == before + 1000
    assert value["mission"]["missionRewards"]["rewards"]["WEEKLY"]["reward_weekly_401"] == 1


@pytest.mark.asyncio
async def test_periodical_milestone_below_threshold_is_not_claimable(monkeypatch):
    value = player()
    saturday = ts(2026, 10, 17, 8)
    monkeypatch.setattr("openbachelors.util.mission_lifecycle.faketime", lambda: saturday)
    reconcile_visible_missions(value, saturday)
    value["mission"]["missionRewards"]["dailyPoint"] = 2
    before = value["inventory"].copy()

    response = await mission_exchangeMissionRewards.__wrapped__(
        value, request({"targetRewardsId": "reward_daily_693"})
    )

    assert response["items"] == []
    assert value["inventory"].copy() == before
    assert value["mission"]["missionRewards"]["rewards"]["DAILY"]["reward_daily_693"] == 0


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
