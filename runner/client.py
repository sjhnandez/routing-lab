"""HTTP clients for the scenario battery.

``LabClient`` talks to the LiteLLM proxy under test; ``MockClient`` talks to
the mock upstream's journal endpoints (the attribution source).

Timeouts are deliberately generous: litellm decides when a request is dead
(silence-based ``timeout`` / ``stream_timeout``), and the runner must never
impose a shorter cut. A hung turn is bounded by the read timeout, not by us
being clever.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from .settings import LAB_MASTER_KEY

Emit = Callable[[str, str], None]

READ_TIMEOUT_S = 90.0
CONNECT_TIMEOUT_S = 10.0

# Router-side evidence headers litellm attaches to proxy responses that went
# through its retry / fallback machinery. Captured opportunistically (they are
# not guaranteed on every path); the mock journal stays the attribution source.
ROUTER_ATTEMPTED_RETRIES_HEADER = "x-litellm-attempted-retries"
ROUTER_ATTEMPTED_FALLBACKS_HEADER = "x-litellm-attempted-fallbacks"


def _noop(event: str, detail: str = "") -> None:
    pass


def _capture_router_headers(
    headers: httpx.Headers, label: str, emit: Emit
) -> tuple[Optional[str], Optional[str]]:
    """Record ``x-litellm-attempted-retries/-fallbacks`` when the proxy sent them."""
    retries = headers.get(ROUTER_ATTEMPTED_RETRIES_HEADER)
    fallbacks = headers.get(ROUTER_ATTEMPTED_FALLBACKS_HEADER)
    if retries is not None or fallbacks is not None:
        emit(
            "router_headers",
            f"{label}: attempted_retries={retries if retries is not None else 'absent'}, "
            f"attempted_fallbacks={fallbacks if fallbacks is not None else 'absent'}",
        )
    return retries, fallbacks


def excerpt(text: str, limit: int = 60) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def compact_error(value: Any) -> str:
    """One-line rendering of a litellm/OpenAI-style error payload."""
    if isinstance(value, dict):
        message = value.get("message")
        etype = value.get("type")
        code = value.get("code")
        label = "/".join(str(p) for p in (etype, code) if p)
        if message:
            return f"{label}: {message}" if label else str(message)
        return json.dumps(value, ensure_ascii=True)[:200]
    return str(value)


@dataclass
class StreamOutcome:
    status_code: Optional[int] = None
    done: bool = False
    error_event: Optional[str] = None
    content_chunks: int = 0
    content: str = ""
    finish_reason: Optional[str] = None
    termination: str = "unknown"  # non-200 | done | eof | exception
    exception: Optional[str] = None
    attempted_retries: Optional[str] = None
    attempted_fallbacks: Optional[str] = None

    @property
    def success(self) -> bool:
        """A clean completion: HTTP 200, no error event, [DONE], non-empty text."""
        return (
            self.status_code == 200
            and self.error_event is None
            and self.done
            and bool(self.content)
            and self.termination in ("done", "eof")
        )

    def manifestation(self) -> str:
        """Human-readable description of how the turn actually ended."""
        if self.status_code is not None and self.status_code != 200:
            detail = self.error_event or self.exception or ""
            return f"HTTP {self.status_code} error response ({detail})"
        if self.error_event:
            return f"SSE error event: {self.error_event}"
        if self.exception:
            return f"abrupt termination ({self.exception})"
        if self.done:
            return f"SSE [DONE] after {self.content_chunks} content chunk(s)"
        return "stream ended without [DONE] (abrupt termination)"


@dataclass
class CompletionOutcome:
    status_code: Optional[int] = None
    content: str = ""
    error: Optional[str] = None
    excerpt: str = ""
    exception: Optional[str] = None
    attempted_retries: Optional[str] = None
    attempted_fallbacks: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.status_code == 200 and not self.error and bool(self.content)

    def manifestation(self) -> str:
        if self.exception:
            return f"transport error ({self.exception})"
        if self.status_code != 200:
            return f"HTTP {self.status_code} error response ({self.error})"
        return f"HTTP 200 completion ({excerpt(self.content)})"


class LabClient:
    """Thin client for the LiteLLM proxy under test."""

    def __init__(self, base_url: str, read_timeout_s: float = READ_TIMEOUT_S):
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S, read=read_timeout_s, write=30.0, pool=10.0
        )
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {"Authorization": f"Bearer {LAB_MASTER_KEY}"}

    def close(self) -> None:
        self._http.close()

    def wait_ready(
        self, timeout_s: float = 90.0, poll_s: float = 2.0, emit: Emit = _noop
    ) -> bool:
        """Poll GET /v1/models with the lab master key until it answers."""
        deadline = time.monotonic() + timeout_s
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                resp = self._http.get("/v1/models", headers=self._headers)
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = None
                    if isinstance(body, dict) and isinstance(body.get("data"), list):
                        emit("lab_ready", f"GET /v1/models ok after {attempt} poll(s)")
                        return True
            except httpx.HTTPError:
                pass
            time.sleep(poll_s)
        emit("lab_ready", f"TIMEOUT: no healthy /v1/models within {timeout_s:.0f}s")
        return False

    def chat_stream(
        self, model: str, messages: list[dict[str, str]], emit: Emit = _noop
    ) -> StreamOutcome:
        """POST a streaming chat completion and parse the SSE result."""
        outcome = StreamOutcome()
        parts: list[str] = []
        payload = {"model": model, "messages": messages, "stream": True}
        try:
            with self._http.stream(
                "POST", "/v1/chat/completions", json=payload, headers=self._headers
            ) as resp:
                outcome.status_code = resp.status_code
                outcome.attempted_retries, outcome.attempted_fallbacks = (
                    _capture_router_headers(resp.headers, model, emit)
                )
                emit("http_status", f"{model}: HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raw = ""
                    try:
                        raw = resp.read().decode("utf-8", errors="replace")
                    except httpx.HTTPError as exc:
                        outcome.exception = f"{type(exc).__name__}: {exc}"
                    try:
                        body = json.loads(raw)
                        err = body.get("error") if isinstance(body, dict) else None
                        outcome.error_event = compact_error(
                            err if err is not None else body
                        )
                    except ValueError:
                        outcome.error_event = excerpt(raw, 200)
                    outcome.termination = "non-200"
                    return outcome
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        name = line[len("event:") :].strip()
                        if name == "error" and outcome.error_event is None:
                            outcome.error_event = "SSE event: error"
                            emit("error_event", "event: error")
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        outcome.done = True
                        emit("done_marker", "[DONE]")
                        continue
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    err = obj.get("error")
                    if err:
                        outcome.error_event = compact_error(err)
                        emit("error_event", outcome.error_event)
                        continue
                    choices = obj.get("choices")
                    if not isinstance(choices, list):
                        continue
                    for choice in choices:
                        if not isinstance(choice, dict):
                            continue
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            text = delta.get("content")
                            if isinstance(text, str) and text:
                                parts.append(text)
                                outcome.content_chunks += 1
                                if outcome.content_chunks == 1:
                                    emit("first_content_chunk", excerpt(text))
                        finish_reason = choice.get("finish_reason")
                        if finish_reason:
                            outcome.finish_reason = str(finish_reason)
                outcome.termination = "done" if outcome.done else "eof"
                emit(
                    "stream_end",
                    f"done={outcome.done} chunks={outcome.content_chunks} "
                    f"error_event={outcome.error_event or 'none'}",
                )
        except httpx.HTTPError as exc:
            outcome.termination = "exception"
            outcome.exception = f"{type(exc).__name__}: {exc}"
            emit("stream_exception", outcome.exception)
        outcome.content = "".join(parts)
        return outcome

    def chat_once(
        self, model: str, messages: list[dict[str, str]], emit: Emit = _noop
    ) -> CompletionOutcome:
        """POST a non-streaming chat completion."""
        payload = {"model": model, "messages": messages, "stream": False}
        try:
            resp = self._http.post(
                "/v1/chat/completions", json=payload, headers=self._headers
            )
            attempted_retries, attempted_fallbacks = _capture_router_headers(
                resp.headers, model, emit
            )
            emit("http_status", f"{model}: HTTP {resp.status_code}")
            body: Any = None
            try:
                body = resp.json()
            except ValueError:
                body = None
            if resp.status_code == 200 and isinstance(body, dict):
                content = ""
                choices = body.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    message = choices[0].get("message")
                    if isinstance(message, dict) and isinstance(
                        message.get("content"), str
                    ):
                        content = message["content"]
                emit("completion", excerpt(content))
                return CompletionOutcome(
                    status_code=200,
                    content=content,
                    excerpt=excerpt(content),
                    attempted_retries=attempted_retries,
                    attempted_fallbacks=attempted_fallbacks,
                )
            error = None
            if isinstance(body, dict):
                raw_error = body.get("error")
                error = compact_error(raw_error if raw_error is not None else body)
            if error is None:
                error = excerpt(resp.text, 200)
            emit("error_response", f"HTTP {resp.status_code}: {error}")
            return CompletionOutcome(
                status_code=resp.status_code,
                error=error,
                excerpt=excerpt(resp.text),
                attempted_retries=attempted_retries,
                attempted_fallbacks=attempted_fallbacks,
            )
        except httpx.HTTPError as exc:
            exc_str = f"{type(exc).__name__}: {exc}"
            emit("transport_exception", exc_str)
            return CompletionOutcome(exception=exc_str)


class MockClient:
    """Client for the mock upstream's journal endpoints (attribution source)."""

    def __init__(self, base_url: str):
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def reset_journal(self) -> None:
        resp = self._http.delete("/__hits")
        resp.raise_for_status()

    def fetch_journal(self) -> list[dict[str, Any]]:
        resp = self._http.get("/__hits")
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"journal is not a JSON array: {type(data).__name__}")
        return data
