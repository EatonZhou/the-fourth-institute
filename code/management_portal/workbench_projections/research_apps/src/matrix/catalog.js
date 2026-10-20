import {modelKey, parseReport, summary, relevantMethod, parseMethodMeasurement} from '../reports.js';
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
  function modelsFromGraph(graph) {
    const models = new Map();
    for (const node of graph.nodes || graph.members || []) {
      const match = String(node.title || "").match(/^模型\s*Steward\s*[｜|]\s*(.+)$/i);
      if (!match) continue;
      const key = modelKey(match[1]);
      models.set(key, { key, name: match[1].trim(), residentId: node.resident_id || node.id, sources: catalog[key]?.sources || [] });
    }
    return [...models.values()];
  }

export const data = {hardware, catalog, methods, modelKey, modelsFromGraph, parseReport, summary, relevantMethod, parseMethodMeasurement};

