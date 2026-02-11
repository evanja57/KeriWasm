"""
run_blake3_suite.py - Hio-based test runner for blake3.

This module uses hio's cooperative multitasking (via WebDoist) to run
the blake3 test suite without blocking the browser's main thread.

Architecture:
  - test_loaders.py: Loads tests from test modules (pure functions)
  - test_runner_doer.py: Generic Doer that executes injected tests
  - This file: Orchestrates loading and execution
"""

import asyncio

from core import ui_log


def log(msg: str, css_class: str = "info"):
    """Emit a structured log entry."""
    ui_log.emit(msg, css_class)


def clear_output():
    """Clear the active output sink."""
    ui_log.clear()


async def _run_blake3_suite_async():
    """
    Async entry point that uses hio's WebDoist to run tests.

    This orchestrator:
    1. Loads tests using test_loaders (configuration)
    2. Injects them into TestRunnerDoer (execution)
    3. Runs via WebDoist for cooperative scheduling
    """
    clear_output()
    log("Initializing hio-based blake3 test runner...")
    log("")

    try:
        # Import hio components
        from core.hio_bridge import WebDoist
        from core.test_runner_doer import TestRunnerDoer
        from core.test_loaders import load_all_blake3_tests

        log("Loaded hio and test components", "success")
    except Exception as e:
        log(f"FAILED to load components: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
        return

    # Show blake3 info before loading tests
    try:
        import blake3

        log(f"blake3 version: {blake3.__version__}", "success")
        log("")
    except Exception as e:
        log(f"FAILED to import blake3: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
        return

    # Load tests (configuration phase)
    log("Loading tests from test modules...")
    test_queue = load_all_blake3_tests()
    log(f"Found {len(test_queue)} test entries")
    log("")

    # Create the test doer with injected test queue (hio convention)
    test_doer = TestRunnerDoer(
        test_queue=test_queue,
        title="BLAKE3 FULL TEST SUITE (hio scheduler)",
        tock=0.0,  # tock=0 means ready to run immediately
    )

    # Create WebDoist scheduler
    # tock=0.01 means 10ms between cycles (100 updates/sec max)
    # real=True means honor timing (vs run as fast as possible)
    web_doist = WebDoist(doers=[test_doer], tock=0.01, real=True, limit=600.0)

    log("Starting test execution with hio scheduler...")
    log("")

    try:
        await web_doist.do()
    except Exception as e:
        log(f"Test execution failed: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")

    log("")
    log("Test run complete.", "info")


def run_blake3_suite(event):
    """
    Button click handler - starts the hio-based test runner.

    This function is called synchronously by PyScript when the button
    is clicked. It schedules the async test runner to execute.
    """
    return asyncio.ensure_future(_run_blake3_suite_async())
