from copy import deepcopy
import json

import pytest
from starlette.requests import Request

from openbachelors.bp import bp_mission
from openbachelors.const.filepath import (
    GENERATED_TRAINING_SETTLEMENT_RULES,
    MISSION_TABLE,
    STAGE_TABLE,
)
from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    _stage_record,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
    reconcile_pending_session,
)
from openbachelors.util.const_json_loader import const_json_loader
from openbachelors.util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template
from openbachelors.util.settlement_rules import get_stage_rule


TRAINING_IDS = [
    stage_id
    for stage_id, _ in const_json_loader[GENERATED_TRAINING_SETTLEMENT_RULES]["stages"]
]


class FakeExtra:
    def __init__(self, value=None):
        self.save_obj = value or {}

    async def save(self):
        pass


def player(delta=None, extra=None):
    value = OverlayJson(post_tutorial_player_data_template, delta or DeltaJson())
    value.extra_save = extra or FakeExtra()
    value.preset = "post_tutorial"
    return value


def unlock_training(value, stage_id):
    rule, source = get_stage_rule(stage_id)
    assert source == "generated_training_rule"
    for condition in rule["unlockCondition"]:
        state = 3 if condition["completeState"] == "COMPLETE" else 2
        value["dungeon"]["stages"][condition["stageId"]] = _stage_record(
            condition["stageId"], state
        )
    value["dungeon"]["stages"][stage_id] = _stage_record(stage_id)
    return rule


def start(value, stage_id, timestamp):
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": stage_id,
            "startTs": timestamp,
            "battleType": 0,
            "usePracticeTicket": 0,
            "squad": None,
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    return session


def result(stage_id, state, timestamp, interrupt=0):
    return {
        "battleId": CLIENT_BATTLE_ID,
        "stageId": stage_id,
        "interrupt": interrupt,
        "giveUp": 0,
        "completeState": state,
        "percent": 100 if state in (2, 3) else 0,
        "killCnt": 1 if state in (2, 3) else 0,
        "platform": 2,
        "battleData": {"beginTs": timestamp, "endTs": timestamp + 10},
    }


def finish(value, decoded):
    prepared = prepare_battle_finish(value, decoded, "cipher")
    assert prepared["kind"] == "prepared"
    commit_battle_finish(value, prepared["session"])
    return prepared


def request(payload):
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
            "path": "/mission/confirmMission",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def reward_balance(value, reward):
    if reward["type"] in {"CARD_EXP", "MATERIAL"}:
        return value["inventory"][reward["id"]] if reward["id"] in value["inventory"] else 0
    if reward["type"] == "GOLD":
        return value["status"]["gold"]
    raise AssertionError(f"unhandled test reward {reward}")


@pytest.mark.parametrize("stage_id", TRAINING_IDS)
def test_every_mainline_training_rule_loads_and_matches_resources(stage_id):
    rule, source = get_stage_rule(stage_id)
    stage = const_json_loader[STAGE_TABLE]["stages"][stage_id]
    assert source == "generated_training_rule"
    assert rule["settlementEnabled"] is True
    assert rule["apCost"] == stage["apCost"]
    assert rule["apFailReturn"] == stage["apFailReturn"]
    assert rule["expGain"] == stage["expGain"]
    assert rule["goldGain"] == stage["goldGain"]
    assert rule["diamondOnceDrop"] == stage["diamondOnceDrop"]


@pytest.mark.parametrize("stage_id", TRAINING_IDS)
def test_every_mainline_training_two_then_three_star_is_idempotent_and_reloadable(stage_id):
    delta = DeltaJson()
    extra = FakeExtra()
    value = player(delta, extra)
    rule = unlock_training(value, stage_id)
    ap_before = value["status"]["ap"]

    start(value, stage_id, 100)
    two_result = result(stage_id, 2, 100)
    two = finish(value, two_result)
    assert value["status"]["ap"] == ap_before - rule["apCost"]
    assert value["dungeon"]["stages"][stage_id]["state"] == 2
    assert value["dungeon"]["stages"][stage_id]["completeTimes"] == 1
    assert value["status"]["androidDiamond"] == 0

    for reward in rule["firstPassRewards"]:
        if reward["type"] == "CHAR":
            response_reward = next(
                item for item in two["response"]["firstRewards"]
                if item["type"] == "CHAR" and item["id"] == reward["id"]
            )
            assert response_reward["charGet"]["charId"] == reward["id"]
            char = next(
                char for _, char in value["troop"]["chars"]
                if char["charId"] == reward["id"]
            )
            assert char["currentEquip"] is None
            assert char["equip"].copy() == {}

    duplicate = prepare_battle_finish(value, deepcopy(two_result), "retry")
    assert duplicate["kind"] == "duplicate"
    assert value["dungeon"]["stages"][stage_id]["completeTimes"] == 1

    start(value, stage_id, 300)
    three = finish(value, result(stage_id, 3, 300))
    assert value["dungeon"]["stages"][stage_id]["state"] == 3
    assert value["dungeon"]["stages"][stage_id]["completeTimes"] == 2
    assert value["status"]["androidDiamond"] == rule["diamondOnceDrop"]
    assert all(item["type"] != "CHAR" for item in three["response"]["firstRewards"])

    actual_stage_ids = {item_id for item_id, _ in value["dungeon"]["stages"]}
    expected_unlocks = set()
    for candidate_id, candidate in const_json_loader[STAGE_TABLE]["stages"]:
        conditions = candidate["unlockCondition"].copy()
        if not any(condition["stageId"] == stage_id for condition in conditions):
            continue
        if all(
            condition["stageId"] in actual_stage_ids
            and value["dungeon"]["stages"][condition["stageId"]]["state"]
            >= (3 if condition["completeState"] == "COMPLETE" else 2)
            for condition in conditions
        ):
            expected_unlocks.add(candidate_id)
    assert expected_unlocks.issubset(actual_stage_ids)
    for unlocked_id in set(two["response"]["unlockStages"] + three["response"]["unlockStages"]):
        assert value["dungeon"]["stages"][unlocked_id]["state"] == 0

    restarted = player(delta, FakeExtra(deepcopy(extra.save_obj)))
    assert reconcile_pending_session(restarted) is None
    assert restarted["dungeon"]["stages"][stage_id]["state"] == 3
    assert restarted["dungeon"]["stages"][stage_id]["completeTimes"] == 2


@pytest.mark.parametrize("stage_id", TRAINING_IDS)
@pytest.mark.parametrize("interrupt", [0, 1])
def test_every_mainline_training_failure_or_interrupt_does_not_clear(stage_id, interrupt):
    value = player()
    rule = unlock_training(value, stage_id)
    before_ap = value["status"]["ap"]
    start(value, stage_id, 100)
    settled = finish(value, result(stage_id, 1, 100, interrupt=interrupt))
    assert value["dungeon"]["stages"][stage_id]["state"] == 0
    assert value["dungeon"]["stages"][stage_id]["completeTimes"] == 0
    assert value["status"]["ap"] == before_ap - rule["apCost"] + min(
        rule["apCost"], rule["apFailReturn"]
    )
    assert settled["response"]["firstRewards"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_id", TRAINING_IDS)
async def test_every_training_related_mission_claims_resource_reward_once(stage_id):
    value = player()
    rule = unlock_training(value, stage_id)
    for mission in rule["missions"]:
        for predecessor in mission["preMissionIds"]:
            resource = const_json_loader[MISSION_TABLE]["missions"][predecessor]
            value["mission"]["missions"][resource["type"]][predecessor] = {
                "progress": [{"target": 1, "value": 1}],
                "state": 3,
            }
    start(value, stage_id, 100)
    finish(value, result(stage_id, 3, 100))

    for mission in rule["missions"]:
        record = value["mission"]["missions"][mission["type"]][mission["id"]]
        assert record["state"] == 2
        before = [reward_balance(value, reward) for reward in mission["rewards"]]
        first = await bp_mission.mission_confirmMission.__wrapped__(
            value, request({"missionId": mission["id"]})
        )
        second = await bp_mission.mission_confirmMission.__wrapped__(
            value, request({"missionId": mission["id"]})
        )
        assert first["items"] == mission["rewards"]
        assert second["items"] == []
        after = [reward_balance(value, reward) for reward in mission["rewards"]]
        assert after == [old + reward["count"] for old, reward in zip(before, mission["rewards"])]


def test_special_and_activity_training_are_explicitly_outside_mainline_generator():
    for stage_id in ("tr_s_1", "tr_s_2", "tr_26", "tr_27", "tr_28", "lt_tr_01", "act11d0_tr01"):
        rule, reason = get_stage_rule(stage_id)
        assert rule is None
        assert reason == "stage_settlement_not_audited"


def test_training_scan_report_matches_current_resource_families():
    report = const_json_loader[GENERATED_TRAINING_SETTLEMENT_RULES]["report"]
    assert report["mainlineTrainingCount"] == 25
    assert report["supportedCount"] == 25
    assert report["ids"].copy() == [f"tr_{index:02d}" for index in range(1, 26)]
    assert report["unsupported"].copy() == {}
    assert report["specialMainTrainingExcluded"].copy() == ["tr_s_1", "tr_s_2"]
    assert report["activityNumberedTrainingExcluded"].copy() == [
        "tr_26", "tr_27", "tr_28"
    ]
