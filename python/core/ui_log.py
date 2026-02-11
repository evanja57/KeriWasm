"""
ui_log.py - shared UI logging sink for browser and non-browser runs.

Runners emit structured log entries through this module instead of writing
straight to the DOM. A controller (e.g. test_harness_app) can register custom
entry/clear sinks for fully state-driven rendering.
"""

from __future__ import annotations

import datetime
import html
from typing import Any, Callable, Dict, Iterable, Optional

# Optional browser document bridge for legacy pages.
try:
    from pyscript import document
except ImportError:  # pragma: no cover - non-browser usage
    document = None


LogEntry = Dict[str, Any]
EntrySink = Callable[[LogEntry], None]
ClearSink = Callable[[], None]

_ALLOWED_CSS = {"info", "success", "fail", "loading"}
_entry_sink: Optional[EntrySink] = None
_clear_sink: Optional[ClearSink] = None


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _normalize_css(css_class: str) -> str:
    return css_class if css_class in _ALLOWED_CSS else "info"


def _normalize_entry(entry: LogEntry) -> LogEntry:
    return {
        "time": str(entry.get("time") or _now()),
        "css": _normalize_css(str(entry.get("css") or "info")),
        "msg": str(entry.get("msg") or ""),
        "run_id": entry.get("run_id"),
    }


def set_sinks(
    entry_sink: Optional[EntrySink] = None, clear_sink: Optional[ClearSink] = None
) -> None:
    """Register sinks for app-level state-driven rendering."""
    global _entry_sink, _clear_sink
    _entry_sink = entry_sink
    _clear_sink = clear_sink


def clear_sinks() -> None:
    """Remove registered sinks and fall back to legacy/default behavior."""
    global _entry_sink, _clear_sink
    _entry_sink = None
    _clear_sink = None


def emit(
    msg: Any,
    css_class: str = "info",
    *,
    time: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    entry = _normalize_entry(
        {"time": time, "css": css_class, "msg": msg, "run_id": run_id}
    )
    if _entry_sink is not None:
        _entry_sink(entry)
        return
    _legacy_emit(entry)


def emit_batch(entries: Iterable[LogEntry]) -> None:
    for entry in entries:
        normalized = _normalize_entry(entry)
        if _entry_sink is not None:
            _entry_sink(normalized)
        else:
            _legacy_emit(normalized)


def clear() -> None:
    if _clear_sink is not None:
        _clear_sink()
        return
    _legacy_clear()


def _legacy_emit(entry: LogEntry) -> None:
    """Legacy fallback for pages that still rely on #output direct append."""
    msg = entry["msg"]
    if document is None:
        print(msg)
        return

    output = document.querySelector("#output")
    if output is None:
        print(msg)
        return

    line = html.escape(f"[{entry['time']}] {msg}")
    css = entry["css"]
    output.innerHTML += f'<span class="{css}">{line}</span>\n'
    output.scrollTop = output.scrollHeight


def _legacy_clear() -> None:
    if document is None:
        return
    output = document.querySelector("#output")
    if output is not None:
        output.innerHTML = ""
