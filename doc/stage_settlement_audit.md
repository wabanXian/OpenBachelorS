# Ordinary stage settlement audit

Run from the repository root:

```powershell
uv run python -m openbachelors.tool.stage_settlement_rules
uv run python -m openbachelors.tool.stage_settlement_audit
uv run pytest -q test/test_generic_settlement.py
```

The machine-readable comparison is `data/stage_settlement_audit.json`. It lists
all 255 selected ordinary stages, their deterministic fields, first-pass rules,
mission/unlock inputs, random-drop classification, and the independent checks
performed against `stage_table.json` and the manual evidence file.

## Coverage

- Ordinary MAIN/SUB NORMAL stages selected by the generator: 255
- Settlement enabled: 255
- Deterministic/local audit errors: 0
- Exact Penguin stage matrix available: 254
- No exact matrix: `main_12-14`; deterministic settlement remains supported,
  but no random material is invented.
- Random drops remain explicitly labelled marginal single-player
  approximations. `quantity / times` is never treated as an exact per-run
  probability distribution.

## Cross-checked first-pass overrides

| Stage | Local reward | Count | External evidence |
|---|---:|---:|---|
| 0-9 | Pure Gold (`3003`) | 2 | https://arknights.wiki.gg/wiki/Pure_Gold |
| 0-10 | Ansel (`char_212_ansel`) | 1 | https://arknights.wiki.gg/wiki/Ansel/Overview |
| 0-10 | Carbon Stick (`3112`) | 6 | https://arknights.wiki.gg/wiki/Carbon_Stick |
| 0-11 | Melantha (`char_208_melan`) | 1 | https://arknights.wiki.gg/wiki/Melantha/Overview |
| 0-11 | Light Building Material (`3131`) | 15 | https://arknights.wiki.gg/wiki/Light_Building_Material |
| 1-1 | Keel (`3105`) | 1 | https://arknights.wiki.gg/wiki/Keel |
| 1-1 | Light Building Material (`3131`) | 4 | https://arknights.wiki.gg/wiki/Light_Building_Material |
| 1-7 | Orchid (`char_278_orchid`) | 1 | https://arknights.wiki.gg/wiki/Orchid/Overview |
| 1-8 | Keel (`3105`) | 1 | https://arknights.wiki.gg/wiki/Keel |
| 1-12 | Shaw (`char_277_sqrrel`) | 1 | https://arknights.wiki.gg/wiki/Shaw |
| 2-10 | Amiya token (`p_char_002_amiya`) | 1 | https://arknights.wiki.gg/wiki/Amiya/Overview |
| 3-4 | Keel (`3105`) | 1 | https://arknights.wiki.gg/wiki/Keel |
| 3-8 | Amiya token (`p_char_002_amiya`) | 1 | https://arknights.wiki.gg/wiki/Amiya/Overview |
| 4-7 | Keel (`3105`) | 1 | https://arknights.wiki.gg/wiki/Keel |
| 4-9 | Amiya token (`p_char_002_amiya`) | 1 | https://arknights.wiki.gg/wiki/Amiya/Overview |
| 7-2 | THRM-EX (`char_376_therex`) | 1 | https://arknights.wiki.gg/wiki/THRM-EX/Overview |
| 7-18 | Amiya token (`p_char_002_amiya`) | 1 | https://arknights.wiki.gg/wiki/Amiya/Overview |

Every row is also backed by an `ONCE` + `ALWAYS` entry in the local
`stage_table.json`. Counts are not inferred from Penguin Statistics.

For a first-clear operator already owned by the account, settlement returns a
`CHAR` reward with `charGet.isNew = 0`, grants the operator's potential token,
and does not create a second character instance. Story Amiya rewards therefore
grant `p_char_002_amiya` without headhunting certificate bonuses.

## Outside the 255-stage ordinary engine

The audit classifies every stage in `stage_table.json` without enabling it:

- 30 main TR stages (handled by the separate training rule engine)
- 204 story-only stages
- 9 predefined/performance stages
- 833 special-difficulty stages
- 35 annihilation stages
- 73 event tutorial stages
- 1,879 resource/event stages
- 293 MAIN/SUB stages outside the conservative ordinary selection

Normal mainline progression still contains special protocol boundaries. The
first known one is `main_06-15` / 6-17: a zero-sanity predefined performance
stage whose first clear supplies an Amiya token. Later examples include JT8-1,
14-10, 14-22, 15-2, 15-19, 16-9, 16-18, and 17-16. They are deliberately not
treated as ordinary battles until their client request/finish protocol is
verified.

This audit reads resources and generated data only. It never opens, rewrites,
or migrates player saves.
