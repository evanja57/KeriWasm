"""
Worker-side liboqs test runner.

Runs the liboqs suite inside a Pyodide web worker and streams
batched log entries back to the main thread via postMessage.
"""

import datetime
import json

import js  # type: ignore


def _escape(msg: str) -> str:
    return str(msg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def log(msg: str, css_class: str = "info") -> None:
    time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    payload = json.dumps(
        {
            "time": time,
            "msg": _escape(msg),
            "css": css_class,
        }
    )
    js.enqueueLiboqsLogJson(payload)


async def run() -> None:
    """Run the liboqs test suite with hio scheduling in the worker."""
    log("Initializing hio-based liboqs test runner...")
    log("")

    try:
        from core.hio_bridge import WebDoist
        from core import test_runner_doer
        from core.test_runner_doer import TestRunnerDoer
        from core.test_loaders import load_all_liboqs_tests

        # Route all test_runner_doer logging into the worker logger
        test_runner_doer.log = log
        log("Loaded hio and test components", "success")
    except Exception as exc:
        log(f"FAILED to load components: {exc}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
        js.flushLiboqsLogs()
        return

    try:
        import oqs

        log(f"liboqs version: {oqs.oqs_version()}", "success")
        log(f"Enabled KEMs: {len(oqs.get_enabled_kem_mechanisms())}")
        log(f"Enabled SIGs: {len(oqs.get_enabled_sig_mechanisms())}")
        log("")
    except Exception as exc:
        log(f"FAILED to import oqs: {exc}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
        js.flushLiboqsLogs()
        return

    log("Loading tests from test modules...")
    test_queue = load_all_liboqs_tests()
    log(f"Found {len(test_queue)} test entries")
    log("")

    test_doer = TestRunnerDoer(
        test_queue=test_queue,
        title="LIBOQS-PYTHON FULL TEST SUITE (hio scheduler)",
        tock=0.0,
    )

    web_doist = WebDoist(doers=[test_doer], tock=0.01, real=True, limit=600.0)

    log("Starting test execution with hio scheduler...")
    log("")

    try:
        await web_doist.do()
    except Exception as exc:
        log(f"Test execution failed: {exc}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")

    log("")
    log("Test run complete.", "info")
    js.flushLiboqsLogs()
