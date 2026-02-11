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
SUMMARY_RE = re.compile(r"SUMMARY:\s+(\d+)\s+passed,\s+(\d+)\s+failed")


def wait_for_server(url: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    last_error = "server did not respond"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/index.html", timeout=5) as resp:
                if resp.status == 200:
                    return
                last_error = f"unexpected status {resp.status}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(1)

    raise RuntimeError(f"Timed out waiting for server at {url}: {last_error}")


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
    wait_for_server(BASE_URL, SERVER_WAIT_SECONDS)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()

        page_errors: list[str] = []

        page = context.new_page()
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        try:
            page.goto(
                f"{BASE_URL}/index.html", wait_until="domcontentloaded", timeout=180_000
            )
            page.wait_for_selector("#pkgTestBtn", timeout=120_000)
            page.click("#pkgTestBtn")
            page.wait_for_function(
                """
                () => /SUMMARY:\\s+\\d+\\s+passed,\\s+\\d+\\s+failed/.test(
                    document.querySelector('#output')?.innerText || ''
                )
                """,
                timeout=900_000,
            )

            output_text = page.inner_text("#output")
            require_summary_ok(output_text)

            harness = context.new_page()
            harness.on("pageerror", lambda exc: page_errors.append(str(exc)))
            harness.goto(
                f"{BASE_URL}/pages/test-harness.html",
                wait_until="domcontentloaded",
                timeout=180_000,
            )
            harness.wait_for_selector("#statusBadge", timeout=120_000)
            harness.wait_for_function(
                "() => document.querySelectorAll('#runGrid button').length >= 1",
                timeout=240_000,
            )
            print("Architecture harness loaded and rendered run controls")

        except PlaywrightTimeoutError as exc:
            print(f"Playwright timeout: {exc}")
            return 1
        finally:
            browser.close()

        if page_errors:
            print("Detected browser page errors:")
            for err in page_errors:
                print(f"- {err}")
            return 1

    print("Browser smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
