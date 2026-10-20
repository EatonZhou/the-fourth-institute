(function (root) {
  "use strict";
  const hardware = ["A100", "Thor", "RTX4090"];
  const catalog = {
    "pi05": { name: "PI 0.5", sources: ["experiment_7ee25cd33ddc", "experiment_3917f71f0efc", "experiment_e1573e1238bb"] },
    "motus": { name: "Motus", sources: ["experiment_35a102bcd81c", "experiment_74abf6da58fa", "experiment_22cb06269c11"] },
    "efficientwam": { name: "Efficient-WAM", sources: ["experiment_c210df809c7e", "experiment_d26453514640", "experiment_ee93c8d54b6a"] },
    "gr00tn17": { name: "GR00T N1.7", sources: ["experiment_0f8255f9b610", "experiment_a80dd93e802e", "experiment_acc1b65f165b"] },
  };
  const methods = [
    { name: "VLA Pruning", id: "experiment_79d21f79dd64", measurements: { pi05: "experiment_79d21f79dd64/file/results/pi05/machine_summary.json" }, format: "pruning" },
    { name: "多层次动态稀疏", id: "experiment_539123e3b613" },
    { name: "VLA-Cache", id: "experiment_75a7543a957e", measurements: { pi05: "experiment_75a7543a957e/file/results/pi05_motus/machine_results/pi05_pytorch_e3.json" }, format: "cache" },
    { name: "DiT Cache", id: "experiment_14525fc42af0" },
    { name: "Adaptive Timestep", id: "experiment_81853d11c0c6", measurements: { pi05: "experiment_81853d11c0c6/file/results/pi05/flashrt_adaptive_eta05.json" }, format: "adaptive" },
    { name: "ActionCache", id: "experiment_cde5b0cf2e04" },
    { name: "SVD-LLM / Low Rank", id: "experiment_4dcf2ce461a7" },
    { name: "STAR 条件层路由", id: "experiment_b23b42232b6b" },
  ];
  function modelKey(name) { return String(name).toLowerCase().replace(/[^a-z0-9]/g, ""); }
  function modelsFromGraph(graph) {
    const models = new Map();
    for (const node of graph.nodes || []) {
      const match = String(node.title || "").match(/^模型\s*Steward\s*[｜|]\s*(.+)$/i);
      if (!match) continue;
      const key = modelKey(match[1]);
      models.set(key, { key, name: match[1].trim(), residentId: node.resident_id, sources: catalog[key]?.sources || [] });
    }
    return [...models.values()];
  }
  function number(value) {
    const match = String(value || "").replace(/[`*,]/g, "").trim().match(/^(\d+(?:\.\d+)?)(?:\s*ms)?$/i);
    return match ? Number(match[1]) : null;
  }
  function parseReport(payload, sourceId) {
    const text = String(payload.content || "");
    const lines = text.split(/\r?\n/);
    const header = lines.findIndex(line => /^\|/.test(line) && /Implementation/i.test(line) && /p50/i.test(line));
    const rows = [];
    if (header >= 0) {
      const headings = lines[header].split("|").slice(1, -1).map(value => value.trim().toLowerCase());
      const p50Index = headings.findIndex(value => value.includes("p50"));
      const p95Index = headings.findIndex(value => value.includes("p95"));
      for (const line of lines.slice(header + 2)) {
        if (!line.startsWith("|")) break;
        const fields = line.split("|").slice(1, -1).map(value => value.trim());
        const kind = /pytorch/i.test(fields[0]) ? "baseline" : /optimized/i.test(fields[0]) ? "optimized" : /flashrt/i.test(fields[0]) ? "native" : "method";
        rows.push({ name: fields[0], kind, precision: fields[1], contract: fields[2], p50: number(fields[p50Index]), p95: number(fields[p95Index]), evidence: line, sourceId });
      }
    }
    if (!rows.length && ["experiment_e1573e1238bb", "experiment_ee93c8d54b6a"].includes(sourceId)) {
      const metricLine = lines.find(line => /p50\s*[=:：` ]*\d[\d.]*\s*ms/i.test(line));
      const latency = metricLine?.match(/p50\s*[=:：` ]*(\d+(?:\.\d+)?)\s*ms/i);
      const percentile = metricLine?.match(/p95\s*[=:：` ]*(\d+(?:\.\d+)?)\s*ms/i);
      if (latency) rows.push({ name: "FlashRT native", kind: "native", p50: Number(latency[1]), p95: percentile ? Number(percentile[1]) : null, contract: "实验报告的完整模型正式测量；未导入跨文档基线", sourceId, evidence: metricLine });
    }
    const baseline = rows.find(row => row.kind === "baseline" && row.p50 > 0);
    for (const row of rows) {
      row.speedup = baseline && row.p50 > 0 && row.kind !== "baseline" && /same as baseline/i.test(row.contract || "") ? baseline.p50 / row.p50 : null;
    }
    const frontmatter = text.match(/^---\s*\n([\s\S]*?)\n---/);
    const status = frontmatter?.[1].match(/^status:\s*['"]?([^'"\r\n]+)/m)?.[1]?.trim() || "unknown";
    const progress = lines.filter(line => /^- (阶段|屏障状态)|^阶段[：:]|^当前状态[：:]/.test(line)).join("\n");
    const metadata = payload.metadata?.record || {};
    return { sourceId, rows, text, status, progress, updatedAt: metadata.version?.updated_at || metadata.updated_at || "", title: metadata.title || sourceId };
  }
  function summary(report) {
    const rows = report?.rows || [];
    const baseline = rows.find(row => row.kind === "baseline");
    const current = ["optimized", "native"].map(kind => rows.find(row => row.kind === kind && row.p50 > 0)).find(Boolean);
    return { baseline, current, recorded: rows.filter(row => row.p50 > 0).length, total: Math.max(3, rows.length) };
  }
  function relevantMethod(report, model) {
    const text = modelKey(report.text);
    return text.includes(model.key) || (model.key === "pi05" && /pi0?5|openpi/.test(text)) || (model.key === "gr00tn17" && /gr00t|groot/.test(text));
  }
  function parseMethodMeasurement(payload, format) {
    const result = JSON.parse(payload.content);
    const rows = [];
    if (format === "adaptive" && result.timed_region?.complete_model === true && /A100/.test(result.device?.name || "")) rows.push({ label: "FlashRT Adaptive 完整模型", p50: result.measurement?.p50_ms, note: "本方法测量；缺少同窗口未优化基线，不计算语义加速比。" });
    if (format === "cache" && result.model_key === "pi05" && result.validation?.full_model === true && /A100/.test(result.device?.gpu_name || "")) rows.push({ label: "PyTorch VLA-Cache 完整模型", p50: result.timing?.p50_ms, ratio: result.speedup?.comparable === true ? result.speedup.value : null, note: `仅相对该记录的 ${result.speedup?.baseline_result_id || "未登记基线"}；不与 FlashRT 方法混排。` });
    if (format === "pruning" && /PI 0.5/.test(result.checkpoint?.model || "") && result.device?.resource_id === "device_498b270d6b17") {
      rows.push({ label: "剪枝方法 · PyTorch", p50: result.pytorch?.p50_ms, note: result.pytorch?.raw_status || "状态未登记" });
      rows.push({ label: "剪枝方法 · FlashRT", p50: result.flashrt?.p50_ms, note: "这是同一剪枝方法的不同实现，不能当作相对未剪枝模型的语义增益。" });
    }
    return rows.filter(row => Number.isFinite(row.p50) && row.p50 > 0).map(row => ({ ...row, status: result.status || "测量记录；独立验收状态另见报告" }));
  }
  const api = Object.freeze({ hardware, catalog, methods, modelKey, modelsFromGraph, parseReport, summary, relevantMethod, parseMethodMeasurement });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SemanticMatrixData = api;
})(typeof window === "undefined" ? globalThis : window);
