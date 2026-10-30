(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSelectionToolbar) return;

  // The toolbar of a Preview selection: the button the page passes (or one
  // the controller adds to the document), and showing it at the selection
  // after the pointer or keys that change it, or hiding it.

  const DEPENDENCIES = Object.freeze([
    "options", "previewSelectionState", "resolveRoot", "selectionInfo",
  ]);

  function createSelectionToolbar(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSelectionToolbar is missing: ${missing.join(", ")}`);
    const {
      options, previewSelectionState, resolveRoot, selectionInfo,
    } = dependencies;

    let toolbar = resolveRoot(options.toolbar);
    let ownsToolbar = false;
    if (!toolbar) {
      toolbar = document.createElement("button");
      toolbar.type = "button";
      toolbar.hidden = true;
      document.body.appendChild(toolbar);
      ownsToolbar = true;
    }
    toolbar.classList.add("portalPreviewSelectionToolbar");
    toolbar.textContent = String(options.label || toolbar.textContent || window.PortalI18n.t("发送到对话"));
    toolbar.dataset.portalOverlayLayer = "popover";
    toolbar.setAttribute("aria-label", String(options.ariaLabel || window.PortalI18n.t("把选中的文件内容发送到对话")));

    const hide = () => {
      window.clearTimeout(previewSelectionState.timer);
      previewSelectionState.timer = 0;
      previewSelectionState.current = null;
      toolbar.hidden = true;
      return true;
    };
    const position = info => {
      const rect = info?.rect;
      if (!rect) return hide();
      toolbar.hidden = false;
      const width = toolbar.offsetWidth || 92;
      const height = toolbar.offsetHeight || 30;
      const above = rect.top - height - 8;
      const top = above >= 8
        ? above
        : Math.min(window.innerHeight - height - 8, rect.bottom + 8);
      const center = rect.left + (Number(rect.width || 0) / 2);
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, center - width / 2));
      toolbar.style.top = `${Math.max(8, top)}px`;
      toolbar.style.left = `${left}px`;
      return true;
    };
    const show = event => {
      const info = selectionInfo(event);
      if (!info) return hide();
      previewSelectionState.current = info;
      position(info);
      options.onShow?.(Object.freeze({ ...info }));
      return info;
    };
    const scheduleShow = event => {
      if (event && Number.isFinite(Number(event.clientX)) && Number.isFinite(Number(event.clientY))) {
        previewSelectionState.lastPointer = {
          clientX: Number(event.clientX),
          clientY: Number(event.clientY),
          target: event.target,
        };
      }
      window.clearTimeout(previewSelectionState.timer);
      previewSelectionState.timer = window.setTimeout(() => {
        previewSelectionState.timer = 0;
        show(event || previewSelectionState.lastPointer);
      }, 0);
    };

    return {
      hide, ownsToolbar, scheduleShow, show, toolbar,
    };
  }

  window.PortalWorkspaceSurfaceSelectionToolbar = Object.freeze({ createSelectionToolbar, dependencies: DEPENDENCIES });
})();
