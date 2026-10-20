(function (global) {
  "use strict";

  if (window.PortalFileTree) return;

  function resolveRoot(rootRef) {
    if (typeof rootRef === "string") return document.querySelector(rootRef);
    return rootRef || null;
  }

  const escapeHtml = global.PortalUIPrimitives.escapeHtml;
  const bindings = new WeakMap();

  function valueFor(option, entry, fallback) {
    if (typeof option === "function") return option(entry);
    return option === undefined ? fallback : option;
  }

  function rowHtml(entry, options = {}) {
    const path = String(valueFor(options.getPath, entry, entry?.path) || "");
    const kind = String(valueFor(options.getKind, entry, entry?.type || entry?.kind) || "file");
    const directory = kind === "directory";
    const depth = Number(valueFor(options.getDepth, entry, entry?.depth) || 0);
    const expanded = directory && Boolean(valueFor(options.isExpanded, entry, options.expanded?.has(path)));
    const selected = Boolean(valueFor(options.isSelected, entry, false));
    const loading = Boolean(valueFor(options.isLoading, entry, false));
    const label = String(valueFor(options.getLabel, entry, entry?.label || entry?.name || path) || path);
    const title = String(valueFor(options.getTitle, entry, path || label) || label);
    const meta = String(valueFor(options.getMeta, entry, "") || "");
    const badge = valueFor(options.getBadge, entry, "") || "";
    const marker = directory && valueFor(options.hasChildren, entry, entry?.has_children !== false)
      ? window.PortalUIPrimitives?.iconHtml?.(expanded ? "chevron-up" : "chevron-down") || ""
      : "";
    const extraClasses = String(valueFor(options.getClasses, entry, "") || "").trim();
    const indentation = Number(options.indentBase ?? 8) + depth * Number(options.indentStep ?? 14);
    const row = window.PortalI18n.html`<button type="button" role="treeitem" aria-level="${depth + 1}" aria-selected="${selected ? "true" : "false"}" class="portalFileRow${selected ? " is-active" : ""}${loading ? " is-loading" : ""}${extraClasses ? ` ${escapeHtml(extraClasses)}` : ""}" style="--portal-file-depth:${depth};padding-left:${indentation}px" data-portal-file-${directory ? "directory" : "path"}="${escapeHtml(path)}" aria-expanded="${directory ? String(expanded) : "false"}" aria-busy="${loading ? "true" : "false"}" title="${escapeHtml(title)}">
      <span class="portalFileDisclosure">${marker}</span>
      <span class="portalFileName" title="${escapeHtml(path)}">${escapeHtml(label)}</span>
      ${badge}
      <span class="portalFileMeta">${escapeHtml(meta)}</span>
    </button>`;
    return window.PortalI18n.html`<div class="portalFileRowShell" data-portal-file-row-shell="${escapeHtml(path)}">${row}</div>`;
  }

  function rowsHtml(entries, options = {}) {
    return (Array.isArray(entries) ? entries : []).map(entry => rowHtml(entry, options)).join("");
  }

  function parentPaths(path) {
    const parts = String(path || "").replace(/\\/g, "/").split("/").filter(Boolean);
    const parents = [];
    for (let index = 1; index < parts.length; index += 1) {
      parents.push(parts.slice(0, index).join("/"));
    }
    return parents;
  }

  function entryVisible(entry, expanded, getPath = item => item?.path) {
    const path = typeof getPath === "function" ? getPath(entry) : entry?.path;
    return parentPaths(path).every(parent => expanded?.has(parent));
  }

  function preserveViewport(rootRef, render) {
    const root = resolveRoot(rootRef);
    if (window.PortalUI?.preserveScrollPosition) {
      return window.PortalUI.preserveScrollPosition(root, render);
    }
    if (!root || typeof render !== "function") {
      return typeof render === "function" ? render() : undefined;
    }
    const scrollTop = root.scrollTop;
    const scrollLeft = root.scrollLeft;
    const result = render();
    root.scrollTop = scrollTop;
    root.scrollLeft = scrollLeft;
    return result;
  }

  function toggleDirectory({ root, membership, path, render }) {
    const directoryPath = String(path || "");
    if (!membership || !directoryPath) return false;
    if (membership.has(directoryPath)) membership.delete(directoryPath);
    else membership.add(directoryPath);
    preserveViewport(root, () => render?.({ revealSelection: false }));
    return true;
  }

  function pathFrom(element, value) {
    if (typeof value === "function") return value(element);
    if (typeof value === "string") return element?.dataset?.[value] || "";
    return "";
  }

  function bindRows(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) return;
    dispose(root);
    const controller = new AbortController();
    const listenerOptions = { signal: controller.signal };
    const menuButtons = [];
    let revealFrame = 0;
    const release = () => {
      controller.abort();
      menuButtons.forEach(button => button.remove());
      if (revealFrame) window.cancelAnimationFrame(revealFrame);
      if (bindings.get(root) === release) bindings.delete(root);
    };
    bindings.set(root, release);

    root.querySelectorAll("[data-portal-file-directory]").forEach(button => {
      button.addEventListener("click", event => {
        const path = button.dataset.portalFileDirectory || "";
        if (!path) return;
        if (options.beforeToggle?.(path, button, event) === false) return;
        toggleDirectory({ root, membership: options.membership, path, render: options.render });
        options.afterToggle?.(path, button, event);
      }, listenerOptions);
    });
    root.querySelectorAll("[data-portal-file-path]").forEach(button => {
      button.addEventListener("click", event => {
        options.open?.(button.dataset.portalFilePath || "", button, event);
      }, listenerOptions);
      if (typeof options.openMenu !== "function") return;
      const path = button.dataset.portalFilePath || "";
      const shell = button.closest(".portalFileRowShell");
      if (!shell || !path) return;
      const menuButton = document.createElement("button");
      menuButton.type = "button";
      menuButton.className = "portalResourceOpenTrigger";
      menuButton.dataset.portalFileOpenMenu = path;
      menuButton.setAttribute("aria-label", window.PortalI18n.text`Open ${path}`);
      menuButton.setAttribute("data-portal-tooltip", window.PortalI18n.t("打开方式"));
      menuButton.setAttribute("data-portal-icon", "more-horizontal");
      shell.appendChild(menuButton);
      menuButtons.push(menuButton);
      globalThis.PortalUIPrimitives?.mountIcon?.(menuButton);
      menuButton.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        options.openMenu?.({ path, trigger: menuButton, event });
      }, listenerOptions);
      button.addEventListener("contextmenu", event => {
        event.preventDefault();
        event.stopPropagation();
        options.openMenu?.({ path, trigger: button, event });
      }, listenerOptions);
    });

    for (const binding of options.directories || []) {
      root.querySelectorAll(binding.selector).forEach(button => {
        button.addEventListener("click", event => {
          const path = pathFrom(button, binding.path);
          if (!path) return;
          if (
            typeof binding.beforeToggle === "function"
            && binding.beforeToggle(path, button, event) === false
          ) return;
          toggleDirectory({
            root,
            membership: binding.membership || options.membership,
            path,
            render: binding.render || options.render,
          });
          if (typeof binding.afterToggle === "function") binding.afterToggle(path, button, event);
        }, listenerOptions);
      });
    }

    for (const binding of options.files || []) {
      root.querySelectorAll(binding.selector).forEach(button => {
        button.addEventListener("click", event => {
          const path = pathFrom(button, binding.path);
          binding.open?.(path, button, event);
        }, listenerOptions);
      });
    }

    if (options.revealSelection !== false && typeof options.revealSelection === "function") {
      revealFrame = window.requestAnimationFrame(() => options.revealSelection(root));
    }
    return release;
  }

  function dispose(rootRef) {
    const root = resolveRoot(rootRef);
    if (root) bindings.get(root)?.();
  }

  function render(options = {}) {
    const root = resolveRoot(options.root);
    if (!root) return;
    dispose(root);
    root.innerHTML = Array.isArray(options.entries)
      ? window.PortalI18n.html`<div class="portalFileTree" role="tree" aria-label="${escapeHtml(options.ariaLabel || "Files")}">${rowsHtml(options.entries, options)}</div>${options.footerHtml || ""}`
      : String(options.html ?? "");
    return bindRows({ ...options, root });
  }

  window.PortalFileTree = Object.freeze({
    bindRows,
    dispose,
    entryVisible,
    parentPaths,
    preserveViewport,
    render,
    rowHtml,
  });
})(window);
