// Knowledge board: the Knowledge overview (radar chart, signals and Projects).

// ── Knowledge overview ───────────────────────────────────────────────
function clampNumber(value, min = 0, max = 100) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, n));
}

function knowledgeRadarItems() {
  return knowledgeMapBaseItems();
}

function itemAgeDays(item) {
  const ts = Number(item?.mtime || 0);
  if (!Number.isFinite(ts) || ts <= 0) return Infinity;
  return Math.max(0, (Date.now() - ts * 1000) / 86400000);
}

function groupRadarProjects(items) {
  const map = new Map();
  for (const item of items) {
    const project = item.project || "unknown";
    if (!map.has(project)) map.set(project, []);
    map.get(project).push(item);
  }
  return [...map.entries()].map(([project, list]) => {
    const total = list.length;
    const recent = list.filter(item => itemAgeDays(item) <= 14).length;
    const stale = list.filter(item => Number(item.importance || 0) >= 4 && itemAgeDays(item) > 120).length;
    const thin = list.filter(item => String(item.preview || "").trim().length < 80).length;
    const decisions = list.filter(item => item.type === "decision").length;
    const ideas = list.filter(item => item.type === "idea" || item.type === "question").length;
    const insights = list.filter(item => item.type === "insight" || item.type === "concept").length;
    const activity = clampNumber((recent / Math.max(1, total)) * 100 + Math.min(22, total));
    const decisionCoverage = clampNumber((decisions / Math.max(1, ideas + decisions)) * 100);
    const insightDensity = clampNumber((insights / Math.max(1, total)) * 160);
    const staleRisk = clampNumber((stale / Math.max(1, total)) * 220);
    const thinRisk = clampNumber((thin / Math.max(1, total)) * 130);
    const score = clampNumber(activity * 0.35 + decisionCoverage * 0.25 + insightDensity * 0.2 + (100 - staleRisk) * 0.2);
    return { project, total, recent, stale, thin, decisions, ideas, insights, activity, decisionCoverage, insightDensity, staleRisk, thinRisk, score };
  }).sort((a, b) => b.score - a.score || b.total - a.total);
}

function buildKnowledgeRadarData() {
  const items = knowledgeRadarItems();
  const total = items.length;
  const projects = groupRadarProjects(items);
  const recent7 = items.filter(item => itemAgeDays(item) <= 7).length;
  const recent30 = items.filter(item => itemAgeDays(item) <= 30).length;
  const highImportance = items.filter(item => Number(item.importance || 0) >= 4).length;
  const staleHigh = items.filter(item => Number(item.importance || 0) >= 4 && itemAgeDays(item) > 120).length;
  const decisions = items.filter(item => item.type === "decision").length;
  const ideasQuestions = items.filter(item => item.type === "idea" || item.type === "question").length;
  const insightsConcepts = items.filter(item => item.type === "insight" || item.type === "concept").length;
  const thin = items.filter(item => String(item.preview || "").trim().length < 80).length;
  const projectCount = projects.length;
  const projectsWithDecision = projects.filter(project => project.decisions > 0).length;
  const freshness = clampNumber((recent30 / Math.max(1, total)) * 180);
  const decisionCoverage = clampNumber((projectsWithDecision / Math.max(1, projectCount)) * 100);
  const insightDensity = clampNumber((insightsConcepts / Math.max(1, total)) * 170);
  const maintenance = clampNumber(100 - (staleHigh / Math.max(1, highImportance || total)) * 130);
  const focus = clampNumber(100 - Math.abs(ideasQuestions - decisions) / Math.max(1, ideasQuestions + decisions) * 75);
  const axes = [
    { label: "Freshness", value: freshness },
    { label: "Decision", value: decisionCoverage },
    { label: "Insight", value: insightDensity },
    { label: "Focus", value: focus },
    { label: "Maintenance", value: maintenance },
  ];
  const signals = [
    {
      title: window.PortalI18n.t("Recent movement"),
      value: `${recent7} / 7d`,
      text: `${recent30} items changed in 30 days across ${projectCount} projects.`,
      level: freshness,
    },
    {
      title: window.PortalI18n.t("Decision coverage"),
      value: `${projectsWithDecision}/${projectCount}`,
      text: `${decisions} decision items currently anchor project memory.`,
      level: decisionCoverage,
    },
    {
      title: window.PortalI18n.t("High-importance stale"),
      value: String(staleHigh),
      text: `${staleHigh} high-importance items have not changed for more than 120 days.`,
      level: 100 - maintenance,
      risk: true,
    },
    {
      title: window.PortalI18n.t("Thin previews"),
      value: String(thin),
      text: `${thin} loaded items have short previews and may need clearer summaries.`,
      level: clampNumber((thin / Math.max(1, total)) * 100),
      risk: true,
    },
  ];
  return {
    items,
    total,
    projectCount,
    recent7,
    recent30,
    highImportance,
    staleHigh,
    decisions,
    insightsConcepts,
    thin,
    axes,
    projects,
    signals,
  };
}

function radarPolygonPoints(values, radius, cx, cy) {
  const count = values.length;
  return values.map((value, idx) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * idx) / count;
    const r = radius * clampNumber(value) / 100;
    return `${(cx + Math.cos(angle) * r).toFixed(1)},${(cy + Math.sin(angle) * r).toFixed(1)}`;
  }).join(" ");
}

function renderRadarChart(axes) {
  const cx = 180;
  const cy = 170;
  const radius = 108;
  const levels = [25, 50, 75, 100];
  const grid = levels.map(level => window.PortalI18n.html`<polygon class="radarGrid" points="${radarPolygonPoints(axes.map(() => level), radius, cx, cy)}" />`).join("");
  const axisLines = axes.map((axis, idx) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * idx) / axes.length;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius;
    const lx = cx + Math.cos(angle) * (radius + 34);
    const ly = cy + Math.sin(angle) * (radius + 28);
    return window.PortalI18n.html`
            <line class="radarAxis" x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" />
            <text class="radarLabel" x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle">${escapeHtml(axis.label)}</text>
          `;
  }).join("");
  const shape = radarPolygonPoints(axes.map(axis => axis.value), radius, cx, cy);
  const dots = axes.map((axis, idx) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * idx) / axes.length;
    const r = radius * clampNumber(axis.value) / 100;
    return window.PortalI18n.html`<circle class="radarDot" cx="${(cx + Math.cos(angle) * r).toFixed(1)}" cy="${(cy + Math.sin(angle) * r).toFixed(1)}" r="3.4" />`;
  }).join("");
  return window.PortalI18n.html`<svg data-portal-visualization="knowledge-radar" viewBox="0 0 360 340" role="img" aria-label="Knowledge overview">${grid}${axisLines}<polygon class="radarShape" points="${shape}" />${dots}</svg>`;
}

function radarMetricHtml(value, label) {
  return window.PortalI18n.html`
          <div class="radarMetric">
            <div class="radarMetricValue">${escapeHtml(value)}</div>
            <div class="radarMetricLabel">${escapeHtml(label)}</div>
          </div>
        `;
}

function radarBarHtml(label, value, kind = "") {
  const pct = clampNumber(value);
  return window.PortalI18n.html`
          <div class="radarBar">
            <span>${escapeHtml(label)}</span>
            <span class="radarBarTrack"><span class="radarBarFill ${escapeHtml(kind)}" style="width:${pct}%"></span></span>
            <span>${Math.round(pct)}</span>
          </div>
        `;
}

function renderKnowledgeRadar({ force = false } = {}) {
  const data = buildKnowledgeRadarData();
  const signature = JSON.stringify({
    ids: data.items.map(item => item.kb_id),
    axes: data.axes.map(axis => Math.round(axis.value)),
    projects: data.projects.map(project => `${project.project}:${project.total}:${Math.round(project.score)}`),
  });
  if (!force && signature === state.knowledgeRadar.signature) return;
  state.knowledgeRadar.signature = signature;
  el.knowledgeRadarSub.textContent = `${data.total} items · ${data.projectCount} projects`;
  el.knowledgeRadarChart.innerHTML = data.total ? renderRadarChart(data.axes)
    : knowledgeStateHtml("当前没有可评估的知识条目，暂不显示评分。", { density: "inline", icon: false });
  el.knowledgeRadarMetrics.innerHTML = [
    radarMetricHtml(data.total, "Loaded items"),
    radarMetricHtml(data.recent7, "Changed in 7d"),
    radarMetricHtml(data.decisions, "Decisions"),
    radarMetricHtml(data.insightsConcepts, "Insights/concepts"),
    radarMetricHtml(data.highImportance, "Importance >= 4"),
    radarMetricHtml(data.staleHigh, "Stale high-value"),
  ].join("");
  const signals = data.total ? data.signals : [];
  el.knowledgeRadarSignalCount.textContent = `${signals.length} signals`;
  el.knowledgeRadarSignals.innerHTML = signals.length ? signals.map(signal => window.PortalI18n.html`
          <div class="radarSignal">
            <div class="radarSignalTop">
              <div class="radarSignalTitle">${escapeHtml(signal.title)}</div>
              <div class="radarSignalValue">${escapeHtml(signal.value)}</div>
            </div>
            <div class="radarSignalText">${escapeHtml(signal.text)}</div>
            <div class="radarBars">${radarBarHtml(signal.risk ? "risk" : "signal", signal.level, signal.risk ? "risk" : "good")}</div>
          </div>
        `).join("") : knowledgeStateHtml("暂无数据，无法生成指标。", { density: "inline", icon: false });
  const topProjects = data.projects.slice(0, 12);
  el.knowledgeRadarProjectCount.textContent = `${data.projects.length} projects`;
  el.knowledgeRadarProjects.innerHTML = topProjects.length ? topProjects.map(project => window.PortalI18n.html`
          <div class="radarProject">
            <div class="radarProjectTop">
              <div class="radarProjectName">${escapeHtml(project.project)}</div>
              <div class="radarProjectScore">${Math.round(project.score)} score · ${project.total} items</div>
            </div>
            <div class="radarBars">
              ${radarBarHtml("activity", project.activity, "good")}
              ${radarBarHtml("decision", project.decisionCoverage, "")}
              ${radarBarHtml("insight", project.insightDensity, "")}
              ${radarBarHtml("stale", project.staleRisk, "risk")}
            </div>
          </div>
        `).join("") : knowledgeStateHtml("当前过滤条件下没有可展示的项目。", {
    density: "inline",
    icon: false,
  });
}
