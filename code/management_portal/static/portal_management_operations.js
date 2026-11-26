import {startOperation, waitForOperation} from "./portal_operations.js";

export function mountOperations(container, signal) {
  const t = value => window.PortalI18n.t(value);
  const execute = (name, args) => window.PortalInstructionClient.execute(name, args, {signal});
  const root = document.createElement("div");
  root.innerHTML = window.PortalI18n.html`
    <details class="settingsSection">
      <summary>发布 Portal</summary>
      <form class="backendForm" data-operation="publish_release">
        <label class="portalField"><span>提交说明</span><input name="message" required></label>
        <label class="portalField"><span>发布路径（相对 code/，每行一条）</span><textarea name="paths" rows="4" required></textarea></label>
        <label><input type="checkbox" name="all"> 发布全部改动</label>
        <div><button class="portalButton is-primary" type="submit">发布</button></div>
        <p data-run-status role="status"></p><pre data-run-output hidden></pre>
      </form>
    </details>
    <details class="settingsSection">
      <summary>恢复 Article</summary>
      <form class="backendForm" data-operation="article_restore">
        <label class="portalField"><span>步骤</span><select name="step"><option value="check">预检证据</option><option value="apply">执行恢复</option></select></label>
        <label class="portalField"><span>证据参数（JSON 对象）</span><textarea name="evidence" rows="8" required spellcheck="false"></textarea></label>
        <a href="/r/plan_c9a0d073f14c">查看恢复合同</a>
        <div><button class="portalButton is-primary" type="submit">运行</button></div>
        <p data-run-status role="status"></p><pre data-run-output hidden></pre>
      </form>
    </details>`;
  container.append(root);
  const publish = root.querySelector('[data-operation="publish_release"]');
  publish.elements.all.addEventListener("change", () => {
    const paths = publish.elements.paths;
    paths.disabled = publish.elements.all.checked;
    paths.required = !paths.disabled;
  });
  function parameters(form) {
    if (form === publish) {
      const message = form.elements.message.value.trim();
      const paths = form.elements.paths.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      if (!message || (!paths.length && !form.elements.all.checked)) throw new Error(t("请填写提交说明及发布范围。"));
      return {message, ...(form.elements.all.checked ? {all: true} : {paths})};
    }
    let evidence;
    try { evidence = JSON.parse(form.elements.evidence.value); } catch { /* invalid input */ }
    if (!evidence || Array.isArray(evidence) || typeof evidence !== "object" || "step" in evidence) {
      throw new Error(t("请填写有效的证据参数对象，并在上方选择步骤。"));
    }
    return {...evidence, step: form.elements.step.value};
  }
  for (const form of root.querySelectorAll("form")) {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (signal.aborted || form.dataset.busy) return;
      const status = form.querySelector("[data-run-status]");
      const output = form.querySelector("[data-run-output]");
      const controls = [...form.elements].filter(control => !control.disabled);
      let run;
      form.dataset.busy = "true";
      controls.forEach(control => { control.disabled = true; });
      status.textContent = ""; output.hidden = true;
      try {
        const args = parameters(form);
        if ((form === publish || args.step === "apply") && !await window.PortalUI.confirm({
          title: t(form === publish ? "确认发布 Portal？" : "确认执行 Article 恢复？"),
          message: form === publish ? (args.all ? t("发布全部改动") : args.paths.join("\n")) : String(args.article_id || ""),
          confirmLabel: t(form === publish ? "发布" : "执行恢复"),
        })) return;
        if (signal.aborted) return;
        run = await startOperation(form.dataset.operation, args, execute, signal);
        status.textContent = window.PortalI18n.text`正在处理… ${run.script_run_id}`;
        const receipt = await waitForOperation(run, execute, () => !signal.aborted);
        if (!receipt) return;
        output.textContent = JSON.stringify(receipt.outputs || {}, null, 2);
        output.hidden = false;
        const message = receipt.outputs?.health?.status === "pending" ? "发布已提交，等待健康检查。" : "脚本已完成。";
        status.textContent = t(message) + " · " + run.script_run_id;
      } catch (error) {
        if (signal.aborted) return;
        if (error.receipt?.outputs) {
          output.textContent = JSON.stringify(error.receipt.outputs, null, 2);
          output.hidden = false;
        }
        status.textContent = [error.message || String(error), run?.script_run_id].filter(Boolean).join(" · ");
      } finally {
        delete form.dataset.busy;
        controls.forEach(control => { control.disabled = false; });
      }
    }, {signal});
  }
  return {isBusy: () => Boolean(root.querySelector("[data-busy]"))};
}
