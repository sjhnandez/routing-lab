"""HTTP client for the router under probe.

Only two endpoints are used:

  GET  /v1/models            - bearer-auth model list (rotation source)
  POST /v1/chat/completions  - the probe itself (max_tokens=1, non-streaming)

Both requests carry ``Authorization: Bearer <LITELLM_API_KEY>``. Transport
errors are classified, never raised, so a probe run always has an outcome to
record. ``transport`` is injectable for fully offline tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .classify import classify, extract_error_type

MODELS_PATH = "/v1/models"
CHAT_PATH = "/v1/chat/completions"
PROBE_MESSAGES = [{"role": "user", "content": "ping"}]


@dataclass(frozen=True)
class ProbeResult:
    model: str
    ok: bool
    cls: str
    http_status: Optional[int]
    latency_ms: int
    error_type: Optional[str]

    def to_entry(self, *, ts: str, date: str, count: int, cap: int) -> dict[str, Any]:
        """History line for this probe (count = count AFTER this probe)."""
        return {
            "ts": ts,
            "model": self.model,
            "ok": self.ok,
            "class": self.cls,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "budget": {"date": date, "count": count, "cap": cap},
        }


class ProberClient:
    """Thin bearer-auth client for the production router."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def fetch_models(self) -> list[str]:
        """GET /v1/models (Bearer) and return the model ids, in server order."""
        resp = self._http.get(MODELS_PATH)
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError as exc:
            raise ValueError(f"{MODELS_PATH} returned a non-JSON body") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise ValueError(f"{MODELS_PATH} response has no 'data' list")
        models: list[str] = []
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id.strip():
                    models.append(model_id)
        return models

    def probe(self, model: str) -> ProbeResult:
        """POST one minimal chat completion and classify the outcome."""
        payload = {
            "model": model,
            "messages": PROBE_MESSAGES,
            "max_tokens": 1,
            "stream": False,
        }
        started = time.monotonic()
        try:
            resp = self._http.post(CHAT_PATH, json=payload)
        except httpx.HTTPError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return ProbeResult(
                model=model,
                ok=False,
                cls=classify(exception=exc),
                http_status=None,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            body = resp.json()
        except ValueError:
            body = None
        cls = classify(status_code=resp.status_code, body=body)
        error_type = None if cls == "ok" else extract_error_type(body)
        return ProbeResult(
            model=model,
            ok=cls == "ok",
            cls=cls,
            http_status=resp.status_code,
            latency_ms=latency_ms,
            error_type=error_type,
        )
