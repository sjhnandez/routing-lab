"""Failure taxonomy for router probes.

``classify()`` maps one probe outcome to exactly one of ``CLASSES``:

  ok           - 2xx response
  rate_limited - HTTP 429 (or a body error type saying so)
  unavailable  - HTTP 5xx (500/502/503/504 are the canonical ones)
  timeout      - request timed out, or the connect attempt failed
                 (refused/unreachable: no response was received in time)
  not_found    - HTTP 404 (e.g. the model no longer exists)
  auth         - HTTP 401/403
  other_4xx    - any other HTTP 4xx
  network      - any other transport failure that is not a timeout/connect
                 error (connection reset mid-exchange, protocol errors)

Precedence: an exception means no response was ever produced and decides
first; otherwise a parseable litellm/OpenAI ``error.type`` in the body wins
over the HTTP status; unknown types fall back to the status.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx

CLASSES: tuple[str, ...] = (
    "ok",
    "rate_limited",
    "unavailable",
    "timeout",
    "not_found",
    "auth",
    "other_4xx",
    "network",
)

# Normalized (lowercase, alphanumeric only) litellm/OpenAI ``error.type``
# values. Unknown types fall back to the HTTP status.
_TYPE_MAP: dict[str, str] = {
    # rate limits
    "ratelimiterror": "rate_limited",
    "ratelimitexceeded": "rate_limited",
    "ratelimit": "rate_limited",
    "toomanyrequests": "rate_limited",
    # availability / server-side
    "internalservererror": "unavailable",
    "apierror": "unavailable",
    "servererror": "unavailable",
    "serviceunavailable": "unavailable",
    "serviceunavailableerror": "unavailable",
    "nodeploymentsavailable": "unavailable",
    # timeouts / connect errors (no timely response)
    "timeout": "timeout",
    "timeouterror": "timeout",
    "apitimeout": "timeout",
    "connecttimeout": "timeout",
    "readtimeout": "timeout",
    "requesttimeout": "timeout",
    "apiconnectionerror": "timeout",
    "connectionerror": "timeout",
    # not found
    "notfounderror": "not_found",
    "modelnotfound": "not_found",
    # auth
    "authenticationerror": "auth",
    "invalidapikey": "auth",
    "permissionerror": "auth",
    "permissiondeniederror": "auth",
    # other 4xx
    "invalidrequesterror": "other_4xx",
    "badrequesterror": "other_4xx",
    "contextlengthexceeded": "other_4xx",
}


def _type_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def extract_error_type(body: Any) -> Optional[str]:
    """Return ``error.type`` from an OpenAI/litellm error body, else None."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        etype = error.get("type")
        if isinstance(etype, str) and etype.strip():
            return etype.strip()
    return None


def classify(
    status_code: Optional[int] = None,
    body: Any = None,
    exception: Optional[BaseException] = None,
) -> str:
    """Map one probe outcome to exactly one class from ``CLASSES``."""
    if exception is not None:
        if isinstance(exception, httpx.TimeoutException):
            return "timeout"
        if isinstance(exception, httpx.ConnectError):
            return "timeout"
        return "network"
    etype = extract_error_type(body)
    if etype is not None:
        mapped = _TYPE_MAP.get(_type_key(etype))
        if mapped is not None:
            return mapped
    if status_code is not None:
        return _classify_status(status_code)
    raise ValueError("classify() needs at least one of status_code/body/exception")


def _classify_status(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status == 429:
        return "rate_limited"
    if status == 404:
        return "not_found"
    if status in (401, 403):
        return "auth"
    if status >= 500:
        return "unavailable"
    if 400 <= status < 500:
        return "other_4xx"
    return "network"
