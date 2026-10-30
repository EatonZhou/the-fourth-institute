(() => {
  "use strict";

  if (window.PortalPermissionSchemesActions) return;

  // The editor's actions: saving rules after their check and instance
  // difference, discarding, merging the source's changes, copying,
  // activating and deleting a scheme.

  const DEPENDENCIES = Object.freeze([
    "DIFF_LIMIT", "applyChange", "automatic", "busy", "changed", "checkRules", "clone", "confirmDiscard", "enabled",
    "freshInstanceState", "load", "mountState", "open", "previewDiff", "render", "run", "setNotice", "sourceChanges",
    "state", "t", "tx",
  ]);

  function createActions(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesActions is missing: ${missing.join(", ")}`);
    const {
      DIFF_LIMIT, applyChange, automatic, busy, changed, checkRules, clone, confirmDiscard, enabled,
      freshInstanceState, load, mountState, open, previewDiff, render, run, setNotice, sourceChanges, state, t, tx,
    } = dependencies;

    async function act(action) {
      const current = state.current;
      if (action === "discard") {
        if (!(await confirmDiscard())) return;
        setNotice("");
        if (state.stale) { await busy(() => open(current.scheme_id)); return; }
        state.draft = JSON.parse(state.base);
        state.serverErrors = [];
        state.merged = null;
        changed();
      } else if (action === "merge") {
        await busy(async () => {
          // The digest is read before the source's rules: if the source changes in between, saving with it is refused.
          const fresh = await run("GetPermissionScheme", {scheme_id: current.scheme_id});
          if (state.destroyed) return;
          if (fresh.version !== current.version) {
            await open(current.scheme_id);
            setNotice(t("方案已被他人修改，已载入最新版本；请重新合并。"), "error");
            return;
          }
          state.source = fresh.source || null;
          if (!state.source?.outdated) {
            setNotice(state.source?.current_digest ? t("来源方案没有新的修改。") : t("来源方案已删除，没有可合并的修改。"));
            return;
          }
          const source = await run("GetPermissionScheme", {scheme_id: state.source.scheme_id});
          if (state.destroyed) return;
          const changes = sourceChanges(state.source.rules, source.rules, state.draft);
          // Changes this scheme did not touch are taken unless unticked; a conflict waits for a person's choice.
          const picks = new Map(changes.map(change => [change.key, change.conflict ? null : "source"]));
          state.merge = {digest: state.source.current_digest, rules: source.rules, changes, picks};
          state.review = false;
          setNotice("");
        });
      } else if (action === "merge-cancel") {
        state.merge = null;
        render();
      } else if (action === "merge-apply") {
        const merge = state.merge;
        if (!merge || merge.changes.some(change => change.conflict && !merge.picks.get(change.key))) return;
        const draft = clone(state.draft);
        for (const change of merge.changes) if (merge.picks.get(change.key) === "source") applyChange(draft, change, merge.rules);
        state.draft = draft;
        state.merge = null;
        state.merged = {digest: merge.digest, rules: merge.rules};
        changed({rerender: false});
        // The merged rules are checked and the instance difference listed before anything is saved.
        await act("save");
      } else if (action === "last-merge") {
        state.review = !state.review;
        render();
      } else if (action === "copy") {
        await copyScheme(current.scheme_id, state.draft.title);
      } else if (action === "save") {
        await busy(async () => {
          const rules = clone(state.draft);
          const key = JSON.stringify(rules);
          const refuse = result => {
            state.serverErrors = result.errors || [];
            setNotice(t("规则没有通过检查，未保存。"), "error");
          };
          const merge = state.merged ? {source_digest: state.merged.digest} : null;
          try {
            const checked = !merge && mountState.lastCheck?.key === key ? mountState.lastCheck.result : await checkRules(rules, merge || {});
            if (state.destroyed) return;
            if (checked.valid === false) return refuse(checked);
            // Titles, descriptions and the instruction mapping keep the instance; a changed mapping moves A elements,
            // which the 授权差 reports against the current instance (groups with an element, and remapped).
            // The difference to the instance takes seconds.
            if (checked.regenerates) {
              setNotice(t("正在按 Portal 现有对象计算保存后的实例差异，需要几秒…"));
              render();
            }
            const preview = await previewDiff(rules, merge || {});
            if (state.destroyed) return;
            setNotice("");
            if (preview.valid === false) return refuse(preview);
            const diff = preview.diff || {};
            const auth = preview.auth_diff || null;
            const kept = merge ? {unrestored: preview.unrestored || [], deleted_again: preview.deleted_again || []} : {};
            const reported = auth && (auth.groups?.total || auth.remapped?.total || auth.complete === false);
            if (diff.added?.total || diff.removed?.total || diff.lost_edits?.total || kept.unrestored?.length || kept.deleted_again?.length || reported) {
              state.pendingSave = {rules, diff, auth, regenerates: preview.regenerates !== false, merge: Boolean(merge), ...kept};
            } else await saveRules(rules);
          } catch (error) {
            if (!(merge && error.code === "instruction.conflict")) throw error;
            await mergeRefused();
          }
        });
      } else if (action === "confirm-save") {
        const pending = state.pendingSave;
        if (pending) await busy(() => saveRules(pending.rules));
      } else if (action === "cancel-save") {
        state.pendingSave = null;
        render();
      } else if (action === "activate") {
        // The dry_run's effect and 授权差 are confirmed in the editor; the report never blocks the button.
        await busy(async () => {
          const preview = await run("ActivatePermissionScheme", {scheme_id: current.scheme_id, dry_run: true, diff_limit: DIFF_LIMIT});
          if (state.destroyed) return;
          state.pendingActivate = {scheme_id: current.scheme_id, preview, on: enabled()};
        });
      } else if (action === "confirm-activate") {
        const pending = state.pendingActivate;
        if (!pending) return;
        await busy(async () => {
          await run("ActivatePermissionScheme", {scheme_id: pending.scheme_id}, "permission-scheme-activate");
          state.pendingActivate = null;
          setNotice(pending.on ? t("方案已启用。") : t("开启权限系统时将使用这套方案。"));
          await load(pending.scheme_id);
        });
      } else if (action === "cancel-activate") {
        state.pendingActivate = null;
        render();
      } else if (action === "delete") {
        const approved = await window.PortalUI.confirm({title: tx`删除方案 ${state.draft.title || current.scheme_id}？`, message: t("方案的规则和实例一起删除，无法恢复。"), confirmLabel: t("删除方案"), tone: "danger"});
        if (!approved) return;
        await busy(async () => {
          await run("DeletePermissionScheme", {scheme_id: current.scheme_id}, "permission-scheme-delete", {expectedVersion: current.version});
          state.current = null;
          state.draft = null;
          state.base = "";
          setNotice(t("方案已删除。"));
          await load(state.system?.scheme?.scheme_id || "");
        });
      }
    }

    // A copy takes the rules and the instance as they are saved.
    async function copyScheme(sourceId, sourceTitle) {
      const title = await window.PortalUI.prompt({title: t("复制为新方案"), label: t("新方案名称"), value: tx`${sourceTitle || sourceId} 副本`, confirmLabel: t("下一步")});
      if (!title || state.destroyed) return;
      const taken = new Set(state.schemes.map(item => item.scheme_id));
      const stem = `${String(sourceId).slice(0, 52)}_copy`;
      let suggested = stem;
      for (let suffix = 2; taken.has(suggested); suffix += 1) suggested = `${stem}_${suffix}`;
      const schemeId = await window.PortalUI.prompt({title: t("复制为新方案"), label: t("新方案 ID（小写字母开头，只含小写字母、数字和下划线；保存后不能修改）"), value: suggested, confirmLabel: t("复制")});
      if (!schemeId || state.destroyed) return;
      await busy(async () => {
        const result = await run("CreatePermissionScheme", {scheme_id: String(schemeId).trim(), source_scheme_id: sourceId, title: String(title).trim()}, "permission-scheme-create");
        setNotice(tx`已复制为新方案，实例 ${result.nodes ?? "?"} 个节点、${result.entries ?? "?"} 条表项。`);
        await load(result.scheme_id || String(schemeId).trim());
      });
    }

    async function saveRules(rules) {
      const current = state.current;
      const merged = state.merged;
      try {
        // A merge carries the source digest it was made against: the server writes back the instance edits
        // as an automatic merge does and moves the source record in the same save.
        const result = await run("UpdatePermissionScheme", {scheme_id: current.scheme_id, rules,
          ...(merged ? {source_digest: merged.digest} : {})}, "permission-scheme-update", {expectedVersion: current.version});
        current.version = result.version ?? current.version;
        state.base = JSON.stringify(rules);
        state.pendingSave = null;
        state.serverErrors = [];
        state.merged = null;
        const regenerated = result.regenerated;
        const left = merged && (result.unrestored?.length || result.deleted_again?.length);
        setNotice([regenerated ? tx`规则已保存，实例已重新生成：${regenerated.nodes ?? "?"} 个节点，${regenerated.entries ?? "?"} 条表项。` : t("规则已保存，实例不变。"),
          merged ? t("来源记录已更新到来源现在的规则。") : "", left ? t("没有写回的表项列在下面的上次合并中。") : ""].filter(Boolean).join(" "));
        state.instance = freshInstanceState();
        if (merged) state.source = {...state.source, rules: merged.rules, digest: result.source_digest || merged.digest, outdated: false};
        // The source record now holds this merge as last_merge.
        const [list, fresh] = await Promise.all([run("ListPermissionSchemes"), merged ? run("GetPermissionScheme", {scheme_id: current.scheme_id}) : null]);
        if (state.destroyed) return;
        state.schemes = list.schemes || state.schemes;
        if (fresh?.source) state.source = fresh.source;
        if (left) state.review = true;
      } catch (error) {
        state.pendingSave = null;
        if (error.details?.errors) { state.serverErrors = error.details.errors; setNotice(t("规则没有通过检查，未保存。"), "error"); return; }
        if (merged && error.code === "instruction.conflict") { await mergeRefused(); return; }
        if (error.code !== "instruction.version_conflict") throw error;
        state.stale = true;
        // Portal merges a copy's source when it starts; one that finished during this merge moved the scheme to the
        // same source rules. The conflict cannot tell, so the scheme's last merge is read.
        const last = merged ? (await run("GetPermissionScheme", {scheme_id: current.scheme_id})).source?.last_merge : null;
        if (state.destroyed) return;
        if (last && automatic(last) && last.to_digest === merged.digest) {
          setNotice(t("合并没有保存：Portal 已自动合并了来源的同一批修改。放弃修改即可载入合并后的方案，需要时在“上次自动合并”中调整冲突项。"), "error");
          return;
        }
        setNotice(tx`方案已被他人修改（当前版本 ${error.details?.current_version ?? "?"}）。放弃修改即可载入最新版本。`, "error");
      }
    }

    // The source changed after it was read: the digest no longer matches, so this merge is not saved. The draft
    // stays for reference; the source record is read again so the merge can start over from the saved rules.
    async function mergeRefused() {
      state.merged = null;
      state.pendingSave = null;
      const fresh = await run("GetPermissionScheme", {scheme_id: state.current.scheme_id});
      if (state.destroyed) return;
      state.source = fresh.source || null;
      setNotice(t("合并没有保存：来源方案在读取之后又有修改。已重新读取来源，放弃修改后重新合并来源的修改。"), "error");
    }

    return {
      act, copyScheme,
    };
  }

  window.PortalPermissionSchemesActions = Object.freeze({ createActions, dependencies: DEPENDENCIES });
})();
