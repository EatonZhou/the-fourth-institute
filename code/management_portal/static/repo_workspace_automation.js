(() => {
  "use strict";

  if (window.PortalRepoWorkspaceAutomation) return;

  // Automatic commits: of one Repo from its card, or of every dirty Repo the
  // board's query matches, with the progress dialog, stopping, and the
  // credential retry of a push that needs authentication.
  const DEPENDENCIES = Object.freeze([
    "automationStatusFor", "dialogs", "elements", "esc", "invalidateRevisionViews", "loadAutomationStatuses",
    "loadWorkspace", "postJson", "queryAllRepoRecords", "queryState", "renderRepoBoard",
    "retryGitAuthentication", "showToast", "state", "uiIcon", "updateAutomationControls",
  ]);

  function createAutomation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceAutomation is missing: ${missing.join(", ")}`);
    const {
      automationStatusFor, dialogs, elements, esc, invalidateRevisionViews, loadAutomationStatuses,
      loadWorkspace, postJson, queryAllRepoRecords, queryState, renderRepoBoard, retryGitAuthentication,
      showToast, state, uiIcon, updateAutomationControls,
    } = dependencies;

    function showAutomationOverlay() {
      dialogs.automation.open();
    }

    function hideAutomationOverlay() {
      dialogs.automation.close();
    }

    function automationResultMarkup(item) {
      const stateName = String(item.state || "pending");
      const marks = {
        pending: "status-dot",
        running: "refresh",
        success: "check",
        partial: "alert-circle",
        auth_required: "lock",
        skipped: "minus",
        error: "close",
        stopped: "pause",
      };
      const result = item.result || {};
      const push = result.push || {};
      let stateLabel = {
        pending: window.PortalI18n.t("等待"),
        running: window.PortalI18n.t("AI 分析中"),
        success: result.pushed
          ? `${push.remote || "remote"}/${push.branch || ""}`
          : window.PortalI18n.t("本地 commit"),
        partial: window.PortalI18n.t("已 commit · push 失败"),
        auth_required: window.PortalI18n.t("等待 Git 认证"),
        skipped: window.PortalI18n.t("无变更"),
        error: window.PortalI18n.t("失败"),
        stopped: window.PortalI18n.t("已停止"),
      }[stateName] || stateName;
      if (stateName === "success" && result.ai?.model) {
        stateLabel = `${stateLabel} · ${result.ai.model}`;
      }
      const title = result.message
        ? window.PortalI18n.html`<p class="repoAutomationResultTitle">${esc(result.message)}</p>`
        : "";
      const reason = result.reason
        ? window.PortalI18n.html`<p class="repoAutomationResultReason">${esc(result.reason)}</p>`
        : "";
      const error = item.error || (
        ["partial", "auth_required"].includes(stateName) ? push.error : ""
      );
      const errorMarkup = error
        ? window.PortalI18n.html`<p class="repoAutomationResultError">${esc(error)}</p>`
        : "";
      const note = stateName === "skipped"
        ? window.PortalI18n.markup('<p class="repoAutomationResultReason">工作区没有需要提交的变更。</p>')
        : stateName === "stopped"
          ? window.PortalI18n.markup('<p class="repoAutomationResultReason">没有开始处理该仓库。</p>')
          : "";
      return window.PortalI18n.html`
      <article class="repoAutomationResult ${esc(stateName)}" data-automation-result="${esc(item.id)}">
        <span class="repoAutomationResultMark" aria-hidden="true">${uiIcon(marks[stateName] || "status-dot")}</span>
        <div class="repoAutomationResultBody">
          <div class="repoAutomationResultHead">
            <strong>${esc(item.name || item.id)}</strong>
            <span>${esc(stateLabel)}</span>
          </div>
          ${title}${reason}${errorMarkup}${note}
        </div>
      </article>`;
    }

    function renderAutomationDialog() {
      const total = state.automationTargets.length;
      const completed = Math.min(state.automationCompleted, total);
      const percent = total ? Math.round(completed * 100 / total) : (state.automationActive ? 8 : 100);
      const currentName = state.automationNames.get(state.automationCurrent) || state.automationCurrent;
      elements.automationTitle.textContent = state.automationScope === "all"
        ? window.PortalI18n.t("AI 提交所有 Repo")
        : window.PortalI18n.t("AI 提交 Repo");
      if (state.automationActive && state.automationCurrent) {
        elements.automationStatus.textContent = window.PortalI18n.text`正在处理 ${currentName}`;
        elements.automationSub.textContent = window.PortalI18n.text`${completed + 1}/${total} · AI 先生成 commit 原因，再执行 Git 操作`;
      } else if (state.automationActive) {
        elements.automationStatus.textContent = state.automationScope === "all"
          ? window.PortalI18n.t("正在检查当前筛选的 Repo…")
          : window.PortalI18n.t("正在准备 Repo 变更…");
        elements.automationSub.textContent = window.PortalI18n.t("由 AI 生成 commit 标题和原因");
      } else if (!total && !state.automationResults.length) {
        elements.automationStatus.textContent = window.PortalI18n.t("没有需要提交的 Repo");
        elements.automationSub.textContent = window.PortalI18n.t("所有已检查工作区均无本地变更");
      } else {
        const successful = state.automationResults.filter(item => item.state === "success").length;
        const partial = state.automationResults.filter(item => (
          ["partial", "auth_required"].includes(item.state)
        )).length;
        const failed = state.automationResults.filter(item => item.state === "error").length;
        elements.automationStatus.textContent = state.automationCancelRequested ? window.PortalI18n.t("已停止后续仓库") : window.PortalI18n.t("AI 提交已完成");
        elements.automationSub.textContent = window.PortalI18n.text`${successful} 成功${partial ? window.PortalI18n.text` · ${partial} push 失败` : ""}${failed ? window.PortalI18n.text` · ${failed} 失败` : ""}`;
      }
      elements.automationProgressBar.style.width = `${percent}%`;
      elements.automationProgress.classList.toggle("complete", !state.automationActive);
      elements.automationProgress.setAttribute("aria-valuenow", String(percent));
      elements.automationResults.innerHTML = state.automationResults.length
        ? state.automationResults.map(automationResultMarkup).join("")
        : window.PortalI18n.html`<div class="repoAutomationEmpty">${state.automationActive ? window.PortalI18n.t("正在读取 Git 状态…") : window.PortalI18n.t("当前没有需要提交的变更。")}</div>`;
      elements.automationStop.hidden = !state.automationActive || total <= 1;
      elements.automationStop.disabled = state.automationCancelRequested;
      elements.automationStop.textContent = state.automationCancelRequested ? window.PortalI18n.t("正在停止…") : window.PortalI18n.t("停止后续仓库");
      elements.automationDone.textContent = state.automationActive ? window.PortalI18n.t("隐藏") : window.PortalI18n.t("完成");
      updateAutomationControls();
    }

    function beginAutomation(scope, reveal = true) {
      state.automationActive = true;
      state.automationCancelRequested = false;
      state.automationTargets = [];
      state.automationResults = [];
      state.automationCompleted = 0;
      state.automationCurrent = "";
      state.automationScope = scope;
      if (reveal) showAutomationOverlay();
      renderAutomationDialog();
    }

    async function executeAutomationTargets(targets, initialResults = []) {
      state.automationTargets = targets.map(target => ({
        id: String(target.id || ""),
        name: String(target.name || target.id || ""),
      }));
      const pending = state.automationTargets.map(target => ({
        ...target,
        state: "pending",
        result: null,
        error: "",
      }));
      state.automationResults = [...initialResults, ...pending];
      state.automationCompleted = 0;
      renderAutomationDialog();

      for (const target of pending) {
        if (state.automationCancelRequested) break;
        target.state = "running";
        state.automationCurrent = target.id;
        renderAutomationDialog();
        if (state.mode === "board") renderRepoBoard();
        try {
          const result = await postJson(
            `/api/repos/${encodeURIComponent(target.id)}/workspace/auto-commit`,
            {},
            // The server owns model, Git and push timeouts for this multi-step operation.
            {timeoutMs: 0},
          );
          target.result = result;
          if (result.automation_status) {
            state.automationStatuses.set(target.id, result.automation_status);
          }
          if (result.outcome === "clean") {
            target.state = "skipped";
          } else if (result.auth_challenge) {
            target.state = "auth_required";
            renderAutomationDialog();
            const syncResult = await retryGitAuthentication(result.auth_challenge);
            if (syncResult) {
              result.outcome = "committed_and_pushed";
              result.pushed = true;
              result.auth_challenge = null;
              result.credential = syncResult.credential || {};
              result.push = {
                ...(result.push || {}),
                attempted: true,
                ok: true,
                error: "",
                auth_challenge: null,
              };
              target.state = "success";
            } else {
              target.state = "partial";
              target.error = "已取消 Git 认证；本地 commit 已保留，可稍后单独 Push。";
            }
          } else if (result.outcome === "committed_push_failed") {
            target.state = "partial";
          } else {
            target.state = "success";
          }
        } catch (error) {
          target.state = "error";
          target.error = String(error?.message || error);
        }
        state.automationCompleted += 1;
        state.automationCurrent = "";
        renderAutomationDialog();
        if (state.mode === "board") renderRepoBoard();
      }

      if (state.automationCancelRequested) {
        pending.filter(item => item.state === "pending").forEach(item => {
          item.state = "stopped";
          state.automationCompleted += 1;
        });
      }
      state.automationActive = false;
      state.automationCurrent = "";
      renderAutomationDialog();
      if (state.mode === "board") renderRepoBoard();
      else if (state.automationTargets.some(item => item.id === state.selectedId)) {
        state.commitDraft = { message: "", description: "" };
        invalidateRevisionViews();
        loadWorkspace().catch(error => showToast(error.message || error, "error"));
      }
      const successCount = state.automationResults.filter(item => item.state === "success").length;
      const partialCount = state.automationResults.filter(item => (
        ["partial", "auth_required"].includes(item.state)
      )).length;
      const errorCount = state.automationResults.filter(item => item.state === "error").length;
      const singleResult = state.automationScope === "single"
        ? state.automationResults[0]
        : null;
      if (singleResult?.state === "error") {
        showToast(window.PortalI18n.text`AI 提交失败：${singleResult.error || window.PortalI18n.t("未知错误")}`, "error");
      } else if (singleResult && ["partial", "auth_required"].includes(singleResult.state)) {
        showToast(
          window.PortalI18n.text`Commit 已创建，但 Push 失败：${singleResult.error || singleResult.result?.push?.error || window.PortalI18n.t("请检查 Remote 权限")}`,
          "error",
        );
      } else if (singleResult?.state === "success") {
        showToast(
          singleResult.result?.pushed
            ? window.PortalI18n.text`Commit 已创建并 Push：${singleResult.result.message || singleResult.name}`
            : window.PortalI18n.text`Commit 已创建：${singleResult.result?.message || singleResult.name}`,
          "success",
        );
      } else if (errorCount || partialCount) {
        showToast(window.PortalI18n.text`AI 提交完成：${successCount} 成功，${partialCount} push 失败，${errorCount} 失败。`, "error");
      } else if (successCount) {
        showToast(window.PortalI18n.text`AI 提交完成：${successCount} 个 Repo 已处理。`, "success");
      } else {
        showToast(window.PortalI18n.t("没有 Repo 需要提交。"));
      }
    }

    async function startRepoAutomation(repoId) {
      if (state.automationActive) {
        showToast(
          state.automationScope === "single"
            ? window.PortalI18n.t("这个 Repo 正在执行 AI Commit。")
            : window.PortalI18n.t("所有 Repo 的 AI 提交正在执行。"),
        );
        return;
      }
      const id = String(repoId || "").trim();
      if (!id) return;
      const status = automationStatusFor(id);
      if (status && !status.dirty) {
        showToast(window.PortalI18n.t("当前 Repo 工作区干净。"));
        return;
      }
      beginAutomation("single", false);
      await executeAutomationTargets([{
        id,
        name: state.automationNames.get(id) || id,
      }]);
    }

    async function startAllRepoAutomation() {
      if (state.automationActive) {
        showAutomationOverlay();
        renderAutomationDialog();
        return;
      }
      beginAutomation("all");
      try {
        const records = await queryAllRepoRecords(queryState());
        records.forEach(record => {
          state.automationNames.set(
            String(record.resource_id || ""),
            String(record.title || record.resource_id || ""),
          );
        });
        const statuses = await loadAutomationStatuses(records, { render: false });
        const byId = new Map(records.map(record => [String(record.resource_id || ""), record]));
        const blocked = statuses.filter(item => item?.error || (item?.dirty && !item?.actionable));
        const initialResults = blocked.map(item => ({
          id: String(item.id || ""),
          name: String(byId.get(String(item.id || ""))?.title || item.name || item.id || ""),
          state: "error",
          result: null,
          error: String(item.blocked_reason || item.error || "该 Repo 当前不能自动提交"),
        }));
        const targets = statuses.filter(item => item?.actionable).map(item => ({
          id: String(item.id || ""),
          name: String(byId.get(String(item.id || ""))?.title || item.name || item.id || ""),
        }));
        if (!targets.length) {
          state.automationActive = false;
          state.automationResults = initialResults;
          state.automationTargets = [];
          state.automationCompleted = 0;
          renderAutomationDialog();
          showToast(initialResults.length ? window.PortalI18n.t("有 Repo 需要先处理冲突或权限问题。") : window.PortalI18n.t("所有 Repo 工作区均无本地变更。"));
          return;
        }
        const statusById = new Map(statuses.map(item => [String(item.id || ""), item]));
        const preview = targets.slice(0, 10).map(target => {
          const item = statusById.get(target.id) || {};
          const destination = item.will_push
            ? `commit + push → ${item.remote || "remote"}`
            : "仅本地 commit";
          return `• ${target.name}：${Number(item.change_count || 0)} changes，${destination}`;
        });
        if (targets.length > preview.length) {
          preview.push(`• 以及另外 ${targets.length - preview.length} 个 Repo`);
        }
        const approved = await window.PortalUI.confirm({
          title: window.PortalI18n.text`提交 ${targets.length} 个 Repo？`,
          message: window.PortalI18n.text`${preview.join("\n")}\n\nAI 会分别生成可审阅的 commit 标题与原因；此操作会修改以上仓库，标为 push 的仓库还会更新 remote。`,
          confirmLabel: window.PortalI18n.t("开始 AI 提交"),
          tone: "warning",
        });
        if (!approved) {
          state.automationActive = false;
          state.automationTargets = [];
          state.automationResults = [];
          hideAutomationOverlay();
          renderAutomationDialog();
          showToast(window.PortalI18n.t("已取消批量 AI 提交。"));
          return;
        }
        await executeAutomationTargets(targets, initialResults);
      } catch (error) {
        state.automationActive = false;
        state.automationTargets = [];
        state.automationResults = [{
          id: "preflight",
          name: window.PortalI18n.t("所有 Repo 检查"),
          state: "error",
          result: null,
          error: String(error?.message || error),
        }];
        renderAutomationDialog();
        showToast(error.message || error, "error");
      }
    }

    return Object.freeze({
      hideAutomationOverlay, renderAutomationDialog, startAllRepoAutomation, startRepoAutomation,
    });
  }

  window.PortalRepoWorkspaceAutomation = Object.freeze({ createAutomation, dependencies: DEPENDENCIES });
})();
