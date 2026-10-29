(() => {
  "use strict";

  if (window.PortalArticleWorkspaceRegistration) return;

  // Registering an Article directory after its preflight check, and
  // unregistering the current Article while its Git refresh is held.
  const DEPENDENCIES = Object.freeze([
    "$", "articleGitRefresh", "articleRegisterDialog", "detailHistory", "detailResourceGuest", "esc",
    "loadArticles", "persistArticleSelection", "request", "scheduleArticleGitRefresh", "selectedArticle",
    "setProjectMenu", "showToast", "state", "unregisteringArticles", "updateUrl",
  ]);

  function createRegistration(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceRegistration is missing: ${missing.join(", ")}`);
    const {
      $, articleGitRefresh, articleRegisterDialog, detailHistory, detailResourceGuest, esc, loadArticles,
      persistArticleSelection, request, scheduleArticleGitRefresh, selectedArticle, setProjectMenu, showToast,
      state, unregisteringArticles, updateUrl,
    } = dependencies;

    function openRegisterModal() {
      setProjectMenu(false);
      state.registerPreflight = null;
      state.registerPrimaryConfirmed = false;
      $("articleRegisterProjects").value = "";
      $("articleRegisterPreflight").dataset.status = "idle";
      $("articleRegisterPreflight").textContent = window.PortalI18n.t("输入目录后检查主文档、编译器与依赖。");
      $("articleRegisterPrimaryField").hidden = true;
      $("articleRegisterCompilerField").hidden = true;
      articleRegisterDialog.open();
      window.setTimeout(() => $("articleRegisterPath").focus(), 0);
    }

    function renderRegistrationPreflight(preflight) {
      const host = $("articleRegisterPreflight");
      host.dataset.status = preflight.status || "idle";
      const issues = Array.isArray(preflight.issues) ? preflight.issues : [];
      const heading = preflight.status === "blocked"
        ? window.PortalI18n.t("目录暂不可注册")
        : preflight.requires_selection && !state.registerPrimaryConfirmed
          ? window.PortalI18n.t("请选择主文档")
          : window.PortalI18n.t("构建配置已识别");
      host.innerHTML = window.PortalI18n.html`<strong>${heading}</strong><span>${esc(preflight.primary_file || window.PortalI18n.t("未识别主文档"))} · ${esc(preflight.compiler || window.PortalI18n.t("未识别编译器"))}</span>${issues.map(issue => window.PortalI18n.html`<span>${esc(issue.message || "")}</span>`).join("")}`;
    }

    async function inspectRegistration({ forSubmit = false } = {}) {
      const path = $("articleRegisterPath").value.trim();
      if (!path) throw new Error("请输入 Article 目录路径");
      const useSelection = state.registerPreflight?.path === path && state.registerPrimaryConfirmed;
      let preflight;
      try {
        preflight = await request("/api/articles/preflight", {
          method: "POST",
          body: JSON.stringify({
            path,
            primary_file: useSelection ? $("articleRegisterPrimary").value : "",
            compiler: useSelection ? $("articleRegisterCompiler").value : "",
          }),
        });
      } catch (error) {
        // A refused directory is a preflight result: show why inside the dialog.
        state.registerPreflight = null;
        $("articleRegisterProjects").value = "";
        renderRegistrationPreflight({ status: "blocked", issues: [{ message: error.message || String(error) }] });
        return false;
      }
      state.registerPreflight = preflight;
      // An Article belongs to the Project its directory is in; the preflight names it and registration sends exactly that.
      $("articleRegisterProjects").value = (preflight.project_ids || []).join(", ");
      const currentPrimary = $("articleRegisterPrimary").value;
      $("articleRegisterPrimary").innerHTML = (preflight.candidates || [])
        .filter(candidate => candidate.standalone)
        .map(candidate => window.PortalI18n.html`<option value="${esc(candidate.path)}" ${(currentPrimary || preflight.primary_file) === candidate.path ? "selected" : ""}>${esc(candidate.path)}${candidate.compiler_hint ? ` · ${esc(candidate.compiler_hint)}` : ""}</option>`)
        .join("");
      $("articleRegisterCompiler").innerHTML = ["pdflatex", "xelatex", "lualatex"]
        .map(name => window.PortalI18n.html`<option value="${name}" ${name === preflight.compiler ? "selected" : ""}>${name}</option>`)
        .join("");
      $("articleRegisterPrimaryField").hidden = !(preflight.candidates || []).some(candidate => candidate.standalone);
      $("articleRegisterCompilerField").hidden = false;
      renderRegistrationPreflight(preflight);
      if (preflight.status === "blocked") return false;
      if (forSubmit && preflight.requires_selection && !state.registerPrimaryConfirmed) {
        showToast(window.PortalI18n.t("检测到多个主文档，请确认要注册的主文档。"), "warning");
        return false;
      }
      return true;
    }

    async function registerArticle(event) {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      const submit = formElement.querySelector('button[type="submit"]');
      submit.disabled = true;
      try {
        if (!await inspectRegistration({ forSubmit: true })) return;
        const projectIds = (state.registerPreflight?.project_ids || []).filter(Boolean);
        if (!projectIds.length) throw new Error(window.PortalI18n.t("Article 目录必须位于已登记的 Project 内"));
        const result = await window.PortalResourceRegistrationService.register({
          resource_type: "article",
          path: form.get("path"),
          project_ids: projectIds,
          primary_file: $("articleRegisterPrimary").value,
          compiler: $("articleRegisterCompiler").value,
        });
        articleRegisterDialog.close();
        formElement.reset();
        state.selectedId = result.resource_id;
        detailHistory.open(state.selectedId);
        await loadArticles({ preserve: true });
        showToast(window.PortalI18n.t("Article 目录已注册。"), "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        submit.disabled = false;
      }
    }

    async function unregisterCurrentArticle() {
      const article = selectedArticle();
      if (!article || unregisteringArticles.has(article.id)) return;
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.t("解除 Article 注册？"),
        message: window.PortalI18n.text`解除「${article.title || article.id}」的 Article 注册？\n原始目录和文件不会被删除。`,
        confirmLabel: window.PortalI18n.t("解除注册"),
        tone: "danger",
      })) return;
      if (unregisteringArticles.has(article.id)) return;
      unregisteringArticles.add(article.id);
      if (state.selectedId === article.id) articleGitRefresh.stop();
      try {
        await window.PortalInstructionClient.execute("UnregisterResource", { resource_id: article.id }, {
          idempotencyKey: `article-unregister:${article.id}:${globalThis.PortalUIPrimitives.randomUUID()}`,
        });
        detailResourceGuest.deleted({ resourceId: article.id, kind: "unregistered" });
        const stillSelected = state.selectedId === article.id;
        if (stillSelected) {
          state.selectedId = "";
          persistArticleSelection("");
          updateUrl("");
        }
        await loadArticles({ preserve: !stillSelected, reloadSelected: stillSelected });
        showToast(window.PortalI18n.t("Article 注册已解除；原始目录和文件保持不变。"), "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        unregisteringArticles.delete(article.id);
        if (state.selectedId === article.id) scheduleArticleGitRefresh(0);
      }
    }

    return Object.freeze({
      inspectRegistration, openRegisterModal, registerArticle, unregisterCurrentArticle,
    });
  }

  window.PortalArticleWorkspaceRegistration = Object.freeze({ createRegistration, dependencies: DEPENDENCIES });
})();
