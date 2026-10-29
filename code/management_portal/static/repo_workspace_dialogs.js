(() => {
  "use strict";

  if (window.PortalRepoWorkspaceDialogs) return;

  // The page's forms in dialogs: registering a Repo, the file editor with its
  // preview, creating a branch, and the Git credential prompt that a sync or
  // automatic commit waits on before it retries.
  const DEPENDENCIES = Object.freeze([
    "detailHistory", "dialogs", "dirname", "elements", "esc", "getJson", "invalidateRevisionViews",
    "isMarkdown", "loadRepos", "loadWorkspace", "markdown", "postJson", "queryState", "renderWorkspace",
    "setBusy", "showToast", "state", "workspaceCapability", "writeQuery",
  ]);

  function createDialogs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceDialogs is missing: ${missing.join(", ")}`);
    const {
      detailHistory, dialogs, dirname, elements, esc, getJson, invalidateRevisionViews, isMarkdown, loadRepos,
      loadWorkspace, markdown, postJson, queryState, renderWorkspace, setBusy, showToast, state,
      workspaceCapability, writeQuery,
    } = dependencies;

    function openRegisterModal() {
      syncRegisterPlacement();
      dialogs.register.open();
    }

    // Public content has no Projects; a Repo under Projects needs at least one.
    function syncRegisterPlacement() {
      const form = elements.registerForm;
      const projects = form.querySelector('[name="project_ids"]');
      const publicContent = form.querySelector('[name="placement"]').value === "public";
      projects.disabled = publicContent;
      projects.required = !publicContent;
    }

    function closeRegisterModal() {
      dialogs.register.close();
    }

    async function registerRepo(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const path = String(data.get("path") || "").trim();
      // A Repo names its place: Projects, or public content written as ["@public"].
      const projects = String(data.get("project_ids") || "").split(",").map(value => value.trim()).filter(Boolean);
      const publicContent = data.get("placement") === "public";
      if (!publicContent && !projects.length) {
        showToast(window.PortalI18n.t("填写 Project，或把位置选为公共内容。"), "error");
        return;
      }
      const payload = {
        resource_type: "repo",
        path,
        project_ids: publicContent ? ["@public"] : projects,
      };
      const submit = form.querySelector('button[type="submit"]');
      setBusy(submit, true, "注册中…");
      try {
        const result = await window.PortalResourceRegistrationService.register(payload, {
          confirmInitialize: error => window.PortalUI.confirm({
              title: window.PortalI18n.t("初始化 Git 仓库？"),
              message: window.PortalI18n.text`这个目录还不是 Git 仓库：\n${error.path || path}\n\n是否先创建 .git，再注册为 Repo？`,
              confirmLabel: window.PortalI18n.t("初始化并注册"),
            }),
        });
        if (result?.cancelled) return;
        const repoId = String(result.resource_id || "");
        form.reset();
        syncRegisterPlacement();
        closeRegisterModal();
        detailHistory.open(repoId, {
          q: "",
          project: "",
          tab: "",
          path: "",
          file: "",
        });
        elements.query.value = "";
        state.selectedId = repoId;
        state.mode = "workspace";
        await loadRepos({ keepWorkspace: false });
        showToast(window.PortalI18n.t("Repo 已注册并打开。"), "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        setBusy(submit, false);
      }
    }

    function setEditorMode(mode) {
      state.editorMode = mode === "preview" ? "preview" : "edit";
      elements.editorTabs.forEach(tab => {
        const active = tab.dataset.editorMode === state.editorMode;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      elements.editorSource.hidden = state.editorMode !== "edit";
      elements.editorPreview.hidden = state.editorMode !== "preview";
      if (state.editorMode === "preview") {
        const path = elements.editorPath.value;
        elements.editorPreview.innerHTML = isMarkdown(path)
          ? markdown(elements.editorSource.value, path)
          : window.PortalI18n.html`<pre>${esc(elements.editorSource.value)}</pre>`;
      }
    }

    function openEditor(file = null) {
      if (!workspaceCapability("write_files")) return;
      const basePath = state.tree?.path || queryState().path || "";
      state.editorOriginalPath = String(file?.path || "");
      state.editorOriginalContent = String(file?.content || "");
      state.editorExpectedHash = String(file?.hash || "");
      state.editorNew = !file;
      elements.editorEyebrow.textContent = file ? window.PortalI18n.t("编辑文件") : window.PortalI18n.t("新建文件");
      elements.editorTitle.textContent = file?.name || window.PortalI18n.t("New file");
      elements.editorPath.value = file?.path || (basePath ? `${basePath}/` : "");
      elements.editorSource.value = file?.content || "";
      elements.editorStatus.textContent = window.PortalI18n.t("保存后进入 Changes，可与其他改动一起提交。");
      setEditorMode("edit");
      dialogs.editor.open({ focusTarget: file ? elements.editorSource : elements.editorPath });
      if (!file) {
        window.setTimeout(() => {
          elements.editorPath.setSelectionRange(elements.editorPath.value.length, elements.editorPath.value.length);
        }, 0);
      }
    }

    function closeEditor(options = {}) {
      return dialogs.editor.close(options);
    }

    async function saveEditor() {
      const path = elements.editorPath.value.trim();
      const content = elements.editorSource.value;
      if (!path) {
        elements.editorStatus.textContent = window.PortalI18n.t("请输入仓库内的文件路径。");
        elements.editorPath.focus();
        return;
      }
      setBusy(elements.editorSave, true, "保存中…");
      try {
        if (state.editorNew) {
          await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/entry`, {
            operation: "create",
            kind: "file",
            path,
            content,
          });
        } else {
          if (path !== state.editorOriginalPath) {
            await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/move`, {
              path: state.editorOriginalPath,
              destination: path,
            });
          }
          await postJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/file`, {
            path,
            content,
            expected_hash: state.editorExpectedHash,
          });
        }
        closeEditor({ force: true });
        writeQuery({ file: path, path: dirname(path), tab: "code" });
        await loadWorkspace({ file: path, tab: "code" });
        showToast(window.PortalI18n.t("文件已保存到工作区；可在 Changes 中统一提交。"), "success");
      } catch (error) {
        elements.editorStatus.textContent = error.message || String(error);
        showToast(error.message || error, "error");
      } finally {
        setBusy(elements.editorSave, false);
      }
    }

    function openBranchDialog() {
      elements.branchName.value = "";
      elements.branchStart.value = "HEAD";
      dialogs.branch.open();
    }

    function closeBranchDialog() {
      dialogs.branch.close();
    }

    async function createBranch(event) {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const submit = event.currentTarget.querySelector('button[type="submit"]');
      setBusy(submit, true, "创建中…");
      try {
        state.overview = await postJson(
          `/api/repos/${encodeURIComponent(state.selectedId)}/workspace/branch`,
          {
            operation: "create",
            name: String(data.get("name") || "").trim(),
            start_point: String(data.get("start_point") || "HEAD").trim(),
          },
        );
        closeBranchDialog();
        invalidateRevisionViews();
        state.activeTab = "code";
        writeQuery({ tab: "code", file: "", path: "" }, { historyMode: "push" });
        state.file = null;
        state.tree = await getJson(`/api/repos/${encodeURIComponent(state.selectedId)}/workspace/tree`);
        renderWorkspace();
        showToast(window.PortalI18n.text`已切换到 ${state.overview.current_branch}。`, "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        setBusy(submit, false);
      }
    }

    function renderCredentialChallenge(challenge, errorMessage = "") {
      const value = challenge && typeof challenge === "object" ? challenge : {};
      state.credentialChallenge = value;
      const protocol = String(value.protocol || "https").toUpperCase();
      const host = String(value.host || "Git remote");
      const path = String(value.path || "");
      const operation = String(value.operation || "push");
      const isSsh = String(value.protocol || "") === "ssh";
      elements.credentialTitle.textContent = window.PortalI18n.text`${host} 需要认证`;
      elements.credentialHost.textContent = host;
      elements.credentialPath.textContent = `${value.remote || "remote"}${path ? ` · ${path}` : ""}`;
      elements.credentialProtocol.textContent = protocol;
      elements.credentialUsernameRow.hidden = isSsh;
      elements.credentialUsername.required = !isSsh;
      elements.credentialUsername.value = String(value.username || (isSsh ? "git" : ""));
      elements.credentialSecretLabel.textContent = String(value.secret_label || window.PortalI18n.t("访问令牌或密码"));
      elements.credentialSecret.placeholder = value.provider === "github"
        ? "github_pat_…"
        : isSsh
          ? window.PortalI18n.t("输入私钥口令或服务器密码")
          : window.PortalI18n.t("输入访问令牌或密码");
      elements.credentialHelp.textContent = String(value.help_text || "");
      elements.credentialRemember.checked = value.remember_default !== false;
      elements.credentialReason.textContent = value.rejected
        ? window.PortalI18n.t("刚才的凭据未被远程接受。请检查凭据是否已更新、过期或被撤销。")
        : operation === "push"
          ? window.PortalI18n.t("本地 commit 已保留。认证成功后，Portal 只会重试 Push。")
          : window.PortalI18n.text`Remote 要求认证后才能继续 ${operation.toUpperCase()}。`;
      elements.credentialReason.hidden = false;
      elements.credentialError.textContent = String(errorMessage || "");
      elements.credentialError.hidden = !errorMessage;
    }

    function closeCredentialDialog(result = null) {
      dialogs.credential.close();
      elements.credentialSecret.value = "";
      elements.credentialError.textContent = "";
      elements.credentialError.hidden = true;
      const resolve = state.credentialResolver;
      state.credentialResolver = null;
      state.credentialRejecter = null;
      state.credentialSubmitter = null;
      state.credentialChallenge = null;
      if (resolve) resolve(result);
    }

    function failCredentialDialog(error) {
      dialogs.credential.close();
      elements.credentialSecret.value = "";
      const reject = state.credentialRejecter;
      state.credentialResolver = null;
      state.credentialRejecter = null;
      state.credentialSubmitter = null;
      state.credentialChallenge = null;
      if (reject) reject(error);
    }

    function requestGitCredential(challenge, submitter) {
      if (state.credentialResolver) closeCredentialDialog(null);
      renderCredentialChallenge(challenge);
      state.credentialSubmitter = submitter;
      elements.credentialSecret.value = "";
      const target = elements.credentialUsernameRow.hidden
        || elements.credentialUsername.value
        ? elements.credentialSecret
        : elements.credentialUsername;
      dialogs.credential.open({ focusTarget: target });
      return new Promise((resolve, reject) => {
        state.credentialResolver = resolve;
        state.credentialRejecter = reject;
      });
    }

    async function submitGitCredential(event) {
      event.preventDefault();
      const submitter = state.credentialSubmitter;
      if (typeof submitter !== "function") {
        closeCredentialDialog(null);
        return;
      }
      const secret = elements.credentialSecret.value;
      const username = elements.credentialUsername.value.trim();
      if (!secret || (!elements.credentialUsernameRow.hidden && !username)) return;
      setBusy(elements.credentialSubmit, true, "正在验证…");
      elements.credentialError.hidden = true;
      try {
        const result = await submitter({
          username,
          secret,
          remember_credential: elements.credentialRemember.checked,
        });
        state.credentials = null;
        closeCredentialDialog(result);
      } catch (error) {
        const authChallenge = error?.data?.auth_challenge;
        if (authChallenge) {
          renderCredentialChallenge(
            authChallenge,
            error.message || String(error),
          );
          elements.credentialSecret.value = "";
          elements.credentialSecret.focus();
        } else {
          failCredentialDialog(error);
        }
      } finally {
        setBusy(elements.credentialSubmit, false);
      }
    }

    async function retryGitAuthentication(challenge) {
      const value = challenge && typeof challenge === "object" ? challenge : {};
      if (!value.repo_id || !value.remote || !value.operation) {
        throw new Error("Git 认证挑战缺少 remote 或操作信息。");
      }
      return requestGitCredential(value, credentials => postJson(
        `/api/repos/${encodeURIComponent(value.repo_id)}/workspace/sync`,
        {
          operation: value.operation,
          remote: value.remote,
          branch: value.branch || "",
          credential: {
            username: credentials.username,
            secret: credentials.secret,
          },
          remember_credential: credentials.remember_credential,
        },
      ));
    }

    return Object.freeze({
      closeBranchDialog, closeCredentialDialog, closeEditor, closeRegisterModal, createBranch,
      openBranchDialog, openEditor, openRegisterModal, registerRepo, retryGitAuthentication, saveEditor,
      setEditorMode, submitGitCredential, syncRegisterPlacement,
    });
  }

  window.PortalRepoWorkspaceDialogs = Object.freeze({ createDialogs, dependencies: DEPENDENCIES });
})();
