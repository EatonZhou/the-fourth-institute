(() => {
  "use strict";
  const data = window.SemanticMatrixData;
  const sdk = window.Projection;
  const byId = id => document.getElementById(id);
  const state = { models: [], reports: new Map(), failures: new Map(), members: new Map(), pending: new Map(), selected: null, preferences: {} };
  const labels = { baseline: "PyTorch 基线", native: "FlashRT 原生", optimized: "进一步优化", method: "方法测量" };
  function node(tag, className, text) { const result = document.createElement(tag); if (className) result.className = className; if (text !== undefined) result.textContent = String(text); return result; }
  const latency = value => value == null ? "未登记" : `${value.toFixed(3)} ms`;
  const sourceFor = (model, hardware) => model.sources[data.hardware.indexOf(hardware)];
  const statusLabel = report => ({ success: "报告：success", running: "报告：running", failed: "报告：failed", blocked: "报告：blocked", unknown: "状态未登记" }[report?.status] || `报告：${report?.status || "未读取"}`);
  async function readReport(id) {
    if (state.reports.has(id)) return state.reports.get(id);
    if (state.pending.has(id)) return state.pending.get(id);
    const promise = sdk.resource("read", { address: id }).then(payload => {
      const report = data.parseReport(payload, id); state.reports.set(id, report); state.failures.delete(id); return report;
    }).catch(error => { state.failures.set(id, error.message); throw error; }).finally(() => state.pending.delete(id));
    state.pending.set(id, promise); return promise;
  }
  function metric(label, value) { const result = node("span", "cellMetric"); result.append(node("small", "", label), node("span", "", latency(value))); return result; }
  function cell(model, hardware) {
    const source = sourceFor(model, hardware);
    const report = state.reports.get(source);
    const facts = data.summary(report);
    const button = node("button", "portalButton matrixCell");
    button.type = "button"; button.dataset.model = model.key; button.dataset.hardware = hardware; button.setAttribute("aria-label", `${model.name} × ${hardware}，查看性能与优化方式`);
    const top = node("span", "cellTop");
    const ratio = facts.current?.speedup;
    top.append(node("strong", "cellSpeedup", ratio ? `${ratio.toFixed(2)}×` : "—"), node("span", "cellBadge", state.failures.has(source) ? "证据不可读" : !source ? "尚无关联实验" : !report ? "正在读取" : facts.current ? "已有测量记录" : facts.baseline?.p50 ? "仅有基线" : "待测量"));
    const values = node("span", "cellMetrics"); values.append(metric("PyTorch 基线 p50", facts.baseline?.p50), metric(facts.current ? `${labels[facts.current.kind]} p50` : "优化 p50", facts.current?.p50));
    const stages = node("span", "cellStages");
    for (const kind of ["baseline", "native", "optimized"]) stages.append(node("span", `cellStage${report?.rows.some(row => row.kind === kind && row.p50 > 0) ? " recorded" : ""}`, labels[kind]));
    button.append(top, values, stages, node("small", "matrixMuted", `${statusLabel(report)} · ${ratio ? "同口径比较，查看证据 →" : hardware === "A100" ? "组合待验证，查看单项方法 →" : "无配对基线，查看进展 →"}`));
    button.onclick = () => openDetail(model, hardware);
    return button;
  }
  function render() {
    const query = byId("matrixSearch").value.trim().toLowerCase();
    const selectedHardware = byId("matrixHardware").value;
    const platforms = selectedHardware === "all" ? data.hardware : [selectedHardware];
    const models = state.models.filter(model => model.name.toLowerCase().includes(query));
    const table = byId("performanceMatrix");
    const header = node("tr"); header.append(node("th", "", "模型 / 平台"));
    for (const hardware of platforms) { const heading = node("th", "", hardware); heading.scope = "col"; header.append(heading); }
    table.tHead.replaceChildren(header);
    const rows = models.map(model => {
      const row = node("tr"); const heading = node("th", "", model.name); heading.scope = "row";
      heading.append(node("span", "modelOwner", `模型负责人：${state.members.get(model.residentId) || "未知"}`)); row.append(heading);
      for (const hardware of platforms) { const column = node("td"); column.append(cell(model, hardware)); row.append(column); }
      return row;
    });
    if (!rows.length) { const row = node("tr"); const column = node("td", "", state.models.length ? "没有匹配的模型" : "该团队未登记模型 Steward，未创建虚构模型行"); column.colSpan = platforms.length + 1; row.append(column); rows.push(row); }
    table.tBodies[0].replaceChildren(...rows);
    byId("matrixDimensions").textContent = `${state.models.length} 个真实模型 × ${data.hardware.length} 类硬件 · ${state.models.length * data.hardware.length} 个组合`;
    const sources = state.models.flatMap(model => model.sources);
    byId("matrixCoverage").textContent = `已读取 ${sources.filter(id => state.reports.has(id)).length}/${sources.length} 份平台实验报告`;
  }
  function evidence(container, report) {
    container.replaceChildren();
    if (!report) { container.append(node("p", "matrixMuted", "暂无可读证据；请刷新重试。")); return; }
    container.append(node("p", "matrixMuted", `来源：${report.sourceId} · 更新：${report.updatedAt || "未登记"}`));
    container.append(node("pre", "sourceEvidence", report.text));
  }
  function results(container, report) {
    container.replaceChildren(); container.classList.add("resultList");
    if (!report?.rows.length) container.append(node("p", "matrixNotice", "当前报告未登记可用于比较的完整模型性能。尚未测量、准入阻塞或失败不会显示为 0 ms。"));
    for (const row of report?.rows || []) {
      const card = node("article", "resultRow"); card.append(node("h3", "", labels[row.kind] || row.name));
      const values = node("div", "resultNumbers"); values.append(node("span", "", `p50：${latency(row.p50)}`), node("span", "", `p95：${latency(row.p95)}`), node("strong", "", row.speedup ? `${row.speedup.toFixed(3)}× PyTorch` : "加速比：无同口径配对"));
      card.append(values, node("p", "matrixMuted", row.contract || "契约未登记"));
      const details = node("details"); details.append(node("summary", "", "查看该项测量的原文"), node("pre", "sourceEvidence", row.evidence)); card.append(details); container.append(card);
    }
    if (report?.progress) container.append(node("p", "matrixNotice", report.progress));
    container.append(node("p", "matrixMuted", "已登记数值与独立验收状态分开展示；不会把不同模型、平台或实验窗口拼成加速比。"));
  }
  async function showMethods(model, hardware, selection) {
    const container = byId("matrixMethods"); container.replaceChildren(node("p", "matrixMuted", "正在读取该模型的真实方法研究记录…"));
    const source = sourceFor(model, hardware);
    const report = state.reports.get(source);
    const cards = [];
    const optimized = report?.rows.filter(row => row.kind === "optimized") || [];
    for (const row of optimized) cards.push({ name: "整体优化结果（具体语义方法未登记）", report, outcome: `${labels[row.kind]} p50 ${latency(row.p50)}${row.speedup ? ` · ${row.speedup.toFixed(3)}× PyTorch` : ""}。输入口径：${row.contract || "未登记"}；输入口径不是语义优化方法名称。` });
    if (hardware === "A100") {
      for (const method of data.methods) {
        if (state.selected !== selection) return;
        try {
          const current = await readReport(method.id);
          if (data.relevantMethod(current, model)) {
            const item = { name: method.name, report: current, outcome: "方法研究报告；没有同口径配对时不计算语义加速比。" };
            const measurementSource = method.measurements?.[model.key];
            if (measurementSource) {
              try { const payload = await sdk.resource("read", { address: measurementSource }); item.measurements = data.parseMethodMeasurement(payload, method.format); item.measurementSource = measurementSource; item.measurementText = payload.content; }
              catch (error) { item.outcome = `方法报告可读，测量记录暂不可读：${error.message}`; }
            }
            cards.push(item);
          }
        } catch (error) { cards.push({ name: method.name, error: error.message }); }
      }
    }
    if (state.selected !== selection) return;
    container.replaceChildren(); container.classList.add("methodList");
    if (!cards.length) container.append(node("p", "matrixNotice", "该平台尚未关联逐方法性能记录。不将 A100 方法结果移植为此硬件的测量结果。"));
    for (const item of cards) {
      const card = node("article", "methodCard"); card.append(node("h3", "", item.name), node("p", "matrixMuted", item.error || item.outcome));
      for (const measurement of item.measurements || []) {
        card.append(node("strong", "resultNumbers", `${measurement.label} · p50 ${latency(measurement.p50)}${measurement.ratio ? ` · ${measurement.ratio.toFixed(3)}×` : ""}`), node("p", "matrixMuted", measurement.note));
      }
      if (item.measurementText) { const detail = node("details"); detail.append(node("summary", "", `机器测量证据 · ${item.measurementSource}`), node("pre", "sourceEvidence", item.measurementText)); card.append(detail); }
      if (item.report) { card.append(node("p", "matrixMuted", `${statusLabel(item.report)} · ${item.report.sourceId}`)); const detail = node("details"); detail.append(node("summary", "", "展开方法效果、进展与原始证据"), node("pre", "sourceEvidence", item.report.text)); card.append(detail); }
      container.append(card);
    }
  }
  function selectTab(name) {
    document.querySelectorAll(".semanticMatrix [data-tab]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.tab === name)));
    for (const tab of ["results", "methods", "evidence"]) byId(`matrix${tab[0].toUpperCase()}${tab.slice(1)}`).hidden = tab !== name;
    if (name === "methods" && state.selected) showMethods(state.selected.model, state.selected.hardware, state.selected);
  }
  function openDetail(model, hardware) {
    const source = sourceFor(model, hardware); const report = state.reports.get(source);
    state.selected = { model, hardware };
    byId("matrixDetailTitle").textContent = `${model.name} × ${hardware}`;
    byId("matrixDetailSubtitle").textContent = source ? `${statusLabel(report)} · ${source}` : "暂无关联实验";
    byId("matrixDetailSummary").textContent = state.failures.get(source) || "性能和方法只属于当前模型与硬件；源报告的候选、阻塞和验收信息保留在详情中。";
    results(byId("matrixResults"), report); evidence(byId("matrixEvidence"), report); selectTab("results");
    byId("matrixDetail").showModal();
  }
  async function refresh() {
    byId("matrixRefresh").disabled = true; byId("matrixError").hidden = true;
    state.reports.clear(); state.failures.clear();
    try {
      const graph = (await sdk.hatg("GetLineage"))?.lineage || {}; state.models = data.modelsFromGraph(graph); render();
      const residents = state.models.map(model => model.residentId);
      if (residents.length) {
        try { const snapshot = await sdk.hatg("ListResidentStates", { resident_ids: residents }); state.members = new Map((snapshot.states || []).map(item => [item.resident_id, item.state?.work_state || "未知"])); }
        catch { state.members.clear(); }
      }
      const sources = [...new Set(state.models.flatMap(model => model.sources))];
      for (let index = 0; index < sources.length; index += 3) { await Promise.allSettled(sources.slice(index, index + 3).map(readReport)); render(); }
      byId("matrixUpdated").textContent = `查询时间 ${new Date().toLocaleString()}`;
    } catch (error) { byId("matrixError").textContent = error.message; byId("matrixError").hidden = false; }
    finally { byId("matrixRefresh").disabled = false; }
  }
  async function saveFilters() {
    render();
    try { state.preferences = { ...state.preferences, matrix: { search: byId("matrixSearch").value, hardware: byId("matrixHardware").value } }; await sdk.state(state.preferences); }
    catch (error) { byId("matrixError").textContent = `筛选未保存：${error.message}`; byId("matrixError").hidden = false; }
  }
  for (const name of data.hardware) { const option = node("option", "", name); option.value = name; byId("matrixHardware").append(option); }
  byId("matrixRefresh").onclick = refresh;
  byId("matrixLineage").onclick = () => sdk.openLineage("main").catch(error => { byId("matrixError").textContent = error.message; byId("matrixError").hidden = false; });
  byId("matrixSearch").oninput = render; byId("matrixSearch").onchange = saveFilters; byId("matrixHardware").onchange = saveFilters;
  byId("matrixClose").onclick = () => byId("matrixDetail").close();
  byId("matrixDetail").onclose = () => { state.selected = null; };
  document.querySelectorAll(".semanticMatrix [data-tab]").forEach(button => button.onclick = () => selectTab(button.dataset.tab));
  (async () => {
    try { state.preferences = await sdk.state(); const saved = state.preferences.matrix || {}; byId("matrixSearch").value = saved.search || ""; if (data.hardware.includes(saved.hardware)) byId("matrixHardware").value = saved.hardware; }
    catch { state.preferences = {}; }
    await refresh();
  })();
})();
