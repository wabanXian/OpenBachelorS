# Normal-account operator skins

`post_tutorial` operator creation uses `skin_table.json` as its authority:

- `buildinEvolveMap[charId]["0"]` supplies a newly acquired operator's E0
  built-in skin.
- Patch/multi-template operators keep top-level `skin=null`; the base template
  uses its E0 evolve skin and alternate templates use `buildinPatchMap`.
- `sandbox` retains its original all-unlocked/current-outfit behavior.
- Only the currently equipped built-in skin is added to `characterSkins` for a
  new normal-account operator. Outfit ownership and current equipment are
  separate state and are never conflated by migration.

All normal-account creation paths share the same normalizer: initial roster,
stage/TR rewards, recruitment and headhunting. Existing operator objects are
not normalized at login, so a player's selected outfit is preserved.

## Existing-save audit

The audit is read-only:

```powershell
uv run operator-skin-save audit --username test123
```

It compares each equipped/template skin with the resource default and the old
sandbox-derived value. A matching legacy value is reported as a repair
candidate. For a currently equipped outfit, historical data has no reliable
"player selected this" marker, so the report explicitly says that manual
selection cannot be excluded. No repair happens during login.

After reviewing and explicitly accepting the candidate list:

```powershell
uv run operator-skin-save repair --username test123 --yes
```

Repair refuses a non-empty pending delta, creates a complete ZIP backup under
`save_backups/post_tutorial/operator_skin/`, and changes only reported
`troop.chars.*.skin` / `tmpl.*.skinId` paths. It never deletes or adds
`skin.characterSkins` or `skin.skinTs`; legacy ownership cleanup remains a
separate, evidence-dependent decision.

## Explicit legacy ownership revocation

`audit` also reports `ownershipRemovalCandidates`. A candidate must be a real
outfit (`isBuySkin=true`), not an evolve/patch built-in, match the exact outfit
equipped by the old sandbox source operator, and belong to an operator on the
account. The report includes save-local acquisition evidence and keeps any
non-matching outfit under `uncertainExtraOutfits`.

After reviewing that preview, ownership cleanup is a separate explicit action:

```powershell
uv run operator-skin-save revoke-legacy-outfits --username test123 --yes
```

This action makes a full ZIP backup, repairs any still-matching legacy equipped
skin, removes only previewed outfit keys from `characterSkins`, `skinTs`, and
`skinSp`, and retains/adds the operator's resource-defined built-in skin. It
does not touch level, trust, potential, skills, modules, stages, building,
missions, inventory, currencies, or tutorial progress. Deletions are written
as overlay tombstones, so the post_tutorial base template cannot restore them
on reload. No action runs automatically at login.

Legitimate `CHAR_SKIN` mission/activity rewards remain supported: they add the
skin ownership and timestamp once and repeated reward application is
idempotent.
