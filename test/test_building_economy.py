from openbachelors.util.building_economy import (
    accelerate,
    collect_manufacture,
    settle_building_economy,
    settle_labor,
    settle_manufacture_room,
    settle_trading_room,
)
from openbachelors.util.building_state import deliver_trade_order
from openbachelors.util.building_state import deliver_trade_orders_batch

from .test_building_tutorial import prepare_part11_player


def economy_player():
    player = prepare_part11_player()
    manufacture = player["building"]["rooms"]["MANUFACTURE"]["slot_25"]
    manufacture["formulaId"] = "4"
    manufacture["remainSolutionCnt"] = 20
    manufacture["outputSolutionCnt"] = 0
    manufacture["processPoint"] = 0
    manufacture["lastUpdateTime"] = 1000
    manufacture["capacity"] = 24
    trade = player["building"]["rooms"]["TRADING"]["slot_24"]
    trade["strategy"] = "O_GOLD"
    trade["stock"] = []
    trade["stockLimit"] = 6
    trade["next"] = {"order": 0, "processPoint": 0, "speed": 1, "maxPoint": 8640}
    trade["lastUpdateTime"] = 1000
    return player


def test_manufacture_offline_progress_capacity_and_collect_are_idempotent():
    player = economy_player()
    assert settle_manufacture_room(player, "slot_25", 1000 + 4320 * 20) == 12
    room = player["building"]["rooms"]["MANUFACTURE"]["slot_25"]
    assert room["outputSolutionCnt"] == 12
    assert room["remainSolutionCnt"] == 8
    assert settle_manufacture_room(player, "slot_25", 1000 + 4320 * 20) == 0
    rewards = collect_manufacture(player, ["slot_25"], 1000 + 4320 * 20)
    assert rewards == [{"roomSlotId": "slot_25", "itemId": "3003", "count": 12}]
    assert player["inventory"]["3003"] >= 12
    assert collect_manufacture(player, ["slot_25"], 1000 + 4320 * 20) == []


def test_level_one_gold_orders_are_persisted_unique_and_stop_at_limit():
    player = economy_player()
    assert settle_trading_room(player, "slot_24", 1000 + 8640 * 20) == 6
    room = player["building"]["rooms"]["TRADING"]["slot_24"]
    assert len(room["stock"]) == 6
    stock = room["stock"].copy()
    assert len({order["instId"] for order in stock}) == 6
    assert all(order["delivery"][0]["id"] == "3003" for order in stock)
    assert settle_trading_room(player, "slot_24", 1000 + 8640 * 20) == 0


def test_labor_recovery_and_acceleration_have_hard_bounds():
    player = economy_player()
    labor = player["building"]["status"]["labor"]
    labor["value"], labor["maxValue"], labor["processPoint"], labor["lastUpdateTime"] = 1, 3, 0, 1000
    assert settle_labor(player, 1000 + 360 * 10) == 2
    assert labor["value"] == 3
    before = player["building"]["rooms"]["MANUFACTURE"]["slot_25"]["processPoint"]
    assert accelerate(player, "MANUFACTURE", "slot_25", 2, 1000 + 360 * 10) == 360
    assert labor["value"] == 1
    assert player["building"]["rooms"]["MANUFACTURE"]["slot_25"]["processPoint"] >= before + 360


def test_multi_room_settlement_is_repeatable_at_same_timestamp():
    player = economy_player()
    first = settle_building_economy(player, 10000)
    snapshot = player["building"].copy()
    second = settle_building_economy(player, 10000)
    assert second["manufacture"]["slot_25"] == 0
    assert second["trading"]["slot_24"] == 0
    assert player["building"].copy() == snapshot


def test_trade_order_ids_are_never_reused_after_stock_becomes_empty():
    player = economy_player()
    player["inventory"]["3003"] = 20
    settle_trading_room(player, "slot_24", 9640)
    first_id = player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy()[0]["instId"]
    deliver_trade_order(player, "slot_24", first_id)
    assert player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == []
    settle_trading_room(player, "slot_24", 18280)
    second_id = player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy()[0]["instId"]
    assert second_id > first_id
    try:
        deliver_trade_order(player, "slot_24", first_id)
    except Exception as exc:
        assert "trade_order_not_found" in str(exc)
    else:
        raise AssertionError("old delivery request matched a new order")


def test_batch_delivery_settles_all_orders_and_old_ids_stay_dead():
    player = economy_player(); player["inventory"]["3003"] = 20
    settle_trading_room(player, "slot_24", 1000 + 8640 * 2)
    ids = [x["instId"] for x in player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy()]
    delivered = deliver_trade_orders_batch(player, ["slot_24"])
    assert len(delivered["slot_24"]) == 2
    assert player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == []
    settle_trading_room(player, "slot_24", 1000 + 8640 * 3)
    assert player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy()[0]["instId"] > max(ids)
