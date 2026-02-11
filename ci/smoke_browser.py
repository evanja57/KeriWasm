#!/usr/bin/env python3
"""Browser smoke test for KeriWasm PyScript routes."""

from __future__ import annotations

import os
import re
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("KERIWASM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SERVER_WAIT_SECONDS = 90
PYSCRIPT_ENTRYPOINT_WAIT_SECONDS = 300
PYSCRIPT_ENTRYPOINT_HEARTBEAT_SECONDS = 15
PYSCRIPT_ENTRYPOINT_POLL_SECONDS = 2
PACKAGE_SUMMARY_WAIT_SECONDS = 900
PACKAGE_SUMMARY_HEARTBEAT_SECONDS = 15
PACKAGE_SUMMARY_POLL_SECONDS = 3
SUMMARY_RE = re.compile(r"SUMMARY:\s+(\d+)\s+passed,\s+(\d+)\s+failed")


def log_step(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def wait_for_server(url: str, timeout_s: int) -> None:
    log_step(f"Waiting for local server at {url} (timeout={timeout_s}s)")
    start = time.time()
    deadline = time.time() + timeout_s
    next_heartbeat = start
    last_error = "server did not respond"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/index.html", timeout=5) as resp:
                if resp.status == 200:
                    elapsed = int(time.time() - start)
                    log_step(f"Server is reachable after {elapsed}s")
                    return
                last_error = f"unexpected status {resp.status}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)

        now = time.time()
        if now >= next_heartbeat:
            elapsed = int(now - start)
            log_step(
                f"Still waiting for server... elapsed={elapsed}s last_error={last_error}"
            )
            next_heartbeat = now + 10

        time.sleep(1)

    raise RuntimeError(f"Timed out waiting for server at {url}: {last_error}")


def _last_non_empty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "<no output yet>"
    last = lines[-1]
    if len(last) > 240:
        return f"{last[:237]}..."
    return last


def wait_for_python_entrypoint(page, fn_name: str, timeout_s: int) -> None:
    log_step(f"Waiting for PyScript entrypoint window.{fn_name} (timeout={timeout_s}s)")
    start = time.monotonic()
    deadline = start + timeout_s
    next_heartbeat = start
    output_text = ""

    while True:
        ready = bool(page.evaluate(f"() => typeof window['{fn_name}'] === 'function'"))
        output_text = page.inner_text("#output")
        if ready:
            elapsed = int(time.monotonic() - start)
            log_step(f"PyScript entrypoint window.{fn_name} is ready after {elapsed}s")
            return

        now = time.monotonic()
        if now >= deadline:
            raise RuntimeError(
                f"Timed out waiting for window.{fn_name} after {timeout_s}s; "
                f"last_output_line={_last_non_empty_line(output_text)!r}"
            )

        if now >= next_heartbeat:
            elapsed = int(now - start)
            log_step(
                f"Still waiting for window.{fn_name}... elapsed={elapsed}s "
                f"last_line={_last_non_empty_line(output_text)!r}"
            )
            next_heartbeat = now + PYSCRIPT_ENTRYPOINT_HEARTBEAT_SECONDS

        time.sleep(PYSCRIPT_ENTRYPOINT_POLL_SECONDS)


def wait_for_package_summary(page) -> str:
    log_step(
        "Waiting for package summary "
        f"(timeout={PACKAGE_SUMMARY_WAIT_SECONDS}s, heartbeat={PACKAGE_SUMMARY_HEARTBEAT_SECONDS}s)"
    )
    start = time.monotonic()
    deadline = start + PACKAGE_SUMMARY_WAIT_SECONDS
    next_heartbeat = start
    output_text = ""

    while True:
        output_text = page.inner_text("#output")
        if SUMMARY_RE.search(output_text):
            elapsed = int(time.monotonic() - start)
            log_step(f"Package summary detected after {elapsed}s")
            return output_text

        now = time.monotonic()
        if now >= deadline:
            raise RuntimeError(
                "Timed out waiting for package summary "
                f"after {PACKAGE_SUMMARY_WAIT_SECONDS}s; "
                f"last_output_line={_last_non_empty_line(output_text)!r}"
            )

        if now >= next_heartbeat:
            elapsed = int(now - start)
            lines = [line for line in output_text.splitlines() if line.strip()]
            log_step(
                "Still waiting for summary... "
                f"elapsed={elapsed}s output_lines={len(lines)} "
                f"last_line={_last_non_empty_line(output_text)!r}"
            )
            next_heartbeat = now + PACKAGE_SUMMARY_HEARTBEAT_SECONDS

        time.sleep(PACKAGE_SUMMARY_POLL_SECONDS)


def require_summary_ok(output_text: str) -> None:
    match = SUMMARY_RE.search(output_text)
    if not match:
        raise AssertionError("Did not find package test summary in output")

    passed = int(match.group(1))
    failed = int(match.group(2))
    print(f"Package summary: {passed} passed, {failed} failed")
    if failed != 0:
        raise AssertionError(f"Package smoke tests reported failures: {failed}")


def main() -> int:
    log_step(f"Starting browser smoke checks for {BASE_URL}")
    wait_for_server(BASE_URL, SERVER_WAIT_SECONDS)
    log_step("Launching headless Chromium")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()

        page_errors: list[str] = []
        browser_console_errors: list[str] = []

        def on_console(msg) -> None:
            text = msg.text
            if msg.type == "error":
                browser_console_errors.append(text)
                log_step(f"Browser console error: {text}")

        page = context.new_page()
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("console", on_console)

        try:
            log_step("Opening /index.html")
            page.goto(
                f"{BASE_URL}/index.html", wait_until="domcontentloaded", timeout=180_000
            )
            log_step("Waiting for package test button")
            page.wait_for_selector("#pkgTestBtn", timeout=120_000)
            wait_for_python_entrypoint(
                page, "run_tests", timeout_s=PYSCRIPT_ENTRYPOINT_WAIT_SECONDS
            )
            log_step("Clicking package smoke test button")
            output_before = page.inner_text("#output")
            page.click("#pkgTestBtn")
            time.sleep(2)
            output_after = page.inner_text("#output")
            if output_after.strip() == output_before.strip():
                log_step(
                    "Output unchanged after click; invoking window.run_tests(null) fallback"
                )
                page.evaluate(
                    """
                    () => {
                        if (typeof window.run_tests !== 'function') {
                            throw new Error('window.run_tests is not available');
                        }
                        window.run_tests(null);
                    }
                    """
                )

            output_text = wait_for_package_summary(page)
            require_summary_ok(output_text)

            log_step("Opening /pages/test-harness.html")
            harness = context.new_page()
            harness.on("pageerror", lambda exc: page_errors.append(str(exc)))
            harness.on("console", on_console)
            harness.goto(
                f"{BASE_URL}/pages/test-harness.html",
                wait_until="domcontentloaded",
                timeout=180_000,
            )
            log_step("Waiting for harness status badge")
            harness.wait_for_selector("#statusBadge", timeout=120_000)
            log_step("Waiting for harness run controls")
            harness.wait_for_function(
                "() => document.querySelectorAll('#runGrid button').length >= 1",
                timeout=240_000,
            )
            log_step("Architecture harness loaded and rendered run controls")

        except PlaywrightTimeoutError as exc:
            log_step(f"Playwright timeout: {exc}")
            return 1
        except Exception as exc:
            log_step(f"Smoke check failed: {type(exc).__name__}: {exc}")
            return 1
        finally:
            browser.close()

        if browser_console_errors:
            log_step("Captured browser console errors:")
            for err in browser_console_errors:
                log_step(f"- {err}")

        if page_errors:
            log_step("Detected browser page errors:")
            for err in page_errors:
                log_step(f"- {err}")
            return 1

    log_step("Browser smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
