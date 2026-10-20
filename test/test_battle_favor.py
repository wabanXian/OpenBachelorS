from copy import deepcopy

import pytest

from openbachelors.util.battle_settlement import (
    CLIENT_BATTLE_ID,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
)
from openbachelors.util.favor import favor_config, favor_percent
from openbachelors.util.player_data import (
    DeltaJson,
    OverlayJson,
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


def squad(*slots):
    return {"squadId": "0", "name": "test", "slots": list(slots)}


def amiya_slot():
    return {
        "charInstId": 2,
        "currentTmpl": "char_002_amiya",
        "tmpl": {"char_002_amiya": {"skillIndex": 0, "currentEquip": None}},
    }


def ordinary_slot(inst_id):
    return {"charInstId": inst_id, "skillIndex": 0, "currentEquip": None}


def start(value, formation, ts=100, stage_id="main_00-01"):
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": stage_id,
            "startTs": ts,
            "battleType": 0,
            "usePracticeTicket": 0,
            "squad": formation,
        },
    )
    assert reason == "new_session"
    activate_battle_start(value, session)
    return session


def result(stars=3, *, interrupt=0, give_up=0, stage_id="main_00-01"):
    return {
        "battleId": CLIENT_BATTLE_ID,
        "stageId": stage_id,
        "interrupt": interrupt,
        "giveUp": give_up,
        "completeState": stars,
        "percent": 100 if stars in (2, 3) else 0,
        "killCnt": 11 if stars in (2, 3) else 0,
        "platform": 2,
        "battleData": {"beginTs": 100, "endTs": 200},
    }


def finish(value, decoded):
    prepared = prepare_battle_finish(value, decoded, "cipher")
    assert prepared["kind"] == "prepared"
    commit_battle_finish(value, prepared["session"])
    return prepared


@pytest.mark.parametrize("stars", [2, 3])
def test_success_grants_actual_ap_to_every_snapshotted_operator(stars):
    value = player()
    formation = squad(amiya_slot(), ordinary_slot(502), None)
    session = start(value, formation)

    settled = finish(value, result(stars))

    assert session["apCost"] == 6
    assert session["squadSnapshot"] == [
        {
            "charInstId": 2,
            "charId": "char_002_amiya",
            "currentTmpl": "char_002_amiya",
            "currentEquip": None,
        },
        {"charInstId": 502, "charId": "char_502_nblade", "currentTmpl": None, "currentEquip": None},
    ]
    assert value["troop"]["chars"]["2"]["favorPoint"] == 6
    assert value["troop"]["charGroup"]["char_002_amiya"]["favorPoint"] == 6
    assert value["troop"]["chars"]["502"]["favorPoint"] == 6
    assert len(settled["session"]["rewards"]["favor"]) == 2


def test_instance_and_group_counters_keep_their_own_baselines_and_cap():
    value = player()
    max_favor, _ = favor_config()
    value["troop"]["chars"]["2"]["favorPoint"] = max_favor - 2
    value["troop"]["charGroup"]["char_002_amiya"]["favorPoint"] = 100
    start(value, squad(amiya_slot()))

    finish(value, result(3))

    assert value["troop"]["chars"]["2"]["favorPoint"] == max_favor
    assert value["troop"]["charGroup"]["char_002_amiya"]["favorPoint"] == 106
    assert favor_percent(max_favor) == 200


@pytest.mark.parametrize(
    "decoded",
    [result(1), result(1, interrupt=1), result(1, give_up=1)],
)
def test_failure_interrupt_and_give_up_do_not_grant_favor(decoded):
    value = player()
    start(value, squad(amiya_slot()))
    settled = finish(value, decoded)

    assert value["troop"]["chars"]["2"]["favorPoint"] == 0
    assert value["troop"]["charGroup"]["char_002_amiya"]["favorPoint"] == 0
    assert "favor" not in settled["session"]["rewards"]


def test_zero_ap_training_and_null_tutorial_squad_grant_no_favor():
    value = player()
    value["dungeon"]["stages"]["main_00-01"]["state"] = 3
    value["dungeon"]["stages"]["tr_01"] = {
        "stageId": "tr_01", "completeTimes": 0, "startTimes": 0,
        "practiceTimes": 0, "state": 0, "hasBattleReplay": 0, "noCostCnt": 0,
    }
    session = start(value, None, stage_id="tr_01")
    assert session["apCost"] == 0
    training = result(3, stage_id="tr_01")
    finish(value, training)
    assert value["troop"]["chars"]["2"]["favorPoint"] == 0


def test_duplicate_finish_does_not_grant_favor_twice():
    value = player()
    start(value, squad(amiya_slot()))
    decoded = result(3)
    finish(value, decoded)
    before = value["troop"]["chars"]["2"]["favorPoint"]

    duplicate = prepare_battle_finish(value, deepcopy(decoded), "new-cipher")

    assert duplicate["kind"] == "duplicate"
    assert value["troop"]["chars"]["2"]["favorPoint"] == before


def test_favor_delta_contains_both_fields_and_survives_overlay_reload():
    value = player()
    start(value, squad(amiya_slot()))
    finish(value, result(3))
    value.delta_json.prune_empty_branches()

    modified = deepcopy(value.delta_json.modified_dict)
    deleted = deepcopy(value.delta_json.deleted_dict)
    assert modified["troop"]["chars"]["2"]["favorPoint"] == 6
    assert modified["troop"]["charGroup"]["char_002_amiya"]["favorPoint"] == 6

    reloaded = OverlayJson(
        post_tutorial_player_data_template, DeltaJson(modified, deleted)
    )
    assert reloaded["troop"]["chars"]["2"]["favorPoint"] == 6
    assert reloaded["troop"]["charGroup"]["char_002_amiya"]["favorPoint"] == 6


@pytest.mark.parametrize(
    "formation,reason",
    [
        (squad(ordinary_slot(999999)), "squad_character_not_owned"),
        (squad(amiya_slot(), amiya_slot()), "duplicate_squad_character"),
        (
            squad({"charInstId": 2, "currentTmpl": "char_missing", "tmpl": {}}),
            "invalid_character_template",
        ),
    ],
)
def test_invalid_squad_snapshot_is_rejected_before_ap_is_spent(formation, reason):
    value = player()
    ap_before = value["status"]["ap"]
    session, actual = prepare_battle_start(
        value,
        {
            "stageId": "main_00-01", "startTs": 100, "battleType": 0,
            "usePracticeTicket": 0, "squad": formation,
        },
    )
    assert session is None
    assert actual == reason
    assert value["status"]["ap"] == ap_before


def test_practice_start_remains_rejected_and_has_no_favor_session():
    value = player()
    session, reason = prepare_battle_start(
        value,
        {
            "stageId": "main_00-01", "startTs": 100, "battleType": 0,
            "usePracticeTicket": 1, "squad": squad(amiya_slot()),
        },
    )
    assert session is None
    assert reason == "practice_settlement_not_implemented"
    assert value.extra_save.save_obj == {}
