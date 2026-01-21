"""
run_liboqs_suite.py - Hio-based test runner for liboqs-python.

This module uses hio's cooperative multitasking (via WebDoist) to run
the liboqs test suite without blocking the browser's main thread.

Architecture:
  - test_loaders.py: Loads tests from test modules (pure functions)
  - test_runner_doer.py: Generic Doer that executes injected tests
  - This file: Orchestrates loading and execution
"""

import asyncio
import datetime

# Try to import pyscript document
try:
    from pyscript import document
except ImportError:
    document = None


def log(msg: str, css_class: str = "info"):
    """Append a message to the output div."""
    if document is None:
        print(msg)
        return
    output = document.querySelector("#output")
    if output is None:
        print(msg)
        return
    time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    msg = str(msg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    output.innerHTML += f'<span class="{css_class}">[{time}] {msg}</span>\n'
    output.scrollTop = output.scrollHeight


def clear_output():
    """Clear the output div."""
    if document is None:
        return
    output = document.querySelector("#output")
    if output:
        output.innerHTML = ""


async def _run_liboqs_suite_async():
    """
    Async entry point that uses hio's WebDoist to run tests.
    
    This orchestrator:
    1. Loads tests using test_loaders (configuration)
    2. Injects them into TestRunnerDoer (execution)
    3. Runs via WebDoist for cooperative scheduling
    """
    clear_output()
    log("Initializing hio-based liboqs test runner...")
    log("")
    
    try:
        # Import hio components
        from hio_bridge import WebDoist
        from test_runner_doer import TestRunnerDoer
        from test_loaders import load_all_liboqs_tests
        log("Loaded hio and test components", "success")
    except Exception as e:
        log(f"FAILED to load components: {e}", "fail")
        import traceback
        log(traceback.format_exc(), "fail")
        return
    
    # Show oqs info before loading tests
    try:
        import oqs
        log(f"liboqs version: {oqs.oqs_version()}", "success")
        log(f"Enabled KEMs: {len(oqs.get_enabled_kem_mechanisms())}")
        log(f"Enabled SIGs: {len(oqs.get_enabled_sig_mechanisms())}")
        log("")
    except Exception as e:
        log(f"FAILED to import oqs: {e}", "fail")
        import traceback
        log(traceback.format_exc(), "fail")
        return
    
    # Load tests (configuration phase)
    log("Loading tests from test modules...")
    test_queue = load_all_liboqs_tests()
    log(f"Found {len(test_queue)} test entries")
    log("")
    
    # Create the test doer with injected test queue (hio convention)
    test_doer = TestRunnerDoer(
        test_queue=test_queue,
        title="LIBOQS-PYTHON FULL TEST SUITE (hio scheduler)",
        tock=0.0  # tock=0 means ready to run immediately
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


def run_liboqs_suite(event):
    """
    Button click handler - starts the hio-based test runner.
    
    This function is called synchronously by PyScript when the button
    is clicked. It schedules the async test runner to execute.
    """
    asyncio.ensure_future(_run_liboqs_suite_async())
