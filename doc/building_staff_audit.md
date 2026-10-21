# Building staff audit (2026-10-21)

## Protocol and resource evidence

- PC 2.7.81 exposes `PlayerBuildingChar(ap,lastApAddTime,roomSlotId,index)` and `BuildingAssignCharRequest(roomSlotId,charInstIdList)`.
- The stored maximum morale is 8,640,000. `manpowerDisplayFactor=360000` maps it to the displayed 24 points.
- Normal production runtimes use base `apCost=-100`, equivalent to 360,000 stored units (one displayed morale) per hour.
- `dormData.phases[*].manpowerRecover` supplies level-based recovery (160..200 stored units/second). Comfort adds `comfort / comfortManpowerRecoverFactor`, with the resource limit of 5000.

## Implemented

- `/building/sync` settles staff first, then production, at one shared `faketime()` time source.
- Working operators consume morale, idle operators do not, dormitory operators recover, and values clamp to 0..8,640,000.
- Training rooms remain owned by the specialization state machine and receive no generic morale mutation.
- Assignment validates ownership, built room state, capacity, duplicates, and all source rooms before mutation. Active/waiting trainees cannot be pulled out through another room.
- Before assignment changes, old staff and economic state are settled, preventing new staff from affecting historical production.
- Trade order identity now uses persisted `building.status.tradeOrderSerial`; compatibility initialization also scans current stock and the completed tutorial marker. Old delivery requests cannot match newly allocated orders.

## Deliberate limitations

- No operator base-skill production multipliers are enabled yet. The skill table contains many cross-room and composition-dependent effects; names alone are not sufficient evidence for safe arithmetic.
- Furniture-specific and operator-skill dormitory recovery modifiers remain unimplemented.
- Training morale behavior remains isolated until a live protocol trace establishes its independent rule.

## Windows PC acceptance

Assign one operator to manufacturing, one to a dormitory, note displayed morale, exit for at least an hour, then re-enter and call building sync. Verify the worker decreases by one displayed point, the dormitory operator recovers according to room level/comfort, moving either operator preserves the settled value, and restarting keeps both assignments and morale.
