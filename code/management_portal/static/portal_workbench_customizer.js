(function (global) {
  "use strict";

  if (global.PortalWorkbenchCustomizer) return;

  // The layout menu (Panel toggles, presets, saved workspaces, new editors,
  // reset) with opening and closing it, and the rail of hidden Panels.

  const DEPENDENCIES = Object.freeze([
    "applyWorkspace", "clone", "customizeButton", "descriptors", "escapeHtml", "executeCommand", "factoryPresets",
    "hiddenRail", "isLayoutLocked", "menu", "openResource", "options", "panelPackages", "persist", "savedWorkspaces",
    "shellState",
  ]);

  function createCustomizer(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchCustomizer is missing: ${missing.join(", ")}`);
    const {
      applyWorkspace, clone, customizeButton, descriptors, escapeHtml, executeCommand, factoryPresets, hiddenRail,
      isLayoutLocked, menu, openResource, options, panelPackages, persist, savedWorkspaces, shellState,
    } = dependencies;

    let outsidePointerHandler = null;

    function renderMenu() {
      const editorOptionsOpen = Boolean(menu.querySelector(".portalWorkbenchEditorOptions")?.open);
      const presetRows = [...factoryPresets.values()].map(preset => window.PortalI18n.html`
        <button class="portalWorkbenchMenuRow" type="button" data-workbench-preset="${escapeHtml(preset.id)}">
          <span data-portal-icon="window"></span><span>${escapeHtml(preset.name)}</span>
        </button>`).join("");
      const savedRows = Object.entries(savedWorkspaces).map(([workspaceId, workspace]) => window.PortalI18n.html`
        <span class="portalWorkbenchSavedRow">
          <button class="portalWorkbenchMenuRow" type="button" data-workbench-saved="${escapeHtml(workspaceId)}">
            <span data-portal-icon="star"></span><span>${escapeHtml(String(workspace.name || workspaceId))}</span>
          </button>
          <button class="portalWorkbenchSavedDelete" type="button" data-workbench-delete="${escapeHtml(workspaceId)}" aria-label="删除 ${escapeHtml(String(workspace.name || workspaceId))}" data-portal-icon="trash"></button>
        </span>`).join("");
      const panelRows = [...descriptors.values()].filter(descriptor => !descriptor.closable && !descriptor.required && descriptor.canHide).map(descriptor => window.PortalI18n.html`
        <label class="portalWorkbenchPanelToggle${descriptor.required ? " is-required" : ""}">
          <input type="checkbox" data-workbench-panel-toggle="${escapeHtml(descriptor.id)}" ${shellState.hidden.has(descriptor.id) ? "" : "checked"} ${descriptor.required ? "disabled" : ""} />
          <span data-portal-icon="${escapeHtml(descriptor.icon)}"></span><span>${escapeHtml(descriptor.layoutTitle || descriptor.title)}</span>
        </label>`).join("");
      const editorRows = (options.editorTypes || []).map(raw => {
        const type = String(raw.type || raw.id || raw || "").trim();
        if (!type) return "";
        const panelPackage = panelPackages.get(type);
        const title = String(raw.title || panelPackage?.presentation.title || type);
        const icon = String(raw.icon || panelPackage?.presentation.icon || "panel");
        return window.PortalI18n.html`<span class="portalWorkbenchEditorCreateRow">
          <span data-portal-icon="${escapeHtml(icon)}"></span><span>${escapeHtml(title)}</span>
          <button class="portalButton is-icon is-quiet" type="button" data-workbench-create-editor="${escapeHtml(type)}" data-open-mode="new-tab" data-portal-icon="copy" aria-label="新建 ${escapeHtml(title)} 标签" title="新建标签"></button>
          <button class="portalButton is-icon is-quiet" type="button" data-workbench-create-editor="${escapeHtml(type)}" data-open-mode="side" data-portal-icon="split-right" aria-label="在侧边新建 ${escapeHtml(title)}" title="在侧边打开"></button>
        </span>`;
      }).join("");
      menu.innerHTML = window.PortalI18n.html`
        <div class="portalWorkbenchMenuHeading"><strong>调整布局</strong></div>
        ${panelRows ? window.PortalI18n.html`<section><h3>面板</h3>${panelRows}</section>` : ""}
        ${presetRows ? window.PortalI18n.html`<section><h3>预设</h3><div class="portalWorkbenchPresets">${presetRows}</div></section>` : ""}
        ${savedRows ? window.PortalI18n.html`<section><h3>我的工作区</h3>${savedRows}</section>` : ""}
        <form class="portalWorkbenchSaveForm">
          <label><span>保存当前布局</span><input class="portalControl" name="name" maxlength="60" placeholder="工作区名称" required /></label>
          <button class="portalButton is-compact is-primary" type="submit">保存</button>
        </form>
        <button class="portalButton is-compact portalWorkbenchReset" type="button" data-workbench-reset>恢复默认布局</button>
        ${editorRows ? window.PortalI18n.html`<details class="portalWorkbenchEditorOptions" ${editorOptionsOpen ? "open" : ""}><summary>新建编辑器</summary>${editorRows}</details>` : ""}`;
      menu.querySelectorAll("[data-workbench-panel-toggle]").forEach(input => {
        input.addEventListener("change", event => {
          executeCommand(`panel.${input.dataset.workbenchPanelToggle}.toggle-visibility`, event, "customizer")
            .finally(renderMenu);
        });
      });
      menu.querySelectorAll("[data-workbench-create-editor]").forEach(button => {
        button.addEventListener("click", async () => {
          const type = String(button.dataset.workbenchCreateEditor || "");
          const resource = options.resolveEditorResource?.(type, shellState.controller) || { kind: type };
          button.disabled = true;
          try {
            await openResource({
              type,
              resource: { ...resource, kind: type },
              mode: String(button.dataset.openMode || "new-tab"),
              targetStackId: shellState.focusedStackId,
            });
            closeMenu();
          } catch (error) {
            window.PortalUI?.toast?.(window.PortalI18n.text`打开编辑器失败：${error.message || error}`, "error");
          } finally {
            button.disabled = false;
          }
        });
      });
      menu.querySelectorAll("[data-workbench-preset]").forEach(button => {
        button.addEventListener("click", () => applyWorkspace(factoryPresets.get(button.dataset.workbenchPreset)));
      });
      menu.querySelectorAll("[data-workbench-saved]").forEach(button => {
        button.addEventListener("click", () => applyWorkspace(savedWorkspaces[button.dataset.workbenchSaved]));
      });
      menu.querySelectorAll("[data-workbench-delete]").forEach(button => {
        button.addEventListener("click", () => {
          delete savedWorkspaces[button.dataset.workbenchDelete];
          persist();
          renderMenu();
        });
      });
      menu.querySelector(".portalWorkbenchSaveForm")?.addEventListener("submit", event => {
        event.preventDefault();
        const name = String(new FormData(event.currentTarget).get("name") || "").trim();
        if (!name) return;
        const workspaceId = `workspace-${Date.now().toString(36)}`;
        savedWorkspaces[workspaceId] = { id: workspaceId, name, layout: clone(shellState.model), hidden: [...shellState.hidden], collapsed: [...shellState.collapsed] };
        shellState.activeWorkspaceName = name;
        persist();
        renderMenu();
      });
      menu.querySelector("[data-workbench-reset]")?.addEventListener("click", () => {
        executeCommand("workbench.reset-layout", null, "customizer");
      });
      global.PortalUIPrimitives?.mountIcons?.(menu);
    }

    function renderHiddenRail() {
      hiddenRail.replaceChildren();
      [...shellState.hidden].forEach(panelId => {
        const descriptor = descriptors.get(panelId);
        if (!descriptor) return;
        const button = document.createElement("button");
        button.className = "portalWorkbenchHiddenPanelButton";
        button.type = "button";
        button.dataset.workbenchRestorePanel = descriptor.id;
        button.dataset.portalIcon = descriptor.icon;
        button.setAttribute("aria-label", window.PortalI18n.text`显示 ${descriptor.title}`);
        button.title = window.PortalI18n.text`显示 ${descriptor.title}`;
        button.addEventListener("click", event => executeCommand(`panel.${descriptor.id}.toggle-visibility`, event, "hidden-rail"));
        hiddenRail.append(button);
      });
      hiddenRail.hidden = !hiddenRail.childElementCount;
    }

    function openMenu() {
      if (isLayoutLocked()) return false;
      menu.hidden = false;
      customizeButton.setAttribute("aria-expanded", "true");
      renderMenu();
      outsidePointerHandler = event => {
        if (menu.contains(event.target) || customizeButton.contains(event.target)) return;
        closeMenu();
      };
      document.addEventListener("pointerdown", outsidePointerHandler, true);
      requestAnimationFrame(() => menu.querySelector("input, button")?.focus());
    }

    function closeMenu() {
      menu.hidden = true;
      const editorOptions = menu.querySelector(".portalWorkbenchEditorOptions");
      if (editorOptions) editorOptions.open = false;
      customizeButton.setAttribute("aria-expanded", "false");
      if (outsidePointerHandler) document.removeEventListener("pointerdown", outsidePointerHandler, true);
      outsidePointerHandler = null;
    }

    return {
      closeMenu, openMenu, renderHiddenRail, renderMenu,
    };
  }

  global.PortalWorkbenchCustomizer = Object.freeze({ createCustomizer, dependencies: DEPENDENCIES });
})(window);
