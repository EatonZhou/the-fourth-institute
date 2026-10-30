(() => {
  "use strict";

  if (window.PortalPermissionSchemesRender) return;

  // Drawing the page: the scheme list, the editor with its tabs and
  // actions, and redrawing only the status while the user types.

  const DEPENDENCIES = Object.freeze([
    "SCHEME_WRITES", "activeLabel", "checkHtml", "dirty", "editable", "enabled", "entriesHtml", "esc", "instanceHtml",
    "instructionsHtml", "lastMergeHtml", "may", "mergeHtml", "pendingActivateHtml", "pendingSaveHtml",
    "releaseNoteHtml", "root", "sourceNoteHtml", "state", "t", "treesHtml", "tx", "validationHtml",
  ]);

  function createRender(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesRender is missing: ${missing.join(", ")}`);
    const {
      SCHEME_WRITES, activeLabel, checkHtml, dirty, editable, enabled, entriesHtml, esc, instanceHtml,
      instructionsHtml, lastMergeHtml, may, mergeHtml, pendingActivateHtml, pendingSaveHtml, releaseNoteHtml, root,
      sourceNoteHtml, state, t, treesHtml, tx, validationHtml,
    } = dependencies;

    // ---- rendering ------------------------------------------------------------------
    function render() {
      if (state.destroyed) return;
      const active = root.contains(document.activeElement) ? document.activeElement : null;
      const focus = active?.dataset.focus || "";
      const caret = typeof active?.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
      const scrolls = [...root.querySelectorAll("[data-scroll]")].map(item => [item.dataset.scroll, item.scrollTop, item.scrollLeft]);
      root.innerHTML = listHtml() + editorHtml();
      for (const [key, top, left] of scrolls) {
        const item = root.querySelector(`[data-scroll="${key}"]`);
        if (item) { item.scrollTop = top; item.scrollLeft = left; }
      }
      const restored = focus ? root.querySelector(`[data-focus="${CSS.escape(focus)}"]`) : null;
      if (restored) {
        restored.focus();
        if (caret && typeof restored.setSelectionRange === "function") restored.setSelectionRange(...caret);
      }
    }

    // Redraws only what typing changes, so the focused input keeps its caret.
    function renderStatus() {
      if (state.destroyed) return;
      root.querySelector("[data-editor-actions]")?.replaceWith(fragment(actionsHtml()));
      root.querySelector("[data-validation]")?.replaceWith(fragment(validationHtml()));
      // The server's answer arrives after the entries were drawn; mark the ones it names.
      root.querySelectorAll("[data-entry-card]").forEach(card => card.classList.toggle("is-invalid", entryInvalid(Number(card.dataset.entryCard))));
    }

    const entryInvalid = position => state.serverErrors.some(item => item.path.startsWith(`entries[${position}]`));

    const fragment = html => document.createRange().createContextualFragment(html);

    function listHtml() {
      const system = state.system;
      const title = system?.scheme?.title || system?.scheme?.scheme_id || "";
      const status = !system ? "" : typeof system.enabled !== "boolean"
        ? `<p class="permSchemeMuted" data-system-status>${esc(t("权限系统状态未读取"))}</p>` : !enabled()
        ? `<p class="permSchemeMuted" data-system-status>${esc(t("权限系统已关闭：方案暂不参与判定，规则和实例可以照常查看和编辑，开启权限系统后生效。"))}${title ? ` ${esc(tx`开启时使用：${title}`)}` : ""}</p>`
        : `<p class="permSchemeMuted" data-system-status>${esc(tx`当前启用：${title}`)}</p>`;
      const items = state.schemes.map(item => {
        const selected = item.scheme_id === state.current?.scheme_id;
        const badges = [item.builtin ? `<span class="permSchemeBadge">${esc(t("内置"))}</span>` : "",
          item.active ? `<span class="permSchemeBadge is-active">${esc(activeLabel())}</span>` : "",
          item.outdated ? `<span class="permSchemeBadge is-outdated" data-outdated>${esc(t("来源有新修改"))}</span>` : ""].join("");
        const count = Number.isFinite(item.entry_count) ? ` · ${esc(tx`实例 ${item.entry_count} 条表项`)}` : "";
        // A built-in's rules change only through a copy, so its card offers one.
        const copy = item.builtin ? `<button type="button" class="portalButton is-compact permSchemeListCopy" data-copy-scheme="${esc(item.scheme_id)}"
          ${may("CreatePermissionScheme") && !state.busy ? "" : "disabled"}>${esc(t("复制"))}</button>` : "";
        return `<div class="permSchemeListCard"><button type="button" class="permSchemeListItem" data-open-scheme="${esc(item.scheme_id)}" aria-current="${selected}">
          <span class="permSchemeListTitle"><strong>${esc(item.title || item.scheme_id)}</strong>${badges}</span>
          <small>${esc(item.description || "")}${count}</small></button>${copy}</div>`;
      }).join("");
      return `<aside class="permSchemeList"><h2>${esc(t("权限方案"))}</h2>${status}
        <p class="permSchemeHelp">${esc(t("一套权限方案由规则和实例组成：规则规定 Portal 的主体、动作和资源怎样放进三棵权限树、新对象带来哪些表项；实例是按规则生成的树和权限表，判定只查实例，可以逐行编辑。内置方案的规则只读，要改规则先复制为新方案；内置方案本身可以直接启用，实例可以直接编辑。"))}</p>
        <div class="permSchemeListItems">${items}</div></aside>`;
    }

    function editorHtml() {
      const notice = state.notice ? `<p class="permSchemeNotice" data-tone="${esc(state.noticeTone)}" role="status">${esc(state.notice)}</p>` : "";
      if (state.loading) return `<section class="permSchemeEditor">${notice}<p role="status">${esc(t("正在读取权限方案…"))}</p></section>`;
      if (!state.draft) return `<section class="permSchemeEditor">${notice}</section>`;
      const doc = state.draft;
      const locked = editable() ? "" : "disabled";
      const tabs = [["trees", "三棵树"], ["entries", "表规则"], ["instructions", "指令映射"], ["instance", "实例表"], ["check", "试判定"]]
        .map(([view, label]) => `<button type="button" role="tab" class="portalButton is-compact${state.view === view ? " is-primary" : ""}" aria-selected="${state.view === view}" data-view="${view}">${esc(t(label))}</button>`).join("");
      const body = {trees: treesHtml, entries: entriesHtml, instructions: instructionsHtml, instance: instanceHtml, check: checkHtml}[state.view]();
      return `<section class="permSchemeEditor">
        ${notice}
        <header class="permSchemeHeader">
          <div class="permSchemeTitleFields">
            <label class="portalField"><span>${esc(t("方案名称"))}</span><input data-doc-field="title" data-focus="title" value="${esc(doc.title || "")}" ${locked} /></label>
            <p class="permSchemeMuted">${esc(t("方案 ID"))} <code>${esc(state.current.scheme_id || "")}</code></p>
            <label class="portalField"><span>${esc(t("说明"))}</span><textarea data-doc-field="description" data-focus="description" rows="2" ${locked}>${esc(doc.description || "")}</textarea></label>
          </div>
          ${actionsHtml()}
        </header>
        ${validationHtml()}
        ${pendingSaveHtml()}
        ${pendingActivateHtml()}
        ${mergeHtml()}
        ${lastMergeHtml()}
        <div class="permSchemeTabs" role="tablist">${tabs}</div>
        <div class="permSchemeBody">${body}</div>
      </section>`;
    }

    function actionsHtml() {
      const current = state.current;
      const badges = [
        current.builtin ? `<span class="permSchemeBadge">${esc(t("内置，规则只读"))}</span>` : "",
        current.active ? `<span class="permSchemeBadge is-active">${esc(activeLabel())}</span>` : "",
        dirty() ? `<span class="permSchemeBadge is-draft">${esc(t("规则有未保存的修改"))}</span>` : "",
        state.merged ? `<span class="permSchemeBadge is-draft" data-merged>${esc(t("已合并来源的修改，保存后来源记录更新"))}</span>` : "",
        SCHEME_WRITES.some(may) ? "" : `<span class="permSchemeMuted">${esc(t("你只能查看方案。"))}</span>`,
        // A built-in's rules follow the code and are read-only; the scheme is activated and its instance edited
        // as it is (b820 §5.11).
        current.builtin ? `<span class="permSchemeMuted" data-builtin-note>${esc(t("内置方案的规则跟随代码发布，只读；要改规则先复制。实例可以直接编辑。"))}</span>` : "",
        current.builtin ? releaseNoteHtml() : sourceNoteHtml(),
      ].join("");
      const allowed = name => may(name) && !state.busy;
      const clean = !state.serverErrors.length;
      const buttons = [];
      // A merge is saved even when the draft already matched the source: saving moves the source record.
      const changes = dirty() || Boolean(state.merged);
      if (!current.builtin) {
        buttons.push(`<button type="button" class="portalButton is-primary is-compact" data-action="save" ${allowed("UpdatePermissionScheme") && changes && clean && !state.pendingSave ? "" : "disabled"}>${esc(t("保存规则"))}</button>`);
        buttons.push(`<button type="button" class="portalButton is-compact" data-action="discard" ${!state.busy && changes ? "" : "disabled"}>${esc(t("放弃修改"))}</button>`);
        // Merging starts from the saved rules: the source's changes are compared with what this scheme saved.
        if (state.source?.outdated && state.source.current_digest) {
          const ready = allowed("UpdatePermissionScheme") && !changes && !state.merge && !state.pendingSave;
          buttons.push(`<button type="button" class="portalButton is-compact" data-action="merge" ${ready ? "" : "disabled"}${!changes ? "" : ` data-portal-tooltip="${esc(t("先保存或放弃当前修改"))}"`}>${esc(t("合并来源的修改"))}</button>`);
        }
      }
      buttons.push(`<button type="button" class="portalButton is-compact" data-action="copy" ${allowed("CreatePermissionScheme") && !dirty() ? "" : "disabled"}>${esc(t("复制为新方案"))}</button>`);
      if (!current.active) {
        const label = enabled() ? (dirty() ? t("保存后才能启用") : t("启用")) : (dirty() ? t("保存后才能设为开启时使用") : t("设为开启时使用"));
        buttons.push(`<button type="button" class="portalButton is-compact" data-action="activate" ${allowed("ActivatePermissionScheme") && !dirty() && !state.pendingActivate ? "" : "disabled"}>${esc(label)}</button>`);
      }
      if (!current.builtin && !current.active) buttons.push(`<button type="button" class="portalButton is-compact is-danger" data-action="delete" ${allowed("DeletePermissionScheme") ? "" : "disabled"}>${esc(t("删除方案"))}</button>`);
      return `<div class="permSchemeActions" data-editor-actions><div class="permSchemeBadges">${badges}</div><div class="permSchemeButtons">${buttons.join("")}</div></div>`;
    }

    return {
      entryInvalid, render, renderStatus,
    };
  }

  window.PortalPermissionSchemesRender = Object.freeze({ createRender, dependencies: DEPENDENCIES });
})();
