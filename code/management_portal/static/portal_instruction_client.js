(function () {
  "use strict";

  // Pages submit every HATG ISA instruction to the one instruction address
  // (decision_898b8911a2ff “调用入口”). Streaming instructions answer with
  // Server-Sent Events and resume from the last cursor after a disconnect.
  if (window.PortalInstructionClient) return;

  const SCHEMA = "tfi.hatg-instruction.v2";
  const ENDPOINT = "/api/instructions/execute";

  function instructionError(body, status) {
    const error = body && typeof body.error === "object" && body.error ? body.error : {};
    const message = error.message || (typeof body?.error === "string" ? body.error : "") || `指令执行失败（HTTP ${status}）`;
    return Object.assign(new Error(window.PortalI18n.message(message)), {
      code: String(error.code || body?.code || "instruction.failed"),
      details: error.details || body?.details || {},
      status,
      instructionId: String(body?.instruction_id || ""),
      body,
    });
  }

  function callBody(instruction, args, options) {
    const body = { schema: SCHEMA, instruction: String(instruction || ""), arguments: args && typeof args === "object" ? args : {} };
    if (options.idempotencyKey) body.idempotency_key = String(options.idempotencyKey);
    if (options.expectedVersion !== undefined && options.expectedVersion !== null) body.expected_version = options.expectedVersion;
    return body;
  }

  // An expired login session sends the page to the login page, the same way
  // PortalAPI does (decision_daba6706a788 “登录失效”).
  function sessionExpired() {
    window.location.assign("/login");
    return Object.assign(new Error("登录状态已失效，请重新登录后再试。"), { status: 401 });
  }

  // Requests this page cancels on purpose: a watch it no longer needs (such
  // as a backlog read that took its first batch) or a call its caller gave
  // up. The browser reports each as a cancelled request (ERR_ABORTED);
  // window.PortalEvents.closedStreams() lists them with the page's closed
  // event streams, so a reviewer tells them apart from failures.
  const closedRequests = [];
  const clientId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let requestSequence = 0;
  const requestId = () => `${clientId}-${++requestSequence}`;

  function recordClosed(instruction, reason, transportId) {
    const receipt = {
      url: ENDPOINT,
      transport_id: transportId,
      instruction: String(instruction || ""),
      reason: String(reason || "closed"),
      at: new Date().toISOString(),
    };
    closedRequests.push(receipt);
    if (closedRequests.length > 200) closedRequests.splice(0, closedRequests.length - 200);
    window.dispatchEvent(new CustomEvent("portal:connection-closed", { detail: { ...receipt } }));
  }

  function abortReason(signal) {
    const reason = signal?.reason;
    return String(reason?.name || reason?.message || reason || "caller_aborted");
  }

  async function post(body, accept, signal, transportId) {
    return fetch(ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: accept, "X-Portal-Language": window.PortalI18n.requestLanguage(), "X-Portal-Request-ID": transportId },
      body: JSON.stringify(body),
      signal,
    });
  }

  // A pending projection, in-flight call or temporarily unavailable owner can
  // hide an already committed result. Resolve it with the same business key.
  const SAME_KEY_RETRY = new Set(["instruction.projection_pending", "instruction.in_progress", "instruction.owner_unavailable"]);
  const SAME_KEY_RETRY_DELAYS_MS = [500, 1000, 2000, 3000, 5000, 8000, 10000];

  function waitForRetry(delay, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) { reject(signal.reason); return; }
      const abort = () => {
        globalThis.clearTimeout(timer);
        signal.removeEventListener("abort", abort);
        reject(signal.reason);
      };
      const timer = globalThis.setTimeout(() => {
        signal?.removeEventListener("abort", abort);
        resolve();
      }, delay);
      signal?.addEventListener("abort", abort, { once: true });
    });
  }

  // Run one non-streaming instruction and return its result object.
  // options.keepalive lets a small call finish while the page unloads.
  async function execute(instruction, args = {}, options = {}) {
    const call = callBody(instruction, args, options);
    for (let attempt = 0; ; attempt += 1) {
      // One identity per HTTP attempt, including retries of one business call.
      const transportId = requestId();
      let response, body;
      try {
        const result = await window.PortalAPI.requestResult(ENDPOINT, {
          method: "POST", credentials: "same-origin", body: call,
          headers: { Accept: "application/json", "X-Portal-Request-ID": transportId },
          signal: options.signal, timeoutMs: options.timeoutMs, keepalive: !!options.keepalive,
        });
        response = result.response;
        body = result.payload;
      } catch (error) {
        if (options.signal?.aborted) recordClosed(instruction, abortReason(options.signal), transportId);
        throw error;
      }
      if (response.ok && body?.status === "committed") {
        return body.result ?? {};
      }
      const error = instructionError(body, response.status);
      const delay = SAME_KEY_RETRY_DELAYS_MS[attempt];
      if (!call.idempotency_key || !SAME_KEY_RETRY.has(error.code) || delay === undefined || options.signal?.aborted) throw error;
      await waitForRetry(delay, options.signal);
    }
  }

  function parseEvents(buffer, onEvent) {
    let rest = buffer;
    let boundary = rest.indexOf("\n\n");
    while (boundary >= 0) {
      const block = rest.slice(0, boundary);
      rest = rest.slice(boundary + 2);
      let name = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        let value = null;
        try { value = JSON.parse(data.join("\n")); } catch (_error) { value = null; }
        onEvent(name, value);
      }
      boundary = rest.indexOf("\n\n");
    }
    return rest;
  }

  // Subscribe to a streaming instruction (WatchConversationEvents, WatchTriggerPath,
  // WatchLineage). onBatch receives each batch of items; the subscription
  // resumes with after_cursor after a dropped connection until closed.
  function watch(instruction, args = {}, onBatch = () => {}, options = {}) {
    const controller = new AbortController();
    let cursor = args.after_cursor;
    let closed = false;
    let open = false;
    let transportId;
    const done = (async () => {
      let delay = 500;
      while (!closed) {
        const current = { ...args };
        if (cursor !== undefined && cursor !== null && cursor !== "") current.after_cursor = cursor;
        let finished = false;
        try {
          open = true;
          transportId = requestId();
          const response = await post(callBody(instruction, current, options), "text/event-stream", controller.signal, transportId);
          if (response.status === 401) throw sessionExpired();
          if (!response.ok || !response.body) {
            let body = null;
            try { body = await response.json(); } catch (_error) { body = null; }
            throw instructionError(body, response.status);
          }
          delay = 500;
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let failure = null;
          while (!finished && !failure) {
            const { value, done: ended } = await reader.read();
            if (ended) break;
            buffer = parseEvents(buffer + decoder.decode(value, { stream: true }), (name, data) => {
              if (name === "batch") {
                const items = Array.isArray(data?.items) ? data.items : [];
                const last = items[items.length - 1];
                if (last && last.cursor !== undefined && last.cursor !== null) cursor = last.cursor;
                if (items.length || options.includeEmptyBatches) onBatch(items);
              } else if (name === "end") finished = true;
              else if (name === "error") failure = instructionError({ error: data }, 200);
            });
          }
          if (failure) throw failure;
          if (finished) return;
        } catch (error) {
          if (closed || error?.name === "AbortError") return;
          if (error?.status && error.status < 500 && error.status !== 200) throw error;
          if (error?.code && !["instruction.owner_unavailable", "instruction.failed"].includes(error.code)) throw error;
        }
        open = false;
        await new Promise(resolve => globalThis.setTimeout(resolve, delay));
        delay = Math.min(delay * 2, 8000);
      }
    })();
    // A request of the watch is open from its POST until its stream ends.
    done.finally(() => { open = false; }).catch(() => {});
    return {
      done,
      // `reason` says why the page no longer needs the stream.
      close(reason = "watch_closed") {
        if (closed) return;
        closed = true;
        if (open) recordClosed(instruction, reason, transportId);
        controller.abort();
      },
    };
  }

  // Upload one file: CreateUpload declares it, then its bytes go to the
  // returned data channel. Resolves with the upload record.
  async function upload(file, options = {}) {
    const fileName = String(options.fileName || file?.name || "file");
    const mediaType = String(options.mediaType || file?.type || "application/octet-stream");
    const created = await execute("CreateUpload", {
      purpose: String(options.purpose || ""),
      ...(options.target ? { target: String(options.target) } : {}),
      file_name: fileName,
      media_type: mediaType,
      size: Number(file?.size || 0),
    }, { idempotencyKey: `upload:${globalThis.PortalUIPrimitives.randomUUID()}` });
    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", String(created.upload_url || ""), true);
      xhr.withCredentials = true;
      xhr.timeout = Number(options.timeoutMs || 360_000);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = event => {
        if (!event.lengthComputable || !event.total || typeof options.onProgress !== "function") return;
        options.onProgress(Math.max(0, Math.min(99, Math.round(event.loaded / event.total * 100))));
      };
      xhr.onerror = () => reject(new Error("上传失败，请检查网络后重试。"));
      xhr.onabort = () => reject(new Error("上传已取消。"));
      xhr.ontimeout = () => reject(new Error("上传超时，请重试。"));
      xhr.onload = () => {
        if (xhr.status === 401) {
          window.location.assign("/login");
          reject(new Error("登录状态已失效，请重新登录后再试。"));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          let payload = {};
          try { payload = JSON.parse(xhr.responseText || "{}"); } catch (_error) { payload = {}; }
          reject(instructionError(payload, xhr.status));
          return;
        }
        resolve();
      };
      xhr.send(file);
    });
    const uploadId = String(created.upload_id || "");
    return { upload_id: uploadId, url: `/api/uploads/${uploadId}`, file_name: fileName, media_type: mediaType, size: Number(file?.size || 0) };
  }

  window.PortalInstructionClient = Object.freeze({
    SCHEMA, ENDPOINT, execute, watch, upload,
    closedRequests: () => closedRequests.map(item => ({ ...item })),
  });
})();
