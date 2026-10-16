# `post_tutorial` fresh-account preset

The historical preset name is retained for configuration and save-path
compatibility. Its base data now represents a new account immediately before
normal onboarding; it no longer skips the tutorial.

## Selecting the preset

Set `player_preset` in `conf/config.json`, then restart the server:

```json
"player_preset": "post_tutorial"
```

Use a dedicated login when testing. Existing saves are overlays and are never
silently cleared when this base template changes. A previously initialized
`post_tutorial` account therefore keeps its existing progress until it is
explicitly rebuilt.

The isolated save locations remain unchanged:

- multi-user files: `multi_sav/presets/post_tutorial/<username>/`
- single-user files: `sav/presets/post_tutorial/`
- database rows: username `post_tutorial::<username>`

Switching to `sandbox` exposes its original save and full-unlock template; it
does not convert either save.

## Fresh account contents

Initial values come from `res/excel/gamedata_const.json`:

- player level 1 and the level-1 sanity cap;
- `initPlayerGold` and `initPlayerDiamondShard` only;
- exactly the operators in `initCharIdList`, at E0 level 1 with unupgraded
  skills, no potential and no modules;
- empty squads, so normal squad-formation teaching can run;
- no completed stages, training stages, guide stories, guide missions, main
  missions or mission groups;
- no stage, story or mission rewards pre-granted.

The base template's special-operator protocol map and matching pinned value are
retained because they are client account-data metadata, not owned operators.

Generate the auditable empty reward ledger with:

```powershell
uv run preset-audit
```

## Safe backup, rebuild and restore

Nothing is rebuilt automatically. For a multi-user test account:

```powershell
uv run preset-save backup --username fresh_tutorial_test
uv run preset-save rebuild --username fresh_tutorial_test --yes
uv run preset-save restore --username fresh_tutorial_test --archive <backup.zip> --yes
```

For single-user mode, replace `--username ...` with `--single-user`. Rebuild
first creates a ZIP backup. Restore refuses to overwrite an existing save.
Database-backed deployments should use PostgreSQL-native backup/restore tools.

## Starting S and the PC client bridge together

From the OpenBachelorS directory, run:

```powershell
.\main-uv.cmd
```

It opens one terminal for the S server and one for the sibling
`OpenBachelorC\main-uv.cmd --attach_pc`. The C project must already have been
configured with `setup-pc-uv.cmd`.

## Current settlement limitation

`/quest/battleFinish` still returns empty reward arrays. It does not deduct
sanity, grant drops or experience, record stage completion, unlock later stages,
or advance tutorial and mission progress. Consequently the client can enter and
play the normal tutorial flow, but this server version cannot persist completion
past a battle. This preset change intentionally does not implement settlement.
