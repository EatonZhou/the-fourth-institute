(() => {
  "use strict";

  if (window.PortalRepoWorkspaceGitActions) return;

  // Changes to the selected Repo: switching branches, pull and push with the
  // credential retry, discarding changes, deleting a file, committing, the
  // remote, push policy and SSH key settings, deleting a saved credential and
  // unregistering the Repo.
  const DEPENDENCIES = Object.freeze([
    "detailResourceGuest", "dirname", "getJson", "invalidateRevisionViews", "loadRepos", "loadWorkspace",
    "postJson", "queryState", "refreshOverview", "renderWorkspace", "repoInstruction",
    "retryGitAuthentication", "setBusy", "setSettingsBusy", "showSettingsSaved", "showToast", "state",
    "writeQuery",
  ]);

  function createGitActions(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceGitActions is missing: ${missing.join(", ")}`);
    const {
      detailResourceGuest, dirname, getJson, invalidateRevisionViews, loadRepos, loadWorkspace, postJson,
      queryState, refreshOverview, renderWorkspace, repoInstruction, retryGitAuthentication, setBusy,
      setSettingsBusy, showSettingsSaved, showToast, state, writeQuery,
    } = dependencies;

    async function switchBranch(name, button) {
      const select = button?.tagName === "SELECT";
      if (select) button.disabled = true;
      else setBusy(button, true, "切换中…");
      try {
        state.overview = await postJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/branch`,
          { operation: "switch", name },
        );
        invalidateRevisionViews();
        state.file = null;
        state.tree = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree`);
        state.activeTab = "code";
        writeQuery({ tab: "code", file: "", path: "" }, { historyMode: "push" });
        renderWorkspace();
        showToast(window.PortalI18n.text`已切换到 ${name}。`, "success");
      } catch (error) {
        showToast(error.message || error, "error");
        if (select) {
          button.disabled = false;
          button.value = state.overview.current_branch;
        } else {
          setBusy(button, false);
        }
      }
    }

    async function syncRepo(operation, button) {
      if (
        operation === "push"
        && !await window.PortalUI.confirm({
          title: window.PortalI18n.t("Push 到 remote？"),
          message: window.PortalI18n.text`把 ${state.overview.current_branch} Push 到 remote？\n这会更新远程仓库。`,
          confirmLabel: "Push",
          tone: "warning",
        })
      ) return;
      setBusy(button, true, `${operation[0].toUpperCase()}${operation.slice(1)}…`);
      try {
        let result;
        try {
          result = await postJson(
            `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/sync`,
            {
              operation,
              remote: state.overview.remotes.find(remote => remote.name === "origin")?.name
                || state.overview.remotes[0]?.name
                || "",
              branch: state.overview.current_branch,
            },
          );
        } catch (error) {
          const authChallenge = error?.data?.auth_challenge;
          if (!authChallenge) throw error;
          result = await retryGitAuthentication(authChallenge);
          if (!result) {
            setBusy(button, false);
            return;
          }
        }
        state.overview = result.workspace;
        invalidateRevisionViews();
        if (!state.file) {
          state.tree = await getJson(
            `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree?path=${encodeURIComponent(state.tree?.path || "")}`,
          );
        }
        renderWorkspace();
        showToast(window.PortalI18n.text`${operation[0].toUpperCase()}${operation.slice(1)} 完成。`, "success");
      } catch (error) {
        if (operation === "push") {
          await refreshOverview().catch(() => {});
        }
        showToast(error.message || error, "error");
        setBusy(button, false);
      }
    }

    async function discardRepoChanges(button) {
      const count = Number(state.overview?.status?.changes?.length || 0);
      if (!count) return;
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.t("放弃未提交改动？"),
        message: window.PortalI18n.text`放弃当前 Repo 的 ${count} 项未提交改动？\n`
          + window.PortalI18n.t("这会恢复 staged 与 unstaged 文件，并永久删除未跟踪文件和目录。此操作不能撤销。"),
        confirmLabel: window.PortalI18n.t("放弃改动"),
        tone: "danger",
      })) return;
      setBusy(button, true, "处理中…");
      // File reads started before or during the Discard would show discarded content.
      state.navigationSequence += 1;
      try {
        const result = await postJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/discard`,
          {},
        );
        state.navigationSequence += 1;
        state.overview = result.workspace;
        invalidateRevisionViews();
        state.commitDraft = { message: "", description: "" };
        state.file = null;
        if (queryState().file) writeQuery({ file: "" }, { historyMode: "replace" });
        state.tree = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree?path=${encodeURIComponent(queryState().path || "")}`,
        );
        renderWorkspace();
        showToast(window.PortalI18n.text`已放弃 ${Number(result.discarded_count || 0)} 项改动。`, "success");
      } catch (error) {
        showToast(error.message || error, "error");
        setBusy(button, false);
      }
    }

    async function deleteFile(path, button) {
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.t("删除文件？"),
        message: window.PortalI18n.text`删除「${path}」？\n文件会从 checkout 中移除，并作为未提交变更保留在 Changes。`,
        confirmLabel: window.PortalI18n.t("删除"),
        tone: "danger",
      })) return;
      setBusy(button, true);
      try {
        await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/entry`, {
          operation: "delete",
          path,
        });
        writeQuery({ file: "", path: dirname(path) });
        state.file = null;
        await loadWorkspace({ path: dirname(path), file: "", tab: "code" });
        showToast(window.PortalI18n.t("文件已删除；可在 Changes 中检查并提交。"), "success");
      } catch (error) {
        showToast(error.message || error, "error");
        setBusy(button, false);
      }
    }

    async function submitCommit(form) {
      const data = new FormData(form);
      const button = form.querySelector('button[type="submit"]');
      setBusy(button, true, "Committing…");
      try {
        const result = await postJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/commit`,
          {
            message: String(data.get("message") || "").trim(),
            description: String(data.get("description") || "").trim(),
          },
        );
        state.overview = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace`);
        state.activeTab = "code";
        state.file = null;
        state.tree = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree`);
        invalidateRevisionViews();
        state.commitDraft = { message: "", description: "" };
        writeQuery({ tab: "code", file: "", path: "" }, { historyMode: "push" });
        renderWorkspace();
        showToast(window.PortalI18n.text`已创建 commit ${result.commit?.short_sha || ""}。`, "success");
      } catch (error) {
        showToast(error.message || error, "error");
        setBusy(button, false);
      }
    }

    async function saveRemote(form) {
      const data = new FormData(form);
      const button = form.querySelector('button[type="submit"]');
      setBusy(button, true, "保存中…");
      setSettingsBusy(form, true);
      try {
        const result = await repoInstruction("UpdateResource", {
          changes: {
            operation: "set_remote",
            name: String(data.get("name") || "").trim(),
            url: String(data.get("url") || "").trim(),
            push_url: String(data.get("push_url") || "").trim(),
          },
        });
        await refreshOverview();
        showSettingsSaved(result, window.PortalI18n.t("Remote 已保存。"));
      } catch (error) {
        showToast(error.message || error, "error");
        setSettingsBusy(form, false);
        setBusy(button, false);
      }
    }

    async function savePushPolicy(form) {
      const button = form.querySelector('button[type="submit"]');
      const enabled = form.elements.push_enabled.checked;
      setBusy(button, true, "保存中…");
      setSettingsBusy(form, true);
      try {
        const result = await repoInstruction("UpdateResource", {
          changes: {
            operation: "set_policy",
            push_enabled: enabled,
          },
        });
        await refreshOverview();
        showSettingsSaved(result, enabled ? window.PortalI18n.t("已允许 Portal Push。") : window.PortalI18n.t("已禁用此 Repo 的 Push。"));
      } catch (error) {
        showToast(error.message || error, "error");
        setSettingsBusy(form, false);
        setBusy(button, false);
      }
    }

    async function saveSshKey(form) {
      const data = new FormData(form);
      const button = form.querySelector('button[type="submit"]');
      const sshKey = String(data.get("ssh_key") || "").trim();
      setBusy(button, true, "保存中…");
      setSettingsBusy(form, true);
      try {
        const result = await repoInstruction("UpdateResource", {
          changes: {
            operation: "set_ssh_key",
            ssh_key: sshKey,
          },
        });
        await refreshOverview();
        showSettingsSaved(result, sshKey ? window.PortalI18n.text`已为此 Repo 使用 ${sshKey}。` : window.PortalI18n.t("已恢复 SSH 默认密钥。"));
      } catch (error) {
        showToast(error.message || error, "error");
        setSettingsBusy(form, false);
        setBusy(button, false);
      }
    }

    async function deleteGitCredential(credentialId, button) {
      if (!credentialId) return;
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.t("删除 Git 凭据？"),
        message: window.PortalI18n.t("后续 Remote 再次要求认证时，Portal 会重新提示。"),
        confirmLabel: window.PortalI18n.t("删除"),
        tone: "danger",
      })) return;
      setBusy(button, true, "删除中…");
      try {
        await postJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/credentials`,
          {
            operation: "delete",
            credential_id: credentialId,
          },
        );
        state.credentials = await getJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/credentials`,
        );
        renderWorkspace();
        showToast(window.PortalI18n.t("Git 凭据已删除。"), "success");
      } catch (error) {
        showToast(error.message || error, "error");
        setBusy(button, false);
      }
    }

    async function unregisterRepo() {
      const resourceId = state.selectedId;
      const name = state.overview.name || state.selectedId;
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.t("解除 Repo 注册？"),
        message: window.PortalI18n.text`解除「${name}」的 Repo 注册？\nGit checkout、分支、提交和文件不会被删除。`,
        confirmLabel: window.PortalI18n.t("解除注册"),
        tone: "danger",
      })) return;
      try {
        const result = await repoInstruction("UnregisterResource", { resource_id: resourceId });
        detailResourceGuest.deleted({ resourceId, kind: "unregistered" });
        if (state.selectedId !== resourceId) return;
        writeQuery({ item: "", tab: "code", path: "", file: "" });
        state.selectedId = "";
        state.mode = "board";
        state.overview = null;
        await loadRepos({ keepWorkspace: false });
        const pendingIndex = result.index_projection?.status === "unconfirmed";
        showToast(window.PortalI18n.t(pendingIndex
          ? "Repo 注册已解除；checkout 保持不变，索引通知尚未完成。"
          : "Repo 注册已解除；checkout 保持不变。"), pendingIndex ? "warning" : "success");
      } catch (error) {
        showToast(error.message || error, "error");
      }
    }

    return Object.freeze({
      deleteFile, deleteGitCredential, discardRepoChanges, savePushPolicy, saveRemote, saveSshKey,
      submitCommit, switchBranch, syncRepo, unregisterRepo,
    });
  }

  window.PortalRepoWorkspaceGitActions = Object.freeze({ createGitActions, dependencies: DEPENDENCIES });
})();
