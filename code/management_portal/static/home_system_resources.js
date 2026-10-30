(() => {
  if (window.PortalHomeSystemResources) return;

  // How Home's system resources panel shows GPU and disk status: summaries, the
  // details views and the disk triage findings.
  const DEPENDENCIES = Object.freeze([
    "esc", "formatDateTime",
  ]);

  function createSystemResources(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomeSystemResources is missing: ${missing.join(", ")}`);
    const {
      esc, formatDateTime,
    } = dependencies;

    function formatBytes(value) {
      return window.PortalUI.formatBytes(value, { precision: "single" });
    }

    function gpuCount(gpu) {
      return Number(gpu?.gpu_count) || 0;
    }

    function gpuAvailableCount(gpu) {
      return Number(gpu?.available_gpu_count) || 0;
    }

    function gpuSummaryText(gpu) {
      if (!gpu || gpu.status === "loading") return window.PortalI18n.t("GPU: loading");
      if (gpu.status === "not_checked") return window.PortalI18n.t("GPU: not checked");
      if (gpu.status === "error") return window.PortalI18n.t("GPU: unavailable");
      const unknown = Number(gpu?.unknown_gpu_count || 0);
      if (gpu.stale || gpu.refresh_error) {
        return window.PortalI18n.text`GPU: stale · ${gpuAvailableCount(gpu)}/${gpuCount(gpu)} available${unknown ? ` · ${unknown} unknown` : ""}`;
      }
      return window.PortalI18n.text`GPU: ${gpuAvailableCount(gpu)}/${gpuCount(gpu)} available${unknown ? ` · ${unknown} unknown` : ""}`;
    }

    function gpuResourceHtml(gpu) {
      return window.PortalI18n.html`<span>${esc(gpuSummaryText(gpu))}</span>`;
    }

    function gpuCommandText(process) {
      return String(process?.command_line || process?.process_name || process?.command || "").trim();
    }

    function gpuOwnerFromRunId(command) {
      const runId = command.match(/--run-id\s+([^\s]+)/)?.[1] || command.match(/\/live\/([^/\s]+)/)?.[1] || "";
      if (!runId) return "";
      return runId;
    }

    function gpuProcessOwnerInfo(process) {
      const command = gpuCommandText(process);
      const runOwner = gpuOwnerFromRunId(command);
      if (runOwner) return {label: runOwner, source: "run id"};
      const user = String(process?.user || "").trim();
      if (user) return {label: user, source: "linux user"};
      return {label: "unknown", source: "unknown"};
    }

    function gpuProcessHtml(process) {
      const owner = gpuProcessOwnerInfo(process);
      const pid = process?.pid || "";
      const memory = process?.used_memory_mb != null ? `${process.used_memory_mb} MB` : "";
      const elapsed = process?.elapsed ? ` · ${esc(process.elapsed)}` : "";
      const ownerText = owner.label && owner.label !== "unknown" ? ` · owner ${esc(owner.label)}` : "";
      return window.PortalI18n.html`
    <div class="agent-status-gpu-process">
      ${pid ? `pid ${esc(pid)}` : "process"}${memory ? window.PortalI18n.html` · <strong>${esc(memory)}</strong>` : ""}${elapsed}${ownerText}
    </div>`;
    }

    function gpuStatusLabel(item) {
      const state = String(item?.state || "").trim();
      if (["empty", "idle", "busy", "unknown"].includes(state)) return state;
      return "unknown";
    }

    function gpuMemoryMeta(item) {
      if (item?.memory_supported && item?.memory_used_mb != null && Number(item?.memory_total_mb) > 0) {
        return `${item.memory_used_mb}/${item.memory_total_mb} MB`;
      }
      if (item?.unified_memory_used_percent != null) return `unified memory ${item.unified_memory_used_percent}%`;
      if (item?.memory_util_percent != null) return `memory ${item.memory_util_percent}%`;
      if (item?.memory_supported) return "memory telemetry unknown";
      return "memory total unsupported";
    }

    function gpuRowHtml(item) {
      const compute = Array.isArray(item.compute_processes) ? item.compute_processes : [];
      const graphics = Array.isArray(item.graphics_processes) ? item.graphics_processes : [];
      const processes = [...compute, ...graphics].filter(process => Number(process?.used_memory_mb || 0) > 0);
      const meta = [
        item.gpu_util_percent != null ? `${item.gpu_util_percent}% util` : "",
        gpuMemoryMeta(item),
        item.temperature_c != null ? `${item.temperature_c}C` : "",
        item.power_draw_w != null ? `${item.power_draw_w}W` : "",
      ].filter(Boolean).join(" · ");
      return window.PortalI18n.html`
    <div class="agent-status-gpu-row">
      <div class="agent-status-gpu-title">
        <span class="agent-status-gpu-name"><strong>GPU ${esc(item.index ?? "")}</strong>${item.name ? ` ${esc(item.name)}` : ""}</span>
        <span>${esc(gpuStatusLabel(item))}</span>
      </div>
      <div class="agent-status-gpu-meta">${esc(meta || window.PortalI18n.t("no telemetry"))}</div>
      ${processes.length ? processes.map(gpuProcessHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">No process using GPU memory.</div>`}
    </div>`;
    }

    function gpuResourceDetailHtml(resource) {
      if (!resource || resource.status === "loading") {
        return window.PortalI18n.html`<div class="agent-status-gpu-row"><div class="agent-status-muted">GPU status is loading.</div></div>`;
      }
      if (resource.status === "not_checked") {
        return window.PortalI18n.html`<div class="agent-status-gpu-row"><div class="agent-status-muted">No GPU observation yet. Use Refresh to collect it.</div></div>`;
      }
      if (resource.status === "error") {
        return window.PortalI18n.html`<div class="agent-status-gpu-row"><div class="agent-status-error">Status unavailable: ${esc(resource.error || resource.refresh_error || window.PortalI18n.t("unknown error"))}</div></div>`;
      }
      const gpus = Array.isArray(resource.gpus) ? resource.gpus : [];
      return gpus.length ? gpus.map(gpuRowHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">No GPU data.</div>`;
    }

    function gpuSingleResourceHtml(resource) {
      const checked = formatDateTime(resource?.updated_at || resource?.generated_at);
      const resourceTitle = `${resource?.display_name || resource?.device_id || window.PortalI18n.t("GPU server")}${resource?.ip ? ` | IP: ${resource.ip}` : ""}`;
      const statusText = resource?.status === "not_checked"
        ? window.PortalI18n.t("not checked")
        : `${Number(resource?.available_gpu_count || 0)}/${Number(resource?.gpu_count ?? resource?.expected_gpu_count ?? 0) || 0} available${Number(resource?.unknown_gpu_count || 0) ? ` · ${Number(resource.unknown_gpu_count)} unknown` : ""}`;
      return window.PortalI18n.html`
    <div class="agent-status-gpu-resource-card">
      <div class="agent-status-gpu-head">
        <span>${esc(resourceTitle)}</span>
        <span class="agent-status-muted">${esc(statusText)}${checked ? ` · ${esc(checked)}` : ""}</span>
      </div>
      ${resource?.refresh_error ? window.PortalI18n.html`<div class="agent-status-error">Status is stale: ${esc(resource.refresh_error)}</div>` : ""}
      <div class="agent-status-gpu-grid">${gpuResourceDetailHtml(resource)}</div>
    </div>`;
    }

    function resourceRefreshWarningHtml(resource, label) {
      if (!resource?.stale && !resource?.refresh_error) return "";
      const detail = String(resource?.refresh_error || "latest refresh failed");
      return window.PortalI18n.html`<div class="agent-status-error">${esc(label)} data is stale: ${esc(detail)}</div>`;
    }

    function gpuDetailsHtml(gpu) {
      if (!gpu || gpu.status === "loading") {
        return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-muted">GPU status is loading.</div></div>`;
      }
      const servers = Array.isArray(gpu?.servers) ? gpu.servers : [];
      if (servers.length) {
        return window.PortalI18n.html`
      <div class="agent-status-gpu-card">
        ${resourceRefreshWarningHtml(gpu, "GPU")}
        <div class="agent-status-gpu-resource-list">${servers.map(gpuSingleResourceHtml).join("")}</div>
      </div>`;
      }
      if (gpu.status === "error") {
        return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-error">GPU status unavailable: ${esc(gpu.error || window.PortalI18n.t("unknown error"))}</div></div>`;
      }
      return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-muted">No GPU data.</div></div>`;
    }

    function diskSummaryText(disk) {
      if (!disk || disk.status === "loading") return window.PortalI18n.t("Disk: loading");
      if (disk.status === "error") return window.PortalI18n.t("Disk: unavailable");
      if (disk.stale || disk.refresh_error) return window.PortalI18n.t("Disk: stale");
      const filesystems = Array.isArray(disk?.filesystems) ? disk.filesystems : [];
      const critical = filesystems.filter(item => item?.status === "critical").length;
      const warn = filesystems.filter(item => item?.status === "warn").length;
      if (critical) return window.PortalI18n.text`Disk: critical ${critical}`;
      if (warn) return window.PortalI18n.text`Disk: warn ${warn}`;
      if (disk.status === "partial") return window.PortalI18n.t("Disk: partial");
      if (!filesystems.length) return window.PortalI18n.t("Disk: not checked");
      return window.PortalI18n.t("Disk: ok");
    }

    function diskResourceHtml(disk) {
      return window.PortalI18n.html`<span>${esc(diskSummaryText(disk))}${disk?.refreshing ? window.PortalI18n.t(" · 采集中") : ""}</span>`;
    }

    function diskStageHtml(resource, name, title) {
      const stage = resource?.stages?.[name];
      if (!stage) return "";
      const labels = { pending: window.PortalI18n.t("等待采集"), running: window.PortalI18n.t("采集中"), ok: window.PortalI18n.t("已完成"), partial: window.PortalI18n.t("部分完成"), error: window.PortalI18n.t("失败"), skipped: window.PortalI18n.t("未执行") };
      const detail = [stage.error || stage.reason, formatDateTime(stage.updated_at)].filter(Boolean).join(" · ");
      return window.PortalI18n.html`<div class="agent-status-muted" data-disk-stage="${esc(name)}" data-stage-status="${esc(stage.status)}">
    ${esc(title)} · ${esc(labels[stage.status] || stage.status)}${detail ? ` · ${esc(detail)}` : ""}
  </div>`;
    }

    function diskRowHtml(item) {
      const percent = Number(item?.used_percent || 0);
      const mounts = Array.isArray(item?.mounts) ? item.mounts.filter(Boolean) : item?.mount ? [item.mount] : [];
      const mountText = mounts.length ? `mounted at ${mounts.join(", ")}` : "";
      const title = item?.volume_label || item?.filesystem || item?.path || item?.mount || "volume";
      const meta = [
        mountText,
        item?.fstype || "",
        `${formatBytes(item?.available_bytes)} free`,
      ].filter(Boolean).join(" · ");
      return window.PortalI18n.html`
    <div class="agent-status-gpu-row">
      <div class="agent-status-gpu-title">
        <span>${esc(title)}</span>
        <span>${esc(item?.status || "ok")} · ${Number.isFinite(percent) ? percent.toFixed(1) : "0.0"}%</span>
      </div>
      <div class="agent-status-progress" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, percent))}%"></span></div>
      <div class="agent-status-gpu-meta">${esc(meta)}</div>
    </div>`;
    }

    function diskTargetHtml(item) {
      const label = [item?.label, item?.model, item?.mount].filter(Boolean).join(" · ");
      return window.PortalI18n.html`
    <div class="agent-status-gpu-process">
      <strong>${esc(label || window.PortalI18n.t("External disk"))}</strong>
      <div class="agent-status-gpu-command">${esc(item?.mount || "")}${item?.available_bytes ? ` · ${formatBytes(item.available_bytes)} free` : ""}</div>
    </div>`;
    }

    function diskTriageFindingHtml(item) {
      const rows = Array.isArray(item?.items) ? item.items.slice(0, 5) : [];
      return window.PortalI18n.html`
    <div class="agent-status-gpu-process">
      <strong>${esc(item?.title || "Finding")}</strong>
      ${item?.detail ? window.PortalI18n.html`<div class="agent-status-gpu-command">${esc(item.detail)}</div>` : ""}
      ${rows.map(row => window.PortalI18n.html`<div class="agent-status-gpu-command">- ${esc(row.label || "")}${row.detail ? ` · ${esc(row.detail)}` : ""}</div>`).join("")}
    </div>`;
    }

    function diskTriageHtml(resource) {
      const triage = resource?.triage && typeof resource.triage === "object" ? resource.triage : {};
      const findings = Array.isArray(triage.findings) ? triage.findings : [];
      const recommendations = Array.isArray(triage.recommendations) ? triage.recommendations : [];
      const errors = Array.isArray(triage.errors) ? triage.errors : [];
      const checks = [["deleted_open_files", "已删除仍占用"], ["journal_usage", "系统日志"], ["docker_usage", "容器存储"], ["large_files", "大文件扫描"]];
      const states = checks.map(([name]) => resource?.stages?.[name]?.status).filter(Boolean);
      const emptyText = states.some(state => ["pending", "running"].includes(state))
        ? window.PortalI18n.t("诊断采集中，结果将逐段补充。")
        : states.some(state => ["error", "partial"].includes(state))
          ? window.PortalI18n.t("诊断未完整取得，请查看各段错误。")
          : !states.length || states.every(state => state === "skipped")
            ? window.PortalI18n.t("按需诊断尚未执行。")
            : window.PortalI18n.t("No quick cleanup candidate detected.");
      return window.PortalI18n.html`
    <div class="agent-status-gpu-row">
      <div class="agent-status-gpu-title"><span>Lightweight triage</span><span>${findings.length}</span></div>
      ${checks.map(([name, title]) => diskStageHtml(resource, name, title)).join("")}
      ${findings.length ? findings.map(diskTriageFindingHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">${esc(emptyText)}</div>`}
      ${recommendations.length ? window.PortalI18n.html`<div class="agent-status-gpu-process"><strong>Optimization plan</strong>${recommendations.map(item => window.PortalI18n.html`<div class="agent-status-gpu-command">- ${esc(item)}</div>`).join("")}</div>` : ""}
      ${errors.length ? window.PortalI18n.html`<div class="agent-status-muted">Skipped: ${esc(errors.join("; "))}</div>` : ""}
    </div>`;
    }

    function diskSingleResourceHtml(resource) {
      const checked = formatDateTime(resource?.updated_at);
      const rows = Array.isArray(resource?.filesystems) ? resource.filesystems : [];
      const targets = Array.isArray(resource?.transfer_targets) ? resource.transfer_targets : [];
      const suggestions = Array.isArray(resource?.suggestions) ? resource.suggestions : [];
      const statusText = resource?.status === "loading" ? "loading" : resource?.status === "error" ? "unavailable" : resource?.status || window.PortalI18n.t("not checked");
      return window.PortalI18n.html`
    <div class="agent-status-gpu-resource-card" data-disk-resource="${esc(resource?.resource_id || "")}">
      <div class="agent-status-gpu-head">
        <span>${esc(resource?.display_name || resource?.resource_id || window.PortalI18n.t("Disk resource"))}</span>
        <span class="agent-status-muted">${esc(statusText)}${resource?.stale ? window.PortalI18n.t(" · 旧快照") : ""}${resource?.refreshing ? window.PortalI18n.t(" · 采集中") : ""}${checked ? ` · ${esc(checked)}` : ""}</span>
      </div>
      ${resource?.error ? window.PortalI18n.html`<div class="agent-status-error">${esc(resource.error)}</div>` : ""}
      ${resource?.refresh_error ? window.PortalI18n.html`<div class="agent-status-error">${esc(resource.refresh_error)}</div>` : ""}
      ${resource?.warning ? window.PortalI18n.html`<div class="agent-status-muted">${esc(resource.warning)}</div>` : ""}
      ${diskStageHtml(resource, "capacity", window.PortalI18n.t("文件系统容量"))}
      ${rows.length && ["pending", "running"].includes(resource?.stages?.capacity?.status) ? window.PortalI18n.html`<div class="agent-status-muted">显示上次容量，正在更新。</div>` : ""}
      <div class="agent-status-gpu-grid">${rows.length ? rows.map(diskRowHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">No filesystem data.</div>`}</div>
      <div class="agent-status-gpu-row">
        <div class="agent-status-gpu-title"><span>External targets</span><span>${targets.length}</span></div>
        ${diskStageHtml(resource, "transfer_targets", window.PortalI18n.t("外接目标"))}
        ${targets.length ? targets.map(diskTargetHtml).join("") : resource?.stages?.transfer_targets?.status === "ok" ? window.PortalI18n.html`<div class="agent-status-muted">No mounted external disk detected.</div>` : ""}
      </div>
      ${diskTriageHtml(resource)}
      ${suggestions.length || resource?.stages?.suggestions ? window.PortalI18n.html`<div class="agent-status-gpu-row">
        <div class="agent-status-gpu-title"><span>Lazy transfer suggestions</span><span>${suggestions.length}</span></div>
        ${diskStageHtml(resource, "suggestions", window.PortalI18n.t("迁移建议"))}
        ${suggestions.map(item => window.PortalI18n.html`<div class="agent-status-gpu-process">${esc(item)}</div>`).join("")}
      </div>` : ""}
    </div>`;
    }

    function diskDetailsHtml(disk) {
      const resources = Array.isArray(disk?.resources) ? disk.resources : [];
      if (!disk || (disk.status === "loading" && !resources.length)) {
        return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-muted">Disk status is loading.</div></div>`;
      }
      if (resources.length) {
        const checked = formatDateTime(disk.updated_at);
        return window.PortalI18n.html`
      <div class="agent-status-gpu-card">
        ${resourceRefreshWarningHtml(disk, "Disk")}
        <div class="agent-status-gpu-head">
          <span>${esc(diskSummaryText(disk))}</span>
          <span class="agent-status-muted">${checked ? `Checked ${esc(checked)}` : window.PortalI18n.t("Click refreshes once")}</span>
        </div>
        <div class="agent-status-gpu-resource-list">${resources.map(diskSingleResourceHtml).join("")}</div>
      </div>`;
      }
      return window.PortalI18n.html`<div class="agent-status-gpu-card">${resourceRefreshWarningHtml(disk, "Disk")}${diskSingleResourceHtml(disk)}</div>`;
    }

    return Object.freeze({
      gpuResourceHtml, gpuDetailsHtml, diskResourceHtml, diskDetailsHtml,
    });
  }

  window.PortalHomeSystemResources = Object.freeze({ createSystemResources, dependencies: DEPENDENCIES });
})();
