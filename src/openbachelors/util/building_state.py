from copy import deepcopy
import time

from ..const.filepath import BUILDING_DATA, TMPL_JSON
from .const_json_loader import const_json_loader


BUILDING_SCHEMA_VERSION = 2
TUTORIAL_CLEAN_ORDER = ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24")
TUTORIAL_BUILD_ORDER = {
    "slot_26": "POWER",
    "slot_25": "MANUFACTURE",
    "slot_24": "TRADING",
}
EMPTY_OUTPUT_ROOM = {
    "level": 0,
    "state": 0,
    "roomId": "NONE",
    "charInstIds": [],
    "completeConstructTime": -1,
}

TUTORIAL_CLEAN_RECOVERY = (
    (1100, "slot_33", "obt/guide/l0-11/0_building_init", 1101, "ELEVATOR"),
    (1101, "slot_27", "obt/guide/l0-11/1a_slot_clean", 1102, "ELEVATOR"),
    (1102, "slot_26", "obt/guide/l0-11/1b_slot_clean", 1103, "NONE"),
    (1103, "slot_25", "obt/guide/l0-11/1c_slot_clean", 1104, "NONE"),
    (1104, "slot_24", "obt/guide/l0-11/1d_slot_clean", 1105, "NONE"),
)
TUTORIAL_TRADE_STORY = "obt/guide/l0-11/3c_station_trade"
TUTORIAL_TRADE_NEXT_STORY = "obt/guide/l0-11/3d_op_trade"


class BuildingRuleError(ValueError):
    pass


def reconcile_interrupted_clean_tutorial(player_data):
    """Recover only a clean that is durably present before finishStory.

    This intentionally covers no build, assignment, manufacturing, or order
    state: those client protocols have not yet been observed on this version.
    """
    status = player_data["status"]
    progress = int(status["progress"])
    flags = status["flags"]
    slots = player_data["building"]["roomSlots"]
    for expected_progress, slot_id, story_id, next_progress, room_type in TUTORIAL_CLEAN_RECOVERY:
        if progress != expected_progress or flags.copy().get(story_id):
            continue
        slot = slots.copy().get(slot_id)
        if not isinstance(slot, dict):
            continue
        if room_type == "NONE":
            matches = slot == EMPTY_OUTPUT_ROOM
        else:
            matches = slot == {
                "level": 1,
                "state": 2,
                "roomId": room_type,
                "charInstIds": [],
                "completeConstructTime": -1,
            }
        if not matches:
            continue
        flags[story_id] = 1
        status["progress"] = next_progress
        return {"storyId": story_id, "roomSlotId": slot_id, "progress": next_progress}
    return None


def _next_trade_order_inst_id(player_data):
    status = player_data["building"]["status"]
    highest = int(status.copy().get("tradeOrderSerial", 0))
    if player_data["status"]["flags"].copy().get(TUTORIAL_TRADE_NEXT_STORY):
        highest = max(highest, 1)
    for room in player_data["building"]["rooms"]["TRADING"].copy().values():
        stored = room.get("stock", [])
        for order in (stored.copy() if hasattr(stored, "copy") else stored):
            if isinstance(order, dict) and type(order.get("instId")) is int:
                highest = max(highest, order["instId"])
    return highest + 1


def allocate_trade_order_inst_id(player_data):
    value = _next_trade_order_inst_id(player_data)
    player_data["building"]["status"]["tradeOrderSerial"] = value
    return value


def tutorial_trade_readiness(player_data):
    """Describe the exact server-visible prerequisites for tutorial Part11."""
    status = player_data["status"]
    flags = status["flags"].copy()
    slots = player_data["building"]["roomSlots"].copy()
    rooms = player_data["building"]["rooms"]["TRADING"].copy()
    slot = slots.get("slot_24")
    room = rooms.get("slot_24")
    reasons = []
    if int(status["progress"]) != 1111:
        reasons.append("progress_not_1111")
    if not flags.get(TUTORIAL_TRADE_STORY):
        reasons.append("part10_not_finished")
    if flags.get(TUTORIAL_TRADE_NEXT_STORY):
        reasons.append("part11_already_finished")
    if not isinstance(slot, dict) or slot.get("roomId") != "TRADING" or slot.get("state") != 2:
        reasons.append("slot_24_not_built_trading")
    elif not [value for value in slot.get("charInstIds", []) if value != -1]:
        reasons.append("slot_24_has_no_operator")
    if not isinstance(room, dict):
        reasons.append("slot_24_trading_runtime_missing")
        stock = None
    else:
        stock = room.get("stock")
        if not isinstance(stock, list):
            reasons.append("trading_stock_not_array")
    return {
        "ready": not reasons and bool(stock),
        "canSeedTutorialOrder": not reasons and stock == [],
        "reasons": reasons,
        "stockCount": len(stock) if isinstance(stock, list) else None,
        "next": room.get("next") if isinstance(room, dict) else None,
    }


def ensure_tutorial_first_trade_order(player_data):
    """Seed the deterministic level-1 tutorial order exactly once.

    Level-1 O_GOLD orders are resource/mechanics-confirmed as two Pure Gold for
    1000 LMD. The order instance id is server-owned identity and is allocated
    monotonically from already persisted orders.
    """
    readiness = tutorial_trade_readiness(player_data)
    if readiness["ready"]:
        return {"status": "already_present", **readiness}
    if not readiness["canSeedTutorialOrder"]:
        return {"status": "not_applicable", **readiness}
    order = {
        "type": "O_GOLD",
        "delivery": [{"type": "MATERIAL", "id": "3003", "count": 2}],
        "gain": {"type": "GOLD", "id": "4001", "count": 1000},
        "buff": [],
        "instId": allocate_trade_order_inst_id(player_data),
        "isViolated": 0,
        "specGoldTag": {"activated": 0, "from": None},
    }
    player_data["building"]["rooms"]["TRADING"]["slot_24"]["stock"] = [order]
    return {
        "status": "seeded",
        "order": deepcopy(order),
        **tutorial_trade_readiness(player_data),
    }


def deliver_trade_order(player_data, slot_id, order_id):
    """Atomically consume and settle one server-persisted trading order."""
    rooms = player_data["building"]["rooms"]["TRADING"].copy()
    room = rooms.get(slot_id)
    if not isinstance(room, dict):
        raise BuildingRuleError("trading_room_not_found")
    stock = room.get("stock")
    if not isinstance(stock, list):
        raise BuildingRuleError("trading_stock_not_array")
    try:
        normalized_order_id = int(order_id)
    except (TypeError, ValueError) as exc:
        raise BuildingRuleError("invalid_trade_order_id") from exc
    matches = [
        (index, order)
        for index, order in enumerate(stock)
        if isinstance(order, dict) and order.get("instId") == normalized_order_id
    ]
    if len(matches) != 1:
        raise BuildingRuleError("trade_order_not_found")
    index, order = matches[0]
    delivery = order.get("delivery")
    gain = order.get("gain")
    if not isinstance(delivery, list) or not isinstance(gain, dict):
        raise BuildingRuleError("invalid_trade_order_shape")
    if gain.get("type") != "GOLD" or gain.get("id") != "4001":
        raise BuildingRuleError("unsupported_trade_order_gain")
    costs = []
    for item in delivery:
        if (
            not isinstance(item, dict)
            or item.get("type") != "MATERIAL"
            or not isinstance(item.get("id"), str)
            or type(item.get("count")) is not int
            or item["count"] <= 0
        ):
            raise BuildingRuleError("unsupported_trade_order_delivery")
        costs.append({"id": item["id"], "count": item["count"]})
    gain_count = gain.get("count")
    if type(gain_count) is not int or gain_count <= 0:
        raise BuildingRuleError("invalid_trade_order_gain")

    # Validate every input before the first mutation.
    for item in costs:
        if _inventory_count(player_data, item["id"]) < item["count"]:
            raise BuildingRuleError(f"insufficient_item:{item['id']}")

    inventory_before = {
        item["id"]: _inventory_count(player_data, item["id"]) for item in costs
    }
    gold_before = int(player_data["status"]["gold"])
    for item in costs:
        player_data["inventory"][item["id"]] = (
            _inventory_count(player_data, item["id"]) - item["count"]
        )
    player_data["status"]["gold"] = gold_before + gain_count
    player_data["building"]["rooms"]["TRADING"][slot_id]["stock"] = [
        value for stock_index, value in enumerate(stock) if stock_index != index
    ]
    return {
        "roomSlotId": slot_id,
        "orderId": normalized_order_id,
        "delivery": deepcopy(delivery),
        "gain": deepcopy(gain),
        "inventoryBefore": inventory_before,
        "inventoryAfter": {
            item["id"]: _inventory_count(player_data, item["id"]) for item in costs
        },
        "goldBefore": gold_before,
        "goldAfter": gold_before + gain_count,
    }


def validate_building_structure(player_data):
    """Return protocol-shape problems without mutating player data.

    The validator deliberately does not invent defaults for unknown room types
    or states.  Its output is suitable for structured login diagnostics.
    """
    issues = []
    building = player_data["building"]
    building_data = _building_data()
    layout_slots = building_data["layouts"]["v0"]["slots"]
    room_rules = building_data["rooms"]
    room_slots = building["roomSlots"].copy()
    runtime_groups = building["rooms"].copy()

    for slot_id, slot_value in room_slots.items():
        path = f"building.roomSlots.{slot_id}"
        if slot_id not in layout_slots:
            issues.append({"code": "unknown_slot_id", "path": path})
            continue
        if not isinstance(slot_value, dict):
            issues.append({"code": "slot_not_object", "path": path})
            continue
        slot = dict(slot_value)
        room_type = slot.get("roomId")
        level = slot.get("level")
        state = slot.get("state")
        layout_category = layout_slots[slot_id]["category"]
        if (
            layout_category == "OUTPUT"
            and level == 0
            and state == 0
            and room_type == "NONE"
            and slot.get("charInstIds") == []
            and isinstance(slot.get("completeConstructTime"), int)
        ):
            # PlayerRoomSlotState.EMPTY + RoomType.NONE is the client's
            # cleared-but-unbuilt OUTPUT representation. It deliberately has
            # no entry under building.rooms.
            continue
        if room_type not in room_rules:
            issues.append(
                {
                    "code": "unknown_room_type",
                    "path": f"{path}.roomId",
                    "value": room_type,
                }
            )
            # Unknown types are reported only.  Do not guess their schema.
            continue
        if type(level) is not int or level < 1 or level > len(room_rules[room_type]["phases"]):
            issues.append(
                {"code": "invalid_room_level", "path": f"{path}.level", "value": level}
            )
            continue
        if type(state) is not int or state not in {0, 1, 2}:
            issues.append(
                {"code": "invalid_room_state", "path": f"{path}.state", "value": state}
            )
        expected_category = (
            room_type if layout_category in {"ELEVATOR", "CORRIDOR"} else room_rules[room_type]["category"]
        )
        if expected_category != layout_category:
            issues.append(
                {
                    "code": "room_category_mismatch",
                    "path": f"{path}.roomId",
                    "roomType": room_type,
                    "layoutCategory": layout_category,
                }
            )
        char_ids = slot.get("charInstIds")
        if not isinstance(char_ids, list):
            issues.append({"code": "char_inst_ids_not_array", "path": f"{path}.charInstIds"})
        else:
            capacity = int(room_rules[room_type]["phases"][level - 1]["maxStationedNum"])
            if len(char_ids) != capacity:
                issues.append(
                    {
                        "code": "station_capacity_mismatch",
                        "path": f"{path}.charInstIds",
                        "expected": capacity,
                        "actual": len(char_ids),
                    }
                )
        if not isinstance(slot.get("completeConstructTime"), int):
            issues.append(
                {"code": "construct_time_not_integer", "path": f"{path}.completeConstructTime"}
            )
        if room_type not in {"ELEVATOR", "CORRIDOR"}:
            runtime_group = runtime_groups.get(room_type)
            runtime = runtime_group.get(slot_id) if isinstance(runtime_group, dict) else None
            if not isinstance(runtime, dict):
                issues.append(
                    {
                        "code": "missing_room_runtime",
                        "path": f"building.rooms.{room_type}.{slot_id}",
                    }
                )

    for room_type, runtime_group in runtime_groups.items():
        if not isinstance(runtime_group, dict):
            issues.append(
                {"code": "runtime_group_not_object", "path": f"building.rooms.{room_type}"}
            )
            continue
        for slot_id in runtime_group:
            slot = room_slots.get(slot_id)
            if not isinstance(slot, dict) or slot.get("roomId") != room_type:
                issues.append(
                    {
                        "code": "orphan_room_runtime",
                        "path": f"building.rooms.{room_type}.{slot_id}",
                    }
                )
            runtime = runtime_group.get(slot_id)
            if room_type != "TRADING" or not isinstance(runtime, dict):
                continue
            stock = runtime.get("stock")
            if not isinstance(stock, list):
                issues.append(
                    {
                        "code": "trading_stock_not_array",
                        "path": f"building.rooms.TRADING.{slot_id}.stock",
                    }
                )
                continue
            for index, order in enumerate(stock):
                order_path = f"building.rooms.TRADING.{slot_id}.stock.{index}"
                if not isinstance(order, dict):
                    issues.append({"code": "trade_order_not_object", "path": order_path})
                    continue
                if (
                    order.get("type") != "O_GOLD"
                    or type(order.get("instId")) is not int
                    or not isinstance(order.get("delivery"), list)
                    or not isinstance(order.get("gain"), dict)
                    or not isinstance(order.get("buff"), list)
                    or type(order.get("isViolated")) is not int
                    or not isinstance(order.get("specGoldTag"), dict)
                ):
                    issues.append(
                        {"code": "invalid_trade_order_shape", "path": order_path}
                    )
                    continue
                rewards = [*order["delivery"], order["gain"]]
                if any(
                    not isinstance(reward, dict)
                    or not isinstance(reward.get("type"), str)
                    or not isinstance(reward.get("id"), str)
                    or type(reward.get("count")) is not int
                    or reward["count"] <= 0
                    for reward in rewards
                ):
                    issues.append(
                        {"code": "invalid_trade_order_reward", "path": order_path}
                    )
    return issues


def build_newbie_building(source_building):
    """Build the pre-0-11 RIIC state.

    The client obtains rubble/locked slots from the static layout.  Only rooms
    which actually exist belong in ``roomSlots``; emitting zero-level entries
    makes the station-management UI treat rubble as malformed rooms.
    """
    result = deepcopy(source_building)
    control_slot = deepcopy(result["roomSlots"]["slot_34"])
    control_slot.update(
        level=1,
        state=2,
        roomId="CONTROL",
        charInstIds=[-1],
        completeConstructTime=-1,
    )
    control_room = deepcopy(result["rooms"]["CONTROL"]["slot_34"])
    control_room["lastUpdateTime"] = 1700000000
    control_room["presetQueue"] = []

    result["roomSlots"] = {"slot_34": control_slot}
    result["rooms"] = {room_type: {} for room_type in result["rooms"]}
    result["rooms"]["CONTROL"] = {"slot_34": control_room}
    result["status"]["labor"] = {
        "buffSpeed": 0,
        "processPoint": 0,
        "value": 32,
        "lastUpdateTime": 1700000000,
        "maxValue": 32,
    }
    result["status"]["workshop"] = {"bonusActive": 0, "bonus": {}}
    result["assist"] = [-1] * len(result.get("assist", [-1] * 5))
    return result


def _building_data():
    return const_json_loader[BUILDING_DATA].copy()


def _layout_slot(slot_id):
    try:
        return _building_data()["layouts"]["v0"]["slots"][slot_id].copy()
    except KeyError as exc:
        raise BuildingRuleError("unknown_room_slot") from exc


def _prototype_room_type(slot_id):
    try:
        return const_json_loader[TMPL_JSON]["building"]["roomSlots"][slot_id][
            "roomId"
        ]
    except KeyError as exc:
        raise BuildingRuleError("missing_room_slot_prototype") from exc


def _inventory_count(player_data, item_id):
    return int(player_data["inventory"].copy().get(item_id, 0))


def _consume_items(player_data, items):
    for item in items:
        item_id = item["id"]
        count = int(item["count"])
        if _inventory_count(player_data, item_id) < count:
            raise BuildingRuleError(f"insufficient_item:{item_id}")
    for item in items:
        item_id = item["id"]
        player_data["inventory"][item_id] = _inventory_count(player_data, item_id) - int(
            item["count"]
        )


def _clean_cost(player_data, slot_id):
    layout = _layout_slot(slot_id)
    cost_id = layout["cleanCostId"]
    layouts = _building_data()["layouts"]["v0"]
    cleaned_same_kind = 0
    for existing_slot_id in player_data["building"]["roomSlots"].copy():
        if existing_slot_id == slot_id:
            continue
        existing_slot = player_data["building"]["roomSlots"][existing_slot_id].copy()
        existing_layout = layouts["slots"].get(existing_slot_id, {})
        is_structural = (
            existing_layout.get("category") in {"ELEVATOR", "CORRIDOR"}
            and int(existing_slot.get("level", 0)) == 1
            and int(existing_slot.get("state", -1)) == 2
        )
        is_empty_output = (
            existing_layout.get("category") == "OUTPUT"
            and existing_slot.get("roomId") == "NONE"
            and int(existing_slot.get("level", -1)) == 0
            and int(existing_slot.get("state", -1)) == 0
        )
        if not (is_structural or is_empty_output):
            continue
        try:
            if layouts["slots"][existing_slot_id]["cleanCostId"] == cost_id:
                cleaned_same_kind += 1
        except KeyError:
            continue
    number = layouts["cleanCosts"][cost_id]["number"]
    entry = number.get(str(cleaned_same_kind))
    if entry is None:
        entry = number.get(cleaned_same_kind)
    if entry is None:
        raise BuildingRuleError("unconfirmed_clean_cost")
    return entry.get("items", []), int(layout["provideLabor"])


def clean_room_slot(player_data, slot_id):
    if slot_id in player_data["building"]["roomSlots"]:
        slot = player_data["building"]["roomSlots"][slot_id]
        slot_values = slot.copy()
        layout_category = _layout_slot(slot_id)["category"]
        if (
            layout_category in {"ELEVATOR", "CORRIDOR"}
            and int(slot_values.get("level", 0)) == 1
            and int(slot_values.get("state", 0)) == 2
            and slot_values.get("roomId") == layout_category
        ):
            return {"items": [], "providedLabor": 0, "alreadyApplied": True}
        if layout_category == "OUTPUT" and slot_values == EMPTY_OUTPUT_ROOM:
            return {"items": [], "providedLabor": 0, "alreadyApplied": True}
        is_empty_slot = int(slot_values.get("level", 0)) == 0
        if is_empty_slot and int(slot_values.get("state", 0)) == 2:
            # A client may retry after receiving a malformed/aborted delta.  Re-send
            # the absolute cleaned-slot state without granting labor a second time.
            if layout_category not in {"ELEVATOR", "CORRIDOR"}:
                raise BuildingRuleError("unconfirmed_cleaned_output_slot_protocol")
            slot["level"] = 1
            slot["state"] = 2
            slot["roomId"] = layout_category
            slot["charInstIds"] = []
            slot["completeConstructTime"] = -1
            labor = player_data["building"]["status"]["labor"]
            labor_values = labor.copy()
            for key in ("maxValue", "value", "lastUpdateTime"):
                labor[key] = labor_values.get(key, 0)
            return {"items": [], "providedLabor": 0, "alreadyApplied": True}
        if not is_empty_slot or int(slot_values.get("state", 0)) != 0:
            raise BuildingRuleError("room_slot_already_clean")
    items, provided_labor = _clean_cost(player_data, slot_id)
    layout_category = _layout_slot(slot_id)["category"]
    _consume_items(player_data, items)
    if slot_id not in player_data["building"]["roomSlots"]:
        player_data["building"]["roomSlots"][slot_id] = {}
    slot = player_data["building"]["roomSlots"][slot_id]
    if layout_category == "OUTPUT":
        for key, value in EMPTY_OUTPUT_ROOM.items():
            slot[key] = deepcopy(value)
    elif layout_category in {"ELEVATOR", "CORRIDOR"}:
        slot["level"] = 1
        slot["state"] = 2
        slot["roomId"] = layout_category
        slot["charInstIds"] = []
        slot["completeConstructTime"] = -1
    else:
        raise BuildingRuleError("unsupported_clean_slot_category")
    labor = player_data["building"]["status"]["labor"]
    labor_values = labor.copy()
    labor["maxValue"] = int(labor_values.get("maxValue", 0)) + provided_labor
    labor["value"] = int(labor_values.get("value", 0)) + provided_labor
    labor["lastUpdateTime"] = int(time.time())
    return {"items": items, "providedLabor": provided_labor}


def repair_malformed_cleaned_structural_slots(player_data):
    """Repair only schema-v1 structural slots known to have been cleaned."""
    repaired = []
    room_slots = player_data["building"]["roomSlots"]
    for slot_id in room_slots.copy():
        slot = room_slots[slot_id]
        values = slot.copy()
        try:
            category = _layout_slot(slot_id)["category"]
        except BuildingRuleError:
            continue
        if (
            category in {"ELEVATOR", "CORRIDOR"}
            and int(values.get("level", 0)) == 0
            and int(values.get("state", 0)) == 2
        ):
            slot["level"] = 1
            slot["roomId"] = category
            slot["charInstIds"] = []
            slot["completeConstructTime"] = -1
            repaired.append(slot_id)
    return repaired


# Compatibility import for code/tests written against schema v1.
repair_empty_room_type = repair_malformed_cleaned_structural_slots


def _count_rooms(player_data, room_type, minimum_level=1):
    return sum(
        1
        for slot in player_data["building"]["roomSlots"].copy().values()
        if slot.get("roomId") == room_type
        and int(slot.get("level", 0)) >= minimum_level
    )


def _electricity_balance(player_data):
    building_data = _building_data()
    total = 0
    for slot in player_data["building"]["roomSlots"].copy().values():
        room_type = slot.get("roomId")
        level = int(slot.get("level", 0))
        if not room_type or level < 1 or room_type not in building_data["rooms"]:
            continue
        total += int(building_data["rooms"][room_type]["phases"][level - 1]["electricity"])
    return total


def _check_unlock_condition(player_data, condition_id, ordinal):
    condition = _building_data()["roomUnlockConds"][condition_id]["number"]
    entry = condition.get(str(ordinal)) or condition.get(ordinal)
    if entry is None:
        raise BuildingRuleError("unconfirmed_room_unlock_condition")
    room_type = entry["type"]
    if room_type == "NONE":
        return
    if _count_rooms(player_data, room_type, int(entry["level"])) < int(entry["count"]):
        raise BuildingRuleError("room_unlock_condition_not_met")


def _room_runtime_template(room_type, slot_id, level):
    source = const_json_loader[TMPL_JSON]["building"]["rooms"][room_type].copy()
    if not source:
        raise BuildingRuleError("missing_room_runtime_template")
    prototype = deepcopy(next(iter(source.values())))
    prototype["presetQueue"] = []
    now = int(time.time())
    if "lastUpdateTime" in prototype:
        prototype["lastUpdateTime"] = now
    if room_type == "MANUFACTURE":
        prototype.update(
            state=1,
            formulaId="",
            remainSolutionCnt=0,
            outputSolutionCnt=0,
            capacity=int(_building_data()["manufactData"]["phases"][level - 1]["outputCapacity"]),
            processPoint=0,
            completeWorkTime=-1,
        )
    elif room_type == "TRADING":
        prototype.update(
            state=1,
            stockLimit=int(_building_data()["tradingData"]["phases"][level - 1]["orderLimit"]),
            stock=[],
            completeWorkTime=-1,
        )
    return prototype


def build_or_upgrade_room(player_data, slot_id, room_type, target_level=1):
    room_type = str(room_type).upper()
    target_level = int(target_level)
    if slot_id not in player_data["building"]["roomSlots"]:
        raise BuildingRuleError("room_slot_not_clean")
    slot = player_data["building"]["roomSlots"][slot_id]
    slot_values = slot.copy()
    layout = _layout_slot(slot_id)
    room_data = _building_data()["rooms"].get(room_type)
    if room_data is None or room_data["category"] != layout["category"]:
        raise BuildingRuleError("room_type_not_allowed_in_slot")
    current_level = int(slot_values.get("level", 0))
    current_type = slot_values.get("roomId")
    if current_level == 0 and not (
        layout["category"] == "OUTPUT"
        and int(slot_values.get("state", -1)) == 0
        and current_type == "NONE"
        and slot_values.get("charInstIds") == []
    ):
        raise BuildingRuleError("room_slot_not_clean")
    if current_level > 0 and current_type != room_type:
        raise BuildingRuleError("room_slot_occupied")
    if target_level != current_level + 1 or target_level > len(room_data["phases"]):
        raise BuildingRuleError("invalid_target_level")
    if current_level == 0 and _count_rooms(player_data, room_type) >= int(room_data["maxCount"]):
        raise BuildingRuleError("room_type_count_limit")
    ordinal = _count_rooms(player_data, room_type) + (1 if current_level == 0 else 0)
    phase = room_data["phases"][target_level - 1]
    _check_unlock_condition(player_data, phase["unlockCondId"], ordinal)
    cost = phase["buildCost"]
    previous_electricity = (
        int(room_data["phases"][current_level - 1]["electricity"])
        if current_level
        else 0
    )
    if _electricity_balance(player_data) - previous_electricity + int(
        phase["electricity"]
    ) < 0:
        raise BuildingRuleError("insufficient_electricity")
    runtime_room = _room_runtime_template(room_type, slot_id, target_level)
    labor = player_data["building"]["status"]["labor"]
    labor_values = labor.copy()
    if int(labor_values.get("value", 0)) < int(cost["labor"]):
        raise BuildingRuleError("insufficient_labor")
    _consume_items(player_data, cost.get("items", []))
    labor["value"] = int(labor_values.get("value", 0)) - int(cost["labor"])
    labor["lastUpdateTime"] = int(time.time())
    slot["level"] = target_level
    slot["state"] = 2
    slot["roomId"] = room_type
    slot["charInstIds"] = [-1] * int(phase["maxStationedNum"])
    slot["completeConstructTime"] = -1
    player_data["building"]["rooms"][room_type][slot_id] = runtime_room
    return {"items": cost.get("items", []), "labor": int(cost["labor"])}
