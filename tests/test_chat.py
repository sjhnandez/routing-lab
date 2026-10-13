"""Unit tests for the POST /api/chat "Try the router" proxy in ``api.main``.

Fully in-process via ``fastapi.testclient.TestClient`` - no litellm, no
docker. The upstream is an ``httpx.MockTransport`` injected through the
``Config.chat_transport`` seam, so every path runs offline: the gate order
(LAB_CHAT kill switch -> body validation -> key availability -> per-IP
window -> in-flight cap), the frozen error shapes, SSE passthrough plus the
single ``event: meta`` accounting frame, upstream non-2xx / timeout /
truncation mapping, and the dotenv master-key parsing + caching (the tmp
fixture mirrors the read-only ``/secrets/apps.env`` mount used in prod).

The injected key is an obvious fake; real keys never appear in tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import (
    CHAT_SYSTEM_PROMPT,
    ChatState,
    Config,
    _load_master_key,
    _parse_env_value,
    create_app,
)

FAKE_KEY = "sk-test-master-key"
OTHER_IP = "198.51.100.9"

META_HEADERS = {
    "content-type": "text/event-stream",
    "x-litellm-model-group": "free-chat",
    "x-litellm-model-id": "text-chat-primary",
    "x-litellm-attempted-retries": "1",
    "x-litellm-attempted-fallbacks": "0",
}


def make_key_file(tmp_path: Path, value: str = FAKE_KEY) -> Path:
    path = tmp_path / "apps.env"
    path.write_text(f"OTHER=1\nLITELLM_MASTER_KEY={value}\n", encoding="utf-8")
    return path


def make_config(tmp_path: Path, key_file: Path | None = None, **overrides) -> Config:
    fields = {
        "queue_dir": tmp_path / "queue",
        "reports_dir": tmp_path / "reports",
        "docs_dir": tmp_path / "docs",
        "static_dir": tmp_path / "static",
        "litellm_key_file": (
            key_file if key_file is not None else make_key_file(tmp_path)
        ),
    }
    fields.update(overrides)
    return Config(**fields)


def make_client(tmp_path: Path, key_file: Path | None = None, **overrides) -> TestClient:
    return TestClient(create_app(make_config(tmp_path, key_file, **overrides)))


def sse_data(obj: object) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def chunks_body() -> str:
    return (
        sse_data({"choices": [{"index": 0, "delta": {"content": "Hello"}}]})
        + sse_data({"choices": [{"index": 0, "delta": {"content": " world"}}]})
        + sse_data({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        + "data: [DONE]\n\n"
    )


def stream_handler(requests, *, status=200, body="", headers=None, exc=None):
    """MockTransport handler: record the request, then reply or raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if exc is not None:
            raise exc
        return httpx.Response(status, text=body, headers=headers or {})

    return handler


def streaming_client(
    tmp_path: Path,
    requests: list,
    *,
    status=200,
    body="",
    headers=None,
    exc=None,
    **config_overrides,
) -> TestClient:
    transport = httpx.MockTransport(
        stream_handler(requests, status=status, body=body, headers=headers, exc=exc)
    )
    return make_client(tmp_path, chat_transport=transport, **config_overrides)


def parse_sse(text: str) -> list[tuple[str | None, str]]:
    """``(event, data)`` pairs from an SSE body; ``None`` = plain data frame."""
    events: list[tuple[str | None, str]] = []
    event: str | None = None
    data: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
        elif not line and data:
            events.append((event, "\n".join(data)))
            event, data = None, []
    if data:
        events.append((event, "\n".join(data)))
    return events


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


def test_health_exposes_chat_enabled(tmp_path):
    assert make_client(tmp_path).get("/api/health").json()["chat_enabled"] is True
    off = make_client(tmp_path, chat_enabled=False)
    assert off.get("/api/health").json()["chat_enabled"] is False


# ---------------------------------------------------------------------------
# Gate: kill switch
# ---------------------------------------------------------------------------


def test_disabled_chat_returns_503_and_blocks_upstream(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, chat_enabled=False)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert resp.json() == {"reason": "chat_disabled"}
    # The kill switch beats validation too, and nothing reaches the router.
    assert client.post("/api/chat", json={"message": ""}).status_code == 503
    assert requests == []


# ---------------------------------------------------------------------------
# Body validation (before the rate gate; consumes no slot)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [{}, {"message": ""}, {"message": "   "}, {"message": 42}, {"message": None}],
)
def test_message_empty(tmp_path, body):
    requests: list = []
    client = streaming_client(tmp_path, requests)
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 400
    assert resp.json() == {"reason": "message_empty"}
    assert requests == []


def test_invalid_json_body_is_message_empty(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests)
    resp = client.post(
        "/api/chat",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json() == {"reason": "message_empty"}
    assert requests == []


def test_message_too_long(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests)
    resp = client.post("/api/chat", json={"message": "x" * 401})
    assert resp.status_code == 400
    assert resp.json() == {"reason": "message_too_long"}
    assert requests == []


def test_max_length_boundary_passes_validation(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, body=chunks_body(), headers=META_HEADERS)
    resp = client.post("/api/chat", json={"message": "x" * 400})
    assert resp.status_code == 200
    assert json.loads(requests[0].content)["messages"][1]["content"] == "x" * 400


def test_max_chars_is_configurable(tmp_path):
    requests: list = []
    client = streaming_client(
        tmp_path, requests, body=chunks_body(), headers=META_HEADERS,
        chat_max_chars=10,
    )
    assert client.post("/api/chat", json={"message": "x" * 11}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 10}).status_code == 200


def test_long_message_does_not_consume_rate_slot(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, body=chunks_body(), headers=META_HEADERS)
    assert client.post("/api/chat", json={"message": "x" * 401}).status_code == 400
    for _ in range(5):
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    # The five valid messages filled the window; the rejected one was free.
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 429
    assert len(requests) == 5


# ---------------------------------------------------------------------------
# Gate: master key (fail closed)
# ---------------------------------------------------------------------------


def test_missing_key_fails_closed(tmp_path):
    requests: list = []
    client = streaming_client(
        tmp_path, requests, key_file=tmp_path / "absent.env"
    )
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert resp.json() == {"reason": "router_key_missing"}
    assert requests == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("LITELLM_MASTER_KEY=sk-plain", "sk-plain"),
        ('LITELLM_MASTER_KEY="sk-double"', "sk-double"),
        ("LITELLM_MASTER_KEY='sk-single'", "sk-single"),
        ("LITELLM_MASTER_KEY=  sk-spaced  ", "sk-spaced"),
        ("export LITELLM_MASTER_KEY=sk-exported", "sk-exported"),
    ],
)
def test_parse_env_value_strips_quotes_and_export(line, expected):
    assert _parse_env_value(line + "\n", "LITELLM_MASTER_KEY") == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "OTHER=1\n",
        "LITELLM_MASTER_KEY=\n",
        "LITELLM_MASTER_KEY=''\n",
        "# LITELLM_MASTER_KEY=sk-commented\n",
    ],
)
def test_parse_env_value_missing_or_blank_is_none(text):
    assert _parse_env_value(text, "LITELLM_MASTER_KEY") is None


def test_load_master_key_reads_once_then_caches(tmp_path):
    path = tmp_path / "cache-key.env"
    path.write_text('LITELLM_MASTER_KEY="sk-first"\n', encoding="utf-8")
    assert _load_master_key(path) == "sk-first"
    path.write_text("LITELLM_MASTER_KEY=sk-second\n", encoding="utf-8")
    assert _load_master_key(path) == "sk-first"  # cached: one read per process


def test_load_master_key_missing_file_is_none(tmp_path):
    assert _load_master_key(tmp_path / "does-not-exist.env") is None


# ---------------------------------------------------------------------------
# Gate: per-IP window (5 / 600 s)
# ---------------------------------------------------------------------------


def test_rate_limit_after_five_messages(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, body=chunks_body(), headers=META_HEADERS)
    for _ in range(5):
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 429
    body = resp.json()
    assert body["reason"] == "rate_limited"
    assert 1 <= body["retry_after_s"] <= 600
    assert len(requests) == 5


def test_rate_limit_is_per_ip(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, body=chunks_body(), headers=META_HEADERS)
    for _ in range(5):
        client.post(
            "/api/chat", json={"message": "hi"}, headers={"CF-Connecting-IP": OTHER_IP}
        )
    # A different visitor is unaffected...
    other = client.post(
        "/api/chat", json={"message": "hi"}, headers={"CF-Connecting-IP": "203.0.113.7"}
    )
    assert other.status_code == 200
    # ...while the exhausted visitor stays blocked.
    blocked = client.post(
        "/api/chat", json={"message": "hi"}, headers={"CF-Connecting-IP": OTHER_IP}
    )
    assert blocked.status_code == 429
    assert blocked.json()["reason"] == "rate_limited"


def test_chat_state_retry_after_counts_from_oldest_hit():
    state = ChatState()
    assert state.acquire("ip", 1000.0, 600, 2) is None
    assert state.acquire("ip", 1000.0, 600, 2) is None
    assert state.acquire("ip", 1100.0, 600, 2) == 500
    assert state.acquire("ip", 1599.0, 600, 2) == 1  # never below 1 s
    assert state.acquire("ip", 1601.0, 600, 2) is None  # window drained


# ---------------------------------------------------------------------------
# Gate: global in-flight cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_inflight", [1, 3])
def test_busy_when_inflight_full(tmp_path, max_inflight):
    requests: list = []
    app = create_app(
        make_config(
            tmp_path,
            chat_max_inflight=max_inflight,
            chat_transport=httpx.MockTransport(stream_handler(requests)),
        )
    )
    client = TestClient(app)
    app.state.chat.inflight = max_inflight
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 429
    assert resp.json() == {"reason": "busy", "retry_after_s": 5}
    assert requests == []


def test_inflight_slot_released_after_stream(tmp_path):
    requests: list = []
    app = create_app(
        make_config(
            tmp_path,
            chat_transport=httpx.MockTransport(
                stream_handler(requests, body=chunks_body(), headers=META_HEADERS)
            ),
        )
    )
    client = TestClient(app)
    assert app.state.chat.inflight == 0
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert app.state.chat.inflight == 0


# ---------------------------------------------------------------------------
# Proxy: request shape, SSE passthrough, meta frame
# ---------------------------------------------------------------------------


def test_streams_upstream_chunks_then_meta(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, body=chunks_body(), headers=META_HEADERS)
    resp = client.post("/api/chat", json={"message": "Say hello"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: error" not in resp.text

    events = parse_sse(resp.text)
    assert [event for event, _ in events] == [None, None, None, None, "meta"]
    assert json.loads(events[0][1])["choices"][0]["delta"]["content"] == "Hello"
    assert json.loads(events[1][1])["choices"][0]["delta"]["content"] == " world"
    finish = json.loads(events[2][1])
    assert finish["choices"][0]["finish_reason"] == "stop"
    assert events[3][1] == "[DONE]"

    meta = json.loads(events[4][1])
    assert meta["served_by"] == "free-chat"
    assert meta["model_id"] == "text-chat-primary"
    assert meta["attempted_retries"] == 1
    assert meta["attempted_fallbacks"] == 0
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0

    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://litellm:4000/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer " + FAKE_KEY
    assert json.loads(request.content) == {
        "model": "free-chat",
        "messages": [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": "Say hello"},
        ],
        "max_tokens": 250,
        "stream": True,
    }


def test_upstream_url_model_and_tokens_are_configurable(tmp_path):
    requests: list = []
    client = streaming_client(
        tmp_path,
        requests,
        body=chunks_body(),
        headers=META_HEADERS,
        litellm_url="http://router.example:9999/",
        litellm_chat_model="prod-chat",
        chat_max_tokens=7,
    )
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    request = requests[0]
    assert str(request.url) == "http://router.example:9999/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["model"] == "prod-chat"
    assert payload["max_tokens"] == 7


def test_non_data_upstream_lines_are_skipped(tmp_path):
    body = ": keep-alive\n\nevent: ping\n\n" + chunks_body()
    requests: list = []
    client = streaming_client(tmp_path, requests, body=body, headers=META_HEADERS)
    events = parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    assert [event for event, _ in events] == [None, None, None, None, "meta"]


def test_meta_defaults_to_none_without_accounting_headers(tmp_path):
    requests: list = []
    client = streaming_client(
        tmp_path,
        requests,
        body=chunks_body(),
        headers={"content-type": "text/event-stream"},
    )
    events = parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    meta = json.loads(events[-1][1])
    assert meta == {
        "served_by": None,
        "model_id": None,
        "attempted_retries": None,
        "attempted_fallbacks": None,
        "latency_ms": meta["latency_ms"],
    }


# ---------------------------------------------------------------------------
# Proxy: upstream failures -> one event: error frame, never a [DONE]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "pools_busy"),
        (503, "pools_busy"),
        (408, "router_timeout"),
        (504, "router_timeout"),
        (500, "router_error"),
        (401, "router_error"),
    ],
)
def test_upstream_non_2xx_maps_to_one_error_event(tmp_path, status, reason):
    requests: list = []
    client = streaming_client(
        tmp_path,
        requests,
        status=status,
        body='{"error": "nope"}',
        headers={"content-type": "application/json"},
    )
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200  # gates passed: failures arrive in-band
    events = parse_sse(resp.text)
    assert len(events) == 1
    assert events[0][0] == "error"
    assert json.loads(events[0][1]) == {"reason": reason, "status": status}
    assert "[DONE]" not in resp.text


def test_upstream_timeout_maps_to_router_timeout(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, exc=httpx.ReadTimeout("slow"))
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert json.loads(events[0][1]) == {"reason": "router_timeout", "status": 504}


def test_upstream_connect_error_maps_to_router_error(tmp_path):
    requests: list = []
    client = streaming_client(tmp_path, requests, exc=httpx.ConnectError("refused"))
    events = parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    assert json.loads(events[0][1]) == {"reason": "router_error", "status": 502}


def test_truncated_stream_without_done_maps_to_router_error(tmp_path):
    body = sse_data({"choices": [{"index": 0, "delta": {"content": "partial"}}]})
    requests: list = []
    client = streaming_client(tmp_path, requests, body=body, headers=META_HEADERS)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert [event for event, _ in events] == [None, "error"]
    assert json.loads(events[1][1]) == {"reason": "router_error", "status": 502}
    assert "[DONE]" not in resp.text


class _BrokenStream(httpx.AsyncByteStream):
    """2xx response whose body dies after the first chunk."""

    async def __aiter__(self):
        yield b'data: {"choices": [{"index": 0, "delta": {"content": "par"}}]}\n\n'
        raise httpx.ReadError("connection broke mid-stream")


def test_mid_stream_failure_maps_to_router_error(tmp_path):
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_BrokenStream())

    client = make_client(tmp_path, chat_transport=httpx.MockTransport(handler))
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert [event for event, _ in events] == [None, "error"]
    assert json.loads(events[1][1]) == {"reason": "router_error", "status": 502}
    assert "[DONE]" not in resp.text
