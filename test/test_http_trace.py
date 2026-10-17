import json
import logging

import pytest

from openbachelors.util.http_trace import HttpTraceMiddleware, _body_summary, _redact, logger


def test_http_trace_redacts_credentials_and_large_battle_data():
    original = {"token": "sensitive", "stageId": "main_01-11", "data": "battle-payload",
                "nested": {"secret": "hidden", "battleReplay": "replay-payload"}}
    result = _redact(original)
    assert result["token"] == "<redacted>"
    assert result["nested"]["secret"] == "<redacted>"
    assert result["stageId"] == "main_01-11"
    assert result["data"]["omitted"] is True
    assert result["data"]["length"] == len("battle-payload")
    assert result["nested"]["battleReplay"]["omitted"] is True
    assert "battle-payload" not in json.dumps(result)


def test_http_trace_limits_body_without_partial_secret_leaks():
    body = b'{"secret":"not-public","stageId":"main_01-11"}'
    assert "not-public" not in _body_summary(body[:8], len(body), "application/json", 8, 300)
    assert "<redacted>" in _body_summary(body, len(body), "application/json", 65536, 300)
    assert "not-public" not in _body_summary(body, len(body), "application/json", 65536, 300)


@pytest.mark.asyncio
async def test_http_trace_preserves_asgi_io_and_logs_payloads():
    request = json.dumps({"squadId": "1", "slots": [{"charInstId": 2}]}).encode()
    response = b'{"result":0,"playerDataDelta":{"modified":{},"deleted":{}}}'
    sent = []
    events = [
        {"type": "http.request", "body": request[:10], "more_body": True},
        {"type": "http.request", "body": request[10:], "more_body": False},
    ]

    async def receive():
        return events.pop(0)

    async def send(message):
        sent.append(message)

    async def app(scope, recv, send_message):
        parts = []
        while True:
            message = await recv()
            parts.append(message["body"])
            if not message.get("more_body", False):
                break
        assert b"".join(parts) == request
        await send_message({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"application/json")]})
        await send_message({"type": "http.response.body", "body": response, "more_body": False})

    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []
        def emit(self, record):
            self.lines.append(record.getMessage())

    capture = Capture()
    old_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    try:
        middleware = HttpTraceMiddleware(app)
        middleware.enabled = True
        await middleware({"type": "http", "method": "POST", "path": "/quest/squadFormation",
                          "headers": [(b"content-type", b"application/json")]}, receive, send)
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == response
    lines = "\n".join(capture.lines)
    assert "/quest/squadFormation" in lines
    assert '"charInstId":2' in lines
    assert '"playerDataDelta"' in lines
    assert "status=200" in lines


@pytest.mark.asyncio
async def test_http_trace_suppresses_complete_sync_data_response():
    response = json.dumps({"user": {"large": "x" * 1000}}).encode()
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    async def app(scope, recv, send_message):
        await recv()
        await send_message({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"application/json")]})
        await send_message({"type": "http.response.body", "body": response})

    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []
        def emit(self, record):
            self.lines.append(record.getMessage())

    capture = Capture()
    old_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    try:
        middleware = HttpTraceMiddleware(app)
        middleware.enabled = True
        await middleware({"type": "http", "method": "POST", "path": "/account/syncData",
                          "headers": [(b"content-type", b"application/json")]}, receive, send)
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)

    lines = "\n".join(capture.lines)
    assert "suppressed syncData body" in lines
    assert '"large"' not in lines
    assert sent[-1]["body"] == response
