(() => {
  "use strict";

  if (window.PortalArticleWorkspaceCatalog) return;

  // The registered Articles: loading them, the Project menu and library
  // selectors, the header search with semantic results, the Project identity,
  // the remembered selection, and selecting an Article with everything it
  // loads.
  const DEPENDENCIES = Object.freeze([
    "$", "activeDocument", "articleProjects", "articleRoot", "articleStateHtml", "cancelPdf", "clearEditor",
    "detailHistory", "detailResourceGuest", "documentIsDirty", "esc", "hasDirtyDocuments", "loadArticleChat",
    "loadArticleGit", "loadBuild", "loadFiles", "loadSemanticIndex", "loadSettings", "openArticleFile",
    "queryState", "renderBuildSummary", "renderCompileMenu", "renderFileTree", "renderPdf",
    "renderSemanticNavigation", "renderSettings", "request", "resetArticleGit", "scheduleArticleGitRefresh",
    "selectedArticle", "setArticleResourceSearchOpen", "setOutputView", "setProjectMenu", "setTexlabStatus",
    "showPdfError", "showToast", "state", "updateSemanticUrl", "updateUrl",
  ]);

  function createCatalog(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceCatalog is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, articleProjects, articleRoot, articleStateHtml, cancelPdf, clearEditor,
      detailHistory, detailResourceGuest, documentIsDirty, esc, hasDirtyDocuments, loadArticleChat,
      loadArticleGit, loadBuild, loadFiles, loadSemanticIndex, loadSettings, openArticleFile, queryState,
      renderBuildSummary, renderCompileMenu, renderFileTree, renderPdf, renderSemanticNavigation,
      renderSettings, request, resetArticleGit, scheduleArticleGitRefresh, selectedArticle,
      setArticleResourceSearchOpen, setOutputView, setProjectMenu, setTexlabStatus, showPdfError, showToast,
      state, updateSemanticUrl, updateUrl,
    } = dependencies;

    async function loadArticleNavigationPreference() {
      state.lastSelectedId = "";
      try {
        const preferences = await window.PortalIdentity?.loadPreferences?.({ maxAgeMs: 0 });
        const serverValue = String(preferences?.resource_workspaces?.article?.last_item_id || "").trim();
        if (/^article_[0-9a-f]{12}$/.test(serverValue)) state.lastSelectedId = serverValue;
      } catch (_error) {}
      return state.lastSelectedId;
    }

    function flushArticleNavigationPreference({ keepalive = false } = {}) {
      window.clearTimeout(state.preferenceSaveTimer);
      state.preferenceSaveTimer = 0;
      window.PortalIdentity.savePreferences({
        resource_workspaces: {
          article: { last_item_id: state.lastSelectedId },
        },
      }, {
        immediate: true,
        keepalive,
      }).catch(() => {});
    }

    function persistArticleSelection(articleId) {
      const id = /^article_[0-9a-f]{12}$/.test(String(articleId || ""))
        ? String(articleId)
        : "";
      state.lastSelectedId = id;
      window.clearTimeout(state.preferenceSaveTimer);
      state.preferenceSaveTimer = window.setTimeout(flushArticleNavigationPreference, 120);
    }

    function filteredArticles(input) {
      const needle = String(input?.value || "").trim().toLowerCase();
      if (!needle) return state.articles;
      return state.articles.filter(article => [
        article.title,
        article.id,
        article.canonical_root,
        ...articleProjects(article),
      ].join(" ").toLowerCase().includes(needle));
    }

    function articleResourceResultHtml(article, semanticResult = null) {
      const projects = articleProjects(article).join(" · ") || "No Project";
      return window.PortalI18n.html`<button class="articleResourceResult ${article.id === state.selectedId ? "active" : ""}" type="button" data-article-header-select="${esc(article.id)}" data-portal-resource-id="${esc(article.id)}" aria-label="${esc(`${article.title || article.id}，Article ID ${article.id}`)}">
      <span>
        <strong title="${esc(article.title || article.id)}">${esc(article.title || article.id)}</strong>
        <small>${esc(projects)}</small>
        ${semanticResult?.reason ? window.PortalI18n.html`<em>${esc(semanticResult.reason)}</em>` : ""}
      </span>
      <code title="${esc(`Article ID：${article.id}`)}">${semanticResult ? `${Number(semanticResult.score || 0)}% · ${esc(article.id.replace("article_", ""))}` : esc(article.id.replace("article_", ""))}</code>
    </button>`;
    }

    function renderArticleResourceSearch() {
      const host = $("articleResourceSearchResults");
      const activeSemanticId = state.semanticSearch?.activeId() || "";
      if (activeSemanticId) {
        const articles = state.semanticSearch.filterAndSort(
          state.articles,
          article => article.id,
        );
        host.innerHTML = window.PortalI18n.html`
        <div class="articleResourceSearchHeading"><span>语义结果</span><span>${articles.length}</span></div>
        ${articles.length
            ? articles.map(article => articleResourceResultHtml(
                article,
                state.semanticSearch.resultFor(article.id),
              )).join("")
            : window.PortalI18n.t('<div class="articleResourceSearchEmpty">当前语义检索没有匹配的 Article。</div>')}`;
        return;
      }
      const articles = filteredArticles($("articleResourceQuery"));
      host.innerHTML = window.PortalI18n.html`
      <div class="articleResourceSearchHeading"><span>Article 项目</span><span>${articles.length}</span></div>
      ${articles.length
          ? articles.map(article => articleResourceResultHtml(article)).join("")
          : window.PortalI18n.t('<div class="articleResourceSearchEmpty">没有匹配的 Article。</div>')}`;
    }

    function initializeArticleSemanticSearch() {
      state.semanticSearch = window.PortalSemanticSearch?.create({
        input: $("articleResourceQuery"),
        button: $("articleSemanticButton"),
        panel: $("articleSemanticPanel"),
        identity: $("articleSemanticIdentity"),
        identityOptions: {
          titleId: "articleSemanticSearchTitle",
          idId: "articleSemanticSearchId",
        },
        projectionScope: "article",
        client: {
          get: url => request(url),
          post: (url, payload) => request(url, {
            method: "POST",
            body: JSON.stringify(payload),
          }),
        },
        initialActiveId: String(queryState().get("semantic") || ""),
        urls: {
          list: "/api/resource-semantic-searches?resource_scope=article",
          start: "/api/resource-semantic-search/start",
          refresh: "/api/resource-semantic-search/refresh",
          delete: "/api/resource-semantic-search/delete",
        },
        startPayload: () => ({ resource_scope: "article" }),
        actionPayload: () => ({ resource_scope: "article" }),
        onStart: () => {
          $("articleResourceQuery").value = "";
          updateSemanticUrl("");
          setArticleResourceSearchOpen(true);
        },
        onActiveChange: searchId => {
          if (searchId) $("articleResourceQuery").value = "";
          updateSemanticUrl(searchId || "");
          renderArticleResourceSearch();
          if (searchId) setArticleResourceSearchOpen(true);
        },
        onJobsChange: () => {
          if (state.resourceSearchOpen) renderArticleResourceSearch();
        },
        onError: (_error, message) => showToast(message, "error"),
      });
    }

    function articleSelectorHtml(article) {
      const projects = articleProjects(article);
      const projectLabel = projects.join(" · ") || window.PortalI18n.t("No Project");
      return window.PortalI18n.html`<button class="articleMenuItem ${article.id === state.selectedId ? "active" : ""}" type="button" data-article-select="${esc(article.id)}" data-portal-resource-id="${esc(article.id)}" aria-label="${esc(`${article.title || article.id}，Article ID ${article.id}`)}">
      <span><strong title="${esc(article.title || article.id)}">${esc(article.title || article.id)}</strong><small title="${esc(`Article ID：${article.id}`)}">${esc(article.id)}</small></span>
      <span class="articleMenuProjects" title="${esc(window.PortalI18n.text`所属项目：${projectLabel}`)}">${esc(projectLabel)}</span>
    </button>`;
    }

    function renderArticleSelectors() {
      const menuArticles = filteredArticles($("articleMenuSearch"));
      $("articleMenuList").innerHTML = menuArticles.length
        ? menuArticles.map(articleSelectorHtml).join("")
        : articleStateHtml("没有匹配的 Article。", { density: "inline", icon: false });

      const libraryArticles = filteredArticles($("articleLibrarySearch"));
      $("articleLibraryMeta").textContent = `${libraryArticles.length} / ${state.articles.length} registered`;
      $("articleLibraryList").innerHTML = libraryArticles.length
        ? libraryArticles.map(article => window.PortalI18n.html`
        <button class="articleLibraryCard ${article.id === state.selectedId ? "active" : ""}" type="button" data-article-select="${esc(article.id)}" data-portal-resource-id="${esc(article.id)}" aria-label="${esc(`${article.title || article.id}，Article ID ${article.id}`)}">
          <strong title="${esc(article.title || article.id)}">${esc(article.title || article.id)}</strong>
          <span>${esc(articleProjects(article).join(" · ") || window.PortalI18n.t("No Project"))}</span>
          <code title="${esc(article.canonical_root || "")}">${esc(article.id)}</code>
        </button>`).join("")
        : articleStateHtml("没有匹配的 Article。");
      $("articleUnregisterCurrent").disabled = !state.selectedId;
      if (state.resourceSearchOpen) renderArticleResourceSearch();
    }

    function renderProjectIdentity() {
      const article = selectedArticle();
      window.PortalResourceHeader?.mount?.("articleProjectIdentity", {
        title: article?.title || window.PortalI18n.t("Select an Article project"),
        type: article ? "article" : "",
        resource_id: article?.id || "",
        copy_label: "article_id",
      }, {
        compact: true,
        headingTag: "strong",
        titleId: "articleProjectTitle",
        idId: "articleProjectId",
      });
      const projectButton = $("articleProjectButton");
      projectButton.setAttribute("aria-label", article
        ? window.PortalI18n.text`切换 Article。当前：${article.title || article.id}，Article ID ${article.id}`
        : window.PortalI18n.t("选择 Article"));
      projectButton.title = article
        ? `${article.title || article.id} · ${article.id}`
        : window.PortalI18n.t("选择 Article");
      const pdfIdentity = $("articlePdfIdentity");
      pdfIdentity.textContent = article?.id || window.PortalI18n.t("未选择 Article");
      pdfIdentity.title = article ? window.PortalI18n.text`当前 Article Resource ID：${article.id}` : window.PortalI18n.t("当前未选择 Article");
      $("articleCompile").disabled = !article || state.compilingArticles.has(state.selectedId);
      $("articleSaveFile").disabled = !state.openFile || !documentIsDirty(activeDocument());
      window.PortalSite.setTitle(() => article
        ? `${article.title} — ${window.PortalI18n.t("Article")}`
        : window.PortalI18n.t("Article Workspace"));
      renderArticleSelectors();
      renderCompileMenu();
    }

    function clearSelectedArticle() {
      cancelPdf();
      window.clearTimeout(state.texlabTimer);
      state.texlabTimer = 0;
      state.texlabVersion += 1;
      state.texlabDiagnostics = [];
      setTexlabStatus(state.monacoReady ? "starting" : "warning");
      window.clearTimeout(state.semanticTimer);
      state.semanticTimer = 0;
      state.semanticIndex = null;
      state.activeDiagnostic = null;
      state.pdfSyncLocation = null;
      state.chatLoadRequest = null;


      state.chatPanel?.closeSurface?.();
      state.chatPanel?.showEmpty?.({
        emptyText: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
        placeholder: window.PortalI18n.t("Select an Article project to start a Chat Workspace."),
      });
      window.clearTimeout(state.autoCompileTimer);
      state.autoCompileTimer = 0;
      state.autoCompileQueued = false;
      state.files = [];
      state.filesByPath.clear();
      state.linkedFiles = new Map();
      state.linkedFileProbes = new Set();
      state.settings = null;
      state.build = null;
      state.selectedDirectory = "";
      clearEditor();
      renderFileTree();
      renderSettings();
      renderBuildSummary();
      renderSemanticNavigation();

      setOutputView("pdf");
    }

    async function selectArticle(articleId, { initial = false, updateHistory = true } = {}) {
      const id = String(articleId || "");
      if (id === state.selectedId && !initial) {
        setProjectMenu(false);
        return true;
      }
      if (hasDirtyDocuments() && !await window.PortalUI.confirm({
        title: window.PortalI18n.t("切换 Article？"),
        message: window.PortalI18n.t("当前打开的文件中有未保存修改。切换 Article 会放弃这些修改。"),
        confirmLabel: window.PortalI18n.t("切换并放弃修改"),
        tone: "danger",
      })) return false;
      if (updateHistory) {
        if (initial) updateUrl(id);
        else detailHistory.open(id);
      }
      state.selectedId = id;
      state.buildPreflight = null;
      state.stopRequested = false;
      state.autoCompileQueued = false;
      window.clearTimeout(state.autoCompileTimer);
      $("articleCompile").textContent = state.compilingArticles.has(id) ? window.PortalI18n.t("编译中…") : window.PortalI18n.t("重新编译");
      state.buildLoading = Boolean(id);
      resetArticleGit();
      persistArticleSelection(id);
      setProjectMenu(false);
      clearSelectedArticle();
      renderProjectIdentity();
      if (!id) return true;
      try {
        // Only files/settings determine which document can be opened.
        loadBuild().catch(error => {
          if (id === state.selectedId) showToast(window.PortalI18n.text`构建状态：${error.message || error}`, "warning");
        }).finally(() => {
          if (id !== state.selectedId) return;
          state.buildLoading = false;
          if (state.outputView === "pdf") return renderPdf().catch(showPdfError);
        });
        loadSemanticIndex();
        loadArticleGit({ includeHistory: state.sideView === "git", silent: true })
          .then(() => { if (id === state.selectedId) scheduleArticleGitRefresh(); })
          .catch(error => { if (id === state.selectedId) showToast(window.PortalI18n.text`Git 状态：${error.message || error}`, "warning"); });
        await Promise.all([loadFiles(), loadSettings()]);
        if (id !== state.selectedId) return false;
        const primary = state.settings?.primary_file || articleRoot()?.primary_file || selectedArticle()?.primary_file;
        if (primary) await openArticleFile(primary);
        if (state.sideView === "chat") loadArticleChat().catch(error => showToast(error.message || error, "warning"));
        detailResourceGuest.setResource({ resourceId: id });
        detailResourceGuest.ready();
      } catch (error) {
        detailResourceGuest.failed({ resourceId: id, error: error.message || String(error) });
        showToast(error.message || error, "error");
      }
      return true;
    }

    async function loadArticles({ preserve = true, reloadSelected = true } = {}) {
      const payload = await request("/api/articles");
      state.articles = Array.isArray(payload.articles) ? payload.articles : [];
      const requested = String(queryState().get("item") || "");
      if (requested && !state.articles.some(article => article.id === requested)) {
        try {
          const resolution = await request(`/api/resources/resolve?address=${encodeURIComponent(requested)}`);
          if (resolution.target?.resource_type !== "article" || resolution.target.root_id !== requested) {
            throw new Error(window.PortalI18n.t("这个地址不是可打开的 Article。"));
          }
          state.articles.push({ ...resolution.resource, id: requested });
        } catch (error) {
          detailResourceGuest.failed({ resourceId: requested, error: error.message || String(error) });
          $("articleEditorState").textContent = window.PortalI18n.text`无法打开 Article：${error.message || error}`;
          renderArticleSelectors();
          throw error;
        }
      }
      const next = requested
        || (preserve && state.articles.some(article => article.id === state.selectedId)
          ? state.selectedId
          : state.articles.some(article => article.id === state.lastSelectedId)
            ? state.lastSelectedId
          : state.articles[0]?.id || "");
      renderArticleSelectors();
      if (reloadSelected) await selectArticle(next, { initial: true });
      else renderProjectIdentity();
    }

    return Object.freeze({
      flushArticleNavigationPreference, initializeArticleSemanticSearch, loadArticleNavigationPreference,
      loadArticles, persistArticleSelection, renderArticleResourceSearch, renderArticleSelectors,
      selectArticle,
    });
  }

  window.PortalArticleWorkspaceCatalog = Object.freeze({ createCatalog, dependencies: DEPENDENCIES });
})();
