(() => {
  "use strict";
  const T = window.PortalTest;

  function create({ root }) {
    let draft = { caseIds: [], implementation: {} };
    let plan = null;
    let run = null;
    let timer = 0;
    let pollGeneration = 0;
    let pendingAction = "";
    let actionError = "";
    let pollError = "";
    let unavailableRunId = "";
    const selectedRunIds = new Set();
    const expandedSections = new Set(["plan", "current"]);

    const request = () => ({
      select: { case_ids: draft.caseIds },
      implementation: draft.implementation || {},
    });

    const upsert = value => {
      const normalized = {
        ...value,
        caseIds: [...new Set((value.plan?.selected || []).map(item => item.case_id))],
      };
      T.store.runs = [
        normalized,
        ...T.store.runs.filter(item => item.run_id !== value.run_id),
      ];
    };

    const rememberRun = (runId = run?.run_id) => {
      const url = new URL(location.href);
      if (runId) url.searchParams.set("run", runId);
      else url.searchParams.delete("run");
      history.replaceState({}, "", url);
    };

    const stopPolling = () => {
      window.clearTimeout(timer);
      timer = 0;
      pollGeneration += 1;
    };

    const schedulePoll = generation => {
      timer = window.setTimeout(() => poll(generation), 900);
    };

    const poll = async (generation = pollGeneration) => {
      const runId = run?.run_id;
      if (!runId || T.terminalStatuses.has(run.status)) return;
      try {
        const next = await T.api(`/test/api/run?run_id=${encodeURIComponent(runId)}`);
        if (generation !== pollGeneration || run?.run_id !== runId) return;
        run = next;
        pollError = "";
        upsert(run);
        T.store.emit("run", run);
        render();
        if (!T.terminalStatuses.has(run.status)) schedulePoll(generation);
      } catch (error) {
        if (generation !== pollGeneration || run?.run_id !== runId) return;
        pollError = `读取执行状态失败：${error.message}。控制台将在 1.5 秒后重试。`;
        T.notify(error.message, "error");
        render();
        timer = window.setTimeout(() => poll(generation), 1_500);
      }
    };

    const performAction = async (name, task) => {
      if (pendingAction) return;
      pendingAction = name;
      actionError = "";
      render();
      try {
        await task();
      } catch (error) {
        const labels = {
          preview: window.PortalI18n.t("生成计划"),
          start: window.PortalI18n.t("启动运行"),
          cancel: window.PortalI18n.t("中止运行"),
          open: window.PortalI18n.t("打开历史运行"),
          delete: window.PortalI18n.t("删除运行报告"),
        };
        actionError = `${labels[name] || "请求"}失败：${error.message || "未知错误"}`;
        T.notify(actionError, "error");
      } finally {
        pendingAction = "";
        render();
      }
    };

    const statusRank = status => ({
      failed: 0,
      error: 0,
      running: 1,
      queued: 1,
      cancelling: 1,
      cancelled: 2,
      unsupported: 2,
      passed: 3,
    })[status] ?? 2;

    const problemStatuses = new Set(["failed", "error", "cancelled", "unsupported"]);
    const activeStatuses = new Set(["running", "queued", "cancelling"]);

    const normalizedList = value => (Array.isArray(value) ? value : []).map(item => (
      typeof item === "string" ? item : JSON.stringify(item)
    ));

    const populatedRecord = value => Boolean(
      value
      && typeof value === "object"
      && Object.keys(value).length,
    );

    const resultHtml = result => {
      const details = result.details || {};
      const caseItem = T.store.case(result.case_id);
      const plannedItem = (run?.plan?.selected || []).find(item => item.case_id === result.case_id);
      const title = plannedItem?.case_title
        || plannedItem?.description
        || caseItem?.title
        || caseItem?.description
        || result.case_id;
      const issues = normalizedList(details.issues);
      const evidence = normalizedList(details.evidence);
      const experience = details.user_experience || {};
      const diagnosis = details.diagnosis || {};
      const visualReview = details.visual_review && typeof details.visual_review === "object"
        ? details.visual_review
        : {};
      const {
        summary: _summary,
        issues: _issues,
        evidence: _evidence,
        user_experience: _experience,
        diagnosis: _diagnosis,
        visual_review: _visualReview,
        ...metadata
      } = details;
      const attentionLabel = result.status === "passed" ? window.PortalI18n.t("条观察") : window.PortalI18n.t("个问题");
      const detailMeta = [
        issues.length ? `${issues.length} ${attentionLabel}` : "",
        evidence.length ? `${evidence.length} 份证据` : "",
        visualReview.summary ? "截图审查" : "",
      ].filter(Boolean).join(" · ");
      const issueHtml = issues.length
        ? window.PortalI18n.html`<section class="testRunDetailBlock"><strong>${result.status === "passed" ? window.PortalI18n.t("观察与改进项") : window.PortalI18n.t("问题")}</strong><ul>${issues.map(item => window.PortalI18n.html`<li>${T.escapeHtml(item)}</li>`).join("")}</ul></section>`
        : "";
      const evidenceHtml = evidence.length
        ? window.PortalI18n.html`<section class="testRunDetailBlock"><strong>证据</strong><ul class="testRunEvidence">${evidence.map(item => window.PortalI18n.html`<li><code>${T.escapeHtml(item)}</code></li>`).join("")}</ul></section>`
        : "";
      const visualStatus = ["passed", "failed"].includes(visualReview.status) ? visualReview.status : "";
      const visualReviewHtml = visualReview.summary
        ? window.PortalI18n.html`<section class="testRunDetailBlock" data-run-visual-review><strong>截图审查${visualStatus ? window.PortalI18n.html` · <span class="testStatus" data-status="${T.escapeHtml(visualStatus)}">${T.escapeHtml(T.statusLabel(visualStatus))}</span>` : ""}</strong><p>${T.escapeHtml(visualReview.summary)}</p></section>`
        : "";
      const experienceHtml = populatedRecord(experience)
        ? window.PortalI18n.html`<details><summary>用户体验原始记录</summary><pre class="testRunOutput">${T.escapeHtml(JSON.stringify(experience, null, 2))}</pre></details>`
        : "";
      const diagnosisHtml = populatedRecord(diagnosis)
        ? window.PortalI18n.html`<details><summary>源码 / SPEC 诊断原始记录</summary><pre class="testRunOutput">${T.escapeHtml(JSON.stringify(diagnosis, null, 2))}</pre></details>`
        : "";
      const metadataHtml = populatedRecord(metadata)
        ? window.PortalI18n.html`<details><summary>其他运行元数据</summary><pre class="testRunOutput">${T.escapeHtml(JSON.stringify(metadata, null, 2))}</pre></details>`
        : "";
      const hasDetails = Boolean(issueHtml || evidenceHtml || visualReviewHtml || experienceHtml || diagnosisHtml || metadataHtml);
      const statusLabel = T.statusLabel(result.status) || window.PortalI18n.t("状态未知");
      const failureClass = result.failure_class ? T.failureClassLabel(result.failure_class) : "";
      const summary = details.summary || result.error || window.PortalI18n.t("本次运行没有提供结果摘要。");
      return window.PortalI18n.html`<article class="testRunCard" data-run-result data-result-status="${T.escapeHtml(result.status)}" aria-label="${T.escapeHtml(`${title}：${statusLabel}`)}"><header><strong>${T.escapeHtml(title)}</strong></header><p><span class="testStatus" data-status="${T.escapeHtml(result.status)}">${T.escapeHtml(statusLabel)}</span>${failureClass ? window.PortalI18n.html` · <span class="testBadge" data-failure-class="${T.escapeHtml(result.failure_class)}">${T.escapeHtml(failureClass)}</span>` : ""} · ${T.escapeHtml(summary)}</p>${hasDetails ? window.PortalI18n.html`<details class="testRunDetails"><summary>详情${detailMeta ? ` · ${T.escapeHtml(detailMeta)}` : ""}</summary><div class="testSectionFlow"><div class="testMuted">用例 · <code>${T.escapeHtml(result.case_id)}</code></div>${issueHtml}${visualReviewHtml}${evidenceHtml}${experienceHtml}${diagnosisHtml}${metadataHtml}</div></details>` : ""}</article>`;
    };

    const groupHtml = group => {
      const results = [...(group.results || [])].sort((left, right) => (
        statusRank(left.status) - statusRank(right.status)
      ));
      const firstCase = results[0]?.case_id ? T.store.case(results[0].case_id) : null;
      const moduleId = group.module_id || group.group_id || "未分类模块";
      // Local runs label their pytest shards and Playwright runs; TAU groups are modules.
      const moduleLabel = group.label || (firstCase ? T.store.moduleLabel(firstCase) : moduleId);
      const count = status => results.filter(item => item.status === status).length;
      const passed = count("passed");
      const problems = results.filter(item => problemStatuses.has(item.status)).length;
      const active = results.filter(item => activeStatuses.has(item.status)).length;
      const unclassified = results.length - passed - problems - active;
      const outcome = [
        problems ? `${problems} 个问题` : "",
        active ? `${active} 个进行中` : "",
        unclassified ? `${unclassified} 个未完成` : "",
        passed ? `${passed} 个通过` : "",
      ].filter(Boolean).join(" · ") || "等待结果";
      const currentCase = group.current_case_id ? T.store.case(group.current_case_id) : null;
      const progress = currentCase?.title || group.current_case_id || group.waiting || outcome;
      const expanded = problems > 0
        || active > 0
        || activeStatuses.has(group.status)
        || Boolean(group.error);
      return window.PortalI18n.html`<article class="testCampaignModule" data-run-group data-result-status="${T.escapeHtml(group.status)}"><header><div><strong>${T.escapeHtml(moduleLabel)}</strong><span><span class="testStatus" data-status="${T.escapeHtml(group.status)}">${T.escapeHtml(T.statusLabel(group.status))}</span> · <code>${T.escapeHtml(moduleId)}</code> · ${T.escapeHtml(progress)}</span></div></header>${group.error ? window.PortalI18n.html`<p role="alert">${T.escapeHtml(group.error)}</p>` : ""}<details data-run-group-results ${expanded ? "open" : ""}><summary>${T.escapeHtml(outcome)}</summary><div class="testSectionFlow">${results.map(resultHtml).join("") || window.PortalI18n.t('<div class="testEmpty">这个模块还没有用例结果。</div>')}</div></details></article>`;
    };

    const campaignReportHtml = () => {
      if (!run || run.plan?.campaign !== "tau") return "";
      const results = run.results || [];
      const count = status => results.filter(item => item.status === status).length;
      const passed = count("passed");
      const problems = results.filter(item => problemStatuses.has(item.status)).length;
      const active = results.filter(item => activeStatuses.has(item.status)).length;
      const execution = run.report?.execution || run.plan?.execution || {};
      const maxParallel = Number(execution.max_parallel_modules || 0);
      const policy = maxParallel
        ? `模块间最多 ${maxParallel} 个 Resident 并发，模块内用例串行`
        : "模块间并发，模块内用例串行（历史 Run 未记录并发上限）";
      const outcome = [
        `${(run.groups || []).length} 个模块`,
        `${results.length} 条用例`,
        `${passed} 个通过`,
        `${problems} 个问题`,
        active ? `${active} 个进行中` : "",
      ].filter(Boolean).join(" · ");
      const metricsText = `功能模块${(run.groups || []).length}测试用例${results.length}通过${passed}问题${problems}`;
      return window.PortalI18n.html`<section class="testCampaignReport" aria-label="TAU · Agent 真实使用报告"><header><div><strong>TAU · Agent 真实使用摘要</strong><span><code>${T.escapeHtml(run.run_id)}</code></span></div><span class="testStatus" data-status="${T.escapeHtml(run.status)}">${T.escapeHtml(T.statusLabel(run.status))}</span></header><span class="testCampaignMetrics srOnly">${T.escapeHtml(metricsText)}</span><p data-run-outcome><strong>${T.escapeHtml(outcome)}</strong></p><p>${T.escapeHtml(policy)} · 受控账号 · DEV</p></section>`;
    };

    const resolvedPlanHtml = () => (plan?.selected || []).map(item => {
      const implementation = item.implementation || {};
      const runner = item.runner || {};
      const prompt = implementation.task_prompt || "";
      const risk = item.risk || implementation.risk || "unspecified";
      const profileRisk = item.profile_risk || "";
      const riskBadges = window.PortalI18n.html`<span class="testBadge ${T.riskTone(risk)}">执行风险 · ${T.escapeHtml(T.riskLabel(risk))}</span>${profileRisk ? window.PortalI18n.html`<span class="testBadge ${T.riskTone(profileRisk)}">环境风险 · ${T.escapeHtml(T.riskLabel(profileRisk))}</span>` : ""}`;
      const entrypoint = implementation.entrypoint || "未指定";
      return window.PortalI18n.html`<article class="testRunCard" data-plan-implementation><strong>${T.escapeHtml(item.description || item.case_title || item.case_id)}</strong><div class="testImplementationPath"><span>入口</span><code title="${T.escapeHtml(entrypoint)}">${T.escapeHtml(entrypoint)}</code></div><div class="testBadges"><span class="testBadge" title="${T.escapeHtml(implementation.id || window.PortalI18n.t("未指定"))}">实现 · ${T.escapeHtml(implementation.id || window.PortalI18n.t("未指定"))}</span><span class="testBadge">Runner · ${T.escapeHtml(runner.label || runner.id || implementation.runner || window.PortalI18n.t("未指定"))}</span><span class="testBadge">Profile · ${T.escapeHtml(item.profile || window.PortalI18n.t("无附加 Profile"))}</span>${riskBadges}</div>${prompt ? window.PortalI18n.html`<details class="testAgentPrompt"><summary><span>任务 Prompt</span><span class="testPromptHint">展开完整内容</span></summary><p>${T.escapeHtml(prompt)}</p></details>` : ""}</article>`;
    }).join("");

    const excludedPlanHtml = () => (plan?.excluded || []).map(item => {
      const reason = item.reason || item.error || "当前选择不包含此实现。";
      return window.PortalI18n.html`<article class="testRunCard" data-plan-excluded><strong>${T.escapeHtml(item.description || item.case_title || item.case_id || window.PortalI18n.t("已排除条目"))}</strong><p role="status">${T.escapeHtml(reason)}</p></article>`;
    }).join("");

    const feedbackHtml = () => {
      if (unavailableRunId) return window.PortalI18n.html`<div class="testEmpty" data-run-feedback data-tone="error" role="alert">运行报告已不存在：<code>${T.escapeHtml(unavailableRunId)}</code>。请从最近运行中选择其他报告，或<a href="/wall?view=wall">返回墙面</a>。</div>`;
      const message = actionError || pollError;
      return message
        ? window.PortalI18n.html`<div class="testEmpty" data-run-feedback data-tone="error" role="alert">${T.escapeHtml(message)}</div>`
        : "";
    };

    const deleteRunReports = async runIds => {
      const ids = [...new Set(runIds)].filter(Boolean);
      if (!ids.length) return;
      const plural = ids.length > 1;
      const accepted = await window.PortalUI.confirm({
        title: plural ? window.PortalI18n.text`删除 ${ids.length} 份运行报告？` : window.PortalI18n.t("删除这份运行报告？"),
        message: plural
          ? window.PortalI18n.text`将永久删除所选的 ${ids.length} 份运行报告及其运行产物。`
          : window.PortalI18n.text`将永久删除运行报告 ${ids[0]} 及其运行产物。`,
        description: window.PortalI18n.t("此操作无法撤销；正在执行的运行不会被删除。"),
        confirmLabel: plural ? window.PortalI18n.text`删除 ${ids.length} 份报告` : window.PortalI18n.t("删除报告"),
        tone: "danger",
      });
      if (!accepted) return;
      await performAction("delete", async () => {
        const result = await T.api("/test/api/runs/delete", {
          method: "POST",
          body: JSON.stringify({ run_ids: ids }),
        });
        const deleted = result.deleted_run_ids || ids;
        deleted.forEach(runId => selectedRunIds.delete(runId));
        if (run && deleted.includes(run.run_id)) {
          stopPolling();
          run = null;
          rememberRun();
          pollError = "";
        }
        T.store.removeRuns(deleted);
        T.notify(`已删除 ${deleted.length} 份运行报告。`, "success");
        if (result.artifact_warnings?.length) {
          T.notify(result.artifact_warnings.join("；"), "warning");
        }
      });
    };

    const render = () => {
      const cases = draft.caseIds.map(id => T.store.case(id)).filter(Boolean);
      const groups = [...(run?.groups || [])].sort((left, right) => (
        statusRank(left.status) - statusRank(right.status)
      ));
      const planGroups = plan?.execution?.groups || [];
      const canOperate = T.store.canOperate();
      const canCancel = Boolean(
        run?.created_by
        && run.created_by === T.store.catalog?.access?.actor,
      );
      const parallelModules = Math.min(
        planGroups.length,
        Number(plan?.execution?.max_parallel_modules || planGroups.length),
      );
      const busy = Boolean(pendingAction);
      const deletableRunIds = new Set(
        T.store.runs.filter(item => T.terminalStatuses.has(item.status)).map(item => item.run_id),
      );
      [...selectedRunIds].forEach(runId => {
        if (!deletableRunIds.has(runId)) selectedRunIds.delete(runId);
      });
      const selectedCount = selectedRunIds.size;
      const allSelected = Boolean(deletableRunIds.size) && selectedCount === deletableRunIds.size;
      const recent = T.store.runs.map(item => {
        const isTau = item.plan?.campaign === "tau"
          || (item.plan?.selected || []).some(entry => entry.runner?.id === "tau")
          || (item.results || []).some(entry => entry.runner === "tau");
        const runId = String(item.run_id || "");
        const compactRunId = runId.length > 18 ? `…${runId.slice(-10)}` : runId;
        const creator = item.created_by || "未知发起人";
        const deletable = canOperate && T.terminalStatuses.has(item.status);
        const checked = selectedRunIds.has(item.run_id);
        return window.PortalI18n.html`<article class="testRecentRunRow" data-selected="${checked}"><input data-select-run="${T.escapeHtml(runId)}" type="checkbox" aria-label="选择运行报告 ${T.escapeHtml(runId)}" ${checked ? "checked" : ""} ${busy || !deletable ? "disabled" : ""}><button class="testRecentRun" data-open-run="${T.escapeHtml(runId)}" type="button" title="打开运行报告 ${T.escapeHtml(runId)}" ${busy ? "disabled" : ""}><span class="testRecentRunTitle">${T.escapeHtml(isTau ? window.PortalI18n.t("TAU · Agent 真实使用") : window.PortalI18n.t("测试运行"))}</span><small><span>${T.escapeHtml(creator)}</span><code title="${T.escapeHtml(runId)}">${T.escapeHtml(compactRunId)}</code></small></button><strong class="testStatus testRecentRunStatus" data-status="${T.escapeHtml(item.status)}">${T.escapeHtml(T.statusLabel(item.status))}</strong><button class="portalButton is-icon is-quiet is-compact testRecentRunDelete" data-delete-run="${T.escapeHtml(runId)}" data-portal-icon="trash" type="button" aria-label="删除运行报告 ${T.escapeHtml(runId)}" data-portal-tooltip="删除运行报告" ${busy || !deletable ? "disabled" : ""}></button></article>`;
      }).join("");
      const historyToolbar = T.store.runs.length && canOperate
        ? window.PortalI18n.html`<div class="testRecentRunToolbar"><label><input data-select-all-runs type="checkbox" ${allSelected ? "checked" : ""} ${busy || !deletableRunIds.size ? "disabled" : ""}>全选可删除报告</label><button class="portalButton is-danger" data-delete-selected type="button" ${busy || !selectedCount ? "disabled" : ""}>删除所选（<span data-selected-count>${selectedCount}</span>）</button></div>`
        : "";
      const planDetails = plan
        ? window.PortalI18n.html`<article class="testRunCard"><strong>${plan.selected_case_count} 条用例 · ${plan.selected_implementation_count} 个实现${plan.campaign === "tau" ? window.PortalI18n.text` · ${planGroups.length} 个模块 · 最多 ${parallelModules} 个并发` : ""}</strong><p>执行模式 · ${T.escapeHtml(plan.execution?.mode || window.PortalI18n.t("未指定"))} · ${plan.excluded?.length || 0} 条排除</p></article><div class="testPlanGroupLabel">运行配置</div><div class="testSectionFlow">${resolvedPlanHtml()}${excludedPlanHtml()}</div>`
        : "";
      const rawOutputText = run?.output || run?.error || (
        T.terminalStatuses.has(run?.status)
          ? window.PortalI18n.t("运行已结束，没有 Runner 原始输出。")
          : window.PortalI18n.t("等待输出…")
      );
      const current = run
        ? window.PortalI18n.html`<div class="testPanelActions"><button class="portalButton" data-cancel type="button" ${busy || !canCancel || T.terminalStatuses.has(run.status) ? "disabled" : ""}>${pendingAction === "cancel" ? window.PortalI18n.t("正在中止…") : run.plan?.campaign === "tau" ? window.PortalI18n.t("中止 TAU · Agent 真实使用") : window.PortalI18n.t("中止运行")}</button>${canOperate && T.terminalStatuses.has(run.status) ? window.PortalI18n.html`<button class="portalButton is-danger" data-delete-current type="button" ${busy ? "disabled" : ""}>删除此报告</button>` : ""}</div>${campaignReportHtml()}<div class="testCampaignGrid">${groups.map(groupHtml).join("") || [...(run.results || [])].sort((left, right) => statusRank(left.status) - statusRank(right.status)).map(resultHtml).join("")}</div><details><summary>Runner 原始输出 · 按需展开</summary><pre class="testRunOutput" data-run-raw-output>${T.escapeHtml(rawOutputText)}</pre></details>`
        : window.PortalI18n.markup('<div class="testEmpty">计划确认后在这里显示执行状态。</div>');
      const startDisabled = busy || !canOperate || !plan?.executable || Boolean(run);
      const startLabel = pendingAction === "start" ? window.PortalI18n.t("正在启动…") : run ? window.PortalI18n.t("已运行 · 重新生成计划后可再次运行") : window.PortalI18n.t("确认并运行");
      root.innerHTML = window.PortalI18n.html`${T.panelHead(window.PortalI18n.t("运行控制台"), window.PortalI18n.t("查看运行计划、执行进度与结果。"))}<div class="testPanelBody" aria-busy="${busy}"><div class="testSectionFlow">${feedbackHtml()}${canOperate ? "" : window.PortalI18n.t('<div class="testEmpty" role="note">当前账号可以生成和查看运行计划；启动测试需要 Test Lab 操作权限。</div>')}${T.section("plan", window.PortalI18n.t("运行计划"), window.PortalI18n.text`${cases.length} 条测试用例`, window.PortalI18n.html`<div class="testPanelActions"><button class="portalButton" data-preview type="button" ${busy || !cases.length ? "disabled" : ""}>${pendingAction === "preview" ? window.PortalI18n.t("正在生成…") : run ? window.PortalI18n.t("重新生成计划") : window.PortalI18n.t("生成计划")}</button><button class="portalButton is-primary" data-start type="button" ${startDisabled ? "disabled" : ""}>${startLabel}</button></div><div class="testPlanGroupLabel">测试用例</div><ol>${cases.map(item => window.PortalI18n.html`<li>${T.escapeHtml(item.description || item.title || item.id)} <span class="testBadge">${T.escapeHtml(item.importance)}</span><code title="${T.escapeHtml(item.key || item.id)}">${T.escapeHtml(item.key || item.id)}</code></li>`).join("")}</ol>${planDetails}`, expandedSections.has("plan"))}${T.section("current", run?.plan?.campaign === "tau" ? window.PortalI18n.t("当前 TAU · Agent 真实使用") : window.PortalI18n.t("当前运行"), T.statusLabel(run?.status) || window.PortalI18n.t("尚未运行"), current, expandedSections.has("current"), run?.status || "")}${T.section("history", window.PortalI18n.t("最近运行"), window.PortalI18n.text`${T.store.runs.length} 条记录`, window.PortalI18n.html`${historyToolbar}<div class="testRecentRuns">${recent || window.PortalI18n.t('<div class="testEmpty">暂无运行记录。</div>')}</div>`, expandedSections.has("history"))}</div></div>`;

      // HTML parsing normalizes CRLF and discards a leading newline in <pre>.
      // Keep the retained runner output exact, including literal markup.
      const rawOutput = root.querySelector("[data-run-raw-output]");
      if (rawOutput) rawOutput.textContent = rawOutputText;
      root.querySelectorAll("details[data-section]").forEach(details => {
        details.addEventListener("toggle", () => {
          if (details.open) expandedSections.add(details.dataset.section);
          else expandedSections.delete(details.dataset.section);
        });
      });

      root.querySelectorAll(".testAgentPrompt").forEach(details => {
        const hint = details.querySelector(".testPromptHint");
        const updateHint = () => {
          if (hint) hint.textContent = details.open ? window.PortalI18n.t("收起完整内容") : window.PortalI18n.t("展开完整内容");
        };
        updateHint();
        details.addEventListener("toggle", updateHint);
      });
      root.querySelector("[data-preview]")?.addEventListener("click", () => performAction("preview", async () => {
        plan = await T.api("/test/api/preview", {
          method: "POST",
          body: JSON.stringify(request()),
        });
        run = null;
        rememberRun();
      }));
      root.querySelector("[data-start]")?.addEventListener("click", () => performAction("start", async () => {
        stopPolling();
        run = await T.api("/test/api/run", {
          method: "POST",
          body: JSON.stringify(plan.request),
        });
        pollError = "";
        upsert(run);
        rememberRun();
        const generation = pollGeneration;
        window.setTimeout(() => poll(generation), 0);
      }));
      root.querySelector("[data-cancel]")?.addEventListener("click", () => performAction("cancel", async () => {
        stopPolling();
        run = await T.api("/test/api/run/cancel", {
          method: "POST",
          body: JSON.stringify({ run_id: run.run_id }),
        });
        pollError = "";
        upsert(run);
        if (!T.terminalStatuses.has(run.status)) schedulePoll(pollGeneration);
      }));
      root.querySelector("[data-delete-current]")?.addEventListener("click", () => deleteRunReports([run.run_id]));
      root.querySelector("[data-delete-selected]")?.addEventListener("click", () => deleteRunReports([...selectedRunIds]));
      root.querySelectorAll("[data-delete-run]").forEach(button => button.addEventListener("click", () => {
        deleteRunReports([button.dataset.deleteRun]);
      }));
      const syncSelectionControls = () => {
        const checkboxes = [...root.querySelectorAll("[data-select-run]:not(:disabled)")];
        const selectAll = root.querySelector("[data-select-all-runs]");
        const selected = checkboxes.filter(checkbox => checkbox.checked).length;
        if (selectAll) {
          selectAll.checked = Boolean(checkboxes.length) && selected === checkboxes.length;
          selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
        }
        const deleteSelected = root.querySelector("[data-delete-selected]");
        if (deleteSelected) deleteSelected.disabled = selected === 0;
        const count = root.querySelector("[data-selected-count]");
        if (count) count.textContent = String(selected);
      };
      root.querySelectorAll("[data-select-run]").forEach(checkbox => checkbox.addEventListener("change", () => {
        if (checkbox.checked) selectedRunIds.add(checkbox.dataset.selectRun);
        else selectedRunIds.delete(checkbox.dataset.selectRun);
        checkbox.closest(".testRecentRunRow")?.setAttribute("data-selected", String(checkbox.checked));
        syncSelectionControls();
      }));
      root.querySelector("[data-select-all-runs]")?.addEventListener("change", event => {
        root.querySelectorAll("[data-select-run]:not(:disabled)").forEach(checkbox => {
          checkbox.checked = event.currentTarget.checked;
          if (checkbox.checked) selectedRunIds.add(checkbox.dataset.selectRun);
          else selectedRunIds.delete(checkbox.dataset.selectRun);
          checkbox.closest(".testRecentRunRow")?.setAttribute("data-selected", String(checkbox.checked));
        });
        syncSelectionControls();
      });
      root.querySelectorAll("[data-open-run]").forEach(button => button.addEventListener("click", () => openRun(button.dataset.openRun)));
    };

    const openRun = runId => performAction("open", async () => {
      stopPolling();
      run = null;
      plan = null;
      draft = { caseIds: [], implementation: {} };
      unavailableRunId = "";
      pollError = "";
      rememberRun(runId);
      try {
        run = await T.api(`/test/api/run?run_id=${encodeURIComponent(runId)}`);
      } catch (error) {
        if (error.status === 404) unavailableRunId = runId;
        throw error;
      }
      plan = run.plan || null;
      draft = { caseIds: (plan?.selected || []).map(item => item.case_id), implementation: plan?.request?.implementation || {} };
      rememberRun();
      pollError = "";
      upsert(run);
      if (!T.terminalStatuses.has(run.status)) schedulePoll(pollGeneration);
    });

    const controller = T.panelController(root, render, {
      openRun,
      setDraft(next) {
        stopPolling();
        unavailableRunId = "";
        draft = next;
        plan = null;
        run = null;
        rememberRun();
        actionError = "";
        pollError = "";
        render();
      },
      ensureDraft(next) {
        if (!draft.caseIds.length) {
          draft = next;
          render();
        }
      },
      snapshot: () => ({ dirty: Boolean(plan && !run) }),
    });
    const destroy = controller.destroy;
    controller.destroy = () => {
      stopPolling();
      destroy();
    };
    return controller;
  }

  T.registerPanel({
    type: "run-console",
    title: window.PortalI18n.t("运行控制台"),
    icon: "play",
    component: "PortalTest.RunConsolePanel",
    create,
  });
})();
