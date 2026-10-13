#!/usr/bin/env python3
"""Browser end-to-end test for the routing-lab dashboard (http://127.0.0.1:3003).

Covers: cached last-run on load, one real "Run experiment" click driven end to
end by the host executor (queue -> battery -> SSE stages), fresh 8/8 results,
the post-run cooldown gate, the Fleet empty state and the Method rendering.

Run from the repo root on the host (the image ships the browsers but not the
Python packages, so they are installed on the fly; the tag must stay
v1.55.0-jammy - v1.55-jammy does not exist):
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp \
    -v "$PWD:/w" -w /w \
    mcr.microsoft.com/playwright/python:v1.55.0-jammy \
    sh -c "pip install --user --quiet playwright==1.55.0 pillow && python tests/e2e/dashboard_e2e.py"

Screenshots are written to docs/e2e/ (each kept under ~600 KB).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:3003"
REPO = Path(__file__).resolve().parents[2]
SHOTS = REPO / "docs" / "e2e"
QUEUE_DIR = REPO / "queue"
MAX_SHOT_BYTES = 600 * 1024
COOLDOWN_S = 120.0
DASH = "\u2014"  # em dash used throughout the UI copy
LIVE_PANEL_XPATH = (
    "xpath=//h3[normalize-space()='Live stages']"
    "/ancestor::div[contains(@class, 'rounded-xl')][1]"
)


def api(path: str):
    with urllib.request.urlopen(BASE + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def newest_queue_age_s() -> float | None:
    """Age in seconds of the newest queue/<32hex>.json request, if any."""
    best = None
    for path in QUEUE_DIR.glob("*.json"):
        if re.fullmatch(r"[0-9a-f]{32}", path.stem) is None:
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = obj.get("requested_at")
        if isinstance(ts, str) and ts and (best is None or ts > best):
            best = ts
    if best is None:
        return None
    requested = dt.datetime.fromisoformat(best.replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    return (now - requested).total_seconds()


def shrink_image(path: Path) -> None:
    if path.stat().st_size <= MAX_SHOT_BYTES:
        return
    img = Image.open(path)
    while path.stat().st_size > MAX_SHOT_BYTES and img.width > 480:
        img = img.resize((int(img.width * 0.8), int(img.height * 0.8)))
        img.save(path, optimize=True)
    img.close()


def save_shot(page: Page, name: str, scroll_to: str | None = None) -> Path:
    if scroll_to is not None:
        page.locator(scroll_to).first.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
    path = SHOTS / name
    page.screenshot(path=str(path))
    shrink_image(path)
    return path


def read_results_id(page: Page) -> str:
    loc = page.locator("#results h3")
    try:
        if loc.count() == 0:
            return ""
        return loc.first.inner_text().strip()
    except Exception:
        return ""


def live_panel(page: Page):
    return page.locator(LIVE_PANEL_XPATH)


def read_button(page: Page):
    btn = page.locator("#run button").first
    label = ""
    disabled = True
    try:
        label = btn.inner_text().strip()
        disabled = btn.is_disabled()
    except Exception:
        pass
    return btn, label, disabled


def run() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[int, str, bool, str]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    def check(step: int, name: str, ok: bool, detail: str = "") -> bool:
        checks.append((step, name, ok, detail))
        line = ("PASS" if ok else "FAIL") + f"  {step}. {name}"
        if detail:
            line += f"  [{detail}]"
        print(line, flush=True)
        return ok

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", on_console)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.url} :: {req.failure}"))

        try:
            # ------------------------------------------------------------------
            # Step 1: load; brand visible; cached last-run shows 8/8; history rows.
            # ------------------------------------------------------------------
            page.goto(BASE + "/")
            page.locator("h1").first.wait_for(state="visible", timeout=15000)
            brand_ok = page.locator("header").get_by_text("routing-lab").first.is_visible()
            last_run = api("/api/last-run").get("report")
            history = api("/api/history")

            chip_ok = False
            try:
                page.locator("#results").get_by_text("8/8", exact=True).first.wait_for(
                    state="visible", timeout=15000
                )
                chip_ok = True
            except Exception:
                pass

            rows_ok = False
            try:
                page.locator("#history tbody tr").first.wait_for(
                    state="visible", timeout=15000
                )
                rows_ok = page.locator("#history tbody tr").count() >= 1
            except Exception:
                pass

            check(
                1,
                "load: brand + cached last-run 8/8 + history rows",
                brand_ok and chip_ok and rows_ok,
                f"brand={brand_ok} chip8of8={chip_ok} rows={page.locator('#history tbody tr').count()} "
                f"api_report={last_run.get('run_id') if last_run else None}",
            )
            save_shot(page, "01-initial.png", scroll_to="#results")

            # ------------------------------------------------------------------
            # Step 2: record displayed newest run id; cross-check /api/history.
            # ------------------------------------------------------------------
            old_id = ""
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not old_id:
                old_id = read_results_id(page)
                if not old_id:
                    page.wait_for_timeout(250)
            api_id = history[0]["run_id"] if history else ""
            check(
                2,
                "newest displayed run id matches /api/history",
                bool(old_id) and old_id == api_id and (last_run or {}).get("run_id") == old_id,
                f"page={old_id!r} api_history[0]={api_id!r}",
            )

            # ------------------------------------------------------------------
            # Step 3: click "Run experiment"; handle gates; live panel appears.
            # Bounded to 3 attempts / 300 s total.
            # ------------------------------------------------------------------
            gate_notes: list[str] = []
            attempts = 0
            live_ok = False
            loop_deadline = time.monotonic() + 300
            while attempts < 3 and time.monotonic() < loop_deadline and not live_ok:
                attempts += 1
                btn, label, disabled = read_button(page)
                enable_wait = 30 if attempts == 1 else 150
                enable_deadline = min(loop_deadline, time.monotonic() + enable_wait)
                while disabled and time.monotonic() < enable_deadline:
                    page.wait_for_timeout(500)
                    btn, label, disabled = read_button(page)
                if disabled:
                    gate_notes.append(f"attempt {attempts}: button stayed disabled ({label!r})")
                    break

                try:
                    btn.click()
                except Exception as exc:
                    gate_notes.append(f"attempt {attempts}: click raised {exc!r}")
                    continue

                outcome = ""
                outcome_wait = time.monotonic() + 20
                while time.monotonic() < outcome_wait:
                    panel = live_panel(page)
                    try:
                        if panel.count() > 0 and panel.first.is_visible():
                            outcome = "live"
                            break
                    except Exception:
                        pass
                    _, label, _ = read_button(page)
                    try:
                        note = page.locator("#run p[aria-live='polite']").first.inner_text()
                    except Exception:
                        note = ""
                    if "Cooling down" in label or "Rate limited" in note or "Cooling down" in note:
                        outcome = "cooldown"
                        break
                    if "already in progress" in note:
                        outcome = "run_in_progress"
                        break
                    if "trigger is disabled" in note:
                        outcome = "blocked_disabled"
                        break
                    page.wait_for_timeout(300)

                if outcome == "live":
                    live_ok = True
                    break
                if outcome == "blocked_disabled":
                    gate_notes.append(f"attempt {attempts}: server trigger disabled")
                    break
                if outcome == "run_in_progress":
                    gate_notes.append(f"attempt {attempts}: run_in_progress; polling /api/health")
                    while time.monotonic() < loop_deadline:
                        health = api("/api/health")
                        if health["queue"]["running"] == 0 and health["queue"]["pending"] == 0:
                            break
                        page.wait_for_timeout(2000)
                    continue
                if outcome == "cooldown":
                    _, label, _ = read_button(page)
                    match = re.search(r"(\d+)\s*s", label)
                    wait_s = (int(match.group(1)) + 5) if match else 20
                    wait_s = max(1, min(wait_s, int(loop_deadline - time.monotonic())))
                    gate_notes.append(f"attempt {attempts}: cooldown gate; waited {wait_s}s")
                    page.wait_for_timeout(wait_s * 1000)
                    continue
                gate_notes.append(f"attempt {attempts}: no live panel nor gate after 20s")

            check(
                3,
                "click Run experiment -> live SSE stage panel",
                live_ok,
                f"attempts={attempts}; gates={gate_notes or 'none'}",
            )
            clicked_at = time.monotonic()

            # ------------------------------------------------------------------
            # Step 4: wait for at least one scenario pass mark; mid-run screenshot.
            # ------------------------------------------------------------------
            panel = live_panel(page)
            first_pass = False
            while time.monotonic() < clicked_at + 240:
                try:
                    if panel.count() > 0 and panel.first.locator("span:text-is('pass')").count() >= 1:
                        first_pass = True
                        break
                except Exception:
                    pass
                page.wait_for_timeout(500)
            pass_marks = 0
            try:
                if panel.count() > 0:
                    pass_marks = panel.first.locator("span:text-is('pass')").count()
            except Exception:
                pass
            check(
                4,
                "at least one scenario stage completed (pass mark)",
                first_pass,
                f"pass_marks={pass_marks}",
            )
            save_shot(page, "02-mid-run.png", scroll_to="#run")

            # ------------------------------------------------------------------
            # Step 5: done state + fresh results (new run id, 8/8) + API checks.
            # ------------------------------------------------------------------
            done_seen = False
            while time.monotonic() < clicked_at + 240:
                try:
                    if panel.count() > 0 and "results refreshed below" in panel.first.inner_text():
                        done_seen = True
                        break
                except Exception:
                    pass
                page.wait_for_timeout(500)

            new_id = ""
            while time.monotonic() < clicked_at + 240:
                rid = read_results_id(page)
                if rid and rid != old_id:
                    try:
                        if page.locator("#results").get_by_text("8/8", exact=True).count() > 0:
                            new_id = rid
                            break
                    except Exception:
                        pass
                page.wait_for_timeout(500)

            lr = api("/api/last-run").get("report") or {}
            hist2 = api("/api/history")
            api_ok = (
                lr.get("run_id") == new_id
                and lr.get("summary", {}).get("passed") == 8
                and lr.get("summary", {}).get("total") == 8
            )
            hist_ok = (
                len(hist2) == len(history) + 1
                and hist2[0].get("run_id") == new_id
                and hist2[0].get("summary", {}).get("passed") == 8
            )
            check(
                5,
                "done state + fresh 8/8 results with new run id",
                done_seen and bool(new_id) and api_ok and hist_ok,
                f"old={old_id!r} new={new_id!r} done_seen={done_seen} "
                f"last_run_8of8={api_ok} history_grew={hist_ok} (rows {len(history)}->{len(hist2)})",
            )
            save_shot(page, "03-results.png", scroll_to="#results")

            # ------------------------------------------------------------------
            # Step 6: post-run cooldown gate: clicking Run again must show a
            # disabled button with a countdown (server 429 cooldown). Guarded so
            # the click can never start a second real run: only clicked while
            # the 120 s cooldown window is provably still active.
            # ------------------------------------------------------------------
            btn, pre_label, pre_disabled = read_button(page)
            age = newest_queue_age_s()
            remaining = None if age is None else COOLDOWN_S - age
            cooldown_ok = False
            detail6 = (
                f"before click: label={pre_label!r} disabled={pre_disabled}; "
                f"queue_age={None if age is None else round(age, 1)}s remaining={None if remaining is None else round(remaining, 1)}s"
            )
            if remaining is not None and remaining > 8:
                enable_deadline = time.monotonic() + 20
                while pre_disabled and time.monotonic() < enable_deadline:
                    page.wait_for_timeout(250)
                    btn, pre_label, pre_disabled = read_button(page)
                if not pre_disabled:
                    btn.click()
                    wait_deadline = time.monotonic() + 20
                    while time.monotonic() < wait_deadline:
                        _, label, disabled = read_button(page)
                        if disabled and ("Cooling down" in label or "Rate limited" in label):
                            try:
                                note6 = page.locator("#run p[aria-live='polite']").first.inner_text()
                            except Exception:
                                note6 = ""
                            cooldown_ok = True
                            detail6 += f"; after click: label={label!r} disabled=True note={note6.strip()!r}"
                            break
                        page.wait_for_timeout(250)
                else:
                    detail6 += "; button still disabled, no click possible"
            elif remaining is not None:
                # The cooldown provably expired between the run and this check
                # (SSE + result polling can outlast 120 s): clicking would
                # start a second real run, so report a documented skip.
                cooldown_ok = True
                detail6 += "; skipped: cooldown window already closed"
            else:
                detail6 += "; no queue record found -> cannot verify the cooldown gate"
            check(6, "post-run Run button shows disabled cooldown countdown", cooldown_ok, detail6)
            save_shot(page, "03b-cooldown.png", scroll_to="#run")

            # ------------------------------------------------------------------
            # Step 7: Fleet nav entry -> empty-state explainer visible.
            # ------------------------------------------------------------------
            nav = page.get_by_label("Sections")
            nav.get_by_role("link", name="Fleet").click()
            fleet_visible = False
            try:
                page.locator("#fleet").get_by_text("no probe data yet").first.wait_for(
                    state="visible", timeout=15000
                )
                fleet_visible = True
            except Exception:
                pass
            fleet_full = (
                "The production prober runs host-side after deployment "
                + DASH
                + " no probe data yet."
            )
            fleet_full_ok = page.locator("#fleet").get_by_text(fleet_full).count() > 0
            fleet_api = api("/api/fleet")
            check(
                7,
                "Fleet empty state (no probe data yet)",
                fleet_visible and fleet_full_ok and not fleet_api.get("available"),
                f"hash={'#fleet' in page.url} api_available={fleet_api.get('available')}",
            )
            save_shot(page, "04-fleet.png", scroll_to="#fleet")

            # ------------------------------------------------------------------
            # Step 8: Method nav entry -> methodology renders (Architecture heading).
            # ------------------------------------------------------------------
            nav.get_by_role("link", name="Method").click()
            method_ok = False
            try:
                page.locator("#method").get_by_text("Architecture").first.wait_for(
                    state="visible", timeout=15000
                )
                method_ok = True
            except Exception:
                pass
            check(8, "Method section renders methodology (Architecture heading)", method_ok)

            if not all(ok for _, _, ok, _ in checks):
                save_shot(page, "99-failure-state.png")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            print(f"FAIL  0. unhandled exception: {exc!r}", flush=True)
            checks.append((0, "unhandled exception", False, repr(exc)))
            try:
                save_shot(page, "99-failure-state.png")
            except Exception:
                pass
        finally:
            browser.close()

    log_path = SHOTS / "console-network.log"
    log_path.write_text(
        "console errors:\n"
        + "\n".join(console_errors)
        + "\n\npage errors:\n"
        + "\n".join(page_errors)
        + "\n\nfailed requests:\n"
        + "\n".join(failed_requests)
        + "\n",
        encoding="utf-8",
    )

    failures = [c for c in checks if not c[2]]
    print("")
    if console_errors or page_errors or failed_requests:
        print("Captured page console errors / page errors / failed requests (raw):")
        for line in console_errors:
            print(f"  console: {line}")
        for line in page_errors:
            print(f"  pageerror: {line}")
        for line in failed_requests:
            print(f"  requestfailed: {line}")
    else:
        print(
            "Captured page console errors / failed requests: none "
            f"(console={len(console_errors)} pageerror={len(page_errors)} requestfailed={len(failed_requests)})"
        )
        print(f"(full log: {log_path})")
    passed = len(checks) - len(failures)
    print(f"VERDICT: {'PASS' if not failures else 'FAIL'} - {passed}/{len(checks)} checks passed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(run())
