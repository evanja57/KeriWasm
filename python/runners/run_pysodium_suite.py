"""
PyScript-compatible test runner for the full pysodium unittest suite.

This module adapts Python's unittest framework to run in the browser
via PyScript, capturing output and displaying it in the page.
"""

import sys
import unittest

from core import ui_log


def log(msg: str, css_class: str = "info"):
    """Emit a structured log entry."""
    ui_log.emit(msg, css_class)


def clear_output():
    """Clear the active output sink."""
    ui_log.clear()


class BrowserTestResult(unittest.TestResult):
    """Custom TestResult that logs to the browser output div."""

    def __init__(self):
        super().__init__()
        self.successes = []

    def startTest(self, test):
        super().startTest(test)
        log(f"Running: {test}", "info")

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test)
        log(f"  PASS: {test}", "success")

    def addError(self, test, err):
        super().addError(test, err)
        log(f"  ERROR: {test}", "fail")
        log(f"    {err[1]}", "fail")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        log(f"  FAIL: {test}", "fail")
        log(f"    {err[1]}", "fail")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        log(f"  SKIP: {test} - {reason}", "info")


def _run_full_suite():
    """Run the full pysodium unittest suite."""
    log("================================================================")
    log("Starting Full Pysodium unittest Suite")
    log("================================================================")

    try:
        # Import the test module
        from tests.pysodium.pysodium_unittest import TestPySodium

        log(
            "SUCCESS: Imported tests.pysodium.pysodium_unittest.TestPySodium", "success"
        )
    except ImportError as exc:
        log(f"FAIL: Could not import pysodium_unittest: {exc}", "fail")
        raise

    # Load all tests from the TestPySodium class
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestPySodium)

    log(f"Found {suite.countTestCases()} tests to run")
    log("----------------------------------------------------------------")

    # Run with our custom result handler
    result = BrowserTestResult()
    suite.run(result)

    # Summary
    log("================================================================")
    log("TEST SUMMARY")
    log("================================================================")
    log(f"Tests run:    {result.testsRun}")
    log(
        f"Passed:       {len(result.successes)}",
        "success" if result.successes else "info",
    )
    log(f"Failures:     {len(result.failures)}", "fail" if result.failures else "info")
    log(f"Errors:       {len(result.errors)}", "fail" if result.errors else "info")
    log(f"Skipped:      {len(result.skipped)}", "info")

    if result.failures:
        log("----------------------------------------------------------------")
        log("FAILURES:", "fail")
        for test, traceback in result.failures:
            log(f"  {test}", "fail")

    if result.errors:
        log("----------------------------------------------------------------")
        log("ERRORS:", "fail")
        for test, traceback in result.errors:
            log(f"  {test}", "fail")

    if result.wasSuccessful():
        log("----------------------------------------------------------------")
        log("ALL TESTS PASSED!", "success")
    else:
        log("----------------------------------------------------------------")
        log("SOME TESTS FAILED - see details above", "fail")


def run_full_suite(event):
    """Async wrapper for running the test suite."""
    clear_output()
    log("Initializing full pysodium test suite...", "info")

    # Capture stdout/stderr
    stdout = sys.stdout
    stderr = sys.stderr

    try:
        _run_full_suite()
    except Exception as exc:
        log(f"Test suite failed with exception: {exc}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
    finally:
        sys.stdout = stdout
        sys.stderr = stderr
