(() => {
  "use strict";
  if (window.PortalRepositoryPanels) return;

  const t = value => window.PortalI18n?.t(value) || value;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const icons = root => window.PortalUIPrimitives?.mountIcons?.(root);
  let markdownResolverRegistered = false;
  const message = value => String(value?.message || value || "");
  const button = (action, title, icon) => `<button type="button" class="portalButton is-compact is-quiet" data-repository-action="${action}" title="${escape(t(title))}" aria-label="${escape(t(title))}"><span data-portal-icon="${icon}" aria-hidden="true"></span></button>`;

  function base(options, kind, title) {
    const root = options.root || document.createElement("section");
    root.classList.add("portalRepositoryPanel", `portalRepositoryPanel-${kind}`);
    root.setAttribute("aria-label", t(title));
    root.innerHTML = `<header class="repositoryPanelToolbar"><strong>${escape(t(title))}</strong>${button("refresh", "刷新", "refresh-cw")}</header><p class="repositoryPanelStatus" role="status" aria-live="polite" hidden></p><div class="repositoryPanelBody"></div>`;
    const status = root.querySelector(".repositoryPanelStatus"), body = root.querySelector(".repositoryPanelBody");
    const api = options.api || window.PortalAPI;
    let destroyed = false, visible = false;
    const cleanups = [];
    function listen(target, event, handler) { target.addEventListener(event, handler); cleanups.push(() => target.removeEventListener(event, handler)); }
    const state = (text = "", error = false) => {
      if (destroyed) return;
      status.textContent = text; status.hidden = !text;
      status.classList.toggle("is-error", error);
    };
    const fail = error => { if (!destroyed) { state(message(error), true); options.onError?.(error); } };
    const call = action => Promise.resolve().then(action).catch(fail);
    const get = suffix => api.get(`/api/repos/${encodeURIComponent(options.repoId)}/workspace${suffix}`);
    icons(root);
    return { root, body, status, get, state, fail, call, listen, changed: () => options.emit?.("persistence-change"),
      destroyed: () => destroyed, visible: () => visible,
      show() { visible = true; }, hide() { visible = false; },
      destroy() { destroyed = true; visible = false; cleanups.forEach(fn => fn()); root.replaceChildren(); },
    };
  }

  function createFiles(options) {
    const view = base(options, "files", "文件树");
    const expanded = new Set(), directories = new Map(), pending = new Map();
    let revision = 0, loaded = false, refreshing = null, selected = "";
    function render() {
      if (view.destroyed()) return;
      const focusPath = view.root.contains(document.activeElement) ? document.activeElement?.dataset.repositoryPath : null;
      const scrollTop = view.body.scrollTop;
      const branch = path => {
        const data = directories.get(path);
        if (!data) return `<li class="repositoryPanelHint">${escape(t("正在读取…"))}</li>`;
        const entries = (data.entries || []).map(entry => {
          const isDirectory = entry.kind === "directory", open = expanded.has(entry.path);
          return `<li><button type="button" class="repositoryFileRow${selected === entry.path ? " is-selected" : ""}" data-repository-path="${escape(entry.path)}" data-repository-kind="${escape(entry.kind)}" ${isDirectory ? `aria-expanded="${open}"` : ""} title="${escape(entry.path)}"><span class="repositoryTreeArrow" aria-hidden="true">${isDirectory ? (open ? "▾" : "▸") : ""}</span><span data-portal-icon="${isDirectory ? "folder" : "file"}" aria-hidden="true"></span><span class="repositoryFileName">${escape(entry.name)}</span>${entry.status ? `<small>${escape(entry.status)}</small>` : ""}</button>${isDirectory && open ? `<ul>${branch(entry.path)}</ul>` : ""}</li>`;
        }).join("");
        return (entries || `<li class="repositoryPanelHint">${escape(t("空目录"))}</li>`) + (data.truncated ? `<li class="repositoryPanelHint">${escape(t("目录条目已截断，可使用文件名搜索查找。"))}</li>` : "");
      };
      view.body.innerHTML = `<ul class="repositoryFileTree" aria-label="${escape(t("仓库文件"))}">${branch("")}</ul>`;
      icons(view.body); view.body.scrollTop = scrollTop;
      if (focusPath != null) Array.from(view.body.querySelectorAll("[data-repository-path]")).find(el => el.dataset.repositoryPath === focusPath)?.focus({ preventScroll: true });
    }
    function load(path) {
      if (pending.has(path)) return pending.get(path);
      const token = revision;
      const request = view.get(`/tree?path=${encodeURIComponent(path)}`).then(data => {
        if (view.destroyed() || token !== revision) return;
        directories.set(path, data); render();
      }).catch(error => { if (token === revision) { view.fail(error); directories.set(path, { entries: [] }); render(); } }).finally(() => {
        if (pending.get(path) === request) pending.delete(path);
      });
      pending.set(path, request); return request;
    }
    function refresh() {
      if (view.destroyed()) return Promise.resolve();
      if (refreshing) return refreshing;
      revision++; pending.clear(); view.state(t("正在读取文件树…"));
      refreshing = (async () => {
        await load("");
        // Only reload expanded descendants that still exist in their parents.
        for (const path of [...expanded].sort((a, b) => a.split("/").length - b.split("/").length)) {
          const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
          if (directories.get(parent)?.entries?.some(item => item.path === path && item.kind === "directory")) await load(path);
          else { expanded.delete(path); directories.delete(path); }
        }
        loaded = true; if (!view.status.classList.contains("is-error")) view.state(); render();
      })().finally(() => { refreshing = null; });
      return refreshing;
    }
    view.listen(view.root, "click", event => {
      const action = event.target.closest("[data-repository-action]");
      if (action?.dataset.repositoryAction === "refresh") { void refresh(); return; }
      const row = event.target.closest("[data-repository-path]");
      if (!row) return;
      const path = row.dataset.repositoryPath;
      if (row.dataset.repositoryKind === "directory") {
        if (expanded.has(path)) expanded.delete(path);
        else { expanded.add(path); if (!directories.has(path)) void load(path); }
        render(); view.changed();
      } else { selected = path; render(); view.changed(); void view.call(() => options.onOpenFile?.(path)); }
    });
    render();
    return Object.freeze({ root: view.root, refresh,
      show() { view.show(); if (!loaded) void refresh(); }, hide: view.hide,
      snapshot: () => ({ expanded: [...expanded], selected, scrollTop: view.body.scrollTop }),
      async restore(value = {}) { expanded.clear(); (value.expanded || []).filter(path => typeof path === "string").forEach(path => expanded.add(path)); selected = String(value.selected || ""); await refresh(); if (!view.destroyed()) view.body.scrollTop = Number(value.scrollTop || 0); },
      destroy() { revision++; pending.clear(); view.destroy(); },
    });
  }

  function createSearch(options) {
    const view = base(options, "search", "文件名搜索");
    const form = document.createElement("form"); form.className = "repositorySearchForm";
    form.innerHTML = `<input type="search" aria-label="${escape(t("搜索文件名或路径"))}" placeholder="${escape(t("文件名或路径…"))}" autocomplete="off"><button type="submit" class="portalButton is-compact">${escape(t("搜索"))}</button>`;
    view.root.insertBefore(form, view.status);
    const input = form.querySelector("input");
    let revision = 0, timer = 0;
    view.body.innerHTML = `<p class="repositoryPanelHint">${escape(t("按文件名或路径查找仓库文件。"))}</p>`;
    async function refresh() {
      clearTimeout(timer);
      const query = input.value.trim(), token = ++revision;
      if (!query || view.destroyed()) { view.body.replaceChildren(); view.state(); return; }
      view.state(t("正在搜索…"));
      try {
        const data = await view.get(`/search?q=${encodeURIComponent(query)}`);
        if (view.destroyed() || token !== revision) return;
        view.body.innerHTML = (data.results || []).map(item => `<button type="button" class="repositorySearchResult" data-repository-path="${escape(item.path)}" title="${escape(item.path)}"><strong>${escape(item.name)}</strong><small>${escape(item.directory || "/")}</small></button>`).join("") || `<p class="repositoryPanelHint">${escape(t("没有匹配的文件。"))}</p>`;
        view.state(data.truncated ? t("结果已截断，请缩小搜索范围。") : "");
      } catch (error) { if (token === revision) view.fail(error); }
    }
    view.listen(form, "submit", event => { event.preventDefault(); void refresh(); });
    view.listen(input, "input", () => { revision++; clearTimeout(timer); view.changed(); timer = setTimeout(() => { void refresh(); }, 250); });
    view.listen(view.root, "click", event => {
      if (event.target.closest('[data-repository-action="refresh"]')) { void refresh(); return; }
      const row = event.target.closest("[data-repository-path]");
      if (row) void view.call(() => options.onOpenFile?.(row.dataset.repositoryPath));
    });
    return Object.freeze({ root: view.root, refresh, show: view.show, hide: view.hide,
      snapshot: () => ({ query: input.value }),
      restore(value = {}) { input.value = String(value.query || ""); return refresh(); },
      destroy() { revision++; clearTimeout(timer); view.destroy(); },
    });
  }

  function createGit(options) {
    const view = base(options, "git", "源代码管理");
    let loaded = false, revision = 0, diffRevision = 0, refreshing = null;
    if (options.onOpenChanges) view.root.querySelector("header").insertAdjacentHTML("beforeend", `<button type="button" class="portalButton is-compact" data-repository-action="changes">${escape(t("管理改动"))}</button>`);
    async function read() {
      const token = ++revision; view.state(t("正在读取改动…"));
      try {
        const data = await view.get("");
        if (view.destroyed() || token !== revision) return;
        loaded = true;
        if (data.capabilities?.git === false) {
          view.body.innerHTML = `<p class="repositoryPanelHint">${escape(t("此仓库来源不提供 Git 操作。"))}</p>`; view.state(); return;
        }
        const changes = data.status?.changes || [];
        view.state();
        view.body.innerHTML = `<p class="repositoryPanelHint">${escape(data.current_branch || "HEAD")} · ${changes.length} ${escape(t("项改动"))}</p>` + (changes.map(change => `<div class="repositoryChangeRow"><button type="button" class="repositoryChangeDiff" data-repository-diff="${escape(change.path)}" title="${escape(t("查看差异"))}: ${escape(change.path)}"><span class="repositoryChangeCode">${escape(change.code || "M")}</span><span><strong>${escape(change.path.split("/").at(-1))}</strong><small>${escape(change.path)}</small></span></button>${String(change.code || "").includes("D") ? "" : `<button type="button" class="portalButton is-compact is-quiet" data-repository-path="${escape(change.path)}" aria-label="${escape(t("打开文件"))}: ${escape(change.path)}"><span data-portal-icon="code" aria-hidden="true"></span></button>`}</div>`).join("") || `<p class="repositoryPanelHint">${escape(t("没有本地改动。"))}</p>`);
        icons(view.body);
      } catch (error) { if (token === revision) view.fail(error); }
    }
    function refresh() {
      if (view.destroyed()) return Promise.resolve();
      if (!refreshing) refreshing = read().finally(() => { refreshing = null; });
      return refreshing;
    }
    view.listen(view.root, "click", event => {
      const action = event.target.closest("[data-repository-action]")?.dataset.repositoryAction;
      if (action === "refresh") { void refresh(); return; }
      if (action === "changes") { void view.call(() => options.onOpenChanges()); return; }
      const file = event.target.closest("[data-repository-path]");
      if (file) { void view.call(() => options.onOpenFile?.(file.dataset.repositoryPath)); return; }
      const row = event.target.closest("[data-repository-diff]");
      if (row) {
        const path = row.dataset.repositoryDiff, token = ++diffRevision;
        view.state(t("正在读取差异…"));
        void view.call(async () => {
          const diff = await view.get(`/diff?path=${encodeURIComponent(path)}`);
          if (view.destroyed() || token !== diffRevision) return;
          view.state(); await options.onDiff?.(path, diff);
        });
      }
    });
    return Object.freeze({ root: view.root, refresh,
      show() { view.show(); if (!loaded) void refresh(); }, hide: view.hide,
      snapshot: () => ({ scrollTop: view.body.scrollTop }),
      async restore(value = {}) { await refresh(); if (!view.destroyed()) view.body.scrollTop = Number(value.scrollTop || 0); },
      destroy() { revision++; diffRevision++; view.destroy(); },
    });
  }

  function repositoryLink(repoId, fromPath, href) {
    if (!href || /^(?:[a-z][a-z\d+.-]*:|\/\/|#|\/api\/|\/r\/)/i.test(href)) return null;
    const base = new URL(`https://repository.invalid/${String(fromPath || "").split("/").map(encodeURIComponent).join("/")}`);
    const url = new URL(href, base);
    if (url.origin !== base.origin) return null;
    const path = url.pathname.slice(1).split("/").map(part => decodeURIComponent(part)).join("/");
    return { path, address: `${repoId}/file/${path}`, hash: url.hash };
  }

  function createPreview(options) {
    if (!markdownResolverRegistered && window.MD?.registerImagePathResolver) {
      window.MD.registerImagePathResolver("repository-panels", (href, context = {}) => {
        if (!/^repo_[a-f0-9]{12}$/.test(context.repoId || "")) return "";
        let link; try { link = repositoryLink(context.repoId, context.basePath, href); } catch (_) { return ""; }
        return link ? `/api/resources/content?address=${encodeURIComponent(link.address)}` : "";
      });
      markdownResolverRegistered = true;
    }
    const view = base(options, "preview", "预览");
    view.root.querySelector('[data-repository-action="refresh"]').remove();
    const title = view.root.querySelector("header strong");
    const form = document.createElement("form"); form.className = "repositoryLatexToolbar"; form.hidden = true;
    form.innerHTML = `<label><span>${escape(t("主文件"))}</span><input type="text" aria-label="${escape(t("LaTeX 主文件"))}" placeholder="main.tex" spellcheck="false"></label><select aria-label="${escape(t("LaTeX 编译器"))}"><option value="">${escape(t("自动（源文件设置）"))}</option><option value="pdflatex">pdfLaTeX</option><option value="xelatex">XeLaTeX</option><option value="lualatex">LuaLaTeX</option></select><button class="portalButton is-primary is-compact" type="submit">${escape(t("编译预览"))}</button>`;
    view.root.insertBefore(form, view.status);
    const mainInput = form.querySelector("input"), compiler = form.querySelector("select"), compileButton = form.querySelector("button");
    const markdownRoot = document.createElement("article"); markdownRoot.className = "repositoryMarkdown markdownBody"; markdownRoot.hidden = true;
    const pdfRoot = document.createElement("div"); pdfRoot.className = "repositoryPdfHost"; pdfRoot.hidden = true;
    const empty = document.createElement("p"); empty.className = "repositoryPanelHint"; empty.textContent = t("打开 Markdown 或 LaTeX 文件以预览。");
    view.body.classList.add("repositoryPreviewBody"); view.body.replaceChildren(markdownRoot, pdfRoot, empty);
    const log = document.createElement("details"); log.className = "repositoryCompileLog"; log.hidden = true;
    log.innerHTML = `<summary>${escape(t("编译日志"))}</summary><pre></pre>`; view.root.append(log);
    let documentValue = null, documentRevision = 0, compileRevision = 0, cacheRevision = 0, markdownRevision = 0, busy = false, pinnedMain = false, pdfPanel = null, lastPdf = "";
    const isMarkdown = path => /\.(?:md|markdown|mdown)$/i.test(path || "");
    const isTex = path => /\.(?:tex|ltx)$/i.test(path || "");
    const changed = view.changed;
    function setBusy(value) {
      busy = value; compileButton.disabled = value || typeof options.compileLatex !== "function";
      mainInput.disabled = value; compiler.disabled = value;
      view.root.setAttribute("aria-busy", String(value));
    }
    function ensurePdf() {
      if (!pdfPanel) {
        const surface = document.createElement("div");
        pdfRoot.replaceChildren(surface);
        pdfPanel = window.PortalWorkbench.createPanel("preview", {
          root: surface, panelId: `${options.panelId || "repository-preview"}-pdf`, heading: "PDF", explorer: false,
        });
      }
      return pdfPanel;
    }
    function clearPdf() { pdfPanel?.destroy?.(); pdfPanel = null; lastPdf = ""; pdfRoot.hidden = true; }
    function showReport(result) {
      const diagnosticText = (result?.diagnostics || []).map(item => [item.file || item.path, item.line, item.message].filter(value => value !== undefined && value !== "").join(": ")).join("\n");
      log.querySelector("pre").textContent = String(result?.log || diagnosticText || result?.error || "");
      log.hidden = !log.querySelector("pre").textContent;
      if (/^(?:failed|error|cancelled)$/i.test(result?.status || "")) log.open = true;
    }
    function errorReport(error) {
      const candidate = error?.payload?.report || error?.data?.report || error?.payload || error?.data;
      return candidate && typeof candidate === "object" && (candidate.log || candidate.diagnostics) ? candidate : null;
    }
    async function showPdf(result, path, current) {
      pdfRoot.hidden = false; empty.hidden = true;
      const primary = result.primary_file || result.path || path;
      let pdfUrl = result.pdf_url;
      if (result.compiled_at) {
        const versioned = new URL(pdfUrl, window.location.href);
        versioned.searchParams.set("v", String(result.compiled_at));
        pdfUrl = /^[a-z][a-z\d+.-]*:/i.test(pdfUrl) ? versioned.href : `${versioned.pathname}${versioned.search}${versioned.hash}`;
      }
      await ensurePdf().setDocument({ id: `${options.repoId}:${primary}:pdf`, title: primary.replace(/\.(tex|ltx)$/i, ".pdf").split("/").at(-1), path: primary.replace(/\.(tex|ltx)$/i, ".pdf"), kind: "pdf", mediaType: "application/pdf", binary: true, rawUrl: pdfUrl, downloadUrl: pdfUrl });
      if (current()) { lastPdf = pdfUrl; changed(); }
    }
    async function loadCached() {
      if (view.destroyed() || busy || !isTex(documentValue?.path) || typeof options.loadLatex !== "function") return null;
      const path = mainInput.value.trim(), token = ++cacheRevision, documentToken = documentRevision;
      const current = () => !view.destroyed() && !busy && token === cacheRevision && documentToken === documentRevision;
      if (!path) return null;
      try {
        const result = await options.loadLatex(path, compiler.value);
        if (!current() || !result) return null;
        showReport(result);
        if (!result.pdf_url || result.pdf_available === false || /^(?:failed|error|cancelled)$/i.test(result.status || "")) return result;
        await showPdf(result, path, current);
        if (current()) view.state(t("已加载上次编译预览"));
        return result;
      } catch (error) {
        if (current()) { const report = errorReport(error); if (report) showReport(report); view.fail(error); }
        return null;
      }
    }
    async function renderMarkdown() {
      const token = ++markdownRevision, value = documentValue;
      const scrollTop = markdownRoot.scrollTop;
      const html = await (options.renderMarkdown ? options.renderMarkdown(String(value.content || ""), value.path)
        : window.MD.render(String(value.content || ""), { basePath: value.path, repoId: options.repoId }));
      if (view.destroyed() || token !== markdownRevision) return;
      markdownRoot.innerHTML = html;
      // Use the resource content route for local media; internal document links
      // go through the host's open-file API and retain its dirty-file guard.
      for (const el of markdownRoot.querySelectorAll("img[src], a[href]")) {
        const attr = el.tagName === "IMG" ? "src" : "href", raw = el.getAttribute(attr);
        let link; try { link = repositoryLink(options.repoId, value.path, raw); } catch (_) { continue; }
        if (!link) continue;
        if (attr === "src") {
          const src = `/api/resources/content?address=${encodeURIComponent(link.address)}`;
          el.setAttribute(attr, src); el.dataset.portalPreviewImage = src;
        } else {
          el.href = `/r/${link.address.split("/").map(encodeURIComponent).join("/")}${link.hash}`;
          el.dataset.portalPreviewLink = el.getAttribute("href"); el.dataset.repositoryPath = link.path;
        }
      }
      markdownRoot.scrollTop = scrollTop;
    }
    async function setDocument(value) {
      if (view.destroyed()) return;
      const oldPath = documentValue?.path || "", newPath = String(value?.path || ""), changedPath = oldPath !== newPath;
      documentValue = value && newPath ? { ...value, path: newPath } : null;
      if (changedPath) { documentRevision++; compileRevision++; cacheRevision++; markdownRevision++; setBusy(false); view.state(); log.hidden = true; log.open = false; markdownRoot.scrollTop = 0; }
      title.textContent = documentValue ? `${newPath.split("/").at(-1)}${value.dirty ? " · " + t("未保存") : ""}` : t("预览");
      form.hidden = !isTex(newPath); markdownRoot.hidden = !isMarkdown(newPath);
      if (isMarkdown(newPath)) { if (changedPath) clearPdf(); empty.hidden = true; await renderMarkdown(); }
      else if (isTex(newPath)) {
        if (!pinnedMain || !mainInput.value) mainInput.value = newPath;
        if (changedPath && !pinnedMain) clearPdf();
        pdfRoot.hidden = !lastPdf; empty.hidden = !!lastPdf;
        empty.textContent = t("选择 LaTeX 主文件，点击“编译预览”生成 PDF。");
        if (changedPath && !lastPdf) await loadCached();
      } else {
        clearPdf(); empty.hidden = false;
        empty.textContent = documentValue ? t("此文件不提供 Markdown / LaTeX 预览。") : t("打开 Markdown 或 LaTeX 文件以预览。");
      }
      changed();
    }
    async function compile() {
      if (view.destroyed() || busy || !isTex(documentValue?.path)) return null;
      const path = mainInput.value.trim();
      if (!path || !isTex(path)) { view.state(t("请输入 LaTeX 主文件路径（.tex 或 .ltx）。"), true); mainInput.focus(); return null; }
      if (typeof options.compileLatex !== "function") { view.state(t("当前仓库不支持编译。"), true); return null; }
      const token = ++compileRevision, documentToken = documentRevision;
      cacheRevision++;
      setBusy(true); view.state(t("正在保存文件…")); log.hidden = true;
      const current = () => !view.destroyed() && token === compileRevision && documentToken === documentRevision;
      try {
        if (await options.beforeCompile?.() === false) { if (current()) view.state(t("编译已取消。")); return null; }
        if (!current()) return null;
        view.state(t("正在编译…"));
        const result = await options.compileLatex(path, compiler.value);
        if (!current()) return null;
        showReport(result);
        if (!result?.pdf_url || result.pdf_available === false || /^(?:failed|error|cancelled)$/i.test(result.status || "")) {
          log.open = true; throw new Error(result?.error || t("编译未生成 PDF，请查看编译日志。"));
        }
        view.state(t("正在加载预览…"));
        await showPdf(result, path, current);
        if (!current()) return null;
        view.state(result.status === "warning" ? t("编译完成（有警告）") : t("编译完成")); return result;
      } catch (error) {
        if (current()) { const report = errorReport(error); if (report) showReport(report); view.fail(error); empty.hidden = !!lastPdf; if (!lastPdf) { pdfRoot.hidden = true; empty.textContent = t("编译失败，请检查日志后重试。"); } }
        return null;
      } finally { if (current()) setBusy(false); }
    }
    view.listen(form, "submit", event => { event.preventDefault(); void compile(); });
    view.listen(mainInput, "change", () => { pinnedMain = true; changed(); void loadCached(); });
    view.listen(compiler, "change", () => { changed(); void loadCached(); });
    view.listen(markdownRoot, "click", event => {
      const link = event.target.closest("a[data-repository-path]");
      if (link && options.onOpenFile && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
        event.preventDefault(); void view.call(() => options.onOpenFile(link.dataset.repositoryPath));
      }
    });
    setBusy(false);
    return Object.freeze({ root: view.root, setDocument, compile, loadCached,
      show() { view.show(); pdfPanel?.show?.(); }, hide() { view.hide(); pdfPanel?.hide?.(); },
      snapshot: () => ({ mainPath: mainInput.value, pinnedMain, compiler: compiler.value, markdownScrollTop: markdownRoot.scrollTop }),
      restore(value = {}) { mainInput.value = String(value.mainPath || ""); pinnedMain = value.pinnedMain === true; if (["", "pdflatex", "xelatex", "lualatex"].includes(value.compiler)) compiler.value = value.compiler; markdownRoot.scrollTop = Number(value.markdownScrollTop || 0); },
      destroy() { documentRevision++; compileRevision++; cacheRevision++; markdownRevision++; clearPdf(); view.destroy(); },
    });
  }

  const definitions = [
    ["repository-files", "文件树", "files", "navigation", 180, createFiles],
    ["repository-search", "文件名搜索", "search", "search", 200, createSearch],
    ["repository-git", "源代码管理", "git-branch", "source-control", 200, createGit],
    ["repository-preview", "预览", "window", "preview", 280, createPreview],
  ];
  function register() {
    const existing = new Set(window.PortalWorkbench.registeredPanelPackages().map(item => item.type));
    definitions.forEach(([type, title, icon, capability, minWidth, create]) => {
      if (existing.has(type)) return;
      window.PortalWorkbench.registerPanelPackage({
        type, version: 1, component: `PortalRepositoryPanels.${type.slice(11)}`,
        presentation: { title: t(title), icon }, sizing: { minWidth, minHeight: 180 }, instances: "single",
        capabilities: [capability, "snapshot", "restore"], behavior: { canHide: true, canCollapse: true, canMove: true, closable: false },
        // Domain dependencies are explicit callbacks. No undeclared host state
        // or context channel is read by these packages.
        context: { channels: [] }, persistence: { schemaVersion: 1 }, create,
      });
    });
  }
  window.PortalRepositoryPanels = Object.freeze({ register });
})();
