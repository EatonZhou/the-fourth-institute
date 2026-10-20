(() => {
  "use strict";

  if (window.PortalPreviewRenderers) return;
  if (!window.PortalPreview || !window.PortalPdfPreview) {
    throw new Error("Preview runtime and PDF renderer must load before portal_preview_renderers.js");
  }

  const registrations = [];
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);

  function versionedUrl(url, document) {
    const clean = String(url || "").trim();
    const version = String(document.version || "").trim();
    if (!clean || !version) return clean;
    return `${clean}${clean.includes("?") ? "&" : "?"}_preview_version=${encodeURIComponent(version)}`;
  }

  function previewUrl(document, context) {
    return versionedUrl(
      document.previewUrl
      || document.rawUrl
      || context.rawUrl?.(document)
      || "",
      document,
    );
  }

  function copyDocumentAppearance(sourceDocument, targetDocument) {
    targetDocument.documentElement.className = sourceDocument.documentElement.className;
    targetDocument.body.className = sourceDocument.body.className;
    for (const source of [sourceDocument.documentElement, sourceDocument.body]) {
      for (const attribute of source.attributes) {
        if (attribute.name.startsWith("data-")) {
          const target = source === sourceDocument.body ? targetDocument.body : targetDocument.documentElement;
          target.setAttribute(attribute.name, attribute.value);
        }
      }
    }
    sourceDocument.head.querySelectorAll('link[rel~="stylesheet"], style').forEach(node => {
      const clone = targetDocument.importNode(node, true);
      if (clone.tagName === "LINK") clone.href = node.href;
      targetDocument.head.append(clone);
    });
  }

  function openRenderedDocument(root, options = {}) {
    if (!(root instanceof Element)) return false;
    const target = window.open("about:blank", "_blank");
    if (!target) return false;
    try { target.opener = null; } catch (_error) { /* Browser policy may already isolate it. */ }

    const targetDocument = target.document;
    targetDocument.head.replaceChildren();
    targetDocument.body.replaceChildren();
    copyDocumentAppearance(document, targetDocument);
    targetDocument.title = String(options.title || document.title || window.PortalI18n.t("预览"));

    const main = targetDocument.createElement("main");
    main.className = "portalPreviewLargeView";
    const preview = targetDocument.importNode(root, true);
    preview.removeAttribute("id");
    preview.classList.add("portalPreviewSurface");
    main.append(preview);
    targetDocument.body.append(main);
    target.focus?.();
    return true;
  }

  function contentText(document) {
    if (document.contentKind !== "json") return String(document.content || "");
    try { return JSON.stringify(JSON.parse(document.content || ""), null, 2); } catch (_error) { return String(document.content || ""); }
  }

  const CODE_KEYWORDS = Object.freeze({
    python: new Set("and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield".split(" ")),
    shell: new Set("case coproc do done elif else esac fi for function if in select then time until while".split(" ")),
    json: new Set(["false", "null", "true"]),
    jsonl: new Set(["false", "null", "true"]),
    yaml: new Set(["false", "null", "true", "yes", "no", "on", "off"]),
  });

  function codeLanguage(document) {
    if (document.contentKind === "json" || document.contentKind === "jsonl") return document.contentKind;
    const aliases = { yml: "yaml", ndjson: "jsonl", "x-ndjson": "jsonl" };
    const explicitValue = String(document.language || "").trim().toLowerCase();
    const explicit = aliases[explicitValue] || explicitValue;
    if (explicit) return explicit;
    const mediaType = String(document.mediaType || "").split(";", 1)[0].trim().toLowerCase();
    if (mediaType === "application/xml" || mediaType === "text/xml" || mediaType.endsWith("+xml")) return "xml";
    if (["application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"].includes(mediaType)) return "yaml";
    const path = String(document.path || document.title || "").toLowerCase();
    if (path.endsWith(".json")) return "json";
    if (path.endsWith(".jsonl") || path.endsWith(".ndjson")) return "jsonl";
    if ([".xml", ".xsd", ".xsl", ".xslt"].some(extension => path.endsWith(extension))) return "xml";
    if (path.endsWith(".yaml") || path.endsWith(".yml")) return "yaml";
    if (path.endsWith(".py")) return "python";
    if (path.endsWith(".sh")) return "shell";
    return "plain";
  }

  function codeToken(className, value) {
    return window.PortalI18n.html`<span class="${className}">${esc(value)}</span>`;
  }

  function highlightGenericLine(line, language) {
    const source = String(line || "");
    const keywords = CODE_KEYWORDS[language] || new Set();
    let output = "";
    let index = 0;
    while (index < source.length) {
      const character = source[index];
      if (character === "#" && ["python", "shell", "yaml"].includes(language)) {
        output += codeToken("codeTokenComment", source.slice(index));
        break;
      }
      if (character === '"' || character === "'") {
        const triple = language === "python" && source.slice(index, index + 3) === character.repeat(3);
        const delimiter = triple ? character.repeat(3) : character;
        let end = index + delimiter.length;
        while (end < source.length) {
          if (source[end] === "\\") {
            end += 2;
            continue;
          }
          if (source.slice(end, end + delimiter.length) === delimiter) {
            end += delimiter.length;
            break;
          }
          end += 1;
        }
        const tokenClass = ["json", "jsonl"].includes(language) && /^\s*:/.test(source.slice(end))
          ? "codeTokenProperty"
          : "codeTokenString";
        output += codeToken(tokenClass, source.slice(index, end));
        index = end;
        continue;
      }
      if (language === "shell" && character === "$") {
        const variable = source.slice(index).match(/^\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!_-])/);
        if (variable) {
          output += codeToken("codeTokenProperty", variable[0]);
          index += variable[0].length;
          continue;
        }
      }
      if (/[A-Za-z_]/.test(character)) {
        const identifier = source.slice(index).match(/^[A-Za-z_][A-Za-z0-9_]*/)?.[0] || character;
        output += keywords.has(identifier) ? codeToken("codeTokenKeyword", identifier) : esc(identifier);
        index += identifier.length;
        continue;
      }
      if (/\d/.test(character) || (character === "-" && /\d/.test(source[index + 1] || ""))) {
        const number = source.slice(index).match(/^-?(?:0[xob][0-9A-Fa-f_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)/)?.[0] || character;
        output += codeToken("codeTokenNumber", number);
        index += number.length;
        continue;
      }
      output += esc(character);
      index += 1;
    }
    return output;
  }

  function commentIndex(source, marker) {
    let quote = "";
    for (let index = 0; index < source.length; index += 1) {
      const character = source[index];
      if (quote) {
        if (character === "\\") index += 1;
        else if (character === quote) quote = "";
        continue;
      }
      if (character === '"' || character === "'") quote = character;
      else if (source.startsWith(marker, index)) return index;
    }
    return -1;
  }

  function highlightYamlLine(line) {
    const source = String(line || "");
    const commentAt = commentIndex(source, "#");
    const body = commentAt >= 0 ? source.slice(0, commentAt) : source;
    const comment = commentAt >= 0 ? source.slice(commentAt) : "";
    const keyMatch = body.match(/^(\s*(?:-\s+)?)([^'"{}\[\],][^:]*?)(\s*:)(.*)$/);
    const rendered = keyMatch && keyMatch[2].trim()
      ? esc(keyMatch[1]) + codeToken("codeTokenProperty", keyMatch[2]) + esc(keyMatch[3]) + highlightGenericLine(keyMatch[4], "yaml")
      : highlightGenericLine(body, "yaml");
    return rendered + (comment ? codeToken("codeTokenComment", comment) : "");
  }

  function highlightXmlTag(tag) {
    if (tag.startsWith("<!--")) return codeToken("codeTokenComment", tag);
    if (tag.startsWith("<![CDATA[")) return codeToken("codeTokenString", tag);
    let output = "";
    let index = 0;
    let tagNameSeen = false;
    while (index < tag.length) {
      const character = tag[index];
      if (character === '"' || character === "'") {
        let end = index + 1;
        while (end < tag.length && tag[end] !== character) end += tag[end] === "\\" ? 2 : 1;
        end = Math.min(tag.length, end + 1);
        output += codeToken("codeTokenString", tag.slice(index, end));
        index = end;
        continue;
      }
      if (/[A-Za-z_:]/.test(character)) {
        const identifier = tag.slice(index).match(/^[A-Za-z_:][A-Za-z0-9_.:-]*/)?.[0] || character;
        output += codeToken(tagNameSeen ? "codeTokenProperty" : "codeTokenTag", identifier);
        tagNameSeen = true;
        index += identifier.length;
        continue;
      }
      output += esc(character);
      index += 1;
    }
    return output;
  }

  function highlightXmlLine(line) {
    const source = String(line || "");
    let output = "";
    let index = 0;
    const tags = /<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<[^>]*>/g;
    for (const match of source.matchAll(tags)) {
      output += esc(source.slice(index, match.index));
      output += highlightXmlTag(match[0]);
      index = Number(match.index) + match[0].length;
    }
    return output + esc(source.slice(index));
  }

  function highlightCodeLine(line, language) {
    if (language === "xml") return highlightXmlLine(line);
    if (language === "yaml") return highlightYamlLine(line);
    return highlightGenericLine(line, language);
  }

  function lineRowsHtml(document, context) {
    const source = contentText(document) || window.PortalI18n.t("（空文件）");
    const basePath = document.path || "";
    const language = ["code", "json", "jsonl"].includes(document.contentKind) ? codeLanguage(document) : "";
    const rows = source.split("\n").map((line, index) => {
      const number = index + 1;
      const body = document.contentKind === "markdown"
        ? (String(line).trim() ? (context.renderMarkdown?.(line, { basePath }) || window.MD?.render?.(line, { basePath }) || esc(line)) : "&nbsp;")
        : language
          ? (highlightCodeLine(line, language) || "&nbsp;")
          : esc(line) || "&nbsp;";
      return window.PortalI18n.html`<div class="previewLine" data-preview-line="${number}" tabindex="-1"><div class="previewLineNo" aria-hidden="true">${number}</div><div class="previewLineContent">${body}</div></div>`;
    }).join("");
    return window.PortalI18n.html`<div class="previewLineTable${document.contentKind === "markdown" ? " previewMarkdownLines markdownBody" : ""}" data-preview-line-content${language ? ` data-preview-language="${esc(language)}"` : ""}>${rows}</div>`;
  }

  function splitMarkdownFrontMatter(content) {
    const source = String(content || "");
    const match = source.match(/^\uFEFF?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/);
    if (!match) return { body: source, metadata: "" };
    return {
      body: source.slice(match[0].length).replace(/^\r?\n/, ""),
      metadata: match[1],
    };
  }

  function markdownVisualHtml(document, context) {
    const source = splitMarkdownFrontMatter(document.content);
    const markdownOptions = { basePath: document.path };
    const renderMarkdown = value => context.renderMarkdown?.(value, markdownOptions)
      || window.MD?.render?.(value, markdownOptions)
      || esc(value);
    const body = `<article class="markdownBody">${renderMarkdown(source.body || window.PortalI18n.t("（空文件）"))}</article>`;
    if (!source.metadata) return body;
    const knowledgeMetadata = /^\s*kb_id\s*:/m.test(source.metadata);
    const metadata = source.metadata.split("\n").map(highlightYamlLine).join("\n");
    return `<details class="previewFrontMatter" data-preview-front-matter>
      <summary><span>${window.PortalI18n.t(knowledgeMetadata ? "知识库文件信息" : "文件信息")}</span><span class="previewFrontMatterType">YAML</span></summary>
      <pre class="previewFrontMatterContent"><code data-preview-language="yaml">${metadata}</code></pre>
    </details>${body}`;
  }

  function enhanceRenderedText(root, document, context) {
    window.PortalResourceChips?.enhance?.(root, {
      ...(context.resourceChipOptions || {}),
      onResourcePreview: context.onResourcePreview || context.onPreviewRequest,
    });
    context.bindLinks?.(root, document);
    return {};
  }

  function register(renderer) {
    registrations.push(window.PortalPreview.registerRenderer(renderer));
  }

  register({
    id: "remote-link",
    priority: 100,
    accepts: document => document.contentKind === "remote-link" || document.source === "remote_link_card",
    render(root, document) {
      const href = String(document.href || document.path || "").trim();
      const label = String(document.title || "").trim();
      let target = "";
      try {
        const resolved = new URL(href, window.location.href);
        if (["http:", "https:", "mailto:"].includes(resolved.protocol)) target = resolved.href;
      } catch (_error) {}
      let display = target || href;
      try { display = decodeURI(display); } catch (_error) {}
      const canOpen = Boolean(target);
      root.className = "previewBox markdownBody";
      root.innerHTML = window.PortalI18n.html`<div class="linkPreviewCard">
        <div class="linkPreviewLabel">Link</div>
        ${label && label !== display ? window.PortalI18n.html`<div>${esc(label)}</div>` : ""}
        <div class="linkPreviewUrl">${esc(display || window.PortalI18n.t("（空链接）"))}</div>
        <div class="linkPreviewHint">这是网页链接；Portal 保持当前工作区并允许单独打开。</div>
        <div class="linkPreviewActions">
          ${canOpen ? '<button class="portalButton is-primary" type="button" data-preview-link-open>Open</button>' : ""}
          <button class="portalButton" type="button" data-preview-link-copy>Copy</button>
        </div>
      </div>`;
      root.querySelector("[data-preview-link-copy]")?.addEventListener("click", () => window.PortalUI.copyText(target || href, "Link"));
      root.querySelector("[data-preview-link-open]")?.addEventListener("click", () => window.open(target, "_blank", "noopener,noreferrer"));
    },
  });

  register({
    id: "directory",
    priority: 90,
    accepts: document => document.contentKind === "directory",
    render(root, document) {
      root.className = "previewBox";
      const children = Array.isArray(document.children) ? document.children : [];
      root.innerHTML = window.PortalI18n.html`<pre>${esc(children.length ? children.map(child => {
        const kind = child.type || child.kind || "";
        const path = child.path || child.rel_path || child.name || "";
        return `${kind}  ${path}${child.size ? `  ${child.size}` : ""}`;
      }).join("\n") : window.PortalI18n.t("目录为空。"))}</pre>`;
    },
  });

  register({
    id: "pdf",
    priority: 80,
    accepts: document => document.contentKind === "pdf" || (document.contentKind === "office" && Boolean(document.previewUrl)),
    render(root, document, context) {
      const url = previewUrl(document, context);
      root.className = String(context.pdfRootClass || "previewBox pdfPreview");
      root.innerHTML = window.PortalI18n.html`<div class="${esc(context.pdfViewerClass || "previewPdfViewer")}" data-portal-pdf-viewer></div>`;
      const viewer = root.querySelector("[data-portal-pdf-viewer]");
      if (!url) {
        viewer.textContent = window.PortalI18n.t("当前文件没有可用的预览地址。");
        return {};
      }
      const controller = window.PortalPdfPreview.render({
        root: viewer,
        url,
        title: document.title || document.path,
        version: document.version,
        statusClass: context.pdfStatusClass || "previewPdfStatus",
        pageClass: context.pdfPageClass || "previewPdfPage",
        textLayerClass: context.pdfTextLayerClass || "textLayer",
        minPageWidth: context.pdfMinPageWidth,
        maxPageWidth: context.pdfMaxPageWidth,
        minScale: context.pdfMinScale ?? 0.2,
        maxScale: context.pdfMaxScale ?? 4,
        scaleMultiplier: 1,
        horizontalPadding: context.pdfHorizontalPadding,
        outputScale: context.pdfOutputScale,
        onPage: context.onPdfPage,
        onReady: context.onPdfReady,
        onError: context.onPdfError,
      });
      const zoom = window.PortalPreview.mountZoom({
        root,
        viewport: viewer,
        min: context.previewZoomMin ?? 0.5,
        max: context.previewZoomMax ?? 4,
        step: context.previewZoomStep ?? 0.1,
        enabled: false,
        captureAnchor: point => controller.captureAnchor(point),
        retargetAnchor: (anchor, point) => controller.retargetAnchor(anchor, point),
        restoreAnchor: anchor => controller.restoreAnchor(anchor),
        restoreOnChange: false,
        onChange: (value, { point, anchor }) => controller.previewGestureScale({
          scaleMultiplier: value,
          point,
          anchor,
        }),
        onCommit: async (value, { anchor, restoreAnchor }) => {
          controller.commitGestureScale({ scaleMultiplier: value });
          if (anchor) restoreAnchor(anchor);
          await controller.redraw();
        },
        onError: context.onPdfError,
      });
      const promise = controller.promise.then(pdf => {
        zoom.setEnabled(Boolean(pdf));
        return pdf;
      });
      return {
        dispose() {
          zoom.dispose();
          controller.cancel();
        },
        promise,
      };
    },
  });

  register({
    id: "media",
    priority: 70,
    accepts: document => ["image", "video", "audio"].includes(document.contentKind),
    render(root, document, context) {
      const url = previewUrl(document, context);
      root.className = "previewBox mediaPreview";
      if (!url) {
        root.textContent = window.PortalI18n.t("当前媒体文件没有可用的预览地址。");
        return;
      }
      if (document.contentKind === "image") {
        // Standalone media follows the zoom stage, not inline Markdown size limits.
        root.innerHTML = window.PortalI18n.html`<div class="portalPreviewZoomViewport" data-preview-zoom-viewport><div class="portalPreviewImageStage" data-preview-image-stage><img class="previewMedia" src="${esc(url)}" alt="${esc(document.title)}" title="${esc(document.title)}" data-portal-preview-image="${esc(url)}" /></div></div>`;
        const viewport = root.querySelector("[data-preview-zoom-viewport]");
        const stage = root.querySelector("[data-preview-image-stage]");
        const image = root.querySelector("[data-portal-preview-image]");
        let imageReady = false;
        let activeZoom = 1;
        const stableCssPixel = value => Math.round(Math.max(1, value) * 64) / 64;
        const applyScale = value => {
          activeZoom = value;
          if (!imageReady || !image.naturalWidth || !image.naturalHeight) return false;
          const viewportBounds = viewport.getBoundingClientRect();
          const availableWidth = Math.max(1, viewportBounds.width);
          const availableHeight = Math.max(1, viewportBounds.height);
          const fitScale = Math.min(
            1,
            availableWidth / image.naturalWidth,
            availableHeight / image.naturalHeight,
          );
          const nextWidth = `${stableCssPixel(image.naturalWidth * fitScale * value)}px`;
          const nextHeight = `${stableCssPixel(image.naturalHeight * fitScale * value)}px`;
          const changed = stage.style.width !== nextWidth || stage.style.height !== nextHeight;
          if (stage.style.width !== nextWidth) stage.style.width = nextWidth;
          if (stage.style.height !== nextHeight) stage.style.height = nextHeight;
          return changed;
        };
        const zoom = window.PortalPreview.mountZoom({
          root,
          viewport,
          min: context.previewZoomMin ?? 0.25,
          max: context.previewZoomMax ?? 6,
          step: context.previewZoomStep ?? 0.1,
          enabled: false,
          onChange: applyScale,
        });
        const resizeFrames = window.PortalPreview.createFrameCoalescer(
          () => applyScale(activeZoom),
        );
        const markReady = () => {
          if (imageReady) return;
          imageReady = true;
          zoom.setEnabled(true);
          applyScale(activeZoom);
        };
        image.addEventListener("load", markReady, { once: true });
        if (image.complete && image.naturalWidth) markReady();
        const resizeObserver = typeof ResizeObserver === "function"
          ? new ResizeObserver(() => resizeFrames.schedule(activeZoom))
          : null;
        if (resizeObserver) {
          try {
            resizeObserver.observe(viewport, { box: "border-box" });
          } catch (_error) {
            resizeObserver.observe(viewport);
          }
        }
        window.PortalImageEditor?.bind(root, { title: document.title, editable: false });
        return {
          dispose() {
            image.removeEventListener("load", markReady);
            resizeObserver?.disconnect?.();
            resizeFrames.dispose();
            zoom.dispose();
          },
        };
      } else if (document.contentKind === "video") {
        root.innerHTML = window.PortalI18n.html`<video class="previewMedia previewVideo" src="${esc(url)}" controls preload="metadata"></video>`;
      } else {
        root.innerHTML = window.PortalI18n.html`<audio class="previewMedia previewAudio" src="${esc(url)}" controls preload="metadata"></audio>`;
      }
    },
  });

  register({
    id: "text",
    priority: 50,
    accepts: document => window.PortalPreviewContract.isTextDocument(document),
    render(root, document, context) {
      const language = ["code", "json", "jsonl"].includes(document.contentKind) ? codeLanguage(document) : "";
      root.className = document.contentKind === "markdown"
        ? "previewBox markdownBody"
        : language
          ? "previewBox codePreview"
          : "previewBox";
      if (language) root.dataset.previewLanguage = language;
      if (document.contentKind === "markdown" && context.markdownLayout !== "lines") {
        root.innerHTML = markdownVisualHtml(document, context);
      } else {
        root.innerHTML = lineRowsHtml(document, context);
      }
      return enhanceRenderedText(root, document, context);
    },
  });

  register({
    id: "unsupported",
    priority: -1000,
    accepts: () => true,
    render(root, document, context) {
      root.className = "previewBox";
      const url = document.downloadUrl || document.rawUrl || context.rawUrl?.(document) || "";
      const message = document.previewUnavailableReason
        || document.preview_unavailable_reason
        || context.unsupportedText
        || window.PortalI18n.t("当前文件种类不支持预览。");
      root.innerHTML = window.PortalI18n.html`<div class="previewWelcome"><span data-portal-icon="file" aria-hidden="true"></span><span>${esc(message)}</span>${url ? window.PortalI18n.html`<a class="portalButton" href="${esc(url)}" download>下载文件</a>` : ""}</div>`;
    },
  });

  window.PortalPreviewRenderers = Object.freeze({
    dispose() {
      registrations.splice(0).forEach(unregister => unregister());
    },
    openRenderedDocument,
    previewUrl,
  });
})();
