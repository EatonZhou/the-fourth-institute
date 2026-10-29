(() => {
  "use strict";

  if (window.PortalPaperReviews) return;

  // AI reviews, experiments and the deep read: their modes, starting them,
  // following their status and deleting a review.
  const DEPENDENCIES = Object.freeze([
    "$", "chip", "detailSectionLoadingHtml", "detailSectionsLoaded", "esc", "loadDetailSections",
    "loadLibrary", "md", "needPaperHtml", "paperApi", "paperStateHtml", "renderWorkspace", "setMode", "state",
    "updateDeepReadControls",
  ]);

  function createReviews(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperReviews is missing: ${missing.join(", ")}`);
    const {
      $, chip, detailSectionLoadingHtml, detailSectionsLoaded, esc, loadDetailSections, loadLibrary, md,
      needPaperHtml, paperApi, paperStateHtml, renderWorkspace, setMode, state, updateDeepReadControls,
    } = dependencies;

    function renderReviewMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["reviews"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("Reviews");
        return;
      }
      const reviews = detail.reviews || [];
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="portalToolbar paperToolbar" style="justify-content:space-between;margin-bottom:10px;">
            <div class="paneNote">${state.reviewInFlight ? window.PortalI18n.t("后台评审任务处理中，可离开或刷新页面。") : window.PortalI18n.t("Generate review from PDF text, analysis, and related papers.")}</div>
            <button class="portalButton is-compact is-primary" type="button" id="createReviewBtn" ${state.reviewInFlight ? "disabled" : ""}>${state.reviewInFlight ? "Generating..." : window.PortalI18n.t("Generate AI Review")}</button>
          </div>
          <div class="reviewOutput">
            ${state.reviewInFlight ? paperStateHtml(window.PortalI18n.t("后台评审正在进行，完成后从论文产物恢复显示。"), { state: "loading" }) : ""}
            ${state.reviewPollError ? paperStateHtml(state.reviewPollError, { state: "error" }) : ""}
            ${state.reviewJob?.status === "error" ? paperStateHtml(state.reviewJob.error || window.PortalI18n.t("后台评审失败，可重试。"), { state: "error" }) : ""}
            ${reviews.length ? reviews.map(renderReview).join("") : (!state.reviewInFlight ? paperStateHtml(window.PortalI18n.t("No AI review yet.")) : "")}
          </div>
        `;
    }

    function renderReview(review) {
      const incomplete = ["ai_incomplete", "ai_generated_unstructured"].includes(review.status);
      const rawResponse = review.raw_response ?? (incomplete ? review.reviewers?.[0]?.body || "" : "");
      return window.PortalI18n.html`
          <article class="apiCard">
            <div class="portalToolbar paperToolbar" style="justify-content:space-between;">
              <h3>${esc(review.title || window.PortalI18n.t("AI Review"))}</h3>
              <div class="chips">
                ${chip(review.status || "ai_generated", "accent")}
                ${review.overall_recommendation ? chip(review.overall_recommendation) : ""}
                ${review.confidence ? chip(`confidence: ${review.confidence}`) : ""}
                ${review.usage?.total_tokens ? chip(`${review.usage.total_tokens} tokens`) : ""}
                <button class="portalButton is-compact is-danger" type="button" data-delete-review="${esc(review.review_id || "")}">删除</button>
              </div>
            </div>
            ${incomplete ? paperStateHtml(window.PortalI18n.t("评审未完成：原始输出不是完整的结构化评审。"), { state: "error" }) : ""}
            ${review.summary && !incomplete ? window.PortalI18n.html`<div class="markdownBody">${md(review.summary)}</div>` : ""}
            ${incomplete ? window.PortalI18n.html`<details class="paperDisclosure"><summary>查看未完成评审的原始输出</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(rawResponse)}</pre></details>` : ""}
            ${(incomplete ? [] : review.reviewers || []).map(item => window.PortalI18n.html`
              <details class="paperDisclosure">
                <summary><span>${esc(item.title)} · ${esc(item.subtitle)}</span><span data-portal-icon="chevron-down" data-portal-icon-class="paperDisclosureIcon" class="paperDisclosureIcon" aria-hidden="true"></span></summary>
                <div class="markdownBody">${md(item.body || "")}</div>
              </details>
            `).join("")}
            ${!incomplete && review.author_action_memo ? window.PortalI18n.html`<details class="paperDisclosure" open>
              <summary><span>Author action memo</span><span data-portal-icon="chevron-down" data-portal-icon-class="paperDisclosureIcon" class="paperDisclosureIcon" aria-hidden="true"></span></summary>
              <div class="markdownBody">${md(review.author_action_memo || "")}</div>
            </details>` : ""}
          </article>
        `;
    }

    function renderExpMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["exp"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("Exp");
        return;
      }
      const exp = detail.exp || {};
      const status = state.expStatus || {};
      const expRunning = ["queued", "running"].includes(String(status.status || ""));
      const expError = String(status.status || "") === "error" ? String(status.error || "Exp generation failed.") : "";
      const statusText = expRunning
        ? `${status.step || "queued"}${Number.isFinite(Number(status.progress)) ? ` · ${Number(status.progress)}%` : ""}`
        : (expError || window.PortalI18n.t("Extract datasets, baselines, metrics, implementation settings, results, and reproduction risks."));
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="portalToolbar paperToolbar" style="justify-content:space-between;margin-bottom:10px;">
            <div class="paneNote">${esc(statusText)}</div>
            <button class="portalButton is-compact is-primary" type="button" id="createExpBtn" ${expRunning ? "disabled" : ""}>${expRunning ? "Generating..." : (exp.content ? window.PortalI18n.t("Regenerate Exp") : window.PortalI18n.t("Generate Exp"))}</button>
          </div>
          <div class="reviewOutput">
            ${expRunning ? paperStateHtml(window.PortalI18n.t("Generating Exp in the background. You can leave this page and come back later."), { state: "loading" }) : ""}
            ${expError ? paperStateHtml(expError, { state: "error" }) : ""}
            ${exp.content ? window.PortalI18n.html`<article class="apiCard"><div class="markdownBody">${md(exp.content)}</div></article>` : (!expRunning && !expError ? paperStateHtml(window.PortalI18n.t("No Exp extraction yet.")) : "")}
          </div>
        `;
    }

    function scheduleReviewPoll(paperId) {
      clearTimeout(state.reviewPollTimer);
      state.reviewPollTimer = setTimeout(() => loadReviewStatus(paperId).catch(() => {}), 3000);
    }

    async function loadReviewStatus(paperId) {
      if (!paperId || String(state.selectedId || "") !== String(paperId)) return;
      try {
        const response = await paperApi.get(`/paper/api/reviews/status?paper_id=${encodeURIComponent(paperId)}`);
        if (String(state.selectedId || "") !== String(paperId)) return;
        const previous = state.reviewJob;
        state.reviewJob = response.job || null;
        state.reviewPollError = "";
        state.reviewInFlight = ["queued", "running"].includes(state.reviewJob?.status);
        clearTimeout(state.reviewPollTimer);
        state.reviewPollTimer = 0;
        if (state.reviewInFlight) scheduleReviewPoll(paperId);
        if (state.reviewJob?.status === "done" && (previous?.job_id !== state.reviewJob.job_id || previous?.status !== "done" || !(state.detail?.reviews || []).some(review => review.review_id === state.reviewJob.review_id))) {
          await loadDetailSections(paperId, ["reviews"], { force: true });
        }
        if (state.mode === "review") renderWorkspace();
      } catch (error) {
        if (String(state.selectedId || "") !== String(paperId)) return;
        state.reviewPollError = "暂时无法获取评审状态；任务可能仍在运行，正在重试。";
        scheduleReviewPoll(paperId);
        if (state.mode === "review") renderWorkspace();
        throw error;
      }
    }

    async function createReview() {
      if (!state.selectedId || state.reviewInFlight) return;
      const paperId = String(state.selectedId);
      state.reviewInFlight = true;
      state.reviewPollError = "";
      renderWorkspace();
      try {
        const job = await paperApi.request("/paper/api/reviews/start", {
          method: "POST",
          body: JSON.stringify({ paper_id: paperId }),
        });
        if (String(state.selectedId || "") !== paperId) return;
        state.reviewJob = job;
        await loadReviewStatus(paperId);
      } catch (error) {
        if (String(state.selectedId || "") === paperId) {
          state.reviewInFlight = false;
          if (state.mode === "review") renderWorkspace();
        }
        throw error;
      }
    }

    async function createExp() {
      await startExp();
      setMode("exp");
    }

    async function deleteReview(reviewId) {
      const id = String(reviewId || "").trim();
      if (!id) return;
      if (!await window.PortalUI.confirmAction("delete", "这条 review")) return;
      await paperApi.request("/paper/api/reviews/delete", {
        method: "POST",
        body: JSON.stringify({ review_id: id }),
      });
      if (state.detail?.reviews) {
        state.detail.reviews = state.detail.reviews.filter(item => String(item.review_id || "") !== id);
      }
      await loadLibrary({ keepDetail: true });
      if (state.mode === "review") renderWorkspace();
    }

    async function loadDeepReadStatus(paperId = state.selectedId, { render = true } = {}) {
      if (!paperId) {
        state.deepReadStatus = null;
        return null;
      }
      const targetPaperId = String(paperId || "");
      const status = await paperApi.request(`/paper/api/deep-read/status?paper_id=${encodeURIComponent(targetPaperId)}`);
      if (String(state.selectedId || "") !== targetPaperId) return status;
      state.deepReadStatus = status;
      if (render && state.mode === "reader") renderWorkspace();
      return status;
    }

    async function refreshDeepReadProjection() {
      const previousStatus = String(state.deepReadStatus?.status || "");
      const status = await loadDeepReadStatus(state.selectedId, { render: false });
      const currentStatus = String(status?.status || "");
      if (currentStatus === "done" && ["queued", "running"].includes(previousStatus)) {
        await loadDetailSections(state.selectedId, ["analysis", "summary"], { render: false, force: true });
        await loadLibrary({ keepDetail: true });
        renderWorkspace();
        return status;
      }
      updateDeepReadControls();
      return status;
    }

    async function startDeepRead() {
      if (!state.selectedId) return;
      const job = await paperApi.request("/paper/api/deep-read/start", {
        method: "POST",
        body: JSON.stringify({ paper_id: state.selectedId }),
      });
      state.deepReadStatus = job;
      updateDeepReadControls();
    }

    async function loadExpStatus(paperId = state.selectedId, { render = true } = {}) {
      if (!paperId) {
        state.expStatus = null;
        return null;
      }
      const targetPaperId = String(paperId || "");
      const status = await paperApi.request(`/paper/api/exp/status?paper_id=${encodeURIComponent(targetPaperId)}`);
      if (String(state.selectedId || "") !== targetPaperId) return status;
      state.expStatus = status;
      if (render && state.mode === "exp") renderWorkspace();
      return status;
    }

    async function refreshExpProjection({ snapshot = false } = {}) {
      const previousStatus = String(state.expStatus?.status || "");
      const status = await loadExpStatus(state.selectedId, { render: false });
      const currentStatus = String(status?.status || "");
      if (currentStatus === "done" && (snapshot || ["queued", "running"].includes(previousStatus))) {
        await loadDetailSections(state.selectedId, ["exp"], { render: false, force: true });
        await loadLibrary({ keepDetail: true });
        renderWorkspace();
        return status;
      }
      if (state.mode === "exp") renderWorkspace();
      return status;
    }

    async function startExp() {
      if (!state.selectedId) return;
      const job = await paperApi.request("/paper/api/exp/start", {
        method: "POST",
        body: JSON.stringify({ paper_id: state.selectedId }),
      });
      state.expStatus = job;
      renderWorkspace();
    }

    return Object.freeze({
      createExp, createReview, deleteReview, loadDeepReadStatus, loadExpStatus, loadReviewStatus,
      refreshDeepReadProjection, refreshExpProjection, renderExpMode, renderReviewMode, startDeepRead,
    });
  }

  window.PortalPaperReviews = Object.freeze({ createReviews, dependencies: DEPENDENCIES });
})();
