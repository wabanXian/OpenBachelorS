from copy import deepcopy

from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
)
from openbachelors.util.module_mission_lifecycle import (
    SUPPORTED_TEMPLATES,
    module_mission_target,
    plan_module_battle_missions,
    reconcile_module_missions,
)
from openbachelors.util.const_json_loader import const_json_loader
from openbachelors.const.filepath import UNIEQUIP_TABLE

from .test_battle_settlement import player


def _eligible_amiya(value):
    char = value["troop"]["chars"]["2"]
    char["evolvePhase"] = 2
    char["level"] = 50
    char["favorPoint"] = 10070
    loadout = char["tmpl"]["char_002_amiya"]
    loadout["evolvePhase"] = 2
    loadout["level"] = 50
    return char


def _decoded(*, stage="main_00-01", stars=3, event_count=2, deployed=True):
    return {
        "battleId": CLIENT_BATTLE_ID,
        "stageId": stage,
        "interrupt": 0,
        "giveUp": 0,
        "completeState": stars,
        "percent": 100,
        "killCnt": 12,
        "platform": 2,
        "battleData": {
            "beginTs": 100,
            "endTs": 200,
            "stats": {
                "charStats": ([{"Key": {"charId": "char_002_amiya", "counterType": "SPAWN"}, "Value": 1}] if deployed else []),
                "skillTrigStats": [],
                "extraBattleInfo": {"DEATHDETAIL,char_002_amiya,BOSS,ELITE": event_count},
            },
        },
    }


AMIYA_SQUAD = [{"charInstId": 2, "charId": "char_002_amiya", "currentTmpl": "char_002_amiya"}]


def test_module_mission_reconcile_uses_resource_targets_without_progress():
    value = player()
    _eligible_amiya(value)
    value["equipment"]["missions"] = {}
    changes = reconcile_module_missions(value)
    assert {row["missionId"] for row in changes} >= {"uniequip_002_amiya_1", "uniequip_002_amiya_2"}
    assert value["equipment"]["missions"]["uniequip_002_amiya_1"].copy() == {"value": 0, "target": 10}
    assert value["equipment"]["missions"]["uniequip_002_amiya_2"].copy() == {"value": 0, "target": 1}
    assert reconcile_module_missions(value) == []


def test_module_event_total_requires_real_deployment_evidence():
    value = player()
    _eligible_amiya(value)
    reconcile_module_missions(value)
    mutations, changes = plan_module_battle_missions(value, _decoded(deployed=False), 3, "battle-a", AMIYA_SQUAD)
    assert mutations == []
    assert changes == []
    mutations, changes = plan_module_battle_missions(value, _decoded(event_count=3), 3, "battle-b", AMIYA_SQUAD)
    row = next(m["value"] for m in mutations if m["path"][-1] == "uniequip_002_amiya_1")
    assert row == {"value": 3, "target": 10}
    assert changes[0]["template"] == "EquipmentEventTotal"


def test_module_progress_requires_eligible_owned_operator_in_persisted_squad():
    value = player()
    reconcile_module_missions(value)
    mutations, changes = plan_module_battle_missions(
        value, _decoded(event_count=9), 3, "battle-low-level", AMIYA_SQUAD
    )
    assert mutations == []
    assert changes == []

    _eligible_amiya(value)
    reconcile_module_missions(value)
    mutations, changes = plan_module_battle_missions(
        value, _decoded(event_count=9), 3, "battle-not-in-squad", []
    )
    assert mutations == []
    assert changes == []


def test_module_progress_is_atomic_with_settlement_and_duplicate_is_idempotent():
    value = player()
    _eligible_amiya(value)
    reconcile_module_missions(value)
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": "main_00-01",
            "startTs": 100,
            "battleType": 0,
            "usePracticeTicket": 0,
            "squad": {"slots": [{"charInstId": 2, "currentTmpl": "char_002_amiya"}]},
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    result = _decoded(event_count=4)
    prepared = prepare_battle_finish(value, result, "cipher")
    assert prepared["kind"] == "prepared"
    assert prepared["session"]["rewards"]["moduleMissionChanges"][0]["after"] == 4
    commit_battle_finish(value, prepared["session"])
    assert value["equipment"]["missions"]["uniequip_002_amiya_1"]["value"] == 4
    snapshot = value.copy()
    duplicate = prepare_battle_finish(value, deepcopy(result), "different-cipher")
    assert duplicate["kind"] == "duplicate"
    assert value.copy() == snapshot


def test_failed_and_interrupted_results_never_plan_module_progress():
    value = player()
    _eligible_amiya(value)
    reconcile_module_missions(value)
    for index, change in enumerate(({"completeState": 1}, {"interrupt": 1})):
        session, reason = prepare_battle_start(
            value,
            {
                "stageId": "main_00-01",
                "startTs": 200 + index,
                "battleType": 0,
                "usePracticeTicket": 0,
                "squad": {"slots": [{"charInstId": 2, "currentTmpl": "char_002_amiya"}]},
            },
        )
        assert reason == "new_session"
        activate_battle_start(value, session)
        result = _decoded(event_count=9)
        result.update(change)
        prepared = prepare_battle_finish(value, result, "cipher")
        assert prepared["kind"] == "prepared"
        assert "moduleMissionChanges" not in prepared["session"]["rewards"]
        commit_battle_finish(value, prepared["session"])
    assert value["equipment"]["missions"]["uniequip_002_amiya_1"]["value"] == 0


def test_supported_template_audit_targets_are_parseable():
    missions = const_json_loader[UNIEQUIP_TABLE]["missionList"]
    observed = set()
    for _, resource in missions:
        if resource["template"] in SUPPORTED_TEMPLATES:
            observed.add(resource["template"])
            assert module_mission_target(resource) > 0
    assert observed == SUPPORTED_TEMPLATES
