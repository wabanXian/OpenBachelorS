# Resource-driven mission lifecycle

The post-tutorial preset loads mission definitions from
`res/excel/mission_table.json`. It does not pre-complete or pre-reward tasks.

## Lifecycle

- MAIN, SUB and GUIDE visibility follows `missionGroup`, `preMissionGroup`,
  `preMissionIds`, `unlockCondition` and `unlockParam`.
- DAILY selects the active entry from `dailyMissionPeriodInfo`, including the
  weekday/weekend group. WEEKLY selects the time-valid weekly group.
- Periodical missions unlock after the resource story
  `obt/guide/l0-9/0_mission_others` reaches progress 1000.
- CN server time and the 04:00 boundary define logical days and weeks.
- Period markers live in the account extra-save journal, not the client-visible
  mission schema. Old saves are extended in place.
- A claimed DAILY/WEEKLY mission adds its resource `periodicalPoint`. Milestone
  claims use `/mission/exchangeMissionRewards`; GUIDE stage rewards use
  `/mission/confirmMissionGroup`.

The current PC client evidence for this project uses mission `state=2` for a
visible row even when its progress is incomplete. The server therefore checks
the progress array, not state alone, before allowing a claim. `state=3` means
claimed. `missionGroups` remains a numeric mapping as required by the client.

## Verified event hooks

- successful battle: stage, stars and kill count;
- successful operator EXP upgrade;
- squad formation;
- successful public-recruit result collection;
- successful trading-post order delivery;
- durable state reconciliation: stage completion, player level, owned
  operator, operator level and built rooms.

Battle events are emitted only after the persisted settlement session reaches
its prepared commit path. Duplicate `battleFinish`, failures and withdrawals
therefore do not increment successful-battle tasks.

## Coverage and limitations

Run:

```powershell
uv run python -m openbachelors.tool.mission_template_audit
```

The generated `data/mission_template_coverage.json` distinguishes decoded
templates from unsupported templates. A decoded template still needs a real
server operation hook before gameplay can emit it. Systems not implemented by
the server (for example workshop synthesis, clue exchange and several advanced
operator-growth operations) never receive fabricated progress.

The periodical milestone route name is present in the reference route set.
Its request aliases are accepted conservatively (`rewardId` or `id`); the exact
current-PC-client request body and reward presentation still require live
client confirmation.
