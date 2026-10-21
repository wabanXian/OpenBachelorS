# Building economy audit (2026-10-21)

## Implemented

- `/building/sync` performs deterministic timestamp-based settlement. No background timer is required; repeated sync at the same timestamp is idempotent.
- Manufacture queues use `manufactFormulas.costPoint`, `weight`, `count`, and the room-level `manufactData.phases.outputCapacity`. Produced-but-unclaimed units remain in `outputSolutionCnt`; `/building/settleManufacture` is the only operation that moves them into inventory.
- Full manufacture storage stops time accumulation. Collecting does not retroactively convert time spent while full into products.
- Level-1 `O_GOLD` trading rooms persist normal two-Pure-Gold/1000-LMD orders, stable monotonic `instId` values, progress in `next`, and stop while the stock limit is full. Tutorial orders are left intact and participate in the same delivery path.
- Drone recovery uses `laborRecoverTime=360`, `processPoint`, `lastUpdateTime`, `value`, and `maxValue`. Manufacture and trading acceleration use the PC request shapes and the GameData 180-second reduction per drone, and reject empty/full rooms before charging.
- Clock rollback is clamped to the last persisted update timestamp.

## Evidence

- `building_data.json`: manufacture formulas, output weights/capacities, room levels, order limits, `laborRecoverTime`, `manufactReduceTimeUnit`, and `tradingReduceTimeUnit`.
- PC 2.7.81 `dump.cs`: `BuildingSettleManufactRequest(roomSlotIdList,supplement)`, `BuildingManufactLaborAccelRequest(slotId,cost)`, `BuildingTradingLaborAccelRequest(slotId,orderId,cost)`, and the complete `PlayerBuildingManufacture`, `PlayerBuildingTrading`, `PlayerBuildingTradingNext`, and `PlayerBuildingLabor` schemas.
- Existing verified tutorial order: two item `3003` for 1000 currency `4001` and the existing `/building/deliveryOrder` client flow.

## Deliberate limitations

- Only the evidence-backed level-1 two-gold order is generated. Level-2/3 random order rarity distributions, `O_DIAMOND`, special operator orders, and violated orders remain ungenerated because this resource version does not contain their server probability table.
- Operator base-skill efficiency, morale, dormitory recovery, and combinations are outside this iteration. Base production speed is used.
- Queue cancellation/refund rules and the trading `deleteOrder` endpoint still need a captured PC request/response before implementation.
- Acceleration behavior is structurally implemented from current PC types and GameData, but still needs live PC confirmation of UI result handling.

## Verification

- Automated coverage includes offline manufacture, storage cap, collection idempotency, unique/persistent orders, stock cap, drone recovery and acceleration bounds, simultaneous room settlement, and same-timestamp sync.
- Existing `test_building_tutorial.py` remains a mandatory regression suite.

## Short Windows PC acceptance path

1. Select Pure Gold in a level-1 manufacture room and queue several units.
2. Re-enter after enough time, verify completed and remaining counts, collect once, then reopen to confirm no duplicate inventory.
3. Let a level-1 trading room create an order, deliver two Pure Gold, and verify 1000 LMD plus the next order timer.
4. Spend one drone in each room, confirm one drone is deducted and the displayed remaining time decreases by 180 seconds; restart and verify persistence.
