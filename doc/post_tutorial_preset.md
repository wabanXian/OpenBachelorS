# post_tutorial player preset

`post_tutorial` is an isolated first-pass preset for an account that has skipped
the mandatory onboarding and completed the main story through 1-10, while still
retaining normal operator and stage progression.

## Selecting the preset

Set `player_preset` in `conf/config.json` and restart the server:

```json
"player_preset": "post_tutorial"
```

Use a dedicated test login such as `post_tutorial_test`. Switching back to
`sandbox` exposes the original save again; it does not convert either save.

The `sandbox` preset deliberately keeps the legacy save locations. The
`post_tutorial` preset uses:

- multi-user files: `multi_sav/presets/post_tutorial/<username>/`
- single-user files: `sav/presets/post_tutorial/`
- database rows: username `post_tutorial::<username>`

This prevents a global base-template change or a configuration switch from
overlaying a save delta created for another preset.

## Explicit save operations

File-backed multi-user example:

```powershell
uv run preset-save backup --username post_tutorial_test
uv run preset-save rebuild --username post_tutorial_test --yes
uv run preset-save restore --username post_tutorial_test --archive <backup.zip> --yes
```

For single-user mode, replace `--username ...` with `--single-user`. Rebuild
always creates a ZIP backup before removing the selected preset save. Restore
refuses to overwrite an existing save. Database-backed deployments use the same
row namespace, but should use PostgreSQL-native backup/restore tooling.

## Progress and rewards

- Completed: 0-1 through 0-11, 1-1 through 1-10, and TR-1 through TR-10.
- Claimed guide missions: `guide_1` through `guide_18` (groups 1 and 2 only).
- Guide groups 3 through 8, later main missions, stories, stages and activities
  remain incomplete.
- Deterministic stage `ONCE` and `COMPLETE` rewards, matching main mission
  rewards, the selected guide mission rewards, and the two selected guide group
  rewards are granted exactly once by the immutable base preset.
- Random `NORMAL` and `ADDITIONAL` stage drops are excluded because the resource
  table provides no deterministic count.
- A clearly labelled opening subsidy of 40,000 LMD, 20 basic battle records and
  10 frontline battle records compensates for skipped upgrade prompts. It is
  not presented as an official mission reward.

Generate the line-by-line audit ledger with:

```powershell
uv run preset-audit
```

The checked-in result is `doc/post_tutorial_reward_audit.json`. Every row records
its source, reward type, item ID, count, target save field and quantity basis.

## Current battle settlement limitation

`/quest/battleFinish` currently returns empty reward arrays. It does not deduct
sanity, grant drops or experience, advance a stage state, unlock subsequent
stages, or update mission progress. This preset only supplies a consistent
initial save; it intentionally does not turn battle settlement into a complete
reward/task system.
