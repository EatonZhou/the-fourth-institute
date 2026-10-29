(() => {
  "use strict";

  if (window.PortalPaperCollector) return;

  // Collector jobs: their list and status, selecting one as the Library's collection, starting and deleting jobs.
  const DEPENDENCIES = Object.freeze([
    "$", "activeCollectorJobHasResults", "alertError", "esc", "loadLibrary", "paperApi", "state",
    "writeUiState",
  ]);

  function createCollector(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperCollector is missing: ${missing.join(", ")}`);
    const {
      $, activeCollectorJobHasResults, alertError, esc, loadLibrary, paperApi, state, writeUiState,
    } = dependencies;

    function collectorHasPending(jobs = state.collectorJobs) {
      return (jobs || []).some(job => ["queued", "running"].includes(String(job.status || "")));
    }

    function collectorStatusText(job) {
      const status = String(job.status || "queued");
      if (status === "done") return `${job.result_count ?? job.registered_count ?? 0} found · ${job.registered_count || 0} added`;
      if (status === "error") return "error";
      return status;
    }

    function renderCollectorJobs() {
      const box = $("collectorJobList");
      if (!box) return;
      const expandedFailures = new Set(Array.from(box.querySelectorAll("[data-collector-error][open]"), item => item.dataset.collectorError));
      const jobs = (state.collectorJobs || []).slice(0, 3);
      box.innerHTML = jobs.map(job => {
        const status = String(job.status || "queued");
        const detail = job.summary || job.error || job.step || "";
        const jobId = String(job.job_id || "");
        const selectable = Number(job.result_count || job.registered_count || 0) > 0;
        const disabled = selectable ? "" : " disabled";
        const active = jobId && jobId === state.activeCollectorJobId ? " active" : "";
        return window.PortalI18n.html`
            <div class="collectorJobItem${active}">
              <button class="collectorJobMain" type="button" data-collector-select="${esc(jobId)}"${disabled}>
                <span class="collectorJobTop">
                  <span class="collectorJobDirection">${esc(job.direction || "")}</span>
                  <span class="collectorJobStatus ${esc(status)}">${esc(collectorStatusText(job))}</span>
                </span>
                ${detail ? window.PortalI18n.html`<span class="collectorJobMeta">${esc(detail)}</span>` : ""}
              </button>
              <span class="collectorJobActions">
                <button class="collectorJobAction" type="button" data-collector-delete="${esc(jobId)}" title="删除收集任务" aria-label="删除收集任务" data-portal-icon="close"></button>
              </span>
              ${status === "error" ? window.PortalI18n.html`<details class="collectorJobFailure" data-collector-error="${esc(jobId)}"${expandedFailures.has(jobId) ? " open" : ""}>
                <summary>失败详情</summary>
                <div class="collectorJobFailureBody">${esc(job.error || window.PortalI18n.t("收集任务未完成"))}</div>
                <div>Job: <code>${esc(jobId)}</code></div>
                ${job.invocation?.resident_id ? window.PortalI18n.html`<div>Resident: <code>${esc(job.invocation.resident_id)}</code></div>` : ""}
                ${job.invocation?.input_message_id ? window.PortalI18n.html`<div>Input: <code>${esc(job.invocation.input_message_id)}</code></div>` : ""}
              </details>` : ""}
            </div>
          `;
      }).join("");
      const btn = $("collectorBtn");
      if (btn) {
        btn.disabled = !!state.collectorStarting;
        btn.textContent = state.collectorStarting ? "Starting" : "Collect";
      }
    }

    async function selectCollectorJob(jobId) {
      const job = (state.collectorJobs || []).find(item => item.job_id === jobId);
      if (!job || Number(job.result_count || job.registered_count || 0) <= 0) return;
      state.activeCollectorJobId = state.activeCollectorJobId === job.job_id ? "" : job.job_id;
      if (state.activeCollectorJobId) {
        state.activeSemanticSearchId = "";
        state.semanticSearch?.clearActive?.({ notify: false });
      }
      state.mode = "library";
      writeUiState();
      renderCollectorJobs();
      await loadLibrary({ keepDetail: false });
    }

    async function loadCollectorJobs({ render = true } = {}) {
      const hadPending = collectorHasPending();
      const hadActiveResults = activeCollectorJobHasResults();
      const payload = await paperApi.request("/paper/api/collector/jobs");
      state.collectorJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
      if (state.activeCollectorJobId) {
        const active = state.collectorJobs.find(job => job.job_id === state.activeCollectorJobId);
        if (!active || Number(active.result_count || active.registered_count || 0) <= 0) {
          state.activeCollectorJobId = "";
          writeUiState();
        }
      }
      if (render) renderCollectorJobs();
      const hasPending = collectorHasPending();
      if (hadPending && !hasPending) loadLibrary({ keepDetail: true }).catch(alertError);
      else if (!hadActiveResults && activeCollectorJobHasResults()) {
        loadLibrary({ keepDetail: true }).catch(alertError);
      }
      return payload;
    }

    async function submitCollector(event) {
      event?.preventDefault?.();
      if (state.collectorStarting) return;
      const input = $("collectorInput");
      const limit = $("collectorMaxPapers");
      if (!limit.reportValidity()) return;
      const maxPapers = Number(limit.value);
      const direction = String(input?.value || "").trim();
      if (!direction) {
        window.PortalUI.toast(window.PortalI18n.t("请输入论文收集需求。"), "warning");
        return;
      }
      state.collectorStarting = true;
      renderCollectorJobs();
      try {
        const job = await paperApi.request("/paper/api/collector/start", {
          method: "POST",
          body: JSON.stringify({ direction, max_papers: maxPapers }),
        });
        if (input) input.value = "";
        state.collectorJobs = [job, ...(state.collectorJobs || []).filter(item => item.job_id !== job.job_id)];
        renderCollectorJobs();
      } finally {
        state.collectorStarting = false;
        renderCollectorJobs();
      }
    }

    async function deleteCollectorJob(jobId) {
      if (!jobId) return;
      await paperApi.request("/paper/api/collector/delete", {
        method: "POST",
        body: JSON.stringify({ job_id: jobId }),
      });
      state.collectorJobs = (state.collectorJobs || []).filter(item => item.job_id !== jobId);
      if (state.expandedCollectorJobId === jobId) state.expandedCollectorJobId = "";
      if (state.activeCollectorJobId === jobId) {
        state.activeCollectorJobId = "";
        writeUiState();
        await loadLibrary({ keepDetail: false });
      }
      renderCollectorJobs();
    }

    return Object.freeze({
      deleteCollectorJob, loadCollectorJobs, renderCollectorJobs, selectCollectorJob, submitCollector,
    });
  }

  window.PortalPaperCollector = Object.freeze({ createCollector, dependencies: DEPENDENCIES });
})();
