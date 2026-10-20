(() => {
  "use strict";

  if (window.PortalRefreshCoordinator) return;

  const sharedFetchers = new Map();

  function createSharedFetcher(channelName = window.PortalCompatibility.identifier("refresh_channel")) {
    if (sharedFetchers.has(channelName)) return sharedFetchers.get(channelName);

    const cache = new Map();
    const localInflight = new Map();
    const supportsBroadcastChannel = typeof BroadcastChannel === "function";
    let channel = null;
    const actor = () => String(window.PortalIdentity?.username?.() || "").trim();
    const cacheKey = (identity, key) => JSON.stringify([identity, key]);
    let identityRevision = 0;
    let invalidatedAt = 0;

    window.addEventListener(window.PortalIdentity?.eventName || "portal:identity-changed", () => {
      identityRevision += 1;
      invalidatedAt = Date.now();
      cache.clear();
      localInflight.clear();
    });

    function handleChannelMessage(event) {
      const message = event.data;
      if (!message || message.type !== "refresh-result" || !message.key || !actor() || message.actor !== actor()) return;
      const fetchedAt = Number(message.fetchedAt);
      const key = cacheKey(message.actor, String(message.key));
      if (!Number.isFinite(fetchedAt) || fetchedAt < invalidatedAt || fetchedAt < (cache.get(key)?.fetchedAt || 0)) return;
      cache.set(key, {
        value: message.value,
        fetchedAt,
      });
    }

    function openChannel() {
      if (channel || !supportsBroadcastChannel) return;
      channel = new BroadcastChannel(channelName);
      channel.addEventListener("message", handleChannelMessage);
    }

    function closeChannel() {
      channel?.removeEventListener("message", handleChannelMessage);
      channel?.close();
      channel = null;
    }

    window.addEventListener("pagehide", closeChannel);
    window.addEventListener("pageshow", event => {
      if (event.persisted) openChannel();
    });
    openChannel();

    function freshValue(key, maxAgeMs, freshAfter = 0) {
      const entry = cache.get(key);
      if (!entry || entry.fetchedAt < freshAfter || Date.now() - entry.fetchedAt > maxAgeMs) return undefined;
      return entry.value;
    }

    async function runFetch(key, fetcher, context) {
      if (localInflight.has(key)) return localInflight.get(key);
      const fetchedAt = Date.now();
      const request = Promise.resolve()
        .then(() => context.isCurrent() ? fetcher() : undefined)
        .then(value => {
          if (!context.isCurrent()) return undefined;
          cache.set(key, { value, fetchedAt });
          try {
            if (context.actor) channel?.postMessage({ type: "refresh-result", actor: context.actor, key: context.key, value, fetchedAt });
          } catch {
            // The local result remains available when a value cannot be cloned across tabs.
          }
          return value;
        })
        .finally(() => { if (localInflight.get(key) === request) localInflight.delete(key); });
      localInflight.set(key, request);
      return request;
    }

    async function fetchShared(rawKey, fetcher, options = {}) {
      const requestKey = String(rawKey || "");
      if (!requestKey) return fetcher();
      const requestActor = actor();
      const revision = identityRevision;
      const key = cacheKey(requestActor, requestKey);
      const context = {
        actor: requestActor,
        key: requestKey,
        isCurrent: () => revision === identityRevision && requestActor === actor(),
      };
      const maxAgeMs = Math.max(0, Number(options.maxAgeMs || 0));
      const dedupeWindowMs = Math.max(
        maxAgeMs,
        Number(options.dedupeWindowMs ?? 1500)
      );
      const freshAfter = Math.max(0, Number(options.freshAfter || 0));
      const cached = freshValue(key, maxAgeMs, freshAfter);
      if (cached !== undefined) return cached;

      if (!requestActor || !navigator.locks?.request) return runFetch(key, fetcher, context);
      return navigator.locks.request(`${window.PortalCompatibility.identifier("refresh_lock_prefix")}${JSON.stringify([channelName, requestActor, requestKey])}`, async () => {
        await new Promise(resolve => setTimeout(resolve, 25));
        if (!context.isCurrent()) return undefined;
        // A forced refresh still shares a request that another tab has just
        // completed. This prevents every restored tab from repeating the same
        // cold filesystem scan after a Portal restart.
        const shared = freshValue(key, dedupeWindowMs, freshAfter);
        return shared !== undefined ? shared : runFetch(key, fetcher, context);
      });
    }

    const sharedFetcher = { fetch: fetchShared };
    sharedFetchers.set(channelName, sharedFetcher);
    return sharedFetcher;
  }

  function create(options = {}) {
    let stopped = true;
    let destroyed = false;
    let generation = 0;
    let subscriptionGeneration = 0;
    let pendingInvalidation = false;
    let pendingInvalidationDelay = 0;
    let pendingForce = false;
    let requestController = null;
    let timer = null;
    let repairTimer = null;
    let reconnectTimer = null;
    let streamConnectTimer = null;
    let streamCleanup = null;
    let inFlight = null;
    let queuedForcedRefresh = null;
    let streamApplyInFlight = null;
    let pendingStreamUpdate = null;
    let activeKey = "";
    let reconnectAttempts = 0;
    let reconnectExhausted = false;
    const maxReconnectAttempts = Math.max(
      0,
      Math.min(10, Number(options.maxReconnectAttempts ?? 3) || 0),
    );
    const key = () => String(typeof options.key === "function" ? options.key() : options.key || "");
    const clearTimer = () => {
      if (timer) clearTimeout(timer);
      timer = null;
    };
    const clearRepairTimer = () => {
      if (repairTimer) clearTimeout(repairTimer);
      repairTimer = null;
    };
    const closeStream = () => {
      subscriptionGeneration += 1;
      if (streamConnectTimer) clearTimeout(streamConnectTimer);
      streamConnectTimer = null;
      clearRepairTimer();
      streamCleanup?.();
      streamCleanup = null;
    };
    const interval = () => {
      const configured = Math.max(250, Number(
        document.hidden
          ? (typeof options.hiddenInterval === "function" ? options.hiddenInterval() : (options.hiddenInterval || 15000))
          : (typeof options.interval === "function" ? options.interval() : (options.interval || 2000))
      ));
      return configured;
    };
    const scheduleRepair = () => {
      clearRepairTimer();
      if (stopped || !streamCleanup || document.hidden || options.repairInterval === false) return;
      const delay = Math.max(120000, Number(options.repairInterval || 300000));
      repairTimer = setTimeout(() => {
        repairTimer = null;
        refresh()
          .catch(() => {})
          .finally(scheduleRepair);
      }, delay);
    };
    const initialDelay = () => Math.max(0, Number(
      typeof options.initialDelay === "function" ? options.initialDelay() : (options.initialDelay || 0)
    ));
    const schedule = delay => {
      // A queued invalidation (including an HTTP retry) keeps its own deadline
      // when the realtime subscription disconnects or reconnects.
      if (pendingInvalidation) return;
      clearTimer();
      if (stopped || streamCleanup || options.interval === false || (document.hidden && options.pauseWhenHidden !== false) || (options.shouldRefresh && !options.shouldRefresh())) return;
      timer = setTimeout(() => {
        timer = null;
        refresh().catch(() => {});
      }, Math.max(0, Number(delay ?? interval())));
    };
    const invalidate = ({ delay = 0 } = {}) => {
      if (stopped) return;
      pendingInvalidation = true;
      pendingInvalidationDelay = Math.max(
        pendingInvalidationDelay,
        Math.max(0, Number(delay) || 0),
      );
      clearTimer();
      if (inFlight || (document.hidden && options.pauseWhenHidden !== false)) return;
      const requestedDelay = pendingInvalidationDelay;
      pendingInvalidationDelay = 0;
      timer = setTimeout(() => {
        timer = null;
        refresh().catch(() => {});
      }, requestedDelay);
    };
    const resetReconnectBudget = () => {
      reconnectAttempts = 0;
      reconnectExhausted = false;
    };
    const exhaustReconnects = error => {
      if (reconnectExhausted) return;
      reconnectExhausted = true;
      const terminal = error instanceof Error
        ? error
        : new Error("Portal 实时连接无法恢复。自动重连已停止。");
      terminal.retryable = false;
      terminal.code = terminal.code || "portal_reconnect_exhausted";
      options.onFatal?.(terminal);
    };
    const scheduleReconnect = error => {
      if (error?.retryable === false || reconnectAttempts >= maxReconnectAttempts) {
        exhaustReconnects(error);
        return;
      }
      reconnectAttempts += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!stopped && !document.hidden) connect();
      }, Math.max(1000, Number(options.reconnectInterval || 5000)));
    };
    const apply = async (update, context) => {
      if (update == null) return;
      await options.apply?.(update, context);
      if (context.isCurrent() && options.live?.(update) === false) {
        closeStream();
        schedule();
      }
    };
    const drainStreamUpdates = () => {
      if (streamApplyInFlight || !pendingStreamUpdate || (document.hidden && options.pauseWhenHidden !== false)) return;
      streamApplyInFlight = (async () => {
        while (pendingStreamUpdate && !(document.hidden && options.pauseWhenHidden !== false)) {
          const pending = pendingStreamUpdate;
          pendingStreamUpdate = null;
          if (!pending.context.isCurrent()) continue;
          try {
            await apply(pending.update, pending.context);
          } catch (error) {
            options.onError?.(error);
          }
        }
      })().finally(() => {
        streamApplyInFlight = null;
        if (pendingStreamUpdate) drainStreamUpdates();
      });
    };
    const refresh = async ({ force = false } = {}) => {
      if (stopped || (!force && document.hidden && options.pauseWhenHidden !== false)) {
        schedule();
        return null;
      }
      if (inFlight) {
        if (!force) return inFlight;
        pendingForce = true;
        if (!queuedForcedRefresh) {
          const queuedGeneration = generation;
          const queued = inFlight
            .catch(() => null)
            .then(() => {
              if (stopped || queuedGeneration !== generation) return null;
              const nextForce = pendingForce;
              pendingForce = false;
              queuedForcedRefresh = null;
              return refresh({ force: nextForce });
            })
            .finally(() => {
              if (queuedForcedRefresh === queued) queuedForcedRefresh = null;
            });
          queuedForcedRefresh = queued;
        }
        return queuedForcedRefresh;
      }
      clearTimer();
      pendingInvalidation = false;
      pendingInvalidationDelay = 0;
      pendingStreamUpdate = null;
      const requestKey = key();
      const requestGeneration = generation;
      const controller = new AbortController();
      requestController = controller;
      const context = {
        key: requestKey, force, signal: controller.signal,
        isCurrent: () => !stopped && requestGeneration === generation && requestKey === key(),
      };
      let followupDelay = null;
      const request = Promise.resolve().then(() => context.isCurrent() ? options.fetch?.(context) : null)
        .then(async update => {
          if (!context.isCurrent()) return null;
          if (document.hidden && options.pauseWhenHidden !== false && !force) {
            pendingStreamUpdate = { update, context };
          } else {
            await apply(update, context);
          }
          followupDelay = options.nextDelay?.(update, null);
          return update;
        })
        .catch(error => {
          if (!context.isCurrent()) return null;
          followupDelay = options.nextDelay?.(null, error);
          options.onError?.(error);
          throw error;
        })
        .finally(() => {
          if (inFlight !== request) return;
          inFlight = null;
          requestController = null;
          if (pendingInvalidation) {
            const requestedDelay = pendingInvalidationDelay;
            pendingInvalidation = false;
            pendingInvalidationDelay = 0;
            invalidate({ delay: requestedDelay });
          }
          else if (Number.isFinite(followupDelay)) invalidate({ delay: followupDelay });
          else schedule();
        });
      inFlight = request;
      return inFlight;
    };
    const connect = (pollDelay = 0) => {
      if (!pendingInvalidation) clearTimer();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      closeStream();
      if (
        stopped
        || document.hidden
        || reconnectExhausted
        || typeof options.subscribe !== "function"
      ) {
        schedule(pollDelay);
        return;
      }
      const requestKey = key();
      const requestGeneration = generation;
      const streamGeneration = subscriptionGeneration;
      const isCurrent = () => !stopped && requestGeneration === generation && requestKey === key() && streamGeneration === subscriptionGeneration;
      let streamOpened = false;
      try {
        const cleanup = options.subscribe({
          key: requestKey,
          onInvalidate: config => {
            if (isCurrent()) invalidate(config);
          },
          onUpdate: update => {
            if (!isCurrent()) return;
            streamOpened = true;
            resetReconnectBudget();
            if (streamConnectTimer) clearTimeout(streamConnectTimer);
            streamConnectTimer = null;
            pendingStreamUpdate = { update, context: { key: requestKey, isCurrent } };
            drainStreamUpdates();
          },
          onOpen: () => {
            if (!isCurrent()) return;
            streamOpened = true;
            resetReconnectBudget();
            if (streamConnectTimer) clearTimeout(streamConnectTimer);
            streamConnectTimer = null;
            scheduleRepair();
          },
          onError: error => {
            if (!isCurrent()) return;
            closeStream();
            options.onError?.(error);
            schedule(0);
            scheduleReconnect(error);
          },
        });
        // A subscriber can fail or stop the target before returning its cleanup.
        if (!isCurrent()) {
          if (typeof cleanup === "function") cleanup();
          return;
        }
        streamCleanup = typeof cleanup === "function" ? cleanup : null;
        if (streamCleanup && streamOpened) scheduleRepair();
        if (!streamCleanup) {
          schedule(0);
          scheduleReconnect(new Error("Portal realtime subscription unavailable"));
        } else if (!streamOpened) {
          streamConnectTimer = setTimeout(() => {
            streamConnectTimer = null;
            if (stopped || requestKey !== key() || !streamCleanup) return;
            closeStream();
            schedule(0);
            scheduleReconnect(new Error("Portal realtime connection timed out"));
          }, Math.max(250, Number(options.streamConnectTimeout || 3000)));
        }
      } catch (error) {
        if (!isCurrent()) return;
        options.onError?.(error);
        schedule(0);
        scheduleReconnect(error);
      }
    };
    const start = () => {
      if (destroyed) return;
      const wasStopped = stopped;
      stopped = false;
      const nextKey = key();
      const keyChanged = activeKey !== nextKey;
      if (keyChanged || wasStopped) {
        generation += 1;
        requestController?.abort();
        requestController = null;
        inFlight = null;
        queuedForcedRefresh = null;
        pendingForce = false;
        pendingInvalidation = false;
        pendingInvalidationDelay = 0;
        activeKey = nextKey;
        pendingStreamUpdate = null;
        closeStream();
        resetReconnectBudget();
      }
      if (!wasStopped && !keyChanged && (
        streamCleanup
        || inFlight
        || timer
        || reconnectTimer
        || streamConnectTimer
      )) return;
      connect(initialDelay());
    };
    const stop = () => {
      stopped = true;
      generation += 1;
      requestController?.abort();
      requestController = null;
      inFlight = null;
      queuedForcedRefresh = null;
      pendingForce = false;
      pendingInvalidation = false;
      pendingInvalidationDelay = 0;
      clearTimer();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      pendingStreamUpdate = null;
      clearRepairTimer();
      closeStream();
    };
    const visibility = () => {
      if (stopped) return;
      if (document.hidden) {
        if (options.pauseWhenHidden === false) return;
        clearTimer();
        clearRepairTimer();
        if (options.disconnectWhenHidden === true) closeStream();
        return;
      }
      if (!streamCleanup) connect();
      else scheduleRepair();
      if (pendingInvalidation) invalidate();
      else drainStreamUpdates();
    };
    document.addEventListener("visibilitychange", visibility);
    const destroy = () => {
      destroyed = true;
      stop();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("portal:events-ready", recoverEvents);
    };
    const reconnect = () => {
      resetReconnectBudget();
      connect();
    };
    const recoverEvents = () => {
      if (!stopped && reconnectExhausted) reconnect();
    };
    window.addEventListener("portal:events-ready", recoverEvents);
    return { start, stop, refresh, invalidate, reconnect, destroy, isRefreshing: () => !!inFlight };
  }

  function portalSubscription(options = {}) {
    const domains = new Set(
      (Array.isArray(options.domains) ? options.domains : [options.domains])
        .map(value => String(value || "").trim().toLowerCase())
        .filter(Boolean)
    );
    return ({ onInvalidate, onError, onOpen }) => {
      if (typeof window.EventSource !== "function") return null;
      let disposed = false;
      const refresh = () => {
        if (!disposed) onInvalidate({ delay: Math.max(0, Number(options.debounceMs ?? 100)) });
      };
      const handleChange = event => {
        const detail = event?.detail || {};
        if (typeof options.matches === "function" && !options.matches(detail)) return;
        const changedDomains = new Set(
          Array.isArray(detail.domains) ? detail.domains : []
        );
        if (
          detail.snapshot_required
          || !domains.size
          || [...domains].some(domain => changedDomains.has(domain))
        ) refresh();
      };
      const handleReady = event => {
        onOpen();
        if (event?.detail?.snapshot_required !== false) refresh();
      };
      const handleOpen = () => onOpen();
      const handleError = () => {
        const error = new Error("Portal event stream unavailable");
        error.code = "portal_event_stream_unavailable";
        onError(error);
      };
      window.addEventListener("portal:change", handleChange);
      window.addEventListener("portal:events-ready", handleReady);
      window.addEventListener("portal:events-open", handleOpen);
      window.addEventListener("portal:events-error", handleError);
      if (window.PortalEvents?.isConnected?.()) {
        onOpen();
        if (options.refreshOnAttach !== false) refresh();
      }
      return () => {
        disposed = true;
        window.removeEventListener("portal:change", handleChange);
        window.removeEventListener("portal:events-ready", handleReady);
        window.removeEventListener("portal:events-open", handleOpen);
        window.removeEventListener("portal:events-error", handleError);
      };
    };
  }

  window.PortalRefreshCoordinator = {
    create,
    createSharedFetcher,
    portalSubscription,
  };
})();
