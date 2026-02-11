/*
 * Router Worker (skeleton)
 *
 * Purpose:
 * - Keep backend workers warm and stateful.
 * - Route HTMX-style requests by path prefix.
 * - Normalize responses/events into a single protocol.
 * - Support protocol-native workers and legacy liboqs worker adapter mode.
 */

const PROTOCOL_VERSION = 1;
const DEFAULT_TIMEOUT_MS = 30000;

const state = {
  requestTimeoutMs: DEFAULT_TIMEOUT_MS,
  routes: [], // [{ prefix, worker }]
  workerDefs: new Map(), // workerName -> { url, mode, type, config }
  workers: new Map(), // workerName -> workerInfo
  pending: new Map(), // requestId -> { workerName, timeoutId }
  activeLegacyByWorker: new Map(), // workerName -> requestId
};

function nowIso() {
  return new Date().toISOString();
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function escapeHtml(raw) {
  return String(raw)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function send(msg) {
  self.postMessage(msg);
}

function sendProtocolError(code, message, id = null) {
  send({
    v: PROTOCOL_VERSION,
    kind: "error",
    id,
    code,
    message,
    ts: nowIso(),
  });
}

function sendHxResponse(id, status, body, headers = {}) {
  send({
    v: PROTOCOL_VERSION,
    kind: "hx.response",
    id,
    status,
    headers,
    body,
    ts: nowIso(),
  });
}

function sendEvent(id, event, data = {}) {
  send({
    v: PROTOCOL_VERSION,
    kind: "event",
    id,
    event,
    data,
    ts: nowIso(),
  });
}

function normalizeRoutes(routes) {
  if (!Array.isArray(routes)) return [];
  const clean = routes
    .filter((r) => isObject(r) && typeof r.prefix === "string" && typeof r.worker === "string")
    .map((r) => ({ prefix: r.prefix, worker: r.worker }));

  // Longest prefix first for deterministic best-match routing.
  clean.sort((a, b) => b.prefix.length - a.prefix.length);
  return clean;
}

function applyInit(msg) {
  if (Array.isArray(msg.routes)) {
    state.routes = normalizeRoutes(msg.routes);
  }

  if (isObject(msg.workers)) {
    for (const [name, def] of Object.entries(msg.workers)) {
      if (!isObject(def) || typeof def.url !== "string") continue;
      state.workerDefs.set(name, {
        url: def.url,
        mode: typeof def.mode === "string" ? def.mode : "protocol",
        type: typeof def.type === "string" ? def.type : "classic",
        config: isObject(def.config) ? def.config : {},
      });
    }
  }

  if (Number.isFinite(msg.requestTimeoutMs) && msg.requestTimeoutMs > 0) {
    state.requestTimeoutMs = Math.floor(msg.requestTimeoutMs);
  }
}

function matchRoute(path) {
  for (const route of state.routes) {
    if (path.startsWith(route.prefix)) {
      return route;
    }
  }
  return null;
}

function ensureWorker(workerName) {
  if (state.workers.has(workerName)) {
    return state.workers.get(workerName);
  }

  const def = state.workerDefs.get(workerName);
  if (!def) {
    throw new Error(`No worker definition for '${workerName}'`);
  }

  const worker = new Worker(def.url, { type: def.type, name: `router:${workerName}` });
  const info = {
    name: workerName,
    mode: def.mode,
    ready: def.mode === "legacy-liboqs", // protocol workers need worker.ready
    startedAt: Date.now(),
    worker,
  };

  worker.onmessage = (event) => {
    handleWorkerMessage(info, event.data || {});
  };

  worker.onerror = (event) => {
    const message = String(event?.message || "worker error");
    failPendingForWorker(workerName, `Worker '${workerName}' error: ${message}`);
    sendProtocolError("WORKER_ERROR", message);
  };

  if (info.mode === "protocol") {
    worker.postMessage({
      v: PROTOCOL_VERSION,
      kind: "worker.init",
      worker: workerName,
      config: def.config,
      ts: nowIso(),
    });
  }

  state.workers.set(workerName, info);
  return info;
}

function clearPending(requestId) {
  const pending = state.pending.get(requestId);
  if (!pending) return;
  clearTimeout(pending.timeoutId);
  state.pending.delete(requestId);
}

function failPendingForWorker(workerName, reason) {
  for (const [requestId, pending] of state.pending.entries()) {
    if (pending.workerName !== workerName) continue;
    clearPending(requestId);
    sendHxResponse(
      requestId,
      502,
      `<div class="fail">Backend worker failed: ${escapeHtml(reason)}</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
  }
}

function handleProtocolWorkerMessage(info, msg) {
  if (!isObject(msg)) return;

  if (msg.kind === "worker.ready") {
    info.ready = true;
    sendEvent(null, "worker.ready", { worker: info.name });
    return;
  }

  if (msg.kind === "worker.event") {
    sendEvent(msg.id || null, msg.event || "worker.event", msg.data || {});
    return;
  }

  if (msg.kind === "worker.response") {
    const requestId = msg.id;
    if (!requestId || !state.pending.has(requestId)) {
      sendProtocolError("ORPHAN_RESPONSE", "Received worker.response for unknown request id", requestId || null);
      return;
    }

    clearPending(requestId);
    const response = isObject(msg.response) ? msg.response : {};
    sendHxResponse(
      requestId,
      Number.isFinite(response.status) ? response.status : 500,
      typeof response.body === "string" ? response.body : "",
      isObject(response.headers) ? response.headers : {}
    );
    return;
  }
}

function handleLegacyLiboqsMessage(info, msg) {
  if (!isObject(msg)) return;

  // Existing worker emits {type: "log-batch", entries:[...]} and {type:"status", state, error}
  if (msg.type === "log-batch") {
    const requestId = state.activeLegacyByWorker.get(info.name) || null;
    sendEvent(requestId, "legacy.log_batch", { worker: info.name, entries: msg.entries || [] });
    return;
  }

  if (msg.type === "status") {
    const requestId = state.activeLegacyByWorker.get(info.name) || null;
    const payload = { worker: info.name, state: msg.state || "unknown" };
    if (msg.error) payload.error = msg.error;
    sendEvent(requestId, "legacy.status", payload);

    if (msg.state === "done" || msg.state === "error") {
      state.activeLegacyByWorker.delete(info.name);
    }
    return;
  }
}

function handleWorkerMessage(info, msg) {
  if (info.mode === "legacy-liboqs") {
    handleLegacyLiboqsMessage(info, msg);
    return;
  }
  handleProtocolWorkerMessage(info, msg);
}

function dispatchProtocolRequest(requestId, route, reqMsg) {
  const info = ensureWorker(route.worker);
  if (!info.ready) {
    sendHxResponse(
      requestId,
      503,
      `<div class="info">Worker '${escapeHtml(route.worker)}' is still warming up.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
    return;
  }

  const timeoutId = setTimeout(() => {
    clearPending(requestId);
    sendHxResponse(
      requestId,
      504,
      `<div class="fail">Request timed out in router after ${state.requestTimeoutMs}ms.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
  }, state.requestTimeoutMs);

  state.pending.set(requestId, { workerName: route.worker, timeoutId });

  info.worker.postMessage({
    v: PROTOCOL_VERSION,
    kind: "worker.request",
    id: requestId,
    request: {
      method: reqMsg.method,
      path: reqMsg.path,
      headers: isObject(reqMsg.headers) ? reqMsg.headers : {},
      query: isObject(reqMsg.query) ? reqMsg.query : {},
      form: isObject(reqMsg.form) ? reqMsg.form : {},
      body: typeof reqMsg.body === "string" ? reqMsg.body : "",
    },
    ts: nowIso(),
  });
}

function dispatchLegacyLiboqsRequest(requestId, route, reqMsg) {
  const info = ensureWorker(route.worker);
  const current = state.activeLegacyByWorker.get(route.worker);

  if (current) {
    sendHxResponse(
      requestId,
      409,
      `<div class="info">liboqs worker is busy with request '${escapeHtml(current)}'.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
    return;
  }

  // Adapter supports explicit run endpoint only.
  if (reqMsg.path !== "/hx/tests/liboqs/run") {
    sendHxResponse(
      requestId,
      404,
      `<div class="fail">Legacy liboqs adapter only supports /hx/tests/liboqs/run.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
    return;
  }

  state.activeLegacyByWorker.set(route.worker, requestId);
  info.worker.postMessage({ type: "run" });

  sendHxResponse(
    requestId,
    202,
    `<div class="info">liboqs run started in warm worker '${escapeHtml(route.worker)}'.</div>`,
    { "Content-Type": "text/html; charset=utf-8" }
  );
}

function handleHxRequest(msg) {
  const requestId = typeof msg.id === "string" && msg.id.length > 0 ? msg.id : null;
  if (!requestId) {
    sendProtocolError("INVALID_REQUEST", "hx.request requires non-empty string id", null);
    return;
  }

  if (typeof msg.path !== "string" || msg.path.length === 0) {
    sendHxResponse(
      requestId,
      400,
      `<div class="fail">Missing request path.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
    return;
  }

  const route = matchRoute(msg.path);
  if (!route) {
    sendHxResponse(
      requestId,
      404,
      `<div class="fail">No worker route for path '${escapeHtml(msg.path)}'.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
    return;
  }

  const def = state.workerDefs.get(route.worker);
  if (!def) {
    sendHxResponse(
      requestId,
      500,
      `<div class="fail">Route targets undefined worker '${escapeHtml(route.worker)}'.</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
    return;
  }

  msg.method = typeof msg.method === "string" ? msg.method.toUpperCase() : "GET";

  try {
    if (def.mode === "legacy-liboqs") {
      dispatchLegacyLiboqsRequest(requestId, route, msg);
    } else {
      dispatchProtocolRequest(requestId, route, msg);
    }
  } catch (err) {
    sendHxResponse(
      requestId,
      500,
      `<div class="fail">Router dispatch error: ${escapeHtml(err?.message || String(err))}</div>`,
      { "Content-Type": "text/html; charset=utf-8" }
    );
  }
}

self.onmessage = (event) => {
  const msg = event.data || {};
  if (!isObject(msg)) return;

  if (msg.kind === "control.init") {
    applyInit(msg);
    send({
      v: PROTOCOL_VERSION,
      kind: "control.ready",
      id: msg.id || null,
      routes: state.routes,
      workerCount: state.workerDefs.size,
      requestTimeoutMs: state.requestTimeoutMs,
      ts: nowIso(),
    });
    return;
  }

  if (msg.kind === "control.ping") {
    send({
      v: PROTOCOL_VERSION,
      kind: "control.pong",
      id: msg.id || null,
      ts: nowIso(),
    });
    return;
  }

  if (msg.kind === "hx.request") {
    handleHxRequest(msg);
    return;
  }

  sendProtocolError("UNKNOWN_KIND", `Unsupported router message kind '${String(msg.kind)}'`, msg.id || null);
};
