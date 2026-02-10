# -*- encoding: utf-8 -*-
"""
run_indexeddb_suite.py - PyScript entry point for IndexedDB tests.

This script runs the IndexedDB test suite in a browser environment using
hio's cooperative scheduling.

Usage in PyScript:
    Add to pyscript.toml:
        [[fetch]]
        files = ["./python/run_indexeddb_suite.py"]
    
    Then call from JavaScript or inline Python:
        await run_indexeddb_tests()
"""

import asyncio
import datetime
import sys
from hio.base.doing import Doist

try:
    from pyscript import document
except ImportError:
    document = None

# Import test module without polluting globals (avoid name collisions)
import test_indexeddb


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def log(msg: str, css_class: str = "info"):
    """Append a message to the output div."""
    if document is None:
        target = getattr(sys, "__stdout__", None)
        if target is not None:
            target.write(f"{msg}\n")
            target.flush()
        else:
            print(msg)
        return
    output = document.querySelector("#output")
    if output is None:
        target = getattr(sys, "__stdout__", None)
        if target is not None:
            target.write(f"{msg}\n")
            target.flush()
        else:
            print(msg)
        return
    time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    output.innerHTML += f'<span class="{css_class}">[{time}] {_escape(str(msg))}</span>\n'
    output.scrollTop = output.scrollHeight


def clear_output():
    """Clear the output div."""
    if document is None:
        return
    output = document.querySelector("#output")
    if output:
        output.innerHTML = ""


class OutputRedirector:
    """File-like writer that redirects output to the UI."""

    def __init__(self, css_class: str = "info"):
        self.css_class = css_class
        self._buffer = ""
        self.encoding = "utf-8"

    def _class_for_line(self, line: str) -> str:
        upper = line.upper()
        if "FAIL" in upper or "ERROR" in upper:
            return "fail"
        if "PASS" in upper:
            return "success"
        return self.css_class

    def write(self, data):
        if data is None:
            return
        if isinstance(data, bytes):
            data = data.decode(self.encoding, errors="replace")
        self._buffer += str(data)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            log(line, self._class_for_line(line))

    def flush(self):
        if self._buffer:
            log(self._buffer, self._class_for_line(self._buffer))
            self._buffer = ""

    def isatty(self):
        return False


async def run_and_display():
    """Run tests and display results."""
    print("Starting IndexedDB test suite...")
    print()
    
    results = await test_indexeddb.run_all_tests()
    
    print()
    print("Test run complete.")
    
    return results


def run_indexeddb_tests():
    """
    Entry point for running IndexedDB tests from PyScript.
    
    Returns a coroutine that should be awaited.
    
    Example:
        results = await run_indexeddb_tests()
    """
    return run_and_display()


async def _run_indexeddb_suite_async():
    """Async wrapper that awaits the IndexedDB test suite."""
    clear_output()
    stdout = sys.stdout
    stderr = sys.stderr
    sys.stdout = OutputRedirector(css_class="info")
    sys.stderr = OutputRedirector(css_class="fail")
    try:
        await run_indexeddb_tests()
    except Exception as exc:
        log(f"Test suite failed with exception: {exc}", "fail")
        import traceback
        log(traceback.format_exc(), "fail")
    finally:
        sys.stdout = stdout
        sys.stderr = stderr


def run_indexeddb_suite(event=None):
    """
    Button click handler - schedules the async test runner.
    """
    asyncio.ensure_future(_run_indexeddb_suite_async())


# For Doist-style execution (cooperative with hio)
class IndexedDBTestDoer:
    """
    Doer-style wrapper for running IndexedDB tests with hio.
    
    This allows the tests to run cooperatively alongside other Doers.
    """
    
    def __init__(self):
        self.done = False
        self.results = None
        self._task = None
    
    def enter(self):
        """Start the async test execution."""
        print("IndexedDB Test Doer starting...")
        self._task = asyncio.ensure_future(test_indexeddb.run_all_tests())
    
    def recur(self, tyme):
        """Check if tests are complete."""
        if self._task is None:
            return True
        
        if self._task.done():
            try:
                self.results = self._task.result()
            except Exception as e:
                print(f"Test suite error: {e}")
            self.done = True
            return True
        
        return False
    
    def exit(self):
        """Cleanup."""
        pass


# Direct execution
if __name__ == "__main__":
    print("=" * 64)
    print("IndexedDB Backend Test Suite")
    print("=" * 64)
    print()
    print("This script must be run in a browser environment with PyScript.")
    print()
    print("To run tests, execute in browser console:")
    print("  await run_indexeddb_tests()")
    print()
