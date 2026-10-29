(() => {
  "use strict";

  if (window.PortalArticleWorkspaceBuild) return;

  // Building the Article: its settings and preflight, compile preferences,
  // compiling (also automatically after edits) and stopping, the build
  // summary, the log with its filters, and opening or asking about a
  // diagnostic. A compile result belongs to the Article that started it.
  const DEPENDENCIES = Object.freeze([
    "$", "addArticleChatReference", "articleStateHtml", "esc", "formatDate", "hasDirtyDocuments",
    "loadArticles", "loadFiles", "loadSemanticIndex", "normalizedDiagnosticPath", "openArticleFile",
    "renderEditorLayers", "renderEditorPreferenceControls", "renderPdf", "request", "saveAllDirtyDocuments",
    "setCompileMenu", "setSideView", "showPdfError", "showToast", "state", "updateAllEditorMarkers",
    "updateArticlePdfZoomControls",
  ]);

  function createBuild(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceBuild is missing: ${missing.join(", ")}`);
    const {
      $, addArticleChatReference, articleStateHtml, esc, formatDate, hasDirtyDocuments, loadArticles,
      loadFiles, loadSemanticIndex, normalizedDiagnosticPath, openArticleFile, renderEditorLayers,
      renderEditorPreferenceControls, renderPdf, request, saveAllDirtyDocuments, setCompileMenu, setSideView,
      showPdfError, showToast, state, updateAllEditorMarkers, updateArticlePdfZoomControls,
    } = dependencies;

    function compileSettingValue(key) {
      const defaults = {
        auto_compile: false,
        compile_mode: "normal",
        syntax_check: true,
        error_handling: "continue",
      };
      return state.settings && Object.hasOwn(state.settings, key)
        ? state.settings[key]
        : defaults[key];
    }

    function renderCompileMenu() {
      document.querySelectorAll("[data-compile-setting]").forEach(button => {
        const key = button.dataset.compileSetting;
        const raw = button.dataset.compileValue;
        const value = raw === "true" ? true : raw === "false" ? false : raw;
        const selected = compileSettingValue(key) === value;
        button.classList.toggle("selected", selected);
        button.setAttribute("aria-checked", selected ? "true" : "false");
      });
      $("articleStopCompile").disabled = !state.compilingArticles.has(state.selectedId) || state.stopRequested;
      $("articleRecompileScratch").disabled = !state.selectedId || state.buildPreflight?.status === "blocked" || state.compilingArticles.has(state.selectedId);
      $("articleCompileSettings").disabled = !state.selectedId;
    }

    async function loadSettings() {
      if (!state.selectedId) return;
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`);
      if (payload.id !== state.selectedId) return;
      state.settings = payload;
      renderSettings();
      if (!payload.synctex_available) {
        $("articleSyncToPdf").disabled = true;
        $("articleSyncToPdf").title = window.PortalI18n.t("缺少 synctex；安装 Article 工具链后可在源码与 PDF 间定位");
        $("articleSyncHint").textContent = window.PortalI18n.t("缺少 synctex：无法从 PDF 定位源码");
      } else {
        $("articleSyncToPdf").title = window.PortalI18n.t("在 PDF 中定位当前源码");
        $("articleSyncHint").textContent = window.PortalI18n.t("双击 PDF 返回源码");
      }
      const id = state.selectedId;
      request(`/api/articles/${encodeURIComponent(id)}/preflight`).then(preflight => {
        if (id !== state.selectedId) return;
        state.buildPreflight = preflight;
        renderBuildPreflight();
      }).catch(error => {
        if (id !== state.selectedId) return;
        state.buildPreflight = { status: "blocked", issues: [{ severity: "error", message: error.message || String(error) }] };
        renderBuildPreflight();
      });
    }

    function renderBuildPreflight() {
      const host = $("articleBuildPreflight");
      const preflight = state.buildPreflight;
      if (!preflight) {
        host.dataset.status = "idle";
        host.textContent = window.PortalI18n.t("尚未检查构建条件");
        return;
      }
      host.dataset.status = preflight.status || "idle";
      const issues = Array.isArray(preflight.issues) ? preflight.issues : [];
      const blocked = preflight.status === "blocked";
      $("articleCompile").disabled = !state.selectedId || blocked || state.compilingArticles.has(state.selectedId);
      $("articleRecompileScratch").disabled = !state.selectedId || blocked || state.compilingArticles.has(state.selectedId);
      const reason = blocked ? issues.filter(issue => issue.severity === "error").map(issue => issue.message).join("；") : "";
      $("articleCompile").title = reason;
      $("articleRecompileScratch").title = reason;
      if (!issues.length) {
        host.innerHTML = window.PortalI18n.html`<strong>构建预检通过</strong><span>${(preflight.dependencies || []).length} 项依赖可解析</span>`;
        return;
      }
      host.innerHTML = window.PortalI18n.html`<strong>${preflight.status === "blocked" ? window.PortalI18n.t("构建条件不完整") : window.PortalI18n.t("构建前提醒")}</strong>${issues.map(issue => window.PortalI18n.html`<span>${esc(issue.message || "")}</span>`).join("")}`;
    }

    function renderSettings() {
      const settings = state.settings;
      if (!settings) {
        $("articleCompiler").innerHTML = "";
        $("articlePrimaryFile").innerHTML = "";
        $("articleTexLive").textContent = "—";
        $("articleLatexmkPath").textContent = "—";
        $("articleEnvironmentProfile").textContent = "—";
        $("articleEnvironmentPolicy").textContent = "—";
        $("articleUseLatexmkrc").checked = false;
        $("articleShellEscape").checked = false;
        renderBuildPreflight();
        renderEditorPreferenceControls();
        renderCompileMenu();
        return;
      }
      $("articleCompiler").innerHTML = (settings.available_compilers || []).map(compiler => window.PortalI18n.html`
      <option value="${esc(compiler.id)}" ${compiler.id === settings.compiler ? "selected" : ""} ${compiler.available ? "" : "disabled"}>
        ${esc(compiler.label)}${compiler.available ? "" : " (unavailable)"}
      </option>`).join("");
      const configuredCandidates = Array.isArray(settings.primary_candidates)
        ? new Set(settings.primary_candidates.map(path => String(path || "")))
        : null;
      const texFiles = state.files.filter(entry => (
        entry.type === "file"
        && String(entry.path || "").toLowerCase().endsWith(".tex")
        && (!configuredCandidates || configuredCandidates.has(String(entry.path || "")))
      ));
      if (!texFiles.some(entry => entry.path === settings.primary_file)) {
        texFiles.unshift({ path: settings.primary_file, invalidPrimary: true });
      }
      $("articlePrimaryFile").innerHTML = texFiles.map(entry => window.PortalI18n.html`<option value="${esc(entry.path)}" ${entry.path === settings.primary_file ? "selected" : ""} ${entry.invalidPrimary ? "disabled" : ""}>${esc(entry.path)}${entry.invalidPrimary ? window.PortalI18n.t("（不是独立主文档）") : ""}</option>`).join("");
      $("articleTexLive").textContent = settings.tex_live_version || window.PortalI18n.t("TeX Live");
      $("articleLatexmkPath").textContent = settings.latexmk || "latexmk";
      $("articleEnvironmentProfile").textContent = settings.environment_profile || "—";
      $("articleEnvironmentPolicy").textContent = settings.environment_policy || "—";
      $("articleUseLatexmkrc").checked = Boolean(settings.use_project_latexmkrc);
      $("articleShellEscape").checked = Boolean(settings.shell_escape);
      renderBuildPreflight();
      renderEditorPreferenceControls();
      renderCompileMenu();
    }

    function buildCounts() {
      const fallback = { all: 0, error: 0, warning: 0, info: 0 };
      return { ...fallback, ...(state.build?.counts || {}) };
    }

    function renderBuildSummary() {
      const build = state.build || { status: "idle" };
      const counts = buildCounts();
      const status = build.status || "idle";
      const mode = build.compile_mode === "draft" ? window.PortalI18n.t("快速草稿 · 图片以空框显示") : "";
      const summary = status === "idle"
        ? window.PortalI18n.t("尚未编译")
        : status === "running"
          ? (build.compile_mode === "draft" ? window.PortalI18n.t("正在编译快速草稿…") : window.PortalI18n.t("编译中…"))
          : `${build.compiler_label || build.compiler || "LaTeX"}${mode ? ` · ${mode}` : ""} · ${formatDate(build.compiled_at)}${counts.all ? ` · ${counts.all} diagnostics` : ""}`;
      $("articleBuildSummary").textContent = summary;
      $("articleLogBadge").hidden = counts.all === 0;
      $("articleLogBadge").textContent = counts.all > 99 ? "99+" : String(counts.all);
      $("articlePdfDownload").hidden = !build.pdf_available;
      if (build.pdf_available) {
        $("articlePdfDownload").href = build.pdf_url || `/api/articles/${encodeURIComponent(state.selectedId)}/pdf`;
      }
      $("articleSyncHint").hidden = !build.pdf_available;
      $("articleSyncToPdf").disabled = !state.settings?.synctex_available || !build.pdf_available || !state.openFile.toLowerCase().endsWith(".tex");
      updateArticlePdfZoomControls();
      renderLog();
      updateAllEditorMarkers();
    }

    async function loadBuild() {
      if (!state.selectedId) return;
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/build`);
      if (payload.id !== state.selectedId) return;
      state.build = payload;
      renderBuildSummary();
    }

    function setOutputView(view) {
      state.outputView = view === "log" ? "log" : "pdf";
      $("articlePdfView").classList.toggle("active", state.outputView === "pdf");
      $("articleLogView").classList.toggle("active", state.outputView === "log");
      $("articleLogButton").classList.toggle("active", state.outputView === "log");
      $("articleCompileMenuWrap").hidden = state.outputView === "log";
      $("articleBackToPdf").hidden = state.outputView !== "log";
      updateArticlePdfZoomControls();
      if (state.outputView === "log") setCompileMenu(false);
      if (state.outputView === "pdf") renderPdf().catch(error => showPdfError(error));
      else renderLog();
    }

    function renderLogTabs() {
      const counts = buildCounts();
      const tabs = [
        ["all", "All logs"],
        ["error", "Errors"],
        ["warning", "Warnings"],
        ["info", "Info"],
      ];
      $("articleLogTabs").innerHTML = tabs.map(([id, label]) => window.PortalI18n.html`
      <button class="articleLogTab ${state.logFilter === id ? "active" : ""}" type="button" data-log-filter="${id}">
        ${label} <span>${counts[id] || 0}</span>
      </button>`).join("");
    }

    function renderLog() {
      renderLogTabs();
      const build = state.build || {};
      const diagnostics = (Array.isArray(build.diagnostics) ? build.diagnostics : [])
        .map((item, buildIndex) => ({ ...item, buildIndex }))
        .filter(item => state.logFilter === "all" || item.severity === state.logFilter);
      const body = diagnostics.length
        ? diagnostics.map(item => window.PortalI18n.html`
        <article class="articleDiagnostic ${esc(item.severity || "info")}">
          <button class="articleDiagnosticButton" type="button" data-build-diagnostic="${item.buildIndex}" ${item.file ? `data-diagnostic-file="${esc(item.file)}"` : ""} ${item.line ? `data-diagnostic-line="${Number(item.line)}"` : ""} data-portal-icon="chevron-right">
            <strong>${esc(item.message || window.PortalI18n.t("LaTeX diagnostic"))}</strong>
            <small>${esc(item.file || "")}${item.line ? `:${Number(item.line)}` : ""}</small>
          </button>
          ${item.file || item.line ? window.PortalI18n.html`<div class="articleDiagnosticMeta">${esc(item.file || state.settings?.primary_file || "")}${item.line ? ` · line ${Number(item.line)}` : ""}</div>` : ""}
          <div class="articleDiagnosticActions">
            <button type="button" data-open-build-diagnostic="${item.buildIndex}">在编辑器中打开</button>
            <button type="button" data-ask-build-diagnostic="${item.buildIndex}">询问 Agent</button>
          </div>
        </article>`).join("")
        : articleStateHtml(
          build.status === "success"
            ? "Compilation completed without diagnostics."
            : (build.status === "idle"
              ? "Compile the Article to view its LaTeX log."
              : `No ${state.logFilter === "all" ? "" : state.logFilter + " "}diagnostics.`),
        );
      const raw = build.log
        ? window.PortalI18n.html`<details class="articleRawLog"><summary>Raw LaTeX log</summary><pre>${esc(build.log)}</pre></details>`
        : "";
      $("articleDiagnostics").innerHTML = body + raw;
    }

    function buildDiagnostic(index) {
      const diagnostics = Array.isArray(state.build?.diagnostics) ? state.build.diagnostics : [];
      return diagnostics[Number(index)] || null;
    }

    async function openBuildDiagnostic(index) {
      const item = buildDiagnostic(index);
      if (!item) return;
      const path = normalizedDiagnosticPath(item.file || state.settings?.primary_file || "");
      state.activeDiagnostic = {
        severity: item.severity || "info",
        message: item.message || window.PortalI18n.t("LaTeX diagnostic"),
        path,
        line: Number(item.line || 1),
        source: "LaTeX build",
      };
      if (path && state.filesByPath.has(path)) {
        await openArticleFile(path, {
          line: Number(item.line || 1),
          column: Number(item.column || 1),
        });

      } else {
        showToast(window.PortalI18n.t("这个编译问题没有可定位的工程源文件。"), "warning");
      }
    }

    async function askBuildDiagnostic(index) {
      const item = buildDiagnostic(index);
      if (!item) return;
      const articleId = state.selectedId;
      await openBuildDiagnostic(index);
      if (articleId !== state.selectedId) return;
      const controller = await setSideView("chat");
      if (!controller || articleId !== state.selectedId) return;
      const path = normalizedDiagnosticPath(item.file || state.settings?.primary_file || "");

      const prompt = `请分析并修复这个 LaTeX 编译问题：\n${item.message || "LaTeX diagnostic"}\n位置：${path || "unknown"}:${Number(item.line || 1)}`;
      const existing = String(controller?.draftText?.() || "").trim();
      controller?.setDraftText?.(existing ? `${existing}\n\n${prompt}` : prompt);
      addArticleChatReference();

    }

    function scheduleAutoCompile(delay = 2500) {
      window.clearTimeout(state.autoCompileTimer);
      state.autoCompileTimer = 0;
      if (!compileSettingValue("auto_compile") || !state.selectedId || !state.openFile) return;
      state.autoCompileTimer = window.setTimeout(() => {
        state.autoCompileTimer = 0;
        if (state.compilingArticles.has(state.selectedId)) {
          state.autoCompileQueued = true;
          return;
        }
        compileArticle({ automatic: true });
      }, Math.max(150, Number(delay) || 2500));
    }

    async function updateCompilePreference(button) {
      if (!state.selectedId || button.disabled) return;
      const key = button.dataset.compileSetting;
      const raw = button.dataset.compileValue;
      const value = raw === "true" ? true : raw === "false" ? false : raw;
      if (compileSettingValue(key) === value) return;
      button.disabled = true;
      try {
        state.settings = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`, {
          method: "POST",
          body: JSON.stringify({ [key]: value }),
        });
        renderSettings();
        if (key === "syntax_check") renderEditorLayers();
        if (key === "auto_compile" && value) scheduleAutoCompile();
        if (key === "auto_compile" && !value) {
          window.clearTimeout(state.autoCompileTimer);
          state.autoCompileTimer = 0;
          state.autoCompileQueued = false;
        }
      } catch (error) {
        showToast(error.message || error, "error");
        renderCompileMenu();
      } finally {
        button.disabled = false;
      }
    }

    async function updateEditorCodeCheck(input) {
      const enabled = Boolean(input.checked);
      if (!state.selectedId) {
        renderEditorPreferenceControls();
        return;
      }
      input.disabled = true;
      try {
        state.settings = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`, {
          method: "POST",
          body: JSON.stringify({ syntax_check: enabled }),
        });
        renderSettings();
        renderEditorLayers();
      } catch (error) {
        showToast(error.message || error, "error");
        renderEditorPreferenceControls();
      } finally {
        input.disabled = false;
      }
    }

    async function stopCompilation() {
      if (!state.selectedId || !state.compilingArticles.has(state.selectedId) || state.stopRequested) return;
      const articleId = state.selectedId;
      state.stopRequested = true;
      renderCompileMenu();
      try {
        const result = await request(`/api/articles/${encodeURIComponent(articleId)}/stop`, {
          method: "POST",
          body: "{}",
        });
        if (articleId !== state.selectedId) return;
        if (result.stop_requested) showToast(window.PortalI18n.t("正在停止 LaTeX 编译…"), "warning");
        else {
          state.stopRequested = false;
          renderCompileMenu();
          showToast(window.PortalI18n.t("当前没有正在运行的编译。"), "warning");
        }
      } catch (error) {
        if (articleId !== state.selectedId) return;
        state.stopRequested = false;
        renderCompileMenu();
        showToast(error.message || error, "error");
      }
    }

    async function compileArticle({ fromScratch = false, automatic = false } = {}) {
      if (!state.selectedId) return;
      if (state.buildPreflight?.status === "blocked") {
        if (!automatic) showToast(window.PortalI18n.t("构建条件不完整，请查看编译设置中的预检提示。"), "warning");
        return;
      }
      const articleId = state.selectedId;
      if (state.compilingArticles.has(state.selectedId)) {
        if (automatic) state.autoCompileQueued = true;
        return;
      }
      setCompileMenu(false);
      if (hasDirtyDocuments() && !(await saveAllDirtyDocuments({ silent: true }))) return;
      if (articleId !== state.selectedId || state.compilingArticles.has(articleId)) return;
      state.compilingArticles.add(articleId);
      state.stopRequested = false;
      const compileButtons = [$("articleCompile")];
      compileButtons.forEach(button => {
        button.disabled = true;
        button.dataset.previousLabel = button.textContent;
        button.textContent = window.PortalI18n.t("编译中…");
      });
      state.build = {
        ...(state.build || {}),
        id: state.selectedId,
        status: "running",
        compile_mode: compileSettingValue("compile_mode"),
        summary: "正在编译 PDF…",
      };
      renderBuildSummary();
      renderCompileMenu();
      try {
        const result = await request(`/api/articles/${encodeURIComponent(articleId)}/compile`, {
          method: "POST",
          timeoutMs: 540000,
          body: JSON.stringify({ from_scratch: Boolean(fromScratch) }),
        });
        if (articleId !== state.selectedId) return;
        state.build = { ...result, pdf_available: true, pdf_url: result.pdf_url };
        renderBuildSummary();
        setOutputView("pdf");
        await loadArticles({ preserve: true, reloadSelected: false });
        if (articleId !== state.selectedId) return;
        if (!automatic) {
          const message = result.compile_mode === "draft"
            ? window.PortalI18n.t("快速草稿已生成；图片已替换为空框。")
            : (result.warnings ? window.PortalI18n.t("PDF 已生成；LaTeX 留下了需要检查的诊断。") : window.PortalI18n.t("PDF 编译完成。"));
          showToast(message, result.warnings ? "warning" : "success");
        }
      } catch (error) {
        if (articleId !== state.selectedId) return;
        try { await loadBuild(); } catch (_) {}
        if (articleId !== state.selectedId) return;
        setOutputView("log");
        showToast(state.stopRequested ? window.PortalI18n.t("LaTeX 编译已停止。") : (error.message || window.PortalI18n.t("LaTeX compilation failed")), state.stopRequested ? "warning" : "error");
      } finally {
        state.compilingArticles.delete(articleId);
        if (articleId !== state.selectedId) return;
        state.stopRequested = false;
        compileButtons.forEach(button => {
          button.disabled = !state.selectedId;
          button.textContent = button.dataset.previousLabel || window.PortalI18n.t("重新编译");
        });
        renderCompileMenu();
        if (state.autoCompileQueued) {
          state.autoCompileQueued = false;
          scheduleAutoCompile(250);
        }
      }
    }

    async function saveSettings(event) {
      event.preventDefault();
      if (!state.selectedId) return;
      const button = event.currentTarget.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        state.settings = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/settings`, {
          method: "POST",
          body: JSON.stringify({
            compiler: $("articleCompiler").value,
            primary_file: $("articlePrimaryFile").value,
            use_project_latexmkrc: $("articleUseLatexmkrc").checked,
            shell_escape: $("articleShellEscape").checked,
          }),
        });
        renderSettings();
        await loadFiles();
        await loadSemanticIndex();
        showToast(window.PortalI18n.t("LaTeX 编译配置已保存。"), "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        button.disabled = false;
      }
    }

    return Object.freeze({
      askBuildDiagnostic, compileArticle, compileSettingValue, loadBuild, loadSettings, openBuildDiagnostic,
      renderBuildSummary, renderCompileMenu, renderLog, renderSettings, saveSettings, scheduleAutoCompile,
      setOutputView, stopCompilation, updateCompilePreference, updateEditorCodeCheck,
    });
  }

  window.PortalArticleWorkspaceBuild = Object.freeze({ createBuild, dependencies: DEPENDENCIES });
})();
