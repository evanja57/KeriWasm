"""
indexeddb_probe.py - PyScript IndexedDB probe + hio ReDoer experiment.

This script:
1) Introspects the awaitable returned by store.sync()
2) Tries yield-from inside a hio ReDoer to see if it can drive sync()
"""

import asyncio
import datetime
import inspect
import sys

try:
    from pyscript import storage
except ImportError:  # allow local/non-PyScript runs
    storage = None

from core import ui_log


def log(msg: str, css_class: str = "info"):
    """Emit a structured log entry."""
    ui_log.emit(msg, css_class)


def clear_output():
    """Clear the active output sink."""
    ui_log.clear()


def _safe_repr(obj) -> str:
    try:
        return repr(obj)
    except Exception as e:
        return f"<repr failed: {type(e).__name__}: {e}>"


async def _run_indexeddb_probe_async():
    clear_output()
    log("IndexedDB probe starting...")
    log(f"Python: {sys.version}")
    log(f"Platform: {sys.platform}")
    log(f"Asyncio loop: {asyncio.get_event_loop().__class__.__name__}")
    log("")

    if storage is None:
        log("pyscript.storage not available in this environment", "fail")
        return

    try:
        store = await storage("keriwasm-cache")
        log("Opened storage: keriwasm-cache", "success")
    except Exception as e:
        log(f"Failed to open storage: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
        return

    # Basic read/write sanity
    try:
        probe_value = f"probe-{datetime.datetime.now().isoformat()}"
        store["probe-key"] = probe_value
        read_back = store.get("probe-key")
        log(f"Wrote probe-key: {probe_value}")
        log(f"Read probe-key: {read_back}")
    except Exception as e:
        log(f"Basic store read/write failed: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")

    # Introspect store.sync
    sync_attr = getattr(store, "sync", None)
    log("")
    log("store.sync introspection:")
    log(f"store.sync: {_safe_repr(sync_attr)}")
    try:
        log(
            f"inspect.iscoroutinefunction(store.sync): {inspect.iscoroutinefunction(sync_attr)}"
        )
    except Exception as e:
        log(f"inspect.iscoroutinefunction failed: {e}", "fail")
    try:
        log(
            f"inspect.isasyncgenfunction(store.sync): {inspect.isasyncgenfunction(sync_attr)}"
        )
    except Exception as e:
        log(f"inspect.isasyncgenfunction failed: {e}", "fail")
    try:
        log(
            f"inspect.isgeneratorfunction(store.sync): {inspect.isgeneratorfunction(sync_attr)}"
        )
    except Exception as e:
        log(f"inspect.isgeneratorfunction failed: {e}", "fail")

    if sync_attr is None:
        log("store.sync missing; cannot continue", "fail")
        return

    sync_result = None
    try:
        sync_result = sync_attr()
        log(f"type(store.sync()): {type(sync_result)}")
        log(f"inspect.iscoroutine(store.sync()): {inspect.iscoroutine(sync_result)}")
        log(f"inspect.isawaitable(store.sync()): {inspect.isawaitable(sync_result)}")
        if hasattr(sync_result, "__await__"):
            try:
                await_iter = sync_result.__await__()
                log(f"type(store.sync().__await__()): {type(await_iter)}")
                log(
                    f"inspect.isgenerator(store.sync().__await__()): {inspect.isgenerator(await_iter)}"
                )
            except Exception as e:
                log(f"store.sync().__await__() failed: {e}", "fail")
    except Exception as e:
        log(f"Calling store.sync() failed: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")

    # Baseline await to make sure sync works outside hio
    if sync_result is not None:
        try:
            await sync_result
            log("await store.sync() completed", "success")
        except Exception as e:
            log(f"await store.sync() failed: {e}", "fail")
            import traceback

            log(traceback.format_exc(), "fail")

    # hio AsyncRecurDoer experiment (task + poll)
    log("")
    log("hio AsyncRecurDoer experiment:")
    try:
        from core.hio_bridge import WebDoist, AsyncRecurDoer
    except Exception as e:
        log(f"Failed to import hio components: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")
        return

    class SyncAsyncDoer(AsyncRecurDoer):
        def __init__(self, store, **kwa):
            super().__init__(**kwa)
            self.store = store

        async def recur_async(self):
            try:
                probe_value = f"async-probe-{datetime.datetime.now().isoformat()}"
                self.store["async-probe-key"] = probe_value
                log(f"[AsyncRecur] wrote async-probe-key: {probe_value}")

                await self.store.sync()
                log("[AsyncRecur] sync after write completed", "success")

                read_back = self.store.get("async-probe-key")
                log(f"[AsyncRecur] read async-probe-key: {read_back}")

                del self.store["async-probe-key"]
                log("[AsyncRecur] deleted async-probe-key")

                await self.store.sync()
                log("[AsyncRecur] sync after delete completed", "success")

                # Failure-mode test: attempt to store a non-serializable value
                try:
                    self.store["async-fail-key"] = {"bad": set([1, 2, 3])}
                    await self.store.sync()
                    log(
                        "[AsyncRecur] unexpected success storing set() (should fail)",
                        "fail",
                    )
                    del self.store["async-fail-key"]
                except Exception as e:
                    log(f"[AsyncRecur] expected failure storing set(): {e}", "success")
                return True
            except Exception as e:
                log(f"[AsyncRecur] error: {e}", "fail")
                import traceback

                log(traceback.format_exc(), "fail")
                raise

    log("Running AsyncRecurDoer mode=task...")
    async_doer = SyncAsyncDoer(store=store, tock=0.0)
    web_doist = WebDoist(doers=[async_doer], tock=0.01, real=True, limit=5.0)
    try:
        await web_doist.do()
        log("AsyncRecurDoer completed", "success")
    except Exception as e:
        log(f"AsyncRecurDoer failed: {e}", "fail")
        import traceback

        log(traceback.format_exc(), "fail")


def run_indexeddb_probe(event=None):
    """
    Button click handler - schedules the async probe.
    """
    return asyncio.ensure_future(_run_indexeddb_probe_async())
