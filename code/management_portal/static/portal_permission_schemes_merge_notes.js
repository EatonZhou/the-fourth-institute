(() => {
  "use strict";

  if (window.PortalPermissionSchemesMergeNotes) return;

  // Merges of a copy's source: where a copy came from, the source's
  // changes being merged with their choices, and the last merge or release
  // write-back with its conflicts and the rows it did not write back.

  const DEPENDENCIES = Object.freeze([
    "TREE_TITLES", "authDiffHtml", "authDiffParts", "esc", "isTemporary", "may", "mergeUnits", "same", "state", "t",
    "tx",
  ]);

  function createMergeNotes(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesMergeNotes is missing: ${missing.join(", ")}`);
    const {
      TREE_TITLES, authDiffHtml, authDiffParts, esc, isTemporary, may, mergeUnits, same, state, t, tx,
    } = dependencies;

    const schemeTitle = schemeId => state.schemes.find(item => item.scheme_id === schemeId)?.title || schemeId;

    // Where a copy came from, whether the source changed since it was copied or last merged,
    // and what the last merge could not take: conflicts, rows not written back and rows left deleted.
    function sourceNoteHtml() {
      const source = state.source;
      if (!source?.scheme_id || state.current.builtin) return "";
      const status = !source.current_digest ? t("来源方案已删除")
        : source.outdated ? t("来源有新修改") : t("已包含来源的全部修改");
      const merge = source.last_merge;
      const counts = mergeCounts(merge);
      const label = automatic(merge) ? tx`上次自动合并有 ${counts.join(t("、"))}` : tx`上次合并有 ${counts.join(t("、"))}`;
      return `<span class="permSchemeMuted" data-source-note>${esc(tx`来源：${schemeTitle(source.scheme_id)}`)}</span>
        <span class="permSchemeBadge${source.outdated ? " is-outdated" : ""}" data-source-status>${esc(status)}</span>
        ${counts.length ? `<button type="button" class="portalButton is-compact" data-action="last-merge" data-last-merge-open aria-expanded="${state.review}">${esc(label)}</button>` : ""}`;
    }

    // What a merge or a release write-back left for people to look at.
    const mergeCounts = merge => [[merge?.conflicts?.length, count => tx`${count} 项冲突`], [merge?.unrestored?.length, count => tx`${count} 条表项未写回`],
      [merge?.deleted_again?.length, count => tx`${count} 行因你删过而没加回`],
      // A merge that changed who may do what opens the panel for its 授权差 too.
      [(merge?.auth_diff?.groups?.total || merge?.auth_diff?.remapped?.total) ? 1 : 0, () => tx`授权差：${authDiffParts(merge.auth_diff).kindLabel}`],
    ].filter(([count]) => count).map(([count, text]) => text(count));

    // A release that changed a built-in's rules regenerated its instance and wrote the instance's edits back
    // (GetPermissionScheme.last_merge, shaped like a copy's source.last_merge). One that changed only the
    // instruction classification left the instance alone (regenerated: false): only A elements moved.
    const followedOnly = merge => merge?.regenerated === false;
    function releaseNoteHtml() {
      const counts = mergeCounts(state.ownMerge);
      const label = followedOnly(state.ownMerge) ? tx`上次跟随发布有 ${counts.join(t("、"))}` : tx`上次随发布重新生成有 ${counts.join(t("、"))}`;
      return counts.length ? `<button type="button" class="portalButton is-compact" data-action="last-merge" data-last-merge-open aria-expanded="${state.review}">${esc(label)}</button>` : "";
    }
    // The last merge of a copy, or the last release write-back of a built-in.
    const lastMerge = () => state.current?.builtin ? state.ownMerge : state.source?.last_merge;

    // One unit of the rules, named for people.
    function changeLabel(change) {
      const [kind, name, id] = change.key.split("|");
      const value = change.now ?? change.own ?? change.was ?? {};
      const titled = (title, key) => title && title !== key ? `${title} (${key})` : key;
      if (kind === "field") return name === "description" ? t("说明") : tx`规则字段 ${name}`;
      if (kind === "tree") return tx`${t(TREE_TITLES[name] || name)}：根与默认位置`;
      if (kind === "node") return tx`${t(TREE_TITLES[name] || name)}节点 ${titled(value.title, id)}`;
      if (kind === "entry") return tx`表规则 ${titled(value.title, name)}`;
      return tx`A 元素 ${name} 的归类`;
    }

    // missing lists the trees whose node a row names is gone.
    const missingText = missing => {
      const trees = [].concat(missing || []).map(tree => t(TREE_TITLES[tree] || tree));
      return trees.length ? tx`${trees.join(t("、"))} 中的节点已不存在` : "";
    };
    // A row the instance's edits added or disabled, or a temporary credential, that a merge did not write back,
    // and a row it left deleted.
    function unrestoredHtml(row) {
      const temporary = isTemporary(row) ? [t("临时凭证"), row.issued_by ? tx`签发者 ${row.issued_by}` : "", row.expires_at ? tx`到期 ${row.expires_at}` : ""] : [];
      return `<li class="permSchemeMergeItem" data-unrestored="${esc(row.entry_id || "")}">
        <div class="permSchemeMergeHead"><code>${esc(row.entry_id || "")}</code>
          <span>${esc([row.effect, row.p, row.a, row.r].join(" · "))}</span>
          ${row.disabled ? `<span class="permSchemeBadge">${esc(t("已停用"))}</span>` : ""}
          <small class="permSchemeMuted">${esc([missingText(row.missing), ...temporary].filter(Boolean).join(" · "))}</small></div></li>`;
    }
    const deletedAgainHtml = row => `<li class="permSchemeMergeItem" data-deleted-again>
        <div class="permSchemeMergeHead"><span>${esc([row.effect, row.p, row.a, row.r].join(" · "))}</span></div></li>`;
    const showUnit = value => value === undefined ? t("（没有）") : JSON.stringify(value, null, 2);

    // The source's changes since the recorded rules, one item each; conflicts wait for a choice.
    function mergeHtml() {
      const merge = state.merge;
      if (!merge) return "";
      const sideHtml = (label, value) => `<div><small>${esc(t(label))}</small><pre>${esc(showUnit(value))}</pre></div>`;
      const items = merge.changes.map((change, index) => {
        const pick = merge.picks.get(change.key);
        const verb = change.was === undefined ? t("来源新增") : change.now === undefined ? t("来源删除") : t("来源修改");
        const label = esc(changeLabel(change));
        const key = esc(change.key);
        const head = change.conflict
          ? `<strong>${label}</strong><span class="permSchemeBadge">${esc(verb)}</span><span class="permSchemeBadge is-conflict">${esc(t("冲突"))}</span>
            <span class="permSchemeMuted">${esc(change.own === undefined ? t("本方案删除了这一项") : change.was === undefined ? t("本方案也新增了这一项，内容不同") : t("本方案也修改了这一项"))}</span>
            <span class="permSchemeMergeChoice" role="radiogroup" aria-label="${label}">
              <label><input type="radio" name="merge-pick-${index}" data-merge-pick="${key}" value="source" ${pick === "source" ? "checked" : ""} /> ${esc(t("采用来源的"))}</label>
              <label><input type="radio" name="merge-pick-${index}" data-merge-pick="${key}" value="own" ${pick === "own" ? "checked" : ""} /> ${esc(t("保留本方案的"))}</label></span>`
          : `<label class="permSchemeCheckbox"><input type="checkbox" data-merge-pick="${key}" ${pick === "source" ? "checked" : ""} /> ${label}</label><span class="permSchemeBadge">${esc(verb)}</span>`;
        return `<li class="permSchemeMergeItem${change.conflict ? " is-conflict" : ""}" data-merge-item="${key}">
          <div class="permSchemeMergeHead">${head}</div>
          <details><summary>${esc(t("查看内容"))}</summary><div class="permSchemeMergeSides">${sideHtml("来源原来", change.was)}${sideHtml("来源现在", change.now)}${change.conflict ? sideHtml("本方案", change.own) : ""}</div></details></li>`;
      }).join("");
      const conflicts = merge.changes.filter(change => change.conflict).length;
      const unresolved = merge.changes.some(change => change.conflict && !merge.picks.get(change.key));
      const summary = !merge.changes.length ? t("来源的修改本方案都已包含；保存后来源记录更新到来源现在的规则。")
        : conflicts ? tx`来源方案 ${schemeTitle(state.source.scheme_id)} 在上次复制或合并之后有 ${merge.changes.length} 项修改，其中 ${conflicts} 项与本方案的修改冲突。`
          : tx`来源方案 ${schemeTitle(state.source.scheme_id)} 在上次复制或合并之后有 ${merge.changes.length} 项修改。`;
      return `<section class="permSchemeMerge" data-merge aria-label="${esc(t("合并来源的修改"))}">
        <p><strong>${esc(summary)}</strong></p>
        ${merge.changes.length ? `<p class="permSchemeMuted">${esc(t("勾选要合并的修改；冲突项是本方案也改过的同一项，需要选择采用哪一边。保存前先检查规则并列出实例的变化：实例按合并后的规则重新生成，实例中的编辑和临时凭证写回：增加和停用的行写回，删掉的行仍删掉。"))}</p>
        <ul class="permSchemeMergeItems">${items}</ul>` : ""}
        <div class="permSchemeButtons">
          <button type="button" class="portalButton is-primary is-compact" data-action="merge-apply" ${state.busy || unresolved || !may("UpdatePermissionScheme") ? "disabled" : ""}>${esc(t("合并并保存"))}</button>
          <button type="button" class="portalButton is-compact" data-action="merge-cancel">${esc(t("取消"))}</button>
          ${unresolved ? `<span class="permSchemeMuted">${esc(t("还有冲突项没有选择。"))}</span>` : ""}
        </div></section>`;
    }

    // A side of a conflict as recorded by the server: null means the side has no such unit.
    const side = value => value ?? undefined;
    // Merges at startup are made by portal; a merge saved with source_digest names the user who saved it.
    const automatic = merge => !merge?.merged_by || merge.merged_by === "portal";

    // The last merge: each conflict kept this scheme's version and can take the source's instead, saved like any
    // edit; rows whose nodes are gone were not written back to the instance; generated rows people had deleted
    // stayed deleted although the new rules give them again.
    function lastMergeHtml() {
      const merge = lastMerge();
      if (!state.review || !merge) return "";
      const units = mergeUnits(state.draft);
      const saved = mergeUnits(JSON.parse(state.base));
      const sideHtml = (label, value) => `<div><small>${esc(t(label))}</small><pre>${esc(showUnit(value))}</pre></div>`;
      const writable = may("UpdatePermissionScheme") && !state.busy && !state.pendingSave;
      const conflicts = (merge.conflicts || []).map(conflict => {
        const [was, now, own] = [side(conflict.was), side(conflict.now), side(conflict.own)];
        const unit = units.get(conflict.key);
        const status = same(unit, now) ? t("已采用来源的") : same(unit, own) ? t("保留本方案的") : t("已另行修改");
        const unsaved = !same(unit, saved.get(conflict.key));
        const key = esc(conflict.key);
        return `<li class="permSchemeMergeItem is-conflict" data-merge-item="${key}">
          <div class="permSchemeMergeHead"><strong>${esc(changeLabel({key: conflict.key, was, now, own}))}</strong>
            <span class="permSchemeBadge" data-merge-status>${esc(status)}</span>
            ${unsaved ? `<span class="permSchemeBadge is-draft">${esc(t("未保存"))}</span>` : ""}
            <span class="permSchemeButtons">
              <button type="button" class="portalButton is-compact" data-merge-take="${key}" data-merge-side="now" ${writable && !same(unit, now) ? "" : "disabled"}>${esc(t("采用来源的"))}</button>
              <button type="button" class="portalButton is-compact" data-merge-take="${key}" data-merge-side="own" ${writable && !same(unit, own) ? "" : "disabled"}>${esc(t("保留本方案的"))}</button>
            </span></div>
          <details><summary>${esc(t("查看内容"))}</summary><div class="permSchemeMergeSides">${sideHtml("来源原来", was)}${sideHtml("来源现在", now)}${sideHtml("本方案", own)}</div></details></li>`;
      }).join("");
      const rows = (merge.unrestored || []).map(unrestoredHtml).join("");
      const deleted = (merge.deleted_again || []).map(deletedAgainHtml).join("");
      const when = merge.merged_at ? new Date(merge.merged_at * 1000).toLocaleString() : "";
      const source = schemeTitle(merge.source_scheme_id || state.source?.scheme_id);
      // A built-in's write-back has no basis when its instance was saved before the rules it came from were kept.
      const head = state.current.builtin
        ? (followedOnly(merge) ? tx`${when} 的发布改变了指令分类：实例的表项不变，移动的 A 元素带来下面的授权变化。`
          : merge.from_digest ? tx`${when} 的发布改变了内置规则：实例已按新规则重新生成，实例中的编辑写回。`
            : tx`${when} 的发布改变了内置规则：实例没有记录生成时的规则，已按新规则重新生成。`)
        : automatic(merge) ? tx`来源方案 ${source} 的修改已在 ${when} 自动合并到本方案。`
          : tx`${merge.merged_by} 在 ${when} 把来源方案 ${source} 的修改合并到本方案。`;
      const label = !state.current.builtin ? t("上次合并") : followedOnly(merge) ? t("上次跟随发布") : t("上次随发布重新生成");
      return `<section class="permSchemeMerge" data-last-merge aria-label="${esc(label)}">
        <p><strong>${esc(head)}</strong></p>
        ${authDiffHtml(merge.auth_diff, JSON.parse(state.base))}
        ${conflicts ? `<p class="permSchemeMuted">${esc(t("冲突项是本方案也改过的同一项，自动合并保留了本方案的版本。采用来源的版本后按普通保存检查规则、确认实例差异。"))}</p>
        <ul class="permSchemeMergeItems" data-merge-conflicts>${conflicts}</ul>` : ""}
        ${rows ? `<p class="permSchemeMuted">${esc(t("实例按新规则重新生成后，这些实例中的编辑或临时凭证引用的节点已不存在，没有写回。需要时在实例表中按新节点重新添加。"))}</p>
        <ul class="permSchemeMergeItems" data-merge-unrestored>${rows}</ul>` : ""}
        ${deleted ? `<p class="permSchemeMuted">${esc(t("因你删过而没加回的行：规则生成过、后来被人删掉的行，新规则仍然给出，合并时保持删除。需要时在实例表中重新添加。"))}</p>
        <ul class="permSchemeMergeItems" data-merge-deleted-again>${deleted}</ul>` : ""}
        <div class="permSchemeButtons">
          ${rows || deleted ? `<button type="button" class="portalButton is-compact" data-view="instance">${esc(t("打开实例表"))}</button>` : ""}
          <button type="button" class="portalButton is-compact" data-action="last-merge">${esc(t("收起"))}</button>
        </div></section>`;
    }

    return {
      automatic, deletedAgainHtml, lastMergeHtml, mergeHtml, missingText, releaseNoteHtml, side, sourceNoteHtml,
      unrestoredHtml,
    };
  }

  window.PortalPermissionSchemesMergeNotes = Object.freeze({ createMergeNotes, dependencies: DEPENDENCIES });
})();
