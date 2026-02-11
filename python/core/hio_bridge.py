"""
hio_bridge.py - Browser-compatible hio integration for Pyodide

This module provides a WebDoist class that wraps hio's Doist scheduler
and integrates with the JavaScript event loop via Pyodide's webloop.
"""

import asyncio
import inspect

try:
    from hio.base import doing
except Exception:  # allow import without hio installed
    doing = None


class WebDoist:
    """
    Browser-compatible wrapper around hio.Doist.

    Uses asyncio.sleep() instead of time.sleep() to yield to the
    JavaScript event loop between scheduling cycles.
    """

    def __init__(self, real=True, limit=None, doers=None, tock=0.03125):
        """
        Initialize WebDoist.

        Args:
            real: If True, run in real-time mode with delays between cycles.
                  If False, run as fast as possible (still yields to JS).
            limit: Maximum run time in seconds. None means no limit.
            doers: List of Doer instances to schedule.
            tock: Time increment per cycle in seconds (default 1/32 second).
        """
        # Import hio here to allow module to load even if hio isn't installed yet
        from hio.base import doing

        # Create inner Doist with real=False (we handle timing ourselves)
        self.doist = doing.Doist(real=False, doers=doers, tock=tock, limit=limit)
        self.real = real
        self.limit = limit
        self.tock = tock
        self._running = False
        self._stop_requested = False

    async def do(self, doers=None, limit=None, tyme=None):
        """
        Async version of Doist.do() that yields to JS event loop.

        This runs the scheduler loop, calling recur() on each cycle,
        and using asyncio.sleep() to yield control back to the browser.

        Args:
            doers: Optional list of doers (updates self.doist.doers if provided)
            limit: Optional time limit override
            tyme: Optional starting tyme override
        """
        self._running = True
        self._stop_requested = False

        # Update parameters if provided
        if doers is not None:
            self.doist.doers = list(doers)
            self.doist.deeds.clear()

        if limit is not None:
            self.limit = limit
            self.doist.limit = limit

        if tyme is not None:
            self.doist.tyme = tyme

        start_time = asyncio.get_event_loop().time()

        try:
            # Enter context - prepares all doers
            self.doist.enter()

            while self.doist.deeds and not self._stop_requested:
                # Run one scheduling cycle
                self.doist.recur()

                if self.real:
                    # Yield to JS event loop for one tock duration
                    await asyncio.sleep(self.tock)
                else:
                    # Still yield briefly to prevent blocking
                    await asyncio.sleep(0)

                # Check time limit
                if self.limit is not None:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= self.limit:
                        break

            self.doist.done = True

        except Exception:
            self.doist.done = False
            raise

        finally:
            # Exit context - cleanup all doers
            self.doist.exit()
            self._running = False

    def stop(self):
        """Request the scheduler to stop after the current cycle."""
        self._stop_requested = True

    @property
    def running(self):
        """Check if the scheduler is currently running."""
        return self._running

    @property
    def tyme(self):
        """Current scheduler time."""
        return self.doist.tyme

    @property
    def done(self):
        """Completion status of the scheduler."""
        return self.doist.done


async def test_hio():
    """
    Test function to verify hio integration works.

    Creates a simple counting Doer and runs it with WebDoist.
    Returns the final count.
    """
    from hio.base.doing import Doer

    class CounterDoer(Doer):
        """Simple Doer that counts to a target value."""

        def __init__(self, target=5, **kwa):
            super().__init__(**kwa)
            self.target = target
            self.count = 0

        def recur(self, tyme):
            """Called each scheduling cycle."""
            self.count += 1
            print(f"[CounterDoer] count={self.count} tyme={tyme:.3f}")

            if self.count >= self.target:
                return True  # Done
            return False  # Continue

    # Create and run the test
    doer = CounterDoer(target=5, tock=0.1)
    web_doist = WebDoist(doers=[doer], tock=0.1, real=True, limit=10.0)

    print("[test_hio] Starting WebDoist...")
    await web_doist.do()
    print(f"[test_hio] Complete! Final count: {doer.count}")

    return {"count": doer.count, "done": web_doist.done, "success": doer.count >= 5}


# Export for easy access
if doing is not None:

    class AsyncRecurDoer(doing.Doer):
        """
        Adapter for async recur semantics in hio.

        Subclass this and implement `async def recur_async(self): ...`.
        The sync `recur` method schedules the coroutine and polls for completion.
        """

        def __init__(self, **kwa):
            super().__init__(**kwa)
            self._async_task = None
            self._async_result = None

        async def recur_async(self):
            """Override in subclasses. Return truthy when done."""
            return True

        def recur(self, tyme):
            if self._async_task is None:
                if not inspect.iscoroutinefunction(self.recur_async):
                    raise TypeError(
                        "recur_async must be an async def coroutine function"
                    )
                loop = asyncio.get_event_loop()
                self._async_task = loop.create_task(self.recur_async())
                return False

            if not self._async_task.done():
                return False

            self._async_result = self._async_task.result()
            return bool(self._async_result)

        def close(self):
            if self._async_task and not self._async_task.done():
                self._async_task.cancel()
            super().close()
else:

    class AsyncRecurDoer:
        def __init__(self, **kwa):
            raise ImportError("hio is required to use AsyncRecurDoer")


__all__ = ["WebDoist", "AsyncRecurDoer", "test_hio", "test_hio_crypto_roundtrip"]


async def test_hio_crypto_roundtrip(message="Hello KERI!"):
    """
    Combined hio + cryptography test.
    Uses hio scheduler to orchestrate libsodium calls instead of async/await.

    Uses a single CryptoDoer that handles both JS bridge and business logic.
    """
    import js
    import uuid
    from hio.base.doing import Doer

    class CryptoDoer(Doer):
        """
        Single Doer that sends crypto requests to JS and polls for results.
        Performs hash → sign → verify in sequence.
        """

        def __init__(self, message, **kwa):
            super().__init__(**kwa)
            self.message = message
            self.step = "hash"  # hash -> sign -> verify -> done
            self.pending_id = None
            self.results = {
                "message": message,
                "hash": None,
                "signature": None,
                "publicKey": None,
                "verified": None,
            }
            self.done_flag = False
            self.error = None

        def _send_request(self, op, data):
            """Send a request to JS and return the request ID."""
            req_id = str(uuid.uuid4())
            js.sodium_call(req_id, op, data)
            return req_id

        def _check_result(self):
            """Check if result is ready. Returns result dict or None."""
            if not self.pending_id:
                return None
            results_map = js.sodium_results
            if results_map.has(self.pending_id):
                res = results_map.get(self.pending_id)
                py_res = res.to_py()
                results_map.delete(self.pending_id)
                self.pending_id = None
                return py_res
            return None

        def recur(self, tyme):
            """Called each scheduling cycle."""

            # Start hash request
            if self.step == "hash" and not self.pending_id:
                print(f"[CryptoDoer] Hashing: '{self.message}'")
                self.pending_id = self._send_request("hash", {"message": self.message})

            # Check for hash result
            elif self.step == "hash" and self.pending_id:
                result = self._check_result()
                if result:
                    if "error" in result:
                        self.error = result["error"]
                        self.done_flag = True
                        return True
                    self.results["hash"] = result["hash"]
                    print(f"[CryptoDoer] Hash: {result['hash'][:16]}...")
                    self.step = "sign"

            # Start sign request
            elif self.step == "sign" and not self.pending_id:
                print("[CryptoDoer] Signing...")
                self.pending_id = self._send_request("sign", {"message": self.message})

            # Check for sign result
            elif self.step == "sign" and self.pending_id:
                result = self._check_result()
                if result:
                    if "error" in result:
                        self.error = result["error"]
                        self.done_flag = True
                        return True
                    self.results["signature"] = result["signature"]
                    self.results["publicKey"] = result["publicKey"]
                    print(f"[CryptoDoer] Signature: {result['signature'][:16]}...")
                    self.step = "verify"

            # Start verify request
            elif self.step == "verify" and not self.pending_id:
                print("[CryptoDoer] Verifying...")
                self.pending_id = self._send_request(
                    "verify",
                    {
                        "message": self.message,
                        "signature": self.results["signature"],
                        "publicKey": self.results["publicKey"],
                    },
                )

            # Check for verify result
            elif self.step == "verify" and self.pending_id:
                result = self._check_result()
                if result:
                    if "error" in result:
                        self.error = result["error"]
                        self.done_flag = True
                        return True
                    self.results["verified"] = result["valid"]
                    print(f"[CryptoDoer] Verified: {result['valid']}")
                    self.done_flag = True
                    return True

            return self.done_flag

    # Create and run single doer
    crypto_doer = CryptoDoer(message, tock=0.05)
    web_doist = WebDoist(doers=[crypto_doer], tock=0.05, real=True, limit=10.0)

    print(f"[test_hio_crypto_roundtrip] Starting with message: '{message}'")
    await web_doist.do()

    if crypto_doer.error:
        print(f"[test_hio_crypto_roundtrip] Error: {crypto_doer.error}")
        return {"error": crypto_doer.error}

    print("[test_hio_crypto_roundtrip] Complete!")
    return crypto_doer.results
