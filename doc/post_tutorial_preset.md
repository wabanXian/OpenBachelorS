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

## Fresh account contents and evidence boundary

The following initial values are derived from
`res/excel/gamedata_const.json`:

- player level 1 and the level-1 sanity cap;
- `initPlayerGold` and `initPlayerDiamondShard` only;
- the operators listed in `initCharIdList`, at E0 level 1 with unupgraded
  skills, no potential and no modules;
- empty squads, so normal squad-formation teaching can run;
- only `main_00-01` present at unlocked/unplayed state `0`; no completed
  stages, training stages, guide stories, guide missions, main missions or
  mission groups;
- no stage, story or mission rewards pre-granted.

The base template's special-operator protocol map and matching pinned value are
retained because they are client account-data metadata, not owned operators.

`initCharIdList` shows that these operators belong to initialization, but the
static resources do not prove whether the official server grants all of them
before the first `syncData`, temporarily supplies any during a tutorial, or
grants them at separate checkpoints. The tables also do not define the meaning
of the inherited `status.flags.init` flag or the condition for the client's
pre-home opening cinematic. A real fresh-account `syncData` and request capture
is required; this preset does not invent extra flags to conceal that gap.

## Resource-confirmed guide chain

The first server-visible guide entries in `story_table.json` are:

| progress | story | trigger/requirement | result |
| --- | --- | --- | --- |
| 0 | `obt/guide/l0-0/0_home_ui` | home page loaded | progress 10 |
| 10 | `obt/guide/l0-0/1_recruit_adv` | previous story finished or home loaded | progress 20 |
| 20 | `obt/guide/l0-0/2_make_squad` | previous story finished | progress 30 |
| 30 | `obt/guide/l0-0/3_battle_ui` | previous story finished | progress 100 |
| 100 | `obt/guide/l0-1/0_mission_main` | `main_00-01` passed, home/stage page loaded | progress 110 |

`stage_table.json` gives `main_00-01` no stage unlock prerequisite and
`zone_table.json` declares it as the first stage of `main_0`. The initial stage
record therefore uses the normal seven-field stage shape with state `0` and all
counters zero. `mainStageProgress` deliberately remains `null` until client
sampling proves that the official first sync requires a different value.

`/story/finishStory` now persists the story flag, declared `setFlags`, and a
monotonic `setProgress`. It does not guess client-internal trigger conditions.
Story `completedRewards` outside the initial reward-free entries are logged but
are not yet granted.

`/gacha/advancedGacha` now persists the returned operator and its required
troop, building, dex-navigation and skin references. A repeated draw returns
the existing instance instead of creating a duplicate. Static files do not
identify a special teaching-pool request or prove its currency-consumption
rules, so those details still require a client capture.

## Tutorial diagnostics

Diagnostics require top-level `debug: true` and this explicit switch:

```json
"tutorial_debug": {
  "enabled": true
}
```

Compact JSON DEBUG events cover preset selection/load, `syncData` summaries,
story completion, advanced recruitment, squad changes, battle start/finish,
story-stage finish and save persistence. They contain a request ID and one-way
account hash. Tokens are never printed, battle data is represented by length,
and the full `syncData` response is suppressed by the optional HTTP trace.

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

## Current settlement scope

`main_00-01` now has a persistent, idempotent settlement path for the
`post_tutorial` preset. It records two- or three-star completion, writes the
resource-backed AP/EXP/LMD and first-clear rewards, applies the explicitly
labelled Penguin-statistics battle-record approximation, and unlocks `tr_01`.
It does not force tutorial progress; the client must finish the next guide
story normally. Passing 0-1 also exposes `main_1` as completed but unclaimed;
claiming it through the client grants the resource-defined five basic battle
records exactly once. Older isolated saves that already passed 0-1 receive the
missing pending mission record without receiving its reward automatically.

Numbered mainline TR stages are generated from the current stage and mission
resources. TR-1 grants Hibiscus, unlocks 0-2 and exposes `main_2` without
claiming it.
Ordinary MAIN/SUB stages are now selected and generated from resource tables;
unconfirmed first-pass rewards disable a stage instead of being guessed.
All 25 `tr_01` through `tr_25` main-zone stages are supported by the generated
training engine, including resource-defined operators, diamonds, MAIN/SUB
missions and conditional downstream unlocks. TR-S, activity, tower, story and
other special-mode stages remain outside it. See `doc/battle_settlement.md`,
`data/stage_settlement_coverage.json` and
`data/training_settlement_coverage.json` for exact
coverage, evidence, retry behaviour and limitations.
