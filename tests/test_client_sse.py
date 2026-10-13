"""Unit tests for ``runner.client``: SSE parsing and outcome clients.

Every request is served by ``httpx.MockTransport`` - fully offline and
instant. The transport is injected by swapping the client's internal
``httpx.Client`` (the production class builds it directly from a base URL).
"""

from __future__ import annotations

import json

import httpx
import pytest

from runner.client import (
    CompletionOutcome,
    LabClient,
    MockClient,
    StreamOutcome,
    compact_error,
    excerpt,
)

MESSAGES = [{"role": "user", "content": "hi"}]


def _sse(obj) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _chunk(content=None, finish_reason=None) -> dict:
    delta = {"content": content} if content is not None else {}
    return {
        "id": "chatcmpl-mock-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "mode-healthy",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _completion_body(content: str = "Hello from the mock upstream!") -> dict:
    return {
        "id": "chatcmpl-mock-1",
        "object": "chat.completion",
        "created": 1,
        "model": "mode-healthy",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 6, "total_tokens": 9},
    }


def _lab_with(handler) -> LabClient:
    lab = LabClient("http://lab.invalid")
    lab._http.close()
    lab._http = httpx.Client(
        base_url="http://lab.invalid", transport=httpx.MockTransport(handler)
    )
    return lab


def _mock_client_with(handler) -> MockClient:
    client = MockClient("http://mock.invalid")
    client._http.close()
    client._http = httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    return client


# ---------------------------------------------------------------------------
# chat_stream: happy path
# ---------------------------------------------------------------------------


def test_chat_stream_parses_chunks_finish_and_done():
    body = (
        _sse(_chunk(content="Hello"))
        + _sse(_chunk(content=" world"))
        + _sse(_chunk(finish_reason="stop"))
        + "data: [DONE]\n\n"
    )

    def handler(request):
        assert request.method == "POST"
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    outcome = _lab_with(handler).chat_stream("mode-healthy", MESSAGES)
    assert isinstance(outcome, StreamOutcome)
    assert outcome.status_code == 200
    assert outcome.termination == "done"
    assert outcome.done is True
    assert outcome.error_event is None
    assert outcome.content_chunks == 2
    assert outcome.content == "Hello world"
    assert outcome.finish_reason == "stop"
    assert outcome.success is True
    assert outcome.manifestation() == "SSE [DONE] after 2 content chunk(s)"


def test_chat_stream_tolerates_malformed_lines():
    body = (
        ": keep-alive comment\n\n"
        "event: message\n\n"
        "garbage line without prefix\n\n"
        "data: {not valid json\n\n"
        + _sse({"weird": "shape"})
        + _sse(_chunk(content="Hi"))
        + _sse(_chunk(finish_reason="stop"))
        + "data: [DONE]\n\n"
    )
    outcome = _lab_with(
        lambda request: httpx.Response(200, text=body)
    ).chat_stream("mode-healthy", MESSAGES)
    assert outcome.success is True
    assert outcome.content == "Hi"
    assert outcome.content_chunks == 1
    assert outcome.finish_reason == "stop"


# ---------------------------------------------------------------------------
# chat_stream: in-band and out-of-band errors
# ---------------------------------------------------------------------------


def test_chat_stream_detects_sse_error_event():
    body = (
        "event: error\n\n"
        + _sse(_chunk(content="partial"))
        + "data: [DONE]\n\n"
    )
    outcome = _lab_with(
        lambda request: httpx.Response(200, text=body)
    ).chat_stream("mode-healthy", MESSAGES)
    assert outcome.error_event == "SSE event: error"
    assert outcome.done is True
    assert outcome.content == "partial"
    assert outcome.success is False
    assert "SSE error event" in outcome.manifestation()


def test_chat_stream_detects_error_payload_in_data_line():
    error = {
        "message": "Injected rate limit",
        "type": "rate_limit_error",
        "code": "rate_limit_exceeded",
    }
    body = _sse({"error": error})
    outcome = _lab_with(
        lambda request: httpx.Response(200, text=body)
    ).chat_stream("mode-err-429", MESSAGES)
    assert outcome.error_event == "rate_limit_error/rate_limit_exceeded: Injected rate limit"
    assert outcome.done is False
    assert outcome.termination == "eof"
    assert outcome.success is False


def test_chat_stream_non_200_reads_error_body():
    error = {
        "message": "Injected rate limit",
        "type": "rate_limit_error",
        "code": "rate_limit_exceeded",
    }

    def handler(request):
        return httpx.Response(429, json={"error": error})

    outcome = _lab_with(handler).chat_stream("mode-err-429", MESSAGES)
    assert outcome.status_code == 429
    assert outcome.termination == "non-200"
    assert outcome.success is False
    assert outcome.error_event == "rate_limit_error/rate_limit_exceeded: Injected rate limit"
    assert outcome.attempted_retries is None


def test_chat_stream_eof_without_done_is_not_success():
    body = _sse(_chunk(content="Hi")) + _sse(_chunk(content=" there"))
    outcome = _lab_with(
        lambda request: httpx.Response(200, text=body)
    ).chat_stream("mode-healthy", MESSAGES)
    assert outcome.done is False
    assert outcome.termination == "eof"
    assert outcome.content == "Hi there"
    assert outcome.content_chunks == 2
    assert outcome.success is False
    assert outcome.manifestation() == "stream ended without [DONE] (abrupt termination)"


def test_chat_stream_transport_exception_recorded():
    def handler(request):
        raise httpx.ConnectError("boom")

    outcome = _lab_with(handler).chat_stream("mode-healthy", MESSAGES)
    assert outcome.status_code is None
    assert outcome.termination == "exception"
    assert outcome.exception is not None and "ConnectError" in outcome.exception
    assert outcome.success is False
    assert "abrupt termination" in outcome.manifestation()


def test_chat_stream_captures_router_headers():
    def handler(request):
        return httpx.Response(
            200,
            text="data: [DONE]\n\n",
            headers={
                "x-litellm-attempted-retries": "1",
                "x-litellm-attempted-fallbacks": "1",
            },
        )

    outcome = _lab_with(handler).chat_stream("mode-err-429", MESSAGES)
    assert outcome.attempted_retries == "1"
    assert outcome.attempted_fallbacks == "1"


# ---------------------------------------------------------------------------
# chat_once
# ---------------------------------------------------------------------------


def test_chat_once_success_captures_content_and_headers():
    def handler(request):
        return httpx.Response(
            200,
            json=_completion_body(),
            headers={"x-litellm-attempted-retries": "2"},
        )

    outcome = _lab_with(handler).chat_once("mode-healthy", MESSAGES)
    assert isinstance(outcome, CompletionOutcome)
    assert outcome.status_code == 200
    assert outcome.success is True
    assert outcome.content == "Hello from the mock upstream!"
    assert outcome.error is None
    assert outcome.attempted_retries == "2"
    assert "Hello from the mock upstream!" in outcome.excerpt


def test_chat_once_error_response_surfaces_compact_error():
    error = {
        "message": "Injected internal error",
        "type": "internal_server_error",
        "code": "internal_server_error",
    }

    def handler(request):
        return httpx.Response(500, json={"error": error})

    outcome = _lab_with(handler).chat_once("mode-err-500", MESSAGES)
    assert outcome.status_code == 500
    assert outcome.success is False
    assert outcome.error == "internal_server_error/internal_server_error: Injected internal error"


def test_chat_once_non_json_200_is_treated_as_error():
    def handler(request):
        return httpx.Response(200, text="not json", headers={"content-type": "text/plain"})

    outcome = _lab_with(handler).chat_once("mode-healthy", MESSAGES)
    assert outcome.status_code == 200
    assert outcome.success is False
    assert outcome.error == "not json"


def test_chat_once_transport_exception_recorded():
    def handler(request):
        raise httpx.ConnectError("boom")

    outcome = _lab_with(handler).chat_once("mode-healthy", MESSAGES)
    assert outcome.status_code is None
    assert outcome.exception is not None and "ConnectError" in outcome.exception
    assert outcome.success is False
    assert "transport error" in outcome.manifestation()


# ---------------------------------------------------------------------------
# MockClient (journal endpoints)
# ---------------------------------------------------------------------------


def test_mock_client_fetch_and_reset_journal():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(200, json={"cleared": True})
        return httpx.Response(200, json=[{"mode": "healthy", "outcome": "completed"}])

    client = _mock_client_with(handler)
    assert client.fetch_journal() == [{"mode": "healthy", "outcome": "completed"}]
    client.reset_journal()
    assert ("DELETE", "/__hits") in calls


def test_mock_client_fetch_journal_rejects_non_array_body():
    client = _mock_client_with(
        lambda request: httpx.Response(200, json={"not": "a list"})
    )
    with pytest.raises(ValueError):
        client.fetch_journal()


def test_mock_client_reset_journal_raises_on_http_error():
    client = _mock_client_with(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        client.reset_journal()


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def test_excerpt_shortens_and_flattens_newlines():
    assert excerpt("a" * 10, limit=5) == "aa..."
    assert excerpt("line1\nline2", limit=60) == "line1 line2"
    assert len(excerpt("x" * 200)) == 60


def test_compact_error_renderings():
    assert compact_error({"message": "m", "type": "t", "code": "c"}) == "t/c: m"
    assert compact_error({"message": "m"}) == "m"
    assert compact_error({"code": "c"}).startswith("{")
    assert compact_error("plain") == "plain"
