import {modelKey, parseReport, summary, relevantMethod, parseMethodMeasurement} from '../reports.js';
export function createCatalog({snapshot, snapshotEvidence}) {
  const hardware = ["A100", "Thor", "RTX4090"];
  const catalog = {

    "smolvla": {name:"SmolVLA",sources:[null,"experiment_688155bc4428","experiment_de3246bf8078"]},
    "openvla": {name:"OpenVLA",sources:[null,"experiment_8990ad9456e8","experiment_fd113d1922d2"]},
    "rdt": {name:"RDT",sources:[null,"experiment_2fe131102e7c","experiment_48531703fa07"]},
    "act": {name:"ACT",sources:[]},
    "octo": {name:"Octo",sources:[]},
    "cosmos": {name:"Cosmos",sources:[]},
    "diffusionpolicy": {name:"Diffusion Policy",sources:[]},
    "dreamer": {name:"Dreamer",sources:[]},
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
    const owners = new Map();
    for (const item of graph.nodes || graph.members || []) {
      const match = String(item.title || "").match(/^模型\s*Steward\s*[｜|]\s*(.+)$/i);
      if(match) owners.set(modelKey(match[1]), item.resident_id);
    }
    return [...new Set(snapshot.map(row=>row[1]))].map(name=>{
      const key=modelKey(name);
      return {key,name,residentId:owners.get(key)||null,sources:catalog[key]?.sources||[]};
    });
  }

return { snapshot, snapshotEvidence, hardware, catalog, methods, modelKey, modelsFromGraph, parseReport, summary, relevantMethod, parseMethodMeasurement };
}
