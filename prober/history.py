"""JSONL probe history and offline aggregation.

One file per UTC day: ``probes-YYYY-MM-DD.jsonl``, one JSON object per probe.
The budget guard and ``--status`` read these files only; nothing here touches
the network.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterator, Optional

from .classify import CLASSES


def as_utc(moment: dt.datetime) -> dt.datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def iso_z(moment: dt.datetime) -> str:
    return as_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date(moment: dt.datetime) -> str:
    return as_utc(moment).strftime("%Y-%m-%d")


def history_path(history_dir: Path, date: str) -> Path:
    return Path(history_dir) / f"probes-{date}.jsonl"


def append_entry(history_dir: Path, date: str, entry: dict) -> Path:
    """Append one JSONL line; creates the history directory if needed."""
    path = history_path(history_dir, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
    return path


def iter_entries(path: Path) -> Iterator[dict]:
    """Yield valid JSON-object lines; blank/malformed lines are skipped."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            yield entry


def count_today(history_dir: Path, date: str) -> int:
    return sum(1 for _ in iter_entries(history_path(history_dir, date)))


def parse_ts(value: object) -> Optional[dt.datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc)


def window_counts(history_dir: Path, now: dt.datetime) -> tuple[dict[str, int], int]:
    """Class counts over the last 24h, read from today's and yesterday's files."""
    now_utc = as_utc(now)
    start = now_utc - dt.timedelta(hours=24)
    counts = {name: 0 for name in CLASSES}
    total = 0
    for date in sorted({utc_date(start), utc_date(now_utc)}):
        for entry in iter_entries(history_path(history_dir, date)):
            ts = parse_ts(entry.get("ts"))
            if ts is None or ts < start or ts > now_utc:
                continue
            name = entry.get("class")
            if name in counts:
                counts[name] += 1
                total += 1
    return counts, total
