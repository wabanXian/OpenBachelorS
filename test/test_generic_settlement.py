from copy import deepcopy
import json

import pytest
from starlette.requests import Request

from openbachelors.bp import bp_account, bp_mission
from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    _stage_record,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
)
from openbachelors.util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template
from openbachelors.util.settlement_rules import generated_rule_report, get_stage_rule


class FakeExtra:
    def __init__(self):
        self.save_obj = {}

    async def save(self):
        pass


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


def player():
    value = OverlayJson(post_tutorial_player_data_template, DeltaJson())
    value.extra_save = FakeExtra()
    value.preset = "post_tutorial"
    return value


def unlock(value, stage_id, prerequisites=()):
    for prerequisite, state in prerequisites:
        value["dungeon"]["stages"][prerequisite] = _stage_record(prerequisite, state)
    value["dungeon"]["stages"][stage_id] = _stage_record(stage_id)


def start(value, stage_id, ts):
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": stage_id,
            "startTs": ts,
            "battleType": 0,
            "usePracticeTicket": 0,
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    return session


def result(stage_id, stars, begin):
    return {
        "battleId": CLIENT_BATTLE_ID,
        "stageId": stage_id,
        "interrupt": 0,
        "giveUp": 0,
        "completeState": stars,
        "percent": 100 if stars in (2, 3) else 0,
        "killCnt": 1 if stars in (2, 3) else 0,
        "platform": 2,
        "battleData": {"beginTs": begin, "endTs": begin + 10},
    }


def finish(value, decoded):
    settlement = prepare_battle_finish(value, decoded, "cipher")
    assert settlement["kind"] == "prepared"
    commit_battle_finish(value, settlement["session"])
    return settlement


@pytest.mark.parametrize("stage_id", ["main_00-02", "main_00-03", "main_00-04", "main_01-03", "main_01-04"])
def test_representative_ordinary_stages_use_generated_rules(stage_id):
    rule, source = get_stage_rule(stage_id)
    assert source == "generated_ordinary_rule"
    assert rule["settlementEnabled"] is True
    assert rule["stageType"] in {"MAIN", "SUB"}


@pytest.mark.parametrize(
    "stage_id", ["main_00-02", "main_00-03", "main_00-04", "main_01-03", "sub_02-01"]
)
def test_representative_generated_stages_settle_without_python_stage_branches(stage_id):
    value = player()
    rule, _ = get_stage_rule(stage_id)
    prerequisites = [
        (
            condition["stageId"],
            3 if condition["completeState"] == "COMPLETE" else 2,
        )
        for condition in rule["unlockCondition"]
    ]
    unlock(value, stage_id, prerequisites)
    start(value, stage_id, 100)
    settlement = finish(value, result(stage_id, 3, 100))
    assert value["dungeon"]["stages"][stage_id]["state"] == 3
    assert value["dungeon"]["stages"][stage_id]["completeTimes"] == 1
    assert settlement["session"]["ruleSource"] == "generated_ordinary_rule"


@pytest.mark.parametrize("stars", [2, 3])
def test_zero_two_generic_first_clear_and_mission(stars):
    value = player()
    unlock(value, "main_00-02", [("tr_01", 3)])
    start(value, "main_00-02", 100)
    settlement = finish(value, result("main_00-02", stars, 100))

    assert value["dungeon"]["stages"]["main_00-02"]["state"] == stars
    assert value["dungeon"]["stages"]["main_00-02"]["completeTimes"] == 1
    assert value["mission"]["missions"]["MAIN"]["main_3"]["state"] == 2
    assert value["mission"]["missionGroups"]["main_3"] == 0
    assert any(item["id"] == "2001" for item in settlement["response"]["firstRewards"])
    assert settlement["session"]["ruleSource"] == "generated_ordinary_rule"
    assert settlement["session"]["rewards"]["approximations"]


def test_generic_two_star_upgrade_repeat_and_random_retry_are_idempotent():
    value = player()
    unlock(value, "main_00-02", [("tr_01", 3)])
    start(value, "main_00-02", 100)
    first_result = result("main_00-02", 2, 100)
    first = finish(value, first_result)
    first_inventory = value["inventory"].copy()
    duplicate = prepare_battle_finish(value, deepcopy(first_result), "different-iv")
    assert duplicate["kind"] == "duplicate"
    assert duplicate["response"] == first["response"]
    assert value["inventory"].copy() == first_inventory

    start(value, "main_00-02", 300)
    upgraded = finish(value, result("main_00-02", 3, 300))
    assert value["dungeon"]["stages"]["main_00-02"]["state"] == 3
    assert value["dungeon"]["stages"]["main_00-02"]["completeTimes"] == 2
    assert all(item["id"] != "2001" for item in upgraded["response"]["firstRewards"])


def test_old_settlement_cannot_finish_a_new_zero_battle_id_session():
    value = player()
    unlock(value, "main_00-02", [("tr_01", 3)])
    start(value, "main_00-02", 100)
    old_result = result("main_00-02", 3, 100)
    finish(value, old_result)
    start(value, "main_00-02", 300)

    rejected = prepare_battle_finish(value, deepcopy(old_result), "replayed")
    assert rejected == {
        "kind": "rejected",
        "reason": "replayed_settled_result_for_active_session",
    }


def test_unmet_prerequisite_rejects_even_if_stage_record_exists():
    value = player()
    unlock(value, "main_00-02", [("tr_01", 0)])
    session, reason = prepare_battle_start(
        value,
        {"stageId": "main_00-02", "startTs": 100, "battleType": 0, "usePracticeTicket": 0},
    )
    assert session is None
    assert reason == "stage_unlock_condition_not_met"


def test_failure_refund_and_ap_clock_remainder_are_preserved():
    value = player()
    unlock(value, "main_00-02", [("tr_01", 3)])
    value["status"]["ap"] = 70
    value["status"]["lastApAddTime"] = 1000
    before = value["status"]["ap"]
    start(value, "main_00-02", 100)
    assert value["status"]["lastApAddTime"] == 1000
    failed = finish(value, result("main_00-02", 1, 100))
    assert value["status"]["ap"] == before
    assert failed["response"]["apFailReturn"] > 0


def test_ap_recovery_uses_resource_interval_and_preserves_remainder():
    value = player()
    value["status"]["ap"] = 70
    value["status"]["lastApAddTime"] = 1000
    recovery = bp_account.recover_ap(value, 1000 + 2 * 360 + 120)
    assert recovery["gained"] == 2
    assert recovery["intervalSeconds"] == 360
    assert value["status"]["ap"] == 72
    assert value["status"]["lastApAddTime"] == 1720


@pytest.mark.asyncio
async def test_generic_main_mission_claim_is_resource_driven_and_once_only():
    value = player()
    unlock(value, "main_00-02", [("tr_01", 3)])
    start(value, "main_00-02", 100)
    finish(value, result("main_00-02", 3, 100))
    before = value["inventory"]["2001"]
    first = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "main_3"})
    )
    second = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "main_3"})
    )
    assert first["items"] == [{"type": "CARD_EXP", "id": "2001", "count": 5}]
    assert second["items"] == []
    assert value["inventory"]["2001"] == before + 5


@pytest.mark.asyncio
async def test_training_two_special_rule_grants_adnachiel_unlocks_and_exposes_sub_mission():
    value = player()
    unlock(value, "tr_02", [("main_00-02", 2)])
    ap_before = value["status"]["ap"]
    start(value, "tr_02", 100)
    settlement = finish(value, result("tr_02", 3, 100))

    assert value["status"]["ap"] == ap_before
    assert value["dungeon"]["stages"]["tr_02"]["state"] == 3
    assert value["dungeon"]["stages"]["main_00-03"]["state"] == 0
    assert settlement["response"]["unlockStages"] == ["main_00-03"]
    char_reward = next(
        item for item in settlement["response"]["firstRewards"]
        if item["type"] == "CHAR"
    )
    assert char_reward["charGet"]["charId"] == "char_211_adnach"
    assert any(
        char["charId"] == "char_211_adnach"
        for _, char in value["troop"]["chars"]
    )
    assert value["mission"]["missions"]["SUB"]["sub_1"]["state"] == 2
    assert value["mission"]["missionGroups"]["sub_g_1"] == 0

    records_before = value["inventory"]["2001"] if "2001" in value["inventory"] else 0
    claim = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "sub_1"})
    )
    duplicate = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "sub_1"})
    )
    assert claim["items"] == [{"type": "CARD_EXP", "id": "2001", "count": 5}]
    assert duplicate["items"] == []
    assert value["inventory"]["2001"] == records_before + 5
    assert value["mission"]["missionGroups"]["sub_g_1"] == 1


@pytest.mark.asyncio
async def test_training_three_special_rule_grants_jessica_unlocks_and_exposes_sub_mission():
    value = player()
    value["mission"]["missions"]["SUB"]["sub_1"] = {
        "progress": [{"target": 1, "value": 1}],
        "state": 3,
    }
    unlock(value, "tr_03", [("main_00-03", 2)])
    start(value, "tr_03", 100)
    settlement = finish(value, result("tr_03", 3, 100))

    assert value["dungeon"]["stages"]["tr_03"]["state"] == 3
    assert value["dungeon"]["stages"]["main_00-04"]["state"] == 0
    assert settlement["response"]["unlockStages"] == ["main_00-04"]
    char_reward = next(
        item for item in settlement["response"]["firstRewards"]
        if item["type"] == "CHAR"
    )
    assert char_reward["charGet"]["charId"] == "char_235_jesica"
    assert any(
        char["charId"] == "char_235_jesica"
        for _, char in value["troop"]["chars"]
    )
    assert value["mission"]["missions"]["SUB"]["sub_2"]["state"] == 2
    assert value["mission"]["missionGroups"]["sub_g_1"] == 0

    records_before = value["inventory"]["2001"] if "2001" in value["inventory"] else 0
    claim = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "sub_2"})
    )
    duplicate = await bp_mission.mission_confirmMission.__wrapped__(
        value, request({"missionId": "sub_2"})
    )
    assert claim["items"] == [{"type": "CARD_EXP", "id": "2001", "count": 5}]
    assert duplicate["items"] == []
    assert value["inventory"]["2001"] == records_before + 5


def test_coverage_report_has_conservative_boundaries():
    report = generated_rule_report()
    assert report["ordinaryStageCount"] == 255
    assert report["settlementEnabledCount"] == 239
    assert report["completeRuleCount"] == 238
    assert report["approximateDropStageCount"] == 254
    assert "main_12-14" in report["stagesWithoutExactMatrix"]
    assert len(report["stagesWithUnconfirmedFirstPassRewards"]) == 16
