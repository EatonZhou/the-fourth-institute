(function (global) {
  "use strict";

  if (global.PortalProjectPage) return;

  const RESOURCE_ID_RE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12}$/;
  const STAGES = Object.freeze([
    ["decision_state", "Decision", "方向"],
    ["implementation_state", "Implementation", "实现"],
    ["test_state", "Test", "测试"],
    ["release_state", "Release", "发布"],
    ["runtime_state", "Runtime", "运行"],
  ]);
  const SECTION_IDS = Object.freeze([
    ["overview", "一眼看懂"],
    ["delivery", "交付进度"],
    ["plan", "下一步"],
    ["people", "协作团队"],
    ["evidence", "证据与认知"],
  ]);

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;

  const {text, array, object, metricHtml, cardHtml: overviewCardHtml} = global.PortalProjectSummary;

  function humanize(value) {
    return text(value, "unknown")
      .replaceAll("_", " ")
      .replace(/\b\w/g, character => character.toUpperCase());
  }

  function formatDateTime(value) {
    const raw = text(value);
    if (!raw) return "Unknown";
    return global.PortalUI.formatDateTime(raw, {
      format: {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      },
    });
  }

  function stateTone(value) {
    const state = text(value, "unknown").toLowerCase();
    if (["failed", "failing", "degraded", "conflicted", "blocked", "error", "invalid", "high"].includes(state)) {
      return "danger";
    }
    if (["partial", "paused", "pending", "proposed", "candidate", "warning", "medium"].includes(state)) {
      return "warning";
    }
    if ([
      "active",
      "confirmed",
      "decided",
      "implemented",
      "passing",
      "released",
      "runtime_observed",
      "runtime_verified",
      "ready",
    ].includes(state)) {
      return "success";
    }
    return "neutral";
  }

  function stateExplanation(value) {
    const state = text(value, "unknown").toLowerCase();
    const explanations = {
      unknown: "Project State 还没有足够证据判断这一阶段。",
      none: "尚未登记这一阶段的正式状态。",
      not_started: "能力边界已记录，但这一阶段尚未开始。",
      partial: "只有部分实现或证据，不能视为完整交付。",
      proposed: "这是候选方案，尚未成为正式决定。",
      candidate: "这是候选计划，尚未承诺交付。",
      runtime_observed: "至少观察到一次真实运行，但还不等于通过完整运行验证。",
      conflicted: "存在互相冲突的事实或契约，需要明确收口。",
      degraded: "目前仍可使用，但有明确退化。",
      failed: "已有明确失败证据。",
      blocked: "被已知依赖或治理选择阻塞。",
    };
    return explanations[state] || `Project State 记录为 ${humanize(state)}。`;
  }

  function statusHtml(value, options = {}) {
    const state = text(value, "unknown");
    const label = text(options.label, humanize(state));
    const explanation = stateExplanation(state);
    return window.PortalI18n.html`<span class="portalStatus projectPageStatus" data-tone="${escapeHtml(stateTone(state))}" data-project-state="${escapeHtml(state)}" title="${escapeHtml(explanation)}" aria-label="${escapeHtml(`${label}。${explanation}`)}">${escapeHtml(label)}</span>`;
  }

  function stringListHtml(value, className = "projectPageInlineList") {
    const items = array(value).map(item => text(item)).filter(Boolean);
    if (!items.length) return "";
    return window.PortalI18n.html`<ul class="${escapeHtml(className)}">${items.map(item => window.PortalI18n.html`<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  }

  function evidenceHtml(refs) {
    const values = Array.from(new Set(array(refs).map(item => text(item)).filter(Boolean)));
    if (!values.length) return window.PortalI18n.html`<span class="projectPageEvidenceEmpty">暂无关联证据</span>`;
    return window.PortalI18n.html`<div class="projectPageEvidenceList">${values.map(reference => {
      if (RESOURCE_ID_RE.test(reference)) {
        return global.PortalResourceChips?.resourceChipHtml?.(
          reference,
          "projectPageEvidenceChip",
        ) || window.PortalI18n.html`<a href="/r/${encodeURIComponent(reference)}">${escapeHtml(reference)}</a>`;
      }
      if (reference.startsWith("/")) {
        const path = reference.split("#")[0];
        return window.PortalI18n.html`<a href="${escapeHtml(path)}" data-portal-file-path="${escapeHtml(path)}" data-portal-preview-link title="在共享 Preview 中打开证据">${escapeHtml(reference)}</a>`;
      }
      return window.PortalI18n.html`<code>${escapeHtml(reference)}</code>`;
    }).join("")}</div>`;
  }

  function recordEvidenceRefs(record) {
    const value = object(record);
    const refs = array(value.evidence_refs).map(item => text(item)).filter(Boolean);
    const canonicalId = text(value.id);
    if (RESOURCE_ID_RE.test(canonicalId)) refs.unshift(canonicalId);
    return Array.from(new Set(refs));
  }

  function sectionHtml(id, title, content, options = {}) {
    return window.PortalI18n.html`<section class="projectPageSection${options.className ? ` ${escapeHtml(options.className)}` : ""}" id="project-section-${escapeHtml(id)}" data-project-section="${escapeHtml(id)}">
      <div class="projectPageSectionHeading">
        <div>
          <span>${escapeHtml(options.eyebrow || window.PortalI18n.t("Project"))}</span>
          <h3>${escapeHtml(title)}</h3>
        </div>
        ${options.meta ? window.PortalI18n.html`<small>${escapeHtml(options.meta)}</small>` : ""}
      </div>
      ${content}
    </section>`;
  }

  function renderAnchorNav() {
    return window.PortalI18n.html`<nav class="projectPageAnchorNav" aria-label="Project page sections">
      ${SECTION_IDS.map(([id, label]) => window.PortalI18n.html`<a href="#project-section-${escapeHtml(id)}" data-project-anchor="${escapeHtml(id)}">${escapeHtml(label)}</a>`).join("")}
    </nav>`;
  }

  function renderHero(contract) {
    const project = object(contract.project);
    const brief = object(contract.brief);
    const snapshot = object(contract.snapshot);
    const source = object(contract.source);
    return window.PortalI18n.html`<header class="projectPageHero">
      <div class="projectPageHeroCopy">
        <span class="projectPageEyebrow">Project brief · 更新于 ${escapeHtml(formatDateTime(snapshot.generated_at || source.generated_at))}</span>
        <h2>${escapeHtml(text(brief.headline, project.objective || window.PortalI18n.t("项目目标尚未定义。")))}</h2>
        <p>${escapeHtml(text(brief.current_state, project.phase_inference?.statement || window.PortalI18n.t("当前阶段尚未形成可靠判断。")))}</p>
      </div>
      <div class="projectPageHeroMeta">
        ${statusHtml(project.status)}
        ${project.current_phase && project.current_phase !== "unknown"
          ? statusHtml(project.current_phase)
          : window.PortalI18n.html`<span class="projectPageQuietMeta">正式阶段未登记</span>`}
      </div>
    </header>`;
  }

  function renderOverview(contract) {
    const brief = object(contract.brief);
    const project = object(contract.project);
    const signals = object(brief.signals);
    const nextState = text(brief.next_move_state, "unknown");
    const successCriteria = text(project.project_level_success_criteria, "unknown");
    const successDefined = successCriteria.toLowerCase() !== "unknown";
    return window.PortalI18n.html`<div class="projectOverviewGrid">
      ${overviewCardHtml("PROGRESS", brief.progress, "progress")}
      ${overviewCardHtml(window.PortalI18n.t("NEEDS ATTENTION"), brief.needs_attention, "attention")}
      ${overviewCardHtml(
        nextState === "confirmed" ? window.PortalI18n.t("NEXT · CONFIRMED") : window.PortalI18n.t("NEXT · PROPOSED"),
        brief.next_move,
        nextState === "confirmed" ? "next-confirmed" : "next-candidate",
        { note: nextState === "candidate" ? window.PortalI18n.t("这是认知层建议，不代表项目承诺。") : "" },
      )}
      ${overviewCardHtml(
        window.PortalI18n.t("SUCCESS CRITERIA"),
        successDefined ? successCriteria : window.PortalI18n.t("项目级成功标准尚未定义。"),
        successDefined ? "success" : "gap",
        {
          action: successDefined
            ? ""
            : window.PortalI18n.html`<a href="#project-section-plan" data-project-anchor="plan">查看需要收口的治理事项</a>`,
        },
      )}
    </div>
    <div class="projectSignalStrip" aria-label="项目关键信号">
      ${metricHtml(window.PortalI18n.t("能力"), signals.capabilities)}
      ${metricHtml("Residents", signals.residents)}
      ${metricHtml(window.PortalI18n.t("待人审"), signals.human_review, { tone: Number(signals.human_review) > 0 ? "warning" : "neutral" })}
      ${metricHtml(window.PortalI18n.t("冲突"), signals.conflicts, { tone: Number(signals.conflicts) > 0 ? "danger" : "neutral" })}
      ${metricHtml(window.PortalI18n.t("风险"), signals.risks, { tone: Number(signals.risks) > 0 ? "warning" : "neutral" })}
    </div>`;
  }

  function stageSummary(capabilities, stageKey) {
    const records = array(capabilities);
    const values = records.map(item => text(item?.[stageKey], "unknown").toLowerCase());
    const doneStates = {
      decision_state: new Set(["decided", "confirmed"]),
      implementation_state: new Set(["implemented"]),
      test_state: new Set(["passing"]),
      release_state: new Set(["released"]),
      runtime_state: new Set(["runtime_observed", "runtime_verified"]),
    };
    const done = values.filter(value => doneStates[stageKey]?.has(value)).length;
    const attention = values.filter(value => ["failed", "failing", "blocked", "conflicted", "degraded"].includes(value)).length;
    const unknown = values.filter(value => ["unknown", "none", ""].includes(value)).length;
    return {
      total: values.length,
      done,
      attention,
      unknown,
    };
  }

  function renderDeliveryStages(capabilities) {
    return window.PortalI18n.html`<div class="projectDeliveryStages">${STAGES.map(([key, english, label]) => {
      const summary = stageSummary(capabilities, key);
      const ratio = summary.total ? Math.round((summary.done / summary.total) * 100) : 0;
      const tone = summary.attention ? "danger" : (summary.unknown === summary.total ? "neutral" : "success");
      return window.PortalI18n.html`<article class="projectDeliveryStage" data-stage="${escapeHtml(key)}" data-tone="${tone}">
        <div><span>${escapeHtml(label)}</span><small>${escapeHtml(english)}</small></div>
        <strong>${summary.done}<small> / ${summary.total}</small></strong>
        <div class="projectDeliveryBar" aria-label="${escapeHtml(`${label} ${ratio}%`)}"><i style="--project-stage-ratio:${ratio}%"></i></div>
        <p>${summary.attention ? window.PortalI18n.text`${summary.attention} 项异常` : summary.unknown ? window.PortalI18n.text`${summary.unknown} 项证据未知` : window.PortalI18n.t("证据已覆盖")}</p>
      </article>`;
    }).join("")}</div>`;
  }

  function capabilityHighlightsHtml(capabilities) {
    const records = array(capabilities);
    if (!records.length) {
      return global.PortalUIPrimitives?.asyncStateHtml?.({
        state: "empty",
        title: window.PortalI18n.t("尚未登记能力"),
        message: window.PortalI18n.t("Project State 没有能力交付条目。"),
        density: "compact",
      }) || "";
    }
    const scored = records.map((item, index) => {
      const capability = object(item);
      const states = STAGES.map(([key]) => text(capability[key], "unknown"));
      const concern = states.filter(state => ["failed", "blocked", "conflicted", "degraded"].includes(state)).length;
      const unknown = states.filter(state => ["unknown", "none"].includes(state)).length;
      return { capability, index, score: concern * 100 + unknown };
    }).sort((a, b) => b.score - a.score || a.index - b.index).slice(0, 6);
    return window.PortalI18n.html`<div class="projectCapabilityHighlights">${scored.map(({ capability }) => window.PortalI18n.html`
      <article>
        <div>
          <strong>${escapeHtml(text(capability.name, capability.id || window.PortalI18n.t("未命名能力")))}</strong>
          ${array(capability.scope).length ? window.PortalI18n.html`<span>${escapeHtml(array(capability.scope).join(" · "))}</span>` : ""}
        </div>
        <div class="projectCapabilityStateDots" aria-label="五阶段状态">
          ${STAGES.map(([key, , label]) => window.PortalI18n.html`<span data-tone="${escapeHtml(stateTone(capability[key]))}" title="${escapeHtml(`${label}：${humanize(capability[key])}`)}"></span>`).join("")}
        </div>
      </article>`).join("")}</div>`;
  }

  function renderCapabilityMatrix(capabilities) {
    const records = array(capabilities);
    if (!records.length) return "";
    return window.PortalI18n.html`<div class="projectCapabilityTableWrap" tabindex="0" aria-label="Capability delivery matrix">
      <table class="projectCapabilityTable">
        <thead><tr><th>Capability</th>${STAGES.map(([, label]) => window.PortalI18n.html`<th>${escapeHtml(label)}</th>`).join("")}</tr></thead>
        <tbody>${records.map(item => {
          const capability = object(item);
          return window.PortalI18n.html`<tr data-capability-id="${escapeHtml(text(capability.id))}">
            <th scope="row">
              <strong>${escapeHtml(text(capability.name, capability.id || window.PortalI18n.t("Unnamed capability")))}</strong>
              ${array(capability.scope).length ? window.PortalI18n.html`<span>${escapeHtml(array(capability.scope).join(" · "))}</span>` : ""}
              <details class="projectCapabilityEvidence">
                <summary>证据与不确定性</summary>
                ${evidenceHtml(capability.evidence_refs)}
                ${stringListHtml(capability.remaining_uncertainty)}
                ${capability.last_verified_at ? window.PortalI18n.html`<small>Last verified · ${escapeHtml(capability.last_verified_at)}</small>` : ""}
              </details>
            </th>
            ${STAGES.map(([key]) => window.PortalI18n.html`<td>${statusHtml(capability[key])}</td>`).join("")}
          </tr>`;
        }).join("")}</tbody>
      </table>
    </div>`;
  }

  function renderDelivery(contract) {
    const capabilities = array(contract.capabilities);
    return window.PortalI18n.html`${renderDeliveryStages(capabilities)}
      <div class="projectDeliveryHighlights">
        <div class="projectSubheading">
          <div><span>优先阅读</span><h4>最需要补证据的能力</h4></div>
          <small>按异常与未知状态排序</small>
        </div>
        ${capabilityHighlightsHtml(capabilities)}
      </div>
      ${capabilities.length ? window.PortalI18n.html`<details class="projectPageDisclosure">
        <summary>查看完整五阶段交付矩阵 · ${capabilities.length} 项能力</summary>
        <div class="projectPageDisclosureBody">${renderCapabilityMatrix(capabilities)}</div>
      </details>` : ""}`;
  }

  function issueListHtml(items, kind) {
    const records = array(items);
    if (!records.length) return window.PortalI18n.html`<p class="projectPageMuted">无已登记事项</p>`;
    return window.PortalI18n.html`<div class="projectIssueList">${records.map(item => {
      const record = typeof item === "string" ? { statement: item } : object(item);
      const state = text(record.severity || record.type, kind);
      return window.PortalI18n.html`<article>
        <div class="projectIssueMeta">${statusHtml(state)}${record.owner ? window.PortalI18n.html`<span>Owner · ${escapeHtml(record.owner)}</span>` : ""}</div>
        <p>${escapeHtml(text(record.statement || record.title, window.PortalI18n.t("未命名事项")))}</p>
        ${evidenceHtml(recordEvidenceRefs(record))}
      </article>`;
    }).join("")}</div>`;
  }

  function milestoneHtml(item, kind) {
    const milestone = object(item);
    const title = text(milestone.title, milestone.statement || window.PortalI18n.t("未命名里程碑"));
    const status = text(milestone.status, kind === "candidate" ? "proposed" : "confirmed");
    return window.PortalI18n.html`<article class="projectMilestone" data-kind="${escapeHtml(kind)}">
      <div class="projectMilestoneMeta">${statusHtml(status)}${milestone.target ? window.PortalI18n.html`<time>${escapeHtml(milestone.target)}</time>` : ""}</div>
      <h4>${escapeHtml(title)}</h4>
      ${milestone.owner ? window.PortalI18n.html`<p><strong>Owner</strong> · ${escapeHtml(milestone.owner)}</p>` : ""}
      ${milestone.proposal_basis ? window.PortalI18n.html`<p>${escapeHtml(milestone.proposal_basis)}</p>` : ""}
      ${array(milestone.acceptance_criteria).length ? window.PortalI18n.html`<strong class="projectMiniLabel">验收条件</strong>${stringListHtml(milestone.acceptance_criteria)}` : ""}
      ${array(milestone.blockers).length ? window.PortalI18n.html`<strong class="projectMiniLabel">阻塞</strong>${stringListHtml(milestone.blockers)}` : ""}
      ${evidenceHtml(recordEvidenceRefs(milestone))}
    </article>`;
  }

  function renderPlan(contract) {
    const roadmap = object(contract.roadmap);
    const confirmed = array(roadmap.confirmed_milestones);
    const candidate = array(roadmap.candidate_milestones);
    const humanReview = object(contract.human_review);
    const review = array(humanReview.highest_priority);
    return window.PortalI18n.html`<div class="projectPlanLead">
      <section class="projectPlanPanel is-review">
        <div class="projectSubheading"><div><span>Human review</span><h4>需要人来决定</h4></div><small>${humanReview.count ?? review.length} 项</small></div>
        ${issueListHtml(review, "human_review")}
      </section>
      <section class="projectPlanPanel">
        <div class="projectSubheading"><div><span>Committed</span><h4>已确认的下一步</h4></div><small>${confirmed.length} 项</small></div>
        ${confirmed.length
          ? window.PortalI18n.html`<div class="projectMilestoneList">${confirmed.map(item => milestoneHtml(item, "confirmed")).join("")}</div>`
          : window.PortalI18n.html`<div class="projectPlanEmpty"><strong>还没有已确认里程碑</strong><span>只有同时具备 owner、交付物与验收条件的计划，才应被视为项目承诺。</span></div>`}
      </section>
      <section class="projectPlanPanel">
        <div class="projectSubheading"><div><span>Proposed</span><h4>候选行动</h4></div><small>${candidate.length} 项</small></div>
        ${candidate.length
          ? window.PortalI18n.html`<div class="projectMilestoneList">${candidate.map(item => milestoneHtml(item, "candidate")).join("")}</div>`
          : window.PortalI18n.html`<div class="projectPlanEmpty"><strong>没有候选行动</strong><span>Project State 尚未形成下一步建议。</span></div>`}
      </section>
    </div>
    <details class="projectPageDisclosure">
      <summary>展开风险与开放问题 · ${array(contract.risks).length + array(contract.open_questions).length} 项</summary>
      <div class="projectPageDisclosureBody projectRiskGrid">
        <section><h4>风险</h4>${issueListHtml(contract.risks, "risk")}</section>
        <section><h4>开放问题</h4>${issueListHtml(contract.open_questions, "open_question")}</section>
      </div>
    </details>`;
  }

  function renderPeople(contract) {
    const lineage = object(contract.lineage);
    const residents = array(lineage.residents);
    const gaps = array(contract.architecture?.ownership_gaps);
    const residentsHtml = residents.length
      ? window.PortalI18n.html`<div class="projectResidentList">${residents.map(item => {
        const resident = object(item);
        return window.PortalI18n.html`<article class="projectResidentRow">
          <div class="projectResidentIdentity">
            <span class="projectResidentAvatar" aria-hidden="true">${escapeHtml(text(resident.name, "R").slice(0, 1).toUpperCase())}</span>
            <div><strong>${escapeHtml(text(resident.name, window.PortalI18n.t("Unnamed Resident")))}</strong><code>${escapeHtml(text(resident.resident_id, window.PortalI18n.t("resident unknown")))}</code></div>
          </div>
          <div class="projectResidentRole"><span>${escapeHtml(text(resident.role, window.PortalI18n.t("Role unknown")))}</span><p>${escapeHtml(text(resident.current_project_scope, window.PortalI18n.t("Project scope unknown")))}</p></div>
          <div class="projectResidentAccountability"><span>Accountability</span><strong>${escapeHtml(text(resident.owner_or_accountability, "unknown"))}</strong></div>
          ${statusHtml(resident.status)}
        </article>`;
      }).join("")}</div>`
      : global.PortalUIPrimitives?.asyncStateHtml?.({
        state: "empty",
        title: window.PortalI18n.t("尚未登记 Project-level Resident"),
        message: window.PortalI18n.t("这里不会从私有 Session 或 Resident Memory 猜测团队。"),
        density: "compact",
      }) || "";
    return `${lineage.summary ? window.PortalI18n.html`<p class="projectSectionIntro">${escapeHtml(lineage.summary)}</p>` : ""}
      ${residentsHtml}
      ${gaps.length ? window.PortalI18n.html`<div class="projectOwnershipGaps"><strong>尚未归属的责任</strong>${stringListHtml(gaps)}</div>` : ""}`;
  }

  function attentionHtml(items) {
    const records = array(items);
    if (!records.length) return window.PortalI18n.html`<p class="projectPageMuted">没有当前关注项。</p>`;
    return window.PortalI18n.html`<div class="projectAttentionList">${records.map(item => {
      const record = object(item);
      return window.PortalI18n.html`<article>
        <div class="projectIssueMeta">${statusHtml(record.type)}${record.owner ? window.PortalI18n.html`<span>Owner · ${escapeHtml(record.owner)}</span>` : ""}</div>
        <h4>${escapeHtml(text(record.statement, window.PortalI18n.t("未命名关注项")))}</h4>
        ${record.why_now ? window.PortalI18n.html`<p>${escapeHtml(record.why_now)}</p>` : ""}
        ${evidenceHtml(recordEvidenceRefs(record))}
      </article>`;
    }).join("")}</div>`;
  }

  function decisionGroupHtml(title, items, kind) {
    const records = array(items);
    return window.PortalI18n.html`<section class="projectDecisionGroup" data-kind="${escapeHtml(kind)}">
      <h4>${escapeHtml(title)} <span>${records.length}</span></h4>
      ${records.length ? window.PortalI18n.html`<div>${records.map(item => {
        const decision = object(item);
        return window.PortalI18n.html`<article>
          <div>${statusHtml(kind)}</div>
          <strong>${escapeHtml(text(decision.title || decision.statement, window.PortalI18n.t("未命名决策")))}</strong>
          ${decision.superseded_by ? window.PortalI18n.html`<small>Superseded by ${escapeHtml(decision.superseded_by)}</small>` : ""}
          ${evidenceHtml(recordEvidenceRefs(decision))}
        </article>`;
      }).join("")}</div>` : window.PortalI18n.html`<p class="projectPageMuted">无</p>`}
    </section>`;
  }

  function renderDecisionsAndTimeline(contract) {
    const decisions = object(contract.decisions);
    const timeline = array(contract.material_timeline);
    return window.PortalI18n.html`<div class="projectDecisionGrid">
      ${decisionGroupHtml("Active", decisions.active, "active")}
      ${decisionGroupHtml("Provisional", decisions.provisional, "proposed")}
      ${decisionGroupHtml("Superseded", decisions.superseded, "superseded")}
      ${decisionGroupHtml("Conflicted", decisions.conflicted, "conflicted")}
    </div>
    <div class="projectTimeline">
      ${timeline.length ? timeline.map(item => {
        const event = object(item);
        return window.PortalI18n.html`<article>
          <time>${escapeHtml(text(event.occurred_at, window.PortalI18n.t("Unknown date")))}</time>
          <div>${statusHtml(event.type)}<p>${escapeHtml(text(event.statement, window.PortalI18n.t("未命名变更")))}</p>${evidenceHtml(recordEvidenceRefs(event))}</div>
        </article>`;
      }).join("") : window.PortalI18n.html`<p class="projectPageMuted">没有 material timeline。</p>`}
    </div>`;
  }

  function renderArchitecture(architecture) {
    const value = object(architecture);
    const group = (title, records, render) => window.PortalI18n.html`<section class="projectArchitectureGroup">
      <h4>${escapeHtml(title)} <span>${array(records).length}</span></h4>
      <div>${array(records).map(item => render(object(item))).join("") || window.PortalI18n.html`<p class="projectPageMuted">无</p>`}</div>
    </section>`;
    return window.PortalI18n.html`<div class="projectArchitectureGrid">
      ${group("Workstreams", value.workstreams, item => window.PortalI18n.html`<article><strong>${escapeHtml(text(item.name, item.id))}</strong>${statusHtml(item.state)}</article>`)}
      ${group("Components", value.components, item => window.PortalI18n.html`<article><strong>${escapeHtml(text(item.name, item.id))}</strong><p>${escapeHtml(text(item.responsibility, window.PortalI18n.t("Responsibility unknown")))}</p></article>`)}
      ${group("Interfaces", value.interfaces, item => window.PortalI18n.html`<article><strong>${escapeHtml(text(item.name, item.id))}</strong><p>Owner · ${escapeHtml(text(item.contract_owner, "unknown"))}</p>${evidenceHtml(recordEvidenceRefs(item))}</article>`)}
      ${group("Dependencies", value.dependencies, item => window.PortalI18n.html`<article><strong>${escapeHtml(text(item.from, "Unknown"))} → ${escapeHtml(text(item.to, "Unknown"))}</strong><p>${escapeHtml(text(item.relation, window.PortalI18n.t("Relation unknown")))}</p></article>`)}
    </div>`;
  }

  function renderKnowledgeHealth(contract) {
    const health = object(contract.knowledge_health);
    const snapshot = object(contract.snapshot);
    const coverage = object(snapshot.source_coverage);
    const metrics = [
      ["未解决冲突", health.unresolved_conflicts],
      ["过期条目", health.stale_items],
      ["无效文档", health.invalid_documents],
      ["高影响未验证", health.unverified_high_impact_claims],
      ["缺失 Owner", health.missing_owners],
      ["缺失验收", health.missing_acceptance_criteria],
      ["开放 TODO", health.total_open_todos],
      ["不可读来源", coverage.unreadable_sources],
    ];
    return window.PortalI18n.html`<div class="projectKnowledgeHealth">${metrics.map(([label, value]) => metricHtml(
      label,
      value,
      { tone: Number(value) > 0 && label !== "开放 TODO" ? "warning" : "neutral" },
    )).join("")}</div>
      ${array(snapshot.blind_spots).length ? window.PortalI18n.html`<div class="projectBlindSpots"><strong>证据盲区</strong>${stringListHtml(snapshot.blind_spots)}</div>` : ""}`;
  }

  function renderEvidence(contract) {
    return window.PortalI18n.html`<div class="projectEvidenceLead">
      <section class="projectEvidencePanel">
        <div class="projectSubheading"><div><span>Now</span><h4>当前认知焦点</h4></div><small>${array(contract.current_attention).length} 项</small></div>
        ${attentionHtml(contract.current_attention)}
      </section>
      <section class="projectEvidencePanel">
        <div class="projectSubheading"><div><span>Health</span><h4>知识与证据健康度</h4></div></div>
        ${renderKnowledgeHealth(contract)}
      </section>
    </div>
    <details class="projectPageDisclosure">
      <summary>决策与 Material Timeline</summary>
      <div class="projectPageDisclosureBody">${renderDecisionsAndTimeline(contract)}</div>
    </details>
    <details class="projectPageDisclosure">
      <summary>架构边界与依赖</summary>
      <div class="projectPageDisclosureBody">${renderArchitecture(contract.architecture)}</div>
    </details>`;
  }

  function renderPartialBanner(contract) {
    if (contract.status !== "partial") return "";
    const sections = array(contract.missing_sections);
    return window.PortalI18n.html`<div class="projectPageNotice" data-tone="warning" role="status">
      <strong>Project State 不完整</strong>
      <span>${escapeHtml(text(contract.source?.message, window.PortalI18n.text`缺失：${sections.join("、")}`))}</span>
    </div>`;
  }

  function renderUnavailable(root, contract, options) {
    const source = object(contract.source);
    const invalid = contract.status === "invalid";
    root.innerHTML = window.PortalI18n.html`<div class="projectPageStateOnly" data-project-source-state="${escapeHtml(contract.status)}">
      ${global.PortalUIPrimitives?.asyncStateHtml?.({
        state: invalid || contract.status === "error" ? "error" : "empty",
        title: invalid
          ? window.PortalI18n.t("Project State 无法读取")
          : contract.status === "error"
            ? window.PortalI18n.t("Project State 加载失败")
            : window.PortalI18n.t("Project State 尚未发布"),
        message: text(source.message, window.PortalI18n.t("No Project State is available.")),
        density: "panel",
      }) || ""}
      <div class="projectPageStateActions">
        <button class="portalButton" type="button" data-project-request-view="summary">查看 Summary</button>
        <button class="portalButton" type="button" data-project-request-view="chat">打开 Project Chat</button>
      </div>
      <dl class="projectPageSourceDetails">
        <div><dt>Source</dt><dd>${escapeHtml(text(source.relative_path, "project_state/PROJECT_STATE.yaml"))}</dd></div>
        <div><dt>Code</dt><dd>${escapeHtml(text(source.code, contract.status))}</dd></div>
        <div><dt>Schema</dt><dd>${escapeHtml(text(source.schema_version, "Unknown"))}</dd></div>
      </dl>
    </div>`;
    root.querySelectorAll("[data-project-request-view]").forEach(button => {
      button.addEventListener("click", () => options.onRequestView?.(button.dataset.projectRequestView));
    });
  }

  function bindRendered(root, options) {
    global.PortalResourceChips?.bind?.(root);
    global.PortalFileLinks?.bind?.(root, { source: "project-state-evidence" });
    root.querySelectorAll("[data-project-anchor]").forEach(anchor => {
      anchor.addEventListener("click", event => {
        const sectionId = text(anchor.dataset.projectAnchor);
        const target = root.querySelector(`#project-section-${CSS.escape(sectionId)}`);
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        options.onAnchor?.(sectionId);
      });
    });
  }

  function renderContract(root, contract, options = {}) {
    const value = object(contract);
    if (!root) return;
    if (["missing", "invalid", "error"].includes(value.status)) {
      renderUnavailable(root, value, options);
      return;
    }

    root.innerHTML = window.PortalI18n.html`<article class="projectPage" data-project-source-state="${escapeHtml(value.status)}">
      ${renderPartialBanner(value)}
      ${renderHero(value)}
      ${renderAnchorNav()}
      <div class="projectPageSections">
        ${sectionHtml("overview", window.PortalI18n.t("一眼看懂这个项目"), renderOverview(value), { eyebrow: window.PortalI18n.t("At a glance") })}
        ${sectionHtml("delivery", window.PortalI18n.t("交付走到哪一步"), renderDelivery(value), { eyebrow: "Delivery", meta: window.PortalI18n.text`${array(value.capabilities).length} 项能力` })}
        ${sectionHtml("plan", window.PortalI18n.t("下一步与需要的决定"), renderPlan(value), { eyebrow: "Direction" })}
        ${sectionHtml("people", window.PortalI18n.t("谁在负责什么"), renderPeople(value), { eyebrow: "People", meta: `${array(value.lineage?.residents).length} Residents` })}
        ${sectionHtml("evidence", window.PortalI18n.t("证据、决策与认知健康"), renderEvidence(value), { eyebrow: window.PortalI18n.t("Deep evidence") })}
      </div>
    </article>`;
    bindRendered(root, options);
    options.onRendered?.(value);
  }

  function renderLoading(root) {
    if (!root) return;
    global.PortalUIPrimitives?.renderAsyncState?.(root, {
      state: "loading",
      title: window.PortalI18n.t("正在整理项目视图"),
      message: window.PortalI18n.t("正在读取项目方向、交付证据和需要人介入的事项。"),
      density: "panel",
    });
  }

  function renderError(root, error) {
    if (!root) return;
    global.PortalUIPrimitives?.renderAsyncState?.(root, {
      state: "error",
      title: text(error?.title, window.PortalI18n.t("Project Page 加载失败")),
      message: text(error?.message, window.PortalI18n.t("无法读取 Project State。")),
      density: "panel",
    });
  }

  function create(options = {}) {
    const root = typeof options.root === "string"
      ? document.querySelector(options.root)
      : options.root;
    if (!root) throw new Error("PortalProjectPage requires a root element.");
    let contract = null;
    return Object.freeze({
      root,
      render(value) {
        contract = object(value);
        renderContract(root, contract, options);
      },
      renderLoading() {
        contract = null;
        renderLoading(root);
      },
      renderError(error) {
        contract = null;
        renderError(root, error);
      },
      scrollTo(sectionId) {
        const clean = text(sectionId).replace(/^project-section-/, "");
        const target = root.querySelector(`#project-section-${CSS.escape(clean)}`);
        if (!target) return false;
        target.scrollIntoView({ block: "start" });
        return true;
      },
      snapshot() {
        return contract;
      },
    });
  }

  global.PortalProjectPage = Object.freeze({
    create,
  });
})(window);
