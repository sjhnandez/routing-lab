"""Unit tests for the mock upstream (``mock_upstream.app``).

Deterministic and in-process: ``fastapi.testclient.TestClient`` drives the
ASGI app directly - no server, no network, no docker.

The stall/slow timing knobs are module constants read from the environment at
import time, so the timing tests reload the module with tiny values (restoring
env and module state afterwards) and verify the *order* of sleeps vs. chunk
yields via spies. No wall-clock waiting, no flakiness.
"""

from __future__ import annotations

import importlib
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

import mock_upstream.app as mock_app

ENV_KNOBS = (
    "STALL_PRE_SECONDS",
    "STALL_MID_SECONDS",
    "STALL_MID_CHUNKS",
    "SLOW_CHUNKS",
    "SLOW_GAP_MS",
)

# Frozen wire contract: the 8 mode ids served by GET /v1/models.
FROZEN_MODE_IDS = [
    "healthy",
    "stall-pre",
    "stall-mid",
    "slow-steady",
    "err-429",
    "err-503",
    "err-500",
    "err-404",
]

# mode -> (http status, error type, error code)
ERROR_MODES = {
    "err-429": (429, "rate_limit_error", "rate_limit_exceeded"),
    "err-503": (503, "service_unavailable_error", "service_unavailable"),
    "err-500": (500, "internal_server_error", "internal_server_error"),
    "err-404": (404, "invalid_request_error", "model_not_found"),
}

# Frozen journal schema: the runner's served_by/mocked_hits consume these keys.
JOURNAL_KEYS = {
    "ts",
    "model",
    "mode",
    "stream",
    "status",
    "chunks_sent",
    "outcome",
    "duration_s",
}

CHAT = [{"role": "user", "content": "Hello from the unit tests."}]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(mock_app.app)


@pytest.fixture()
def load_mock(monkeypatch):
    """Reload ``mock_upstream.app`` under env overrides; restore both after."""

    saved = {name: os.environ.get(name) for name in ENV_KNOBS}

    def _load(**overrides):
        for name in ENV_KNOBS:
            if name in overrides:
                monkeypatch.setenv(name, str(overrides.pop(name)))
            else:
                monkeypatch.delenv(name, raising=False)
        return importlib.reload(mock_app)

    yield _load

    # Restore the original env before the final reload so the module constants
    # return to their pristine values (monkeypatch restores env again later).
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    importlib.reload(mock_app)


def _record_stream_events(module, monkeypatch) -> list[tuple]:
    """Spy on chunk construction + asyncio.sleep inside the stream handler."""
    events: list[tuple] = []

    real_chunk = module._sse_chunk

    def spy_chunk(model, created, chunk_id, delta, finish_reason):
        events.append(("chunk", delta.get("content"), finish_reason))
        return real_chunk(model, created, chunk_id, delta, finish_reason)

    real_sleep = module.asyncio.sleep

    async def spy_sleep(delay, *args, **kwargs):
        if isinstance(delay, (int, float)) and 0.001 <= delay <= 10:
            events.append(("sleep", round(float(delay), 3)))
        return await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(module, "_sse_chunk", spy_chunk)
    monkeypatch.setattr(module.asyncio, "sleep", spy_sleep)
    return events


# ---------------------------------------------------------------------------
# Basic endpoints
# ---------------------------------------------------------------------------


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_models_lists_exactly_the_eight_mode_ids(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert [entry["id"] for entry in body["data"]] == FROZEN_MODE_IDS
    assert all(
        entry["object"] == "model" and entry["owned_by"] == "routing-lab"
        for entry in body["data"]
    )


# ---------------------------------------------------------------------------
# Fault modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
def test_error_modes_immediate_status_and_error_body(client, stream):
    for mode, (status, error_type, code) in ERROR_MODES.items():
        resp = client.post(
            "/v1/chat/completions",
            json={"model": mode, "messages": CHAT, "stream": stream},
        )
        assert resp.status_code == status, f"{mode}: expected {status}, got {resp.status_code}"
        error = resp.json()["error"]
        assert error["type"] == error_type, mode
        assert error["code"] == code, mode
        assert error["message"], mode


def test_unknown_model_404_model_not_found(client):
    resp = client.post(
        "/v1/chat/completions", json={"model": "ghost-mode", "messages": CHAT}
    )
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "model_not_found"
    assert "ghost-mode" in error["message"]


def test_stray_prefix_in_model_id_is_tolerated(client):
    resp = client.post(
        "/v1/chat/completions", json={"model": "openai/healthy", "messages": CHAT}
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == mock_app.FULL_CONTENT


# ---------------------------------------------------------------------------
# Healthy paths
# ---------------------------------------------------------------------------


def test_healthy_nonstream_completion_shape(client):
    resp = client.post(
        "/v1/chat/completions", json={"model": "healthy", "messages": CHAT}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "healthy"
    choices = body["choices"]
    assert len(choices) == 1
    assert choices[0]["finish_reason"] == "stop"
    content = choices[0]["message"]["content"]
    assert isinstance(content, str) and content
    assert content == mock_app.FULL_CONTENT
    usage = body["usage"]
    assert usage["prompt_tokens"] >= 1
    assert usage["completion_tokens"] >= 1
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_healthy_stream_sse_shape(client):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "healthy", "messages": CHAT, "stream": True},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    lines = [line for line in resp.text.split("\n") if line.strip()]
    assert all(line.startswith("data: ") for line in lines)
    assert lines[-1] == "data: [DONE]"

    payloads = [json.loads(line[len("data: ") :]) for line in lines[:-1]]
    assert payloads, "stream must carry at least one data chunk"
    content_chunks = [
        p for p in payloads if p["choices"][0]["delta"].get("content")
    ]
    assert len(content_chunks) >= 5
    assert (
        "".join(p["choices"][0]["delta"]["content"] for p in content_chunks)
        == "Hello from the mock upstream!"
    )
    finish_reasons = [p["choices"][0]["finish_reason"] for p in payloads]
    assert "stop" in finish_reasons
    assert payloads[0]["object"] == "chat.completion.chunk"


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


def test_journal_entries_have_frozen_schema_and_record_outcomes(client):
    client.delete("/__hits")
    client.post("/v1/chat/completions", json={"model": "healthy", "messages": CHAT})
    client.post(
        "/v1/chat/completions",
        json={"model": "healthy", "messages": CHAT, "stream": True},
    )
    client.post("/v1/chat/completions", json={"model": "err-429", "messages": CHAT})

    hits = client.get("/__hits").json()
    assert isinstance(hits, list)
    assert len(hits) == 3
    for entry in hits:
        assert set(entry.keys()) == JOURNAL_KEYS

    nonstream, stream, errored = hits
    assert nonstream["mode"] == "healthy"
    assert nonstream["stream"] is False
    assert nonstream["status"] == 200 and nonstream["outcome"] == "completed"
    assert nonstream["chunks_sent"] == 1
    assert stream["mode"] == "healthy"
    assert stream["stream"] is True
    assert stream["status"] == 200 and stream["outcome"] == "completed"
    assert stream["chunks_sent"] >= 1
    assert errored["mode"] == "err-429"
    assert errored["status"] == 429 and errored["outcome"] == "errored"
    assert all(entry["duration_s"] >= 0 for entry in hits)


def test_only_chat_completions_are_journaled(client):
    client.delete("/__hits")
    client.get("/v1/models")
    client.get("/healthz")
    assert client.get("/__hits").json() == []

    client.post("/v1/chat/completions", json={"model": "healthy", "messages": CHAT})
    assert len(client.get("/__hits").json()) == 1


def test_hits_delete_clears_journal(client):
    client.post("/v1/chat/completions", json={"model": "healthy", "messages": CHAT})
    assert isinstance(client.get("/__hits").json(), list)

    resp = client.delete("/__hits")
    assert resp.status_code == 200
    assert resp.json() == {"cleared": True}
    assert client.get("/__hits").json() == []


def test_unknown_model_request_is_journaled_under_unknown_mode(client):
    client.delete("/__hits")
    client.post("/v1/chat/completions", json={"model": "ghost-mode", "messages": CHAT})

    hits = client.get("/__hits").json()
    assert len(hits) == 1
    entry = hits[0]
    assert set(entry.keys()) == JOURNAL_KEYS
    assert entry["mode"] == "unknown"
    assert entry["status"] == 404
    assert entry["outcome"] == "errored"


# ---------------------------------------------------------------------------
# Timing modes (tiny knobs + reload; ordering asserted, not wall-clock)
# ---------------------------------------------------------------------------


def test_stall_pre_holds_before_first_chunk_then_streams(load_mock, monkeypatch):
    module = load_mock(STALL_PRE_SECONDS=0.07)
    events = _record_stream_events(module, monkeypatch)
    client = TestClient(module.app)

    t0 = time.monotonic()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "stall-pre", "messages": CHAT, "stream": True},
    )
    assert time.monotonic() - t0 < 5.0
    assert resp.status_code == 200
    assert resp.text.endswith("data: [DONE]\n\n")

    # The pre-stall sleep happens first, then all five content chunks, then the
    # finish chunk - with exactly the healthy inter-chunk gaps in between.
    assert events == [
        ("sleep", 0.07),
        ("chunk", "Hello", None),
        ("sleep", 0.05),
        ("chunk", " from the", None),
        ("sleep", 0.05),
        ("chunk", " mock", None),
        ("sleep", 0.05),
        ("chunk", " upstream", None),
        ("sleep", 0.05),
        ("chunk", "!", None),
        ("sleep", 0.05),
        ("chunk", None, "stop"),
    ]


def test_stall_mid_emits_pre_chunks_before_the_hold(load_mock, monkeypatch):
    module = load_mock(STALL_MID_SECONDS=0.11, STALL_MID_CHUNKS=2)
    events = _record_stream_events(module, monkeypatch)
    client = TestClient(module.app)

    t0 = time.monotonic()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "stall-mid", "messages": CHAT, "stream": True},
    )
    assert time.monotonic() - t0 < 5.0
    assert resp.status_code == 200
    assert resp.text.endswith("data: [DONE]\n\n")

    assert events.count(("sleep", 0.11)) == 1
    hold_index = events.index(("sleep", 0.11))
    # Exactly the STALL_MID_CHUNKS pre-chunks arrive before the hold ...
    assert [e for e in events[:hold_index] if e[0] == "chunk"] == [
        ("chunk", "Hello", None),
        ("chunk", " from the", None),
    ]
    assert events[hold_index - 1] == ("chunk", " from the", None)
    # ... and the remaining chunks (incl. the finish chunk) arrive after it.
    after_hold = events[hold_index + 1 :]
    assert after_hold[-1] == ("chunk", None, "stop")
    assert len([e for e in after_hold if e[0] == "chunk" and e[1] is not None]) == 3


def test_timed_nonstream_modes_complete_with_tuned_knobs(load_mock):
    module = load_mock(
        STALL_PRE_SECONDS=0.05,
        STALL_MID_SECONDS=0.05,
        SLOW_CHUNKS=3,
        SLOW_GAP_MS=10,
    )
    client = TestClient(module.app)

    t0 = time.monotonic()
    for mode in ("stall-pre", "stall-mid", "slow-steady"):
        resp = client.post(
            "/v1/chat/completions", json={"model": mode, "messages": CHAT}
        )
        assert resp.status_code == 200, mode
        assert resp.json()["object"] == "chat.completion", mode
    assert time.monotonic() - t0 < 5.0


def test_slow_steady_stream_completes_with_tuned_knobs(load_mock):
    module = load_mock(SLOW_CHUNKS=6, SLOW_GAP_MS=10)
    client = TestClient(module.app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "slow-steady", "messages": CHAT, "stream": True},
    )
    assert resp.status_code == 200
    assert resp.text.endswith("data: [DONE]\n\n")
    payloads = [
        json.loads(line[len("data: ") :])
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    content_chunks = [
        p for p in payloads if p["choices"][0]["delta"].get("content")
    ]
    assert len(content_chunks) == 6
