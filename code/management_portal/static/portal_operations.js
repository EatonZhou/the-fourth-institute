export async function startOperation(operation, args, execute, signal) {
  const query = new URLSearchParams({operation});
  const binding = await window.PortalAPI.get("/api/portal-operations?" + query, {signal, cache: "no-store"});
  if (signal?.aborted) throw signal.reason;
  const run = await execute("RunScript", {
    lineage_id: binding.lineage_id,
    path: binding.path,
    runtime: binding.runtime,
    arguments: args,
  });
  if (!run.script_run_id) throw new Error(window.PortalI18n.t("缺少脚本运行回执。"));
  return {lineage_id: binding.lineage_id, script_run_id: run.script_run_id};
}

export async function readOperation(run, execute) {
  const receipt = await execute("GetScriptRun", run);
  if (receipt.status === "running" || receipt.status === "succeeded") return receipt;
  const detail = receipt.outputs?.error || receipt.error_code || receipt.status;
  const error = new Error(`${typeof detail === "string" ? detail : JSON.stringify(detail)} (${run.script_run_id})`);
  error.receipt = receipt;
  throw error;
}

export async function waitForOperation(run, execute, isCurrent) {
  while (isCurrent()) {
    const receipt = await readOperation(run, execute);
    if (!isCurrent()) return null;
    if (receipt.status === "succeeded") return receipt;
    await new Promise(resolve => window.setTimeout(resolve, 1500));
  }
  return null;
}
