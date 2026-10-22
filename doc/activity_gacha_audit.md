# Historical activities and gacha schedules

## Runtime architecture

Historical content no longer uses additive activity/pool allow-lists and never
changes the account/global clock.

- Permanent side stories and intermezzi use `retro_table.retroActList` and the
  normal `retro.block` unlock state. They coexist and ignore the selected date.
- Limited activities are evaluated independently against
  `activity_table.basicInfo.startTime/endTime`.
- Gacha display schedules are evaluated independently against
  `gacha_table.carousel.startTime/endTime`. Every pool naturally active on the
  selected date is reported; pools are not opened because an activity happens
  to be active.
- With no selected date, availability uses real server time.
- The selected date is stored only in account `extra.json` under
  `historical_content_date_v1`. AP recovery, building production, training,
  missions, timestamps in `/account/syncData`, and every other game timer keep
  using the normal server clock.

Changing or resetting the content date never removes dungeon progress,
operators, event records, gacha records, or pity.

## Commands

Show current mode:

```powershell
uv run historical-content-save show-date --username ACCOUNT
```

Select a China-server calendar date (explicit write and ZIP backup):

```powershell
uv run historical-content-save set-date --username ACCOUNT `
  --date 2019-11-20 --yes
```

Return to real-time content availability (also backed up):

```powershell
uv run historical-content-save reset-date --username ACCOUNT --yes
```

The date is evaluated at 12:00 UTC+8 to avoid accidentally selecting the
preceding schedule day. It is a content filter, not faketime.

## Permanent retrospectives

`POST /retro/unlockRetroBlock` now implements the current PC protocol
(`RetroUnlockRetroBlockRequest.retroId`, response `result`). It checks that the
retro is permanently published, consumes one information fragment, creates the
canonical `{locked: 0, open: 1}` block, and unlocks the verified entry stage
only when its actual stage prerequisite is satisfied. Repeated requests are
idempotent.

The original sandbox continues to have every retro block open. Normal accounts
retain their own unlocked-block set.

## Verified playable sample: Code of Brawl

The first audited release remains `code_of_brawl_original`:

- activity `act5d0`;
- permanent record `permanent_sidestory_3_Code_Of_Brawl`;
- debut banner `NORM_5_0_1`.

Evidence is recorded in `data/historical_releases.json`. Code of Brawl can be
entered either during its original limited window or through its independently
unlocked permanent retro block.

Its audited stages reuse the persistent battle-session engine and resource
AP/EXP/LMD/unlock rules. CB-5 grants the explicit one-time Bison reward.
Story-only nodes persist completion and unlock their resource-defined
successor. Random event drops and the event shop remain intentionally pending.

`NORM_5_0_1` is allowed only when its own carousel window contains the selected
date. It reuses the existing gacha engine, period roster, UP data, currency
deduction, operator grant, duplicate conversion, and persisted pity. Other
pools active on that date are discovered independently, but are not claimed
fully audited merely because their schedule is known.
Their windows can be displayed, but draw requests are explicitly rejected with
`historical_pool_rules_not_audited` until period roster, UP and pity semantics
are verified; the server never substitutes the current roster.

## Windows PC display boundary

`dump.cs` and the saved decompilation evidence show that
`RecruitDataConverter.LoadValidGachaPools` calls
`GachaDB.GetValidPoolsEnumerator()`, which filters the client's static carousel
before server availability is considered. The server deliberately does not
send a historical `/account/syncData.ts`, because that would also distort normal
timers.

Consequently the server-side schedule, permanent retro lifecycle, event battle,
and draw persistence are automated-test verified. Actual Windows PC rendering
of a historical carousel date still needs an opt-in, narrowly scoped client
date predicate or runtime confirmation that the current launcher already
supplies one. No global time hook or cross-year pool coexistence hack has been
added, and PC UI success is not claimed yet.

## Verification

`test/test_historical_content.py` covers date-derived independent schedules,
multiple naturally overlapping pools, permanent retro independence, retro coin
and idempotency, progress/pity preservation across dates, Code of Brawl story
and reward rules, a persisted banner draw, and set/show/reset command behavior.

The local focused run completed 11/11 historical-content and audit tests. The
full `test/` run completed 719 tests; two pre-existing network-download tests
failed under the blocked proxy and four tests could not acquire pytest's
temporary directory because of the local Windows ACL. No product assertion
failed in that run.
# Historical gacha update (2019-11-20)

The account-scoped historical date now authorizes every audited normal pool in
that GameData window independently of activity selection.  For 2019-11-20 the
implemented pools are:

- `NORM_4_0_5`: Hoshiguma/Siege (six-star rate-up) and Sora/Glaucus/Blue
  Poison (five-star rate-up).  Its available roster is cut off at Magallan,
  Executor, and Vermeil for six/five/four stars respectively.
- `NORM_5_0_1`: Mostima; Waai Fu/Firewatch; May, with the roster cut off at
  those debut operators.

GameData supplies the exact windows and client presentation.  The historical
operator mapping is cross-checked against the 2019 banner table at
https://wiki.biligame.com/arknights/用户:418054283/sandbox/寻访池一览/2019 .
Both pools use the existing standard rarity rates (2%/8%/50%/40%), shared
normal-pool six-star pity, per-pool first-ten five-star-or-higher guarantee,
normal currency/ticket consumption, operator grant/duplicate conversion, and
the existing request replay journal.

`POST /account/historicalContentDate` is a read-only, session-authenticated
bridge for OpenBachelorC.  It returns the content-only timestamp and never
changes the server game clock.
