(function (global) {
  "use strict";

  if (global.PortalResourceDetail) return;

  const MESSAGE_TYPE = "portal:resource-detail.v1";
  const HISTORY_STATE_KEY = "portalResourceDetail";
  const HISTORY_DEPTH_STATE_KEY = "portalResourceDetailDepth";
  const HISTORY_RETURN_STATE_KEY = "portalResourceDetailReturn";
  const adapters = new Map();
  let requestSequence = 0;

  const element = ref => window.PortalUIPrimitives.elementOf(ref);

  function clean(value) {
    return String(value || "").trim();
  }

  function optionValue(value) {
    return typeof value === "function" ? value() : value;
  }

  function resourceTypeFromId(resourceId) {
    return clean(resourceId).match(/^([a-z][a-z0-9]*(?:_[a-z0-9]+)*)_[0-9a-f]{12}(?:\/|$)/)?.[1] || "";
  }

  function requestId() {
    requestSequence += 1;
    return `detail-${Date.now().toString(36)}-${requestSequence.toString(36)}`;
  }

  function normalizeResource(resource, fallbackKind = "") {
    if (typeof resource === "string") {
      return {
        resourceId: clean(resource),
        resourceType: resourceTypeFromId(resource),
        detailKind: clean(fallbackKind) || resourceTypeFromId(resource),
      };
    }
    const resourceId = clean(
      resource?.resourceId
      || resource?.resource_id
      || resource?.id,
    );
    const resourceType = clean(resource?.resourceType || resource?.resource_type)
      || resourceTypeFromId(resourceId);
    return {
      ...resource,
      resourceId,
      resourceType,
      detailKind: clean(resource?.detailKind || resource?.detail_kind || fallbackKind)
        || resourceType,
    };
  }

  function plainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function createDetailHistory(options = {}) {
    const parameter = clean(options.parameter || options.param || "item") || "item";
    const owner = clean(options.owner || options.key || options.detailKind)
      || `${global.location?.pathname || "resource"}:${parameter}`;
    const currentUrl = () => new URL(global.location.href);
    const currentValue = () => clean(currentUrl().searchParams.get(parameter));
    let acceptedLocation = { url: currentUrl(), state: global.history.state };
    const acceptCurrent = () => {
      acceptedLocation = { url: currentUrl(), state: global.history.state };
    };
    const marker = state => {
      const value = plainObject(state) ? state[HISTORY_STATE_KEY] : null;
      return plainObject(value) ? value : null;
    };
    const navigationDepth = state => {
      const value = plainObject(state) ? Number(state[HISTORY_DEPTH_STATE_KEY]) : 0;
      return Number.isSafeInteger(value) && value > 0 ? value : 0;
    };
    const returnPosition = state => {
      const value = plainObject(state) ? state[HISTORY_RETURN_STATE_KEY] : null;
      if (!plainObject(value) || clean(value.owner) !== owner) return null;
      const x = Number(value.x);
      const y = Number(value.y);
      return {
        x: Number.isFinite(x) ? Math.max(0, x) : 0,
        y: Number.isFinite(y) ? Math.max(0, y) : 0,
      };
    };
    const nextUrl = patch => {
      const url = currentUrl();
      Object.entries(patch || {}).forEach(([key, value]) => {
        const normalized = clean(value);
        if (normalized) url.searchParams.set(key, normalized);
        else url.searchParams.delete(key);
      });
      return url;
    };
    const relativeUrl = url => `${url.pathname}${url.search}${url.hash}`;
    const rememberReturnPosition = () => {
      const state = plainObject(global.history.state) ? { ...global.history.state } : {};
      state[HISTORY_RETURN_STATE_KEY] = {
        owner,
        x: Math.max(0, Number(global.scrollX) || 0),
        y: Math.max(0, Number(global.scrollY) || 0),
      };
      global.history.replaceState(state, "", relativeUrl(currentUrl()));
    };
    const restoreReturnPosition = () => {
      if (currentValue()) return false;
      const position = returnPosition(global.history.state);
      if (!position) return false;
      const schedule = typeof global.requestAnimationFrame === "function"
        ? callback => global.requestAnimationFrame(callback)
        : callback => global.setTimeout(callback, 0);
      // Consumers close their modal and release the body scroll lock in the
      // popstate turn. Restore one frame later than focus restoration so focus
      // cannot scroll the collection away from its saved viewport.
      schedule(() => schedule(() => global.scrollTo({
        left: position.x,
        top: position.y,
        behavior: "instant",
      })));
      return true;
    };
    const nextState = (detailValue, { managed, depth } = {}) => {
      const state = plainObject(global.history.state) ? { ...global.history.state } : {};
      const existing = marker(state);
      if (managed && detailValue) {
        state[HISTORY_STATE_KEY] = {
          owner,
          parameter,
          resourceId: detailValue,
        };
        if (Number.isSafeInteger(depth) && depth >= 0) {
          state[HISTORY_DEPTH_STATE_KEY] = depth;
        }
      } else if (existing?.owner === owner && !detailValue) {
        delete state[HISTORY_STATE_KEY];
        delete state[HISTORY_DEPTH_STATE_KEY];
      }
      return state;
    };
    const replace = (patch = {}) => {
      const url = nextUrl(patch);
      const detailValue = clean(url.searchParams.get(parameter));
      global.history.replaceState(
        nextState(detailValue, {
          managed: marker(global.history.state)?.owner === owner,
        }),
        "",
        relativeUrl(url),
      );
      acceptCurrent();
      return relativeUrl(url);
    };
    const open = (resourceId, patch = {}) => {
      const detailValue = clean(resourceId);
      if (!detailValue) return false;
      const url = nextUrl({ ...patch, [parameter]: detailValue });
      if (relativeUrl(url) === relativeUrl(currentUrl())) return false;
      if (isManaged()) {
        return push({ ...patch, [parameter]: detailValue });
      }
      rememberReturnPosition();
      global.history.pushState(
        nextState(detailValue, { managed: true, depth: 0 }),
        "",
        relativeUrl(url),
      );
      acceptCurrent();
      return true;
    };
    const isManaged = () => {
      const currentMarker = marker(global.history.state);
      return Boolean(
        currentValue()
        && currentMarker?.owner === owner
        && currentMarker?.parameter === parameter
        && clean(currentMarker.resourceId) === currentValue()
      );
    };
    const push = (patch = {}) => {
      const url = nextUrl(patch);
      if (relativeUrl(url) === relativeUrl(currentUrl())) return false;
      const detailValue = clean(url.searchParams.get(parameter));
      const managed = isManaged();
      global.history.pushState(
        nextState(detailValue, {
          managed,
          depth: managed ? navigationDepth(global.history.state) + 1 : undefined,
        }),
        "",
        relativeUrl(url),
      );
      acceptCurrent();
      return true;
    };
    const restoreAccepted = () => {
      if (relativeUrl(currentUrl()) === relativeUrl(acceptedLocation.url)) return false;
      const acceptedMarker = marker(acceptedLocation.state);
      const currentMarker = marker(global.history.state);
      const acceptedManaged = acceptedMarker?.owner === owner;
      const currentManaged = currentMarker?.owner === owner;
      const atCollection = !currentValue() && returnPosition(global.history.state);
      if (acceptedManaged && (currentManaged || atCollection)) {
        const currentDepth = currentManaged ? navigationDepth(global.history.state) : -1;
        const delta = navigationDepth(acceptedLocation.state) - currentDepth;
        if (delta) {
          global.history.go(delta);
          return true;
        }
      }
      global.history.replaceState(acceptedLocation.state, "", relativeUrl(acceptedLocation.url));
      return true;
    };
    const close = (patch = {}) => {
      if (isManaged()) {
        global.history.go(-(navigationDepth(global.history.state) + 1));
        return "back";
      }
      replace({ ...patch, [parameter]: "" });
      return "replace";
    };

    return Object.freeze({
      acceptCurrent,
      close,
      current: currentValue,
      isManaged,
      open,
      owner,
      parameter,
      push,
      replace,
      restoreAccepted,
      restoreReturnPosition,
    });
  }

  function registerAdapter(kind, adapter = {}) {
    const key = clean(kind);
    if (!key) throw new Error("Resource detail adapter kind is required");
    if (typeof adapter.location !== "function" && !clean(adapter.path)) {
      throw new Error(`Resource detail adapter “${key}” needs path or location`);
    }
    const registration = Object.freeze({ ...adapter, kind: key });
    adapters.set(key, registration);
    return () => adapters.get(key) === registration && adapters.delete(key);
  }

  function adapterFor(resource) {
    const normalized = normalizeResource(resource);
    return adapters.get(normalized.detailKind)
      || adapters.get(normalized.resourceType)
      || null;
  }

  function locationFor(resource, options = {}) {
    const normalized = normalizeResource(resource, options.detailKind);
    if (!normalized.resourceId) throw new Error("Resource ID is required");
    const adapter = options.adapter || adapterFor(normalized);
    if (!adapter) throw new Error(`No detail adapter registered for “${normalized.detailKind || normalized.resourceType}”`);
    if (typeof adapter.location === "function") {
      return String(adapter.location(normalized, options) || "");
    }
    const currentOrigin = clean(global.location?.origin);
    const url = new URL(
      adapter.path,
      currentOrigin && currentOrigin !== "null" ? currentOrigin : "http://localhost",
    );
    url.searchParams.set(adapter.param || "item", normalized.resourceId);
    if (options.embedded !== false) url.searchParams.set(adapter.embedParam || "embedded", "1");
    if (options.requestId) url.searchParams.set("detail_request", clean(options.requestId));
    if (plainObject(options.searchParams)) {
      Object.entries(options.searchParams).forEach(([key, value]) => {
        const normalizedValue = clean(value);
        if (normalizedValue) url.searchParams.set(String(key), normalizedValue);
        else url.searchParams.delete(String(key));
      });
    }
    return `${url.pathname}${url.search}${url.hash}`;
  }

  const detailRoutes = global.PortalResourceRoutes.routes.filter(route => route.embed_parameter);
  detailRoutes.forEach(route => registerAdapter(route.detail_kind, {
    path: route.path, param: route.parameter, embedParam: route.embed_parameter,
  }));

  function createGuest(options = {}) {
    const params = new URLSearchParams(global.location?.search || "");
    let destroyed = false;
    let current = normalizeResource({
      resourceId: clean(optionValue(options.resourceId))
        || detailRoutes.map(route => params.get(route.parameter)).find(Boolean),
      resourceType: optionValue(options.resourceType),
      detailKind: optionValue(options.detailKind),
    });
    let currentRequestId = clean(options.requestId || params.get("detail_request"));
    let closing = null;
    const embedded = options.embedded === undefined
      ? global.parent !== global && detailRoutes.some(route => ["1", "true"].includes(clean(params.get(route.embed_parameter)).toLowerCase()))
      : Boolean(options.embedded);

    if (embedded && document.body) document.body.dataset.resourceDetailEmbedded = "1";

    const snapshot = () => Object.freeze({
      embedded,
      requestId: currentRequestId,
      ...current,
    });
    const refresh = patch => {
      current = normalizeResource({
        ...current,
        ...(patch || {}),
        resourceId: patch?.resourceId
          ?? patch?.resource_id
          ?? (typeof options.resourceId === "function" ? optionValue(options.resourceId) : null)
          ?? current.resourceId,
        resourceType: patch?.resourceType
          ?? patch?.resource_type
          ?? (typeof options.resourceType === "function" ? optionValue(options.resourceType) : null)
          ?? current.resourceType,
        detailKind: patch?.detailKind
          ?? patch?.detail_kind
          ?? (typeof options.detailKind === "function" ? optionValue(options.detailKind) : null)
          ?? current.detailKind,
      });
      if (patch?.requestId !== undefined) currentRequestId = clean(patch.requestId);
      return snapshot();
    };
    const post = (action, detail = {}) => {
      const state = refresh(detail);
      if (!embedded || destroyed) return false;
      global.parent.postMessage({
        ...detail,
        type: MESSAGE_TYPE,
        action: clean(action),
        requestId: state.requestId,
        resourceId: state.resourceId,
        resourceType: state.resourceType,
        detailKind: state.detailKind,
      }, global.location.origin);
      return true;
    };
    const guardClose = async context => {
      const guard = context?.guardClose || options.guardClose;
      if (typeof guard !== "function") return true;
      return (await guard(snapshot(), context)) !== false;
    };
    const requestClose = async context => {
      if (destroyed) return false;
      const requested = refresh();
      if (closing?.resourceId === requested.resourceId && closing.requestId === requested.requestId) {
        return closing.result;
      }
      const transaction = { resourceId: requested.resourceId, requestId: requested.requestId };
      const isCurrent = () => !destroyed
        && refresh().resourceId === requested.resourceId
        && currentRequestId === requested.requestId;
      closing = transaction;
      transaction.result = (async () => {
        try {
          const approved = await guardClose(context);
          if (!isCurrent()) return false;
          if (!approved) {
            post("close-blocked");
            return false;
          }
          if (post("close")) return true;
          if (typeof options.fallbackClose === "function") {
            return (await options.fallbackClose(snapshot(), context)) !== false;
          }
          return false;
        } catch (error) {
          if (isCurrent()) post("close-blocked", { error: clean(error?.message || error) });
          return false;
        } finally {
          if (closing === transaction) closing = null;
        }
      })();
      return transaction.result;
    };
    const onMessage = async event => {
      if (destroyed || !embedded) return;
      if (event.origin !== global.location.origin || event.source !== global.parent) return;
      const message = event.data || {};
      if (message.type !== MESSAGE_TYPE || message.action !== "request-close") return;
      if (currentRequestId && clean(message.requestId) !== currentRequestId) return;
      if (current.resourceId && clean(message.resourceId) !== current.resourceId) return;
      await requestClose({ source: "host", event });
    };
    global.addEventListener("message", onMessage);

    return Object.freeze({
      changed: detail => post("changed", detail),
      deleted: detail => post("deleted", detail),
      destroy() {
        destroyed = true;
        global.removeEventListener("message", onMessage);
      },
      embedded: () => embedded,
      failed: detail => post("failed", detail),
      ready: detail => post("ready", detail),
      requestClose,
      setResource: patch => refresh(patch),
      snapshot,
    });
  }

  function createFrameHost(options = {}) {
    const overlay = element(options.overlay);
    const frame = element(options.frame);
    const loading = element(options.loading);
    if (!overlay || !frame) throw new Error("Frame detail host requires overlay and frame");
    const frameHost = element(options.frameHost) || frame.parentElement;
    if (!frameHost || frameHost === overlay || !overlay.contains(frameHost)) {
      throw new Error("Frame detail host requires a transport container inside the overlay");
    }
    const nestedSurface = frame.closest("[data-portal-detail-surface], .portalDetailSurface");
    if (nestedSurface && overlay.contains(nestedSurface)) {
      throw new Error("Frame detail transport cannot also be a detail surface");
    }
    overlay.classList.add("portalDetailFrameOverlay");
    frameHost.classList.add("portalDetailFrameHost");
    frameHost.dataset.portalDetailFrameHost = "";
    frame.classList.add("portalDetailFrame");
    loading?.classList.add("portalDetailFrameLoading");
    let state = {
      status: "empty",
      requestId: "",
      resourceId: "",
      resourceType: "",
      detailKind: "",
      changed: false,
      deleted: false,
      closePending: false,
      context: null,
    };
    let closeTimer = 0;
    let readyTimer = 0;
    let closeContext = null;
    let destroyed = false;
    let pendingOpen = null;
    let activeFrameLocation = "";
    let restoredFrameNavigation = false;

    const focusReadyError = () => {
      if (state.status !== "error"
        || global.PortalOverlay.top()?.element !== overlay
        || document.activeElement !== frame) return;
      const errorFocus = loading || overlay;
      if (!errorFocus.hasAttribute("tabindex")) errorFocus.tabIndex = -1;
      errorFocus.focus({ preventScroll: true });
    };
    const clearReadyTimer = () => {
      global.clearTimeout(readyTimer);
      readyTimer = 0;
    };
    const failReady = message => {
      if (state.status !== "loading") return;
      clearReadyTimer();
      state = { ...state, status: "error", closePending: false };
      const error = clean(message) || "Resource detail 加载超时，请按 Esc 返回后重试。";
      if (loading) loading.textContent = error;
      focusReadyError();
      options.onReadyTimeout?.(snapshot(), { error });
      emit("ready-timeout", { error });
    };

    const snapshot = () => Object.freeze({ ...state });
    const emit = (reason, detail = {}) => {
      options.onStateChange?.(snapshot(), reason, detail);
    };
    const replaceFrameLocation = url => {
      const target = clean(url) || "about:blank";
      if (frame.contentWindow) frame.contentWindow.location.replace(target);
      else frame.src = target;
    };
    const onFrameLoad = () => {
      if (destroyed || state.status !== "loading" || !activeFrameLocation || restoredFrameNavigation) return;
      let loadedRequestId;
      try {
        loadedRequestId = new URL(frame.contentWindow.location.href).searchParams.get("detail_request");
      } catch (_) {
        return;
      }
      if (!loadedRequestId || loadedRequestId === state.requestId) return;
      // Joint history traversal can commit the old iframe after cancelling open()'s
      // navigation. Reassert the accepted request, never accept the stale guest.
      restoredFrameNavigation = true;
      replaceFrameLocation(activeFrameLocation);
    };
    const updateLocation = (resourceId, context = {}) => {
      if (context.updateLocation === false) return;
      options.updateLocation?.(resourceId, snapshot(), context);
    };
    const finishClose = context => {
      const nextOpen = pendingOpen;
      pendingOpen = null;
      if (nextOpen) context = { ...context, updateLocation: false, source: "switch" };
      global.clearTimeout(closeTimer);
      closeTimer = 0;
      closeContext = null;
      clearReadyTimer();
      const closed = state;
      activeFrameLocation = "";
      state = {
        status: "empty",
        requestId: "",
        resourceId: "",
        resourceType: "",
        detailKind: "",
        changed: false,
        deleted: false,
        closePending: false,
        context: null,
      };
      dialog.close({ ...context, approved: true });
      overlay.hidden = true;
      overlay.classList.remove(options.readyClass || "ready");
      replaceFrameLocation("about:blank");
      updateLocation("", context);
      options.onClose?.(Object.freeze({ ...closed }), context);
      emit("closed", { closed, context });
      if (nextOpen && !destroyed) open(nextOpen.resource, nextOpen.context);
      return true;
    };
    const requestClose = context => {
      if (destroyed) return false;
      if (state.status === "empty") return true;
      if (state.deleted || state.status !== "ready" || !frame.contentWindow) return finishClose(context);
      if (state.closePending) return false;
      closeContext = context || {};
      state = { ...state, closePending: true };
      emit("close-requested", { context });
      frame.contentWindow.postMessage({
        type: MESSAGE_TYPE,
        action: "request-close",
        requestId: state.requestId,
        resourceId: state.resourceId,
        resourceType: state.resourceType,
        detailKind: state.detailKind,
      }, global.location.origin);
      closeTimer = global.setTimeout(() => {
        closeTimer = 0;
        closeContext = null;
        pendingOpen = null;
        state = { ...state, closePending: false };
        options.onCloseTimeout?.(snapshot(), context);
        emit("close-timeout", { context });
      }, Number(options.closeTimeout || 7500));
      return false;
    };
    const dialog = global.PortalUI.createDialogController({
      modal: overlay,
      openClass: options.openClass || "open",
      bodyClass: options.bodyClass,
      initialFocus: options.initialFocus || frame,
      backdropClose: options.backdropClose !== false,
      escapeClose: options.escapeClose !== false,
      requestClose,
      onOpen: () => { overlay.hidden = false; },
    });
    const open = (resource, context = {}) => {
      if (destroyed) return false;
      const normalized = normalizeResource(resource, context.detailKind);
      if (!normalized.resourceId) return false;
      if (state.status === "ready" && !state.closePending
        && state.resourceId === normalized.resourceId && state.detailKind === normalized.detailKind) return true;
      const id = requestId();
      const frameLocation = locationFor(normalized, {
        detailKind: normalized.detailKind,
        embedded: true,
        requestId: id,
        searchParams: context.searchParams,
      });
      if (state.status === "ready") {
        pendingOpen = { resource: normalized, context };
        requestClose({ source: "switch", updateLocation: false });
        return false;
      }
      pendingOpen = null;
      global.clearTimeout(closeTimer);
      closeTimer = 0;
      closeContext = null;
      clearReadyTimer();
      state = {
        status: "loading",
        requestId: id,
        resourceId: normalized.resourceId,
        resourceType: normalized.resourceType,
        detailKind: normalized.detailKind,
        changed: false,
        deleted: false,
        closePending: false,
        context,
      };
      overlay.hidden = false;
      overlay.classList.remove(options.readyClass || "ready");
      if (loading) {
        loading.textContent = typeof options.loadingText === "function"
          ? options.loadingText(normalized, context)
          : clean(options.loadingText) || window.PortalI18n.t("正在打开 Resource…");
      }
      frame.title = typeof options.title === "function"
        ? options.title(normalized, context)
        : clean(options.title) || window.PortalI18n.t("Resource detail");
      if (!dialog.isOpen()) dialog.open(context);
      const configuredReadyTimeout = Number(options.readyTimeout ?? 45_000);
      const readyTimeout = Number.isFinite(configuredReadyTimeout) && configuredReadyTimeout > 0
        ? configuredReadyTimeout
        : 45_000;
      readyTimer = global.setTimeout(() => failReady(), readyTimeout);
      activeFrameLocation = frameLocation;
      restoredFrameNavigation = false;
      replaceFrameLocation(frameLocation);
      updateLocation(normalized.resourceId, context);
      emit("loading", { resource: normalized, context });
      return true;
    };
    const onMessage = event => {
      if (destroyed || state.status === "empty") return;
      if (event.origin !== global.location.origin || event.source !== frame.contentWindow) return;
      const message = event.data || {};
      if (message.type !== MESSAGE_TYPE) return;
      if (clean(message.requestId) !== state.requestId) return;
      if (clean(message.resourceId) !== state.resourceId) return;
      if (message.action === "ready") {
        clearReadyTimer();
        state = { ...state, status: "ready" };
        overlay.classList.add(options.readyClass || "ready");
        options.onReady?.(snapshot(), message);
        emit("ready", { message });
        return;
      }
      if (message.action === "changed" || message.action === "deleted") {
        state = {
          ...state,
          changed: true,
          deleted: state.deleted || message.action === "deleted",
        };
        const callback = message.action === "deleted" ? options.onDeleted : options.onChanged;
        callback?.(snapshot(), message);
        emit(message.action, { message });
        return;
      }
      if (message.action === "failed") {
        clearReadyTimer();
        global.clearTimeout(closeTimer);
        closeTimer = 0;
        closeContext = null;
        pendingOpen = null;
        state = { ...state, status: "error", closePending: false };
        overlay.classList.remove(options.readyClass || "ready");
        if (loading) loading.textContent = clean(message.error || message.message) || window.PortalI18n.t("Resource detail 加载失败");
        focusReadyError();
        options.onFailed?.(snapshot(), message);
        emit("failed", { message });
        return;
      }
      if (message.action === "close-blocked") {
        global.clearTimeout(closeTimer);
        closeTimer = 0;
        closeContext = null;
        pendingOpen = null;
        state = { ...state, closePending: false };
        options.onCloseBlocked?.(snapshot(), message);
        emit("close-blocked", { message });
        return;
      }
      if (message.action === "close") finishClose({ ...closeContext, source: closeContext?.source || "guest", message });
    };
    global.addEventListener("message", onMessage);
    frame.addEventListener("load", onFrameLoad);
    frame.addEventListener("focus", focusReadyError);
    document.addEventListener("portal:overlaychange", focusReadyError);

    return Object.freeze({
      close: requestClose,
      destroy() {
        if (destroyed) return;
        destroyed = true;
        pendingOpen = null;
        global.clearTimeout(closeTimer);
        clearReadyTimer();
        global.removeEventListener("message", onMessage);
        frame.removeEventListener("load", onFrameLoad);
        frame.removeEventListener("focus", focusReadyError);
        document.removeEventListener("portal:overlaychange", focusReadyError);
        if (state.status !== "empty") finishClose({ updateLocation: false, source: "destroy" });
        dialog.destroy();
      },
      open,
      snapshot,
    });
  }

  function createLocalHost(options = {}) {
    const modal = element(options.modal);
    if (!modal) throw new Error("Local detail host requires a modal");
    let sequence = 0;
    let openSequence = 0;
    let destroyed = false;
    let abortController = null;
    let state = {
      status: "empty",
      requestId: "",
      resourceId: "",
      resourceType: "",
      detailKind: "",
      changed: false,
      deleted: false,
      context: null,
      payload: null,
      error: null,
    };
    const snapshot = () => Object.freeze({ ...state });
    const emit = (reason, detail = {}) => options.onStateChange?.(snapshot(), reason, detail);
    const dirty = context => Boolean(options.dirty?.(snapshot(), context));
    const requestDiscard = (context, reason = "close") => {
      const currentRequestId = state.requestId;
      const isCurrent = approved => approved !== false && !destroyed && currentRequestId === state.requestId;
      if (typeof options.confirmDiscard === "function") {
        const decision = options.confirmDiscard(snapshot(), context);
        return decision && typeof decision.then === "function"
          ? Promise.resolve(decision).then(isCurrent, () => false)
          : isCurrent(decision);
      }
      return Promise.resolve(global.PortalUI.confirm({
        title: window.PortalI18n.t("放弃未保存的修改？"),
        message: options.discardMessage || (
          reason === "switch"
            ? window.PortalI18n.t("切换资源后将丢失尚未保存的修改。")
            : window.PortalI18n.t("关闭后将丢失尚未保存的修改。")
        ),
        confirmLabel: window.PortalI18n.t("放弃修改"),
        tone: "danger",
      })).then(isCurrent, () => false);
    };
    const cleanup = (closed, context) => {
      abortController?.abort?.();
      abortController = null;
      sequence += 1;
      openSequence += 1;
      state = {
        status: "empty",
        requestId: "",
        resourceId: "",
        resourceType: "",
        detailKind: "",
        changed: false,
        deleted: false,
        context: null,
        payload: null,
        error: null,
      };
      if (context?.updateLocation !== false) options.updateLocation?.("", closed, context);
      options.onClose?.(closed, context);
      emit("closed", { closed, context });
    };
    let dialog = null;
    const close = context => {
      if (destroyed) return false;
      if (state.status === "empty") return true;
      const closed = snapshot();
      if (!dialog.close(context)) return false;
      cleanup(closed, context);
      return true;
    };
    dialog = global.PortalUI.createDialogController({
      modal,
      openClass: options.openClass || "open",
      bodyClass: options.bodyClass,
      initialFocus: options.initialFocus,
      backdropClose: options.backdropClose !== false,
      escapeClose: options.escapeClose !== false,
      dirty,
      confirmDiscard: context => requestDiscard(context, "close"),
      discardMessage: options.discardMessage,
      requestClose: context => close(context),
    });
    const open = async (resource, context = {}) => {
      if (destroyed) return { accepted: false };
      const requestedOpen = ++openSequence;
      const normalized = normalizeResource(resource, context.detailKind);
      if (!normalized.resourceId) return { accepted: false };
      if (
        state.status !== "empty"
        && state.resourceId !== normalized.resourceId
        && dirty(context)
      ) {
        const approved = await Promise.resolve(requestDiscard(context, "switch"));
        if (approved === false || destroyed || requestedOpen !== openSequence) return { accepted: false };
      }
      const currentSequence = ++sequence;
      abortController?.abort?.();
      abortController = typeof AbortController === "function" ? new AbortController() : null;
      state = {
        status: "loading",
        requestId: requestId(),
        resourceId: normalized.resourceId,
        resourceType: normalized.resourceType,
        detailKind: normalized.detailKind,
        changed: false,
        deleted: false,
        context,
        payload: normalized.payload ?? null,
        error: null,
      };
      if (!dialog.isOpen()) dialog.open(context);
      if (context.updateLocation !== false) options.updateLocation?.(normalized.resourceId, snapshot(), context);
      options.onLoading?.(normalized, snapshot(), context);
      emit("loading", { resource: normalized, context });
      try {
        const loader = context.load || options.load;
        const payload = typeof loader === "function"
          ? await loader(normalized, {
              context,
              requestId: state.requestId,
              signal: abortController?.signal,
            })
          : normalized.payload;
        if (currentSequence !== sequence) return { accepted: true, stale: true };
        abortController = null;
        state = { ...state, status: "ready", payload, error: null };
        (context.present || options.present)?.(payload, normalized, snapshot(), context);
        emit("ready", { resource: normalized, context });
        return { accepted: true, stale: false, payload };
      } catch (error) {
        if (currentSequence !== sequence || error?.name === "AbortError") {
          return { accepted: true, stale: true, error };
        }
        abortController = null;
        state = { ...state, status: "error", error };
        (context.onError || options.onError)?.(error, normalized, snapshot(), context);
        emit("error", { resource: normalized, context, error });
        return { accepted: true, stale: false, error };
      }
    };
    const mark = (action, detail = {}) => {
      if (destroyed || state.status === "empty") return snapshot();
      state = {
        ...state,
        changed: true,
        deleted: state.deleted || action === "deleted",
      };
      const callback = action === "deleted" ? options.onDeleted : options.onChanged;
      callback?.(snapshot(), detail);
      emit(action, detail);
      return snapshot();
    };
    return Object.freeze({
      close,
      destroy() {
        if (destroyed) return;
        destroyed = true;
        const closed = snapshot();
        if (state.status !== "empty") cleanup(closed, { updateLocation: false, source: "destroy" });
        dialog.close({ force: true });
        dialog.destroy();
      },
      markChanged: detail => mark("changed", detail),
      markDeleted: detail => mark("deleted", detail),
      open,
      snapshot,
    });
  }

  global.PortalResourceDetail = Object.freeze({
    MESSAGE_TYPE,
    adapterFor,
    createDetailHistory,
    createFrameHost,
    createGuest,
    createLocalHost,
    registerAdapter,
  });
}(window));
