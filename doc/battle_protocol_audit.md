# Battle protocol audit

This module is for local protocol research only. It records requests without
performing battle settlement or changing the client-facing protocol.

## Enable it

Edit `conf/config.json` and restart the server:

```json
"battle_protocol_audit": {
    "enabled": true,
    "path": "battle_audit/battle_protocol.jsonl"
}
```

Use the single-worker development server while sampling:

```powershell
uv run poe dev
```

The in-memory request correlation is intentionally scoped to the development
process. Multi-worker production mode can route two requests to different
workers and is therefore unsuitable for this audit.

The JSONL output directory is ignored by Git. Each line has a server-run ID,
process-local sequence number, UTC timestamp, pseudonymous account reference,
independent audit ID, endpoint, stage ID, sanitized request and response, and
correlation status. `battleFinish.data` and replay payloads are replaced by
their length and SHA-256 digest. Successfully decrypted battle data is stored
only in this dedicated audit file and is suppressed from the ordinary debug
log while auditing is enabled. Decode failures store only the exception type.

## Local sampling procedure

1. Stop OpenBachelorS, enable the setting above, optionally remove the old
   `battle_audit/battle_protocol.jsonl`, and restart with `uv run poe dev`.
2. Start the client normally through the existing OpenBachelorC setup. No C
   changes are required.
3. On a disposable account, perform these separately: a normal three-star
   victory, a two-star victory, a failed/retreated battle, a practice battle,
   and a story-only stage.
4. After each scenario, note its stage and wall-clock time. Do not infer that a
   missing `battleFinish` is an error; the point of the capture is to observe
   which calls really occur.
5. Stop the server before copying or inspecting the JSONL file. Group lines by
   `audit_id` and order them by their file position or `sequence`.
6. Disable the setting and restart when sampling is complete.

Correlation diagnostics include `missing_battle_start`,
`repeated_battle_finish`, `stage_id_mismatch`, and a
`superseded_audit_id` when the same account starts another stage first.

Do not commit or share an audit file without reviewing its decrypted battle
content. Account tokens, cookies and authentication fields are not recorded,
but battle logs may still describe the player's squad and actions.
