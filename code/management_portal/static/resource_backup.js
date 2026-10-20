(function () {
  "use strict";

  const elements = {
    content: document.getElementById("resourceBackupContent"),
    meta: document.getElementById("resourceBackupMeta"),
    query: document.getElementById("resourceBackupQuery"),
    refresh: document.getElementById("resourceBackupRefresh"),
    runCritical: document.getElementById("resourceBackupCritical"),
    runContent: document.getElementById("resourceBackupRun"),
    verify: document.getElementById("resourceBackupVerify"),
    detail: document.getElementById("resourceBackupRunDetail"),
    detailTitle: document.getElementById("resourceBackupRunTitle"),
    detailBody: document.getElementById("resourceBackupRunBody"),
    detailClose: document.getElementById("resourceBackupRunClose"),
  };

  if (!elements.content) return;

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const state = {
    overview: null,
    query: "",
    loading: true,
    error: "",
  };
  const detailDialog = window.PortalUI.createDialogController({
    modal: elements.detail,
    backdropClose: true,
    escapeClose: true,
  });
  const sharedFetcher = window.PortalRefreshCoordinator.createSharedFetcher(
    window.PortalCompatibility.identifier("backup_refresh_channel"),
  );

  const STATUS_LABELS = Object.freeze({
    healthy: window.PortalI18n.t("健康"),
    ready: window.PortalI18n.t("就绪"),
    idle: window.PortalI18n.t("空闲"),
    running: window.PortalI18n.t("正在同步"),
    backing_up: window.PortalI18n.t("正在备份"),
    preparing: window.PortalI18n.t("正在准备"),
    verifying: window.PortalI18n.t("正在校验"),
    queued: window.PortalI18n.t("已排队"),
    completed: window.PortalI18n.t("成功"),
    success: window.PortalI18n.t("成功"),
    protected: window.PortalI18n.t("已覆盖"),
    covered: window.PortalI18n.t("已覆盖"),
    degraded: window.PortalI18n.t("部分完成"),
    warning: window.PortalI18n.t("需关注"),
    setup_required: window.PortalI18n.t("待初始化"),
    incomplete: window.PortalI18n.t("不完整"),
    failed: window.PortalI18n.t("失败"),
    error: window.PortalI18n.t("异常"),
    skipped: window.PortalI18n.t("已跳过"),
    skipped_locked: window.PortalI18n.t("等待其他任务"),
    uncovered: window.PortalI18n.t("未覆盖"),
    unprotected: window.PortalI18n.t("未覆盖"),
    stale: window.PortalI18n.t("恢复点过期"),
    excluded: window.PortalI18n.t("按策略排除"),
  });

  function statusLabel(status, fallback = "") {
    const key = String(status || "").trim().toLowerCase();
    return STATUS_LABELS[key] || String(fallback || status || window.PortalI18n.t("未知"));
  }

  function statusTone(status) {
    const key = String(status || "").trim().toLowerCase();
    if (["healthy", "ready", "completed", "success", "covered", "protected"].includes(key)) return "success";
    if (["running", "backing_up", "preparing", "verifying", "queued"].includes(key)) return "running";
    if (["failed", "error", "incomplete"].includes(key)) return "danger";
    if (["warning", "degraded", "setup_required", "uncovered", "unprotected", "stale", "skipped", "skipped_locked"].includes(key)) return "warning";
    return "neutral";
  }

  function statusHtml(status, label = "") {
    return window.PortalUIPrimitives.statusHtml(status, {
      label: String(label || statusLabel(status)),
      tone: statusTone(status),
    });
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function formatBytes(value) {
    return window.PortalUI.formatBytes(value, {
      emptyFallback: "—",
      invalidFallback: "—",
    });
  }

  function formatDate(value, fallback = "—") {
    if (!value) return fallback;
    return window.PortalUI.formatDateTime(value, { style: "status" }) || fallback;
  }

  function formatDuration(value) {
    const total = Number(value);
    if (!Number.isFinite(total) || total < 0) return "—";
    if (total < 60) return `${Math.round(total)} 秒`;
    if (total < 3600) return `${Math.round(total / 60)} 分钟`;
    const hours = Math.floor(total / 3600);
    const minutes = Math.round((total % 3600) / 60);
    return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
  }

  function filteredItems(items, fields) {
    const query = state.query.trim().toLocaleLowerCase();
    if (!query) return items;
    return items.filter(item => fields.some(field => (
      String(item?.[field] || "").toLocaleLowerCase().includes(query)
    )));
  }

  function progressOf(run) {
    const progress = run?.progress || {};
    const direct = Number(progress.percent ?? run?.progress_percent);
    if (Number.isFinite(direct)) return Math.max(0, Math.min(100, direct));
    const done = Number(progress.bytes_done ?? progress.items_done);
    const total = Number(progress.bytes_total ?? progress.items_total);
    return Number.isFinite(done) && Number.isFinite(total) && total > 0
      ? Math.max(0, Math.min(100, (done / total) * 100))
      : 0;
  }

  function renderActiveRun(run) {
    if (!run) return "";
    const profile = window.PortalI18n.t(run.profile_label || run.profile || run.kind || "备份任务");
    const phase = statusLabel(run.phase || run.status, "正在运行");
    const progress = progressOf(run);
    const progressLabel = progress > 0 ? `${Math.round(progress)}%` : window.PortalI18n.t("进行中");
    return window.PortalI18n.html`
      <section class="resourceBackupPanel" aria-label="当前运行">
        <div class="resourceBackupPanelHeader">
          <div class="resourceBackupPanelHeading">
            <h2>当前运行</h2>
            <p>后台执行，不会阻塞 Resource 页面。</p>
          </div>
          ${statusHtml(run.status || "running")}
        </div>
        <div class="resourceBackupPanelBody resourceBackupActive">
          <span class="resourceBackupPulse" aria-hidden="true"></span>
          <div class="resourceBackupActiveMain">
            <strong>${esc(profile)}</strong>
            <span>${esc(phase)} · 开始于 ${esc(formatDate(run.started_at))}</span>
          </div>
          <strong>${esc(progressLabel)}</strong>
          <div class="resourceBackupProgress" aria-label="备份进度 ${esc(progressLabel)}">
            <span style="--backup-progress: ${progress || 18}%"></span>
          </div>
        </div>
      </section>`;
  }

  function renderProfiles(profiles) {
    if (!profiles.length) {
      return window.PortalUIPrimitives.asyncStateHtml({
        state: "empty",
        density: "compact",
        title: window.PortalI18n.t("尚未配置同步策略"),
        message: window.PortalI18n.t("备份服务完成初始化后，这里会显示计划任务。"),
      });
    }
    return window.PortalI18n.html`<div class="resourceBackupPlanGrid">${profiles.map(profile => window.PortalI18n.html`
      <article class="resourceBackupPlan">
        <div class="resourceBackupPlanTop">
          <strong>${esc(profile.label || profile.name || profile.id || window.PortalI18n.t("备份计划"))}</strong>
          ${statusHtml(profile.status || "ready")}
        </div>
        <p>${esc(profile.description || window.PortalI18n.t("按计划生成加密、增量的 Resource 快照。"))}</p>
        <div class="resourceBackupPlanMeta">
          <span>${esc(profile.calendar || profile.schedule || window.PortalI18n.t("手动"))}</span>
          <span>下次 ${esc(formatDate(profile.next_run_at, window.PortalI18n.t("待调度")))}</span>
        </div>
      </article>`).join("")}</div>`;
  }

  function renderCoverage(coverage) {
    const filtered = filteredItems(coverage, [
      "source_id", "id", "label", "description", "tier", "status",
    ]);
    if (!filtered.length) {
      return window.PortalUIPrimitives.asyncStateHtml({
        state: "empty",
        density: "compact",
        title: state.query ? window.PortalI18n.t("没有匹配的覆盖域") : window.PortalI18n.t("尚未生成覆盖清单"),
        message: state.query ? window.PortalI18n.t("换一个关键词试试。") : window.PortalI18n.t("备份服务完成一次 inventory 后会显示。"),
      });
    }
    return window.PortalI18n.html`<div class="resourceBackupCoverageGrid">${filtered.map(item => {
      const pathCount = Number(item.path_count ?? asArray(item.paths).length);
      const tier = item.tier_label || ({
        critical: window.PortalI18n.t("关键状态"),
        content: "完整内容",
        external: window.PortalI18n.t("外部介质"),
      }[String(item.tier || "").toLowerCase()]) || item.tier || window.PortalI18n.t("Resource");
      const scope = Number.isFinite(pathCount) && pathCount > 0
        ? window.PortalI18n.text`${tier} · ${pathCount} 个路径`
        : tier;
      const restorePoint = item.latest_snapshot_at
        ? formatDate(item.latest_snapshot_at)
        : ["excluded", "skipped"].includes(String(item.status || "").toLowerCase())
          ? window.PortalI18n.t("不生成恢复点")
          : window.PortalI18n.t("等待首份快照");
      return window.PortalI18n.html`
        <article class="resourceBackupCoverage">
          <div class="resourceBackupCoverageIdentity">
            <strong>${esc(item.label || item.name || item.source_id || item.id || window.PortalI18n.t("数据域"))}</strong>
            <span>${esc(scope)}</span>
          </div>
          <p>${esc(item.description || window.PortalI18n.t("已纳入 Resource 恢复清单。"))}</p>
          <div class="resourceBackupCoverageSnapshot">
            <span>最近恢复点</span>
            <strong>${esc(restorePoint)}</strong>
          </div>
          ${statusHtml(item.status || "covered")}
        </article>`;
    }).join("")}</div>`;
  }

  function renderRuns(runs) {
    const filtered = filteredItems(runs, [
      "run_id", "profile", "profile_label", "kind", "status", "summary", "snapshot_id",
    ]);
    if (!filtered.length) {
      return window.PortalI18n.html`<div class="resourceBackupRunEmpty">${state.query ? window.PortalI18n.t("没有匹配的运行记录。") : window.PortalI18n.t("首轮备份完成后，运行记录会出现在这里。")}</div>`;
    }
    return window.PortalI18n.html`<div class="resourceBackupRunList">${filtered.slice(0, 12).map(run => {
      const runId = String(run.run_id || "");
      const profile = window.PortalI18n.t(run.profile_label || run.profile || run.kind || "备份");
      const summary = window.PortalI18n.t(run.summary || run.phase || statusLabel(run.status));
      const when = run.finished_at || run.started_at;
      return window.PortalI18n.html`
        <button class="resourceBackupRun" type="button" data-backup-run-id="${esc(runId)}">
          <span class="resourceBackupRunPrimary">
            <strong>${esc(profile)}</strong>
            <span>${esc(runId || window.PortalI18n.t("运行记录"))}</span>
          </span>
          <span class="resourceBackupRunMeta">
            <strong>${esc(summary)}</strong>
            <span>${run.snapshot_id ? `Snapshot ${esc(String(run.snapshot_id).slice(0, 12))}` : window.PortalI18n.t("尚无 Snapshot")}</span>
          </span>
          <span class="resourceBackupRunTime">${esc(formatDate(when))}</span>
          ${statusHtml(run.status || "completed")}
        </button>`;
    }).join("")}</div>`;
  }

  function renderWarnings(warnings) {
    if (!warnings.length) {
      return window.PortalI18n.html`<div class="resourceBackupRunEmpty">当前没有需要处理的告警。</div>`;
    }
    return window.PortalI18n.html`<div class="resourceBackupWarnings">${warnings.map((warning, index) => {
      const item = typeof warning === "string" ? { message: warning } : warning || {};
      return window.PortalI18n.html`
        <article class="resourceBackupWarning">
          <span class="resourceBackupWarningMark" aria-hidden="true">!</span>
          <div>
            <strong>${esc(item.title || window.PortalI18n.text`备份提示 ${index + 1}`)}</strong>
            <p>${esc(item.message || item.description || window.PortalI18n.t("请检查备份配置。"))}</p>
          </div>
        </article>`;
    }).join("")}</div>`;
  }

  function render() {
    if (state.loading && !state.overview) {
      window.PortalUIPrimitives.renderAsyncState(elements.content, {
        state: "loading",
        title: window.PortalI18n.t("正在读取 Backup Center"),
        message: window.PortalI18n.t("正在核对备份库、定时策略和最近运行。"),
      });
      return;
    }
    if (state.error && !state.overview) {
      window.PortalUIPrimitives.renderAsyncState(elements.content, {
        state: "error",
        title: window.PortalI18n.t("无法读取 Backup Center"),
        message: state.error,
        actionLabel: "重试",
        onRetry: () => coordinator.refresh({ force: true }),
      });
      return;
    }

    const overview = state.overview;
    if (!overview) return;
    const health = overview.health;
    const target = overview.target;
    const profiles = overview.policy.profiles;
    const runs = overview.recent_runs;
    const warnings = overview.warnings;
    const activeRun = overview.active_run;
    const healthStatus = activeRun ? "running" : (health.status || overview.status || "setup_required");
    const latestSuccess = health.latest_success_at
      || profiles.map(item => item.last_success_at).filter(Boolean).sort().at(-1);
    const nextRun = profiles.map(item => item.next_run_at).filter(Boolean).sort().at(0);
    const repositorySizeRaw = target.repository_size_bytes;
    const repositorySize = Number(repositorySizeRaw) > 0 ? repositorySizeRaw : null;
    const freeBytes = target.free_bytes;
    const targetLabel = (target.label === "加密 Restic 仓库" ? window.PortalI18n.t(target.label) : target.label) || target.name || (target.path ? window.PortalI18n.t("本机 /data") : window.PortalI18n.t("尚未配置"));
    const healthTitle = window.PortalI18n.t(health.label || statusLabel(healthStatus));
    const healthSummary = window.PortalI18n.t(health.summary)
      || (healthStatus === "setup_required"
        ? window.PortalI18n.t("备份服务尚未完成初始化。完成后会自动生成关键状态与完整内容快照。")
        : window.PortalI18n.t("关键状态、Resource 内容和一致性数据库副本都由后台计划任务维护。"));
    const retention = overview.policy.retention;
    const verification = overview.policy.verification;
    const guide = overview.restore_guide;

    elements.content.innerHTML = window.PortalI18n.html`
      <section class="resourceBackupHero" aria-label="备份健康状态">
        <div class="resourceBackupHealth" data-status="${esc(healthStatus)}">
          <span class="resourceBackupHealthMark" aria-hidden="true" data-portal-icon="shield-check"></span>
          <div>
            <p class="resourceBackupEyebrow">Resource Backup Center</p>
            <h1>${esc(healthTitle)}</h1>
            <p>${esc(healthSummary)}</p>
          </div>
        </div>
        <dl class="resourceBackupHeroMetrics">
          <div class="resourceBackupHeroMetric">
            <dt>最近恢复点</dt>
            <dd>
              <strong>${esc(formatDate(latestSuccess, window.PortalI18n.t("等待首份快照")))}</strong>
              <small>${activeRun ? window.PortalI18n.t("新恢复点正在生成") : window.PortalI18n.t("已写入恢复清单")}</small>
            </dd>
          </div>
          <div class="resourceBackupHeroMetric">
            <dt>下次自动同步</dt>
            <dd>
              <strong>${esc(formatDate(nextRun, window.PortalI18n.t("等待调度")))}</strong>
              <small>${profiles.length ? window.PortalI18n.text`${profiles.length} 条自动策略生效` : window.PortalI18n.t("计划任务尚未启用")}</small>
            </dd>
          </div>
          <div class="resourceBackupHeroMetric">
            <dt>加密仓库</dt>
            <dd>
              <strong>${esc(formatBytes(repositorySize))}</strong>
              <small>${esc(targetLabel)}${Number.isFinite(Number(freeBytes)) ? window.PortalI18n.text` · 剩余 ${esc(formatBytes(freeBytes))}` : ""}</small>
            </dd>
          </div>
        </dl>
      </section>

      ${renderActiveRun(activeRun)}

      <div class="resourceBackupLayout">
        <div class="resourceBackupColumn resourceBackupPrimaryColumn">
          <section class="resourceBackupPanel resourceBackupProtection" aria-label="备份计划">
            <div class="resourceBackupPanelHeader">
              <div class="resourceBackupPanelHeading">
                <h2>备份计划</h2>
                <p>自动策略定义节奏，覆盖清单说明哪些数据能够恢复。</p>
              </div>
              <span class="resourceBackupPanelMeta">${profiles.length} 策略 · ${overview.coverage.length} 数据域</span>
            </div>
            <div class="resourceBackupProtectionBody">
              <section class="resourceBackupGroup" aria-label="自动同步策略">
                <div class="resourceBackupSubheading">
                  <div>
                    <h3>自动策略</h3>
                    <p>关键状态高频同步，完整内容错峰增量备份。</p>
                  </div>
                  <span>${profiles.length} 条</span>
                </div>
                ${renderProfiles(profiles)}
              </section>
              <section class="resourceBackupGroup" aria-label="数据覆盖">
                <div class="resourceBackupSubheading">
                  <div>
                    <h3>覆盖范围</h3>
                    <p>区分已覆盖、策略排除和需要异地介质的数据。</p>
                  </div>
                  <span>${overview.coverage.length} 个</span>
                </div>
                ${renderCoverage(overview.coverage)}
              </section>
            </div>
          </section>

          <section class="resourceBackupPanel resourceBackupRunsPanel" aria-label="最近运行">
            <div class="resourceBackupPanelHeader">
              <div class="resourceBackupPanelHeading">
                <h2>最近运行</h2>
                <p>点击一行查看 Snapshot、耗时和失败原因。</p>
              </div>
              <span class="resourceBackupPanelMeta">${runs.length} 条记录</span>
            </div>
            <div class="resourceBackupPanelBody">${renderRuns(runs)}</div>
          </section>
        </div>

        <aside class="resourceBackupColumn resourceBackupRail">
          <section class="resourceBackupPanel resourceBackupRepository" aria-label="仓库状态">
            <div class="resourceBackupPanelHeader">
              <div class="resourceBackupPanelHeading">
                <h2>仓库状态</h2>
                <p>本机快速恢复层，使用 Restic 加密和去重。</p>
              </div>
              ${statusHtml(target.status || healthStatus)}
            </div>
            <div class="resourceBackupPanelBody">
              <dl class="resourceBackupDetailGrid resourceBackupRepositoryGrid">
                <div><dt>目标</dt><dd>${esc(targetLabel)}</dd></div>
                <div><dt>加密</dt><dd>${target.encrypted === false ? window.PortalI18n.t("未加密") : window.PortalI18n.t("Restic AES-256")}</dd></div>
                <div><dt>保留策略</dt><dd>${esc([["hourly", "H"], ["daily", "D"], ["weekly", "W"], ["monthly", "M"]].map(([key, unit]) => Number(retention[key]) > 0 ? `${retention[key]}${unit}` : "—").join(" / "))}</dd></div>
                <div><dt>最近校验</dt><dd>${esc(formatDate(verification.last_check_at, window.PortalI18n.t("等待首次校验")))}</dd></div>
              </dl>
            </div>
          </section>

          <section class="resourceBackupPanel resourceBackupAttention" aria-label="需要关注">
            <div class="resourceBackupPanelHeader">
              <div class="resourceBackupPanelHeading">
                <h2>需要关注</h2>
                <p>本机备份之外仍需处理的灾备缺口。</p>
              </div>
              ${warnings.length ? statusHtml("warning", window.PortalI18n.text`${warnings.length} 项`) : statusHtml("healthy", window.PortalI18n.t("无告警"))}
            </div>
            <div class="resourceBackupPanelBody">${renderWarnings(warnings)}</div>
          </section>

          <section class="resourceBackupPanel resourceBackupRecovery" aria-label="恢复指南">
            <div class="resourceBackupPanelHeader">
              <div class="resourceBackupPanelHeading">
                <h2>恢复指南</h2>
                <p>恢复是高风险操作，当前只提供经过确认的顺序。</p>
              </div>
            </div>
            <div class="resourceBackupPanelBody resourceBackupRestore">
              <p>Backup Center 不直接覆盖生产数据。需要恢复时，请先按知识库中的依赖顺序验证 Snapshot 和数据库。</p>
              ${guide.url ? window.PortalI18n.html`<a class="portalButton is-compact is-quiet" href="${esc(guide.url)}">
                打开恢复指南
              </a>` : ""}
            </div>
          </section>
        </aside>
      </div>`;

    elements.content.querySelectorAll("[data-backup-run-id]").forEach(button => {
      button.addEventListener("click", () => openRun(button.dataset.backupRunId));
    });
    syncToolbar(overview);
  }

  function syncToolbar(overview) {
    const permissions = overview.permissions || {};
    const busy = Boolean(overview.active_run);
    const setupRequired = String(overview.health?.status || overview.status) === "setup_required";
    elements.runCritical.disabled = permissions.can_run === false || busy || setupRequired;
    elements.runContent.disabled = permissions.can_run === false || busy || setupRequired;
    elements.verify.disabled = permissions.can_verify === false || busy || setupRequired;
    const generated = overview.generated_at || overview.updated_at;
    elements.meta.textContent = busy
      ? window.PortalI18n.t("后台任务运行中")
      : generated
        ? window.PortalI18n.text`更新于 ${formatDate(generated)}`
        : setupRequired
          ? window.PortalI18n.t("等待初始化")
          : window.PortalI18n.t("状态已同步");
  }

  function openRun(runId) {
    const run = state.overview?.recent_runs.find(item => (
      String(item.run_id || "") === String(runId || "")
    ));
    if (!run) return;
    elements.detailTitle.textContent = run.profile_label || run.profile || run.kind || window.PortalI18n.t("运行详情");
    elements.detailBody.innerHTML = window.PortalI18n.html`
      <dl class="resourceBackupDetailGrid">
        <div><dt>Run ID</dt><dd>${esc(run.run_id || "—")}</dd></div>
        <div><dt>状态</dt><dd>${esc(statusLabel(run.status))}</dd></div>
        <div><dt>开始时间</dt><dd>${esc(formatDate(run.started_at))}</dd></div>
        <div><dt>结束时间</dt><dd>${esc(formatDate(run.finished_at))}</dd></div>
        <div><dt>耗时</dt><dd>${esc(formatDuration(run.duration_seconds))}</dd></div>
        <div><dt>Snapshot</dt><dd>${esc(run.snapshot_id || "—")}</dd></div>
        <div><dt>备份摘要</dt><dd>${esc(run.summary || run.phase || "—")}</dd></div>
        <div><dt>错误</dt><dd>${esc(run.error || window.PortalI18n.t("无"))}</dd></div>
      </dl>`;
    detailDialog.open({ focusTarget: elements.detailClose });
  }

  async function requestJson(url, options = {}) {
    return window.PortalAPI.request(url, options);
  }

  async function fetchOverview({ force = false, signal } = {}) {
    return sharedFetcher.fetch(
      "resource-backup:overview",
      () => requestJson("/api/resource-backup/overview", { signal }),
      { maxAgeMs: force ? 0 : 900, dedupeWindowMs: force ? 100 : 1200 },
    );
  }

  const coordinator = window.PortalRefreshCoordinator.create({
    key: "resource-backup:overview",
    interval: 3000,
    hiddenInterval: 60000,
    pauseWhenHidden: false,
    shouldRefresh: () => !!state.overview?.active_run,
    fetch: fetchOverview,
    apply: payload => {
      state.overview = payload;
      state.loading = false;
      state.error = "";
      render();
    },
    onError: error => {
      state.loading = false;
      state.error = error?.message || "读取状态失败。";
      if (!state.overview) render();
      else {
        elements.meta.textContent = window.PortalI18n.t("状态刷新失败");
        window.PortalUI.toast(window.PortalI18n.text`备份状态刷新失败：${state.error}`, "error", {
          key: "resource-backup-refresh-error",
        });
      }
    },
  });

  async function runAction(kind, button) {
    const isContent = kind === "content";
    if (isContent) {
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.t("立即执行完整备份？"),
        message: window.PortalI18n.t("完整备份会扫描 Resource 内容、Git 工作区和 Resident 会话。"),
        description: window.PortalI18n.t("任务会在后台执行；大目录只做增量上传，但首次运行可能需要较长时间。"),
        confirmLabel: window.PortalI18n.t("开始完整备份"),
        tone: "primary",
        metadata: [
          { label: window.PortalI18n.t("目标"), value: state.overview?.target?.label || "本机 /data" },
          { label: window.PortalI18n.t("模式"), value: "增量、加密、一致性数据库副本" },
        ],
      });
      if (!approved) return;
    }
    return window.PortalUI.runMutation({
      key: `resource-backup:run:${kind}`,
      button,
      busyLabel: "正在提交…",
      action: "启动备份",
      run: () => requestJson("/api/resource-backup/runs", {
        method: "POST",
        body: JSON.stringify({ profile: kind }),
      }),
      onSuccess: async () => {
        window.PortalUI.toast(
          kind === "critical" ? window.PortalI18n.t("关键状态备份已进入后台队列。") : window.PortalI18n.t("完整 Resource 备份已进入后台队列。"),
          "success",
        );
        await coordinator.refresh({ force: true });
      },
      retry: false,
    });
  }

  async function runVerification() {
    return window.PortalUI.runMutation({
      key: "resource-backup:verify:check",
      button: elements.verify,
      busyLabel: "正在提交…",
      action: "启动校验",
      run: () => requestJson("/api/resource-backup/verifications", {
        method: "POST",
        body: JSON.stringify({ mode: "check" }),
      }),
      onSuccess: async () => {
        window.PortalUI.toast(window.PortalI18n.t("仓库校验已进入后台队列。"), "success");
        await coordinator.refresh({ force: true });
      },
      retry: false,
    });
  }

  elements.query.addEventListener("input", () => {
    state.query = elements.query.value;
    if (state.overview) render();
  });
  elements.refresh.addEventListener("click", () => window.PortalUI.runMutation({
    key: "resource-backup:refresh",
    button: elements.refresh,
    busyLabel: "刷新中…",
    action: "刷新",
    run: () => coordinator.refresh({ force: true }),
    successMessage: "备份状态已刷新。",
    retry: false,
  }));
  elements.runCritical.addEventListener("click", () => runAction("critical", elements.runCritical));
  elements.runContent.addEventListener("click", () => runAction("content", elements.runContent));
  elements.verify.addEventListener("click", runVerification);
  elements.detailClose.addEventListener("click", () => detailDialog.close());

  window.PortalNav?.mount?.();
  window.PortalToolbar?.mountAll?.();
  let identityReady = false;
  let disposed = false;
  const identityEvent = window.PortalIdentity?.eventName || "portal:identity-changed";
  const reloadForIdentity = () => {
    if (!identityReady || disposed) return;
    coordinator.stop();
    state.overview = null;
    state.loading = true;
    state.error = "";
    state.query = "";
    elements.query.value = "";
    elements.meta.textContent = window.PortalI18n.t("正在读取…");
    elements.runCritical.disabled = true;
    elements.runContent.disabled = true;
    elements.verify.disabled = true;
    detailDialog.close();
    elements.detailTitle.textContent = "";
    elements.detailBody.replaceChildren();
    render();
    coordinator.start();
    coordinator.refresh({ force: true }).catch(() => {});
  };
  window.addEventListener(identityEvent, reloadForIdentity);
  window.PortalUI.loadCurrentUser().then(() => {
    identityReady = true;
    reloadForIdentity();
  });
  window.addEventListener("pagehide", event => {
    if (event.persisted) return;
    disposed = true;
    window.removeEventListener(identityEvent, reloadForIdentity);
    coordinator.destroy();
  }, { once: true });
})();
