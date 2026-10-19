# Mainline chapter-entry unlocks

Some ordinary CN mainline chapter entrances have an empty
`stage_table.stages[*].unlockCondition`. Their order is carried by
`slProgress`; `zone_table` identifies whether both sides are ordinary
`MAINLINE` zones.

The runtime infers a PASS prerequisite only when all of these hold:

- target is `MAIN` / `NORMAL`, has no explicit conditions and is not 0-1;
- target and predecessor belong to different `MAINLINE` zones;
- exactly one ordinary mainline stage has `slProgress == target - 1`.

Explicit `unlockCondition` always wins. `MAINLINE_ACTIVITY`, activity, TR-S,
alternate difficulty, story and other empty-condition stages are not inferred.

Read-only legacy-save audit:

```powershell
uv run mainline-unlock-save audit --username test123
```

After reviewing the preview, explicit repair:

```powershell
uv run mainline-unlock-save repair --username test123 --yes
```

Repair requires an empty pending delta, verifies the predecessor's actual
`dungeon.stages[*].state`, creates a ZIP backup, and adds only a normal state-0
stage record. It does not change `mainStageProgress`, rewards, stories,
missions, inventory, building, operators, or existing stage records. Repeated
execution is a no-op.
