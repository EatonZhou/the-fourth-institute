(() => {
  "use strict";

  if (window.PortalPermissionSchemesReview) return;

  // What the editor shows before a change takes effect: the server's check
  // of the draft, the instance difference a save waits on and the
  // activation waiting for confirmation.

  const DEPENDENCIES = Object.freeze([
    "authDiffHtml", "deletedAgainHtml", "dirty", "elementMap", "esc", "may", "missingText", "nodeTitles", "state",
    "t", "tx", "unrestoredHtml",
  ]);

  function createReview(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesReview is missing: ${missing.join(", ")}`);
    const {
      authDiffHtml, deletedAgainHtml, dirty, elementMap, esc, may, missingText, nodeTitles, state, t, tx,
      unrestoredHtml,
    } = dependencies;

    // Classifications of A elements no instruction declares any more (an instruction removed, or one that gained a
    // second slot and named its elements after them): the server's compiler refuses them when saving.
    const staleMappings = () => !state.vocabulary.instructions ? []
      : Object.keys(state.draft?.instructions || {}).filter(element => !elementMap().has(element));

    // Errors come only from the server's compiler.
    function validationHtml() {
      const errors = state.serverErrors;
      if (!errors.length) {
        const editable = state.current && !state.current.builtin && may("UpdatePermissionScheme");
        const status = !editable ? "" : state.compiling ? t("服务端检查中…") : dirty() ? t("服务端检查通过。") : "";
        const stale = editable ? staleMappings().length : 0;
        const hint = stale ? `<span class="permSchemeWarning" data-stale-mappings>${esc(tx`${stale} 条归类指向指令声明中已没有的 A 元素，保存规则前要在「指令映射」的「已失效」中删除。`)}</span>` : "";
        return `<div class="permSchemeValidation" data-validation data-ok="true">${esc(status)}${status && hint ? " " : ""}${hint}</div>`;
      }
      const shown = errors.slice(0, 12).map(item => `<li><button type="button" class="permSchemeLink" data-goto-error="${esc(item.path)}"><code>${esc(item.path)}</code></button> ${esc(item.message)}</li>`).join("");
      const more = errors.length > 12 ? `<li>${esc(tx`还有 ${errors.length - 12} 项`)}</li>` : "";
      return `<div class="permSchemeValidation" data-validation data-ok="false" role="alert"><strong>${esc(tx`服务端检查发现 ${errors.length} 个问题，处理后才能保存`)}</strong><ul>${shown}${more}</ul></div>`;
    }

    // An instance entry as text: @ IDs are the rules' class nodes (titled from the saved rules), the rest object IDs.
    function instanceNodeLabel(tree, id) {
      const saved = state.base ? JSON.parse(state.base) : state.draft;
      const title = String(id).startsWith("@") ? saved?.trees?.[tree]?.nodes?.find(node => node.id === id)?.title : nodeTitles[tree].get(id);
      return title && title !== id ? `${title} (${id})` : String(id);
    }
    const entryText = entry => `${entry.effect} · P ${instanceNodeLabel("P", entry.p)} · A ${instanceNodeLabel("A", entry.a)} · R ${instanceNodeLabel("R", entry.r)}${entry.disabled ? ` · ${t("已停用")}` : ""}`;

    // Saving changed trees or entry rules regenerates the instance; the dry_run difference is confirmed first.
    function pendingSaveHtml() {
      const pending = state.pendingSave;
      if (!pending) return "";
      const {added, removed, lost_edits: lost} = pending.diff;
      const group = (title, part, line) => {
        if (!part?.total) return "";
        const items = (part.items || []).map(item => `<li>${esc(line(item))}</li>`).join("");
        const more = part.total > (part.items || []).length ? `<li>${esc(tx`还有 ${part.total - (part.items || []).length} 条`)}</li>` : "";
        return `<details class="permSchemeDiffGroup" data-diff="${esc(title)}"><summary>${esc(tx`${t(title)}：${part.total} 条`)}</summary><ul>${items}${more}</ul></details>`;
      };
      const kinds = {added: t("在实例中新增"), deleted: t("在实例中删除"), disabled: t("在实例中停用"), unrestored: t("写不回")};
      const lostLine = item => [`${kinds[item.kind] || item.kind}：${entryText(item.entry)}`, missingText(item.missing)].filter(Boolean).join(" · ");
      // A merge writes back what people did to the instance; what it cannot is listed row by row (unrestored
      // carries the entry IDs and credential details that lost_edits leaves out).
      const rows = (title, list, html, data) => list?.length
        ? `<details class="permSchemeDiffGroup" data-diff="${esc(title)}" ${data}><summary>${esc(tx`${t(title)}：${list.length} 条`)}</summary><ul class="permSchemeMergeItems">${list.map(html).join("")}</ul></details>` : "";
      const body = [
        group("新增的表项", added, entryText),
        group("删除的表项", removed, entryText),
        !pending.merge ? group("将丢失的实例修改", lost, lostLine)
          : pending.unrestored?.length ? rows("写不回的表项", pending.unrestored, unrestoredHtml, "data-pending-unrestored")
            : group("写不回的表项", lost, lostLine),
        pending.merge ? rows("因你删过而没加回的行", pending.deleted_again, deletedAgainHtml, "data-pending-deleted-again") : "",
      ].join("");
      const head = !pending.regenerates ? t("保存后实例的表项不变；指令映射的修改移动了下列 A 元素，授权随之改变。")
        : pending.merge
          ? t("合并保存：实例按合并后的规则重新生成，实例中的编辑和临时凭证写回：增加和停用的行带原 ID 写回，删掉的行仍删掉；节点已不存在的表项写不回。")
          : t("保存后按新规则重新生成这套方案的实例，此前对实例的编辑不保留。");
      return `<section class="permSchemePendingSave" data-pending-save role="alertdialog" aria-label="${esc(t("确认保存规则"))}">
        <p><strong>${esc(head)}</strong></p>
        ${authDiffHtml(pending.auth, pending.rules)}
        ${body || (pending.regenerates ? `<p class="permSchemeMuted">${esc(t("实例的表项不会变化。"))}</p>` : "")}
        <div class="permSchemeButtons">
          <button type="button" class="portalButton is-primary is-compact" data-action="confirm-save" ${state.busy ? "disabled" : ""}>${esc(t("确认保存"))}</button>
          <button type="button" class="portalButton is-compact" data-action="cancel-save">${esc(t("返回编辑"))}</button>
        </div></section>`;
    }

    // Activating: what bringing the saved instance up to date does, and what principals gain and lose against
    // the scheme in use (授权差). With the permission system off it only records the choice.
    function pendingActivateHtml() {
      const pending = state.pendingActivate;
      if (!pending || pending.scheme_id !== state.current?.scheme_id) return "";
      const preview = pending.preview || {};
      const title = state.draft.title || pending.scheme_id;
      const fill = [preview.added_nodes ? tx`加入 ${preview.added_nodes} 个节点` : "", preview.moved_nodes ? tx`移动 ${preview.moved_nodes} 个节点` : "",
        preview.removed_nodes ? tx`删除 ${preview.removed_nodes} 个节点` : "",
        preview.added_entries ? tx`加入 ${preview.added_entries} 条表项` : "", preview.removed_entries ? tx`删除 ${preview.removed_entries} 条表项` : ""].filter(Boolean).join("、");
      // regenerate: the scheme has no instance of its current rules, so one is generated and replaces what was saved.
      const effect = preview.regenerate
        ? [t("这套方案还没有按当前规则生成的实例：Portal 按规则和现有对象生成实例，原先保存的实例及其中的修改不保留。"),
          tx`生成 ${preview.added_nodes ?? 0} 个节点、${preview.added_entries ?? 0} 条表项${preview.removed_entries ? tx`，替换原有 ${preview.removed_entries} 条表项` : ""}。`]
        : [t("Portal 加载这套方案保存的实例并补齐：停用期间新增的对象按规则加入，删除的对象及引用它们的表项删除；实例中修改过的部分保持原样。"),
          fill ? tx`补齐：${fill}。` : t("实例不需要补齐。")];
      const message = pending.on ? [...effect, t("所有判定立即按这套方案进行。")]
        : [t("权限系统仍保持关闭，现在不影响任何判定；在“执行设置”中开启权限系统时，Portal 补齐这套方案的实例后按它判定。"), ...effect];
      const compared = pending.on ? t("对比启用中的方案：") : t("对比开启时原本使用的方案：");
      return `<section class="permSchemePendingSave" data-pending-activate role="alertdialog" aria-label="${esc(pending.on ? tx`启用 ${title}？` : tx`开启权限系统时使用 ${title}？`)}">
        <p><strong>${esc(pending.on ? tx`启用 ${title}？` : tx`开启权限系统时使用 ${title}？`)}</strong></p>
        <p>${esc(message.join(" "))}</p>
        ${preview.auth_diff ? `<p class="permSchemeMuted">${esc(compared)}</p>${authDiffHtml(preview.auth_diff, state.draft)}` : ""}
        <div class="permSchemeButtons">
          <button type="button" class="portalButton ${pending.on ? "is-danger" : "is-primary"} is-compact" data-action="confirm-activate" ${state.busy ? "disabled" : ""}>${esc(pending.on ? t("启用") : t("设为开启时使用"))}</button>
          <button type="button" class="portalButton is-compact" data-action="cancel-activate">${esc(t("取消"))}</button>
        </div></section>`;
    }

    return {
      entryText, instanceNodeLabel, pendingActivateHtml, pendingSaveHtml, staleMappings, validationHtml,
    };
  }

  window.PortalPermissionSchemesReview = Object.freeze({ createReview, dependencies: DEPENDENCIES });
})();
