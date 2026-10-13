"""CLI entrypoint: ``python -m prober``.

Modes:
  python -m prober                probe the next model in rotation once
  python -m prober --dry-run      offline: print the plan, write nothing
  python -m prober --status       offline: last-24h counts from history
  python -m prober --probe MODEL  probe one specific model (rotation untouched)

Exit codes: 0 success (plan/counts printed, or a probe ran and was recorded
whatever its class), 1 runtime failure before a probe could be recorded
(model list fetch failed / empty), 2 configuration error (missing
LITELLM_API_KEY for a live probe, invalid cap/timeout), 3 daily cap reached.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Mapping, Optional, Sequence

import httpx

from . import history, rotation
from .classify import CLASSES
from .client import ProbeResult, ProberClient
from .settings import ConfigError, ProberSettings, load_settings


def _parse_args(argv: Optional[Sequence[str]]):
    parser = argparse.ArgumentParser(
        prog="prober",
        description="Probe the LiteLLM router once and record the outcome.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="offline: print the plan for the next probe, write nothing",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="offline: print last-24h class counts and today's budget usage",
    )
    group.add_argument(
        "--probe",
        metavar="MODEL",
        help="probe one specific model (does not touch the rotation)",
    )
    return parser.parse_args(argv)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    now: Optional[dt.datetime] = None,
    env: Optional[Mapping[str, str]] = None,
) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(env)
    except ConfigError as exc:
        print(f"error: {exc}")
        return 2
    moment = history.as_utc(now) if now is not None else dt.datetime.now(dt.timezone.utc)

    if args.status:
        return _cmd_status(settings, moment)
    if args.dry_run:
        return _cmd_dry_run(settings, moment)

    if settings.api_key is None:
        print(
            "error: LITELLM_API_KEY is not set; live probes require it "
            "(--dry-run and --status do not)"
        )
        return 2

    today = history.utc_date(moment)
    done = history.count_today(settings.history_dir, today)
    if done >= settings.daily_cap:
        print(f"daily cap reached ({done}/{settings.daily_cap})")
        return 3

    client = ProberClient(
        settings.base_url, settings.api_key, settings.timeout_s, transport=transport
    )
    try:
        next_rotation: Optional[int] = None
        if args.probe is not None:
            model = args.probe
        else:
            try:
                models = client.fetch_models()
            except (httpx.HTTPError, ValueError) as exc:
                print(
                    f"error: could not fetch the model list: "
                    f"{type(exc).__name__}: {exc}"
                )
                return 1
            if not models:
                print("error: GET /v1/models returned no models")
                return 1
            rotation.write_models(settings.history_dir, models)
            index = rotation.read_rotation(settings.history_dir)
            _, model = rotation.pick(models, index)
            next_rotation = rotation.advance(index, len(models))

        result = client.probe(model)
        count_after = done + 1
        entry = result.to_entry(
            ts=history.iso_z(moment),
            date=today,
            count=count_after,
            cap=settings.daily_cap,
        )
        history.append_entry(settings.history_dir, today, entry)
        if next_rotation is not None:
            rotation.write_rotation(settings.history_dir, next_rotation)
        _print_result(result, count_after, settings.daily_cap)
        return 0
    finally:
        client.close()


def _cmd_dry_run(settings: ProberSettings, moment: dt.datetime) -> int:
    today = history.utc_date(moment)
    done = history.count_today(settings.history_dir, today)
    models = rotation.read_models(settings.history_dir)
    index = rotation.read_rotation(settings.history_dir)
    print("prober dry-run: no network calls, no writes")
    print(f"  base_url:    {settings.base_url}")
    print(f"  history_dir: {settings.history_dir}")
    if models:
        position, model = rotation.pick(models, index)
        print(f"  rotation:    position {position} of {len(models)}")
        print(f"  today:       {done}/{settings.daily_cap} probes")
        print(f"  would probe: {model}")
    else:
        print("  rotation:    position 0 of 0 (no cached model list)")
        print(f"  today:       {done}/{settings.daily_cap} probes")
        print("  would probe: (unknown - no cached model list; a live run fetches it)")
    return 0


def _cmd_status(settings: ProberSettings, moment: dt.datetime) -> int:
    counts, total = history.window_counts(settings.history_dir, moment)
    today = history.utc_date(moment)
    done = history.count_today(settings.history_dir, today)
    start = moment - dt.timedelta(hours=24)
    print("prober status: history only, no network")
    print(f"  history_dir: {settings.history_dir}")
    print(f"  window:      {history.iso_z(start)} .. {history.iso_z(moment)} (last 24h)")
    print(f"  probes:      {total}")
    for name in CLASSES:
        print(f"    {name + ':':<15}{counts[name]}")
    print(f"  today:       {done}/{settings.daily_cap} probes ({today})")
    return 0


def _print_result(result: ProbeResult, count_after: int, cap: int) -> None:
    status = f"http={result.http_status}" if result.http_status is not None else "http=n/a"
    detail = f" type={result.error_type}" if result.error_type else ""
    print(
        f"probe {result.model}: {result.cls} "
        f"({status}, {result.latency_ms}ms){detail} [today {count_after}/{cap}]"
    )


if __name__ == "__main__":
    sys.exit(main())
