from openbachelors.bp.bp_mission import _claim_mission
from openbachelors.util.mission_lifecycle import reconcile_visible_missions
from openbachelors.util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template


def player():
    value = OverlayJson(post_tutorial_player_data_template, DeltaJson())
    reconcile_visible_missions(value)
    return value


def test_fresh_account_exposes_incomplete_root_missions():
    value = player()

    assert value["mission"]["missions"]["MAIN"]["main_1"].copy() == {
        "progress": [{"target": 1, "value": 0}],
        "state": 2,
    }
    assert value["mission"]["missionGroups"]["main_1"] == 0
    assert value["mission"]["missionGroups"]["guide_g_1"] == 0
    assert len(value["mission"]["missions"]["GUIDE"]) == 9
    assert "main_2" not in value["mission"]["missions"]["MAIN"]
    assert "sub_1" not in value["mission"]["missions"]["SUB"]


def test_existing_incomplete_mission_becomes_claimable_when_stage_is_passed():
    value = player()
    value["dungeon"]["stages"]["main_00-01"]["state"] = 2

    changes = reconcile_visible_missions(value)

    assert value["mission"]["missions"]["MAIN"]["main_1"].copy() == {
        "progress": [{"target": 1, "value": 1}],
        "state": 2,
    }
    assert any(change["missionId"] == "main_1" for change in changes)


def test_claiming_main_mission_exposes_only_immediate_successors():
    value = player()
    value["dungeon"]["stages"]["main_00-01"]["state"] = 2
    reconcile_visible_missions(value)

    result = _claim_mission(value, "main_1")

    assert result["reason"] is None
    assert value["mission"]["missionGroups"]["main_1"] == 1
    assert value["mission"]["missions"]["MAIN"]["main_2"]["state"] == 2
    assert value["mission"]["missions"]["SUB"]["sub_1"]["state"] == 2
    assert value["mission"]["missionGroups"]["sub_g_1"] == 0
    assert "main_3" not in value["mission"]["missions"]["MAIN"]
    assert "sub_2" not in value["mission"]["missions"]["SUB"]


def test_claiming_sub_mission_exposes_next_without_completing_group():
    value = player()
    value["mission"]["missionGroups"]["sub_g_1"] = 0
    value["mission"]["missions"]["SUB"]["sub_1"] = {
        "progress": [{"target": 1, "value": 1}],
        "state": 2,
    }

    result = _claim_mission(value, "sub_1")

    assert result["reason"] is None
    assert value["mission"]["missionGroups"]["sub_g_1"] == 0
    assert value["mission"]["missions"]["SUB"]["sub_2"].copy() == {
        "progress": [{"target": 1, "value": 0}],
        "state": 2,
    }


def test_reconciliation_is_idempotent_and_preserves_claimed_state():
    value = player()
    value["mission"]["missions"]["MAIN"]["main_1"] = {
        "progress": [{"target": 1, "value": 1}],
        "state": 3,
    }

    first = reconcile_visible_missions(value)
    snapshot = value["mission"].copy()
    second = reconcile_visible_missions(value)

    assert first
    assert second == []
    assert value["mission"].copy() == snapshot
    assert value["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3


def test_completed_guide_mission_can_be_claimed_and_reward_is_persisted():
    value = player()
    target = value["mission"]["missions"]["GUIDE"]["guide_1"]["progress"][0][
        "target"
    ]
    value["mission"]["missions"]["GUIDE"]["guide_1"] = {
        "progress": [{"target": target, "value": target}],
        "state": 2,
    }
    before = value["inventory"]["2001"] if "2001" in value["inventory"] else 0

    result = _claim_mission(value, "guide_1", "GUIDE")

    assert result["reason"] is None
    assert value["mission"]["missions"]["GUIDE"]["guide_1"]["state"] == 3
    assert value["inventory"]["2001"] == before + 5


def test_legacy_hidden_guide_records_and_group_are_repaired_without_rewards():
    value = player()
    value["mission"]["missionGroups"]["guide_g_1"] = 1
    value["mission"]["missions"]["GUIDE"]["guide_1"] = {
        "progress": [{"target": 1, "value": 0}],
        "state": 1,
    }
    before_inventory = value["inventory"].copy()

    changes = reconcile_visible_missions(value)

    assert value["mission"]["missionGroups"]["guide_g_1"] == 0
    assert value["mission"]["missions"]["GUIDE"]["guide_1"].copy() == {
        "progress": [{"target": 1, "value": 0}],
        "state": 2,
    }
    assert value["inventory"].copy() == before_inventory
    assert {change["kind"] for change in changes} >= {
        "guide_group_reactivated",
        "mission_progress_reconciled",
    }
