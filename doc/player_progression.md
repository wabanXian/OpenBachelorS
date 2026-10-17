# Doctor EXP and level settlement

`status.exp` is the remaining EXP within the current Doctor level. The entry
at `gamedata_const.playerExpMap[level - 1]` is consumed to enter the next
level, and leftover EXP carries into the following level. This repeats until
the remaining EXP is below the next threshold or level 120 is reached.

For every crossed level:

- `status.level` advances by one;
- `status.maxAp` becomes `playerApMap[newLevel - 1]`;
- that new-level AP amount is added directly to `status.ap`;
- AP is allowed to exceed `maxAp` and is never truncated;
- `lastApAddTime` is left unchanged. Existing battle-start logic resets the
  regeneration clock when spending from an over-cap bar.

At maximum level, battle EXP is not retained and `status.exp` is normalized
to zero. `apLimitUpFlag` is not changed: the local resource contains no rule
linking it to ordinary level progression.

The level-2 and level-30 `UpgradePlayer` guide missions are sourced from
`mission_table.json`. They become claimable only after their resource-defined
level and stage conditions are satisfied, and use the existing idempotent
mission reward path.

## Existing save repair

The repair tool defaults to dry-run and changes exactly one explicitly named
filesystem save. It refuses non-`post_tutorial` saves. `--apply` first copies
`delta.json`, `pending_delta.json`, and `extra.json` into a timestamped backup
directory, then atomically replaces only `delta.json`.

```powershell
uv run python tools/repair_player_progression.py `
  --save-dir multi_sav/presets/post_tutorial/test123
```

After reviewing the preview:

```powershell
uv run python tools/repair_player_progression.py `
  --save-dir multi_sav/presets/post_tutorial/test123 `
  --apply
```

Stop the server before applying a filesystem repair. Re-running the command is
safe: a consistent save reports `already consistent` and is not rewritten.
