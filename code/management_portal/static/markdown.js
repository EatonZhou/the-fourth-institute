// Shared markdown renderer for the portal (todo / knowledge / review).
// Exposes window.MD = { render, renderInline, esc }.
// Supports: headings, hr, blockquotes, code fences, tables (with separator),
// task lists, ul/ol with nested-by-indent, math blocks / inline math,
// inline code / bold / em / del / links.
(function (global) {
  "use strict";

  function esc(s) {
    return global.PortalUIPrimitives.escapeHtml(s);
  }

  function unescUrl(s) {
    return String(s == null ? "" : s)
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, "&")
      .trim();
  }

  const MARKDOWN_NAVIGATION_SCHEMES = new Set(["file", "http", "https", "mailto"]);
  const MARKDOWN_INLINE_IMAGE_RE = /^data:image\/(?:gif|jpeg|png|webp);base64,[a-z0-9+/=]+$/i;
  const imagePathResolvers = new Map();

  function registerImagePathResolver(id, resolver) {
    const key = String(id || "").trim();
    if (!key || typeof resolver !== "function") {
      throw new TypeError("MD.registerImagePathResolver requires an id and resolver.");
    }
    if (imagePathResolvers.has(key)) {
      throw new Error(`Markdown image path resolver is already registered: ${key}`);
    }
    imagePathResolvers.set(key, resolver);
    return () => {
      if (imagePathResolvers.get(key) === resolver) imagePathResolvers.delete(key);
    };
  }

  function resolveImagePath(rawPath, options) {
    for (const resolver of imagePathResolvers.values()) {
      const resolved = resolver(rawPath, options);
      if (resolved) return resolved;
    }
    return "";
  }

  function cleanMarkdownUrl(value) {
    const url = unescUrl(value);
    return !url || /[\u0000-\u001f\u007f]/.test(url) ? "" : url;
  }

  function markdownNavigationUrl(value) {
    const url = cleanMarkdownUrl(value);
    if (!url) return "";
    const scheme = url.match(/^([a-z][a-z0-9+.-]*):/i);
    if (!scheme) return url;
    return MARKDOWN_NAVIGATION_SCHEMES.has(scheme[1].toLowerCase()) ? url : "";
  }

  function markdownImageUrl(value) {
    const url = cleanMarkdownUrl(value);
    if (!url || url.includes("\\")) return "";
    if (MARKDOWN_INLINE_IMAGE_RE.test(url)) return url;
    if (url.startsWith("//")) return "";
    if (!/^[a-z][a-z0-9+.-]*:/i.test(url)) return url;
    if (!/^https?:/i.test(url) || !global.location) return "";
    try {
      return new URL(url, global.location.href).origin === global.location.origin ? url : "";
    } catch (_error) {
      return "";
    }
  }

  function dirnamePath(path) {
    const text = String(path == null ? "" : path).trim();
    if (!text) return "";
    if (text.endsWith("/")) return text.replace(/\/+$/, "");
    const idx = text.lastIndexOf("/");
    return idx > 0 ? text.slice(0, idx) : "";
  }

  function joinLocalPath(basePath, relativePath) {
    const baseDir = dirnamePath(basePath);
    const rel = String(relativePath == null ? "" : relativePath).trim();
    if (!baseDir || !rel) return "";
    const parts = (baseDir + "/" + rel).split("/");
    const out = [];
    parts.forEach(function (part, index) {
      if (!part || part === ".") {
        if (index === 0) out.push("");
        return;
      }
      if (part === "..") {
        if (out.length > 1) out.pop();
        return;
      }
      out.push(part);
    });
    return out.join("/") || "/";
  }

  function markdownImageUrlForPath(path, options) {
    const raw = unescUrl(path);
    if (!raw || raw.includes("\\")) return "";
    if (/^data:image\//i.test(raw)) return raw;
    if (/^https?:\/\//i.test(raw)) return raw;
    if (raw.startsWith("/api/agent-workbench/") || raw.startsWith("/knowledge/api/")) return raw;
    let localPath = raw.startsWith("file://") ? raw.slice("file://".length) : raw;
    if (!localPath.startsWith("/") && !/^[a-z][a-z0-9+.-]*:/i.test(localPath)) {
      const basePath = options && (options.basePath);
      const joined = joinLocalPath(basePath, localPath);
      if (joined) localPath = joined;
    }
    const residentPreview = global.PortalFileLinks?.imagePreviewUrl?.(localPath) || "";
    if (residentPreview) return residentPreview;
    // Non-secret roots come from the existing site bootstrap. These only
    // select the preview transport; the endpoint still authorizes the file.
    const paths = global.__PORTAL_SITE__?.paths || {};
    let decodedPath;
    try { decodedPath = decodeURIComponent(localPath); } catch { return ""; }
    if (/[\\\u0000-\u001f\u007f]/.test(decodedPath)
        || decodedPath.split("/").some(part => part === "." || part === "..")) return "";
    const configuredRoot = [paths.share_root, paths.staging_root, paths.test_results_root].some(root => {
      if (typeof root !== "string" || !/^\/(?!\/)/.test(root) || root === "/"
          || /[\\\u0000-\u001f\u007f]/.test(root)
          || root.split("/").some(part => part === "." || part === "..")) return false;
      return localPath.startsWith(root.replace(/\/+$/, "") + "/");
    });
    // Retain the historical server locator alias, and keep ordinary URL paths
    // (including /share when it is a route) on the ordinary image path below.
    if (configuredRoot || localPath.startsWith("/nas_ro/")) {
      return "/api/agent-workbench/link-file-raw?path=" + encodeURIComponent(localPath);
    }
    if (/^\/(?:home|root|srv|tmp|var|etc|usr|opt|data|mnt|media|proc|sys|dev|run)(?:\/|$)/.test(localPath)) return "";
    if (localPath.startsWith("/") || /^(?:\.\/|\.\.\/)/.test(localPath)) return localPath;
    return "";
  }

  let activeRenderOptions = {};
  let katexPromise = null;
  let katexLoadAttempt = 0;
  let unregisterMath = null;
  let mermaidPromise = null;
  let mermaidLoadAttempt = 0;

  function ensureMermaid() {
    if (typeof document === "undefined") return;
    if (mermaidPromise) return mermaidPromise;
    const moduleUrl = "/static/portal_mermaid.mjs" + (mermaidLoadAttempt ? "?retry=" + mermaidLoadAttempt : "");
    mermaidPromise = import(moduleUrl).then(({ default: mermaid }) => {
      mermaid.observe(document);
    }).catch(() => {
      mermaidLoadAttempt += 1;
      mermaidPromise = null;
      document.querySelectorAll('[data-portal-mermaid][data-mermaid-state="pending"]').forEach(block => {
        block.dataset.mermaidState = "error";
        const canvas = block.querySelector("[data-portal-mermaid-canvas]");
        if (canvas) canvas.textContent = window.PortalI18n.t("流程图渲染器加载失败，请重新打开内容重试。");
        const source = block.querySelector(".portalMermaidSource");
        if (source) source.open = true;
      });
    });
    return mermaidPromise;
  }

  function hydrateMathNode(node) {
    if (!global.katex || typeof global.katex.renderToString !== "function") return;
    const source = node.getAttribute("data-portal-math-source");
    if (source === null) return;
    node.innerHTML = renderMathMarkup(source, node.classList.contains("mathBlock"));
    node.removeAttribute("data-portal-math-source");
  }

  function prepareMathDocument() {
    if (!document.querySelector('link[href="/static/vendor/katex/katex.min.css"]')) {
      const style = document.createElement("link");
      style.rel = "stylesheet";
      style.href = "/static/vendor/katex/katex.min.css";
      style.addEventListener("error", () => style.remove(), { once: true });
      document.head.appendChild(style);
    }
    if (!unregisterMath) {
      unregisterMath = global.PortalUIPrimitives.registerEnhancer({
        id: "markdown-math", selector: "[data-portal-math-source]", enhance: hydrateMathNode,
      });
    }
  }

  function ensureKatex() {
    if (typeof document === "undefined") return Promise.resolve(null);
    prepareMathDocument();
    if (global.katex) {
      return Promise.resolve(global.katex);
    }
    if (katexPromise) return katexPromise;
    const moduleUrl = "/static/vendor/katex/katex.mjs" + (katexLoadAttempt ? "?retry=" + katexLoadAttempt : "");
    katexPromise = import(moduleUrl).then(({ default: katex }) => {
      if (typeof katex?.renderToString !== "function") throw new Error("KaTeX unavailable");
      global.katex = katex;
      document.querySelectorAll("[data-portal-math-source]").forEach(hydrateMathNode);
      return katex;
    }).catch(() => {
      katexLoadAttempt += 1;
      katexPromise = null;
      return null;
    });
    return katexPromise;
  }

  function renderInline(text, options) {
    const renderOptions = options || activeRenderOptions || {};
    const math = [];
    const code = [];
    function stashMath(raw) {
      const token = "@@PORTAL_INLINE_MATH_" + math.length + "@@";
      math.push({ token: token, html: renderMath(raw, false) });
      return token;
    }
    let source = String(text == null ? "" : text);
    source = source.replace(/(`+)([^`]+?)\1(?!`)/g, function (_match, _delimiter, body) {
      const token = "@@PORTAL_INLINE_CODE_" + code.length + "@@";
      code.push({ token, html: "<code>" + esc(body) + "</code>" });
      return token;
    });
    source = source.replace(/\\\(([\s\S]+?)\\\)/g, function (_m, body) {
      return stashMath(body);
    });
    source = source.replace(/(^|[^\\$])\$([^\n$]+?)\$(?!\$)/g, function (_m, prefix, body) {
      return prefix + stashMath(body);
    });

    let html = esc(source);
    html = html.replace(/&lt;br\s*\/?&gt;/gi, "<br>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    html = html.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    function renderImageHtml(label, href, fallback) {
      const custom = resolveImagePath(unescUrl(href), renderOptions);
      const src = markdownImageUrl(custom || markdownImageUrlForPath(href, renderOptions));
      if (!src) return fallback == null ? (label ? "Image: " + label : "") : fallback;
      return '<img class="portalMarkdownImage" src="' + esc(src) + '" alt="' + esc(label || "") + '" loading="lazy" data-portal-preview-image="' + esc(src) + '">';
    }
    function renderImage(_m, label, href) {
      return renderImageHtml(label, href);
    }
    function renderPortalImageRef(match, href) {
      return renderImageHtml("image", href, match);
    }
    function renderLink(_m, label, href) {
      const safeHref = markdownNavigationUrl(href);
      if (!safeHref) return label;
      const encodedHref = esc(safeHref);
      return '<a href="' + encodedHref + '" target="_blank" rel="noopener noreferrer" data-portal-preview-link="' + encodedHref + '">' + label + "</a>";
    }
    html = html.replace(/!\[([^\]]*)\]\([ \t]*&lt;([^\n]*?)&gt;[ \t]*\)/g, renderImage);
    html = html.replace(/!\[([^\]]*)\]\([ \t]*([^)\s]+)[ \t]*\)/g, renderImage);
    html = html.replace(/\[image:\s*([^\]]+)\]/gi, renderPortalImageRef);
    html = html.replace(/\[([^\]]+)\]\([ \t]*&lt;([^\n]*?)&gt;[ \t]*\)/g, renderLink);
    html = html.replace(/\[([^\]]+)\]\([ \t]*([^)\s]+)[ \t]*\)/g, renderLink);
    math.forEach(function (item) {
      html = html.split(item.token).join(item.html);
    });
    code.forEach(function (item) {
      html = html.split(item.token).join(item.html);
    });
    return html;
  }

  function renderMath(raw, display) {
    const source = String(raw == null ? "" : raw).trim();
    ensureKatex();
    const body = renderMathMarkup(raw, display);
    const pending = global.katex ? "" : ' data-portal-math-source="' + esc(source) + '"';
    return display
      ? '<div class="mathBlock"' + pending + ">" + body + "</div>"
      : '<span class="mathInline"' + pending + ">" + body + "</span>";
  }

  function renderCodeFence(lines, language, complete = true) {
    const source = lines.join("\n");
    const normalizedLanguage = String(language || "").trim().toLowerCase();
    if (["math", "tex", "latex"].includes(normalizedLanguage)) {
      return renderMath(source, true);
    }
    const safeLanguage = /^[a-z0-9_+-]{1,40}$/.test(normalizedLanguage) ? normalizedLanguage : "";
    const languageClass = safeLanguage ? ` class="language-${safeLanguage}"` : "";
    if (normalizedLanguage === "mermaid" && complete) {
      ensureMermaid();
      return `<figure class="portalMermaidBlock" data-portal-mermaid data-mermaid-state="pending">
        <div class="portalMermaidCanvas" data-portal-mermaid-canvas role="img" aria-label="Mermaid diagram">
          <span class="portalMermaidStatus">正在准备 Mermaid 图…</span>
        </div>
        <details class="portalMermaidSource">
          <summary>查看 Mermaid 源码</summary>
          <pre data-portal-mermaid-source><code${languageClass}>${esc(source)}</code></pre>
        </details>
      </figure>`;
    }
    return `<pre><code${languageClass}>${esc(source)}</code></pre>`;
  }

  function renderMathMarkup(raw, display) {
    const source = String(raw == null ? "" : raw).trim();
    if (global.katex && typeof global.katex.renderToString === "function") {
      try {
        return global.katex.renderToString(source, {
          displayMode: !!display,
          throwOnError: false,
          strict: "ignore",
          trust: false,
        });
      } catch (_err) {}
    }
    return esc(source);
  }

  function indentCols(s) {
    return (s || "").replace(/\t/g, "  ").length;
  }

  function isTableRow(trimmed) {
    return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 1;
  }

  function isTableSep(trimmed) {
    return /^\|[\s|:\-]+\|$/.test(trimmed);
  }

  function parseTableCells(trimmed) {
    return trimmed.slice(1, -1).split("|").map(function (c) { return c.trim(); });
  }

  function render(src, options) {
    const previousRenderOptions = activeRenderOptions;
    activeRenderOptions = options || {};
    try {
    const lines = String(src == null ? "" : src).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    // Stack of open lists: { type: "ul" | "ol", indent, liOpen }
    // Task syntax `- [ ] x` is NOT a separate list type — it is a plain <ul> item
    // rendered as a checkbox. Siblings may freely mix bullets and checkboxes.
    const listStack = [];
    let para = [];
    let blockquote = [];
    let codeFence = false;
    let codeFenceLang = "";
    let codeLines = [];
    let mathFence = false;
    let mathFenceEnd = "";
    let mathLines = [];
    let tableRows = [];
    let tableHasSep = false;

    function flushPara() {
      if (!para.length) return;
      out.push("<p>" + para.map(renderInline).join("<br>") + "</p>");
      para = [];
    }

    function closeAllLists() {
      while (listStack.length) {
        const top = listStack.pop();
        if (top.liOpen) out.push("</li>");
        out.push("</" + top.type + ">");
      }
    }

    function flushBlockquote() {
      if (!blockquote.length) return;
      out.push(
        "<blockquote>" +
          blockquote.map(function (l) { return "<p>" + renderInline(l) + "</p>"; }).join("") +
          "</blockquote>"
      );
      blockquote = [];
    }

    function flushTable() {
      if (!tableRows.length) return;
      let html = "<table>";
      let bodyStart = 0;
      if (tableHasSep && tableRows.length >= 2 && tableRows[0] !== null) {
        html += "<thead><tr>" +
          tableRows[0].map(function (c) { return "<th>" + renderInline(c) + "</th>"; }).join("") +
          "</tr></thead>";
        bodyStart = 2;
      }
      const bodyData = tableRows.slice(bodyStart).filter(function (r) { return r !== null; });
      if (bodyData.length) {
        html += "<tbody>" +
          bodyData.map(function (r) {
            return "<tr>" + r.map(function (c) { return "<td>" + renderInline(c) + "</td>"; }).join("") + "</tr>";
          }).join("") +
          "</tbody>";
      }
      html += "</table>";
      out.push('<div class="markdownTableScroll">' + html + "</div>");
      tableRows = [];
      tableHasSep = false;
    }

    function openMathFence(endToken, firstLine) {
      flushPara();
      closeAllLists();
      flushBlockquote();
      flushTable();
      mathFence = true;
      mathFenceEnd = endToken;
      mathLines = firstLine ? [firstLine] : [];
    }

    function closeMathFence(lastLine) {
      if (lastLine) mathLines.push(lastLine);
      out.push(renderMath(mathLines.join("\n"), true));
      mathFence = false;
      mathFenceEnd = "";
      mathLines = [];
    }

    // Push a list item at a given indent. Handles nesting by comparing indent
    // to the current listStack top. `task` is null for a plain bullet, or
    // { checked: bool } for a "- [ ]" / "- [x]" item — in which case the <li>
    // is rendered as a checkbox but stays inside the current ul as a sibling.
    function pushListItem(type, indent, contentHtml, task, orderedStart) {
      // Pop lists that are deeper, or same-depth-but-different-type.
      while (listStack.length) {
        const top = listStack[listStack.length - 1];
        const deeper = top.indent > indent;
        const sameDepthDifferentType = top.indent === indent && top.type !== type;
        if (deeper || sameDepthDifferentType) {
          if (top.liOpen) out.push("</li>");
          out.push("</" + top.type + ">");
          listStack.pop();
        } else {
          break;
        }
      }
      // Open a new nested list if we're deeper than the current top (or stack empty).
      if (!listStack.length || listStack[listStack.length - 1].indent < indent) {
        // Parent's <li> stays open — the new list nests inside it.
        const start = type === "ol" && Number.isInteger(orderedStart) && orderedStart > 1
          ? ' start="' + orderedStart + '"'
          : "";
        out.push("<" + type + start + ">");
        listStack.push({ type: type, indent: indent, liOpen: false });
      }
      const top = listStack[listStack.length - 1];
      // Close the previous sibling <li> at the same level before opening a new one.
      if (top.liOpen) out.push("</li>");
      if (task) {
        out.push(
          '<li class="taskListItem"><input type="checkbox" disabled' +
            (task.checked ? " checked" : "") +
            "><span>" + contentHtml + "</span>"
        );
      } else {
        out.push("<li>" + contentHtml);
      }
      top.liOpen = true;
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      if (mathFence) {
        if (mathFenceEnd === "$$" && trimmed.endsWith("$$")) {
          closeMathFence(line.replace(/\$\$\s*$/, ""));
        } else if (mathFenceEnd === "\\]" && trimmed === "\\]") {
          closeMathFence("");
        } else if (mathFenceEnd === "\\]" && trimmed.endsWith("\\]")) {
          closeMathFence(line.replace(/\\]\s*$/, ""));
        } else {
          mathLines.push(line);
        }
        continue;
      }

      // Code fence body or boundary
      if (codeFence) {
        if (trimmed.startsWith("```")) {
          out.push(renderCodeFence(codeLines, codeFenceLang));
          codeLines = [];
          codeFenceLang = "";
          codeFence = false;
        } else {
          codeLines.push(line);
        }
        continue;
      }
      if (trimmed.startsWith("```")) {
        flushPara();
        closeAllLists();
        flushBlockquote();
        flushTable();
        codeFence = true;
        codeFenceLang = String((trimmed.match(/^```\s*([^\s`]*)/) || [])[1] || "").trim().toLowerCase();
        codeLines = [];
        continue;
      }

      const singleLineDollarMath = trimmed.match(/^\$\$\s*([\s\S]*?)\s*\$\$$/);
      if (trimmed !== "$$" && singleLineDollarMath) {
        flushPara();
        closeAllLists();
        flushBlockquote();
        flushTable();
        out.push(renderMath(singleLineDollarMath[1], true));
        continue;
      }
      if (trimmed.startsWith("$$")) {
        const firstLine = line.slice(line.indexOf("$$") + 2);
        openMathFence("$$", firstLine);
        continue;
      }
      const singleLineBracketMath = trimmed.match(/^\\\[\s*([\s\S]*?)\s*\\\]$/);
      if (trimmed !== "\\[" && singleLineBracketMath) {
        flushPara();
        closeAllLists();
        flushBlockquote();
        flushTable();
        out.push(renderMath(singleLineBracketMath[1], true));
        continue;
      }
      if (trimmed.startsWith("\\[")) {
        const firstLine = line.slice(line.indexOf("\\[") + 2);
        openMathFence("\\]", firstLine);
        continue;
      }

      // Table rows
      if (isTableRow(trimmed)) {
        flushPara();
        closeAllLists();
        flushBlockquote();
        if (isTableSep(trimmed)) {
          tableHasSep = true;
          tableRows.push(null);
        } else {
          tableRows.push(parseTableCells(trimmed));
        }
        continue;
      } else if (tableRows.length) {
        flushTable();
      }

      // Blank line — ends paragraph / blockquote but does NOT close open
      // lists; subsequent indented text is treated as list-item continuation.
      if (!trimmed) {
        flushPara();
        flushBlockquote();
        continue;
      }

      // Heading
      const hm = line.match(/^(#{1,6})\s+(.*)$/);
      if (hm) {
        flushPara();
        closeAllLists();
        flushBlockquote();
        const level = hm[1].length;
        out.push("<h" + level + ">" + renderInline(hm[2]) + "</h" + level + ">");
        continue;
      }

      // Horizontal rule
      if (/^[-*_]{3,}$/.test(trimmed)) {
        flushPara();
        closeAllLists();
        flushBlockquote();
        out.push("<hr>");
        continue;
      }

      // Blockquote
      const qm = line.match(/^\s*>\s?(.*)$/);
      if (qm) {
        flushPara();
        closeAllLists();
        blockquote.push(qm[1]);
        continue;
      } else if (blockquote.length) {
        flushBlockquote();
      }

      // Task list item — same indent level as a regular bullet, rendered as
      // a checkbox. The "- " marker is optional: "- [ ] foo", "[x] foo", etc.
      const taskm = line.match(/^(\s*)(?:[-*+]\s+)?\[( |x|X)\]\s+(.*)$/);
      if (taskm) {
        flushPara();
        const checked = (taskm[2] || "").toLowerCase() === "x";
        pushListItem("ul", indentCols(taskm[1]), renderInline(taskm[3]), { checked: checked });
        continue;
      }

      // Unordered list item
      const ulm = line.match(/^(\s*)[-*+]\s+(.*)$/);
      if (ulm) {
        flushPara();
        pushListItem("ul", indentCols(ulm[1]), renderInline(ulm[2]), null);
        continue;
      }

      // Ordered list item — allow "1. [ ] foo" as a checkbox too.
      const olm = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
      if (olm) {
        flushPara();
        const inner = olm[3];
        const orderedStart = Number(olm[2]);
        const tm = inner.match(/^\[( |x|X)\]\s+(.*)$/);
        if (tm) {
          pushListItem("ol", indentCols(olm[1]), renderInline(tm[2]), { checked: tm[1].toLowerCase() === "x" }, orderedStart);
        } else {
          pushListItem("ol", indentCols(olm[1]), renderInline(inner), null, orderedStart);
        }
        continue;
      }

      // List-item continuation: a non-list line that is indented deeper than
      // the current innermost list keeps that list open and appends to the
      // currently-open <li> as a soft break.
      if (listStack.length) {
        const top = listStack[listStack.length - 1];
        const leadingWs = (line.match(/^(\s*)/) || ["", ""])[1];
        const indent = indentCols(leadingWs);
        if (top.liOpen && indent > top.indent) {
          flushPara();
          out.push("<br>" + renderInline(line.substring(leadingWs.length)));
          continue;
        }
        // Shallow non-list line → lists end here.
        closeAllLists();
      }
      para.push(line);
    }

    // EOF flushes
    flushPara();
    closeAllLists();
    flushBlockquote();
    flushTable();
    if (codeFence) {
      out.push(renderCodeFence(codeLines, codeFenceLang, false));
    }
    if (mathFence) out.push(renderMath(mathLines.join("\n"), true));
    const html = out.join("");
    return options?.linkifyResources !== false && typeof global.PortalResourceChips?.linkifyHtml === "function"
      ? global.PortalResourceChips.linkifyHtml(html)
      : html;
    } finally {
      activeRenderOptions = previousRenderOptions;
    }
  }

  global.MD = Object.freeze({
    render: render,
    renderInline: renderInline,
    esc: esc,
    ensureKatex: ensureKatex,
    registerImagePathResolver: registerImagePathResolver,
  });
})(typeof window !== "undefined" ? window : this);
