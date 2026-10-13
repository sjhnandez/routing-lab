"""routing-lab ``lab-api``: run trigger, queue API, report/docs serving.

The API process never talks to docker (no docker.sock): it validates the
gates, drops a request file into the shared queue directory and serves queue
and report state. A host-side executor (``deploy/executor.sh``) polls the
queue and runs the battery; see ``deploy/README.md``.

Endpoints (frozen contract, consumed by dashboard/):
  POST /api/run             202 {"id"} | 429 {"reason","retry_after_s"?} | 503
  GET  /api/run/{id}/events text/event-stream: replay + tail, closes after
                            ``done``/``error``
  GET  /api/last-run        {"report": <full report JSON>|null}
  GET  /api/history         [{run_id,profile,started_at,duration_s,summary}]
  GET  /api/fleet           prober history aggregate: {generated_at,available,note,
                            budget,last_24h,models,recent} (read-only, no probing)
  GET  /api/reports/{id}    full report JSON
  GET  /api/docs            {"methodology","scenarios"} markdown strings
  GET  /api/health          {"status","trigger_enabled","queue":{...}}
  GET  /                    dashboard static bundle (SPA fallback); plain 503
                            placeholder while ``dashboard/dist`` is absent

Gates on POST /api/run, in order: LAB_TRIGGER kill switch (503), single
concurrency (429 run_in_progress), 120 s cooldown since the newest queue
entry (429 cooldown), per-IP 1 request / 10 min (429 rate_limited).

State is derived from ``queue/<id>.json`` files written by this API, and is
appended to by the executor; both run on the same host via bind mounts (the
API writes the files world-writable so the non-root host user can update
them).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

# Strictly validated namespaces: only 32-hex run ids and runner-style report
# ids are ever joined onto a directory path (no traversal, no surprises).
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
REPORT_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,31}$")
# Prober history files (one per UTC day, written host-side by ``prober/``).
PROBE_FILE_RE = re.compile(r"^probes-[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl$")

ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("done", "failed")
TERMINAL_STAGES = ("done", "error")

NOT_BUILT_PLACEHOLDER = (
    "routing-lab dashboard is not built yet (dashboard/dist is empty)\n"
)

# Mirrors ``prober.classify.CLASSES`` (the prober package is not importable
# from the ``lab-api`` image) so /api/fleet counts keep a stable shape.
FLEET_CLASSES: tuple[str, ...] = (
    "ok",
    "rate_limited",
    "unavailable",
    "timeout",
    "not_found",
    "auth",
    "other_4xx",
    "network",
)
FLEET_RECENT_LIMIT = 50
FLEET_DEFAULT_CAP = 48
FLEET_EMPTY_NOTE = "prober has not run yet"

_FALSY = {"off", "0", "false", "no"}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in _FALSY


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    queue_dir: Path
    reports_dir: Path
    docs_dir: Path
    static_dir: Path
    prober_history_dir: Path = Path("/app/prober-history")
    trigger_enabled: bool = True
    cooldown_s: float = 120.0
    rate_limit_window_s: float = 600.0
    rate_limit_max: int = 1
    sse_poll_s: float = 0.5

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            queue_dir=Path(os.environ.get("LAB_QUEUE_DIR", "/app/queue")),
            reports_dir=Path(os.environ.get("LAB_REPORTS_DIR", "/app/reports")),
            docs_dir=Path(os.environ.get("LAB_DOCS_DIR", "/app/docs")),
            static_dir=Path(os.environ.get("LAB_STATIC_DIR", "/app/static")),
            prober_history_dir=Path(
                os.environ.get("LAB_PROBER_HISTORY_DIR", "/app/prober-history")
            ),
            trigger_enabled=_env_flag("LAB_TRIGGER", default=True),
            cooldown_s=_env_float("LAB_COOLDOWN_S", 120.0),
            rate_limit_window_s=_env_float("LAB_RATE_LIMIT_WINDOW_S", 600.0),
            rate_limit_max=_env_int("LAB_RATE_LIMIT_MAX", 1),
            sse_poll_s=_env_float("LAB_SSE_POLL_S", 0.5),
        )


@dataclass(frozen=True)
class QueueEntry:
    id: str
    requested_at: dt.datetime
    status: str
    source_ip: str


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_z(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_z(raw: Any) -> Optional[dt.datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _source_ip(request: Request) -> str:
    raw = request.headers.get("CF-Connecting-IP")
    if raw is None or not raw.strip():
        raw = request.client.host if request.client is not None else ""
    ip = raw.strip()
    return ip[:64] if ip else "unknown"


def _read_queue_entries(queue_dir: Path) -> list[QueueEntry]:
    """Parse ``queue/<32hex>.json`` records; unreadable files are ignored."""
    entries: list[QueueEntry] = []
    try:
        paths = sorted(queue_dir.glob("*.json"))
    except OSError:
        return entries
    for path in paths:
        if RUN_ID_RE.fullmatch(path.stem) is None:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        requested_at = _parse_iso_z(raw.get("requested_at"))
        if requested_at is None:
            continue
        entries.append(
            QueueEntry(
                id=str(raw.get("id") or path.stem),
                requested_at=requested_at,
                status=str(raw.get("status") or ""),
                source_ip=str(raw.get("source_ip") or ""),
            )
        )
    return entries


def _write_file(path: Path, data: str) -> None:
    """Atomic-ish write; queue files end up world-writable so the non-root
    host-side executor (different uid than the container root) can rewrite
    ``status`` and append events through the bind mount."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    try:
        os.chmod(tmp, 0o666)
    except OSError:
        pass
    os.replace(tmp, path)


def _create_queue_request(
    cfg: Config, run_id: str, source_ip: str, now: dt.datetime
) -> None:
    requested_at = _iso_z(now)
    record = {
        "id": run_id,
        "requested_at": requested_at,
        "profile": "quick",
        "source_ip": source_ip,
        "status": "pending",
    }
    _write_file(
        cfg.queue_dir / f"{run_id}.json", json.dumps(record, indent=2) + "\n"
    )
    event = {"ts": requested_at, "stage": "queued"}
    _write_file(
        cfg.queue_dir / f"{run_id}.events.jsonl", json.dumps(event) + "\n"
    )


def _parse_event_line(raw: bytes) -> Optional[dict]:
    text = raw.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, separators=(",", ":")) + "\n\n"


def _queue_status(path: Path) -> Optional[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return str(raw.get("status") or "") or None


async def _event_stream(cfg: Config, run_id: str):
    """Replay ``queue/<id>.events.jsonl`` from the start, then tail it; close
    after a ``done``/``error`` event (or once the run is terminal). Client
    disconnects cancel this generator (starlette/uvicorn), so no explicit
    disconnect probe is needed here."""
    events_path = cfg.queue_dir / f"{run_id}.events.jsonl"
    queue_path = cfg.queue_dir / f"{run_id}.json"
    offset = 0
    partial = b""
    while True:
        chunk = b""
        try:
            with open(events_path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
        except OSError:
            chunk = b""
        offset += len(chunk)
        if chunk:
            partial += chunk
            while b"\n" in partial:
                line, partial = partial.split(b"\n", 1)
                event = _parse_event_line(line)
                if event is None:
                    continue
                yield _sse(event)
                if event.get("stage") in TERMINAL_STAGES:
                    return
        else:
            status = _queue_status(queue_path)
            if status is None:
                # Queue record vanished; nothing left to tail.
                return
            if status in TERMINAL_STATUSES:
                # Run is over: flush a trailing newline-less line, close.
                if partial.strip():
                    event = _parse_event_line(partial)
                    if event is not None:
                        yield _sse(event)
                        if event.get("stage") in TERMINAL_STAGES:
                            return
                return
            await asyncio.sleep(cfg.sse_poll_s)


def _report_files(reports_dir: Path) -> list[Path]:
    files: list[Path] = []
    try:
        for path in reports_dir.glob("*.json"):
            try:
                if path.is_file():
                    files.append(path)
            except OSError:
                continue
    except OSError:
        return files

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=lambda p: (_mtime(p), p.name), reverse=True)
    return files


def _load_report(path: Path) -> Optional[dict]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _newest_report(reports_dir: Path) -> Optional[dict]:
    for path in _report_files(reports_dir):
        report = _load_report(path)
        if report is not None:
            return report
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _utc_date(moment: dt.datetime) -> str:
    return _iso_z(moment)[:10]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _probe_history_files(history_dir: Path) -> list[Path]:
    """Newest-first ``probes-YYYY-MM-DD.jsonl`` files (names sort by date)."""
    try:
        paths = [
            path
            for path in history_dir.glob("probes-*.jsonl")
            if PROBE_FILE_RE.fullmatch(path.name) and path.is_file()
        ]
    except OSError:
        return []
    return sorted(paths, key=lambda p: p.name, reverse=True)


def _read_probe_entries(history_dir: Path) -> list[dict]:
    """Valid probe lines from the newest two daily files, oldest first.

    Malformed lines are skipped: invalid JSON, non-objects, blank lines and
    objects without a parseable ISO-8601 ``ts``. The prober appends
    chronologically, so file order is time order.
    """
    entries: list[dict] = []
    for path in reversed(_probe_history_files(history_dir)[:2]):
        for line in _read_text(path).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if _parse_iso_z(entry.get("ts")) is None:
                continue
            entries.append(entry)
    return entries


def _fleet_payload(cfg: Config, now: dt.datetime) -> dict[str, Any]:
    """Aggregate the prober's JSONL history for ``GET /api/fleet``.

    Read-only over the newest two ``probes-*.jsonl`` files; never probes and
    never writes. ``available`` reflects whether any history parsed at all,
    while ``last_24h`` is a rolling window (an all-stale history still reports
    its budget, per-model last class and recent lines).
    """
    entries = _read_probe_entries(cfg.prober_history_dir)
    available = bool(entries)
    start = now - dt.timedelta(hours=24)

    budget: dict[str, Any] = {
        "date": _utc_date(now),
        "count": 0,
        "cap": FLEET_DEFAULT_CAP,
    }
    if entries:
        latest = entries[-1]
        recorded = latest.get("budget")
        recorded = recorded if isinstance(recorded, dict) else {}
        latest_ts = _parse_iso_z(latest.get("ts"))
        date = recorded.get("date")
        if not (isinstance(date, str) and date):
            date = _utc_date(latest_ts) if latest_ts is not None else budget["date"]
        count = _int_or_none(recorded.get("count"))
        if count is None or count < 0:
            count = 0
            for entry in entries:
                entry_ts = _parse_iso_z(entry.get("ts"))
                if entry_ts is not None and _utc_date(entry_ts) == date:
                    count += 1
        cap = _int_or_none(recorded.get("cap"))
        budget = {
            "date": date,
            "count": count,
            "cap": cap if cap is not None and cap > 0 else FLEET_DEFAULT_CAP,
        }

    counts = {name: 0 for name in FLEET_CLASSES}
    total = 0
    ok = 0
    models: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry_ts = _parse_iso_z(entry.get("ts"))
        if entry_ts is None:  # unreachable (filtered above), kept for safety
            continue
        in_window = start <= entry_ts <= now
        cls_raw = entry.get("class")
        cls = cls_raw if isinstance(cls_raw, str) and cls_raw else None
        if in_window and cls is not None:
            counts[cls] = counts.get(cls, 0) + 1
            total += 1
            if cls == "ok":
                ok += 1
        model = entry.get("model")
        if isinstance(model, str) and model.strip():
            row = models.setdefault(
                model,
                {
                    "model": model,
                    "probes_24h": 0,
                    "ok_24h": 0,
                    "last_class": "",
                    "last_ts": "",
                },
            )
            if in_window:
                row["probes_24h"] += 1
                if cls == "ok":
                    row["ok_24h"] += 1
            if cls is not None:
                row["last_class"] = cls
                row["last_ts"] = str(entry.get("ts"))

    return {
        "generated_at": _iso_z(now),
        "available": available,
        "note": None if available else FLEET_EMPTY_NOTE,
        "budget": budget,
        "last_24h": {
            "total": total,
            "ok": ok,
            "ok_rate": (ok / total) if total else 0.0,
            "counts": counts,
        },
        "models": sorted(
            models.values(), key=lambda row: (-row["probes_24h"], row["model"])
        ),
        "recent": list(reversed(entries[-FLEET_RECENT_LIMIT:])),
    }


def create_app(config: Optional[Config] = None) -> FastAPI:
    cfg = config or Config.from_env()
    app = FastAPI(title="routing-lab lab-api", version="0.1.0")

    @app.post("/api/run")
    async def post_run(request: Request) -> JSONResponse:
        # The gate check-then-write below is race-free only with a single
        # uvicorn worker: the queue is read here and the request file is
        # written further down with no await in between, so one event loop
        # cannot interleave two handlers. api/Dockerfile pins --workers 1;
        # scaling workers requires a real cross-process lock first.
        if not cfg.trigger_enabled:
            return JSONResponse({"reason": "trigger_disabled"}, status_code=503)

        entries = _read_queue_entries(cfg.queue_dir)
        now = _utcnow()

        # Single concurrency: one pending/running request at a time.
        if any(entry.status in ACTIVE_STATUSES for entry in entries):
            return JSONResponse({"reason": "run_in_progress"}, status_code=429)

        # Cooldown since the newest run's start (queue files are the log).
        if entries:
            newest = max(entry.requested_at for entry in entries)
            age_s = (now - newest).total_seconds()
            if age_s < cfg.cooldown_s:
                retry_after_s = int(math.ceil(cfg.cooldown_s - age_s))
                return JSONResponse(
                    {"reason": "cooldown", "retry_after_s": max(retry_after_s, 1)},
                    status_code=429,
                )

        # Per-IP window: 1 accepted request per 10 min (cloudflared passes the
        # visitor address as CF-Connecting-IP; fall back to the socket peer).
        source_ip = _source_ip(request)
        window_hits = [
            entry
            for entry in entries
            if entry.source_ip == source_ip
            and (now - entry.requested_at).total_seconds() < cfg.rate_limit_window_s
        ]
        if len(window_hits) >= cfg.rate_limit_max:
            oldest = min(entry.requested_at for entry in window_hits)
            retry_after_s = cfg.rate_limit_window_s - (now - oldest).total_seconds()
            return JSONResponse(
                {"reason": "rate_limited", "retry_after_s": max(int(math.ceil(retry_after_s)), 1)},
                status_code=429,
            )

        run_id = uuid.uuid4().hex
        _create_queue_request(cfg, run_id, source_ip, now)
        return JSONResponse({"id": run_id}, status_code=202)

    @app.get("/api/run/{run_id}/events")
    async def run_events(run_id: str) -> StreamingResponse:
        queue_path = cfg.queue_dir / f"{run_id}.json"
        if RUN_ID_RE.fullmatch(run_id) is None or not queue_path.is_file():
            raise HTTPException(status_code=404, detail="unknown run")
        return StreamingResponse(
            _event_stream(cfg, run_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/last-run")
    async def last_run() -> dict:
        return {"report": _newest_report(cfg.reports_dir)}

    @app.get("/api/history")
    async def history() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in _report_files(cfg.reports_dir):
            report = _load_report(path)
            if report is None or not report.get("run_id"):
                continue
            items.append(
                {
                    "run_id": report.get("run_id"),
                    "profile": report.get("profile"),
                    "started_at": report.get("started_at"),
                    "duration_s": report.get("duration_s"),
                    "summary": report.get("summary"),
                }
            )
        # Newest first: reports carry ISO-8601 Z timestamps (lexicographic).
        items.sort(
            key=lambda r: (r.get("started_at") or "", r.get("run_id") or ""),
            reverse=True,
        )
        return items

    @app.get("/api/reports/{run_id}")
    async def get_report(run_id: str):
        if REPORT_ID_RE.fullmatch(run_id) is None:
            raise HTTPException(status_code=404, detail="unknown report")
        report = _load_report(cfg.reports_dir / f"{run_id}.json")
        if report is None:
            raise HTTPException(status_code=404, detail="unknown report")
        return report

    @app.get("/api/docs")
    async def docs() -> dict[str, str]:
        return {
            "methodology": _read_text(cfg.docs_dir / "methodology.md"),
            "scenarios": _read_text(cfg.docs_dir / "scenarios.md"),
        }

    @app.get("/api/health")
    async def health() -> dict:
        entries = _read_queue_entries(cfg.queue_dir)
        return {
            "status": "ok",
            "trigger_enabled": cfg.trigger_enabled,
            "queue": {
                "pending": sum(1 for e in entries if e.status == "pending"),
                "running": sum(1 for e in entries if e.status == "running"),
            },
        }

    @app.get("/api/fleet")
    async def fleet() -> dict[str, Any]:
        return _fleet_payload(cfg, _utcnow())

    # Dashboard bundle at "/" with SPA fallback; registered last so every
    # /api/* route above wins. Unknown /api/* paths get a JSON 404.
    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str = "") -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        root = cfg.static_dir
        if path:
            try:
                root_resolved = root.resolve()
                candidate = (root / path).resolve()
                if _is_within(candidate, root_resolved) and candidate.is_file():
                    return FileResponse(candidate)
            except OSError:
                pass
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return PlainTextResponse(NOT_BUILT_PLACEHOLDER, status_code=503)

    return app


app = create_app()
