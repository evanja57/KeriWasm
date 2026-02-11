# KeriWasm

> Quick demonstration of PyScript and Pyodide

## Quick start

```bash
python serve.py
```

Then open `http://localhost:8000` in your browser.

## CI/CD (GitHub Actions)

This repo now includes a baseline workflow at `.github/workflows/keriwasm-ci.yml` with:

- `quality-gates` job
  - compiles Python modules (`python -m compileall -q python`)
  - verifies runtime layout and PyScript mappings (`python ci/check_runtime_layout.py`)
- `browser-wasm-smoke` job
  - serves the app locally
  - runs a headless Chromium smoke test via Playwright (`python ci/smoke_browser.py`)
  - checks `/index.html` package smoke output summary
  - checks `/pages/test-harness.html` renders run controls

Optional manual self-hosted smoke workflow:

- `.github/workflows/keriwasm-self-hosted-smoke.yml`
- runs the same browser smoke checks on a runner labeled:
  - `[self-hosted, linux, wasm, keriwasm]`

To reproduce key checks locally:

```bash
python -m compileall -q python
python ci/check_runtime_layout.py
```

Optional self-hosted runner migration:

- Keep `quality-gates` on `ubuntu-latest`
- Switch the browser job `runs-on` to labels like:
  - `[self-hosted, linux, wasm, keriwasm]`
- Register your runner in GitHub with matching labels.


## PyScript

> pyscript is a wasm framework for running python in the browser

## Pyodide

> pyodide is a wasm runtime for python, essentially thecpython interpreter compiled to wasm

## How PyScript and Pyodide connect

> pyscript provides a framework for running python in the browser, pyodide provides the runtime

### Directory map (brief)

```text
KeriWasm/
├─ index.html
├─ pyscript.toml
├─ python/
├─ static/
├─ workers/
└─ serve.py
```

- `index.html` — entry point for the app
- `pyscript.toml` — config for pyscript
- `serve.py` — simple http server so that I can test and see it in the browser on localhost
- `python/` — python code that is loaded and run by pyscript
- `static/` — This is where I am keeping the python wheels I have created
    - blake3
    - liboqs
    - pysodium
- `workers/` — web workers used for long-running browser tasks (liboqs runs here)

### Key files to highlight

- `index.html` — entry point for the app
- `pyscript.toml` — config for pyscript
- `python/hio/` — minimal hio scheduler package trimmed for Pyodide
- `python/core/hio_bridge.py` — port of hio to run on the web (yields to the browser event loop)
- `python/core/test_runner_doer.py` — test runner as a hio Doer that executes a queued list of tests
- `python/core/test_loaders.py` — functions that build test queues from liboqs/blake3 test modules
- `python/runners/run_liboqs_suite.py` — PyScript entry point that wires the hio runner to the liboqs tests
    - the following are lifted from pyoqs:
    - `python/tests/liboqs/test_kem.py` — liboqs KEM correctness and edge-case tests
    - `python/tests/liboqs/test_sig.py` — liboqs signature correctness tests (including context-string support)
    - `python/tests/liboqs/test_stfl_sig.py` — stateful signature (XMSS/XMSSMT) tests filtered for browser-feasible algorithms
- `python/runners/run_pysodium_suite.py` — browser-friendly runner for the full pysodium unittest suite
- `python/tests/pysodium/pysodium_unittest.py` — from libsodium library, the unittest definitions used by the pysodium runner
- `python/runners/package_tests.py` — smoke tests to verify key packages import and run in PyScript


## How the Python runs in the browser

### 1. Loading Phase
- **Entry Point**: The browser loads `index.html`, served with serve.py
- **Framework**: `index.html` loads the PyScript core with `<script type="module">`(`core.js`), which bootstraps the Pyodide runtime (hosting Python in WebAssembly).
- **Configuration**: PyScript reads `pyscript.toml` to determine which Python packages to install (including local wheels from `static/`) and which files to mount into the virtual filesystem. This allows us to call the python functions from the browser using relative paths in the code as we would when running the code in a local python environment.

### 2. Execution Environment
- **Runtime**: Pyodide runs partially on the main thread (blocking for heavy compute unless using workers).
- **Scripts**: The `<script type="py">` tags in `index.html` execute the following Python initialization scripts:
  - `python/runners/package_tests.py`: Defines the `run_tests()` function.
  - `python/runners/run_pysodium_suite.py`: Defines the `run_full_suite()` function.
  - `python/runners/run_blake3_suite.py`: Defines the `run_blake3_suite()` function.
  - `python/runners/run_hio_client_bridge.py`: Defines the `run_hio_client_bridge()` function.
  - `python/runners/run_indexeddb_suite.py`: Defines the `run_indexeddb_suite()` function.
  - `python/runners/indexeddb_probe.py`: Defines the `run_indexeddb_probe()` function.

  - all of these scripts are run in the browser! This puts the functions they have defined in the global scope of the browser.
  - the `type="py"` attribute on the `<script>` tag tells PyScript to run python using pyodide.

### 3. Data Flow & Interaction
- **Trigger**: When you click a button (e.g., "Run Package Tests"), the `py-click` attribute bridges the DOM event to the corresponding Python function.
- **Processing**: The Python function executes within the WASM environment. It imports the necessary libraries (`pysodium`, `oqs`, `hio`) and runs the test logic.
- **Output**: The Python code writes output back to the browser's DOM (specifically the `<div id="output">`) to display results to the user.

### 4. PyScript interface calls used in this repo
- **HTML hooks**:
  - `py-click="run_tests"` / `py-click="run_full_suite"` / `py-click="run_liboqs_suite"` map DOM events directly to Python functions defined in the loaded scripts.
  - `<script type="py" src="...">` tells PyScript to load and execute that Python file in the browser.
  - `config="pyscript.toml"` points PyScript at the package list and the files it should mount into the runtime.
- **Python/DOM bridge**:
  - `from pyscript import document` exposes the browser `document` object to Python.
  - `document.querySelector("#output")` fetches the output element for logging.
  - `output.innerHTML += ...` writes results into the page, and `output.scrollTop = output.scrollHeight` keeps the output pinned to the bottom.

## Future with HTMX

A web worker would sit between the HTMX “frontend” and the Pyodide “backend,” intercept requests, route them to Pyodide, then return HTML fragments for HTMX to swap into the page.


## PyScript IndexedDB

PyScript exposes a small Pythonic wrapper over IndexedDB for simple persistence from Python.

Example calls (as used in PyScript):

```python
from pyscript import storage, Storage

# open (or create) a named store
store = await storage("keriwasm-cache")

# dict-like reads/writes
store["key"] = "value"
value = store.get("key")
del store["key"]

# ensure pending writes are flushed
await store.sync()
```

Notes:
- data persists per-origin (tab/page/domain) and is not the Pyodide virtual filesystem
- multiple PyScript workers on the same origin can access the same IndexedDB database
- supported values are basic types (bool/int/float/str/None), plus lists/dicts/tuples (tuples come back as lists)
- binary blobs are possible via `bytearray`/`memoryview`, but must be top-level values (not nested)
- you can subclass `Storage` and pass `storage_class=...` for a custom interface


## keripy on pyscript

Each web worker would run its own Pyodide instance and hio scheduler, handling a slice of keripy. Workers would coordinate via message passing and shared IndexedDB state, while the main thread (or a router worker) dispatches requests and returns results to the UI.

Main thread or a “router” worker:
  - Receives UI or HTMX requests, dispatches to the relevant workers, and aggregates results.
  - Pulls the HTML fragment or data response and sends it back to the page.
