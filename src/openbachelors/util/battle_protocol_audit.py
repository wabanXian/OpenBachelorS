import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import aiofiles

from ..const.filepath import CONFIG_JSON
from .const_json_loader import const_json_loader
from .helper import decode_battle_log


DEFAULT_OUTPUT_PATH = "battle_audit/battle_protocol.jsonl"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "session",
    "token",
)
OPAQUE_FIELDS = {"battleReplay", "data"}


def _redact(value, key=None):
    if key is not None:
        lower_key = key.lower()
        if any(part in lower_key for part in SENSITIVE_KEY_PARTS):
            return "<redacted>"
        if key in OPAQUE_FIELDS and isinstance(value, str):
            return {
                "omitted": True,
                "length": len(value),
                "sha256": sha256(value.encode()).hexdigest(),
            }
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _account_ref(request):
    token = request.headers.get("secret", "_")
    return f"acct_{sha256(token.encode()).hexdigest()[:16]}"


def _find_stage_id(value):
    if isinstance(value, dict):
        for key in ("stageId", "stage_id"):
            stage_id = value.get(key)
            if isinstance(stage_id, str):
                return stage_id
        for child in value.values():
            stage_id = _find_stage_id(child)
            if stage_id is not None:
                return stage_id
    elif isinstance(value, list):
        for child in value:
            stage_id = _find_stage_id(child)
            if stage_id is not None:
                return stage_id
    return None


class BattleProtocolAudit:
    def __init__(self, enabled=None, output_path=None):
        root_config = const_json_loader[CONFIG_JSON].copy()
        config = root_config.get("battle_protocol_audit", {})
        self.enabled = config.get("enabled", False) if enabled is None else enabled
        configured_path = config.get("path", DEFAULT_OUTPUT_PATH)
        self.output_path = Path(output_path or configured_path)
        self._lock = asyncio.Lock()
        self._run_id = str(uuid4())
        self._sequence = 0
        self._accounts = {}

    def reset_runtime_state(self):
        self._sequence = 0
        self._accounts.clear()

    def _new_session(self, account, stage_id, kind="battle"):
        previous = self._accounts.get(account)
        superseded_audit_id = None
        if previous is not None and previous["state"] == "started":
            previous["state"] = "superseded"
            superseded_audit_id = previous["audit_id"]
        session = {
            "audit_id": str(uuid4()),
            "stage_id": stage_id,
            "state": "started" if kind == "battle" else "story",
            "finish_count": 0,
        }
        self._accounts[account] = session
        return session, superseded_audit_id

    def _finish_session(self, account, decoded_stage_id):
        session = self._accounts.get(account)
        if session is None or session["state"] in {"story", "superseded"}:
            session = {
                "audit_id": str(uuid4()),
                "stage_id": decoded_stage_id,
                "state": "orphan_finish",
                "finish_count": 1,
            }
            self._accounts[account] = session
            return session, "missing_battle_start"

        session["finish_count"] += 1
        if session["finish_count"] > 1:
            return session, "repeated_battle_finish"

        session["state"] = "finished"
        if (
            decoded_stage_id is not None
            and session["stage_id"] is not None
            and decoded_stage_id != session["stage_id"]
        ):
            return session, "stage_id_mismatch"
        if session["stage_id"] is None:
            session["stage_id"] = decoded_stage_id
        return session, "matched_battle_start"

    async def _write(self, event):
        if not self.enabled:
            return
        # Auditing is deliberately best-effort: filesystem and serialization
        # failures must never change a game endpoint's behaviour.
        try:
            async with self._lock:
                self._sequence += 1
                event["run_id"] = self._run_id
                event["sequence"] = self._sequence
                event["timestamp"] = datetime.now(timezone.utc).isoformat()
                self.output_path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(self.output_path, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            return

    async def record_battle_start(self, request, request_json, response):
        if not self.enabled:
            return
        account = _account_ref(request)
        stage_id = request_json.get("stageId")
        session, superseded = self._new_session(account, stage_id)
        await self._write(
            {
                "audit_id": session["audit_id"],
                "account": account,
                "endpoint": "/quest/battleStart",
                "stage_id": stage_id,
                "correlation": "new_battle",
                "superseded_audit_id": superseded,
                "request": _redact(request_json),
                "response": _redact(response),
                "result": response.get("result"),
            }
        )

    async def record_battle_finish(self, player_data, request, request_json, response):
        if not self.enabled:
            return
        account = _account_ref(request)
        decoded = None
        decode_error = None
        try:
            decoded = decode_battle_log(player_data, request_json["data"])
        except Exception as exc:
            decode_error = type(exc).__name__
        decoded_stage_id = _find_stage_id(decoded)
        session, correlation = self._finish_session(account, decoded_stage_id)
        await self._write(
            {
                "audit_id": session["audit_id"],
                "account": account,
                "endpoint": "/quest/battleFinish",
                "stage_id": session["stage_id"],
                "decoded_stage_id": decoded_stage_id,
                "correlation": correlation,
                "finish_count": session["finish_count"],
                "request": _redact(request_json),
                "decoded_data": _redact(decoded),
                "decode_error": decode_error,
                "response": _redact(response),
                "result": response.get("result"),
            }
        )

    async def record_story_finish(self, request, request_json, response):
        if not self.enabled:
            return
        account = _account_ref(request)
        stage_id = request_json.get("stageId")
        session, superseded = self._new_session(account, stage_id, kind="story")
        await self._write(
            {
                "audit_id": session["audit_id"],
                "account": account,
                "endpoint": "/quest/finishStoryStage",
                "stage_id": stage_id,
                "correlation": "standalone_story_finish",
                "superseded_audit_id": superseded,
                "request": _redact(request_json),
                "response": _redact(response),
                "result": response.get("result"),
            }
        )

    async def record_replay_save(self, request, request_json, response):
        if not self.enabled:
            return
        account = _account_ref(request)
        session = self._accounts.get(account)
        if session is None:
            audit_id = str(uuid4())
            stage_id = None
            correlation = "missing_prior_request"
        else:
            audit_id = session["audit_id"]
            stage_id = session["stage_id"]
            correlation = "matched_recent_battle"
        await self._write(
            {
                "audit_id": audit_id,
                "account": account,
                "endpoint": "/quest/saveBattleReplay",
                "stage_id": stage_id,
                "correlation": correlation,
                "request": _redact(request_json),
                "response": _redact(response),
                "result": response.get("result"),
            }
        )


battle_protocol_audit = BattleProtocolAudit()
