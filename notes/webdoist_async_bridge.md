Call path
-----------------

The clearest browser example is KeriWasm/python/runners/indexeddb_probe.py.
This is where I import the bridge, define an async-backed doer, construct the
WebDoist, and call it.

What I did here was move the real browser async work into `recur_async()`, then
run it in task + poll mode. The first pass creates an asyncio task for the
coroutine. Later passes do not await anything directly; they just check whether
that task has finished yet. When it finishes, its result becomes the completion
signal for the doer.

Where I use `asyncio.ensure_future(...)` elsewhere in KeriWasm, it just means
"schedule this coroutine on the event loop now and give me back a task handle
without waiting for it to finish."

KeriWasm/python/runners/indexeddb_probe.py

```python
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
```

That call lands in KeriWasm/python/core/hio_bridge.py, where WebDoist wraps a
real hio Doist and only makes the outer loop async for Pyodide:

KeriWasm/python/core/hio_bridge.py

```python
class WebDoist:
    def __init__(self, real=True, limit=None, doers=None, tock=0.03125):
        from hio.base import doing

        self.doist = doing.Doist(real=False, doers=doers, tock=tock, limit=limit)
        self.real = real
        self.limit = limit
        self.tock = tock
        self._running = False
        self._stop_requested = False

    async def do(self, doers=None, limit=None, tyme=None):
        self._running = True
        self._stop_requested = False

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
            self.doist.enter()

            while self.doist.deeds and not self._stop_requested:
                self.doist.recur()

                if self.real:
                    await asyncio.sleep(self.tock)
                else:
                    await asyncio.sleep(0)

                if self.limit is not None:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= self.limit:
                        break

            self.doist.done = True

        except Exception:
            self.doist.done = False
            raise

        finally:
            self.doist.exit()
            self._running = False
```


AsyncRecurDoer pattern
----------------------

The async bridge itself is KeriWasm/python/core/hio_bridge.py. The important
part is that `recur()` itself never awaits. It creates an asyncio task once,
then later polls that task until completion.

KeriWasm/python/core/hio_bridge.py

```python
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
```

The short version is:

- I put the real async browser operation inside `recur_async()`.
- The first `recur()` call starts that coroutine as a task.
- Later `recur()` calls only poll `task.done()`.
- When the task completes, `task.result()` is used as the doer completion
  signal.
- `WebDoist.do()` yields with `asyncio.sleep(...)` so the task can keep making
  progress on the event loop between polls.
