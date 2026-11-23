// Knowledge board: a detail's files, the files its links name and the file tree.

function intrinsicDetailFiles() {
  const item = state.selected || {};
  const files = Array.isArray(item.files) ? item.files : [];
  if (files.length) return files;
  return item.kb_id ? [{
    address: item.kb_id,
    name: item.filename || "entry.md",
    relative_path: item.filename || "entry.md",
    kind: "markdown",
    primary: true,
  }] : [];
}

function detailFiles() {
  return [
    ...intrinsicDetailFiles(),
    ...state.detailLinkedFiles.values(),
  ];
}

function normalizeDetailRelativePath(value) {
  const parts = [];
  for (const part of String(value || "").replaceAll("\\", "/").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (parts.length) parts.pop();
      continue;
    }
    parts.push(part);
  }
  return parts.join("/");
}

function detailFileByPath(path) {
  const targetPath = String(path || "").replaceAll("\\", "/");
  if (!targetPath) return null;
  const files = intrinsicDetailFiles();
  const current = files.find(file =>
    String(file.address || "") === String(state.detailSelectedFileId || "")
  );
  if (!current) return null;
  const currentRelativePath = normalizeDetailRelativePath(
    current?.relative_path || "",
  );
  const currentAbsolutePath = String(state.detailPath || "").replaceAll("\\", "/");
  let contentRoot = "";
  if (
    currentRelativePath
    && currentAbsolutePath.endsWith(`/${currentRelativePath}`)
  ) {
    contentRoot = currentAbsolutePath.slice(0, -(currentRelativePath.length + 1));
  } else if (
    currentRelativePath
    && currentAbsolutePath === currentRelativePath
  ) {
    contentRoot = "";
  } else if (currentAbsolutePath) {
    contentRoot = currentAbsolutePath.slice(0, currentAbsolutePath.lastIndexOf("/"));
  }
  const targetRelativePath = contentRoot && targetPath.startsWith(`${contentRoot}/`)
    ? targetPath.slice(contentRoot.length + 1)
    : "";
  if (!targetRelativePath) return null;
  return files.find(file =>
    normalizeDetailRelativePath(file.relative_path || "") === targetRelativePath
  ) || null;
}

function skillInternalFileTarget(rawTarget) {
  if (String((state.selected || {}).type || "") !== "skill") return null;
  const targetText = String(rawTarget || "").trim();
  if (!/^\.\/(?:[^/?#\s`]+\/)*[^/?#\s`]+\.md$/i.test(targetText)) return null;
  const files = intrinsicDetailFiles();
  const current = files.find(file =>
    String(file.address || "") === String(state.detailSelectedFileId || "")
  );
  const currentPath = String(current?.relative_path || "")
    .replaceAll("\\", "/")
    .replace(/^\/+|\/+$/g, "");
  const baseParts = currentPath.split("/").filter(Boolean).slice(0, -1);
  const targetParts = targetText.slice(2).split("/").filter(Boolean);
  const relativePath = [...baseParts, ...targetParts].join("/");
  const target = files.find(file =>
    String(file.relative_path || "")
      .replaceAll("\\", "/")
      .replace(/^\/+|\/+$/g, "") === relativePath
  );
  if (
    !target
    || String(target.address || "") === String(state.detailSelectedFileId || "")
  ) return null;
  return target;
}

function scrollToSkillSection(root, section) {
  const elements = root.querySelectorAll("[data-md-section], h1, h2, h3, h4, h5, h6");
  const used = new Set();
  for (const element of elements) {
    let slug = element.dataset.mdSection;
    if (!slug) {
      const base = element.textContent.trim().toLowerCase()
        .replace(/[^\p{L}\p{M}\p{N}\s_-]/gu, "").replace(/\s+/g, "-");
      slug = base;
      for (let n = 1; used.has(slug); n++) slug = `${base}-${n}`;
    }
    used.add(slug);
    if (slug === section) {
      (element.closest("li") || element).scrollIntoView({ block: "start" });
      return;
    }
  }
}

function bindSkillInternalFileLinks(root) {
  if (!root || String((state.selected || {}).type || "") !== "skill") return;
  const files = intrinsicDetailFiles();
  const current = files.find(file => file.address === state.detailSelectedFileId);
  root.querySelectorAll(".markdownBody a[href*='#']").forEach(link => {
    const href = link.getAttribute("href");
    const hash = href.indexOf("#");
    const path = href.slice(0, hash);
    if (!current || !href.slice(hash + 1) || (path && !/^(?![\/\\])[^:?#\\]+\.md$/i.test(path))) return;
    let filePath, section;
    try {
      const packageRoot = new URL("/skill/", window.location.origin);
      const currentUrl = new URL(current.relative_path, packageRoot);
      const target = new URL(path, currentUrl);
      if (!target.pathname.startsWith(packageRoot.pathname)) return;
      filePath = decodeURIComponent(target.pathname.slice(packageRoot.pathname.length));
      section = decodeURIComponent(href.slice(hash + 1));
    } catch { return; }
    const file = files.find(item => item.relative_path === filePath);
    if (!file) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skillInternalFileLink";
    button.append(...link.childNodes);
    button.addEventListener("click", () => {
      if (file.address === current.address) scrollToSkillSection(root, section);
      else if (state.detailReadOnly) openDetailLinkedFile({});
      else openDetailFile(file, { section });
    });
    link.replaceWith(button);
  });
  root.querySelectorAll(".markdownBody code").forEach(code => {
    if (code.closest("pre, button, a")) return;
    const file = skillInternalFileTarget(code.textContent);
    if (!file) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skillInternalFileLink";
    button.dataset.skillFileAddress = String(file.address || "");
    button.setAttribute(
      "aria-label",
      window.PortalI18n.text`打开 Skill 文件 ${String(file.relative_path || "")}`,
    );
    button.appendChild(code.cloneNode(true));
    button.addEventListener("click", () => {
      if (state.detailReadOnly) {
        window.portalToast?.show(
          "只读演示不会切换文件；退出演示后可由你主动打开。",
          { tone: "info" },
        );
        return;
      }
      openDetailFile(file);
    });
    code.replaceWith(button);
  });
}

function detailLinkedFile(descriptor, metadata = {}) {
  const path = String(descriptor?.path || "").trim();
  if (!path) return null;
  const kind = String(metadata.kind || descriptor.kind || window.PortalFileLinks?.kindForPath?.(path) || "text");
  return {
    address: `linked-file:${path}`,
    linked: true,
    path,
    name: String(descriptor.name || window.PortalFileLinks?.fileName?.(path) || "file"),
    relative_path: String(descriptor.name || window.PortalFileLinks?.fileName?.(path) || "file"),
    kind,
    primary: false,
  };
}

function detailFilePathResolver(parsed) {
  if (!window.PortalFileLinks) return null;
  const absolutePaths = [...new Set(
    DETAIL_LINK_FIELDS.flatMap(key => parsed?.meta?.links?.[key] || [])
      .map(value => window.PortalFileLinks.describe(value))
      .filter(Boolean)
      .map(descriptor => descriptor.path),
  )];
  if (!absolutePaths.length) return null;
  return descriptor => {
    const rawPath = window.PortalFileLinks.normalizePath(descriptor?.raw || "");
    if (!rawPath || rawPath.startsWith("/") || rawPath.startsWith("~/")) {
      return descriptor?.path || "";
    }
    const relativePath = rawPath.replace(/^\.\/+/, "");
    const suffixMatches = absolutePaths.filter(path =>
      path === relativePath || path.endsWith(`/${relativePath}`)
    );
    if (suffixMatches.length === 1) return suffixMatches[0];
    const name = window.PortalFileLinks.fileName(relativePath);
    const nameMatches = absolutePaths.filter(path =>
      window.PortalFileLinks.fileName(path) === name
    );
    return nameMatches.length === 1 ? nameMatches[0] : descriptor?.path || "";
  };
}

async function registerDetailLinkedFiles(descriptors) {
  if (!window.PortalFileLinks || !Array.isArray(descriptors) || !descriptors.length) return;
  const resourceId = state.detailFileId;
  const discoverySequence = state.detailLinkDiscoverySequence;
  const currentPath = String(state.detailPath || "");
  const pendingPaths = state.detailLinkedFileProbes;
  const candidates = [...new Map(descriptors.map(descriptor => [
    String(descriptor?.path || ""),
    descriptor,
  ])).values()].filter(descriptor => {
    const path = String(descriptor?.path || "");
    return path
      && String(descriptor?.kind || "") !== "binary"
      && path !== currentPath
      && !detailFileByPath(path)
      && !state.detailLinkedFiles.has(path)
      && !pendingPaths.has(path);
  });
  if (!candidates.length) return;
  candidates.forEach(descriptor => pendingPaths.add(descriptor.path));
  // Text discovery yields candidates, not filesystem facts (e.g. H.264/H.265).
  // Publish only files confirmed by the actor-bound metadata probe.
  let inspected = [];
  try {
    inspected = await Promise.all(candidates.map(async descriptor => {
      try {
        const metadata = await window.PortalFileLinks.probe(descriptor.path);
        return metadata ? detailLinkedFile(descriptor, metadata) : null;
      } catch {
        return null;
      }
    }));
  } finally {
    candidates.forEach(descriptor => pendingPaths.delete(descriptor.path));
  }
  if (
    resourceId !== state.detailFileId
    || discoverySequence !== state.detailLinkDiscoverySequence
  ) return;
  let changed = false;
  inspected.filter(Boolean).forEach(file => {
    if (detailFileByPath(file.path)) return;
    const current = state.detailLinkedFiles.get(file.path);
    if (
      current
      && current.kind === file.kind
    ) return;
    state.detailLinkedFiles.set(file.path, file);
    changed = true;
  });
  if (changed) renderDetailFileTree();
}

async function openDetailLinkedFile(request) {
  if (state.detailReadOnly) {
    window.PortalUI.toast(
      window.PortalI18n.t("只读演示不会打开关联文件；退出演示后可由你主动打开。"),
      "info",
      { key: "knowledge-live-tour-file-blocked" },
    );
    return;
  }
  const path = String(request?.path || request?.href || "").trim();
  if (!path) return;
  const intrinsicFile = detailFileByPath(path);
  if (intrinsicFile) {
    await openDetailFile(intrinsicFile);
    return;
  }
  let file = state.detailLinkedFiles.get(path);
  if (!file) {
    file = detailLinkedFile({
      ...request,
      path,
      name: request?.name || window.PortalFileLinks?.fileName?.(path),
    });
    if (!file) return;
    state.detailLinkedFiles.set(path, file);
    renderDetailFileTree();
  }
  await openDetailFile(file);
}

// A Skill may group exact filenames and directory prefixes in its main document.
// These paths affect browsing only. Opening, saving and copying use the file address.
function skillFileGroups(markdown) {
  const raw = splitDetailMarkdown(markdown).rawFrontmatter;
  const groups = new Map();
  let inMetadata = false, inGroups = false;
  for (const line of raw.split(/\r?\n/)) {
    if (/^\S/.test(line)) inMetadata = line.trimEnd() === "metadata:";
    if (!inMetadata) continue;
    if (/^  \S/.test(line)) inGroups = line.trimEnd() === "  file_groups:";
    if (!inGroups) continue;
    const match = line.match(/^    ([^:]+):\s*(\[.*\])$/);
    if (!match) continue;
    for (const path of parseYamlListValue(match[2])) groups.set(path, match[1].trim());
  }
  return groups;
}

function detailTreePath(file) {
  const path = String(file?.relative_path || "").replace(/^\/+|\/+$/g, "");
  const groups = state.detailSkillGroups;
  if (file?.primary || !groups?.size) return path;
  const parts = path.split("/");
  if (groups.has(path)) return groups.get(path) + "/" + parts.at(-1);
  for (let i = parts.length - 1; i > 0; i--) {
    const prefix = parts.slice(0, i).join("/") + "/";
    if (groups.has(prefix)) return groups.get(prefix) + "/" + parts.slice(i).join("/");
  }
  return path;
}

function buildDetailFileTree(files) {
  const root = { dirs: new Map(), files: [] };
  for (const file of files) {
    const rawPath = detailTreePath(file);
    if (!rawPath) continue;
    const parts = rawPath.split("/").filter(Boolean);
    let node = root;
    let prefix = "";
    for (const part of parts.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${part}` : part;
      if (!node.dirs.has(part)) node.dirs.set(part, { path: prefix, dirs: new Map(), files: [] });
      node = node.dirs.get(part);
    }
    node.files.push({ ...file, path: rawPath });
  }
  return root;
}

function renderDetailFileTree() {
  const files = intrinsicDetailFiles();
  const linkedFiles = [...state.detailLinkedFiles.values()];
  const item = state.selected || {};
  el.mFiles.innerHTML = "";
  const rootName = String(item.title || item.root_path || item.filename || "Knowledge entry").split("/").filter(Boolean).pop() || "Knowledge entry";
  const root = document.createElement("div");
  root.className = "knowledgeFileRoot";
  root.innerHTML = window.PortalI18n.html`<span class="knowledgeFileName"></span><span class="knowledgeFileKind">${files.length + linkedFiles.length}</span>`;
  root.querySelector(".knowledgeFileName").textContent = rootName;
  el.mFiles.appendChild(root);

  const renderNode = (node, parent, depth = 0) => {
    const dirs = [...node.dirs.values()].sort((a, b) => a.path.localeCompare(b.path));
    for (const dir of dirs) {
      const expanded = state.detailExpandedFolders.has(dir.path);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "knowledgeFileFolder";
      button.style.paddingLeft = `${5 + depth * 11}px`;
      button.innerHTML = window.PortalI18n.html`<span class="knowledgeFileToggle${expanded ? "" : " collapsed"}">▾</span><span class="knowledgeFileName"></span>`;
      button.querySelector(".knowledgeFileName").textContent = dir.path.split("/").pop() || dir.path;
      button.addEventListener("click", () => {
        if (state.detailExpandedFolders.has(dir.path)) state.detailExpandedFolders.delete(dir.path);
        else state.detailExpandedFolders.add(dir.path);
        renderDetailFileTree();
      });
      parent.appendChild(button);
      if (expanded) {
        const children = document.createElement("div");
        children.className = "knowledgeFileChildren";
        parent.appendChild(children);
        renderNode(dir, children, depth + 1);
      }
    }
    for (const file of [...node.files].sort((a, b) => a.path.localeCompare(b.path))) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "knowledgeFileLeaf";
      button.style.paddingLeft = `${5 + depth * 11}px`;
      button.classList.toggle("active", String(file.address || "") === String(state.detailSelectedFileId || ""));
      button.innerHTML = window.PortalI18n.html`<span>•</span><span class="knowledgeFileName"></span><span class="knowledgeFileKind"></span>`;
      button.querySelector(".knowledgeFileName").textContent = file.path.split("/").pop() || file.name || "file";
      button.querySelector(".knowledgeFileKind").textContent = file.primary ? "main" : (file.kind || "text");
      button.addEventListener("click", () => {
        if (state.detailReadOnly) {
          window.PortalUI.toast(
            window.PortalI18n.t("只读演示不会切换文件；退出演示后可由你主动打开。"),
            "info",
            { key: "knowledge-live-tour-file-blocked" },
          );
          return;
        }
        openDetailFile(file);
      });
      parent.appendChild(button);
    }
  };
  if (state.detailSkillGroups?.size) {
    renderNode(buildDetailFileTree(files.filter(file => file.primary)), el.mFiles);
    renderNode(buildDetailFileTree(files.filter(file => !file.primary)), el.mFiles);
  } else {
    renderNode(buildDetailFileTree(files), el.mFiles);
  }
  if (linkedFiles.length) {
    const linkedGroup = document.createElement("div");
    linkedGroup.className = "knowledgeLinkedFiles";
    for (const file of linkedFiles.sort((a, b) =>
      String(a.name || "").localeCompare(String(b.name || ""))
      || String(a.path || "").localeCompare(String(b.path || ""))
    )) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "knowledgeFileLeaf knowledgeLinkedFileLeaf";
      button.classList.toggle(
        "active",
        String(file.address || "") === String(state.detailSelectedFileId || ""),
      );
      button.title = String(file.path || "");
      button.innerHTML = window.PortalI18n.html`<span>•</span><span class="knowledgeFileName"></span>`;
      button.querySelector(".knowledgeFileName").textContent = file.name || "file";
      button.addEventListener("click", () => {
        if (state.detailReadOnly) {
          window.PortalUI.toast(
            window.PortalI18n.t("只读演示不会打开关联文件；退出演示后可由你主动打开。"),
            "info",
            { key: "knowledge-live-tour-file-blocked" },
          );
          return;
        }
        openDetailFile(file);
      });
      linkedGroup.appendChild(button);
    }
    el.mFiles.appendChild(linkedGroup);
  }
}
