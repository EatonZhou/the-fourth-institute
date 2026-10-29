(() => {
  "use strict";

  // A run's page and the list of retained runs (decision_f2a6a5dd4646).  A
  // run's state comes from its record only: the page reads it again every
  // 900 ms until the run ends, and never infers a result from output text.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;
  const rank = status => ({ failed: 0, error: 0, running: 1, queued: 1, cancelling: 1, cancelled: 2, unsupported: 2, passed: 3 })[status] ?? 2;
  const list = value => (Array.isArray(value) ? value : []).map(item => typeof item === "string" ? item : JSON.stringify(item));
  const filled = value => Boolean(value && typeof value === "object" && Object.keys(value).length);

  async function deleteReports(runIds) {
    const ids = [...new Set(runIds)].filter(Boolean);
    if (!ids.length) return [];
    const plural = ids.length > 1;
    const accepted = await window.PortalUI.confirm({
      title: plural ? window.PortalI18n.text`删除 ${ids.length} 份运行报告？` : t("删除这份运行报告？"),
      message: plural ? window.PortalI18n.text`将永久删除所选的 ${ids.length} 份运行报告及其运行产物。` : window.PortalI18n.text`将永久删除运行报告 ${ids[0]} 及其运行产物。`,
      description: t("此操作无法撤销；正在执行的运行不会被删除。"),
      confirmLabel: plural ? window.PortalI18n.text`删除 ${ids.length} 份报告` : t("删除报告"),
      tone: "danger",
    });
    if (!accepted) return [];
    const result = await T.api("/test/api/runs/delete", { method: "POST", body: JSON.stringify({ run_ids: ids }) });
    const deleted = result.deleted_run_ids || ids;
    T.forgetRuns(deleted);
    T.notify(window.PortalI18n.text`已删除 ${deleted.length} 份运行报告。`, "success");
    if (result.artifact_warnings?.length) T.notify(result.artifact_warnings.join("；"), "warning");
    return deleted;
  }

  function create({ root }) {
    let run = null;
    let runId = "";
    let timer = 0;
    let generation = 0;
    let pending = "";
    let pollError = "";
    let missing = false;
    let disposed = false;

    const stop = () => { window.clearTimeout(timer); timer = 0; generation += 1; };
    const selected = () => run?.plan?.selected || [];
    const planned = caseId => selected().find(item => item.case_id === caseId);
    const caseTitle = caseId => planned(caseId)?.case_title || planned(caseId)?.description || caseId;

    function schedule(delay = 900) {
      const current = generation;
      timer = window.setTimeout(() => void poll(current), delay);
    }

    async function poll(current) {
      if (current !== generation || !run || T.terminalStatuses.has(run.status)) return;
      try {
        const next = await T.api(`/test/api/run?run_id=${encodeURIComponent(run.run_id)}`);
        if (current !== generation || disposed) return;
        run = next;
        pollError = "";
        T.rememberRun(run);
        render();
        if (!T.terminalStatuses.has(run.status)) schedule();
      } catch (error) {
        if (current !== generation || disposed) return;
        pollError = window.PortalI18n.text`读取运行状态失败：${error.message}。1.5 秒后重试。`;
        render();
        schedule(1500);
      }
    }

    async function act(name, task) {
      if (pending) return;
      pending = name;
      render();
      try {
        await task();
      } catch (error) {
        const labels = { cancel: t("中止运行"), delete: t("删除运行报告") };
        T.notify(window.PortalI18n.text`${labels[name] || t("操作")}失败：${error.message || error}`, "error");
      } finally {
        pending = "";
        if (!disposed && root.isConnected && run) render();
      }
    }

    function heroHtml() {
      const results = run.results || [];
      const count = status => results.filter(item => item.status === status).length;
      const problems = results.filter(item => T.problemStatuses.has(item.status)).length;
      const active = results.filter(item => T.activeStatuses.has(item.status)).length;
      const canCancel = Boolean(run.created_by && run.created_by === T.access().actor) && !T.terminalStatuses.has(run.status);
      const deletable = T.canOperate() && T.terminalStatuses.has(run.status);
      const implementation = run.plan?.request?.implementation || {};
      const execution = run.report?.execution || {};
      const summary = { ...run, case_count: T.runCaseIds(run).length, title: selected().length === 1 ? selected()[0].case_title : "" };
      return window.PortalI18n.html`<header class="testHero is-run" data-run-status="${esc(run.status)}">
        <div class="testHeroIcon" data-portal-icon="${T.isTauRun(run) ? "chat" : "terminal"}" aria-hidden="true"></div>
        <div class="testHeroMain">
          <div class="testHeroTitle"><h1>${esc(T.runTitle(summary))}</h1><div class="testHeroMeta">${T.statusHtml(run.status, "is-large")}${T.chip(T.isTauRun(run) ? t("TAU Campaign") : t("本地运行"))}${implementation.profile ? T.chip(implementation.profile) : ""}${execution.target ? T.chip(window.PortalI18n.text`目标 ${execution.target}`) : ""}</div></div>
          <p class="testHeroDescription">${T.personHtml(run.created_by)} ${esc(window.PortalI18n.text`发起于 ${window.PortalUI.formatDateTime(run.created_at)}`)}${run.started_at ? esc(window.PortalI18n.text` · 用时 ${T.duration(run.started_at, run.finished_at)}`) : ""}</p>
          <code class="testHeroKey">${esc(run.run_id)}</code>
        </div>
        <div class="testHeroActions">
          ${canCancel ? `<button class="portalButton is-compact is-danger" type="button" data-run-cancel ${pending ? "disabled" : ""}>${esc(pending === "cancel" ? t("正在中止…") : t("中止"))}</button>` : ""}
          ${T.canOperate() && T.runCaseIds(run).length ? `<button class="portalButton is-compact" type="button" data-run-again data-portal-icon="refresh"><span>${esc(t("再次运行"))}</span></button>` : ""}
          <button class="portalButton is-compact" type="button" data-run-copy data-portal-icon="copy"><span>${esc(t("复制链接"))}</span></button>
          ${deletable ? `<button class="portalButton is-compact is-quiet" type="button" data-run-delete data-portal-icon="trash" ${pending ? "disabled" : ""}><span>${esc(t("删除"))}</span></button>` : ""}
        </div>
        <div class="testStats">
          <span class="testStat"><strong>${results.length || T.runCaseIds(run).length}</strong><span>条用例</span></span>
          <span class="testStat is-ok"><strong>${count("passed")}</strong><span>通过</span></span>
          <span class="testStat${problems ? " is-danger" : ""}"><strong>${problems}</strong><span>有问题</span></span>
          ${active ? window.PortalI18n.html`<span class="testStat"><strong>${active}</strong><span>进行中</span></span>` : ""}
          <span class="testStat"><strong>${(run.groups || []).length}</strong><span>${esc(T.isTauRun(run) ? t("个模块") : t("个分组"))}</span></span>
        </div>
      </header>`;
    }

    function resultHtml(result) {
      const details = result.details || {};
      const issues = list(details.issues);
      const evidence = list(details.evidence);
      const visual = details.visual_review && typeof details.visual_review === "object" ? details.visual_review : {};
      const { summary: _s, issues: _i, evidence: _e, user_experience: experience, diagnosis, visual_review: _v, conversation, ...metadata } = details;
      const visualStatus = ["passed", "failed"].includes(visual.status) ? visual.status : "";
      const key = `${result.group_id || ""}:${result.item_index ?? result.case_id}`;
      const raw = [[t("体验记录"), experience, "experience"], [t("源码与 SPEC 诊断"), diagnosis, "diagnosis"], [t("其他运行元数据"), metadata, "metadata"]]
        .filter(([, value]) => filled(value))
        .map(([label, value, name]) => `<details class="testRaw" data-detail-key="${esc(`${key}:${name}`)}"><summary>${esc(label)}</summary><pre>${esc(JSON.stringify(value, null, 2))}</pre></details>`).join("");
      return window.PortalI18n.html`<article class="testResult" data-run-result data-result-status="${esc(result.status)}" data-case-id="${esc(result.case_id)}">
        <header>${T.statusHtml(result.status)}<a href="${esc(T.caseHref(result.case_id))}"><strong>${esc(caseTitle(result.case_id))}</strong></a>${result.failure_class && result.status !== "passed" ? T.chip(T.failureClassLabel(result.failure_class), "is-warning") : ""}${conversation?.resident_id ? `<button type="button" class="portalButton is-compact is-quiet" data-result-chat="${esc(conversation.resident_id)}" data-lineage="${esc(conversation.lineage_id || "")}" data-module="${esc(result.module_id || "")}" data-portal-icon="chat"><span>${esc(t("查看对话"))}</span></button>` : ""}</header>
        <p class="testResultSummary">${esc(details.summary || result.error || t("这次运行没有提供结果摘要。"))}</p>
        ${issues.length ? `<div class="testResultBlock"><strong>${esc(result.status === "passed" ? t("观察与改进项") : t("问题"))}</strong><ul>${issues.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}
        ${visual.summary ? window.PortalI18n.html`<div class="testResultBlock" data-run-visual-review><strong>截图审查${visualStatus ? ` · ${T.statusHtml(visualStatus)}` : ""}</strong><p>${esc(visual.summary)}</p></div>` : ""}
        ${evidence.length ? window.PortalI18n.html`<details class="testRaw" data-detail-key="${esc(`${key}:evidence`)}"><summary>证据 · ${evidence.length} 份</summary><ul class="testEvidence">${evidence.map(item => `<li><code>${esc(item)}</code></li>`).join("")}</ul></details>` : ""}
        ${raw}
      </article>`;
    }

    function groupHtml(group) {
      const results = [...(group.results || [])].sort((left, right) => rank(left.status) - rank(right.status));
      const firstModule = results[0]?.module_id || group.module_id;
      const label = group.label || (firstModule ? T.moduleLabel(firstModule) : group.group_id || t("未分类"));
      const problems = results.filter(item => T.problemStatuses.has(item.status)).length;
      const passed = results.filter(item => item.status === "passed").length;
      const active = results.filter(item => T.activeStatuses.has(item.status)).length;
      const outcome = [problems ? window.PortalI18n.text`${problems} 个有问题` : "", active ? window.PortalI18n.text`${active} 个进行中` : "", passed ? window.PortalI18n.text`${passed} 个通过` : ""].filter(Boolean).join(" · ") || t("等待结果");
      const progress = group.current_case_id ? window.PortalI18n.text`正在运行：${caseTitle(group.current_case_id)}` : group.waiting || "";
      return window.PortalI18n.html`<section class="testRunGroup" data-run-group data-result-status="${esc(group.status)}">
        <header><strong>${esc(label)}</strong>${T.statusHtml(group.status)}<span class="testMuted">${esc(outcome)}</span></header>
        ${progress ? `<p class="testRunProgress" role="status">${esc(progress)}</p>` : ""}
        ${group.error ? `<p class="testRunError" role="alert">${esc(group.error)}</p>` : ""}
        ${results.length ? results.map(resultHtml).join("") : window.PortalI18n.html`<p class="testEmpty">还没有结果。</p>`}
      </section>`;
    }

    function tauSummaryHtml() {
      if (!T.isTauRun(run)) return "";
      const execution = run.report?.execution || run.plan?.execution || {};
      const max = Number(execution.max_parallel_modules || 0);
      const classes = Object.entries(run.report?.summary?.failure_classes || {});
      return window.PortalI18n.html`<p class="testRunPolicy">${esc(max ? window.PortalI18n.text`模块之间最多 ${max} 个 Resident 同时执行，模块内的用例依次执行。` : t("模块之间同时执行，模块内的用例依次执行。"))}${classes.length ? ` ${classes.map(([key, value]) => T.chip(`${T.failureClassLabel(key)} ${value}`, "is-warning")).join("")}` : ""}</p>`;
    }

    function planHtml() {
      const rows = selected().map(item => {
        const implementation = item.implementation || {};
        const risk = item.risk || implementation.risk || "unspecified";
        return `<li><a href="${esc(T.caseHref(item.case_id))}">${esc(item.case_title || item.case_id)}</a> <code>${esc(implementation.entrypoint || "")}</code> ${T.chip(window.PortalI18n.text`执行风险 · ${T.riskLabel(risk)}`, T.riskTone(risk))}</li>`;
      }).join("");
      const excluded = (run.plan?.excluded || []).map(item => `<li>${esc(item.case_title || item.case_id || "")}：${esc(item.reason || item.error || "")}</li>`).join("");
      return window.PortalI18n.html`<details class="testRaw" data-run-plan data-detail-key="plan"><summary>运行计划 · ${selected().length} 个实现${excluded ? window.PortalI18n.text` · 排除 ${(run.plan?.excluded || []).length}` : ""}</summary><ol class="testPlanList">${rows}</ol>${excluded ? window.PortalI18n.html`<p><strong>已排除</strong></p><ul class="testPlanList">${excluded}</ul>` : ""}</details>`;
    }

    function render() {
      if (missing) {
        root.innerHTML = window.PortalI18n.html`<article class="testRunPage">
          ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("运行记录"), href: T.pageHref("runs") }, { label: runId }])}
          <div data-run-feedback data-tone="error">${T.stateHtml("error", t("运行报告已不存在"), window.PortalI18n.text`${runId} 已被删除或从未存在。`, `<a class="portalButton is-compact" href="${esc(T.pageHref("runs"))}">${esc(t("查看其他运行"))}</a>`)}</div>
        </article>`;
        return;
      }
      const groups = [...(run.groups || [])].sort((left, right) => rank(left.status) - rank(right.status));
      const output = run.output || run.error || (T.terminalStatuses.has(run.status) ? t("运行已结束，没有 Runner 原始输出。") : t("等待输出…"));
      // A refresh keeps what the reader opened or closed.
      const toggled = new Map([...root.querySelectorAll("details[data-detail-key]")].map(node => [node.dataset.detailKey, node.open]));
      const sameRun = root.querySelector("[data-run-id]")?.dataset.runId === run.run_id;
      const failed = T.problemStatuses.has(run.status) && !T.isTauRun(run);
      root.innerHTML = window.PortalI18n.html`<article class="testRunPage" data-run-id="${esc(run.run_id)}">
        ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("运行记录"), href: T.pageHref("runs") }, { label: run.run_id }])}
        ${heroHtml()}
        ${pollError ? `<p class="testRunError" data-run-feedback role="alert">${esc(pollError)}</p>` : ""}
        <section class="testSection" data-test-section="results">
          <div class="testSectionHead"><h2>结果</h2>${run.error ? `<span class="testRunError">${esc(run.error)}</span>` : ""}</div>
          ${tauSummaryHtml()}
          ${groups.length ? groups.map(groupHtml).join("") : window.PortalI18n.html`<p class="testEmpty">${esc(T.terminalStatuses.has(run.status) ? t("这次运行没有分组结果。") : t("运行开始后在这里显示每个分组的结果。"))}</p>`}
        </section>
        <section class="testSection" data-test-section="output">
          <details class="testRaw" data-detail-key="output" ${failed ? "open" : ""}><summary>Runner 原始输出</summary><pre class="testOutput" data-run-raw-output></pre></details>
          ${planHtml()}
        </section>
      </article>`;
      // HTML parsing would drop a leading newline and fold CRLF; the retained output stays exact.
      root.querySelector("[data-run-raw-output]").textContent = output;
      if (sameRun) root.querySelectorAll("details[data-detail-key]").forEach(node => {
        if (toggled.has(node.dataset.detailKey)) node.open = toggled.get(node.dataset.detailKey);
      });
      window.PortalUIPrimitives?.mountIcons?.(root);
      T.fillNames(root);
      bind();
    }

    function bind() {
      root.querySelector("[data-run-copy]")?.addEventListener("click", () => void T.copyLink(T.runHref(run.run_id)));
      root.querySelector("[data-run-again]")?.addEventListener("click", () => T.prepareRun(T.runCaseIds(run), run.plan?.request?.implementation || {}, window.PortalI18n.text`再次运行 ${run.run_id}`));
      root.querySelector("[data-run-cancel]")?.addEventListener("click", () => act("cancel", async () => {
        stop();
        run = await T.api("/test/api/run/cancel", { method: "POST", body: JSON.stringify({ run_id: run.run_id }) });
        T.rememberRun(run);
        if (!T.terminalStatuses.has(run.status)) schedule();
      }));
      root.querySelector("[data-run-delete]")?.addEventListener("click", () => act("delete", async () => {
        const deleted = await deleteReports([run.run_id]);
        if (deleted.includes(run.run_id)) {
          stop();
          T.navigate(T.pageHref("runs"), { replace: true });
        }
      }));
      root.querySelectorAll("[data-result-chat]").forEach(button => button.addEventListener("click", () => {
        T.openChat({ resident_id: button.dataset.resultChat, lineage_id: button.dataset.lineage, title: window.PortalI18n.text`TAU · ${T.moduleLabel(button.dataset.module)}` });
      }));
    }

    async function load(id) {
      stop();
      runId = id;
      missing = false;
      pollError = "";
      if (run?.run_id !== id) {
        run = null;
        root.innerHTML = T.stateHtml("loading", t("正在读取运行…"));
      }
      void T.loadOverview().catch(() => null);
      try {
        const next = await T.api(`/test/api/run?run_id=${encodeURIComponent(id)}`);
        if (disposed || runId !== id) return null;
        run = next;
        T.rememberRun(run);
      } catch (error) {
        if (disposed || runId !== id) return null;
        if (error.status !== 404) throw error;
        missing = true;
        run = null;
      }
      render();
      if (run && !T.terminalStatuses.has(run.status)) schedule();
      return run;
    }

    return {
      load,
      run: () => run,
      destroy() { disposed = true; stop(); },
    };
  }

  // The retained runs, newest first, whoever started them.
  function createList({ root }) {
    let filter = "all";
    let kind = "all";
    let busy = false;
    let disposed = false;
    const chosen = new Set();

    const matches = run => (filter === "all" || (filter === "problem" ? T.problemStatuses.has(run.status) : filter === "active" ? T.activeStatuses.has(run.status) : run.status === filter))
      && (kind === "all" || (kind === "tau") === T.isTauRun(run));
    const deletable = run => T.canOperate() && T.terminalStatuses.has(run.status);

    function render() {
      const runs = T.recentRuns();
      const shown = runs.filter(matches);
      [...chosen].forEach(id => { if (!shown.some(run => run.run_id === id && deletable(run))) chosen.delete(id); });
      const segment = (name, value, current, label, total) => `<button type="button" class="testToggle" data-runs-${name}="${esc(value)}" aria-pressed="${value === current}">${esc(label)}${total === undefined ? "" : ` <small>${total}</small>`}</button>`;
      const counts = { all: runs.length, problem: runs.filter(run => T.problemStatuses.has(run.status)).length, passed: runs.filter(run => run.status === "passed").length, active: runs.filter(run => T.activeStatuses.has(run.status)).length };
      root.innerHTML = window.PortalI18n.html`<article class="testRunsPage">
        ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("运行记录") }])}
        <header class="testListHead"><h1>运行记录</h1><span class="testListCount">${esc(window.PortalI18n.text`保留最近 ${runs.length} 次运行，谁发起的都在这里`)}</span></header>
        <div class="testCatalogToolbar">
          <div class="testChipRow" role="group" aria-label="结果">${segment("filter", "all", filter, t("全部"), counts.all)}${segment("filter", "problem", filter, t("有问题"), counts.problem)}${segment("filter", "passed", filter, t("通过"), counts.passed)}${segment("filter", "active", filter, t("进行中"), counts.active)}</div>
          <div class="testChipRow" role="group" aria-label="执行方式">${segment("kind", "all", kind, t("全部方式"))}${segment("kind", "tau", kind, "TAU")}${segment("kind", "local", kind, t("本地运行"))}</div>
        </div>
        ${shown.length ? `<div class="testRunRows is-list">${shown.map(run => T.runRowHtml(run, { selectable: T.canOperate(), checked: chosen.has(run.run_id), disabled: busy || !deletable(run) })).join("")}</div>` : T.stateHtml("empty", t("没有符合条件的运行"))}
        ${chosen.size ? window.PortalI18n.html`<div class="testSelectionBar"><span>${esc(window.PortalI18n.text`已选 ${chosen.size} 份运行报告`)}</span><button type="button" class="portalButton is-compact is-quiet" data-runs-clear>清空选择</button><button type="button" class="portalButton is-compact is-danger" data-runs-delete ${busy ? "disabled" : ""}>${esc(busy ? t("正在删除…") : window.PortalI18n.text`删除 ${chosen.size} 份报告`)}</button></div>` : ""}
      </article>`;
      window.PortalUIPrimitives?.mountIcons?.(root);
      T.fillNames(root);
      root.querySelectorAll("[data-runs-filter]").forEach(button => button.addEventListener("click", () => { filter = button.dataset.runsFilter; render(); }));
      root.querySelectorAll("[data-runs-kind]").forEach(button => button.addEventListener("click", () => { kind = button.dataset.runsKind; render(); }));
      root.querySelectorAll("[data-select-run]").forEach(input => input.addEventListener("change", () => {
        input.checked ? chosen.add(input.dataset.selectRun) : chosen.delete(input.dataset.selectRun);
        render();
      }));
      root.querySelector("[data-runs-clear]")?.addEventListener("click", () => { chosen.clear(); render(); });
      root.querySelector("[data-runs-delete]")?.addEventListener("click", async () => {
        busy = true;
        render();
        try {
          (await deleteReports([...chosen])).forEach(id => chosen.delete(id));
        } catch (error) {
          T.notify(window.PortalI18n.text`删除运行报告失败：${error.message || error}`, "error");
        }
        busy = false;
        if (!disposed) render();
      });
    }

    async function load() {
      if (!T.overview()) root.innerHTML = T.stateHtml("loading", t("正在读取运行记录…"));
      else render();
      await T.loadOverview({ fresh: Boolean(T.overview()) });
      if (!disposed) render();
      return T.recentRuns();
    }

    const unsubscribe = T.subscribe(event => { if (!disposed && root.isConnected && (event === "runs" || event === "overview")) render(); });
    return { load, destroy() { disposed = true; unsubscribe(); } };
  }

  window.PortalTestRun = Object.freeze({ create, createList, deleteReports });
})();
