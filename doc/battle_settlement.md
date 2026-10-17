# Battle settlement (development scope)

The settlement engine is enabled only for the isolated `post_tutorial` preset.
`sandbox` keeps its previous endpoint behaviour. It combines audited protocol
evidence with generated rules for conservatively selected ordinary MAIN/SUB
stages and numbered mainline TR training stages.

## Protocol facts and trust boundary

Real client audit samples establish `completeState=2` and `3` (with
`interrupt=0`, `giveUp=0`) as two/three-star victories, and state 1 as a known
non-victory. Unknown encodings are rejected. These values remain client-supplied
evidence: this local server correlates and validates the protocol but does not
independently simulate the battle.

The client-visible all-zero `battleId` is retained for compatibility. Each
start has a persistent internal UUID. A finish must match the active stage and
battle ID. A settled fingerprint is idempotently replayed only when there is no
new active session; replaying an old result into a later all-zero-ID battle is
explicitly rejected.

Start and finish use the existing write-ahead extra-save journal. Absolute
mutations are recoverable after restart, and a settled response is cached so a
transport retry cannot roll rewards again.

## Generated ordinary rules

Run `uv run stage-settlement-rules` to rebuild:

- `data/stage_settlement_generated.json`: runtime rules with source metadata;
- `data/stage_settlement_coverage.json`: compact coverage and exclusions.
- `data/training_settlement_generated.json`: generated numbered mainline TR rules.
- `data/training_settlement_coverage.json`: per-TR rewards, missions, prerequisites,
  unlock candidates and excluded training families.

Selection is intentionally conservative: internal ID `main_`/`sub_`, main
zone, type MAIN/SUB, NORMAL difficulty, NORMAL_STAGE performance flag, not
story-only, not predefined, and not a `#` variant. This currently selects 255
stages (225 MAIN, 30 SUB). 239 are enabled; 16 are disabled because the resource
only displays an ONCE reward without a confirmable quantity. 238 enabled rules
also have an exact-version Penguin matrix match; `main_12-14` has deterministic
data but no matching matrix.

Stage table data supplies AP cost/refund, EXP, LMD, one-time diamond count and
unlock conditions. `mission_table.json` supplies `CompleteMainStage` mission
records and rewards. A completed mission is state 2 and remains a separate
claim action; claiming changes it to state 3 and grants the resource-defined
reward once.

Manual audited overrides remain in `data/stage_settlement_rules.json` only for
ordinary rewards whose quantities are not fully represented by the resource.
TR stages are no longer listed one by one there.

The current resource contains 25 numbered mainline training stages,
`tr_01` through `tr_25`; all 25 are generated and enabled. Their AP, refund,
EXP/LMD, diamond, prerequisite and downstream unlock conditions come directly
from `stage_table.json`. An ONCE+ALWAYS CHAR row becomes one operator acquisition,
using the real-client TR-1/TR-2 `charGet` protocol as the quantity/shape baseline.
TR-1 through TR-8 and TR-10 grant operators; TR-9 and TR-11 through TR-25 do
not. Mission links and rewards come from `mission_table.json`: TR-1 has
`main_2`, TR-2..7 have `sub_1..6`, TR-8..10 have `sub_38..40`, TR-11..14 have
`sub_46..49`, TR-15 has `sub_68`, and TR-16..25 have none. `preMissionIds` are
preserved and enforced when claiming; completion itself is still persisted.

## Approximate ordinary drops

Random ordinary drops use exact internal stage/item IDs from the local Penguin
Statistics CN matrix dataset. `quantity/times` is retained as a marginal mean,
never described as an official per-run probability. The runtime uses a
deterministic stochastic-rounding approximation seeded by the persistent
session/result fingerprint, then allocates the total within NORMAL_DROP and
EXTRA_DROP bounds. A retry therefore returns the same roll.

This approximation does not reconstruct mutual exclusion or correlations.
Every rule records sample counts, interval, algorithm, warning and source. The
dataset attribution is CC BY-NC 4.0; generated data must not be redistributed
without respecting that non-commercial licence and attribution requirement.

## Rewards and AP

Reward planning is centralized so the response and saved asset share one
mutation plan. Supported forms include CHAR (`charGet`), MATERIAL, CARD_EXP,
GOLD, DIAMOND, recruitment/instant-finish/gacha tickets, shards, social points,
AP items and common vouchers. CHAR acquisition updates troop and all required
references rather than inventory.

AP regeneration uses `gamedata_const.playerApRegenSpeed` (currently six
minutes per point), preserves a partial interval, and caps at `maxAp`. Spending
from a full bar starts the clock; spending while already below max no longer
resets it. The old template timestamp is initialized on first sync without
retroactively filling the bar.

## Boundaries and current blockers

- All 25 numbered mainline TR stages are generated. TR-S-1/TR-S-2 remain a
  separate special family and are excluded. Numbered TR-26/TR-27/TR-28 belong
  to activity zones in this resource version and are also excluded, along with
  tower `lt_tr_*` and activity-specific training stages.
- Story-only stages, practice, hard/special modes, annihilation and activity
  mechanics are not silently treated as ordinary or mainline training settlement.
- In chapter 0, 6 of 11 selected ordinary stages are enabled; in chapter 1,
  6 of 10 are enabled. Disabled stages list their unresolved first reward in
  the coverage report.
- Display rewards are never used as quantities. Furniture, unusual rewards,
  special AP protection and other unverified categories remain unsupported.
- Platform diamond selection follows the captured numeric platform field and
  still benefits from real captures on both platforms.
- The journal targets the normal single-process local deployment; it is not a
  distributed transaction or independent anti-cheat system.

Structured DEBUG events include rule source/completeness, result evidence, AP,
state, mission, unlock and reward summaries. Full ciphertext/decrypted battle
payloads remain confined to the opt-in audit JSONL.
