# Historical activities + banners: gameplay-first milestone

Status: **evidence-gathering only**. The selector, client display, event battle
settlement and gacha spending are **not yet implemented by this milestone**.

## Scope and priority

Treat an activity and its contemporary gacha banners as a single player-facing
content release. Goal: select an archived release, enter event story/battle,
see its banners, roll operators, resume after restart. Permanent account
progress (operators, pity, event clear state) must not be erased by switching
releases.

Prioritize gameplay continuity, not event shop, daily missions, login rewards,
perfect loot distributions or base-production skill efficiency.

## Current evidence and limitation

- `res/excel/activity_table.json` includes `basicInfo` activity IDs and
  event timestamps, plus state grouped by activity type.
- `res/excel/gacha_table.json` includes `gachaPoolClient` (pool ID,
  opening/closing timestamps, rules, client descriptions).
- `bp_gacha.py` has separate advanced-gacha rule managers and character grant
  logic. This does **not** by itself prove banner display or currency
  consumption is correct for historical/normal accounts.
- `post_tutorial` is the fresh account preset. `sandbox` is an all-open
  template, not evidence of a legitimate event unlock protocol.
- Server `faketime` is a process-wide override; it is not a per-account
  release selector, and a server-only clock change is not evidence the PC
  client will show a historical banner.

Do not infer event-to-banner identity from date overlaps, matching display
text, nearby IDs or common operator names. Multiple banners may overlap a
single event, including unrelated standard banners. A single event can have
multiple phases, reruns or banners.

## Read-only evidence audit

From the server repository root:

```powershell
uv run python -m openbachelors.tool.activity_gacha_audit
uv run pytest -q test/test_activity_gacha_audit.py
```

Output: `data/activity_gacha_audit.json` (not a runtime selector).

The report identifies:

- Activity ID/type and resource start/end timestamps.
- Explicit pool-ID references found in the activity's own resource fields.
- Separate **time-overlap-only** pool candidates, never authoritative pairs.
- Zone-ID prefix candidates from local stages, not verified playable routes.
- Items without available evidence.

Report statuses describe **evidence classification**, not client or server
feature readiness. An explicitly referenced pool ID must still be checked for
client availability and banner rules before any release is opened.

## Next implementation milestone (PC sample required)

Pick one archived activity and its actual banner(s), **only after** identifying
a reliable activity/pool mapping and capturing the following Windows PC flows
on an isolated account:

1. Entry screen while active vs inactive: request paths, body, response,
   displayed activity title, availability flags and whether the client
   checks local timestamps.
2. Event screen: story viewing, battle start, win/fail, next-stage unlock,
   reopen, and restart. Event battle settlement must persist state even if
   optional rewards remain deferred.
3. Gacha screen: displayed pool IDs/detail/UP, one roll, ten rolls, sufficient
   and insufficient currency, new/duplicate operator, independent pity
   counters and retry/restart. Do not reuse a different pool's guarantee.
4. Switch historical release A -> B -> A without erasing either release's
   permanent unlocks, operators, owned skins, pity or accumulated progress.
5. Distinguish client-local GameData schedule gating, network-delivered
   availability and account save state. A server response alone cannot prove
   client-local display gating works.

Implement the selector **per isolated account**, with explicit backup/restore,
known pool IDs, stable persistence, and no automatic full-account opening.
Do not change `post_tutorial` defaults, `sandbox` semantics or live accounts.
Do not add a global clock override merely to make one archive appear.

## Acceptance conditions

- Chosen event and its *verified* related banners appear together on Windows PC.
- Enter story and battle; victory advances/unlocks properly; failure does not.
- A normal one-pull/ten-pull returns eligible operators, spends correctly,
  saves pity and duplicate/new acquisition, and rejects insufficient funds.
- Restart and release switching preserve event progress and character/pity
  state; old results cannot replay into a new draw or battle.
- No unverified banner is shown as part of the selected event.
- Tests and reports clearly distinguish implemented code, passing automated
  tests, and pending PC acceptance.

This phase is not complete until the Windows PC acceptance is recorded.
