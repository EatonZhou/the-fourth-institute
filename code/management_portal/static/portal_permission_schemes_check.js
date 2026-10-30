(() => {
  "use strict";

  if (window.PortalPermissionSchemesCheck) return;

  // The try-decide view: a call's principal, instruction and arguments,
  // and the decision on the active scheme's instance.

  const DEPENDENCIES = Object.freeze([
    "dirty", "elementsOf", "enabled", "esc", "instructionNames", "render", "run", "state", "t", "tx",
  ]);

  function createCheck(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesCheck is missing: ${missing.join(", ")}`);
    const {
      dirty, elementsOf, enabled, esc, instructionNames, render, run, state, t, tx,
    } = dependencies;

    // -- try-decide view
    function checkHtml() {
      const input = state.checkInput;
      const active = state.current?.active && !dirty();
      // The arguments a call is judged on are the instruction's declared argument slots.
      const slots = elementsOf(input.instruction).filter(slot => slot.argument);
      // A via argument takes record IDs: the server reads the record and judges the object it names.
      const records = state.vocabulary.records || [];
      const field = (name, label, placeholder, hint = "") => `<label class="portalField"><span>${esc(label)}</span>
        <input data-check-argument="${esc(name)}" data-focus="check-${esc(name)}" value="${esc(input.arguments[name] || "")}" placeholder="${esc(placeholder)}" />
        ${hint ? `<small class="permSchemeMuted" data-check-place>${esc(hint)}</small>` : ""}</label>`;
      const args = slots.map(slot => {
        // A place slot takes the new object's type name and the IDs of the objects it will belong to (dotted paths
        // name nested arguments, e.g. fields.project_id).
        if (slot.place) {
          const paths = Object.entries(slot.place).flatMap(([related, list]) => [].concat(list).map(path => [path, related]));
          return [field(slot.argument, tx`${slot.argument}（新资源的类型）`, t("类型名，例如 workspace"), t("按新资源将要放进 R 树时的父节点判定")),
            ...paths.map(([path, related]) => field(path, path, tx`${related} ID`))].join("");
        }
        const record = slot.via ? records.find(item => item.via === slot.via) : null;
        const hint = !slot.via ? "" : record ? tx`${t(record.record_title)} ID，按记录中的${t(record.title)}判定` : tx`记录 ID（${slot.via}）`;
        return `<label class="portalField"><span>${esc(slot.argument)}${slot.types?.length ? ` (${esc(slot.types.join(" / "))})` : ""}</span>
        <input data-check-argument="${esc(slot.argument)}" data-focus="check-${esc(slot.argument)}" value="${esc(input.arguments[slot.argument] || "")}" placeholder="${esc(record ? tx`${t(record.record_title)} ID` : slot.via ? t("记录 ID") : t("资源 ID"))}" />
        ${hint ? `<small class="permSchemeMuted" data-check-via>${esc(hint)}</small>` : ""}</label>`;
      }).join("");
      const options = instructionNames().map(name => `<option value="${esc(name)}"></option>`).join("");
      const result = state.checkResult;
      const reason = item => (item.allowed ? t("允许") : item.reason === "denied" ? t("命中 deny 表项") : t("没有覆盖它的 permit 表项"));
      const resultHtml = !result ? "" : result.error
        ? `<p class="permSchemeNotice" data-tone="error">${esc(result.error)}</p>`
        : `<div class="permSchemeCheckResult" data-allowed="${Boolean(result.allowed)}"><strong>${esc(result.allowed ? t("允许") : t("拒绝"))}</strong>
            <ul>${(result.requests || []).map(item => `<li><code>${esc(item.action || "")}</code> · <code>${esc(item.resource || "")}</code>：${esc(reason(item))}${item.permit ? ` · permit <code>${esc(item.permit.entry_id || "")}</code>` : ""}${item.deny ? ` · deny <code>${esc(item.deny.entry_id || "")}</code>` : ""}</li>`).join("")}</ul></div>`;
      if (!enabled()) {
        return `<div class="permSchemeCheckView"><p class="permSchemeHelp">${esc(t("权限系统已关闭，没有可判定的权限实例。在“执行设置”中开启权限系统后才能试判定。"))}</p></div>`;
      }
      if (!active) {
        return `<div class="permSchemeCheckView"><p class="permSchemeHelp">${esc(t("试判定在当前启用方案的实例上进行。启用这套方案（并保存全部修改）后才能在这里试判定。"))}</p></div>`;
      }
      return `<div class="permSchemeCheckView">
        <p class="permSchemeHelp">${esc(t("按当前启用方案的实例判定，只看执行者自身的权限。"))}</p>
        <div class="permSchemeInline">
          <label class="portalField"><span>${esc(t("执行者（留空为自己）"))}</span><input data-check="principal" data-focus="check-principal" value="${esc(input.principal)}" placeholder="resident_… / username" /></label>
          <label class="portalField"><span>${esc(t("指令"))}</span><input list="permSchemeInstructions" data-check="instruction" data-focus="check-instruction" value="${esc(input.instruction)}" /></label>
          <datalist id="permSchemeInstructions">${options}</datalist>
        </div>
        <div class="permSchemeInline">${args || (input.instruction ? `<span class="permSchemeMuted">${esc(elementsOf(input.instruction).length ? t("这条指令只有固定槽，没有要填的参数。") : t("Portal 中没有这条指令。"))}</span>` : "")}</div>
        <button type="button" class="portalButton is-primary is-compact" data-check-run data-portal-tooltip="${esc(t("按当前启用方案的实例判定这次调用"))}" ${state.checking || !input.instruction ? "disabled" : ""}>${esc(state.checking ? t("正在判定…") : t("试判定"))}</button>
        ${resultHtml}</div>`;
    }

    async function tryDecide() {
      const input = state.checkInput;
      // A dotted name is a nested argument: fields.project_id is {fields: {project_id}}.
      const args = {};
      for (const [name, value] of Object.entries(input.arguments)) {
        if (!value) continue;
        const parts = name.split(".");
        let target = args;
        for (const part of parts.slice(0, -1)) target = target[part] = target[part] && typeof target[part] === "object" ? target[part] : {};
        target[parts.at(-1)] = value;
      }
      state.checking = true;
      render();
      try {
        // principal_id is required; an empty field means the signed-in user.
        const principal = input.principal || (await window.PortalIdentity.load()).username;
        state.checkResult = await run("CheckPermission", {
          principal_id: principal, instruction: input.instruction, arguments: args,
        });
      } catch (error) {
        state.checkResult = {error: error.message || String(error)};
      } finally {
        state.checking = false;
        if (!state.destroyed) render();
      }
    }

    return {
      checkHtml, tryDecide,
    };
  }

  window.PortalPermissionSchemesCheck = Object.freeze({ createCheck, dependencies: DEPENDENCIES });
})();
