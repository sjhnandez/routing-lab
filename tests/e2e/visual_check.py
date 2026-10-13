#!/usr/bin/env python3
"""Production visual check for the routing-lab dashboard (https://lab.superseb.xyz).

Asserts the hero and purpose copy, the two real mermaid diagrams inside the
Method section (>= 2 rendered SVGs), the scenario-catalog summary and a clean
page-error log; writes docs/e2e/08-overview.png and docs/e2e/09-method-diagrams.png.
One retry after ~10 s absorbs tunnel/edge lag.

Run from the repo root on the host (the image ships the browsers but not the
Python packages, so they are installed on the fly; the tag must stay
v1.55.0-jammy - v1.55-jammy does not exist):
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp \
    -v "$PWD:/w" -w /w \
    mcr.microsoft.com/playwright/python:v1.55.0-jammy \
    sh -c "pip install --user --quiet playwright==1.55.0 pillow && python tests/e2e/visual_check.py"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

BASE = "https://lab.superseb.xyz"
REPO = Path(__file__).resolve().parents[2]
SHOTS = REPO / "docs" / "e2e"
MAX_SHOT_BYTES = 600 * 1024
MERMAID_WAIT_S = 30
RETRY_DELAY_S = 10

HERO_A = "Every AI request on this server"
HERO_B = "503 retryability depends on pool topology"


def shrink_image(path: Path) -> None:
    if path.stat().st_size <= MAX_SHOT_BYTES:
        return
    img = Image.open(path)
    while path.stat().st_size > MAX_SHOT_BYTES and img.width > 480:
        img = img.resize((int(img.width * 0.8), int(img.height * 0.8)))
        img.save(path, optimize=True)
    img.close()


def run_attempt(
    page: Page, page_errors: list[str], failed_requests: list[str]
) -> list[tuple[int, str, bool, str]]:
    checks: list[tuple[int, str, bool, str]] = []

    def check(step: int, name: str, ok: bool, detail: str = "") -> bool:
        checks.append((step, name, ok, detail))
        line = ("PASS" if ok else "FAIL") + f"  {step}. {name}"
        if detail:
            line += f"  [{detail}]"
        print(line, flush=True)
        return ok

    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.locator("#top h1").first.wait_for(state="visible", timeout=20000)

    # a) hero copy: both load-bearing claims.
    hero = page.locator("#top").inner_text()
    ok_a = HERO_A in hero
    ok_b = HERO_B in hero
    check(
        1,
        "hero copy (request line + 503 retryability line)",
        ok_a and ok_b,
        f"request={ok_a} retryability={ok_b}",
    )

    # b) purpose overview heading.
    try:
        ok = page.get_by_role("heading", name="What it proves").first.is_visible()
    except Exception:
        ok = False
    check(2, '"What it proves" visible', ok)

    # Screenshot: top of the page.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    shot = SHOTS / "08-overview.png"
    page.screenshot(path=str(shot))
    shrink_image(shot)

    # c) mermaid diagrams in #method (wait up to 30 s for the lazy chunk + render).
    svg = page.locator("#method .mermaid-diagram svg")
    deadline = time.monotonic() + MERMAID_WAIT_S
    while svg.count() < 2 and time.monotonic() < deadline:
        page.wait_for_timeout(500)
    count = svg.count()
    total = page.locator("#method svg").count()
    check(
        3,
        "#method mermaid diagrams rendered (>= 2 svg)",
        count >= 2,
        f"mermaid_svgs={count} total_svgs={total}",
    )

    # Screenshot: first rendered diagram in view.
    target = svg.first if count > 0 else page.locator("#method")
    target.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    shot = SHOTS / "09-method-diagrams.png"
    page.screenshot(path=str(shot))
    shrink_image(shot)

    # d) scenario catalog summary exists.
    summary_ok = False
    summary_text = ""
    try:
        summary_text = page.locator("#method summary").first.inner_text().strip()
        summary_ok = "Scenario catalog" in summary_text
    except Exception:
        pass
    check(4, 'Method summary contains "Scenario catalog"', summary_ok, summary_text[:60])

    # e) no uncaught page errors.
    detail = f"errors={[e[:120] for e in page_errors[:2]]}" if page_errors else "none"
    if failed_requests:
        detail += f" netfailed={len(failed_requests)}"
    check(5, "no page errors", len(page_errors) == 0, detail)

    return checks


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page_errors: list[str] = []
    failed_requests: list[str] = []
    checks: list[tuple[int, str, bool, str]] = []
    attempt = 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.url} :: {req.failure}"))

        for attempt in (1, 2):
            if attempt == 2:
                print(f"\n--- retrying once after {RETRY_DELAY_S}s (edge lag) ---", flush=True)
                time.sleep(RETRY_DELAY_S)
                page_errors.clear()
                failed_requests.clear()
            print(f"--- attempt {attempt}/2 ---", flush=True)
            try:
                checks = run_attempt(page, page_errors, failed_requests)
            except Exception as exc:
                print(f"FAIL  attempt {attempt} aborted  [{exc!r}]", flush=True)
                checks = [(1, "attempt aborted", False, repr(exc)[:160])]
            if all(ok for _, _, ok, _ in checks):
                break

        if failed_requests:
            for line in failed_requests[:5]:
                print(f"  requestfailed: {line}", flush=True)
        browser.close()

    passed = sum(1 for _, _, ok, _ in checks if ok)
    verdict = "PASS" if checks and passed == len(checks) else "FAIL"
    print(f"\nVERDICT: {verdict} ({passed}/{len(checks)} checks, attempt {attempt})", flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
