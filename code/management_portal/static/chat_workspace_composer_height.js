(() => {
  if (window.PortalChatWorkspaceComposerHeight) return;

  // One Workspace's composer height. The reader drags the splitter above the
  // composer, or moves it with the arrow keys, Home and End; when the move
  // ends the height is kept for this Chat and written with the draft. The
  // height stays within limits that follow the Workspace's height, and a
  // window resize brings it back within them. The Workspace's state holds the
  // height (composerInputHeight): it starts from the kept height, the draft
  // saves it and restoring a draft sets it. destroyComposerHeight stops
  // answering the splitter and the window.
  const DEPENDENCIES = Object.freeze([
    "composerResizeEnabled", "els", "followTailAfterResize", "id", "options", "root", "saveActiveDraft", "state",
  ]);

  function createComposerHeight(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceComposerHeight is missing: ${missing.join(", ")}`);
    const { composerResizeEnabled, els, followTailAfterResize, id, options, root, saveActiveDraft, state } = dependencies;

    const storageKey = String(options.composerHeightKey || `portal.workspaceComposerHeight.${id}`);
    const defaultHeight = Math.max(56, Number(options.composerInputHeight || 86));
    const keptHeight = () => {
      const value = Number(window.PortalState.storage.readText(storageKey, ""));
      return Number.isFinite(value) && value > 0 ? value : defaultHeight;
    };
    state.composerInputHeight = keptHeight();
    let destroyed = false;

    const composerInputHeightLimits = () => {
      const rootHeight = root.getBoundingClientRect().height || window.innerHeight || 800;
      const min = Math.max(40, Number(options.composerMinHeight || 56));
      const configuredMax = Math.max(min, Number(options.composerMaxHeight || 360));
      const max = Math.max(min, Math.min(configuredMax, Math.round(rootHeight * 0.46)));
      return { min, max };
    };
    const applyComposerInputLayout = ({ persist = false } = {}) => {
      if (!composerResizeEnabled) return;
      const limits = composerInputHeightLimits();
      state.composerInputHeight = Math.max(limits.min, Math.min(limits.max, Number(state.composerInputHeight || defaultHeight)));
      root.style.setProperty("--portal-composer-input-h", `${Math.round(state.composerInputHeight)}px`);
      if (persist) {
        window.PortalState.storage.writeText(storageKey, Math.round(state.composerInputHeight));
        saveActiveDraft();
      }
    };
    const handleWindowResize = () => {
      applyComposerInputLayout();
      followTailAfterResize();
    };

    let unbindSplitter = () => {};
    if (composerResizeEnabled && els.composerSplitter) {
      if (!window.PortalWorkbench.layout) {
        throw new Error("PortalWorkbench.layout must be loaded before resizable Chat Workspaces.");
      }
      unbindSplitter = window.PortalWorkbench.layout.bindSplitter({
        handle: els.composerSplitter,
        axis: "y",
        direction: -1,
        bodyClass: "resizingComposer",
        boundKey: "portalComposerSplitterBound",
        min: () => composerInputHeightLimits().min,
        max: () => composerInputHeightLimits().max,
        readSize: () => state.composerInputHeight,
        // A drag that began before the Workspace was destroyed changes nothing after it.
        applySize: height => {
          if (destroyed) return;
          state.composerInputHeight = height;
          applyComposerInputLayout();
        },
        onDone: () => {
          if (!destroyed) applyComposerInputLayout({ persist: true });
        },
      });
      window.addEventListener("resize", handleWindowResize);
    }
    applyComposerInputLayout();

    const destroyComposerHeight = () => {
      destroyed = true;
      unbindSplitter();
      window.removeEventListener("resize", handleWindowResize);
    };

    return { applyComposerInputLayout, destroyComposerHeight };
  }

  window.PortalChatWorkspaceComposerHeight = Object.freeze({ createComposerHeight, dependencies: DEPENDENCIES });
})();
