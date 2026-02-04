/*
 * Liboqs test worker
 * Runs the liboqs suite in a dedicated Pyodide worker and streams
 * batched log output back to the main thread.
 */

let pyodide = null;
let pyodideReady = null;
let running = false;

const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.1/full/";
const PYODIDE_JS = `${PYODIDE_INDEX_URL}pyodide.js`;

const LOG_FLUSH_MS = 150;
const LOG_BATCH_MAX = 40;
const logBuffer = [];

function flushLogs() {
    if (logBuffer.length === 0) return;
    const batch = logBuffer.splice(0, logBuffer.length);
    self.postMessage({ type: "log-batch", entries: batch });
}

function enqueueLog(entry) {
    logBuffer.push(entry);
    if (logBuffer.length >= LOG_BATCH_MAX) {
        flushLogs();
    }
}

setInterval(() => {
    if (logBuffer.length > 0) {
        flushLogs();
    }
}, LOG_FLUSH_MS);

self.enqueueLiboqsLog = enqueueLog;
self.flushLiboqsLogs = flushLogs;
self.enqueueLiboqsLogJson = (payload) => {
    try {
        enqueueLog(JSON.parse(payload));
    } catch (err) {
        enqueueLog({
            time: new Date().toISOString().split("T")[1].slice(0, 12),
            msg: `Log parse error: ${err && err.message ? err.message : String(err)}`,
            css: "fail",
        });
    }
};

const PY_FILES = [
    { url: "/python/run_liboqs_worker.py", path: "/run_liboqs_worker.py" },
    { url: "/python/test_runner_doer.py", path: "/test_runner_doer.py" },
    { url: "/python/test_loaders.py", path: "/test_loaders.py" },
    { url: "/python/test_kem.py", path: "/test_kem.py" },
    { url: "/python/test_sig.py", path: "/test_sig.py" },
    { url: "/python/test_stfl_sig.py", path: "/test_stfl_sig.py" },
    { url: "/python/hio_bridge.py", path: "/hio_bridge.py" },
    { url: "/python/hio/__init__.py", path: "/hio/__init__.py" },
    { url: "/python/hio/hioing.py", path: "/hio/hioing.py" },
    { url: "/python/hio/base/__init__.py", path: "/hio/base/__init__.py" },
    { url: "/python/hio/base/basing.py", path: "/hio/base/basing.py" },
    { url: "/python/hio/base/doing.py", path: "/hio/base/doing.py" },
    { url: "/python/hio/base/tyming.py", path: "/hio/base/tyming.py" },
    { url: "/python/hio/help/__init__.py", path: "/hio/help/__init__.py" },
    { url: "/python/hio/help/helping.py", path: "/hio/help/helping.py" },
    { url: "/python/hio/help/timing.py", path: "/hio/help/timing.py" },
];

function resolveUrl(path) {
    if (path.startsWith("http")) return path;
    return `${self.location.origin}${path}`;
}

async function fetchToFS(url, path) {
    const res = await fetch(resolveUrl(url));
    if (!res.ok) {
        throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
    }
    const data = new Uint8Array(await res.arrayBuffer());
    const dir = path.substring(0, path.lastIndexOf("/"));
    if (dir) {
        pyodide.FS.mkdirTree(dir);
    }
    pyodide.FS.writeFile(path, data);
}

async function loadPyFiles() {
    for (const file of PY_FILES) {
        await fetchToFS(file.url, file.path);
    }
}

async function initPyodide() {
    if (pyodideReady) return pyodideReady;

    pyodideReady = (async () => {
        importScripts(PYODIDE_JS);
        pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });
        await pyodide.loadPackage(["micropip"]);

        await loadPyFiles();

        const wheelUrl = resolveUrl("/static/liboqs_python-0.15.0-py3-none-any.whl");
        await pyodide.runPythonAsync(`\
import sys, micropip
if "/" not in sys.path:
    sys.path.insert(0, "/")
await micropip.install(["${wheelUrl}"])
`);

        return pyodide;
    })();

    return pyodideReady;
}

self.onmessage = async (event) => {
    const { type } = event.data || {};

    if (type !== "run") return;

    if (running) {
        self.postMessage({ type: "status", state: "busy" });
        return;
    }

    running = true;
    self.postMessage({ type: "status", state: "starting" });

    try {
        await initPyodide();
        self.postMessage({ type: "status", state: "running" });
        await pyodide.runPythonAsync("import run_liboqs_worker; await run_liboqs_worker.run()");
        flushLogs();
        self.postMessage({ type: "status", state: "done" });
    } catch (err) {
        flushLogs();
        self.postMessage({
            type: "status",
            state: "error",
            error: err && err.message ? err.message : String(err),
        });
    } finally {
        running = false;
    }
};
