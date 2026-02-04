"""
run_hio_client_bridge.py - JS fetch bridge using hio HTTP parsing.

Prototype: build an HTTP request with hio's Requester, execute via JS fetch,
reconstruct a raw HTTP response, and parse it with hio's Respondent.
"""

import asyncio
import datetime

import js  # type: ignore
from pyscript import document, fetch

from hio_http_client_bridge import Requester, Respondent


def log(msg: str, css_class: str = "info") -> None:
    output = document.querySelector("#output")
    if output is None:
        print(msg)
        return
    time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    msg = str(msg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    output.innerHTML += f'<span class="{css_class}">[{time}] {msg}</span>\n'
    output.scrollTop = output.scrollHeight


def clear_output() -> None:
    output = document.querySelector("#output")
    if output:
        output.innerHTML = ""


async def _run_hio_client_bridge_async() -> None:
    clear_output()
    log("Starting hio HTTP client JS-bridge prototype...")

    location = js.window.location
    origin = location.origin
    url = f"{origin}/index.html"
    hostname = str(location.hostname)
    port = str(location.port)
    scheme = str(location.protocol).replace(":", "")
    if not port:
        port = "443" if scheme == "https" else "80"

    # Build request using hio Requester
    requester = Requester(method="GET", path="/index.html", hostname=hostname, port=int(port), scheme=scheme)
    request_bytes = requester.rebuild()
    log("Built request via hio Requester:")
    for line in requester.lines[:5]:
        log(f"  {line.decode('iso-8859-1')}")
    if len(requester.lines) > 5:
        log("  ...")

    # Execute via JS fetch (no raw sockets in WASM)
    try:
        resp = await fetch(url)
    except Exception as exc:
        log(f"Fetch failed: {exc}", "fail")
        return

    status = int(resp.status)
    status_text = resp.statusText or "OK"

    buf = await resp.arrayBuffer()
    body = bytes(js.Uint8Array.new(buf).to_py())

    # Collect headers and normalize for parsing
    header_items = []
    for entry in resp.headers.entries():
        name = str(entry[0])
        value = str(entry[1])
        header_items.append((name, value))

    header_lines = []
    for name, value in header_items:
        lname = name.lower()
        if lname in ("transfer-encoding", "content-length"):
            continue
        header_lines.append(f"{name}: {value}\r\n")
    header_lines.append(f"Content-Length: {len(body)}\r\n")

    raw_head = f"HTTP/1.1 {status} {status_text}\r\n" + "".join(header_lines) + "\r\n"
    raw = raw_head.encode("iso-8859-1") + body

    # Parse with hio Respondent
    respondent = Respondent(msg=bytearray(raw), method=requester.method)
    while respondent.parser:
        respondent.parse()
    respondent.dictify()

    log("")
    log(f"Parsed response: {respondent.status} {respondent.reason}", "success")
    log(f"Parsed headers: {len(respondent.headers)}")

    preview = ""
    try:
        preview = respondent.body[:200].decode("utf-8", errors="replace")
    except Exception:
        preview = str(respondent.body[:200])
    log("Body preview (first 200 chars):")
    log(preview)

    log("")
    log("Prototype complete.", "info")


def run_hio_client_bridge(event):
    asyncio.ensure_future(_run_hio_client_bridge_async())
