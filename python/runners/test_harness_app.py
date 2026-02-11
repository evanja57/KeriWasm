"""
test_harness_app.py - Architecture POC controller for existing KeriWasm tests.

Goals:
- Client-rendered HTML templates (build run buttons from <template>)
- PyScript state/effects dispatch loop
- Worker offload for heavy operations (liboqs)

This uses existing test runners and does not include wallet functionality.
"""

from __future__ import annotations

import asyncio
import datetime
import html
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import js
from pyodide.ffi import create_proxy
from pyodide.ffi.wrappers import add_event_listener
from pyscript import document

from core import ui_log
from runners import (
    indexeddb_probe,
    package_tests,
    run_blake3_suite,
    run_hio_client_bridge,
    run_indexeddb_suite,
    run_pysodium_suite,
)


@dataclass(frozen=True)
class LogEntry:
    time: str
    css: str
    msg: str
    run_id: Optional[str] = None


@dataclass(frozen=True)
class RunDef:
    run_id: str
    label: str
    detail: str
    starter: Callable[[], Any]


@dataclass(frozen=True)
class AppState:
    busy: bool
    active_run: Optional[str]
    status: str
    tone: str
    logs: Tuple[LogEntry, ...]


Effect = Tuple[str, str]

_LOG_CAP = 4000
_ALLOWED_CSS = {"info", "success", "fail", "loading"}
BUTTONS_BY_ID: Dict[str, Any] = {}

state = AppState(
    busy=False,
    active_run=None,
    status="Ready",
    tone="idle",
    logs=(),
)

_worker_batch_proxy = None
_worker_status_proxy = None
_rendered_log_count = 0


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _normalize_css(css_class: str) -> str:
    return css_class if css_class in _ALLOWED_CSS else "info"


def _start_liboqs_worker() -> None:
    started = bool(js.window.startLiboqsRun())
    if not started:
        raise RuntimeError("Liboqs worker is already running")


def _coerce_mapping(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw

    if hasattr(raw, "to_py"):
        try:
            py_val = raw.to_py()
            if isinstance(py_val, dict):
                return py_val
        except Exception:
            pass

    mapping: Dict[str, Any] = {}
    for key in ("time", "css", "msg", "run_id"):
        if hasattr(raw, key):
            mapping[key] = getattr(raw, key)
    return mapping


def _normalize_log_entry(raw: Any) -> LogEntry:
    data = _coerce_mapping(raw)
    return LogEntry(
        time=str(data.get("time") or _now()),
        css=_normalize_css(str(data.get("css") or "info")),
        msg=html.unescape(str(data.get("msg") or "")),
        run_id=(str(data["run_id"]) if data.get("run_id") is not None else None),
    )


def _append_logs(
    existing: Tuple[LogEntry, ...], new_entries: List[LogEntry]
) -> Tuple[LogEntry, ...]:
    combined = existing + tuple(new_entries)
    if len(combined) > _LOG_CAP:
        return combined[-_LOG_CAP:]
    return combined


RUNS: List[RunDef] = [
    RunDef(
        run_id="pkg",
        label="Package Smoke Tests",
        detail="Run package import/usage checks in PyScript.",
        starter=lambda: package_tests.run_tests(None),
    ),
    RunDef(
        run_id="pysodium",
        label="Full Pysodium Suite",
        detail="Execute browser-adapted unittest suite.",
        starter=lambda: run_pysodium_suite.run_full_suite(None),
    ),
    RunDef(
        run_id="blake3",
        label="Blake3 Suite",
        detail="Use hio scheduler with blake3 test loaders.",
        starter=lambda: run_blake3_suite.run_blake3_suite(None),
    ),
    RunDef(
        run_id="hio-bridge",
        label="Hio HTTP Bridge",
        detail="Exercise hio Requester/Respondent over browser fetch.",
        starter=lambda: run_hio_client_bridge.run_hio_client_bridge(None),
    ),
    RunDef(
        run_id="indexeddb",
        label="IndexedDB Suite",
        detail="Run async IndexedDB backend tests.",
        starter=lambda: run_indexeddb_suite.run_indexeddb_suite(None),
    ),
    RunDef(
        run_id="indexeddb-probe",
        label="IndexedDB Probe",
        detail="Inspect storage.sync awaitable + hio integration.",
        starter=lambda: indexeddb_probe.run_indexeddb_probe(None),
    ),
    RunDef(
        run_id="liboqs-worker",
        label="Liboqs Worker Run",
        detail="Heavy run in dedicated worker (off main thread).",
        starter=lambda: _start_liboqs_worker(),
    ),
]

RUNS_BY_ID = {run.run_id: run for run in RUNS}


def _set_text(el, text: str) -> None:
    if el is None:
        return
    el.textContent = text


def _set_disabled(el, value: bool) -> None:
    if el is None:
        return
    el.disabled = bool(value)


def _status_classes(tone: str) -> str:
    base = "rounded-md px-2 py-1 text-xs font-medium"
    if tone == "busy":
        return f"{base} bg-amber-200 text-amber-900"
    if tone == "error":
        return f"{base} bg-rose-200 text-rose-900"
    return f"{base} bg-emerald-200 text-emerald-900"


def _render_output(current: AppState) -> None:
    global _rendered_log_count

    output = document.getElementById("output")
    if output is None:
        return

    total = len(current.logs)
    if total == 0:
        placeholder = document.createElement("span")
        placeholder.className = "text-slate-500"
        placeholder.textContent = "Click a run button to begin."
        output.replaceChildren(placeholder)
        _rendered_log_count = 0
        return

    if _rendered_log_count == 0 or _rendered_log_count > total:
        output.replaceChildren()
        _rendered_log_count = 0

    for entry in current.logs[_rendered_log_count:]:
        span = document.createElement("span")
        span.className = entry.css
        span.textContent = f"[{entry.time}] {entry.msg}"
        output.appendChild(span)
        output.appendChild(document.createTextNode("\n"))

    _rendered_log_count = total
    output.scrollTop = output.scrollHeight


def _render(current: AppState) -> None:
    badge = document.getElementById("statusBadge")
    _set_text(badge, current.status)
    if badge is not None:
        badge.className = _status_classes(current.tone)

    for run_id, button in BUTTONS_BY_ID.items():
        _set_disabled(button, current.busy)
        if run_id == current.active_run and current.busy:
            button.classList.add("ring", "ring-amber-300")
        else:
            button.classList.remove("ring", "ring-amber-300")

    _render_output(current)


def _reduce(current: AppState, action: dict) -> Tuple[AppState, List[Effect]]:
    action_type = action.get("type")

    if action_type == "RUN_REQUESTED":
        run_id = action["run_id"]
        if current.busy:
            return (
                replace(
                    current,
                    status="Please wait - a run is in progress",
                    tone="error",
                ),
                [],
            )
        run = RUNS_BY_ID[run_id]
        return (
            replace(
                current,
                busy=True,
                active_run=run_id,
                status=f"Starting: {run.label}",
                tone="busy",
                logs=(),
            ),
            [("START_RUN", run_id)],
        )

    if action_type == "RUN_DISPATCHED":
        run_id = action["run_id"]
        run = RUNS_BY_ID[run_id]
        return (
            replace(
                current,
                status=f"Running: {run.label}",
                tone="busy",
            ),
            [],
        )

    if action_type == "RUN_COMPLETED":
        run_id = action["run_id"]
        run = RUNS_BY_ID[run_id]
        return (
            replace(
                current,
                busy=False,
                active_run=None,
                status=f"Completed: {run.label}",
                tone="idle",
            ),
            [],
        )

    if action_type == "RUN_FAILED":
        return (
            replace(
                current,
                busy=False,
                active_run=None,
                status=f"Error: {action['error']}",
                tone="error",
            ),
            [],
        )

    if action_type == "WORKER_STATUS":
        next_busy = bool(action.get("busy", current.busy))
        return (
            replace(
                current,
                busy=next_busy,
                active_run=(current.active_run if next_busy else None),
                status=str(action.get("status") or current.status),
                tone=str(action.get("tone") or current.tone),
            ),
            [],
        )

    if action_type == "LOG_APPEND":
        entry = _normalize_log_entry(action.get("entry") or {})
        return (replace(current, logs=_append_logs(current.logs, [entry])), [])

    if action_type == "LOG_BATCH_APPEND":
        entries = [_normalize_log_entry(raw) for raw in (action.get("entries") or [])]
        return (replace(current, logs=_append_logs(current.logs, entries)), [])

    if action_type == "LOG_CLEAR":
        return (replace(current, logs=()), [])

    if action_type == "OUTPUT_CLEARED":
        return (
            replace(current, logs=(), status="Output cleared", tone="idle"),
            [],
        )

    return current, []


def _on_async_run_done(run_id: str, task: asyncio.Future) -> None:
    try:
        task.result()
    except Exception as exc:
        dispatch({"type": "LOG_APPEND", "entry": {"css": "fail", "msg": str(exc)}})
        dispatch({"type": "RUN_FAILED", "error": str(exc)})
        return
    dispatch({"type": "RUN_COMPLETED", "run_id": run_id})


async def _run_effect(effect: Effect) -> None:
    kind, payload = effect
    if kind != "START_RUN":
        return

    try:
        run = RUNS_BY_ID[payload]
        if payload == "liboqs-worker":
            dispatch(
                {
                    "type": "LOG_APPEND",
                    "entry": {"css": "loading", "msg": "Starting liboqs worker..."},
                }
            )
        result = run.starter()
        # Yield once so status updates paint before potential heavy follow-on work.
        await asyncio.sleep(0)
        if payload == "liboqs-worker":
            dispatch({"type": "RUN_DISPATCHED", "run_id": payload})
            return

        if asyncio.iscoroutine(result):
            result = asyncio.ensure_future(result)

        if isinstance(result, asyncio.Future):
            dispatch({"type": "RUN_DISPATCHED", "run_id": payload})
            result.add_done_callback(
                lambda task, run_id=payload: _on_async_run_done(run_id, task)
            )
            return

        dispatch({"type": "RUN_COMPLETED", "run_id": payload})
    except Exception as exc:
        dispatch({"type": "LOG_APPEND", "entry": {"css": "fail", "msg": str(exc)}})
        dispatch({"type": "RUN_FAILED", "error": str(exc)})


def dispatch(action: dict) -> None:
    global state
    state, effects = _reduce(state, action)
    _render(state)
    for effect in effects:
        asyncio.ensure_future(_run_effect(effect))


def _clear_output(_event=None) -> None:
    dispatch({"type": "OUTPUT_CLEARED"})


def _on_run_click(run_id: str):
    def _handler(_event=None):
        dispatch({"type": "RUN_REQUESTED", "run_id": run_id})

    return _handler


def _render_run_cards() -> None:
    grid = document.getElementById("runGrid")
    template = document.getElementById("run-card-template")

    if grid is None or template is None:
        raise RuntimeError("Missing runGrid or run-card-template in page")

    for run in RUNS:
        fragment = template.content.cloneNode(True)
        button = fragment.querySelector("button")
        label = fragment.querySelector(".run-label")
        detail = fragment.querySelector(".run-detail")

        _set_text(label, run.label)
        _set_text(detail, run.detail)

        add_event_listener(button, "click", _on_run_click(run.run_id))
        BUTTONS_BY_ID[run.run_id] = button
        grid.appendChild(fragment)


def _handle_worker_batch(entries) -> None:
    py_entries = entries
    if hasattr(entries, "to_py"):
        try:
            py_entries = entries.to_py()
        except Exception:
            py_entries = entries

    if py_entries is None:
        return

    try:
        entries_list = list(py_entries)
    except TypeError:
        entries_list = [py_entries]

    dispatch({"type": "LOG_BATCH_APPEND", "entries": entries_list})


def _handle_worker_status(state_text, error_text="") -> None:
    state_str = str(state_text or "").lower()
    err = str(error_text or "")

    if state_str in {"starting", "running"}:
        dispatch(
            {
                "type": "WORKER_STATUS",
                "status": f"Worker {state_str}...",
                "tone": "busy",
                "busy": True,
            }
        )
        return

    if state_str == "done":
        dispatch(
            {
                "type": "WORKER_STATUS",
                "status": "Worker completed",
                "tone": "idle",
                "busy": False,
            }
        )
        return

    if state_str == "busy":
        dispatch(
            {
                "type": "WORKER_STATUS",
                "status": "Worker already busy",
                "tone": "error",
                "busy": False,
            }
        )
        return

    if state_str == "error":
        message = err or "Liboqs worker failed"
        dispatch({"type": "LOG_APPEND", "entry": {"css": "fail", "msg": message}})
        dispatch({"type": "RUN_FAILED", "error": message})


def _handle_ui_log_entry(entry: Dict[str, Any]) -> None:
    dispatch({"type": "LOG_APPEND", "entry": entry})


def _handle_ui_log_clear() -> None:
    dispatch({"type": "LOG_CLEAR"})


def _register_worker_callbacks() -> None:
    global _worker_batch_proxy, _worker_status_proxy

    _worker_batch_proxy = create_proxy(_handle_worker_batch)
    _worker_status_proxy = create_proxy(_handle_worker_status)

    js.window.__keriwasmOnWorkerBatch = _worker_batch_proxy
    js.window.__keriwasmOnWorkerStatus = _worker_status_proxy


def _boot() -> None:
    ui_log.set_sinks(entry_sink=_handle_ui_log_entry, clear_sink=_handle_ui_log_clear)
    _register_worker_callbacks()
    _render_run_cards()
    clear_btn = document.getElementById("clearOutputBtn")
    add_event_listener(clear_btn, "click", _clear_output)
    _render(state)


_boot()
