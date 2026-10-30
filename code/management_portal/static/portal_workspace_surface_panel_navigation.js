(() => {
  "use strict";

  if (window.PortalWorkspaceSurfacePanelNavigation) return;

  // Where a Preview panel has been: the locators it persists for a
  // resource (through a navigation adapter when one is set), the history
  // of previously shown resources and the back button that follows it.

  const DEPENDENCIES = Object.freeze([
    "ids", "options", "previewPanelState", "root",
  ]);

  function createPanelNavigation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfacePanelNavigation is missing: ${missing.join(", ")}`);
    const {
      ids, options, previewPanelState, root,
    } = dependencies;

    const configuredNavigationHistoryLimit = Number(options.navigationHistoryLimit ?? 32);
    const navigationHistoryLimit = Number.isInteger(configuredNavigationHistoryLimit)
      && configuredNavigationHistoryLimit > 0
      ? Math.min(configuredNavigationHistoryLimit, 256)
      : 32;

    const hasPreviewTarget = resource => {
      if (!resource || typeof resource !== "object") return false;
      if (resource.document || resource.payload) return true;
      if (resource.request && resource.request !== resource) return hasPreviewTarget(resource.request);
      return Boolean(
        String(resource.resourceAddress || resource.href || resource.path || resource.identity || "").trim(),
      );
    };
    const persistenceBinding = resource => {
      if (!resource || typeof resource !== "object" || Array.isArray(resource)) return {};
      const candidates = [resource, resource.request, resource.document, resource.payload]
        .filter(candidate => candidate && typeof candidate === "object" && !Array.isArray(candidate));
      const locator = {};
      ["resourceAddress", "href", "path", "identity", "locatorKind", "source", "title", "label"].forEach(field => {
        const value = candidates
          .map(candidate => String(candidate[field] ?? "").trim())
          .find(Boolean);
        if (!value || value.length > 8192) return;
        if (field === "href" && /^(?:data|blob):/i.test(value)) return;
        locator[field] = value;
      });
      ["line", "lineStart", "lineEnd", "column", "columnStart", "columnEnd"].forEach(field => {
        const value = candidates.map(candidate => candidate[field]).find(candidate => Number.isInteger(candidate));
        if (value !== undefined) locator[field] = value;
      });
      return locator;
    };
    const sanitizeNavigationLocator = value => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return {};
      const excluded = new Set([
        "content", "text", "body", "draft", "data", "document", "payload",
        "request", "load", "save", "session", "previewUrl",
      ]);
      const locator = {};
      Object.entries(value).forEach(([field, raw]) => {
        if (!field || excluded.has(field) || typeof raw === "function") return;
        if (typeof raw === "string") {
          const normalized = raw.trim();
          if (!normalized || normalized.length > 8192) return;
          if (/^(?:data|blob):/i.test(normalized)) return;
          locator[field] = normalized;
          return;
        }
        if (typeof raw === "boolean" || (typeof raw === "number" && Number.isFinite(raw))) {
          locator[field] = raw;
        }
      });
      return locator;
    };
    const navigationLocator = resource => {
      if (previewPanelState.navigationAdapter?.serialize) {
        const adapted = sanitizeNavigationLocator(previewPanelState.navigationAdapter.serialize(resource));
        if (hasPreviewTarget(adapted)) {
          if (previewPanelState.navigationAdapter.id) adapted.navigationAdapter = previewPanelState.navigationAdapter.id;
          return adapted;
        }
      }
      return persistenceBinding(resource);
    };
    const restoreNavigationResource = async locator => {
      if (previewPanelState.navigationAdapter?.restore) {
        const restored = await previewPanelState.navigationAdapter.restore({ ...(locator || {}) });
        if (restored) return restored;
        if (
          String(locator?.navigationAdapter || "") === previewPanelState.navigationAdapter.id
          || String(locator?.sourceType || "")
        ) {
          throw new Error("Preview 历史目标已失效");
        }
      }
      return locator;
    };
    const needsNavigationAdapter = locator => {
      if (locator?.navigationAdapter || locator?.sourceType) return true;
      const sourceId = String(locator?.source || "").trim();
      return Boolean(sourceId && !window.PortalPreview.source?.(sourceId));
    };
    const locatorIdentity = locator => JSON.stringify([
      String(locator?.navigationAdapter || ""),
      String(locator?.sourceType || locator?.source || ""),
      String(locator?.resourceAddress || locator?.identity || locator?.href || locator?.path || ""),
    ]);
    const normalizeNavigationHistory = values => {
      const normalized = [];
      (Array.isArray(values) ? values : []).forEach(value => {
        const locator = sanitizeNavigationLocator(value);
        if (!hasPreviewTarget(locator)) return;
        if (locatorIdentity(normalized.at(-1)) === locatorIdentity(locator)) return;
        normalized.push(locator);
      });
      return normalized.slice(-navigationHistoryLimit);
    };
    const updateBackAction = () => {
      const previous = previewPanelState.navigationHistory.at(-1) || null;
      const label = String(previous?.title || previous?.label || previous?.path || "").trim();
      [ids.back].forEach(id => {
        const button = root.querySelector(`#${CSS.escape(id)}`);
        if (!button) return;
        button.disabled = previewPanelState.navigationBusy || !previous;
        button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        button.title = label ? window.PortalI18n.text`返回：${label}` : window.PortalI18n.t("返回上一个预览文件");
      });
    };
    const rememberPreviousResource = (previousResource, nextResource) => {
      const previous = navigationLocator(previousResource);
      const next = navigationLocator(nextResource);
      if (!hasPreviewTarget(previous) || locatorIdentity(previous) === locatorIdentity(next)) return;
      if (locatorIdentity(previewPanelState.navigationHistory.at(-1)) !== locatorIdentity(previous)) {
        previewPanelState.navigationHistory.push(previous);
        previewPanelState.navigationHistory = previewPanelState.navigationHistory.slice(-navigationHistoryLimit);
      }
      updateBackAction();
    };

    return {
      hasPreviewTarget, navigationLocator, needsNavigationAdapter, normalizeNavigationHistory,
      rememberPreviousResource, restoreNavigationResource, sanitizeNavigationLocator, updateBackAction,
    };
  }

  window.PortalWorkspaceSurfacePanelNavigation = Object.freeze({ createPanelNavigation, dependencies: DEPENDENCIES });
})();
