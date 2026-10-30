(() => {
  "use strict";

  if (window.PortalHatgPanelMount) return;

  // Mounting the panel: loading the panel template (with its timeout and
  // retry), the first initialization that binds the template and reads
  // the Lineage catalog, and the controller the Workbench drives.

  const DEPENDENCIES = Object.freeze([
    "$id", "DEFAULT_TEMPLATE_TIMEOUT_MS", "activateLineageEdits", "bindActions",
    "bindLineageProjectionSubscription", "bindModals", "bindResidentStateCollection",
    "clearGraphNodeSearch", "commitLineage", "controllerFactory", "currentNode",
    "destroyGraphNodeSearch", "detachedRoots", "disconnectGraphObservers", "initHatgGraph",
    "loadLineagePageData", "mountTemplates", "nodeResidentId", "openCreateLineageModal",
    "openEditLineageModal", "openFunctionBlockMenu", "panelHost", "publishLocation",
    "renderLineageSurface", "renderNoLineage", "resumeDisplayedLineage", "revealSelectedGraphNode",
    "selectLineage", "selectNode", "selectProgramObject", "separateGraphObjects", "setSyncStatus",
    "state", "toast",
  ]);

  function createMount(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgPanelMount is missing: ${missing.join(", ")}`);
    const {
      $id, DEFAULT_TEMPLATE_TIMEOUT_MS, activateLineageEdits, bindActions,
      bindLineageProjectionSubscription, bindModals, bindResidentStateCollection, clearGraphNodeSearch,
      commitLineage, controllerFactory, currentNode, destroyGraphNodeSearch, detachedRoots,
      disconnectGraphObservers, initHatgGraph, loadLineagePageData, mountTemplates, nodeResidentId,
      openCreateLineageModal, openEditLineageModal, openFunctionBlockMenu, panelHost, publishLocation,
      renderLineageSurface, renderNoLineage, resumeDisplayedLineage, revealSelectedGraphNode,
      selectLineage, selectNode, selectProgramObject, separateGraphObjects, setSyncStatus, state, toast,
    } = dependencies;

    // The panel's first read of its Lineage catalog has finished (successfully or not).
    let lineageCatalogHydrated = false;

    async function init() {
      mountTemplates();
      try {
        await window.PortalLineageProgram?.loadPrimitiveDefinitions?.();
      } catch (error) {
        setSyncStatus("原语定义加载失败", "error");
        toast(window.PortalI18n.text`HATG 原语定义加载失败：${error.message || error}`, "error", 8000);
        // Let the mount boundary retain the target and show its retry action.
        // A failed prerequisite is not an initialized, empty Lineage panel.
        throw error;
      }
      const hatgPane = $id("hatgPane");
      if (hatgPane) hatgPane.hidden = false;
      bindModals();
      bindActions();
      initHatgGraph();
      renderLineageSurface();
      // Remote reads hydrate the surface; they do not own its interaction lifecycle.
      panelHost.hatgComponentRoot.inert = false;
      try {
        const me = await window.PortalIdentity.load();
        state.me = String(me?.username || "");
        // HATG needs only SocietyService. Waiting for the host page's whole
        // first load (Resident sidebar, conversations, catalogs) kept a cold
        // HATG idle for about a second, and for many seconds under load. Read
        // now; the service-ready boundary is needed only when that read fails
        // because the service is still starting or recovering.
        try {
          await loadLineagePageData();
        } catch {
          await panelHost.hatgComponentOptions.waitForServiceReady();
          await loadLineagePageData();
        }
      } catch (error) {
        setSyncStatus("初始化失败", "error");
        toast(window.PortalI18n.text`Lineage 初始化失败：${error.message || error}`, "error", 7000);
      } finally {
        lineageCatalogHydrated = true;
      }
      window.PortalButtonTooltips?.enhance?.(panelHost.hatgComponentRoot);
    }

    function mountRuntime(options = {}) {
      const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
      if (!root) throw new TypeError("PortalHatgPanel.mountHatgPanel requires root.");
      if (typeof options.waitForServiceReady !== "function") {
        throw new TypeError("PortalHatgPanel requires its Workbench service-ready boundary.");
      }
      if (typeof options.onLocationChange !== "function") {
        throw new TypeError("PortalHatgPanel requires its Workbench location owner.");
      }
      if (typeof options.onDeleteLineage !== "function") {
        throw new TypeError("PortalHatgPanel requires its Workbench Lineage deletion owner.");
      }
      if (!options.pageState || typeof options.pageState.read !== "function" || typeof options.pageState.write !== "function") {
        throw new TypeError("PortalHatgPanel requires its Workbench page-state owner.");
      }
      panelHost.hatgComponentRoot = root;
      panelHost.hatgComponentOptions = { ...options };
      panelHost.pageStateController = controllerFactory.createController({
        state,
        pageState: options.pageState,
      });
      panelHost.pageStateController.listen("panel", root, "click", event => {
        const badge = event.target.closest("[data-function-block-issues]");
        if (!badge) return;
        event.preventDefault();
        event.stopPropagation();
        openFunctionBlockMenu(badge.dataset.functionBlockIssues, "issues");
      }, true);
      root.classList.add("pane", "lineageHatgPanel", "portalHatgPanelComponent");
      root.setAttribute("aria-busy", "true");
      root.inert = true;
      let destroyed = false;
      let templateRequestAbort = null;
      let loadPromise = Promise.resolve(null);
      let target = {
        lineageId: String(options.lineageId || ""),
        residentId: String(options.residentId || ""),
        programItemId: String(options.programItemId || ""),
      };
      let appliedTarget = null;
      let targetRequestSeq = 0;
      const parsedTemplateTimeout = options.templateTimeoutMs === undefined
        ? DEFAULT_TEMPLATE_TIMEOUT_MS
        : Number(options.templateTimeoutMs);
      const templateTimeoutMs = Number.isFinite(parsedTemplateTimeout)
        ? parsedTemplateTimeout
        : DEFAULT_TEMPLATE_TIMEOUT_MS;

      function renderTemplateLoadState({ title, detail, retry = false }) {
        if (retry) {
          root.removeAttribute("aria-busy");
          root.inert = false;
        } else {
          root.setAttribute("aria-busy", "true");
          root.inert = true;
        }
        const shell = document.createElement("div");
        shell.className = "lineageHatgEmpty";
        shell.setAttribute("role", retry ? "alert" : "status");
        const heading = document.createElement("strong");
        heading.textContent = title;
        const message = document.createElement("small");
        message.textContent = detail;
        shell.append(heading, message);
        if (retry) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "portalButton";
          button.dataset.hatgTemplateRetry = "1";
          button.textContent = window.PortalI18n.t("重试");
          button.addEventListener("click", () => { void startTemplateLoad(); }, { once: true });
          shell.append(button);
        }
        root.replaceChildren(shell);
      }

      async function fetchTemplate() {
        templateRequestAbort?.abort();
        const requestAbort = new AbortController();
        templateRequestAbort = requestAbort;
        let timedOut = false;
        const timeout = templateTimeoutMs > 0
          ? window.setTimeout(() => {
              timedOut = true;
              requestAbort.abort(new DOMException("HATG template timed out", "TimeoutError"));
            }, templateTimeoutMs)
          : 0;
        try {
          return await fetch(options.templateUrl || "/hatg-panel.html", {
            cache: "no-store",
            signal: requestAbort.signal,
          });
        } catch (error) {
          if (timedOut) {
            const seconds = Math.max(1, Math.ceil(templateTimeoutMs / 1000));
            throw new Error(`HATG 模板加载超过 ${seconds} 秒，请重试。`);
          }
          throw error;
        } finally {
          if (timeout) window.clearTimeout(timeout);
          if (templateRequestAbort === requestAbort) templateRequestAbort = null;
        }
      }

      function startTemplateLoad() {
        if (destroyed) return Promise.resolve(null);
        delete root.dataset.hatgError;
        renderTemplateLoadState({ title: window.PortalI18n.t("正在加载 HATG"), detail: "正在读取图编辑模板。" });
        const request = (async () => {
          const [response] = await Promise.all([
            fetchTemplate(),
            window.PortalUIPrimitives.loadAsset("/static/lineage_templates.css"),
          ]);
          if (!response.ok) throw new Error(`HATG template failed (${response.status})`);
          const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
          const fragment = document.createDocumentFragment();
          parsed.querySelectorAll("body > :not(header):not(script)").forEach(node => {
            fragment.append(document.importNode(node, true));
          });
          if (!fragment.childNodes.length) throw new Error("HATG 模板内容为空。");
          if (destroyed) return null;
          root.replaceChildren(fragment);
          window.PortalI18n.renderStatic(root);
          await init();
          if (destroyed) return null;
          root.inert = false;
          if (target.lineageId) await controller.setTarget(target);
          delete root.dataset.hatgError;
          root.removeAttribute("aria-busy");
          return controller;
        })();
        loadPromise = request.catch(error => {
          if (destroyed) return null;
          disconnectGraphObservers();
          root.dataset.hatgError = String(error.message || error);
          renderTemplateLoadState({
            title: window.PortalI18n.t("HATG 加载失败"),
            detail: String(error.message || error),
            retry: true,
          });
          return controller;
        });
        return loadPromise;
      }

      const controller = Object.freeze({
        root,
        get ready() { return loadPromise; },
        retry() { return startTemplateLoad(); },
        suspendLineage(lineageId) {
          const suspendedId = String(lineageId || "");
          if (
            state.lineage?.lineage_id !== suspendedId
            && target.lineageId !== suspendedId
          ) return null;
          const snapshot = controller.snapshot();
          targetRequestSeq += 1;
          state.detailSeq += 1;
          target = { lineageId: "", residentId: "", programItemId: "" };
          appliedTarget = { ...target };
          renderNoLineage({ publishLocation: false });
          return snapshot;
        },
        resumeLineage(snapshot = {}) {
          if (!String(snapshot.lineageId || "")) return Promise.resolve(target);
          return controller.setTarget(snapshot);
        },
        applyLineageDeletion(lineageId) {
          const deletedId = String(lineageId || "");
          state.lineages = state.lineages.filter(item => item.lineage_id !== deletedId);
          if (controller.suspendLineage(deletedId)) publishLocation("");
        },
        async applyLineageMutation(lineage, { separateObjectIds = [] } = {}) {
          await loadPromise;
          if (state.layoutSaveInFlight) await state.layoutSaveInFlight;
          const changed = commitLineage(lineage).changed;
          if (lineage.lineage_id === state.lineage?.lineage_id && separateObjectIds.length) {
            await separateGraphObjects(separateObjectIds);
          }
          return changed;
        },
        async setTarget(next = {}) {
          const requestSeq = ++targetRequestSeq;
          const revealInGraph = next.revealInGraph === true;
          const hasProgramItem = Object.prototype.hasOwnProperty.call(next, "programItemId");
          const requestedLineageId = String(next.lineageId || target.lineageId || "");
          const requestedResidentId = String(next.residentId || target.residentId || "");
          const pendingProgramItemId = (
            target.lineageId === requestedLineageId
            && target.programItemId
            && target.programItemId !== appliedTarget?.programItemId
          ) ? target.programItemId : "";
          const requestedTarget = {
            lineageId: requestedLineageId,
            residentId: requestedResidentId,
            programItemId: hasProgramItem
              ? String(next.programItemId || "")
              : String(state.lineage?.lineage_id === requestedLineageId
                ? state.selectedProgramObjectId || pendingProgramItemId
                : pendingProgramItemId),
          };
          target = requestedTarget;
          state.targetResidentId = requestedTarget.residentId;
          // Before the first catalog read the template load applies this target. After it,
          // an explicit Lineage is opened even when the discoverable list is empty.
          if (!lineageCatalogHydrated) return target;
          const selectedTargetNode = currentNode();
          const selectedTargetMatches = (
            String(state.lineage?.lineage_id || "") === requestedTarget.lineageId
            && (
              !requestedTarget.residentId
              || selectedTargetNode?.id === requestedTarget.residentId
              || nodeResidentId(selectedTargetNode) === requestedTarget.residentId
            )
          );
          if (selectedTargetMatches) {
            // Returning to the displayed graph is still a new navigation. A
            // pending load for another graph must not overwrite this selection.
            state.detailSeq += 1;
            resumeDisplayedLineage();
            let selectionHandledReveal = false;
            if (requestedTarget.programItemId) {
              await selectProgramObject(requestedTarget.programItemId, { revealFamily: true });
            } else if (hasProgramItem && state.selectedProgramObjectId && selectedTargetNode) {
              await selectNode(selectedTargetNode.id, {
                force: true,
                notifyParent: false,
                revealInGraph,
              });
              selectionHandledReveal = revealInGraph;
            }
            if (requestSeq !== targetRequestSeq) return target;
            appliedTarget = { ...requestedTarget };
            if (revealInGraph && selectedTargetNode && !selectionHandledReveal) {
              await revealSelectedGraphNode(selectedTargetNode.id);
            }
            return target;
          }
          if (requestedTarget.lineageId) await selectLineage(requestedTarget.lineageId, {
            residentId: requestedTarget.residentId,
            programItemId: requestedTarget.programItemId,
            revealInGraph,
          });
          if (requestSeq !== targetRequestSeq) return target;
          if (requestSeq === targetRequestSeq) appliedTarget = { ...requestedTarget };
          return target;
        },
        async editLineage(next = {}) {
          await loadPromise;
          await controller.setTarget(next);
          if (!state.lineage) throw new Error("Lineage 尚未加载");
          const modal = $id("lineageModal");
          if (modal && modal.parentElement !== document.body) {
            modal.dataset.portalDetached = "1";
            document.body.append(modal);
            detachedRoots.add(modal);
          }
          openEditLineageModal();
          return state.lineage.lineage_id;
        },
        async createLineage(next = {}) {
          await loadPromise;
          const modal = $id("lineageModal");
          if (modal && modal.parentElement !== document.body) {
            modal.dataset.portalDetached = "1";
            document.body.append(modal);
            detachedRoots.add(modal);
          }
          openCreateLineageModal(next.projectId || "");
        },
        setContext(context = {}) {
          const next = context.hatg || context;
          if (next.lineageId) void controller.setTarget(next);
        },
        snapshot: () => ({
          lineageId: state.lineage?.lineage_id || target.lineageId,
          residentId: state.selectedNodeId || target.residentId,
          programItemId: state.lineage ? state.selectedProgramObjectId : target.programItemId,
        }),
        restore(snapshot = {}) { target = { ...target, ...snapshot }; },
        show() {
          panelHost.panelActive = true;
          root.hidden = false;
          if (state.lineage) {
            bindResidentStateCollection();
            bindLineageProjectionSubscription();
          }
        },
        hide() {
          panelHost.panelActive = false;
          clearGraphNodeSearch();
          root.hidden = true;
          state.residentStateCollection?.destroy?.();
          state.residentStateCollection = null;
          state.lineageProjectionSubscription?.();
          state.lineageProjectionSubscription = null;
        },
        destroy() {
          destroyed = true;
          panelHost.panelActive = false;
          disconnectGraphObservers();
          panelHost.disposeLibraryResize?.();
          panelHost.disposeLibraryResize = null;
          destroyGraphNodeSearch();
          activateLineageEdits();
          templateRequestAbort?.abort();
          templateRequestAbort = null;
          root.removeAttribute("aria-busy");
          delete root.dataset.hatgError;
          root.inert = false;
          state.residentStateCollection?.destroy?.();
          state.residentStateCollection = null;
          state.lineageProjectionSubscription?.();
          state.lineageProjectionSubscription = null;
          state.graphController?.destroy?.();
          panelHost.pageStateController?.destroy?.();
          panelHost.templateController?.destroy();
          root.replaceChildren();
        },
      });
      loadPromise = startTemplateLoad();
      return controller;
    }

    return {
      mountRuntime,
    };
  }

  window.PortalHatgPanelMount = Object.freeze({ createMount, dependencies: DEPENDENCIES });
})();
