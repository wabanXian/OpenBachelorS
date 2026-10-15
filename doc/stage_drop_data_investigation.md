# CN stage drop data investigation

Investigation date: 2026-10-15. This report covers data acquisition and
validation only. Nothing described here is connected to battle settlement.

## Sources and licensing

Penguin Statistics documents its public API data as CC BY-NC 4.0. The tool
uses these official endpoints:

- `https://penguin-stats.io/PenguinStats/api/v2/stages?server=CN`
- `https://penguin-stats.io/PenguinStats/api/v2/result/matrix?server=CN&show_closed_zones=true`
- `https://penguin-stats.io/PenguinStats/api/v2/items?server=CN`

Required attribution: “Drop-rate statistics © Penguin Statistics, licensed
CC BY-NC 4.0.” The generated raw cache and transformed dataset are excluded
from Git. They must not be redistributed with OpenBachelorS until the
repository's distribution model has been reviewed for attribution and the
non-commercial restriction.

Penguin's public GitHub repositories contain API/backend implementations, but
the investigation did not find a maintained, licensed snapshot repository
that replaces the live matrix as a complete historical data dump. The matrix
API's `show_closed_zones=true` option is therefore used for available expired
activity statistics. Matrix rows expose only the selected interval for each
stage/item pair; older non-accumulable intervals require Advanced Query and
are not silently reconstructed.

PRTS Wiki's normal-stage drop template distinguishes normal, first, special,
lucky and extra drops, including fixed, probability, first-drop and
three-star rewards. This supports keeping those concepts separate. PRTS states
that its text is licensed under CC BY-NC-SA 4.0. It was not used as a bulk
machine-readable source: its page-oriented records do not provide a verified
one-to-one export keyed by the game's internal stage IDs, and mixing its
share-alike data into a differently licensed dataset was not assumed safe.

The local `res/excel/stage_table.json` is the authoritative set of 3,614 IDs
for matching. Display names and stage codes are never used as join keys.

## Live API validation result

The three Penguin endpoints returned HTTP 200 with no fetch errors:

| Measure | Result |
| --- | ---: |
| Local stages | 3,614 |
| Penguin stage metadata rows | 1,620 |
| Penguin matrix rows | 12,526 |
| Exact-ID metadata matches | 1,067 (29.52%) |
| Exact-ID stages with matrix data | 1,048 (29.00%) |
| Penguin-only stage IDs | 553 |
| Local stages below 100 maximum samples | 77 |
| Matched stages without Penguin `dropInfos` rules | 628 |
| Stages with multi-quantity evidence | 371 |
| Resource/version mismatch records | 537 |

Coverage by locally classified group:

| Group | Local | Metadata | Matrix |
| --- | ---: | ---: | ---: |
| Mainline (`MAIN` + `SUB`) | 796 | 396 | 384 |
| Resource (`DAILY`) | 35 | 35 | 35 |
| Activity | 2,495 | 636 | 629 |
| Other | 288 | 0 | 0 |

The `other` group is primarily tower, campaign, guide and story-only content
which Penguin does not model as ordinary farmable drop stages. Low overall
coverage therefore does not mean the API failed to return 71% of ordinary
farming stages.

With closed zones enabled, Penguin supplied 997 expired CN activity stages.
Of those, 628 matched local IDs and 621 also had matrix observations. This is
the usable historical activity coverage in the current API response.

All 537 resource mismatches contain a `zoneId` difference; 12 also contain an
`apCost` difference. The common cause is an old stage ID reused under a rerun
or a changed local zone. These rows remain in the research dataset but are
flagged and must not be consumed automatically.

## Semantics and limitations

Penguin matrix `quantity` is the total number of items observed and `times`
is the number of stage samples for that row's `[start, end)` interval.
`quantity / times` is an expected item quantity per run, not necessarily the
probability of receiving the item. In particular, 371 matched stages have
evidence that an item can occur more than once, either from Penguin bounds or
because `quantity > times`.

Drop class comes from Penguin stage `dropInfos`: `NORMAL_DROP`, `EXTRA_DROP`,
`SPECIAL_DROP`, and `FURNITURE`. Bounds with a positive lower value establish
a guaranteed quantity or range; a zero lower bound establishes only that the
item is not guaranteed. Missing bounds remain unknown. Marginal matrix rows
cannot establish which items are mutually exclusive, so every observation
states that exclusivity is undetermined.

Penguin does not provide first-clear rewards in the drop matrix. The offline
dataset therefore preserves local `diamondOnceDrop`, `COMPLETE` display rows
and `ONCE` display rows separately. In the local table, 2,928 stages have one
`diamondOnceDrop`, 3,068 stages display at least one `COMPLETE` reward, and
227 display an `ONCE` reward. Display rows frequently omit counts; the tool
does not invent them or convert them into settlement rules.

The generated format contains every local stage, exact-ID match status,
local fixed progression values, grouped display rewards, Penguin metadata,
raw `quantity`, `times`, `start`, `end`, bounds, sample warnings and resource
mismatches. It is suitable as input to later rule review, not as an executable
loot table.

## Reproduction

Network acquisition:

```powershell
uv run --no-sync python -m openbachelors.tool.stage_drop_dataset --output-dir drop_data
```

Rebuild without network access from the saved raw responses:

```powershell
uv run --no-sync python -m openbachelors.tool.stage_drop_dataset --output-dir drop_data --source-dir drop_data/raw
```

Outputs are `drop_data/stage_drop_dataset_cn.json`,
`drop_data/coverage_report_cn.json`, and `drop_data/raw/*.json`. Download
errors are written to `drop_data/fetch_errors.json` and missing required
sources cause a non-zero exit instead of a partial dataset being presented as
complete.
