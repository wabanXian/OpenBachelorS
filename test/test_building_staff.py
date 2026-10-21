import pytest

from openbachelors.util.building_staff import MAX_AP, settle_building_staff
from openbachelors.util.player_data import DeltaJson, OverlayJson, sandbox_player_data_template
from openbachelors.const.filepath import BUILDING_DATA
from openbachelors.util.const_json_loader import const_json_loader
from openbachelors.bp.bp_building import building_assignChar
from .test_building_tutorial import make_request


def player():
    return OverlayJson(sandbox_player_data_template, DeltaJson())


def test_working_operator_loses_one_morale_per_hour_and_is_idempotent():
    value = player(); char = value["building"]["chars"]["2"]
    char["roomSlotId"] = "slot_16"; char["index"] = 0; char["ap"] = MAX_AP; char["lastApAddTime"] = 1000
    settle_building_staff(value, 4600)
    assert char["ap"] == MAX_AP - 360000
    settle_building_staff(value, 4600)
    assert char["ap"] == MAX_AP - 360000


def test_idle_operator_does_not_lose_morale():
    value = player(); char = value["building"]["chars"]["2"]
    char["roomSlotId"] = ""; char["ap"] = 123456; char["lastApAddTime"] = 1000
    settle_building_staff(value, 100000)
    assert char["ap"] == 123456


def test_dormitory_recovers_from_level_and_comfort_without_overflow():
    value = player(); dorm_id = next(iter(value["building"]["rooms"]["DORMITORY"].copy()))
    char = value["building"]["chars"]["2"]
    char["roomSlotId"] = dorm_id; char["index"] = 0; char["ap"] = 0; char["lastApAddTime"] = 1000
    slot = value["building"]["roomSlots"][dorm_id].copy()
    runtime = value["building"]["rooms"]["DORMITORY"][dorm_id].copy()
    speed = const_json_loader[BUILDING_DATA]["dormData"]["phases"][slot["level"] - 1]["manpowerRecover"] + min(5000, runtime["comfort"]) // 25
    settle_building_staff(value, 4600)
    assert char["ap"] == min(MAX_AP, 3600 * speed)
    settle_building_staff(value, 999999)
    assert char["ap"] == MAX_AP


def test_training_operator_uses_separate_state_machine_and_is_untouched():
    value = player(); training_id = next(iter(value["building"]["rooms"]["TRAINING"].copy()))
    char = value["building"]["chars"]["2"]
    char["roomSlotId"] = training_id; char["ap"] = 777777; char["lastApAddTime"] = 1000
    settle_building_staff(value, 9999)
    assert char["ap"] == 777777


@pytest.mark.asyncio
async def test_active_training_trainee_cannot_be_moved_by_other_room_assignment():
    value = player()
    training_id = next(iter(value["building"]["rooms"]["TRAINING"].copy()))
    manufacture_id = next(iter(value["building"]["rooms"]["MANUFACTURE"].copy()))
    trainee = 2
    value["building"]["chars"][str(trainee)]["roomSlotId"] = training_id
    value["building"]["chars"][str(trainee)]["index"] = 1
    training_slots = value["building"]["roomSlots"][training_id]["charInstIds"].copy()
    training_slots[1] = trainee
    value["building"]["roomSlots"][training_id]["charInstIds"] = training_slots
    value["building"]["rooms"]["TRAINING"][training_id]["trainee"] = {
        "charInstId": trainee, "state": 1, "targetSkill": 0, "processPoint": 0, "speed": 1
    }
    target = [-1] * len(value["building"]["roomSlots"][manufacture_id]["charInstIds"])
    target[0] = trainee
    response = await building_assignChar.__wrapped__(value, make_request({"roomSlotId": manufacture_id, "charInstIdList": target}))
    assert response["buildingError"] == "training_trainee_locked"
    assert value["building"]["chars"][str(trainee)]["roomSlotId"] == training_id

