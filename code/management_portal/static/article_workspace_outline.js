(() => {
  "use strict";

  if (window.PortalArticleWorkspaceOutline) return;

  // The outline side view: the document outline parsed from LaTeX and the
  // semantic index (labels, references, citations, problems) loaded for the
  // Article.
  const DEPENDENCIES = Object.freeze([
    "$", "articleStateHtml", "esc", "request", "showToast", "state", "updateAllEditorMarkers",
  ]);

  function createOutline(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceOutline is missing: ${missing.join(", ")}`);
    const { $, articleStateHtml, esc, request, showToast, state, updateAllEditorMarkers } = dependencies;

    function plainTexTitle(value) {
      return String(value || "")
        .replace(/\\(?:textbf|textit|emph|texorpdfstring)\*?(?:\[[^\]]*\])?\{([^{}]*)\}/g, "$1")
        .replace(/\\[A-Za-z@]+\*?/g, "")
        .replace(/[{}]/g, "")
        .replace(/~/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    }

    function parseOutline(source) {
      const levels = { part: 0, chapter: 0, section: 0, subsection: 1, subsubsection: 2, paragraph: 3 };
      const output = [];
      String(source || "").split("\n").forEach((line, index) => {
        if (/^\s*%/.test(line)) return;
        const match = line.match(/^\s*\\(part|chapter|section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\{(.+?)\}/);
        if (!match) return;
        output.push({
          title: plainTexTitle(match[2]) || match[1],
          level: levels[match[1]] || 0,
          line: index + 1,
        });
      });
      return output;
    }

    function semanticDocumentOrder(index) {
      return new Map((index?.documents || []).map((item, position) => [item.path, position]));
    }

    function groupedSemanticItems(items, index, rowHtml) {
      const groups = new Map();
      for (const item of items) {
        const path = String(item.path || "Other");
        if (!groups.has(path)) groups.set(path, []);
        groups.get(path).push(item);
      }
      const order = semanticDocumentOrder(index);
      return [...groups.entries()]
        .sort(([left], [right]) => (
          (order.get(left) ?? 10000) - (order.get(right) ?? 10000)
          || left.localeCompare(right)
        ))
        .map(([path, values]) => window.PortalI18n.html`
        <section class="articleSemanticGroup">
          <div class="articleSemanticGroupTitle"><span title="${esc(path)}">${esc(path)}</span><small>${values.length}</small></div>
          ${values.map(rowHtml).join("")}
        </section>`).join("");
    }

    function semanticNavigationItems() {
      const index = state.semanticIndex || {};
      if (state.semanticView === "outline") {
        let headings = Array.isArray(index.headings) ? index.headings.slice() : [];
        if (state.openFile.toLowerCase().endsWith(".tex")) {
          headings = headings.filter(item => item.path !== state.openFile);
          headings.push(...parseOutline(state.source).map(item => ({
            ...item,
            path: state.openFile,
            column: 1,
          })));
        }
        return {
          items: headings,
          html: groupedSemanticItems(headings, index, item => window.PortalI18n.html`
          <button class="articleOutlineItem" style="--outline-depth:${Number(item.level || 0)}" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}">
            <span>${esc(item.title || item.command || "Untitled")}</span><small>L${Number(item.line || 1)}</small>
          </button>`),
          empty: state.selectedId ? "工程中还没有可导航的章节标题。" : "Select an Article project.",
        };
      }
      if (state.semanticView === "labels") {
        const items = Array.isArray(index.labels) ? index.labels : [];
        return {
          items,
          html: groupedSemanticItems(items, index, item => window.PortalI18n.html`
          <button class="articleOutlineItem" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}">
            <span>\\label{${esc(item.key)}}</span><small>L${Number(item.line || 1)}</small>
          </button>`),
          empty: "工程中还没有 LaTeX 标签。",
        };
      }
      if (state.semanticView === "citations") {
        const bibliography = Array.isArray(index.bibliography) ? index.bibliography : [];
        const citations = Array.isArray(index.citations) ? index.citations : [];
        const usageCounts = new Map();
        citations.forEach(item => usageCounts.set(item.key, (usageCounts.get(item.key) || 0) + 1));
        const bibliographyHtml = bibliography.length ? window.PortalI18n.html`
        <section class="articleSemanticGroup">
          <div class="articleSemanticGroupTitle"><span>文献库</span><small>${bibliography.length}</small></div>
          ${bibliography.map(item => window.PortalI18n.html`
            <button class="articleOutlineItem" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}" title="${esc(item.title || item.key)}">
              <span>${esc(item.key)}${item.title ? ` · ${esc(item.title)}` : ""}</span><small>${usageCounts.get(item.key) || 0} 次</small>
            </button>`).join("")}
        </section>` : "";
        const usageHtml = groupedSemanticItems(citations, index, item => window.PortalI18n.html`
        <button class="articleOutlineItem" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}">
          <span>\\${esc(item.command || "cite")}{${esc(item.key)}}</span><small>L${Number(item.line || 1)}</small>
        </button>`);
        return {
          items: [...bibliography, ...citations],
          html: bibliographyHtml + usageHtml,
          empty: "工程中还没有文献条目或引用。",
        };
      }
      if (state.semanticView === "figures") {
        const items = Array.isArray(index.figures) ? index.figures : [];
        return {
          items,
          html: groupedSemanticItems(items, index, item => window.PortalI18n.html`
          <button class="articleOutlineItem ${item.exists ? "" : window.PortalI18n.t("problem error")}" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}" title="${esc(item.resolved_path || item.target)}">
            <span>${item.exists ? "" : window.PortalI18n.t("缺失 · ")}${esc(item.target || "figure")}</span><small>L${Number(item.line || 1)}</small>
          </button>`),
          empty: "工程中还没有 includegraphics 图片引用。",
        };
      }
      const items = Array.isArray(index.problems) ? index.problems : [];
      return {
        items,
        html: groupedSemanticItems(items, index, item => window.PortalI18n.html`
        <button class="articleOutlineItem problem ${esc(item.severity || "warning")}" type="button" data-editor-path="${esc(item.path)}" data-editor-line="${Number(item.line || 1)}" data-editor-column="${Number(item.column || 1)}" title="${esc(item.message)}">
          <span>${esc(item.message || window.PortalI18n.t("LaTeX semantic problem"))}</span><small>L${Number(item.line || 1)}</small>
        </button>`),
        empty: state.semanticIndex ? "没有发现跨文件引用、标签、文献或图片问题。" : "正在建立工程语义索引…",
      };
    }

    function renderSemanticNavigation() {
      const navigation = semanticNavigationItems();
      $("articleOutlineCount").textContent = String(navigation.items.length);
      $("articleOutlineList").innerHTML = navigation.html
        || articleStateHtml(navigation.empty, { density: "inline", icon: false });
      document.querySelectorAll("[data-semantic-view]").forEach(button => {
        const active = button.dataset.semanticView === state.semanticView;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
    }

    async function loadSemanticIndex() {
      if (!state.selectedId) {
        state.semanticIndex = null;
        renderSemanticNavigation();
        return;
      }
      const id = state.selectedId;
      try {
        const payload = await request(`/api/articles/${encodeURIComponent(id)}/semantic`);
        if (id !== state.selectedId) return;
        state.semanticIndex = payload;
        renderSemanticNavigation();
        updateAllEditorMarkers();
      } catch (error) {
        if (id !== state.selectedId) return;
        state.semanticIndex = null;
        renderSemanticNavigation();
        showToast(window.PortalI18n.text`工程语义索引：${error.message || error}`, "warning");
      }
    }

    function scheduleSemanticReload(delay = 150) {
      window.clearTimeout(state.semanticTimer);
      state.semanticTimer = window.setTimeout(() => {
        state.semanticTimer = 0;
        renderSemanticNavigation();
      }, Math.max(50, Number(delay) || 150));
    }

    return Object.freeze({
      loadSemanticIndex, renderSemanticNavigation, scheduleSemanticReload,
    });
  }

  window.PortalArticleWorkspaceOutline = Object.freeze({ createOutline, dependencies: DEPENDENCIES });
})();
