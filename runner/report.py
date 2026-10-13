"""Report data model, JSON serialization and the human console summary.

The JSON report schema is frozen (the dashboard consumes it); see
docs/methodology.md. Every scenario carries its assertions, a timeline, the
per-mode hit counts (``served_by``) and the raw mock journal entries from its
isolated window (``mocked_hits``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import REPORTS_DIR


@dataclass
class AssertionResult:
    desc: str
    passed: bool
    detail: str = ""


@dataclass
class TimelineEvent:
    t: float
    event: str
    detail: str = ""


class Timeline:
    """Records events as seconds offsets from the scenario start."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.events: list[TimelineEvent] = []

    def add(self, event: str, detail: str = "") -> None:
        self.events.append(
            TimelineEvent(t=round(time.monotonic() - self._t0, 3), event=event, detail=detail)
        )


@dataclass
class ScenarioResult:
    id: int
    name: str
    status: str  # pass | fail | error
    duration_s: float
    expected: str
    observed: str
    assertions: list[AssertionResult] = field(default_factory=list)
    timeline_events: list[TimelineEvent] = field(default_factory=list)
    served_by: list[dict[str, Any]] = field(default_factory=list)
    mocked_hits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "expected": self.expected,
            "observed": self.observed,
            "assertions": [
                {"desc": a.desc, "pass": a.passed, "detail": a.detail}
                for a in self.assertions
            ],
            "timeline": [
                {"t": e.t, "event": e.event, "detail": e.detail}
                for e in self.timeline_events
            ],
            "served_by": self.served_by,
            "mocked_hits": self.mocked_hits,
        }


def write_report(run_id: str, report: dict[str, Any], reports_dir: str = REPORTS_DIR) -> Path:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return path


def print_console_summary(results: list[ScenarioResult], path: Path) -> None:
    print("\n================ scenario summary ================")
    for result in results:
        print(
            f"[{result.status.upper():5}] S{result.id} {result.name} "
            f"({result.duration_s:.2f}s)"
        )
        print(f"        observed: {result.observed}")
        for assertion in result.assertions:
            mark = "PASS" if assertion.passed else "FAIL"
            print(f"        - [{mark}] {assertion.desc} :: {assertion.detail}")
    passed = sum(1 for r in results if r.status == "pass")
    verdict = "ALL GREEN" if passed == len(results) == 8 else "NOT GREEN"
    print("=" * 50)
    print(f"RESULT: {passed}/{len(results)} scenarios passed -> {verdict}")
    print(f"report: {path}")
