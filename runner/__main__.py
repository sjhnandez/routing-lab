"""Entrypoint: ``python -m runner``.

Flow: load settings -> wait for lab readiness (GET /v1/models, up to 90s) ->
run the 8 scenarios in order, each isolated by a mock journal reset -> write
reports/<run_id>.json -> print the human summary -> exit 0 iff all 8 passed.

Robustness: a failing or erroring scenario never aborts the battery; transport
errors inside a scenario surface as status "error" with the exception detail.
"""

from __future__ import annotations

import datetime as dt
import sys
import time

from .client import LabClient, MockClient
from .report import ScenarioResult, print_console_summary, write_report
from .scenarios import SCENARIOS, Context
from .settings import load_lab_settings


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    settings = load_lab_settings()
    started_mono = time.monotonic()
    started_at = _utc_now_iso()
    run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}-{settings.profile}"

    print(f"routing-lab scenario battery  run_id={run_id}")
    print(f"  profile={settings.profile} lab={settings.base_url} mock={settings.mock_url}")
    print(
        f"  config_source={settings.config_source} "
        f"git_rev={settings.git_rev or 'unknown'} "
        f"litellm_image={settings.litellm_image or 'unknown'}"
    )

    lab = LabClient(settings.base_url)
    mock = MockClient(settings.mock_url)
    ctx = Context(settings, lab, mock)
    results: list[ScenarioResult] = []
    try:
        ready = lab.wait_ready(
            timeout_s=90.0, emit=lambda ev, d: print(f"  [{ev}] {d}")
        )
        if not ready:
            print("WARNING: lab not ready after 90s; scenarios will surface connection errors")

        for spec in SCENARIOS:
            print(f"\n--- scenario {spec.id}: {spec.name} ---")
            t0 = time.monotonic()
            try:
                result = spec.run(ctx)
            except Exception as exc:  # noqa: BLE001 - keep the battery running
                result = ScenarioResult(
                    id=spec.id,
                    name=spec.name,
                    status="error",
                    duration_s=time.monotonic() - t0,
                    expected=spec.expected,
                    observed=f"scenario error: {type(exc).__name__}: {exc}",
                    assertions=[],
                    timeline_events=list(ctx.timeline.events),
                    served_by=[],
                    mocked_hits=[],
                )
            results.append(result)
            print(
                f"[{result.status.upper()}] scenario {result.id} {result.name} "
                f"({result.duration_s:.2f}s)"
            )
    finally:
        lab.close()
        mock.close()

    finished_at = _utc_now_iso()
    duration_s = time.monotonic() - started_mono
    passed = sum(1 for r in results if r.status == "pass")
    total = len(SCENARIOS)

    report = {
        "run_id": run_id,
        "profile": settings.profile,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": round(duration_s, 3),
        "lab": {
            "base_url": settings.base_url,
            "mock_url": settings.mock_url,
            "litellm_image": settings.litellm_image,
            "git_rev": settings.git_rev,
        },
        "config_snapshot": {
            "source": settings.config_source,
            "router_settings": settings.router_settings,
        },
        "scenarios": [r.to_dict() for r in results],
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "status": "pass" if passed == total else "fail",
        },
    }
    path = write_report(run_id, report)
    print_console_summary(results, path)
    return 0 if report["summary"]["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
