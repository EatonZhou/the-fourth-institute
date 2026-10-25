(() => {
  "use strict";

  if (window.PortalLineageWorkbenchWhichResident) return;

  // Which Resident at the top of the Resident list: the recommendation request,
  // its progress and result, the recommended Residents' cards and conversations,
  // forks it advises, routing corrections, and the request state the page keeps
  // across reloads.
  const DEPENDENCIES = Object.freeze([
    "appendChatWorkspaceDraft", "currentLineageWorkbench", "esc",
    "lineageSidebarRootByResidentId", "openResidentForkCreateModal",
    "registeredLineageIdForResident", "renderAgentList", "renderRegisteredResidentItem",
    "residentDiscovery", "residentSidebarPendingText", "selectResident", "state",
    "writeLineageUiState", "writeLineageUiStateSoon",
  ]);

  function compactWhichResidentResult(result) {
    if (!result || typeof result !== "object" || Array.isArray(result)) return null;
    const recommendations = (Array.isArray(result.recommendations) ? result.recommendations : [])
      .slice(0, 8)
      .map(item => ({
        resident_id: String(item?.resident_id || ""),
        title: String(item?.title || ""),
        lineage_id: String(item?.lineage_id || ""),
        lineage_title: String(item?.lineage_title || ""),
        reason: String(item?.reason || ""),
        confidence: String(item?.confidence || ""),
        recommended_action: ["open", "fork"].includes(item?.recommended_action) ? item.recommended_action : "open",
        summary: { current_focus: String(item?.summary?.current_focus || "") },
      }))
      .filter(item => item.resident_id);
    const fork = result.fork_advice && typeof result.fork_advice === "object"
      ? result.fork_advice : {};
    return {
      schema: String(result.schema || ""),
      query: String(result.query || "").slice(0, 4000),
      summary: String(result.summary || "").slice(0, 1000),
      fallback: result.fallback === true,
      fallback_reason: String(result.fallback_reason || ""),
      recommendation_source: String(result.recommendation_source || ""),
      recommendations,
      fork_advice: {
        recommended: fork.recommended === true,
        source_resident_id: String(fork.source_resident_id || ""),
        source_title: String(fork.source_title || ""),
        source_lineage_id: String(fork.source_lineage_id || ""),
        source_lineage_title: String(fork.source_lineage_title || ""),
        reason: String(fork.reason || "").slice(0, 800),
        suggested_title: String(fork.suggested_title || "").slice(0, 200),
      },
    };
  }

  function createWhichResident(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchWhichResident is missing: ${missing.join(", ")}`);
    const {
      appendChatWorkspaceDraft, currentLineageWorkbench, esc, lineageSidebarRootByResidentId,
      openResidentForkCreateModal, registeredLineageIdForResident, renderAgentList,
      renderRegisteredResidentItem, residentDiscovery, residentSidebarPendingText,
      selectResident, state, writeLineageUiState, writeLineageUiStateSoon,
    } = dependencies;

    let whichResidentCorrectionSearchTimer = 0;

    let whichResidentInputWatch = null;

    let whichResidentRetryTimer = 0;

    function whichResidentCorrectionSearchHtml() {
      const finder = state.whichResident;
      if (finder.correctionSearchLoading) {
        return window.PortalI18n.markup('<div class="whichResidentCorrectionSearchState">正在搜索可访问的完整 Resident 目录…</div>');
      }
      if (finder.correctionSearchError) {
        return window.PortalI18n.html`<div class="whichResidentCorrectionSearchState error">${esc(finder.correctionSearchError)}</div>`;
      }
      const results = Array.isArray(finder.correctionSearchResults)
        ? finder.correctionSearchResults : [];
      if (!results.length) {
        return window.PortalI18n.markup('<div class="whichResidentCorrectionSearchState">没有匹配的可访问 Resident。</div>');
      }
      return window.PortalI18n.html`<div class="whichResidentCorrectionResults" role="listbox">${results.map(item => {
          const residentId = String(item?.resident_id || "");
          const selected = residentId === finder.correctionResidentId;
          return window.PortalI18n.html`<button type="button" role="option" aria-selected="${selected ? "true" : "false"}" class="${selected ? "selected" : ""}" data-routing-eval-resident-id="${esc(residentId)}">
            <strong>${esc(item?.title || residentId)}</strong>
            <small>${esc(item?.lineage_title || item?.lineage_id || "")}</small>
            <span>${esc(residentId)}</span>
          </button>`;
        }).join("")}</div>`;
    }

    function whichResidentPanelHtml() {
      const finder = state.whichResident;
      const recommendations = Array.isArray(finder.result?.recommendations)
        ? finder.result.recommendations : [];
      const fork = finder.result?.fork_advice || {};
      const forkSourceId = String(fork.source_resident_id || "").trim();
      const forkSource = recommendations.find(item => item.resident_id === forkSourceId) || {};
      const forkSourceTitle = String(fork.source_title || forkSource.title || forkSourceId).trim();
      // The editable draft is not the query owned by an accepted request.
      const submittedQuery = String(finder.result?.query || finder.submittedQuery || "");
      const requestHtml = finder.requestId || finder.messageId || submittedQuery
        ? window.PortalI18n.html`<div class="whichResidentRequest" aria-label="推荐请求">
              ${finder.requestId ? window.PortalI18n.html`<div>请求 ID：<code>${esc(finder.requestId)}</code></div>` : ""}
              ${finder.messageId ? window.PortalI18n.html`<div>消息 ID：<code>${esc(finder.messageId)}</code></div>` : ""}
              <div>状态：${esc(finder.inputStatus || window.PortalI18n.t("提交中"))}</div>
              <div>已提交查询：${esc(submittedQuery || window.PortalI18n.t("历史查询未保存"))}</div>
            </div>`
        : "";
      const fallbackHtml = finder.result?.fallback
        ? window.PortalI18n.html`<div class="whichResidentStatus" role="status"><strong>目录检索回退</strong>：${esc(finder.result.summary || window.PortalI18n.t("推荐能力当前不可接单；以下候选未经过模型分析。"))}</div>`
        : "";
      const resultHtml = finder.busy
        ? window.PortalI18n.html`<div class="whichResidentStatus">${finder.error ? esc(finder.error) : window.PortalI18n.t("正在分析候选 Resident…")}</div>`
        : finder.error
          ? window.PortalI18n.html`<div class="whichResidentStatus error">${esc(finder.error)}</div>`
          : recommendations.length || (fork.recommended && forkSourceId)
            ? window.PortalI18n.html`${fallbackHtml}${recommendations.length && !finder.result?.fallback ? window.PortalI18n.t('<div class="whichResidentStatus">置信度是模型对职责匹配的判断，不代表统计概率。</div>') : ""}<div class="whichResidentResults">${recommendations.map(item => {
                  const recommendedAction = ["open", "fork"].includes(item.recommended_action) ? item.recommended_action : "open";
                  const confidence = String(item.confidence || "").toLowerCase();
                  const confidenceLabel = finder.result?.fallback ? window.PortalI18n.t("未评估（目录检索）")
                    : confidence === "high" ? window.PortalI18n.t("高") : confidence === "medium" ? window.PortalI18n.t("中")
                    : confidence === "low" ? window.PortalI18n.t("低") : window.PortalI18n.t("未提供");
                  return window.PortalI18n.html`
                  <article class="whichResidentResult">
                    <div><strong>${esc(item.title || item.resident_id)}</strong><small>${esc(item.lineage_title || item.lineage_id || item.resident_id)}</small></div>
                    <small class="whichResidentConfidence">置信度：${esc(confidenceLabel)}</small>
                    <p>${esc(item.reason || item.summary?.current_focus || "")}</p>
                    <div class="whichResidentActions">
                      <button type="button" class="${recommendedAction === "open" ? "recommended" : ""}" data-which-resident-open-id="${esc(item.resident_id)}">打开${recommendedAction === "open" ? window.PortalI18n.t("（推荐）") : ""}</button>
                      <button type="button" class="${recommendedAction === "fork" ? "recommended" : ""}" data-fork-source-resident-id="${esc(item.resident_id)}">创建分支${recommendedAction === "fork" ? window.PortalI18n.t("（推荐）") : ""}</button>
                    </div>
                  </article>`;
                }).join("")}</div>
                ${fork.recommended && forkSourceId ? window.PortalI18n.html`<div class="whichResidentForkAdvice">
                  <strong>建议从 ${esc(forkSourceTitle)} 创建分支</strong>
                  <small>${esc(fork.source_lineage_title || fork.source_lineage_id || forkSource.lineage_title || forkSource.lineage_id || forkSourceId)}</small>
                  <span>${esc(fork.reason || "")}</span>
                  ${fork.suggested_title ? window.PortalI18n.html`<span>建议分支：${esc(fork.suggested_title)}</span>` : ""}
                  <div class="whichResidentAdviceActions">
                    <button type="button" class="recommended" data-fork-source-resident-id="${esc(forkSourceId)}">创建分支（推荐）</button>
                  </div>
                </div>` : ""}`
            : finder.result
              ? window.PortalI18n.markup('<div class="whichResidentStatus">当前可访问摘要中没有足够匹配的 Resident。</div>')
              : window.PortalI18n.markup('<div class="whichResidentStatus">描述你要处理的问题、组件或目标。</div>');
      return window.PortalI18n.html`<section class="whichResidentPanel residentQuestionCard" aria-label="推荐选择">
          ${finder.result?.summary && !finder.result?.fallback ? window.PortalI18n.html`<p>${esc(finder.result.summary)}</p>` : ""}
          ${resultHtml}
          ${requestHtml ? window.PortalI18n.html`<details class="whichResidentRequestDetails"><summary>请求详情</summary>${requestHtml}</details>` : ""}
          ${recommendations.length ? window.PortalI18n.html`<div class="whichResidentCorrectionControls">
            <button type="button" data-which-resident-correction-toggle>${finder.correctionOpen ? window.PortalI18n.t("取消纠正") : window.PortalI18n.t("纠正并加入评测集")}</button>
          </div>` : ""}
          ${finder.correctionOpen ? window.PortalI18n.html`<form data-which-resident-correction-form class="whichResidentCorrectionForm">
            <strong>提交路由 Ground Truth</strong>
            <label>正确的 Resident
              <input type="search" name="resident_search" maxlength="300" autocomplete="off" placeholder="搜索名称、Lineage 或 Resident ID" value="${esc(finder.correctionSearchQuery || "")}">
            </label>
            <input type="hidden" name="ground_truth_resident_id" value="${esc(finder.correctionResidentId || "")}">
            ${finder.correctionResidentId ? window.PortalI18n.html`<div class="whichResidentCorrectionSelected">已选择：<strong>${esc(finder.correctionResidentTitle || finder.correctionResidentId)}</strong><span>${esc(finder.correctionResidentId)}</span></div>` : ""}
            ${whichResidentCorrectionSearchHtml()}
            <select name="ground_truth_action">
              <option value="open" ${finder.correctionAction === "open" ? "selected" : ""}>打开现有 Resident</option>
              <option value="fork" ${finder.correctionAction === "fork" ? "selected" : ""}>创建分支并加入 HATG</option>
            </select>
            <textarea name="rationale" rows="2" maxlength="1200" placeholder="可选：为什么这是正确路由">${esc(finder.correctionRationale || "")}</textarea>
            <button type="submit" ${finder.correctionSaving ? "disabled" : ""}>${finder.correctionSaving ? window.PortalI18n.t("提交中…") : window.PortalI18n.t("提交并加入评测集")}</button>
          </form>` : ""}
          ${finder.evalMessage ? window.PortalI18n.html`<div class="whichResidentStatus">${esc(finder.evalMessage)}</div>` : ""}
        </section>`;
    }

    const recommendationChatPanels = new Map();

    function whichResidentCardHtml() {
      const resident = state.recommendationResident;
      if (!resident && !state.residentSidebarReady) {
        const pending = residentSidebarPendingText("推荐服务");
        return window.PortalI18n.html`<div class="agentItem principalUnavailableCard" aria-busy="${state.residentSidebarError ? "false" : "true"}"><div class="agentSelect noStatusDot"><span class="agentMain"><span class="agentTitle">Which Resident</span><span class="agentMeta" title="${esc(pending)}">${esc(pending)}</span></span></div></div>`;
      }
      if (!resident) {
        const denied = state.recommendationStatus === "denied";
        return window.PortalI18n.html`<div class="agentItem principalUnavailableCard ${denied ? "locked" : ""}"><div class="agentSelect noStatusDot"><span class="agentMain"><span class="agentTitle">Which Resident</span><span class="agentMeta" title="${denied ? window.PortalI18n.t("没有推荐 Resident 的访问权限") : window.PortalI18n.t("推荐服务暂不可用，请刷新重试")}">${denied ? window.PortalI18n.t("没有访问权限") : window.PortalI18n.t("推荐服务暂不可用")}</span></span></div>${denied ? window.PortalI18n.t('<span class="principalAccessLock" data-portal-icon="lock" aria-label="无访问权限"></span>') : ''}</div>`;
      }
      return renderRegisteredResidentItem({ ...resident, title: window.PortalI18n.t("Which Resident") }, { asResident: true });
    }

    function recommendationConversationView(target, panel) {
      if (!state.recommendationResident || target.resident_id !== state.recommendationResident.resident_id) return null;
      recommendationChatPanels.set(panel.instanceId(), panel);
      return {
        messageActions: false,
        // The Resident's reply is presented only once the recommendation it produced is shown.
        readReceipt: () => {
          const request = state.whichResident;
          if (request.busy) return false;
          return request.inputStatus === "completed" ? !!request.result
            : ["failed", "cancelled"].includes(request.inputStatus) && !!request.error;
        },
        project: contract => {
          const request = state.whichResident;
          const query = request.result?.query || request.submittedQuery || "";
          const requestId = request.messageId || request.requestId || "resident-discovery-welcome";
          return { ...contract, title: window.PortalI18n.t("Which Resident"), subtitle: window.PortalI18n.t("描述任务，选择协作者或创建分支。"),
            messages: [
              ...(query ? [{ id: requestId, message_id: requestId, role: "user", content: query }] : []),
              { id: `${requestId}:result`, role: "agent", name: window.PortalI18n.t("Which Resident"),
                content: request.error || request.result?.summary || "描述你要处理的问题、组件或目标。",
                recommendation_view: { html: whichResidentPanelHtml() } },
            ],
            composer: { ...contract.composer, placeholder: window.PortalI18n.t("描述你要处理的任务"), can_attach: false,
              refs_enabled: false, can_auto: false,
              can_send: contract.composer?.can_send !== false && !request.busy,
              disabled_reason: request.busy
                ? "正在寻找合适的 Resident，请等待本次推荐完成。"
                : contract.composer?.disabled_reason || "" },
          };
        },
        renderMessageBody: item => item.recommendation_view?.html,
        onMessagesRendered: root => bindWhichResidentControls(root),
        send: async (payload, options, activePanel) => {
          if (state.whichResident.busy || activePanel.workspace.state.composer.can_send === false) return null;
          const query = String(payload.text || payload.plainText || "").trim();
          if (query.length > 4000) throw new Error("请将任务描述控制在 4000 字以内");
          return activePanel.workspace.submit(async () => {
            const sent = await runWhichResident(query);
            if (!sent) throw new Error(state.whichResident.error || "推荐请求未能提交");
            return sent;
          }, {
            payload,
            historyText: String(payload.plainText || query).trim(),
            rememberHistory: options.rememberHistory,
            clear: options.clear,
            focus: options.focus,
            clearRefsOnSuccess: true,
          });
        },
      };
    }

    function refreshRecommendationChats() {
      for (const [id, panel] of recommendationChatPanels) {
        if (currentLineageWorkbench()?.getPanel(id) !== panel) { recommendationChatPanels.delete(id); continue; }
        if (panel.target().resident_id !== state.recommendationResident?.resident_id) continue;
        const focused = panel.workspace.els.messages.contains(document.activeElement) ? document.activeElement : null;
        const name = focused?.name;
        const cursor = focused?.selectionStart;
        panel.refreshView();
        if (name) {
          const input = panel.workspace.els.messages.querySelector(`[name="${CSS.escape(name)}"]`);
          input?.focus();
          if (typeof cursor === "number") input?.setSelectionRange?.(cursor, cursor);
        }
      }
    }

    async function runWhichResident(query, { recoveryRetry = false } = {}) {
      const clean = String(query || "").trim();
      if (!clean || state.whichResident.busy) return;
      window.clearTimeout(whichResidentRetryTimer);
      whichResidentRetryTimer = 0;
      whichResidentInputWatch?.destroy?.();
      whichResidentInputWatch = null;
      const requestSeq = Number(state.whichResident.requestSeq || 0) + 1;
      state.whichResident = {
        ...state.whichResident,
        query: clean,
        submittedQuery: clean,
        busy: true,
        requestSeq,
        retryCount: recoveryRetry ? Number(state.whichResident.retryCount || 0) : 0,
        requestId: "",
        result: null,
        error: "",
        residentId: "",
        messageId: "",
        inputStatus: "",
        resultLoading: false,
      };
      persistWhichResidentState({ immediate: true });
      renderAgentList();
      try {
        const started = await residentDiscovery.recommend({
          query: clean,
          limit: 5,
        });
        if (started?.status === "failed") {
          state.whichResident.requestId = String(started.request_id || "");
          throw new Error(started.error || "Resident 推荐请求失败");
        }
        if (started?.result) {
          state.whichResident = {
            ...state.whichResident,
            busy: false,
            requestId: String(started.request_id || ""),
            result: started.result,
            inputStatus: "completed",
          };
        } else {
          bindWhichResidentInputWatch(started, requestSeq);
        }
      } catch (error) {
        if (requestSeq !== state.whichResident.requestSeq) return;
        state.whichResident = {
          ...state.whichResident,
          busy: false,
          result: null,
          error: error.message || String(error),
          inputStatus: "failed",
        };
      }
      persistWhichResidentState({ immediate: true });
      renderAgentList();
      return state.whichResident.inputStatus !== "failed";
    }

    function stopUnavailableWhichResident(error, requestSeq) {
      if (![401, 403, 404, 410].includes(Number(error?.status)) || requestSeq !== state.whichResident.requestSeq) return false;
      window.clearTimeout(whichResidentRetryTimer);
      whichResidentRetryTimer = 0;
      whichResidentInputWatch?.destroy?.();
      whichResidentInputWatch = null;
      state.whichResident = {
        ...state.whichResident,
        busy: false,
        resultLoading: false,
        inputStatus: "unavailable",
        error: `原查询不存在或当前无权读取，已停止自动重试；可修改查询后重新提交：${error.message || String(error)}`,
      };
      persistWhichResidentState({ immediate: true });
      renderAgentList();
      return true;
    }

    function scheduleWhichResidentRetry(requestSeq, retry) {
      if (whichResidentRetryTimer || requestSeq !== state.whichResident.requestSeq) return;
      whichResidentRetryTimer = window.setTimeout(() => {
        whichResidentRetryTimer = 0;
        if (requestSeq !== state.whichResident.requestSeq || state.whichResident.result) return;
        retry();
      }, 2000);
    }

    function retryWhichResidentObservation(requestSeq) {
      scheduleWhichResidentRetry(requestSeq, () => {
        bindWhichResidentInputWatch(state.whichResident, requestSeq);
      });
    }

    function retryWhichResidentResult(requestSeq) {
      scheduleWhichResidentRetry(requestSeq, () => {
        loadWhichResidentResult(requestSeq).catch(() => {});
      });
    }

    function retryCancelledWhichResident(requestSeq) {
      if (requestSeq !== state.whichResident.requestSeq || Number(state.whichResident.retryCount || 0) >= 1) return false;
      const query = String(state.whichResident.submittedQuery || state.whichResident.query || "").trim();
      if (!query) return false;
      window.clearTimeout(whichResidentRetryTimer);
      whichResidentInputWatch?.destroy?.();
      whichResidentInputWatch = null;
      const retrySeq = requestSeq + 1;
      state.whichResident = {
        ...state.whichResident,
        requestSeq: retrySeq,
        busy: true,
        resultLoading: false,
        inputStatus: "retrying",
        error: "推荐任务被停止，正在重新提交一次",
        retryCount: 1,
      };
      persistWhichResidentState({ immediate: true });
      renderAgentList();
      whichResidentRetryTimer = window.setTimeout(() => {
        whichResidentRetryTimer = 0;
        if (retrySeq !== state.whichResident.requestSeq || state.whichResident.inputStatus !== "retrying") return;
        state.whichResident.busy = false;
        runWhichResident(query, { recoveryRetry: true }).catch(() => {});
      }, 1000);
      return true;
    }

    async function loadWhichResidentResult(requestSeq) {
      const finder = state.whichResident;
      if (
        requestSeq !== finder.requestSeq
        || finder.resultLoading
        || !finder.residentId
        || !finder.messageId
      ) return;
      finder.resultLoading = true;
      try {
        const payload = await residentDiscovery.loadRecommendationResult({
          resident_id: finder.residentId,
          message_id: finder.messageId,
        });
        if (requestSeq !== state.whichResident.requestSeq) return;
        const resultStatus = String(payload?.status || "").trim().toLowerCase();
        if (resultStatus === "cancelled" && retryCancelledWhichResident(requestSeq)) return;
        if (["failed", "cancelled", "unavailable", "missing"].includes(resultStatus)) {
          state.whichResident = {
            ...state.whichResident,
            busy: false,
            resultLoading: false,
            result: null,
            inputStatus: resultStatus,
            error: String(payload?.error || payload?.input_state?.error?.message || `Resident 推荐已${resultStatus === "cancelled" ? "取消" : "失败"}`),
          };
          window.clearTimeout(whichResidentRetryTimer);
          whichResidentRetryTimer = 0;
          whichResidentInputWatch?.destroy?.();
          whichResidentInputWatch = null;
          return;
        }
        if (payload.status !== "completed" || !payload.result) {
          retryWhichResidentResult(requestSeq);
          return;
        }
        state.whichResident = {
          ...state.whichResident,
          busy: false,
          resultLoading: false,
          result: payload.result,
          requestId: String(payload.request_id || state.whichResident.requestId || ""),
          error: "",
          inputStatus: "completed",
        };
        window.clearTimeout(whichResidentRetryTimer);
        whichResidentRetryTimer = 0;
        whichResidentInputWatch?.destroy?.();
        whichResidentInputWatch = null;
      } catch (error) {
        if (requestSeq !== state.whichResident.requestSeq) return;
        if (stopUnavailableWhichResident(error, requestSeq)) return;
        state.whichResident = {
          ...state.whichResident,
          busy: true,
          resultLoading: false,
          result: null,
          error: `推荐结果读取暂不可用，正在重试：${error.message || String(error)}`,
        };
        retryWhichResidentResult(requestSeq);
      } finally {
        if (requestSeq === state.whichResident.requestSeq) {
          state.whichResident.resultLoading = false;
          persistWhichResidentState({ immediate: true });
          renderAgentList();
        }
      }
    }

    function bindWhichResidentInputWatch(handle, requestSeq) {
      const residentId = String(handle?.resident_id || state.whichResident.residentId || "").trim();
      const messageId = String(handle?.message_id || state.whichResident.messageId || "").trim();
      if (!residentId || !messageId) throw new Error("Resident 推荐未返回消息身份");
      window.clearTimeout(whichResidentRetryTimer);
      whichResidentRetryTimer = 0;
      whichResidentInputWatch?.destroy?.();
      state.whichResident.residentId = residentId;
      state.whichResident.messageId = messageId;
      state.whichResident.requestId = String(handle?.request_id || state.whichResident.requestId || "");
      state.whichResident.inputStatus = String(handle?.status || handle?.inputStatus || "queued");
      whichResidentInputWatch = window.PortalResidentProtocol.watchInputState(
        residentId,
        messageId,
        update => {
          if (requestSeq !== state.whichResident.requestSeq) return;
          if (update.type === "error") {
            if (state.whichResident.result) return;
            if (stopUnavailableWhichResident(update.error, requestSeq)) return;
            state.whichResident.error = `状态读取暂不可用，正在重试：${update.error?.message || String(update.error || "未知错误")}`;
            retryWhichResidentObservation(requestSeq);
            persistWhichResidentState({ immediate: true });
            renderAgentList();
            return;
          }
          const inputState = update.input_state;
          if (!inputState) return;
          const status = String(inputState.state || "");
          if (status === "cancelled" && retryCancelledWhichResident(requestSeq)) return;
          state.whichResident.inputStatus = status;
          state.whichResident.error = "";
          state.whichResident.busy = !["failed", "cancelled"].includes(status);
          if (["failed", "cancelled"].includes(status)) {
            window.clearTimeout(whichResidentRetryTimer);
            whichResidentRetryTimer = 0;
            state.whichResident.busy = false;
            state.whichResident.error = String(inputState.error?.message || `Resident 推荐已${status === "cancelled" ? "取消" : "失败"}`);
          } else if (status === "completed") {
            loadWhichResidentResult(requestSeq).catch(error => {
              state.whichResident.busy = false;
              state.whichResident.error = error.message || String(error);
              renderAgentList();
            });
          }
          persistWhichResidentState({ immediate: true });
          renderAgentList();
        },
        { historyLimit: 1 },
      );
      persistWhichResidentState({ immediate: true });
      if (state.whichResident.inputStatus === "queued") {
        loadWhichResidentResult(requestSeq).catch(() => {});
      }
    }

    async function restoreLatestWhichResident() {
      if (state.whichResident.inputStatus === "retrying"
        || (state.whichResident.inputStatus === "cancelled" && state.whichResident.submittedQuery)) return;
      // A submission that failed before the server recorded it is newer than anything
      // the server's latest record holds: keep its query and error.
      if (!state.whichResident.requestId && state.whichResident.submittedQuery
        && ["failed", "unavailable"].includes(String(state.whichResident.inputStatus || ""))) return;
      const observedRequestSeq = Number(state.whichResident.requestSeq || 0);
      const latest = await residentDiscovery.latestRecommendation();
      if (observedRequestSeq !== Number(state.whichResident.requestSeq || 0)) return;
      // The server records every accepted request, so a different request ID there is
      // newer than the result this page last showed (for example one sent from another tab).
      const latestRequestId = String(latest?.request_id || "");
      const showsLatest = Boolean(state.whichResident.result)
        && (!latestRequestId || latestRequestId === String(state.whichResident.requestId || ""));
      if (latest?.status === "failed") {
        window.clearTimeout(whichResidentRetryTimer);
        whichResidentRetryTimer = 0;
        whichResidentInputWatch?.destroy?.();
        whichResidentInputWatch = null;
        state.whichResident = {
          ...state.whichResident,
          query: String(latest.query || ""),
          submittedQuery: String(latest.query || ""),
          requestId: latestRequestId,
          requestSeq: observedRequestSeq + 1,
          residentId: "",
          messageId: "",
          busy: false,
          resultLoading: false,
          inputStatus: "failed",
          result: null,
          error: String(latest.error || "Resident 推荐请求失败"),
        };
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        return;
      }
      if (latest?.status === "completed" && latest.result && !showsLatest) {
        state.whichResident = {
          ...state.whichResident,
          query: String(latest.query || latest.result.query || state.whichResident.query || "").slice(0, 4000),
          submittedQuery: String(latest.query || latest.result.query || "").slice(0, 4000),
          busy: false,
          requestId: String(latest.request_id || ""),
          inputStatus: "completed",
          resultLoading: false,
          result: latest.result,
          error: "",
        };
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        return;
      }
      const residentId = String(latest?.resident_id || "").trim();
      const messageId = String(latest?.message_id || "").trim();
      if (!residentId || !messageId || showsLatest) return;
      if (
        residentId === String(state.whichResident.residentId || "")
        && messageId === String(state.whichResident.messageId || "")
      ) {
        if (!state.whichResident.submittedQuery && latest.query) {
          state.whichResident.submittedQuery = String(latest.query).slice(0, 4000);
          persistWhichResidentState({ immediate: true });
          renderAgentList();
        }
        return;
      }
      const requestSeq = observedRequestSeq + 1;
      state.whichResident = {
        ...state.whichResident,
        query: String(latest.query || state.whichResident.query || ""),
        submittedQuery: String(latest.query || ""),
        requestId: String(latest.request_id || ""),
        busy: true,
        requestSeq,
        residentId,
        messageId,
        inputStatus: String(latest.status || "queued"),
        resultLoading: false,
        result: null,
        error: "",
      };
      persistWhichResidentState({ immediate: true });
      renderAgentList();
      bindWhichResidentInputWatch(latest, requestSeq);
    }

    async function searchRoutingEvalResidents(query = "") {
      const requestSeq = Number(state.whichResident.correctionSearchRequestSeq || 0) + 1;
      state.whichResident = {
        ...state.whichResident,
        correctionSearchRequestSeq: requestSeq,
        correctionSearchLoading: true,
        correctionSearchError: "",
      };
      renderAgentList();
      try {
        const result = await residentDiscovery.searchRoutingEvalResidents({
          query: String(query || "").trim(),
          limit: 30,
        });
        if (requestSeq !== state.whichResident.correctionSearchRequestSeq) return;
        state.whichResident = {
          ...state.whichResident,
          correctionSearchLoading: false,
          correctionSearchResults: Array.isArray(result.results) ? result.results : [],
        };
      } catch (error) {
        if (requestSeq !== state.whichResident.correctionSearchRequestSeq) return;
        state.whichResident = {
          ...state.whichResident,
          correctionSearchLoading: false,
          correctionSearchError: error.message || String(error),
        };
      }
      renderAgentList();
      window.requestAnimationFrame(() => {
        const input = document.querySelector("[data-which-resident-correction-form] input[name='resident_search']");
        if (!input || !state.whichResident.correctionOpen) return;
        input.focus();
        const end = String(input.value || "").length;
        input.setSelectionRange?.(end, end);
      });
    }

    function scheduleRoutingEvalResidentSearch(query) {
      window.clearTimeout(whichResidentCorrectionSearchTimer);
      whichResidentCorrectionSearchTimer = window.setTimeout(() => {
        searchRoutingEvalResidents(query).catch(() => {});
      }, 220);
    }

    async function submitRoutingEvalCorrection(form) {
      if (state.whichResident.correctionSaving) return;
      const data = new FormData(form);
      const residentId = String(data.get("ground_truth_resident_id") || "").trim();
      const action = String(data.get("ground_truth_action") || "open").trim();
      if (!residentId) {
        window.PortalUI.toast(window.PortalI18n.t("请选择正确的 Resident。"), "warning");
        return;
      }
      state.whichResident = { ...state.whichResident, correctionSaving: true, evalMessage: "" };
      renderAgentList();
      try {
        const recommendations = Array.isArray(state.whichResident.result?.recommendations)
          ? state.whichResident.result.recommendations : [];
        const result = await residentDiscovery.submitRoutingEval({
            query: String(state.whichResident.result?.query || state.whichResident.submittedQuery || "").trim(),
            predicted: recommendations.map(item => ({
              resident_id: item.resident_id,
              title: item.title,
              recommended_action: item.recommended_action,
              reason: item.reason,
            })),
            ground_truth_resident_id: residentId,
            ground_truth_action: action,
            rationale: String(data.get("rationale") || "").trim(),
        });
        const caseId = String(result.submission?.dataset_case_id || "");
        const datasetAdded = result.dataset_added === true;
        state.whichResident = {
          ...state.whichResident,
          correctionSaving: false,
          correctionOpen: false,
          correctionResidentId: "",
          correctionResidentTitle: "",
          correctionSearchQuery: "",
          correctionSearchResults: [],
          correctionSearchError: "",
          correctionAction: "open",
          correctionRationale: "",
          evalMessage: datasetAdded
            ? `已加入评测集${caseId ? `：${caseId}` : ""}。`
            : `该 Ground Truth 已存在${caseId ? `：${caseId}` : ""}，已记录本次纠正。`,
        };
        window.PortalUI.toast(datasetAdded ? window.PortalI18n.t("已加入路由评测集。") : window.PortalI18n.t("评测集已有相同 Ground Truth。"), "success");
      } catch (error) {
        state.whichResident = {
          ...state.whichResident,
          correctionSaving: false,
          evalMessage: error.message || String(error),
        };
      }
      renderAgentList();
    }

    const boundRecommendationCards = new WeakSet();

    function whichResidentDetailsKey(finder = state.whichResident) {
      return finder.requestId ? `request:${finder.requestId}`
        : finder.residentId && finder.messageId ? `message:${finder.residentId}:${finder.messageId}` : "";
    }

    function bindWhichResidentControls(root) {
      const card = root.querySelector(".whichResidentPanel");
      if (!card || boundRecommendationCards.has(card)) return;
      boundRecommendationCards.add(card);
      const details = card.querySelector('.whichResidentRequestDetails');
      if (details) {
        const requestKey = whichResidentDetailsKey();
        details.open = !!requestKey && state.whichResident.detailsRequestKey === requestKey
          && state.whichResident.detailsOpen === true;
        details.addEventListener('toggle', () => {
          if (details.isConnected && requestKey && whichResidentDetailsKey() === requestKey) {
            state.whichResident.detailsRequestKey = requestKey;
            state.whichResident.detailsOpen = details.open;
            persistWhichResidentState({ immediate: true });
          }
        });
      }
      root.querySelectorAll("[data-which-resident-open-id]").forEach(button => {
        button.addEventListener("click", () => openWhichResidentRecommendation(
          button.dataset.whichResidentOpenId || "",
        ).catch(error => window.PortalUI.toast(error.message || error, "error")));
      });
      root.querySelectorAll("[data-fork-source-resident-id]").forEach(button => {
        button.addEventListener("click", () => openWhichResidentFork(
          button.dataset.forkSourceResidentId || "",
        ).catch(error => window.PortalUI.toast(error.message || error, "error")));
      });
      root.querySelector("[data-which-resident-correction-toggle]")?.addEventListener("click", () => {
        const opening = !state.whichResident.correctionOpen;
        state.whichResident = {
          ...state.whichResident,
          correctionOpen: opening,
          correctionSearchQuery: opening ? "" : state.whichResident.correctionSearchQuery,
          correctionSearchResults: opening ? [] : state.whichResident.correctionSearchResults,
          correctionSearchError: "",
          evalMessage: "",
        };
        renderAgentList();
        if (opening) searchRoutingEvalResidents("").catch(() => {});
      });
      const correctionForm = root.querySelector("[data-which-resident-correction-form]");
      correctionForm?.addEventListener("submit", event => {
        event.preventDefault();
        submitRoutingEvalCorrection(event.currentTarget).catch(() => {});
      });
      correctionForm?.querySelector("input[name='resident_search']")?.addEventListener("input", event => {
        const query = String(event.currentTarget.value || "").slice(0, 300);
        state.whichResident.correctionSearchQuery = query;
        state.whichResident.correctionResidentId = "";
        state.whichResident.correctionResidentTitle = "";
        scheduleRoutingEvalResidentSearch(query);
      });
      correctionForm?.querySelectorAll("[data-routing-eval-resident-id]").forEach(button => {
        button.addEventListener("click", () => {
          const residentId = String(button.dataset.routingEvalResidentId || "").trim();
          const resident = state.whichResident.correctionSearchResults.find(item => (
            String(item?.resident_id || "") === residentId
          )) || {};
          state.whichResident.correctionResidentId = residentId;
          state.whichResident.correctionResidentTitle = String(resident.title || residentId);
          state.whichResident.correctionSearchQuery = String(resident.title || residentId);
          renderAgentList();
        });
      });
      correctionForm?.querySelector("select[name='ground_truth_action']")?.addEventListener("change", event => {
        state.whichResident.correctionAction = String(event.currentTarget.value || "open");
      });
      correctionForm?.querySelector("textarea[name='rationale']")?.addEventListener("input", event => {
        state.whichResident.correctionRationale = String(event.currentTarget.value || "").slice(0, 1200);
      });
    }

    async function openWhichResidentFork(residentId) {
      const id = String(residentId || "").trim();
      const forkAdvice = state.whichResident.result?.fork_advice || {};
      const recommendation = state.whichResident.result?.recommendations?.find(item => item.resident_id === id)
        || (forkAdvice.source_resident_id === id ? {
          resident_id: id,
          title: forkAdvice.source_title || id,
          lineage_id: forkAdvice.source_lineage_id || "",
          lineage_title: forkAdvice.source_lineage_title || "",
        } : {});
      const exactResident = state.residents.find(item => String(item?.resident_id || "").trim() === id) || null;
      const sourceRoot = lineageSidebarRootByResidentId(id);
      const source = exactResident || sourceRoot || { resident_id: id, title: recommendation.title || id };
      const lineageId = String(recommendation.lineage_id || "").trim()
        || registeredLineageIdForResident(source)
        || registeredLineageIdForResident(sourceRoot);
      if (!id) throw new Error("缺少要创建分支的 Resident");
      if (!lineageId) {
        throw new Error("这个 Resident 不属于可写入 HATG 的 Lineage，无法创建分支");
      }
      await openResidentForkCreateModal(
        { ...source, resident_id: id, title: recommendation.title || source.title || id },
        {
          draftText: state.whichResident.query,
          intent: "lineage",
          lineageId,
        },
      );
    }

    async function openWhichResidentRecommendation(residentId) {
      const query = String(state.whichResident.query || "").trim();
      const opened = await selectResident(residentId, {
        forceView: true,
        revealInGraph: true,
      });
      if (opened) appendChatWorkspaceDraft(query);
      return opened;
    }

    function persistWhichResidentState({ immediate = false } = {}) {
      const finder = state.whichResident || {};
      const payload = {
        version: 9,
        query: String(finder.query || "").slice(0, 4000),
        submitted_query: String(finder.submittedQuery || "").slice(0, 4000),
        result: compactWhichResidentResult(finder.result),
        request_id: String(finder.requestId || ""),
        resident_id: String(finder.residentId || ""),
        message_id: String(finder.messageId || ""),
        details_request_key: String(finder.detailsRequestKey || ""),
        details_open: finder.detailsOpen === true,
        input_status: String(finder.inputStatus || ""),
        retry_count: Number(finder.retryCount || 0),
        error: String(finder.error || "").slice(0, 2000),
        busy: finder.busy === true,
        saved_at: new Date().toISOString(),
      };
      const patch = { whichResident: payload };
      if (immediate) writeLineageUiState(patch);
      else writeLineageUiStateSoon(patch);
    }

    let whichResidentStateHydrated = false;

    function restoreWhichResidentState(saved) {
      if (whichResidentStateHydrated) return;
      whichResidentStateHydrated = true;
      const restored = saved && typeof saved === "object" && !Array.isArray(saved)
        ? saved
        : {};
      const restoredResult = compactWhichResidentResult(restored.result);
      const restoredStatus = String(restored.input_status || "");
      state.whichResident = {
        ...state.whichResident,
        query: String(restored.query || "").slice(0, 4000),
        submittedQuery: String(restored.submitted_query || restored.result?.query || "").slice(0, 4000),
        busy: !restoredResult && !["failed", "cancelled", "unavailable", "missing"].includes(restoredStatus)
          && Boolean(restored.resident_id && restored.message_id),
        result: restoredResult,
        requestId: String(restored.request_id || ""),
        error: String(restored.error || "").slice(0, 2000),
        residentId: String(restored.resident_id || ""),
        messageId: String(restored.message_id || ""),
        detailsRequestKey: String(restored.details_request_key || ""),
        detailsOpen: restored.details_open === true,
        inputStatus: restoredStatus,
        retryCount: Number(restored.retry_count || 0),
        resultLoading: false,
      };
      if (restoredStatus === "cancelled" && state.whichResident.retryCount < 1 && state.whichResident.submittedQuery) {
        const requestSeq = Number(state.whichResident.requestSeq || 0) + 1;
        state.whichResident.requestSeq = requestSeq;
        window.setTimeout(() => {
          if (requestSeq === state.whichResident.requestSeq && state.whichResident.inputStatus === "cancelled") {
            retryCancelledWhichResident(requestSeq);
          }
        }, 0);
      }
      if (state.whichResident.busy) {
        const requestSeq = Number(state.whichResident.requestSeq || 0) + 1;
        state.whichResident.requestSeq = requestSeq;
        window.setTimeout(() => {
          if (requestSeq !== state.whichResident.requestSeq || state.whichResident.result) return;
          try {
            bindWhichResidentInputWatch(state.whichResident, requestSeq);
          } catch (error) {
            state.whichResident.busy = false;
            state.whichResident.error = error.message || String(error);
            renderAgentList();
          }
        }, 0);
      }
    }

    return Object.freeze({
      whichResidentCardHtml, recommendationConversationView, refreshRecommendationChats,
      restoreLatestWhichResident, bindWhichResidentControls, restoreWhichResidentState,
    });
  }

  window.PortalLineageWorkbenchWhichResident = Object.freeze({
    createWhichResident, dependencies: DEPENDENCIES,
  });
})();
