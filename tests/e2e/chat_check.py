#!/usr/bin/env python3
"""Browser end-to-end test for the "Try the router" chat on the live dashboard.

Covers: the chat section renders on https://lab.superseb.xyz; one real message
("Say hello in five words.") is sent through the live gateway; within 75 s the
page shows EITHER streamed reply text (answer or thinking block) plus the
attribution line, OR a friendly mapped state (rate/busy limit, pools cooling
down, paused, router error) - the check stays green on the friendly branch
because the free pools may legitimately be saturated. When a real reply
arrives the attribution line must read "served by <model> · <n>ms"; the raw
SSE body of the page's own POST /api/chat response is inspected read-side so
that, whenever the meta frame carried a concrete ``model_name``, the line is
not allowed to render the free-chat alias. Asserts no page or console errors
and captures docs/e2e/10-chat.png.

Run from the repo root on the host (the image ships the browsers but not the
Python packages, so they are installed on the fly; the tag must stay
v1.55.0-jammy - v1.55-jammy does not exist):
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp \
    -v "$PWD:/w" -w /w \
    mcr.microsoft.com/playwright/python:v1.55.0-jammy \
    sh -c "pip install --user --quiet playwright==1.55.0 pillow && python tests/e2e/chat_check.py"

Note: each run spends one message from the per-visitor rate window
(5 messages / 10 min), so a busy-window run is expected to land on the
friendly rate-limit branch.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

BASE = "https://lab.superseb.xyz"
REPO = Path(__file__).resolve().parents[2]
SHOTS = REPO / "docs" / "e2e"
MAX_SHOT_BYTES = 600 * 1024
QUESTION = "Say hello in five words."
REPLY_WAIT_S = 75.0

# Friendly terminal states the UI maps server failures to, plus the disabled
# Send-button countdown labels. Any of these green-lights the check when the
# free pools are saturated or the visitor is rate limited.
FRIENDLY_RE = re.compile(
    r"rate limit|free-tier limit|busy|paused|cooling down|timed out|"
    r"returned an error|unavailable|no text",
    re.IGNORECASE,
)

# Attribution shape as rendered: "served by <model> · <n>ms" (middle dot).
ATTRIBUTION_RE = re.compile(r"served by .+ · \d+ms")
# meta frame's model_name inside the captured SSE body: "model_name":".."|null
META_MODEL_RE = re.compile(r'"model_name":\s*(?:"([^"]*)"|null)')

# Cloudflare's Web Analytics injection; matches the whole injected <script> tag.
BEACON_TAG_RE = re.compile(r"<script[^>]*cloudflareinsights[^>]*>\s*</script>")


def shrink_image(path: Path) -> None:
    if path.stat().st_size <= MAX_SHOT_BYTES:
        return
    img = Image.open(path)
    while path.stat().st_size > MAX_SHOT_BYTES and img.width > 480:
        img = img.resize((int(img.width * 0.8), int(img.height * 0.8)))
        img.save(path, optimize=True)
    img.close()


def save_shot(page: Page) -> Path:
    path = SHOTS / "10-chat.png"
    page.locator("#chat").first.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    try:
        page.locator("#chat").first.screenshot(path=str(path))
    except Exception:
        page.screenshot(path=str(path))
    shrink_image(path)
    return path


def visible_text(page: Page, selector: str) -> str:
    loc = page.locator(selector).first
    try:
        if loc.count() == 0:
            return ""
        return loc.inner_text().strip() if loc.is_visible() else ""
    except Exception:
        return ""


def read_button(page: Page) -> tuple[str, bool]:
    btn = page.locator("#chat button[type='submit']").first
    label = ""
    disabled = True
    try:
        label = btn.inner_text().strip()
        disabled = btn.is_disabled()
    except Exception:
        pass
    return label, disabled


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

        # Cloudflare appends its Web Analytics beacon (static.cloudflareinsights.com)
        # to proxied HTML documents; on this network that third-party domain
        # fails TLS for everyone (host curl: "unable to get local issuer
        # certificate"), which would otherwise surface as a console error not
        # caused by the dashboard. Strip the injected tag from the document
        # before Chromium parses it, so the error assertion below stays scoped
        # to the app's own resources.
        def on_document(route):
            if route.request.resource_type != "document":
                route.continue_()
                return
            response = route.fetch()
            route.fulfill(response=response, body=BEACON_TAG_RE.sub("", response.text()))

        page.route(re.compile(r"^https://lab\.superseb\.xyz/$"), on_document)

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{req.url} :: {req.failure}"),
        )

        # The page's own POST /api/chat response (SSE) is kept so its terminal
        # meta frame can be read back once the turn finished.
        chat_responses: list = []

        def on_response(response) -> None:
            if response.request.method == "POST" and response.url.endswith("/api/chat"):
                chat_responses.append(response)

        page.on("response", on_response)

        try:
            # ------------------------------------------------------------------
            # Step 1: load the live site; the chat card renders with input,
            # Send, the 400-char mirror and the footnote.
            # ------------------------------------------------------------------
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
            page.locator("#chat").first.wait_for(state="visible", timeout=30000)

            heading_ok = "Try the router" in visible_text(page, "#chat h2")
            input_loc = page.locator("#chat input[aria-label='Chat message']").first
            input_ok = input_loc.is_visible()
            maxlen_ok = input_ok and input_loc.get_attribute("maxlength") == "400"
            counter_ok = visible_text(page, "#chat").find("0/400") != -1
            button_label, button_disabled = read_button(page)
            send_ok = button_label == "Send" and button_disabled  # disabled while empty
            footnote_ok = "Real completions from free-tier pools." in visible_text(page, "#chat")
            check(
                1,
                "chat section renders (heading, input cap, counter, Send, footnote)",
                heading_ok and input_ok and maxlen_ok and counter_ok and send_ok and footnote_ok,
                f"heading={heading_ok} input={input_ok} maxlength400={maxlen_ok} "
                f"counter0of400={counter_ok} send_disabled_empty={send_ok} footnote={footnote_ok}",
            )

            # ------------------------------------------------------------------
            # Step 2: send the message (Enter; fall back to clicking Send).
            # ------------------------------------------------------------------
            input_loc.click()
            input_loc.fill(QUESTION)
            page.wait_for_timeout(150)
            _, disabled_after_fill = read_button(page)
            input_loc.press("Enter")
            sent_ok = False
            send_deadline = time.monotonic() + 4
            while time.monotonic() < send_deadline and not sent_ok:
                if QUESTION in page.locator("#chat").inner_text():
                    sent_ok = True
                    break
                page.wait_for_timeout(200)
            if not sent_ok:
                page.locator("#chat button[type='submit']").first.click()
                send_deadline = time.monotonic() + 4
                while time.monotonic() < send_deadline and not sent_ok:
                    if QUESTION in page.locator("#chat").inner_text():
                        sent_ok = True
                        break
                    page.wait_for_timeout(200)
            check(
                2,
                "message sent (Enter) and echoed into the transcript",
                sent_ok and not disabled_after_fill,
                f"sent={sent_ok} button_enabled_after_fill={not disabled_after_fill}",
            )

            # ------------------------------------------------------------------
            # Step 3: within 75 s: reply text + attribution, or a friendly state.
            # ------------------------------------------------------------------
            outcome = ""
            detail = ""
            reply_deadline = time.monotonic() + REPLY_WAIT_S
            while time.monotonic() < reply_deadline:
                attribution = visible_text(page, "[data-testid='chat-attribution']")
                answer = visible_text(page, "[data-testid='chat-answer']")
                thinking = visible_text(page, "[data-testid='chat-thinking']")
                notice = visible_text(page, "[data-testid='chat-notice']")
                label, _ = read_button(page)

                if attribution and (answer or thinking):
                    outcome = "reply"
                    detail = (
                        f"attribution={attribution!r} answer_len={len(answer)} "
                        f"thinking_len={len(thinking)}"
                    )
                    break
                friendly = notice or (label if FRIENDLY_RE.search(label) else "")
                if friendly and FRIENDLY_RE.search(friendly):
                    outcome = "friendly"
                    detail = f"state={friendly!r}"
                    break
                page.wait_for_timeout(1000)

            elapsed = REPLY_WAIT_S - max(0.0, reply_deadline - time.monotonic())
            check(
                3,
                "reply text + attribution, or friendly busy/limit state (75 s)",
                outcome != "",
                f"outcome={outcome or 'timeout'} after {elapsed:.0f}s; {detail}",
            )

            # ------------------------------------------------------------------
            # Step 4: attribution names the served model. Every real reply must
            # match "served by <model> · <n>ms"; when the captured meta frame
            # carried a concrete model_name, the free-chat alias must not be
            # the rendered name. Skipped (green) on the friendly-state branch.
            # ------------------------------------------------------------------
            attribution_ok = True
            attribution_detail = f"skipped (outcome={outcome or 'timeout'})"
            if outcome == "reply":
                attribution = visible_text(page, "[data-testid='chat-attribution']")
                shape_ok = bool(ATTRIBUTION_RE.search(attribution))
                meta_model_name = None
                meta_parsed = False
                if chat_responses:
                    try:
                        raw = chat_responses[-1].text()
                    except Exception:
                        raw = ""
                    match = META_MODEL_RE.search(raw or "")
                    if match:
                        meta_parsed = True
                        meta_model_name = match.group(1)
                alias_ok = True
                if meta_parsed and meta_model_name and meta_model_name != "free-chat":
                    alias_ok = f"served by {meta_model_name}" in attribution and (
                        "served by free-chat" not in attribution
                    )
                attribution_ok = shape_ok and alias_ok
                attribution_detail = (
                    f"attribution={attribution!r} shape={shape_ok} meta_parsed={meta_parsed} "
                    f"meta_model_name={meta_model_name!r} alias_ok={alias_ok}"
                )
            check(
                4,
                "attribution names the served model (served by <model> · Nms, not the alias)",
                attribution_ok,
                attribution_detail,
            )

            shot = save_shot(page)
            print(f"  screenshot: {shot}", flush=True)

        except Exception as exc:  # noqa: BLE001 - report, do not mask
            print(f"FAIL  0. unhandled exception: {exc!r}", flush=True)
            checks.append((0, "unhandled exception", False, repr(exc)))
            try:
                save_shot(page)
            except Exception:
                pass
        finally:
            browser.close()

        # ----------------------------------------------------------------------
        # Step 5: no page or console errors during the whole run.
        # ----------------------------------------------------------------------
        check(
            5,
            "no page or console errors",
            not console_errors and not page_errors,
            f"console={len(console_errors)} pageerror={len(page_errors)} "
            f"requestfailed={len(failed_requests)}",
        )
        if console_errors or page_errors or failed_requests:
            print("Captured page console errors / page errors / failed requests (raw):")
            for line in console_errors:
                print(f"  console: {line}")
            for line in page_errors:
                print(f"  pageerror: {line}")
            for line in failed_requests:
                print(f"  requestfailed: {line}")

    failures = [c for c in checks if not c[2]]
    passed = len(checks) - len(failures)
    print(f"VERDICT: {'PASS' if not failures else 'FAIL'} - {passed}/{len(checks)} checks passed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(run())
