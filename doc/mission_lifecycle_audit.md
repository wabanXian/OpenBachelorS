# Mission lifecycle audit

`post_tutorial` reconciles resource-defined `MAIN`, `SUB`, and `GUIDE` missions
when a player is initialized or loaded. Reconciliation is additive and
idempotent: it exposes unlocked incomplete missions, preserves completed and
claimed records, and never grants rewards.

Mission records deliberately do not live in the shared preset template. They
are reconciled only after the account save delta is overlaid. This prevents a
new template key from shadowing a legacy delta key during direct reads while
reappearing during final `copy()` serialization.

## Confirmed lifecycle

- Client/reference syncData evidence uses state `2` for an exposed mission,
  whether incomplete or complete. `progress.value >= progress.target` controls
  claimability; claimed missions use state `3`. Legacy state `1` records were
  accepted by JSON but hidden by the client and are migrated to `2`.
- `missionGroups` values remain JSON numbers. `0` is active; ordinary
  `MAIN`/`SUB` groups advance to `1` only after every mission in the group is
  claimed.
- `preMissionGroup`, `preMissionIds`, and `StageCondition` come from
  `mission_table.json`. Successors appear only when those conditions hold.
- Existing saves are hydrated without deleting, resetting, or rewarding any
  mission. Already exposed legacy groups remain exposed for compatibility.
- Legacy GUIDE groups incorrectly marked `1` after a single mission claim are
  restored to active state `0` only when not all group missions are claimed.
- Passed stages can reconstruct `CompleteMainStage` and `CompleteStage`
  progress. Other progress changes only through an implemented endpoint.

## Implemented progress sources

- `CompleteMainStage`, `CompleteStage`: battle settlement.
- `UpgradeChar`: `/charBuild/upgradeChar`.
- `UpgradePlayer`: player-level settlement.

Other unlocked GUIDE missions are visible as incomplete, but their progress is
not fabricated. DAILY, WEEKLY, and SPECIAL_OPERATOR_WEEKLY are not initialized
because their reset windows, point rewards, and group claim protocols are not
verified. GUIDE group reward claiming is likewise not implemented yet.

## Audit command

```powershell
uv run python -m openbachelors.tool.mission_lifecycle_audit
```

This writes `data/mission_lifecycle_audit.json` with every mission's visibility,
progress support, and known limitation.
