(() => {
  "use strict";

  const modules = window.PortalLineageWorkbenchModules ||= {};

  modules.createWatchboard = function createWatchboard({
    state,
    getChatElement,
    getElementById: $id,
    escapeHtml: esc,
    portalUi,
    applyLayout: applyWatchboardLayout,
    bindSplitter: bindWatchboardSplitter,
    currentResident,
    loadFolder,
    residentService,
    selectedResidentId,
    submitChatPrompt,
    writeUiState: writeLineageUiState,
  }) {
      const watchboardApplications = window.PortalWatchboardApplications.create({
        escapeHtml: esc, ui: portalUi, api: residentService,
        view: {
          dashboard: watchboardPayload, residentId: selectedResidentId,
          render: () => renderWatchboard(state.detail || {}), accept: mergeWatchboardResponse,
          refresh: scheduleWatchboardRefresh, reload: refreshWatchboard,
          get collapsed() { return !!state.watchboardCollapsed; },
          get username() { return state.me || ""; },
        },
        commands: {
          run: runWatchboardCommand, isActive: watchboardActiveRun,
          get running() { return state.watchboardRunInFlight || ""; },
          get arguments() { return state.watchboardRunArgs || {}; },
        },
        modal: { open: showWatchboardModal, close: closeWatchboardModal },
        chat: { submit: submitChatPrompt },
        components: {
          generic: watchboardGenericHtml, statusLabel: watchboardStatusLabel,
          jsonPreview: watchboardJsonPreview, latestRuns: watchboardGenericLatestRunHtml,
        },
      });
      let loadedWatchboard = null;
      function watchboardPayload(detail = state.detail || {}) {
        const dashboard = detail?.watchboard || {};
        const residentId = selectedResidentId();
        if (loadedWatchboard?.resident_id !== residentId) loadedWatchboard = null;
        if (!dashboard.enabled || (dashboard.resident_id && dashboard.resident_id !== residentId)) {
          loadedWatchboard = null;
          return null;
        }
        // Chat projections describe installation, not the loaded extension.
        // Keep this view's full payload across lightweight Resident updates.
        if (Object.prototype.hasOwnProperty.call(dashboard, "signature")) {
          loadedWatchboard = dashboard;
        }
        return loadedWatchboard || dashboard;
      }

      function mergeWatchboardResponse(payload = {}) {
        const watchboard = payload?.watchboard;
        if (!watchboard || watchboardDestroyed || String(watchboard.resident_id || payload.resident_id || "") !== selectedResidentId()) return null;
        state.detail = {
          ...(state.detail || {}),
          watchboard,
          ...(payload?.watchboard_capabilities ? { watchboard_capabilities: payload.watchboard_capabilities } : {}),
        };
        return watchboard;
      }

      const WATCHBOARD_ACTIVE_POLL_MS = 2000;
      const WATCHBOARD_INITIAL_LOAD_ATTEMPTS = 3;
      let watchboardDestroyed = false;
      let watchboardRefreshOwner = "";
      let watchboardLoadFailures = 0;
      let watchboardLoadError = "";
      const watchboardRefresh = window.PortalRefreshCoordinator.create({
        key: selectedResidentId,
        initialDelay: WATCHBOARD_ACTIVE_POLL_MS,
        interval: WATCHBOARD_ACTIVE_POLL_MS,
        shouldRefresh: () => watchboardActiveRun(watchboardPayload(state.detail))
          || (!loadedWatchboard && watchboardLoadFailures < WATCHBOARD_INITIAL_LOAD_ATTEMPTS),
        fetch: ({ key }) => residentService.watchboard(key),
        apply: payload => {
          watchboardLoadFailures = 0;
          watchboardLoadError = "";
          if (mergeWatchboardResponse(payload)) renderWatchboard(state.detail);
        },
        onError: error => {
          watchboardLoadFailures += 1;
          watchboardLoadError = portalUi.errorMessage(window.PortalI18n.t("加载 Watchboard"), error);
          renderWatchboard(state.detail);
        },
      });

      function setWatchboardRefreshOwner(residentId) {
        if (watchboardDestroyed) return "";
        const nextOwner = String(residentId || "").trim();
        const changed = nextOwner !== watchboardRefreshOwner;
        watchboardRefreshOwner = nextOwner;
        if (changed) {
          watchboardEdge?.close();
          watchboardApplications.reset();
          closeWatchboardModal();
          watchboardLoadFailures = 0;
          watchboardLoadError = "";
        }
        if (nextOwner) {
          watchboardRefresh.start();
          // Initial/open loads are required even while the extension is idle;
          // only subsequent periodic polling depends on an active command.
          if (changed) watchboardRefresh.invalidate({ delay: 0 });
        } else {
          watchboardRefresh.stop();
        }
        return nextOwner;
      }

      function scheduleWatchboardRefresh(delayMs = 0) {
        const residentId = setWatchboardRefreshOwner(selectedResidentId());
        if (watchboardDestroyed || !residentId) return;
        watchboardRefresh.invalidate({ delay: delayMs });
      }

      function destroyWatchboard() {
        if (watchboardDestroyed) return;
        watchboardDestroyed = true;
        watchboardEdge?.destroy();
        watchboardEdge = null;
        watchboardRefresh.destroy();
        watchboardApplications.destroy();
        closeWatchboardModal();
        window.removeEventListener("pagehide", handleWatchboardPageHide);
      }

      function handleWatchboardPageHide(event) {
        if (!event.persisted) destroyWatchboard();
      }

      window.addEventListener("pagehide", handleWatchboardPageHide);

      function watchboardStatusLabel(statusPayload = {}) {
        const stateText = String(statusPayload.state || statusPayload.status || "").trim();
        return stateText || "unknown";
      }

      function watchboardJsonPreview(value) {
        try {
          return JSON.stringify(value || {}, null, 2);
        } catch {
          return "{}";
        }
      }

      function watchboardGenericManifest(dashboard) {
        return dashboard?.manifest?.payload || {};
      }

      function watchboardGenericTitle(dashboard) {
        const manifest = watchboardGenericManifest(dashboard);
        const viewManifest = dashboard?.view_manifest?.payload || {};
        const resident = currentResident() || state.detail?.resident || {};
        const scope = String(dashboard?.dashboard_scope || "").trim();
        const forkLabel = String(dashboard?.fork?.label || "").trim();
        if (scope === "fork_template" && forkLabel) return forkLabel;
        return String(viewManifest.title || manifest.title || manifest.name || resident.title || resident.resident_id || "Watchboard").trim();
      }

      function watchboardGenericDescription(dashboard) {
        const manifest = watchboardGenericManifest(dashboard);
        const viewManifest = dashboard?.view_manifest?.payload || {};
        const statusPayload = dashboard?.status?.payload || {};
        const resident = currentResident() || state.detail?.resident || {};
        const candidates = [
          viewManifest.description,
          manifest.description,
          statusPayload.summary,
          statusPayload.description,
          resident.description,
        ].map(value => String(value || "").trim()).filter(Boolean);
        return candidates[0] || window.PortalI18n.t("尚未在 agent.yaml 中填写功能定位。");
      }

      function watchboardPrimaryConfig(dashboard) {
        const configs = Array.isArray(dashboard?.configs) ? dashboard.configs : [];
        return configs.find(item => item?.rel_path) || configs[0] || {};
      }

      function watchboardGenericCommandLabel(command) {
        const id = String(command?.id || "").trim();
        if (id === "run-once") return window.PortalI18n.t("Run once");
        if (id === "status") return window.PortalI18n.t("刷新状态");
        return String(command?.label || id || window.PortalI18n.t("Run")).trim();
      }

      function watchboardGenericRunButtons(commands, canRun) {
        const running = state.watchboardRunInFlight || "";
        const runOnce = commands.find(command => String(command.id || "") === "run-once");
        const statusCommand = commands.find(command => String(command.id || "") === "status");
        const businessCommands = commands.filter(command => !["run-once", "status"].includes(String(command.id || "")));
        const ordered = [runOnce, ...businessCommands, statusCommand].filter(Boolean).slice(0, 8);
        if (!ordered.length) return window.PortalI18n.html`<div class="paneNote">agent.yaml 中还没有声明可运行命令。</div>`;
        return window.PortalI18n.html`<div class="watchboardMiniActions">
          ${ordered.map((command, index) => {
            const commandId = String(command.id || "");
            const title = [command.description, command.risk, command.requires_args ? window.PortalI18n.t("需要参数") : ""].filter(Boolean).join(" · ");
            const primary = commandId === "run-once" || (!runOnce && index === 0);
            return window.PortalI18n.html`<button class="portalButton is-compact watchboardCommandBtn ${primary ? "is-primary" : ""}" type="button" data-watchboard-run-command="${esc(commandId)}" title="${esc(title)}" ${canRun && !running ? "" : "disabled"}>${esc(running === commandId ? "Running" : watchboardGenericCommandLabel(command))}</button>`;
          }).join("")}
        </div>`;
      }

      function watchboardGenericLatestRunHtml(runs = []) {
        const latestRun = runs[0] || {};
        if (!latestRun.run_id) return window.PortalI18n.html`<div class="paneNote">暂无 run 记录。</div>`;
        return window.PortalI18n.html`<div class="watchboardRunItem">
          <span class="watchboardRunStatus ${latestRun.status === "failed" ? "failed" : ""}">${esc(latestRun.status || "-")}</span>
          <span> · ${esc(latestRun.command || "")}</span>
          <div>${esc(latestRun.finished_at || latestRun.updated_at || latestRun.created_at || "")}</div>
          ${latestRun.error ? window.PortalI18n.html`<div class="consoleError">${esc(latestRun.error)}</div>` : ""}
          ${latestRun.script_run_id ? window.PortalI18n.html`<button type="button" data-watchboard-run-output="${esc(latestRun.script_run_id)}">输出</button>
            ${["queued", "running"].includes(latestRun.status) ? window.PortalI18n.html`<button type="button" data-watchboard-run-cancel="${esc(latestRun.script_run_id)}">取消</button>` : ""}` : ""}
        </div>`;
      }

      function watchboardActiveRun(dashboard) {
        const runs = Array.isArray(dashboard?.recent_runs) ? dashboard.recent_runs : [];
        return runs.find(item => {
          const status = String(item.status || "").toLowerCase();
          return status === "queued" || status === "dispatching" || status === "running";
        }) || null;
      }

      function watchboardGenericHtml(dashboard, extraHtml = "") {
        if (!dashboard?.enabled) return "";

        const collapsed = !!state.watchboardCollapsed;
        const manifest = dashboard.manifest || {};
        const statusPayload = dashboard.status?.payload || {};
        const statusError = dashboard.status?.error || "";
        const commands = Array.isArray(dashboard.commands) ? dashboard.commands : [];
        const runs = Array.isArray(dashboard.recent_runs) ? dashboard.recent_runs : [];
        const canRun = !!dashboard.permissions?.can_run;
        const config = watchboardPrimaryConfig(dashboard);
        const configPath = String(config.rel_path || "");
        const title = watchboardGenericTitle(dashboard);
        const description = watchboardGenericDescription(dashboard);
        const statusText = watchboardStatusLabel(statusPayload);
        const commandHtml = watchboardGenericRunButtons(commands, canRun);
        const runHtml = watchboardGenericLatestRunHtml(runs);

        const fullConfigButton = configPath
          ? window.PortalI18n.html`<button class="portalButton is-compact watchboardCommandBtn" type="button" data-watchboard-config-open="${esc(configPath)}">Full config</button>`
          : window.PortalI18n.html`<button class="portalButton is-compact watchboardCommandBtn" type="button" disabled>Full config</button>`;
        return window.PortalI18n.html`
          <div class="watchboardHead">
            <button class="portalButton is-icon is-quiet iconBtn" type="button" data-watchboard-dashboard-toggle aria-label="${collapsed ? window.PortalI18n.t("展开 Watchboard") : window.PortalI18n.t("收起 Watchboard")}" title="${collapsed ? window.PortalI18n.t("展开") : window.PortalI18n.t("收起")}" data-portal-icon="${collapsed ? "chevron-left" : "chevron-right"}"></button>
            <div class="watchboardTitle">${esc(title)}</div>
          </div>
          <div class="watchboardBody">
            ${dashboard.application_error ? window.PortalI18n.html`<div class="paneNote consoleError">${esc(dashboard.application_error)}</div>` : ""}
            <section class="watchboardSection">
              <div class="watchboardSectionBody">
                <div class="watchboardHeroText">${esc(description)}</div>
              </div>
            </section>
            ${extraHtml || window.PortalI18n.html`<section class="watchboardSection">
              <div class="watchboardSectionHead"><span>Run Controls</span><span>${esc(statusText)}</span></div>
              <div class="watchboardSectionBody">
                ${statusError ? window.PortalI18n.html`<div class="paneNote consoleError">${esc(statusError)}</div>` : ""}
                ${commandHtml}
                <div class="watchboardMiniActions">
                  ${fullConfigButton}
                  <button class="portalButton is-compact watchboardCommandBtn" type="button" data-watchboard-status-open>Raw status</button>
                </div>
                ${runHtml}
              </div>
            </section>`}
            ${manifest.error ? window.PortalI18n.html`<section class="watchboardSection">
              <div class="watchboardSectionHead"><span>Manifest</span></div>
              <div class="watchboardSectionBody"><div class="paneNote consoleError">${esc(manifest.error)}</div></div>
            </section>` : ""}
          </div>`;
      }

      // Like the User Mailbox, a collapsed panel is revealed by hover or
      // keyboard focus; touch/click keeps it open until dismissed. This is
      // presentation state only, independent of the saved docked preference.
      let watchboardEdge = null;
      function bindWatchboardEdge(box, body) {
        if (watchboardEdge?.box === box) return watchboardEdge;
        watchboardEdge?.destroy();
        const events = new AbortController();
        const { signal } = events;
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "portalButton is-icon watchboardEdgeTrigger";
        trigger.setAttribute("aria-label", window.PortalI18n.t("展开 Watchboard"));
        trigger.title = window.PortalI18n.t("Watchboard · 悬停查看，点击保持展开");
        trigger.innerHTML = window.PortalUIPrimitives.iconHtml("chevron-left");
        if (box.id) trigger.setAttribute("aria-controls", box.id);
        body.insertBefore(trigger, box);
        let shown = false, held = false, closeTimer = 0, restoringFocus = false;
        const overlay = window.PortalOverlay.register(box, {
          kind: "popover", closeOnEscape: true,
          onRequestClose: event => {
            close();
            if (event?.key === "Escape") {
              restoringFocus = true;
              trigger.focus({ preventScroll: true });
              restoringFocus = false;
            }
          },
        });
        const contains = target => trigger.contains(target) || box.contains(target);
        const cancelClose = () => { clearTimeout(closeTimer); closeTimer = 0; };
        function setShown(value) {
          const next = !!value && !trigger.hidden && !!state.watchboardCollapsed;
          box.classList.toggle("peeking", next);
          box.inert = !!state.watchboardCollapsed && !next;
          trigger.setAttribute("aria-expanded", String(next));
          if (next === shown) return;
          shown = next;
          if (shown) overlay.activate();
          else overlay.deactivate();
        }
        function close() { cancelClose(); held = false; setShown(false); }
        function scheduleClose() {
          cancelClose();
          closeTimer = setTimeout(() => {
            const focused = document.activeElement;
            if (held || trigger.matches(":hover") || box.matches(":hover") || watchboardModalDialog
              || (contains(focused) && focused.matches(":focus-visible, input, textarea, select, [contenteditable=true]"))) return;
            setShown(false);
          }, 160);
        }
        for (const element of [trigger, box]) {
          element.addEventListener("pointerenter", event => {
            if (event.pointerType === "touch") return;
            cancelClose();
            setShown(true);
          }, { signal });
          element.addEventListener("pointerleave", scheduleClose, { signal });
          element.addEventListener("focusin", event => {
            if (!restoringFocus && event.target.matches(":focus-visible")) {
              cancelClose();
              setShown(true);
            }
          }, { signal });
          element.addEventListener("focusout", scheduleClose, { signal });
        }
        trigger.addEventListener("click", () => {
          held = !held;
          cancelClose();
          setShown(held);
        }, { signal });
        trigger.addEventListener("keydown", event => {
          if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
            event.preventDefault();
            setShown(true);
            box.querySelector("button:not(:disabled)")?.focus();
          }
        }, { signal });
        document.addEventListener("pointerdown", event => {
          if (!contains(event.target) && !watchboardModalDialog) close();
        }, { capture: true, signal });
        window.addEventListener("blur", close, { signal });
        watchboardEdge = { box, close, scheduleClose,
          sync(visible) {
            trigger.hidden = !visible || !state.watchboardCollapsed;
            if (trigger.hidden) close();
            box.inert = !visible || (!!state.watchboardCollapsed && !shown);
          },
          destroy() { close(); events.abort(); overlay.destroy(); trigger.remove(); },
        };
        return watchboardEdge;
      }

      function syncWatchboardFrame(dashboard) {
        const box = getChatElement("watchboard");
        const body = getChatElement("body");
        if (!box || !body) return false;
        const splitter = getChatElement("watchboardSplitter");
        body.classList.toggle("hasWatchboard", !!dashboard);
        splitter?.classList.toggle("open", !!dashboard && !state.watchboardCollapsed);
        box.classList.toggle("open", !!dashboard);
        box.classList.toggle("collapsed", !!dashboard && !!state.watchboardCollapsed);
        bindWatchboardEdge(box, body).sync(!!dashboard);
        if (dashboard) {
          bindWatchboardSplitter();
          applyWatchboardLayout();
        }
        return true;
      }

      function renderWatchboard(detail = state.detail || {}) {
        const box = getChatElement("watchboard");
        const dashboard = watchboardPayload(detail);
        const visibleDashboard = state.watchboardHiddenResidents?.has(selectedResidentId()) ? null : dashboard;
        setWatchboardRefreshOwner(visibleDashboard ? selectedResidentId() : "");
        if (!syncWatchboardFrame(visibleDashboard) || !box) return;
        if (!visibleDashboard) {
          box.innerHTML = "";
          box.dataset.signature = "";
          return;
        }
        if (!Object.prototype.hasOwnProperty.call(visibleDashboard, "signature")) {
          box.dataset.signature = "";
          box.innerHTML = watchboardLoadError
            ? window.PortalI18n.html`<div class="paneNote consoleError" role="alert">${esc(watchboardLoadError)}</div><button class="portalButton is-compact" type="button" data-watchboard-retry>重试</button>`
            : window.PortalI18n.markup('<div class="paneNote" role="status">正在加载 Watchboard…</div>');
          box.querySelector("[data-watchboard-retry]")?.addEventListener("click", () => {
            refreshWatchboard().catch(() => {});
          });
          return;
        }
        try {
          const application = watchboardApplications.resolve(visibleDashboard);
          const signature = JSON.stringify({
            application: [application?.id || "", application?.key?.(visibleDashboard) || ""],
            collapsed: !!state.watchboardCollapsed,
            running: state.watchboardRunInFlight || "",
            modeConfigSaving: state.watchboardModeConfigSaving || "",
            dashboard: dashboard.signature || "",
          });
          if (box.dataset.signature === signature) return;
          box.dataset.signature = signature;
          box.innerHTML = application ? application.render(visibleDashboard) : watchboardGenericHtml(visibleDashboard);
          bindWatchboard(box, visibleDashboard);
          application?.bind?.(box, visibleDashboard);
        } catch (error) {
          box.dataset.signature = "";
          box.innerHTML = watchboardGenericHtml(visibleDashboard);
          bindWatchboard(box, visibleDashboard);
          const notice = document.createElement("div");
          notice.className = "paneNote consoleError";
          notice.textContent = window.PortalI18n.t("工作台应用暂不可用，已显示通用面板。");
          box.prepend(notice);
        }
      }

      function bindWatchboard(box, dashboard) {
        box.querySelectorAll("[data-watchboard-run-output]").forEach(button => {
          button.addEventListener("click", () => openWatchboardRunOutput(dashboard, button.dataset.watchboardRunOutput));
        });
        box.querySelectorAll("[data-watchboard-run-cancel]").forEach(button => {
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              const result = await residentService.cancelWatchboardRun({resident_id: selectedResidentId(),
                lineage_id: dashboard.lineage_id, script_run_id: button.dataset.watchboardRunCancel});
              mergeWatchboardResponse(result);
              renderWatchboard(state.detail);
            } catch (error) {
              button.disabled = false;
              portalUi.toast(String(error.message || error), "error");
            }
          });
        });
        const toggle = box.querySelector("[data-watchboard-dashboard-toggle]");
        if (toggle && state.watchboardCollapsed) {
          toggle.setAttribute("aria-label", window.PortalI18n.t("固定展开 Watchboard"));
          toggle.title = window.PortalI18n.t("固定展开 Watchboard");
        }
        toggle?.addEventListener("click", () => {
          state.watchboardCollapsed = !state.watchboardCollapsed;
          writeLineageUiState({ watchboardCollapsed: state.watchboardCollapsed });
          box.dataset.signature = "";
          renderWatchboard(state.detail || {});
        });
        box.querySelectorAll("[data-watchboard-config-open]").forEach(btn => {
          btn.addEventListener("click", () => openWatchboardGenericConfigModal(dashboard, btn.dataset.watchboardConfigOpen || ""));
        });
        box.querySelectorAll("[data-watchboard-status-open]").forEach(btn => {
          btn.addEventListener("click", () => openWatchboardGenericStatusModal(dashboard));
        });
        box.querySelectorAll("[data-watchboard-run-command]").forEach(btn => {
          btn.addEventListener("click", () => {
            runWatchboardCommand(btn.dataset.watchboardRunCommand || "", dashboard);
          });
        });

      }

      let watchboardModalDialog = null;

      function closeWatchboardModal() {
        const overlay = $id("watchboardModalOverlay");
        if (!overlay) return;
        watchboardModalDialog?.close({ force: true });
        watchboardModalDialog?.destroy();
        watchboardModalDialog = null;
        overlay.remove();
        watchboardEdge?.scheduleClose();
      }

      function showWatchboardModal(title, bodyHtml, { onBind = null } = {}) {
        closeWatchboardModal();
        const overlay = document.createElement("div");
        overlay.id = "watchboardModalOverlay";
        overlay.className = "portalDetailOverlay";
        overlay.hidden = true;
        overlay.dataset.portalDetailOverlay = "1";
        overlay.dataset.portalOverlayLayer = "dialog";
        overlay.innerHTML = window.PortalI18n.html`
          <div class="portalDetailSurface watchboardModalCard" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="watchboardModalTitle">
            <header class="portalDetailHeader">
              <h2 id="watchboardModalTitle">${esc(title || "Watchboard")}</h2>
              <button class="portalDetailClose" data-portal-detail-close type="button" data-watchboard-modal-close aria-label="关闭" title="关闭" data-portal-icon="close"></button>
            </header>
            <div class="portalDetailBody watchboardModalBody">${bodyHtml || ""}</div>
          </div>`;
        overlay.querySelector("[data-watchboard-modal-close]")?.addEventListener("click", closeWatchboardModal);
        document.body.appendChild(overlay);
        watchboardModalDialog = portalUi.createDialogController({
          modal: overlay,
          backdropClose: true,
          escapeClose: true,
          requestClose: closeWatchboardModal,
        });
        watchboardModalDialog.open();
        if (typeof onBind === "function") onBind(overlay);
      }

      function openWatchboardGenericConfigModal(dashboard = watchboardPayload(), relPath = "") {
        const configs = Array.isArray(dashboard?.configs) ? dashboard.configs : [];
        const config = configs.find(item => String(item.rel_path || "") === String(relPath || "")) || watchboardPrimaryConfig(dashboard);
        const rel = String(config?.rel_path || "");
        if (!rel) {
          showWatchboardModal("Configuration", window.PortalI18n.html`<div class="paneNote">未在 agent.yaml 中声明 configuration。</div>`);
          return;
        }
        // Saving writes the Workspace address; a config in no registered Workspace has none and stays read-only.
        const editable = !!(dashboard?.permissions?.can_edit_config && config.editable && config.address);
        const saving = state.watchboardModeConfigSaving === rel;
        const body = window.PortalI18n.html`
          <div class="watchboardKV"><span>path</span><span>${esc(rel)}</span></div>
          ${config.error ? window.PortalI18n.html`<div class="paneNote consoleError">${esc(config.error)}</div>` : ""}
          ${config.exists && !config.binary
            ? window.PortalI18n.html`<textarea class="watchboardModalTextarea" data-watchboard-modal-config-path="${esc(rel)}" data-watchboard-modal-config-address="${esc(config.address || "")}" data-watchboard-modal-config-version="${esc(config.version || "")}" ${editable ? "" : "readonly"}>${esc(String(config.content || ""))}</textarea>`
            : window.PortalI18n.html`<div class="paneNote">${config.exists ? window.PortalI18n.t("当前配置文件不支持在 dashboard 中编辑。") : window.PortalI18n.t("配置文件尚未创建。")}</div>`}
          <div class="watchboardMiniActions">
            <button class="portalButton is-compact is-primary watchboardCommandBtn" type="button" data-watchboard-modal-config-save="${esc(rel)}" ${editable && !saving ? "" : "disabled"}>${saving ? "Saving" : window.PortalI18n.t("Save")}</button>
            <button class="portalButton is-compact watchboardCommandBtn" type="button" data-watchboard-modal-close>Close</button>
          </div>`;
        showWatchboardModal("Configuration", body, {
          onBind: overlay => {
            overlay.querySelector("[data-watchboard-modal-config-save]")?.addEventListener("click", () => saveWatchboardModalConfig(rel, overlay));
            overlay.querySelectorAll("[data-watchboard-modal-close]").forEach(btn => btn.addEventListener("click", closeWatchboardModal));
          },
        });
      }

      function openWatchboardGenericStatusModal(dashboard = watchboardPayload()) {
        const statusPayload = dashboard?.status?.payload || {};
        const statusFile = dashboard?.status?.file || {};
        const statusError = dashboard?.status?.error || "";
        const hasPayload = statusPayload && Object.keys(statusPayload).length > 0;
        const content = hasPayload ? watchboardJsonPreview(statusPayload) : String(statusFile.content || "");
        const body = window.PortalI18n.html`
          <div class="watchboardKV"><span>file</span><span>${esc(statusFile.rel_path || "-")}</span></div>
          ${statusError ? window.PortalI18n.html`<div class="paneNote consoleError">${esc(statusError)}</div>` : ""}
          <pre class="watchboardTextPreview">${esc(content || window.PortalI18n.t("暂无状态内容。"))}</pre>
          <div class="watchboardMiniActions">
            <button class="portalButton is-compact watchboardCommandBtn" type="button" data-watchboard-modal-close>Close</button>
          </div>`;
        showWatchboardModal("Raw status", body, {
          onBind: overlay => {
            overlay.querySelectorAll("[data-watchboard-modal-close]").forEach(btn => btn.addEventListener("click", closeWatchboardModal));
          },
        });
      }

      async function saveWatchboardModalConfig(relPath, overlay) {
        const residentId = selectedResidentId();
        const rel = String(relPath || "").trim();
        const textarea = overlay?.querySelector(`[data-watchboard-modal-config-path="${CSS.escape(rel)}"]`);
        if (!residentId || !rel || !textarea || state.watchboardModeConfigSaving) return;
        state.watchboardModeConfigSaving = rel;
        try {
          const payload = await residentService.saveWatchboardConfig({
            resident_id: residentId,
            path: rel,
            content: textarea.value || "",
            address: textarea.dataset.watchboardModalConfigAddress || "",
            expected_version: textarea.dataset.watchboardModalConfigVersion || "",
          });
          mergeWatchboardResponse(payload);
          state.watchboardModeConfigSaving = "";
          closeWatchboardModal();
          renderWatchboard(state.detail || {});
          loadFolder(true).catch(() => {});
        } catch (err) {
          state.watchboardModeConfigSaving = "";
          portalUi.toast(portalUi.errorMessage(window.PortalI18n.t("保存运行模式配置"), err), "error");
        }
      }

      function refreshWatchboard() {
        const residentId = setWatchboardRefreshOwner(selectedResidentId());
        if (watchboardDestroyed || !residentId) return Promise.resolve(null);
        watchboardLoadFailures = 0;
        watchboardLoadError = "";
        renderWatchboard(state.detail);
        if (watchboardRefresh.isRefreshing()) watchboardRefresh.invalidate();
        return watchboardRefresh.refresh();
      }

      function openWatchboardRunOutput(dashboard, scriptRunId) {
        showWatchboardModal("输出", window.PortalI18n.html`${["stdout", "stderr"].map(stream => window.PortalI18n.html`<h4>${stream}</h4><pre class="watchboardTextPreview" data-watchboard-output="${stream}"></pre><button type="button" class="portalButton is-compact watchboardCommandBtn" data-watchboard-output-more="${stream}">读取日志</button>`).join("")}`, {
          onBind: overlay => {
            for (const stream of ["stdout", "stderr"]) {
              let offset = 0;
              const decoder = new TextDecoder();
              const button = overlay.querySelector(`[data-watchboard-output-more="${stream}"]`);
              const output = overlay.querySelector(`[data-watchboard-output="${stream}"]`);
              const read = async () => {
                button.disabled = true;
                try {
                  const result = await residentService.watchboardOutput({lineage_id: dashboard.lineage_id,
                    script_run_id: scriptRunId, stream, offset});
                  if (!overlay.isConnected) return;
                  const bytes = Uint8Array.from(atob(result.base64 || ""), character => character.charCodeAt(0));
                  output.textContent += decoder.decode(bytes, {stream: !result.eof});
                  offset = result.next_offset;
                  button.disabled = false;
                } catch (error) {
                  button.disabled = false;
                  portalUi.toast(String(error.message || error), "error");
                }
              };
              button.addEventListener("click", read);
              read();
            }
          },
        });
      }

      async function runWatchboardCommand(commandId, dashboard = watchboardPayload(), presetArgs = {}) {
        const residentId = selectedResidentId();
        const command = String(commandId || "").trim();
        if (!residentId || !command || state.watchboardRunInFlight) return false;
        const commandSpec = (dashboard?.commands || []).find(item => String(item.id || "") === command) || {};
        let args = { ...(presetArgs || {}) };
        const schema = commandSpec.args_schema || {};
        const requiredKeys = Object.keys(schema).filter(key => !!schema[key]?.required);
        const missingRequiredArgs = requiredKeys.some(key => !(key in args));
        if (commandSpec.requires_args && missingRequiredArgs) {
          const raw = await portalUi.prompt({
            title: window.PortalI18n.t("命令参数"),
            message: window.PortalI18n.t("该命令声明了参数，请输入 JSON 参数对象。"),
            label: window.PortalI18n.t("JSON 参数"),
            value: JSON.stringify(args, null, 2),
            multiline: true,
            confirmLabel: "运行",
          });
          if (raw === null) return false;
          try {
            const parsed = JSON.parse(raw || "{}");
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("参数必须是 JSON object");
            args = parsed;
          } catch (err) {
            portalUi.toast(window.PortalI18n.text`参数格式错误：${err.message || err}`, "error");
            return false;
          }
        }
        state.watchboardRunInFlight = command;
        state.watchboardRunArgs = args;
        renderWatchboard(state.detail || {});
        try {
          const payload = await residentService.runWatchboard({
            resident_id: residentId,
            command_id: command,
            args,
          });
          mergeWatchboardResponse(payload);
          state.watchboardRunInFlight = "";
          state.watchboardRunArgs = {};
          renderWatchboard(state.detail || {});
          scheduleWatchboardRefresh(1200);
          return true;
        } catch (err) {
          state.watchboardRunInFlight = "";
          state.watchboardRunArgs = {};
          renderWatchboard(state.detail || {});
          portalUi.toast(window.PortalI18n.text`运行失败：${err.message || err}`, "error");
          return false;
        }
      }

      return Object.freeze({
        dashboardPayload: watchboardPayload,
        destroy: destroyWatchboard,
        render: renderWatchboard,
      });
  };
})();
