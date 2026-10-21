from fastapi import APIRouter
from fastapi import Request

from ..const.json_const import true, false, null
from ..const.filepath import BUILDING_DATA, CONFIG_JSON, VERSION_JSON
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator
from ..util.building_state import (
    BuildingRuleError,
    build_or_upgrade_room,
    clean_room_slot,
    deliver_trade_order,
    deliver_trade_orders_batch,
    validate_building_structure,
)
from ..util.building_economy import (
    accelerate, collect_manufacture, settle_building_economy,
    settle_manufacture_room, settle_trading_room,
)
from ..util.building_staff import settle_building_staff
from ..util.tutorial_debug import event
from ..util.mission_lifecycle import update_has_room_missions
from ..util.mission_lifecycle import apply_mission_event

router = APIRouter()


@router.post("/building/sync")
@player_data_decorator
async def building_sync(player_data, request: Request):
    request_json = await request.json()
    staff = settle_building_staff(player_data)
    economy = settle_building_economy(player_data)
    labor = player_data["building"]["status"]["labor"]
    structure_issues = validate_building_structure(player_data)
    event(
        request,
        "building_sync",
        requestFields=sorted(request_json),
        roomSlotCount=len(player_data["building"]["roomSlots"]),
        controlLevel=player_data["building"]["roomSlots"].copy().get("slot_34", {}).get(
            "level"
        ),
        labor={"value": labor.copy().get("value"), "maxValue": labor.copy().get("maxValue")},
        structureValid=not structure_issues,
        structureIssues=structure_issues,
        economy=economy,
        staff=staff,
    )
    response = {"ts": __import__("time").time_ns() // 1_000_000_000}
    return response


def _building_error(request, operation, exc, **context):
    event(
        request,
        "building_operation_rejected",
        operation=operation,
        reason=str(exc),
        **context,
    )
    return {"result": 1, "buildingError": str(exc)}


@router.post("/building/cleanRoomSlot")
@player_data_decorator
async def building_cleanRoomSlot(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("roomSlotId")
    try:
        result = clean_room_slot(player_data, slot_id)
    except BuildingRuleError as exc:
        return _building_error(request, "cleanRoomSlot", exc, roomSlotId=slot_id)
    event(request, "building_room_cleaned", roomSlotId=slot_id, changes=result)
    return {}


@router.post("/building/buildRoom")
@player_data_decorator
async def building_buildRoom(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("roomSlotId")
    room_type = request_json.get("roomId")
    try:
        result = build_or_upgrade_room(player_data, slot_id, room_type, 1)
    except BuildingRuleError as exc:
        return _building_error(
            request, "buildRoom", exc, roomSlotId=slot_id, roomId=room_type
        )
    event(
        request,
        "building_room_built",
        roomSlotId=slot_id,
        roomId=room_type,
        level=1,
        costs=result,
        missionUpdates=update_has_room_missions(player_data),
    )
    return {"result": 0}


@router.post("/building/upgradeRoom")
@player_data_decorator
async def building_upgradeRoom(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("roomSlotId")
    target_level = request_json.get("targetLevel")
    slot = player_data["building"]["roomSlots"].copy().get(slot_id, {})
    room_type = slot.get("roomId")
    try:
        result = build_or_upgrade_room(
            player_data, slot_id, room_type, target_level
        )
    except (BuildingRuleError, TypeError, ValueError) as exc:
        return _building_error(
            request,
            "upgradeRoom",
            exc,
            roomSlotId=slot_id,
            roomId=room_type,
            targetLevel=target_level,
        )
    event(
        request,
        "building_room_upgraded",
        roomSlotId=slot_id,
        roomId=room_type,
        level=target_level,
        costs=result,
        missionUpdates=update_has_room_missions(player_data),
    )
    return {"result": 0}


@router.post("/building/completeUpgradeRoom")
@player_data_decorator
async def building_completeUpgradeRoom(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("roomSlotId")
    if slot_id not in player_data["building"]["roomSlots"]:
        slot = None
    else:
        slot = player_data["building"]["roomSlots"][slot_id]
    if slot is None or slot.copy().get("state") != 1:
        return _building_error(
            request,
            "completeUpgradeRoom",
            BuildingRuleError("room_not_upgrading"),
            roomSlotId=slot_id,
        )
    slot["state"] = 2
    slot["completeConstructTime"] = -1
    event(request, "building_room_upgrade_completed", roomSlotId=slot_id)
    return {"result": 0}


@router.post("/building/changeManufactureSolution")
@player_data_decorator
async def building_changeManufactureSolution(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("roomSlotId")
    formula_id = str(request_json.get("targetFormulaId"))
    solution_count = int(request_json.get("solutionCount", 0))
    formulas = const_json_loader[BUILDING_DATA]["manufactFormulas"].copy()
    room = (
        player_data["building"]["rooms"]["MANUFACTURE"][slot_id]
        if slot_id in player_data["building"]["rooms"]["MANUFACTURE"]
        else None
    )
    formula = formulas.get(formula_id)
    if room is None or formula is None or solution_count < 0:
        return _building_error(
            request,
            "changeManufactureSolution",
            BuildingRuleError("invalid_manufacture_solution"),
            roomSlotId=slot_id,
            formulaId=formula_id,
        )
    slot = player_data["building"]["roomSlots"][slot_id].copy()
    requirements = formula.get("requireRooms", [])
    if not any(x.get("roomId") == "MANUFACTURE" and int(slot.get("level", 0)) >= int(x.get("roomLevel", 99)) for x in requirements):
        return _building_error(request, "changeManufactureSolution", BuildingRuleError("manufacture_formula_locked"), roomSlotId=slot_id, formulaId=formula_id)
    settle_manufacture_room(player_data, slot_id)
    room["state"] = 1
    room["formulaId"] = formula_id
    room["remainSolutionCnt"] = solution_count
    room["outputSolutionCnt"] = 0
    room["lastUpdateTime"] = __import__("time").time_ns() // 1_000_000_000
    room["processPoint"] = 0
    room["completeWorkTime"] = -1
    settle_manufacture_room(player_data, slot_id)
    event(
        request,
        "building_manufacture_solution_changed",
        roomSlotId=slot_id,
        formulaId=formula_id,
        solutionCount=solution_count,
    )
    return {"result": 0}


@router.post("/building/settleManufacture")
@player_data_decorator
async def building_settleManufacture(player_data, request: Request):
    body = await request.json()
    slots = body.get("roomSlotIdList")
    if not isinstance(slots, list) or any(not isinstance(x, str) for x in slots):
        return _building_error(request, "settleManufacture", BuildingRuleError("invalid_room_slot_list"))
    try: rewards = collect_manufacture(player_data, slots)
    except BuildingRuleError as exc: return _building_error(request, "settleManufacture", exc)
    return {"supplement": int(body.get("supplement", 0)), "rewards": rewards}


@router.post("/building/accelerateSolution")
@player_data_decorator
async def building_accelerateSolution(player_data, request: Request):
    body = await request.json()
    try: accelerate(player_data, "MANUFACTURE", body.get("slotId"), body.get("cost"))
    except BuildingRuleError as exc: return _building_error(request, "accelerateSolution", exc)
    return {}


@router.post("/building/accelerateOrder")
@player_data_decorator
async def building_accelerateOrder(player_data, request: Request):
    body = await request.json()
    try: accelerate(player_data, "TRADING", body.get("slotId"), body.get("cost"))
    except BuildingRuleError as exc: return _building_error(request, "accelerateOrder", exc)
    return {"result": 0}


@router.post("/building/changeStrategy")
@player_data_decorator
async def building_changeStrategy(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("roomSlotId") or request_json.get("slotId")
    strategy = request_json.get("strategy") or request_json.get("targetStrategy")
    room = (
        player_data["building"]["rooms"]["TRADING"][slot_id]
        if slot_id in player_data["building"]["rooms"]["TRADING"]
        else None
    )
    if room is None or strategy not in {"O_GOLD", "O_DIAMOND"}:
        return _building_error(
            request,
            "changeStrategy",
            BuildingRuleError("invalid_trading_strategy"),
            roomSlotId=slot_id,
            strategy=strategy,
        )
    room["strategy"] = strategy
    room["lastUpdateTime"] = __import__("time").time_ns() // 1_000_000_000
    event(
        request,
        "building_trading_strategy_changed",
        roomSlotId=slot_id,
        strategy=strategy,
    )
    return {"result": 0}


@router.post("/building/deliveryOrder")
@player_data_decorator
async def building_deliveryOrder(player_data, request: Request):
    request_json = await request.json()
    slot_id = request_json.get("slotId")
    order_id = request_json.get("orderId")
    try: settle_trading_room(player_data, slot_id)
    except BuildingRuleError as exc: return _building_error(request, "deliveryOrder", exc)
    try:
        result = deliver_trade_order(player_data, slot_id, order_id)
    except BuildingRuleError as exc:
        return _building_error(
            request,
            "deliveryOrder",
            exc,
            roomSlotId=slot_id,
            orderId=order_id,
            requestFields=sorted(request_json),
        )
    event(
        request,
        "building_trade_order_delivered",
        requestFields=sorted(request_json),
        **result,
    )
    apply_mission_event(
        player_data,
        "delivery_order",
        {"count": 1, "roomSlotId": slot_id, "orderId": order_id},
    )
    settle_trading_room(player_data, slot_id)
    return {"result": 0}


@router.post("/building/deliveryBatchOrder")
@player_data_decorator
async def building_deliveryBatchOrder(player_data, request: Request):
    body = await request.json()
    slot_ids = body.get("slotList")
    try:
        for slot_id in slot_ids if isinstance(slot_ids, list) else []:
            settle_trading_room(player_data, slot_id)
        delivered = deliver_trade_orders_batch(player_data, slot_ids)
        for slot_id in slot_ids:
            settle_trading_room(player_data, slot_id)
    except BuildingRuleError as exc:
        # BuildingDeliveryBatchOrderResponse.delivered is dereferenced directly
        # by the PC client.  Preserve that response shape even for rejected
        # requests instead of returning the generic building error payload.
        _building_error(request, "deliveryBatchOrder", exc)
        valid_slot_ids = slot_ids if isinstance(slot_ids, list) else []
        return {
            "delivered": {
                slot_id: [] for slot_id in valid_slot_ids if isinstance(slot_id, str)
            }
        }
    apply_mission_event(player_data, "delivery_order", {"count": sum(len(items) for items in delivered.values())})
    return {"delivered": delivered}


@router.post("/building/getRecentVisitors")
async def building_getRecentVisitors(request: Request):
    request_json = await request.json()
    response = {"visitors": []}
    return response


@router.post("/building/getInfoShareVisitorsNum")
async def building_getInfoShareVisitorsNum(request: Request):
    request_json = await request.json()
    response = {"num": 0}
    return response


@router.post("/building/getClueFriendList")
@player_data_decorator
async def building_getClueFriendList(player_data, request: Request):
    request_json = await request.json()
    response = {"result": []}
    return response


@router.post("/building/getClueBox")
@player_data_decorator
async def building_getClueBox(player_data, request: Request):
    request_json = await request.json()
    response = {"box": []}
    return response


@router.post("/building/getAssistReport")
@player_data_decorator
async def building_getAssistReport(player_data, request: Request):
    request_json = await request.json()
    response = {"reports": []}
    return response


@router.post("/building/changeDiySolution")
@player_data_decorator
async def building_changeDiySolution(player_data, request: Request):
    request_json = await request.json()

    room_id = request_json["roomSlotId"]
    diy_solution = request_json["solution"]

    room_type = player_data["building"]["roomSlots"][room_id]["roomId"]

    player_data["building"]["rooms"][room_type][room_id]["diySolution"] = diy_solution

    response = {}
    return response


@router.post("/building/setBuildingAssist")
@player_data_decorator
async def building_setBuildingAssist(player_data, request: Request):
    request_json = await request.json()

    char_num_id = request_json["charInstId"]
    assist_idx = request_json["type"]

    assist_lst = player_data["building"]["assist"].copy()
    if char_num_id != -1:
        for i in range(len(assist_lst)):
            if assist_lst[i] == char_num_id:
                assist_lst[i] = -1
    assist_lst[assist_idx] = char_num_id
    player_data["building"]["assist"] = assist_lst

    response = {}
    return response


@router.post("/building/assignChar")
@player_data_decorator
async def building_assignChar(player_data, request: Request):
    request_json = await request.json()

    room_id = request_json["roomSlotId"]
    char_num_id_lst = request_json["charInstIdList"]
    if not isinstance(char_num_id_lst, list) or any(type(value) is not int for value in char_num_id_lst):
        return _building_error(request, "assignChar", BuildingRuleError("invalid_room_assignment"), roomSlotId=room_id)
    if room_id not in player_data["building"]["roomSlots"]:
        return _building_error(
            request, "assignChar", BuildingRuleError("unknown_room_slot"), roomSlotId=room_id
        )
    slot_snapshot = player_data["building"]["roomSlots"][room_id].copy()
    if int(slot_snapshot.get("level", 0)) < 1 or int(slot_snapshot.get("state", 0)) != 2:
        return _building_error(request, "assignChar", BuildingRuleError("room_not_ready"), roomSlotId=room_id)
    training_room = None
    if slot_snapshot.get("roomId") == "TRAINING":
        training_rooms = player_data["building"]["rooms"]["TRAINING"]
        if room_id in training_rooms:
            training_room = training_rooms[room_id]
    expected_slots = len(slot_snapshot.get("charInstIds", []))
    assigned = [value for value in char_num_id_lst if value != -1]
    known_chars = player_data["building"]["chars"].copy()
    owned_chars = player_data["troop"]["chars"].copy()
    if (
        len(char_num_id_lst) != expected_slots
        or len(assigned) != len(set(assigned))
        or any(str(value) not in known_chars or str(value) not in owned_chars for value in assigned)
    ):
        return _building_error(
            request,
            "assignChar",
            BuildingRuleError("invalid_room_assignment"),
            roomSlotId=room_id,
            requestedCount=len(char_num_id_lst),
            capacity=expected_slots,
        )

    if training_room is not None:
        current_trainee = training_room["trainee"]
        requested_trainee = char_num_id_lst[1] if len(char_num_id_lst) > 1 else -1
        if current_trainee["state"] == 1 and requested_trainee != current_trainee["charInstId"]:
            return _building_error(
                request,
                "assignChar",
                BuildingRuleError("training_room_busy"),
                roomSlotId=room_id,
            )

    # Validate every source before settling or mutating any assignment.
    for char_num_id in assigned:
        building_char = known_chars[str(char_num_id)]
        src_room_id = building_char.get("roomSlotId", "")
        if not src_room_id or src_room_id == room_id:
            continue
        src_slot = player_data["building"]["roomSlots"].copy().get(src_room_id, {})
        if src_slot.get("roomId") == "TRAINING":
            src_runtime = player_data["building"]["rooms"]["TRAINING"].copy().get(src_room_id, {})
            trainee = src_runtime.get("trainee", {})
            if trainee.get("charInstId") == char_num_id and trainee.get("state") in {1, 3}:
                return _building_error(request, "assignChar", BuildingRuleError("training_trainee_locked"), roomSlotId=room_id)

    settle_building_staff(player_data)
    settle_building_economy(player_data)

    old_char_num_id_lst = player_data["building"]["roomSlots"][room_id][
        "charInstIds"
    ].copy()

    for char_num_id in old_char_num_id_lst:
        if char_num_id == -1:
            continue
        player_data["building"]["chars"][str(char_num_id)]["roomSlotId"] = ""
        player_data["building"]["chars"][str(char_num_id)]["index"] = -1

    for char_idx, char_num_id in enumerate(char_num_id_lst):
        if char_num_id == -1:
            continue
        src_room_id = player_data["building"]["chars"][str(char_num_id)]["roomSlotId"]
        if src_room_id:
            src_char_idx = player_data["building"]["chars"][str(char_num_id)]["index"]

            src_char_num_id_lst = player_data["building"]["roomSlots"][src_room_id][
                "charInstIds"
            ].copy()
            src_char_num_id_lst[src_char_idx] = -1
            player_data["building"]["roomSlots"][src_room_id]["charInstIds"] = (
                src_char_num_id_lst
            )
        player_data["building"]["chars"][str(char_num_id)]["roomSlotId"] = room_id
        player_data["building"]["chars"][str(char_num_id)]["index"] = char_idx

    player_data["building"]["roomSlots"][room_id]["charInstIds"] = char_num_id_lst

    if training_room is not None and training_room["trainee"]["state"] != 1:
        trainer_id = char_num_id_lst[0] if char_num_id_lst else -1
        trainee_id = char_num_id_lst[1] if len(char_num_id_lst) > 1 else -1
        old_trainee = training_room["trainee"].copy()
        training_room["trainer"] = {
            "charInstId": trainer_id,
            "state": 3 if trainer_id != -1 else 0,
        }
        if trainee_id == old_trainee["charInstId"] and old_trainee["state"] == 3:
            training_room["trainee"] = old_trainee
        else:
            training_room["trainee"] = {
                "charInstId": trainee_id,
                "state": 3 if trainee_id != -1 else 0,
                "targetSkill": -1,
                "processPoint": -1,
                "speed": 1,
            }

    event(
        request,
        "building_chars_assigned",
        roomSlotId=room_id,
        before=old_char_num_id_lst,
        after=char_num_id_lst,
    )

    response = {}
    return response


@router.post("/building/getMessageBoardContent")
@player_data_decorator
async def building_getMessageBoardContent(player_data, request: Request):
    request_json = await request.json()

    response = {
        "thisWeekVisitors": [],
        "lastWeekVisitors": [],
        "todayVisit": 0,
        "weeklyVisit": 0,
        "lastWeekVisit": 0,
        "lastWeekSpReward": 0,
        "lastShowTs": 1700000000,
    }
    return response


@router.post("/building/changeBGM")
@player_data_decorator
async def building_changeBGM(player_data, request: Request):
    request_json = await request.json()

    music_id = request_json["musicId"]

    player_data["building"]["music"]["selected"] = music_id

    response = {}
    return response


@router.post("/building/setPrivateDormOwner")
@player_data_decorator
async def building_setPrivateDormOwner(player_data, request: Request):
    request_json = await request.json()

    room_id = request_json["slotId"]
    char_num_id = request_json["charInsId"]

    player_data["building"]["rooms"]["PRIVATE"][room_id]["owners"] = [char_num_id]

    response = {}
    return response


@router.post("/building/saveDiyPresetSolution")
@player_data_decorator
async def building_saveDiyPresetSolution(player_data, request: Request):
    request_json = await request.json()

    solution_id = request_json["solutionId"]

    player_data["building"]["diyPresetSolutions"][str(solution_id)] = {
        "name": request_json["name"],
        "solution": request_json["solution"],
        "roomType": request_json["roomType"],
        "thumbnail": "http://127.0.0.1/thumbnail.jpg",
    }

    response = {}
    return response


@router.post("/building/getThumbnailUrl")
async def building_getThumbnailUrl(request: Request):
    request_json = await request.json()

    response = {"url": ["http://127.0.0.1/thumbnail.jpg"]}
    return response


@router.post("/building/changePresetName")
@player_data_decorator
async def building_changePresetName(player_data, request: Request):
    request_json = await request.json()

    solution_id = request_json["solutionId"]

    player_data["building"]["diyPresetSolutions"][str(solution_id)]["name"] = (
        request_json["name"]
    )

    response = {}
    return response
