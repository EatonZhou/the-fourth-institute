(function (global) {
  "use strict";

  const esc = global.PortalUIPrimitives.escapeHtml;
  const labels = {running: window.PortalI18n.t("运行中"), succeeded: window.PortalI18n.t("成功"), failed: window.PortalI18n.t("失败"), timed_out: window.PortalI18n.t("超时"), cancelled: window.PortalI18n.t("已取消"), lost: window.PortalI18n.t("运行丢失")};
  const statusText = status => labels[status] || status || window.PortalI18n.t("正在读取");
  const artifactUrl = (lineageId, runId, name, download = false) => `/api/script-runs/artifact?${new URLSearchParams({
    lineage_id: lineageId, script_run_id: runId, name, ...(download ? {download: "1"} : {}),
  })}`;

  function duration(run) {
    const value = run.duration_seconds ?? (run.started_at ? (Date.now() - Date.parse(run.started_at)) / 1000 : null);
    return value !== null && Number.isFinite(Number(value)) ? `${Math.max(0, Number(value)).toFixed(1)} 秒` : "";
  }

  function effectCalls(response) {
    return {items: (response.steps?.items || []).filter(step => step.kind === "effect").flatMap(step =>
      Object.values((step.current || step.result || {}).calls || {}).map(call => ({
        ...call, script_run_id: call.script_run_id || call.business_ids?.script_run_id,
      }))
    )};
  }

  function mount(root, {lineageId, triggerPathId, response}) {
    const abort = new AbortController();
    const entries = new Map();
    let timer = 0;
    let disposed = false;
    let busy = false;
    let pathActive = response.path?.status === "active";
    let nextOffset = response.steps?.next_offset ?? null;
    let loadedOffset = 0;
    const alive = () => !disposed && root.isConnected;
    const execute = (name, args) => global.PortalInstructionClient.execute(name, args, {signal: abort.signal});
    root.innerHTML = window.PortalI18n.markup('<h4>脚本运行</h4><div data-script-list></div><p data-script-status role="status"></p><button type="button" class="portalButton is-quiet" data-script-more hidden>更多调用</button><button type="button" class="portalButton is-quiet" data-script-refresh>刷新脚本运行</button>');
    const list = root.querySelector("[data-script-list]");
    const message = root.querySelector("[data-script-status]");
    const more = root.querySelector("[data-script-more]");

    function artifacts(entry, run) {
      const host = entry.node.querySelector("[data-script-artifacts]");
      const signature = JSON.stringify(run.artifacts || []);
      if (signature === entry.artifactSignature) return;
      entry.artifactSignature = signature;
      host.innerHTML = (run.artifacts || []).map(item => {
        const name = String(item.name || item.path || "");
        const preview = /\.html?$/i.test(name) || item.mime_type === "text/html" || String(item.mime_type).startsWith("text/") || /^image\/(png|jpeg|gif|webp|avif|bmp)$/.test(item.mime_type);
        return window.PortalI18n.html`<div><span>${esc(name)} · ${esc(item.size)} 字节</span> <a href="${esc(artifactUrl(lineageId, entry.id, name, true))}" download>下载</a>${preview ? window.PortalI18n.html` <button type="button" class="portalButton is-quiet" data-script-preview="${esc(name)}">查看</button>` : ""}<div data-artifact-frame></div></div>`;
      }).join("") || "暂无产物";
    }

    async function readStream(entry, stream) {
      const state = entry.streams[stream];
      if (state.busy || !alive()) return;
      state.busy = true;
      const button = entry.node.querySelector(`[data-script-read="${stream}"]`);
      const note = entry.node.querySelector(`[data-script-note="${stream}"]`);
      button.disabled = true;
      try {
        const chunk = await execute("ReadScriptRunOutput", {lineage_id: lineageId, script_run_id: entry.id, stream, offset: state.offset, limit: 16384});
        if (!alive()) return;
        if (chunk.offset !== state.offset || chunk.next_offset < state.offset) throw new Error("日志读取偏移不一致");
        const bytes = Uint8Array.from(global.atob(chunk.base64), value => value.charCodeAt(0));
        // Keep the decoder across byte pages, including partial UTF-8 characters.
        const text = state.decoder.decode(bytes, {stream: !chunk.eof || entry.run?.status === "running"});
        entry.node.querySelector(`[data-script-log="${stream}"]`).append(document.createTextNode(text));
        state.offset = chunk.next_offset;
        state.eof = chunk.eof;
        state.loaded = true;
        note.textContent = window.PortalI18n.text`已读取 ${state.offset} / ${chunk.size} 字节${chunk.truncated ? window.PortalI18n.t(" · 日志达到保存上限，已截断") : ""}`;
        button.hidden = chunk.eof && entry.run?.status !== "running";
        button.textContent = chunk.eof ? window.PortalI18n.t("刷新日志") : window.PortalI18n.t("继续读取");
      } catch (error) {
        if (alive()) note.textContent = window.PortalI18n.text`读取失败：${error.message || error}`;
      } finally {
        state.busy = false;
        button.disabled = false;
      }
    }

    async function refreshEntry(entry) {
      const sequence = (entry.sequence || 0) + 1;
      entry.sequence = sequence;
      try {
        const run = await execute("GetScriptRun", {lineage_id: lineageId, script_run_id: entry.id});
        if (!alive() || entry.sequence !== sequence) return;
        entry.readFailed = false;
        entry.run = run;
        entry.node.dataset.scriptStatus = run.status;
        entry.node.querySelector("[data-script-summary]").textContent = window.PortalI18n.text`RunScript · ${statusText(run.status)} · ${duration(run)} · ${run.path || entry.id}`;
        entry.node.querySelector("[data-script-error]").textContent = run.error_code ? window.PortalI18n.text`错误：${run.error_code}` : "";
        entry.node.querySelector("[data-script-outputs]").textContent = JSON.stringify(run.outputs || {}, null, 2);
        artifacts(entry, run);
        if (entry.node.open) await Promise.all(["stdout", "stderr"].filter(stream => !entry.streams[stream].loaded || entry.streams[stream].eof).map(stream => readStream(entry, stream)));
      } catch (error) {
        if (alive() && entry.sequence === sequence) {
          entry.readFailed = true;
          entry.node.querySelector("[data-script-error]").textContent = window.PortalI18n.text`状态读取失败：${error.message || error}`;
        }
      }
    }

    function addCalls(page) {
      for (const call of page?.items || []) {
        if (!call.script_run_id || entries.has(call.script_run_id)) continue;
        const id = call.script_run_id;
        const node = document.createElement("details");
        node.dataset.scriptRun = id;
        node.innerHTML = window.PortalI18n.html`<summary data-script-summary>RunScript · 正在读取 · ${esc(id)}</summary><p data-script-error role="status"></p><h5>输出字段</h5><pre data-script-outputs></pre>${["stdout", "stderr"].map(stream => window.PortalI18n.html`<h5>${stream}</h5><pre data-script-log="${stream}"></pre><small data-script-note="${stream}"></small><button type="button" class="portalButton is-quiet" data-script-read="${stream}">读取日志</button>`).join("")}<h5>产物</h5><div data-script-artifacts></div>`;
        const entry = {id, node, run: null, streams: Object.fromEntries(["stdout", "stderr"].map(stream => [stream, {offset: 0, eof: false, loaded: false, busy: false, decoder: new TextDecoder()}]))};
        entries.set(id, entry);
        list.append(node);
        node.addEventListener("toggle", () => { if (node.open) void refreshEntry(entry); });
        node.addEventListener("click", event => {
          const read = event.target.closest("[data-script-read]");
          if (read) void readStream(entry, read.dataset.scriptRead);
          const preview = event.target.closest("[data-script-preview]");
          if (preview) {
            const host = preview.parentElement.querySelector("[data-artifact-frame]");
            host.replaceChildren();
            const frame = document.createElement("iframe");
            frame.setAttribute("sandbox", "allow-scripts");
            frame.setAttribute("referrerpolicy", "no-referrer");
            frame.title = preview.dataset.scriptPreview;
            frame.style.cssText = "width:100%;height:420px;border:1px solid currentColor";
            frame.src = artifactUrl(lineageId, id, preview.dataset.scriptPreview);
            host.append(frame);
          }
        });
      }
      more.hidden = nextOffset === null;
      message.textContent = entries.size ? "" : window.PortalI18n.t("暂无脚本运行。");
    }

    function schedule() {
      global.clearTimeout(timer);
      if (alive() && (pathActive || [...entries.values()].some(entry => !entry.readFailed && (!entry.run || entry.run.status === "running")))) {
        timer = global.setTimeout(() => {
          if (!alive()) { destroy(); return; }
          if (document.hidden) { schedule(); return; }
          void refresh();
        }, 5000);
      }
    }

    async function refresh(offset = 0) {
      if (busy || !alive()) return;
      busy = true;
      more.disabled = true;
      try {
        // Discover asynchronously recorded calls even if the enclosing Trigger Path settled.
        const data = await execute("GetTriggerPath", {lineage_id: lineageId, trigger_path_id: triggerPathId, offset, limit: 50});
        if (!alive()) return;
        pathActive = data.path?.status === "active";
        if (offset >= loadedOffset) {
          loadedOffset = offset;
          nextOffset = data.steps?.next_offset ?? null;
        }
        addCalls(effectCalls(data));
        await Promise.all([...entries.values()].map(refreshEntry));
      } catch (error) {
        if (alive()) message.textContent = window.PortalI18n.text`脚本运行读取失败：${error.message || error}`;
      } finally {
        busy = false;
        more.disabled = false;
        schedule();
      }
    }

    function destroy() {
      disposed = true;
      global.clearTimeout(timer);
      abort.abort();
    }

    more.addEventListener("click", () => { if (nextOffset !== null) void refresh(nextOffset); });
    root.querySelector("[data-script-refresh]").addEventListener("click", () => void refresh());
    addCalls(effectCalls(response));
    Promise.all([...entries.values()].map(refreshEntry)).then(schedule);
    return {destroy};
  }

  global.PortalScriptRuns = Object.freeze({mount, statusText, effectCalls});
})(window);
