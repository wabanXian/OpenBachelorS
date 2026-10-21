"""Deterministic, persisted RIIC production settlement without background timers."""

from copy import deepcopy

from ..const.filepath import BUILDING_DATA
from .building_state import BuildingRuleError, allocate_trade_order_inst_id
from .const_json_loader import const_json_loader
from .faketime import faketime


def _now(now=None):
    return int(faketime() if now is None else now)


def _data():
    return const_json_loader[BUILDING_DATA].copy()


def settle_labor(player_data, now=None):
    now = _now(now)
    labor = player_data["building"]["status"]["labor"]
    old = labor.copy()
    last = int(old.get("lastUpdateTime", now))
    now = max(now, last)
    elapsed = max(0, now - last)
    interval = int(_data()["laborRecoverTime"])
    point = float(old.get("processPoint", 0)) + elapsed
    value, maximum = int(old.get("value", 0)), int(old.get("maxValue", 0))
    gained = min(maximum - value, int(point // interval))
    labor["value"] = value + max(0, gained)
    labor["processPoint"] = 0 if labor["value"] >= maximum else point - max(0, gained) * interval
    labor["lastUpdateTime"] = now
    return max(0, gained)


def _manufact_room(player_data, slot_id):
    room = player_data["building"]["rooms"]["MANUFACTURE"].copy().get(slot_id)
    if not isinstance(room, dict):
        raise BuildingRuleError("manufacture_room_not_found")
    return player_data["building"]["rooms"]["MANUFACTURE"][slot_id]


def settle_manufacture_room(player_data, slot_id, now=None, bonus_seconds=0):
    now = _now(now)
    room = _manufact_room(player_data, slot_id)
    formula_id = room.copy().get("formulaId")
    formula = _data()["manufactFormulas"].copy().get(formula_id)
    last = int(room.copy().get("lastUpdateTime", now))
    now = max(now, last)
    if not formula or int(room.copy().get("remainSolutionCnt", 0)) <= 0:
        room["lastUpdateTime"] = now
        room["completeWorkTime"] = -1
        return 0
    point = float(room.copy().get("processPoint", 0)) + max(0, now - last) + max(0, int(bonus_seconds))
    output = int(room.copy().get("outputSolutionCnt", 0))
    remain = int(room.copy().get("remainSolutionCnt", 0))
    capacity = int(room.copy().get("capacity", 0))
    weight = int(formula["weight"])
    cost_point = int(formula["costPoint"])
    can_make = max(0, (capacity - output * weight) // weight)
    made = min(remain, can_make, int(point // cost_point))
    room["outputSolutionCnt"] = output + made
    room["remainSolutionCnt"] = remain - made
    room["processPoint"] = point - made * cost_point
    room["lastUpdateTime"] = now
    blocked = room["remainSolutionCnt"] <= 0 or (capacity - room["outputSolutionCnt"] * weight) < weight
    if blocked:
        room["processPoint"] = 0
    room["completeWorkTime"] = -1 if blocked else now + max(0, cost_point - int(room["processPoint"]))
    return made


def collect_manufacture(player_data, slot_ids, now=None):
    now = _now(now)
    plans = []
    for slot_id in slot_ids:
        settle_manufacture_room(player_data, slot_id, now)
        room = _manufact_room(player_data, slot_id)
        formula = _data()["manufactFormulas"].copy().get(room.copy().get("formulaId"))
        count = int(room.copy().get("outputSolutionCnt", 0))
        if formula and count:
            plans.append((slot_id, formula["itemId"], count * int(formula["count"])))
    for slot_id, item_id, count in plans:
        current = int(player_data["inventory"].copy().get(item_id, 0))
        player_data["inventory"][item_id] = current + count
        player_data["building"]["rooms"]["MANUFACTURE"][slot_id]["outputSolutionCnt"] = 0
        settle_manufacture_room(player_data, slot_id, now)
    return [{"roomSlotId": s, "itemId": i, "count": c} for s, i, c in plans]


def _gold_order(player_data, inst_id=None):
    return {"type": "O_GOLD", "delivery": [{"type": "MATERIAL", "id": "3003", "count": 2}],
            "gain": {"type": "GOLD", "id": "4001", "count": 1000}, "buff": [],
            "instId": allocate_trade_order_inst_id(player_data) if inst_id is None else inst_id, "isViolated": 0,
            "specGoldTag": {"activated": 0, "from": None}}


def settle_trading_room(player_data, slot_id, now=None, bonus_seconds=0):
    now = _now(now)
    room = player_data["building"]["rooms"]["TRADING"].copy().get(slot_id)
    slot = player_data["building"]["roomSlots"].copy().get(slot_id, {})
    if not isinstance(room, dict):
        raise BuildingRuleError("trading_room_not_found")
    room = player_data["building"]["rooms"]["TRADING"][slot_id]
    last = int(room.copy().get("lastUpdateTime", now))
    now = max(now, last)
    if room.copy().get("strategy") != "O_GOLD" or int(slot.get("level", 0)) != 1:
        room["lastUpdateTime"] = now
        return 0
    stored_stock = room.copy().get("stock", [])
    stock = deepcopy(stored_stock.copy() if hasattr(stored_stock, "copy") else stored_stock)
    point = float((room.copy().get("next") or {}).get("processPoint", 0)) + max(0, now-last) + max(0, int(bonus_seconds))
    duration = 8640  # PC base mechanics: level-1 two-gold order, before buffs.
    made = 0
    while point >= duration and len(stock) < int(room.copy().get("stockLimit", 0)):
        stock.append(_gold_order(player_data)); point -= duration; made += 1
    if len(stock) >= int(room.copy().get("stockLimit", 0)):
        point = 0
    room["stock"] = stock
    room["next"] = {"order": 0, "processPoint": point, "speed": 1, "maxPoint": duration}
    room["lastUpdateTime"] = now
    room["completeWorkTime"] = -1 if len(stock) >= int(room.copy().get("stockLimit", 0)) else now + max(0, duration-int(point))
    return made


def settle_building_economy(player_data, now=None):
    now = _now(now); result = {"labor": settle_labor(player_data, now), "manufacture": {}, "trading": {}}
    for slot_id in player_data["building"]["rooms"]["MANUFACTURE"].copy():
        result["manufacture"][slot_id] = settle_manufacture_room(player_data, slot_id, now)
    for slot_id in player_data["building"]["rooms"]["TRADING"].copy():
        result["trading"][slot_id] = settle_trading_room(player_data, slot_id, now)
    return result


def accelerate(player_data, room_type, slot_id, cost, now=None):
    if type(cost) is not int or cost <= 0:
        raise BuildingRuleError("invalid_labor_cost")
    now = _now(now); settle_labor(player_data, now)
    labor = player_data["building"]["status"]["labor"]
    if int(labor["value"]) < cost:
        raise BuildingRuleError("insufficient_labor")
    seconds = cost * int(_data()["manufactReduceTimeUnit" if room_type == "MANUFACTURE" else "tradingReduceTimeUnit"])
    if room_type == "MANUFACTURE":
        room = _manufact_room(player_data, slot_id)
        settle_manufacture_room(player_data, slot_id, now)
        if int(room.copy().get("remainSolutionCnt", 0)) <= 0 or int(room.copy().get("outputSolutionCnt", 0)) * int(_data()["manufactFormulas"][room["formulaId"]]["weight"]) >= int(room["capacity"]):
            raise BuildingRuleError("manufacture_not_acceleratable")
        settle_manufacture_room(player_data, slot_id, now, seconds)
    elif room_type == "TRADING":
        room = player_data["building"]["rooms"]["TRADING"].copy().get(slot_id)
        settle_trading_room(player_data, slot_id, now)
        if not isinstance(room, dict) or len(room.get("stock", [])) >= int(room.get("stockLimit", 0)):
            raise BuildingRuleError("trading_not_acceleratable")
        settle_trading_room(player_data, slot_id, now, seconds)
    else: raise BuildingRuleError("unsupported_acceleration_room")
    labor["value"] = int(labor["value"]) - cost
    return seconds
