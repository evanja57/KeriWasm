"""Minimal PyScript smoke marker for CI."""

from pyscript import document


def _set_smoke_summary() -> None:
    output = document.querySelector("#output")
    if output is None:
        return

    output.textContent = "PASS: ci smoke page loaded\nSUMMARY: 1 passed, 0 failed"


_set_smoke_summary()
