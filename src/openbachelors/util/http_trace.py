"""Opt-in, non-mutating HTTP protocol diagnostics for the local dev server.

The middleware observes ASGI messages rather than consuming ``Request.body()`` or
rebuilding responses. It therefore preserves the request/response stream.
"""

import hashlib
import json
import logging
import sys
import time
from uuid import uuid4

from ..const.filepath import CONFIG_JSON
from .const_json_loader import const_json_loader
from .log_helper import IS_DEBUG


logger = logging.getLogger("openbachelors.http_trace")

DEFAULT_MAX_BODY_BYTES = 65536
DEFAULT_MAX_LOG_CHARS = 12000
DEFAULT_PATHS = ("/quest/", "/account/")
SENSITIVE_KEY_PARTS = (
    "authorization", "cookie", "credential", "password", "secret", "session", "token",
    "api_key", "apikey",
)
OPAQUE_KEYS = {"data", "battleReplay"}


def configure_game_logging():
    """Make existing ``openbachelors.*`` debug logs visible in the console."""
    if not IS_DEBUG:
        return
    game_logger = logging.getLogger("openbachelors")
    game_logger.setLevel(logging.DEBUG)
    if not any(getattr(handler, "_openbachelors_console", False) for handler in game_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
        handler._openbachelors_console = True
        game_logger.addHandler(handler)
    game_logger.propagate = False


def _redact(value, key=None):
    if key is not None:
        lowered = key.lower()
        if any(part in lowered for part in SENSITIVE_KEY_PARTS):
            return "<redacted>"
        if key in OPAQUE_KEYS and isinstance(value, str):
            return {"omitted": True, "length": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _body_summary(buffer, observed_bytes, content_type, max_body_bytes, max_log_chars):
    if observed_bytes == 0:
        return "<empty>"
    if observed_bytes > max_body_bytes:
        return f"<omitted: {observed_bytes} bytes exceed {max_body_bytes} byte capture limit>"
    if "json" not in content_type.lower():
        return f"<omitted non-JSON body: {observed_bytes} bytes, content-type={content_type or 'unknown'}>"
    try:
        payload = json.loads(buffer)
        rendered = json.dumps(_redact(payload), ensure_ascii=False, separators=(",", ":"))
    except (ValueError, UnicodeDecodeError, TypeError, RecursionError):
        return f"<invalid JSON: {observed_bytes} bytes>"
    if len(rendered) > max_log_chars:
        return rendered[:max_log_chars] + f"... <log truncated, {len(rendered)} chars total>"
    return rendered


def _content_type(headers):
    for key, value in headers:
        if key.lower() == b"content-type":
            return value.decode("latin-1", errors="replace")
    return ""


class HttpTraceMiddleware:
    def __init__(self, app):
        self.app = app
        config = const_json_loader[CONFIG_JSON].copy().get("http_trace", {})
        self.enabled = IS_DEBUG and bool(config.get("enabled", False))
        self.paths = tuple(config.get("paths", DEFAULT_PATHS))
        self.max_body_bytes = max(0, int(config.get("max_body_bytes", DEFAULT_MAX_BODY_BYTES)))
        self.max_log_chars = max(100, int(config.get("max_log_chars", DEFAULT_MAX_LOG_CHARS)))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled or not scope.get("path", "").startswith(self.paths):
            await self.app(scope, receive, send)
            return

        trace_id = uuid4().hex[:8]
        method = scope.get("method", "?")
        path = scope.get("path", "")  # query string may contain credentials: don't log it
        request_type = _content_type(scope.get("headers", ()))
        response_type = ""
        request_buffer = bytearray()
        response_buffer = bytearray()
        request_bytes = 0
        response_bytes = 0
        response_status = None
        started = time.perf_counter()

        async def traced_receive():
            nonlocal request_bytes
            event = await receive()
            if event["type"] == "http.request":
                chunk = event.get("body", b"")
                request_bytes += len(chunk)
                remaining = self.max_body_bytes - len(request_buffer)
                if remaining > 0:
                    request_buffer.extend(chunk[:remaining])
            return event

        async def traced_send(event):
            nonlocal response_type, response_status, response_bytes
            if event["type"] == "http.response.start":
                response_status = event["status"]
                response_type = _content_type(event.get("headers", ()))
            elif event["type"] == "http.response.body":
                chunk = event.get("body", b"")
                response_bytes += len(chunk)
                remaining = self.max_body_bytes - len(response_buffer)
                if remaining > 0:
                    response_buffer.extend(chunk[:remaining])
            await send(event)

        try:
            await self.app(scope, traced_receive, traced_send)
        except Exception:
            logger.exception("[%s] %s %s raised an exception", trace_id, method, path)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            response_summary = (
                f"<suppressed syncData body: {response_bytes} bytes>"
                if path == "/account/syncData"
                else _body_summary(
                    response_buffer,
                    response_bytes,
                    response_type,
                    self.max_body_bytes,
                    self.max_log_chars,
                )
            )
            logger.debug("[%s] --> %s %s body=%s", trace_id, method, path,
                         _body_summary(request_buffer, request_bytes, request_type,
                                       self.max_body_bytes, self.max_log_chars))
            logger.debug("[%s] <-- %s %s status=%s elapsed=%.1fms body=%s",
                         trace_id, method, path,
                         response_status if response_status is not None else "no-response",
                         elapsed_ms,
                         response_summary)
