"""OpenAI-compatible mock upstream with deterministic, model-keyed fault modes.

Impersonates the upstream that the routing-lab LiteLLM proxy routes to
(see lab_config/router.*.yaml). Deployments call this service with
litellm_params.model = "openai/<mode>"; LiteLLM strips the "openai/" prefix so
the request arrives with "model": "<mode>". The mode is extracted as the
substring after the last "/" so a stray prefix is tolerated.

Endpoints (internal port 8000):
  POST /v1/chat/completions  chat completions (stream=true SSE, or plain JSON)
  GET  /v1/models            OpenAI-style model list (the 8 mode ids)
  GET  /healthz              liveness probe
  GET  /__hits               request journal as a JSON array
  DELETE /__hits             clears the request journal

Only /v1/chat/completions requests are journaled.
"""

import asyncio
import json
import os
import threading
import time
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Timing knobs are env-tunable so the lab can scale fault timings without
# rebuilding the image.
STALL_PRE_SECONDS = _env_float("STALL_PRE_SECONDS", 30.0)
STALL_MID_SECONDS = _env_float("STALL_MID_SECONDS", 30.0)
STALL_MID_CHUNKS = _env_int("STALL_MID_CHUNKS", 3)
SLOW_CHUNKS = _env_int("SLOW_CHUNKS", 12)
SLOW_GAP_MS = _env_int("SLOW_GAP_MS", 800)

HEALTHY_GAP_MS = 50     # inter-chunk gap for the healthy stream
QUICK_GAP_MS = 20       # "quickly" sent chunks before a mid-stream stall
POST_STALL_GAP_MS = 50  # gap used once a stall has been survived

WORDS = ["Hello", " from the", " mock", " upstream", "!"]
FULL_CONTENT = "".join(WORDS)

MODE_IDS = [
    "healthy",
    "stall-pre",
    "stall-mid",
    "slow-steady",
    "err-429",
    "err-503",
    "err-500",
    "err-404",
]

# mode -> (http status, message, error type, error code). These fail
# immediately for both streaming and non-streaming requests.
ERROR_MODES: dict[str, tuple[int, str, str, str]] = {
    "err-429": (429, "Injected rate limit", "rate_limit_error", "rate_limit_exceeded"),
    "err-503": (
        503,
        "Injected service unavailable",
        "service_unavailable_error",
        "service_unavailable",
    ),
    "err-500": (500, "Injected internal error", "internal_server_error", "internal_server_error"),
    "err-404": (404, "Injected model not found", "invalid_request_error", "model_not_found"),
}

# ---------------------------------------------------------------------------
# Request journal
# ---------------------------------------------------------------------------

_JOURNAL: list[dict[str, Any]] = []

# All handlers run on one event loop (single uvicorn worker), so appends done
# from synchronous code cannot interleave. The lock additionally keeps the
# journal correct if the service is ever run with multiple threads/workers.
# Recording is deliberately synchronous so it also works from cancellation /
# GeneratorExit paths, where awaiting anything is not safe.
_JOURNAL_LOCK = threading.Lock()


def record_hit(entry: dict[str, Any]) -> None:
    with _JOURNAL_LOCK:
        _JOURNAL.append(entry)


def _new_entry(
    started: float, model: str, mode: str, stream: bool, **overrides: Any
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": started,
        "model": model,
        "mode": mode if mode in MODE_IDS else "unknown",
        "stream": stream,
        "status": 200,
        "chunks_sent": 0,
        "outcome": "completed",
        "duration_s": round(time.time() - started, 6),
    }
    entry.update(overrides)
    return entry


def _error_body(message: str, error_type: str, code: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "code": code}}


def _prompt_chars(body: dict[str, Any]) -> int:
    total = 0
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                total += len(message["content"])
    return total


def _sse_chunk(
    model: str, created: int, chunk_id: int, delta: dict[str, Any], finish_reason: Optional[str]
) -> str:
    payload = {
        "id": f"chatcmpl-mock-{chunk_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return "data: " + json.dumps(payload) + "\n\n"


async def _delay_or_disconnect(request: Request, delay_s: float) -> bool:
    """Sleep `delay_s` in small slices; return False if the client vanished."""
    remaining = delay_s
    while remaining > 0:
        slice_s = min(0.1, remaining)
        await asyncio.sleep(slice_s)
        remaining -= slice_s
        if await request.is_disconnected():
            return False
    return True


app = FastAPI(title="routing-lab mock upstream")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": mode, "object": "model", "created": created, "owned_by": "routing-lab"}
            for mode in MODE_IDS
        ],
    }


@app.get("/__hits")
async def get_hits() -> list[dict[str, Any]]:
    with _JOURNAL_LOCK:
        return list(_JOURNAL)


@app.delete("/__hits")
async def clear_hits() -> dict[str, bool]:
    with _JOURNAL_LOCK:
        _JOURNAL.clear()
    return {"cleared": True}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    started = time.time()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    model = str(body.get("model") or "")
    mode = model.rsplit("/", 1)[-1]
    stream = bool(body.get("stream", False))

    # Error modes and unknown models fail immediately, streaming or not.
    if mode in ERROR_MODES:
        status, message, error_type, code = ERROR_MODES[mode]
        record_hit(_new_entry(started, model, mode, stream, status=status, outcome="errored"))
        return JSONResponse(status_code=status, content=_error_body(message, error_type, code))

    if mode not in MODE_IDS:
        record_hit(_new_entry(started, model, mode, stream, status=404, outcome="errored"))
        return JSONResponse(
            status_code=404,
            content=_error_body(
                f"The model '{model}' does not exist", "invalid_request_error", "model_not_found"
            ),
        )

    # --------------------------- non-streaming -----------------------------
    if not stream:
        delay_s = 0.0
        if mode == "stall-pre":
            delay_s = STALL_PRE_SECONDS
        elif mode == "stall-mid":
            delay_s = STALL_MID_SECONDS
        elif mode == "slow-steady":
            delay_s = SLOW_CHUNKS * SLOW_GAP_MS / 1000.0

        disconnected = False
        if delay_s > 0:
            try:
                disconnected = not await _delay_or_disconnect(request, delay_s)
            except asyncio.CancelledError:
                record_hit(
                    _new_entry(started, model, mode, stream, status=200, outcome="aborted")
                )
                raise

        prompt_tokens = max(1, _prompt_chars(body) // 4)
        completion_tokens = max(1, len(FULL_CONTENT) // 4)
        payload = {
            "id": "chatcmpl-mock-1",
            "object": "chat.completion",
            "created": int(started),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": FULL_CONTENT},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        if disconnected:
            record_hit(_new_entry(started, model, mode, stream, status=200, outcome="aborted"))
        else:
            record_hit(
                _new_entry(started, model, mode, stream, chunks_sent=1, outcome="completed")
            )
        return JSONResponse(content=payload)

    # ----------------------------- streaming -------------------------------
    created = int(started)

    async def event_stream():
        chunks_sent = 0
        outcome = "aborted"
        try:
            if mode == "stall-pre":
                # Response headers are already on the wire; hold before the
                # first chunk so the client sees silence.
                await asyncio.sleep(STALL_PRE_SECONDS)

            plan: list[tuple[str, int]] = []  # (content, gap after chunk in ms)
            hold_after: Optional[int] = None  # stall-mid: long sleep after this index
            if mode == "stall-mid":
                pre = max(0, min(STALL_MID_CHUNKS, len(WORDS)))
                plan = [(word, QUICK_GAP_MS) for word in WORDS[:pre]]
                hold_after = len(plan) - 1  # -1 => stall before any chunk
                plan += [(word, POST_STALL_GAP_MS) for word in WORDS[pre:]]
            elif mode == "slow-steady":
                count = max(0, SLOW_CHUNKS)
                plan = [(WORDS[i % len(WORDS)], SLOW_GAP_MS) for i in range(count)]
            else:  # healthy, stall-pre
                plan = [(word, HEALTHY_GAP_MS) for word in WORDS]

            if hold_after == -1:
                await asyncio.sleep(STALL_MID_SECONDS)

            for index, (text, gap_ms) in enumerate(plan):
                chunks_sent += 1
                yield _sse_chunk(model, created, index + 1, {"content": text}, None)
                if hold_after is not None and index == hold_after:
                    await asyncio.sleep(STALL_MID_SECONDS)
                else:
                    await asyncio.sleep(gap_ms / 1000.0)

            yield _sse_chunk(model, created, len(plan) + 1, {}, "stop")
            yield "data: [DONE]\n\n"
            outcome = "completed"
        except (asyncio.CancelledError, GeneratorExit):
            raise
        finally:
            # Runs on normal completion, client disconnect, or cancellation.
            record_hit(
                _new_entry(started, model, mode, stream, chunks_sent=chunks_sent, outcome=outcome)
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
