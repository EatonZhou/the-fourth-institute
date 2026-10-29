(() => {
  "use strict";

  if (window.PortalArticleWorkspaceFileTree) return;

  // The file tree: loading the Article's files, files and resources linked
  // from the LaTeX source, new files and folders, and uploads.
  const DEPENDENCIES = Object.freeze([
    "$", "activeDocument", "articleAbsolutePath", "articleEntryDialog", "articleInternalPath",
    "articleStateHtml", "loadSemanticIndex", "openArticleFile", "openArticleLinkedFile",
    "openArticleResourcePreview", "renderSettings", "request", "showToast", "state",
  ]);

  function createFileTree(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspaceFileTree is missing: ${missing.join(", ")}`);
    const {
      $, activeDocument, articleAbsolutePath, articleEntryDialog, articleInternalPath, articleStateHtml,
      loadSemanticIndex, openArticleFile, openArticleLinkedFile, openArticleResourcePreview, renderSettings,
      request, showToast, state,
    } = dependencies;

    function treeFromEntries(entries) {
      const root = { name: "", path: "", type: "directory", children: new Map() };
      for (const entry of entries) {
        const parts = String(entry.path || "").split("/").filter(Boolean);
        let cursor = root;
        parts.forEach((part, index) => {
          const path = parts.slice(0, index + 1).join("/");
          if (!cursor.children.has(part)) {
            cursor.children.set(part, {
              name: part,
              path,
              type: index === parts.length - 1 ? entry.type : "directory",
              children: new Map(),
            });
          }
          cursor = cursor.children.get(part);
          if (index === parts.length - 1) Object.assign(cursor, entry);
        });
      }
      return root;
    }

    function visibleTreeEntries(node, depth = 0) {
      const children = [...node.children.values()].sort((left, right) => {
        if (left.type !== right.type) return left.type === "directory" ? -1 : 1;
        return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
      });
      return children.flatMap(child => [
        { ...child, depth },
        ...(child.type === "directory" && !state.collapsedDirectories.has(child.path)
          ? visibleTreeEntries(child, depth + 1) : []),
      ]);
    }

    function articleLinkedFile(descriptor, metadata = {}) {
      const path = String(descriptor?.path || "").trim();
      if (!path) return null;
      return {
        path,
        name: String(descriptor.name || window.PortalFileLinks?.fileName?.(path) || "file"),
        kind: String(metadata.kind || descriptor.kind || window.PortalFileLinks?.kindForPath?.(path) || "text"),
      };
    }

    async function registerArticleLinkedFiles(descriptors) {
      if (!window.PortalFileLinks || !Array.isArray(descriptors) || !descriptors.length) return;
      const articleId = state.selectedId;
      const pendingPaths = state.linkedFileProbes;
      const currentPath = articleAbsolutePath(activeDocument()?.path || "");
      const candidates = [...new Map(descriptors.map(descriptor => [
        String(descriptor?.path || ""),
        descriptor,
      ])).values()].filter(descriptor => {
        const path = String(descriptor?.path || "");
        return path
          && path !== currentPath
          && !articleInternalPath(path)
          && !state.linkedFiles.has(path)
          && !pendingPaths.has(path);
      });
      if (!candidates.length) return;
      candidates.forEach(descriptor => pendingPaths.add(descriptor.path));
      let inspected;
      try {
        inspected = await Promise.all(candidates.map(async descriptor => {
          try {
            const metadata = await window.PortalFileLinks.probe(descriptor.path);
            return metadata ? articleLinkedFile(descriptor, metadata) : null;
          } catch {
            return null;
          }
        }));
      } finally {
        candidates.forEach(descriptor => pendingPaths.delete(descriptor.path));
      }
      if (articleId !== state.selectedId || pendingPaths !== state.linkedFileProbes) return;
      let changed = false;
      inspected.filter(Boolean).forEach(file => {
        if (state.linkedFiles.has(file.path) || articleInternalPath(file.path)) return;
        state.linkedFiles.set(file.path, file);
        changed = true;
      });
      if (changed) renderFileTree();
    }

    async function openArticleLinkedReference(request) {
      const resourceAddress = String(
        window.PortalResourceChips?.resourceAddressFromPreviewRequest?.(
          request || {},
        )
        || "",
      ).trim();
      if (resourceAddress) {
        await openArticleResourcePreview({
          ...request,
          address: resourceAddress,
        });
        return;
      }
      const path = String(request?.path || request?.href || "").trim();
      if (!path) return;
      const internalPath = articleInternalPath(path);
      if (internalPath) {
        await openArticleFile(internalPath);
        return;
      }
      let file = state.linkedFiles.get(path);
      if (!file) {
        file = articleLinkedFile({
          ...request,
          path,
          name: request?.name || window.PortalFileLinks?.fileName?.(path),
        });
        if (!file) return;
        state.linkedFiles.set(path, file);
        renderFileTree();
      }
      await openArticleLinkedFile(file);
    }

    function renderFileTree() {
      const root = $("articleFileTree");
      window.PortalFileTree.dispose(root);
      if (!state.selectedId) {
        root.innerHTML = articleStateHtml("Select an Article project.");
        return;
      }
      const linkedFiles = [...state.linkedFiles.values()]
        .sort((left, right) => (
          String(left.name || "").localeCompare(String(right.name || ""))
          || String(left.path || "").localeCompare(String(right.path || ""))
        ))
        .map(file => ({ ...file, type: "file", linked: true, depth: 0 }));
      const entries = [...visibleTreeEntries(treeFromEntries(state.files)), ...linkedFiles];
      if (!entries.length) {
        root.innerHTML = articleStateHtml("This Article project has no visible files.");
        return;
      }
      window.PortalFileTree.render({
        root,
        entries,
        ariaLabel: window.PortalI18n.t("Article files"),
        membership: state.collapsedDirectories,
        isExpanded: entry => !state.collapsedDirectories.has(entry.path),
        isSelected: entry => entry.path === state.openFile,
        getMeta: entry => entry.primary ? "主文件" : (entry.linked ? "引用文件" : ""),
        beforeToggle: path => { state.selectedDirectory = path; },
        render: renderFileTree,
        open: path => {
          const linked = state.linkedFiles.get(path);
          if (linked) return openArticleLinkedReference(linked);
          state.selectedDirectory = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
          return openArticleFile(path);
        },
      });
    }

    async function loadFiles() {
      if (!state.selectedId) return;
      const payload = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/files`);
      if (payload.id !== state.selectedId) return;
      state.files = Array.isArray(payload.entries) ? payload.entries : [];
      state.filesByPath = new Map(state.files.map(entry => [String(entry.path || ""), entry]));
      renderFileTree();
      renderSettings();
    }

    function currentDirectory() {
      if (state.selectedDirectory && state.filesByPath.get(state.selectedDirectory)?.type === "directory") return state.selectedDirectory;
      if (!state.openFile.includes("/")) return "";
      return state.openFile.slice(0, state.openFile.lastIndexOf("/"));
    }

    function openEntryModal(kind) {
      if (!state.selectedId) return;
      state.entryKind = kind;
      $("articleEntryKind").value = kind;
      $("articleEntryEyebrow").textContent = kind === "directory" ? window.PortalI18n.t("NEW FOLDER") : window.PortalI18n.t("NEW FILE");
      $("articleEntryTitle").textContent = kind === "directory" ? window.PortalI18n.t("创建新文件夹") : window.PortalI18n.t("创建新文件");
      const prefix = currentDirectory();
      $("articleEntryPath").value = prefix ? `${prefix}/` : "";
      $("articleEntryPath").placeholder = kind === "directory" ? "sections" : "sections/method.tex";
      articleEntryDialog.open();
      window.setTimeout(() => {
        const input = $("articleEntryPath");
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }, 0);
    }

    async function createEntry(event) {
      event.preventDefault();
      const formElement = event.currentTarget;
      const path = $("articleEntryPath").value.trim();
      const kind = $("articleEntryKind").value;
      if (!path || !state.selectedId) return;
      const submit = formElement.querySelector('button[type="submit"]');
      submit.disabled = true;
      try {
        const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/entries`, {
          method: "POST",
          body: JSON.stringify({ path, kind }),
        });
        articleEntryDialog.close();
        formElement.reset();
        await loadFiles();
        await loadSemanticIndex();
        if (kind === "file") await openArticleFile(result.path);
        else {
          state.selectedDirectory = result.path;
          state.collapsedDirectories.delete(result.path);
          renderFileTree();
        }
        showToast(window.PortalI18n.text`${result.path} 已创建`, "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        submit.disabled = false;
      }
    }

    async function uploadFile(file) {
      if (!file || !state.selectedId) return;
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("directory", currentDirectory());
      try {
        const result = await request(`/api/articles/${encodeURIComponent(state.selectedId)}/upload`, {
          method: "POST",
          body: form,
        });
        await loadFiles();
        await loadSemanticIndex();
        const entry = state.filesByPath.get(result.path);
        if (entry?.editable) await openArticleFile(result.path);
        showToast(window.PortalI18n.text`${result.path} 已上传`, "success");
      } catch (error) {
        showToast(error.message || error, "error");
      } finally {
        $("articleUploadInput").value = "";
      }
    }

    return Object.freeze({
      createEntry, loadFiles, openArticleLinkedReference, openEntryModal, registerArticleLinkedFiles,
      renderFileTree, uploadFile,
    });
  }

  window.PortalArticleWorkspaceFileTree = Object.freeze({ createFileTree, dependencies: DEPENDENCIES });
})();
