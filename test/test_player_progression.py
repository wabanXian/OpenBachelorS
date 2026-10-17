from copy import deepcopy

from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
)
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
    post_tutorial_player_data_template,
)
from openbachelors.util.player_progression import (
    calculate_player_progression,
    plan_player_exp_gain,
)
from openbachelors.bp.bp_mission import _claim_mission


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


def settle_zero_one(value, stars=3):
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": "main_00-01", "startTs": 100, "battleType": 0,
            "usePracticeTicket": 0, "squad": None,
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    decoded = {
        "battleId": CLIENT_BATTLE_ID, "stageId": "main_00-01",
        "interrupt": 0, "giveUp": 0, "completeState": stars,
        "percent": 100, "killCnt": 11, "platform": 2,
        "battleData": {"beginTs": 100, "endTs": 200},
    }
    prepared = prepare_battle_finish(value, decoded, "cipher")
    assert prepared["kind"] == "prepared"
    commit_battle_finish(value, prepared["session"])
    return prepared, decoded


def test_exp_below_threshold_stays_in_current_level():
    result = calculate_player_progression(1, 100, 70, 60)
    assert (result["level"], result["exp"], result["ap"], result["maxAp"]) == (
        1, 160, 70, 82
    )
    assert result["levelUps"] == []


def test_crossing_threshold_carries_exp_and_adds_new_level_ap():
    result = calculate_player_progression(1, 480, 70, 60)
    assert (result["level"], result["exp"]) == (2, 40)
    assert result["maxAp"] == 84
    assert result["ap"] == 154
    assert result["levelUps"] == [
        {"level": 2, "threshold": 500, "maxAp": 84, "apGranted": 84}
    ]


def test_multiple_levels_consume_each_resource_threshold_and_refill_each_time():
    result = calculate_player_progression(1, 0, 10, 2550)
    assert (result["level"], result["exp"]) == (4, 10)
    assert result["ap"] == 10 + 84 + 86 + 88
    assert result["maxAp"] == 88


def test_level_up_sanity_is_allowed_to_overcap():
    result = calculate_player_progression(1, 500, 82, 0)
    assert result["level"] == 2
    assert result["ap"] == 166
    assert result["ap"] > result["maxAp"]


def test_max_level_discards_new_and_stale_exp_without_extra_sanity():
    result = calculate_player_progression(120, 9999, 135, 100000)
    assert result["level"] == 120
    assert result["exp"] == 0
    assert result["ap"] == 135
    assert result["maxAp"] == 135
    assert result["levelUps"] == []
    assert result["expGainApplied"] == 0


def test_existing_level_one_exp_540_is_repaired_idempotently():
    value = player()
    value["status"]["exp"] = 540
    value["status"]["ap"] = 63
    mutations, result = plan_player_exp_gain(value, 0)
    from openbachelors.util.battle_settlement import _apply_mutations
    _apply_mutations(value, mutations)

    assert (value["status"]["level"], value["status"]["exp"]) == (2, 40)
    assert (value["status"]["ap"], value["status"]["maxAp"]) == (147, 84)
    assert "guide_6" in result["missionUpdates"]
    assert value["mission"]["missions"]["GUIDE"]["guide_6"]["state"] == 2

    second, again = plan_player_exp_gain(value, 0)
    assert second == []
    assert again["levelUps"] == []
    assert again["missionUpdates"] == []


def test_player_level_guide_mission_can_be_claimed_once():
    value = player()
    value["status"]["exp"] = 540
    mutations, _ = plan_player_exp_gain(value, 0)
    from openbachelors.util.battle_settlement import _apply_mutations
    _apply_mutations(value, mutations)
    gold_before = value["status"]["gold"]

    claimed = _claim_mission(value, "guide_6")
    duplicate = _claim_mission(value, "guide_6")

    assert claimed["reason"] is None
    assert claimed["items"] == [{"type": "GOLD", "id": "4001", "count": 1000}]
    assert value["status"]["gold"] == gold_before + 1000
    assert value["mission"]["missions"]["GUIDE"]["guide_6"]["state"] == 3
    assert duplicate["reason"] == "already_claimed"
    assert duplicate["items"] == []


def test_battle_level_up_is_in_transaction_delta_and_duplicate_safe():
    value = player()
    value["status"]["exp"] = 480
    prepared, decoded = settle_zero_one(value)

    assert value["status"]["level"] == 2
    assert value["status"]["exp"] == 40
    assert value["status"]["maxAp"] == 84
    assert value["status"]["ap"] == 160
    progression = prepared["session"]["rewards"]["playerProgression"]
    assert progression["levelUps"][0]["level"] == 2
    before = value.copy()
    duplicate = prepare_battle_finish(value, deepcopy(decoded), "retry")
    assert duplicate["kind"] == "duplicate"
    assert value.copy() == before


def test_progression_survives_overlay_reload():
    value = player()
    value["status"]["exp"] = 480
    settle_zero_one(value)
    value.delta_json.prune_empty_branches()
    reloaded = OverlayJson(
        post_tutorial_player_data_template,
        DeltaJson(
            deepcopy(value.delta_json.modified_dict),
            deepcopy(value.delta_json.deleted_dict),
        ),
    )
    assert reloaded["status"]["level"] == 2
    assert reloaded["status"]["exp"] == 40
    assert reloaded["status"]["ap"] == 160
    assert reloaded["status"]["maxAp"] == 84
