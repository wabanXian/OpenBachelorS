# Operator trust settlement audit

## Persisted fields

- `troop.chars[instId].favorPoint` is the owned operator instance counter used
  by the operator detail data.
- `troop.charGroup[charId].favorPoint` is the character-group counter. It is a
  separate persisted value and is shared by a multi-form operator such as
  Amiya; a form is not a second owned instance.
- New grants initialise both counters to zero and the existing sandbox builder
  initialises both to `favor_table.maxFavor`. This establishes the expected
  paired update, but does not prove that legacy saves can never diverge.
  Settlement therefore adds the same earned delta to each counter from its own
  previous value instead of overwriting either counter with the other.

`res/excel/favor_table.json` is authoritative for point thresholds, displayed
percentages, and the cap. In the current resource version it contains 201
frames from 0% through 200% and `maxFavor` is 25570.

## Implemented battle rule

For supported ordinary battles, a successful two- or three-star settlement
adds the session's actually charged AP to every account-owned operator in the
frozen battle-start squad. Deployment, retreat, and survival inside the stage
do not alter eligibility. An assist is not owned and is excluded.

The current CN rule (effective since the 2025-12-05 client update) uses actual
sanity consumed for completed operations; the older two-star reduction is not
used. The settlement engine currently supports neither annihilation nor
multi-run protocols, so `session.apCost` is the complete actual cost for its
supported stage set.

No trust is granted for a failed, interrupted, or given-up battle, a zero-AP
TR battle, or a rejected practice start. Trust mutations are absolute values
inside the existing write-ahead settlement transaction, so retry, duplicate
finish, and process recovery cannot roll a second gain.

## Squad snapshot

`battleStart` freezes `charInstId`, base `charId`, and the selected
`currentTmpl`. Every instance and multi-form template is validated against the
account before AP is charged. `squad: null` is accepted for client-provided TR
formations and creates no trust-eligible owned snapshot.

## Not implemented or not yet evidenced

- Event trust bonuses: `activity_table.json` contains dated `favorUpList`
  operator membership but does not by itself provide a complete multiplier,
  applicable-stage, and active-period settlement rule. No bonus is guessed.
- RIIC daily interaction, Control Center assistant, and floor assistant trust:
  the building router currently has assignment/sync endpoints but no trust
  collection endpoint or settlement implementation. Their client request and
  response protocols still need captures.
- Annihilation, all-delegation, multi-run, and other special settlement
  protocols remain outside the ordinary battle engine.
