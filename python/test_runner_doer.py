"""
test_runner_doer.py - Generic hio Doer for running test queues cooperatively.

This module provides a reusable Doer subclass that runs tests one at a time,
yielding between tests to keep the browser responsive. Tests are injected
via constructor, following hio conventions.
"""

import datetime
from typing import Callable, List, Tuple, Any, Optional

from hio.base.doing import Doer

# Try to import pyscript document, fall back to None for testing
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
    # Escape HTML entities
    msg = str(msg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    output.innerHTML += f'<span class="{css_class}">[{time}] {msg}</span>\n'
    output.scrollTop = output.scrollHeight


class TestResults:
    """Tracks test pass/fail/error counts."""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = 0
        self.failures: List[Tuple[str, str]] = []
        self.error_list: List[Tuple[str, str]] = []
    
    def record_pass(self, name: str):
        self.passed += 1
        log(f"  PASS: {name}", "success")
    
    def record_fail(self, name: str, msg: str):
        self.failed += 1
        self.failures.append((name, msg))
        log(f"  FAIL: {name}", "fail")
        log(f"    AssertionError: {msg}", "fail")
    
    def record_error(self, name: str, msg: str):
        self.errors += 1
        self.error_list.append((name, msg))
        log(f"  ERROR: {name}", "fail")
        log(f"    {msg}", "fail")
    
    def print_summary(self):
        total = self.passed + self.failed + self.errors
        log("=" * 64)
        log("TEST SUMMARY")
        log("=" * 64)
        log(f"Total tests:  {total}")
        log(f"Passed:       {self.passed}", "success" if self.passed else "info")
        log(f"Failed:       {self.failed}", "fail" if self.failed else "info")
        log(f"Errors:       {self.errors}", "fail" if self.errors else "info")

        if self.failures:
            log("-" * 64)
            log("FAILURES:", "fail")
            for name, msg in self.failures:
                log(f"  {name}: {msg}", "fail")

        if self.error_list:
            log("-" * 64)
            log("ERRORS:", "fail")
            for name, msg in self.error_list:
                log(f"  {name}: {msg}", "fail")

        if self.failed == 0 and self.errors == 0:
            log("-" * 64)
            log("ALL TESTS PASSED!", "success")
        else:
            log("-" * 64)
            log("SOME TESTS FAILED - SEE DETAILS ABOVE", "fail")


# Type alias for test queue entries: (name, function, args)
# When function is None, the entry is a section header
TestEntry = Tuple[str, Optional[Callable[..., Any]], Tuple[Any, ...]]


class TestRunnerDoer(Doer):
    """
    Generic Doer that runs tests cooperatively.
    
    Tests are injected via the test_queue parameter, following hio convention
    of injecting work rather than discovering it.
    
    Each call to recur() runs exactly ONE test, then yields control
    back to the hio scheduler (which yields to the browser).
    
    Parameters:
        test_queue: List of (name, func, args) tuples. When func is None,
            the entry is treated as a section header.
        title: Optional title to display at start of test run.
    """
    
    def __init__(self, test_queue: List[TestEntry], title: str = "Test Suite", **kwa):
        super().__init__(**kwa)
        self.test_queue = test_queue
        self.title = title
        self.results = TestResults()
        self.current_index = 0
    
    def enter(self):
        """Called once at start - display header and test count."""
        log("=" * 64)
        log(self.title)
        log("=" * 64)
        log("")
        log(f"Loaded {len(self.test_queue)} tests")
        log("")
    
    def recur(self, tyme):
        """
        Called each scheduling cycle - run ONE test.
        
        Returns:
            True if done (all tests complete)
            False if more tests remain
        """
        if self.current_index >= len(self.test_queue):
            # All tests complete - print summary
            log("")
            self.results.print_summary()
            return True  # Done!
        
        name, func, args = self.test_queue[self.current_index]
        self.current_index += 1
        
        # Section markers (func is None)
        if func is None:
            log("")
            log(name)
            log("-" * 32)
            return False  # Not done
        
        # Run the actual test
        test_name = f"{name}({', '.join(str(a) for a in args)})" if args else name
        try:
            func(*args)
            self.results.record_pass(test_name)
        except AssertionError as e:
            self.results.record_fail(test_name, str(e))
        except Exception as e:
            self.results.record_error(test_name, f"{type(e).__name__}: {e}")
        
        return False  # Not done, more tests to run
