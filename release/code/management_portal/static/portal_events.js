(function () {
  "use strict";

  if (window.PortalEvents) return;

  // With the instruction requests this frame cancelled on purpose
  // (window.PortalInstructionClient), oldest first.
  function withClosedRequests(streams) {
    const requests = window.PortalInstructionClient?.closedRequests?.() || [];
    return [...streams, ...requests].sort((left, right) => String(left.at).localeCompare(String(right.at)));
  }

  if (window.top !== window) {
    const eventNames = [
      "portal:change",
      "portal:events-open",
      "portal:events-ready",
      "portal:events-resubscribed",
      "portal:events-error",
    ];
    const relays = [];
    const interests = new Set();
    let host = null;
    try {
      host = window.top;
      for (const name of eventNames) {
        const relay = event => window.dispatchEvent(
          new CustomEvent(name, { detail: event.detail || {} })
        );
        host.addEventListener(name, relay);
        relays.push([name, relay]);
      }
    } catch {
      host = null;
    }
    const close = () => {
      for (const release of [...interests]) release();
      if (!host) return;
      for (const [name, relay] of relays) host.removeEventListener(name, relay);
      relays.length = 0;
    };
    window.addEventListener("pagehide", event => {
      if (!event.persisted) close();
    });
    window.PortalEvents = {
      connect: () => host?.PortalEvents?.connect?.(),
      close,
      isConnected: () => !!host?.PortalEvents?.isConnected?.(),
      isLeader: () => false,
      closedStreams: () => withClosedRequests(host?.PortalEvents?.closedStreams?.() || []),
      retry: () => host?.PortalEvents?.retry?.(),
      watchResident: residentId => {
        const stop = host?.PortalEvents?.watchResident?.(residentId);
        const release = () => {
          if (!interests.delete(release)) return;
          stop?.();
        };
        interests.add(release);
        return release;
      },
    };
    return;
  }

  const clientId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let streamSequence = 0;
  const streamIdentities = new WeakMap();
  const staleStreamMs = 30_000;
  const watchdogIntervalMs = 5_000;
  const interestLeaseMs = 45_000;
  const handoverTimeoutMs = 10_000;
  const supportsBroadcastChannel = typeof BroadcastChannel === "function";
  const sharedLeadership = !!(supportsBroadcastChannel && navigator.locks?.request);
  let channel = null;
  let source = null;
  let sourceUrl = "";
  let sourceReady = false;
  // A changed subscription opens its stream before the current one closes.
  let nextSource = null;
  let nextSourceUrl = "";
  let handoverTimer = null;
  let connected = false;
  let leader = false;
  let stopped = false;
  let lockPending = false;
  let lockAbort = null;
  let releaseLeadership = null;
  let reconnectAttempts = 0;
  let hasConnected = false;
  let lastStreamSignalAt = 0;
  let watchdogTimer = null;
  let interestTimer = null;
  let presenceTimer = null;
  const residentInterests = new Map();
  const peerInterests = new Map();

  function localResidentIds() {
    return stopped || document.hidden ? [] : [...residentInterests.keys()].sort();
  }

  function publishInterests() {
    post({ type: "interests", residentIds: localResidentIds() });
  }

  function subscriptionUrl() {
    const residents = new Set(localResidentIds());
    if (sharedLeadership) {
      for (const [peer, interest] of peerInterests) {
        if (Date.now() - interest.receivedAt > interestLeaseMs) {
          peerInterests.delete(peer);
          continue;
        }
        for (const residentId of interest.residentIds) residents.add(residentId);
      }
    }
    const params = new URLSearchParams({ domains: "execution,resource,resident,lineage,projection" });
    if (residents.size) params.set("resident_ids", [...residents].sort().join(","));
    return `/api/events?${params}`;
  }

  function scheduleSubscriptionRefresh() {
    if (stopped || document.hidden || interestTimer !== null || (sharedLeadership && !leader)) return;
    interestTimer = window.setTimeout(() => {
      interestTimer = null;
      resubscribe();
    }, 150);
  }

  function residentIdsOf(url) {
    const value = new URL(url, window.location.href).searchParams.get("resident_ids") || "";
    return value.split(",").filter(Boolean);
  }

  // Streams this page closed on purpose, with why: the browser reports each
  // as a cancelled request (ERR_ABORTED). The instance identity also travels
  // on the request, so diagnostics need not infer causality from URL/time.
  const closedStreams = [];

  function recordClosed(stream, url, reason) {
    if (!stream) return;
    let address = String(url || "");
    try {
      const parsed = new URL(address, window.location.href);
      address = `${parsed.pathname}${parsed.search}`;
    } catch {
      // Keep the address as given.
    }
    const receipt = { url: address, stream_id: streamIdentities.get(stream), reason, at: new Date().toISOString() };
    closedStreams.push(receipt);
    if (closedStreams.length > 200) closedStreams.splice(0, closedStreams.length - 200);
    // Deliver before pagehide disposes this document and its diagnostic buffer.
    window.dispatchEvent(new CustomEvent("portal:connection-closed", { detail: { ...receipt } }));
  }

  function resubscribe() {
    const url = subscriptionUrl();
    if (url === sourceUrl) {
      closeNextSource("subscription_unchanged");
      return;
    }
    if (nextSource && url === nextSourceUrl) return;
    closeNextSource("subscription_changed");
    if (!source || !sourceReady) {
      closeSource("subscription_changed");
      openSource();
      return;
    }
    // The current stream keeps delivering until the new one is ready, so the
    // Residents both streams carry miss nothing and keep their Snapshots.
    nextSourceUrl = url;
    nextSource = createSource(url);
    handoverTimer = window.setTimeout(() => {
      handoverTimer = null;
      restartSource("handover_timeout");
    }, handoverTimeoutMs);
  }

  function completeHandover() {
    const previous = new Set(residentIdsOf(sourceUrl));
    const added = residentIdsOf(nextSourceUrl).filter(residentId => !previous.has(residentId));
    if (handoverTimer !== null) window.clearTimeout(handoverTimer);
    handoverTimer = null;
    recordClosed(source, sourceUrl, "handover_complete");
    source?.close();
    source = nextSource;
    sourceUrl = nextSourceUrl;
    nextSource = null;
    nextSourceUrl = "";
    markStreamSignal();
    // Added Residents were read before this stream carried their events.
    publishEvent("portal:events-resubscribed", { added_resident_ids: added });
  }

  function closeNextSource(reason = "next_stream_closed") {
    if (handoverTimer !== null) window.clearTimeout(handoverTimer);
    handoverTimer = null;
    recordClosed(nextSource, nextSourceUrl, reason);
    nextSource?.close();
    nextSource = null;
    nextSourceUrl = "";
  }

  function restartSource(reason = "restart") {
    closeSource(reason);
    openSource();
  }

  function watchResident(value) {
    const residentId = String(value || "").trim();
    if (!residentId) throw new Error("resident_id required");
    residentInterests.set(residentId, (residentInterests.get(residentId) || 0) + 1);
    publishInterests();
    scheduleSubscriptionRefresh();
    let released = false;
    return () => {
      if (released) return;
      released = true;
      const count = (residentInterests.get(residentId) || 0) - 1;
      if (count > 0) residentInterests.set(residentId, count);
      else residentInterests.delete(residentId);
      publishInterests();
      scheduleSubscriptionRefresh();
    };
  }

  function dispatch(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
  }

  function post(message) {
    try {
      channel?.postMessage({ ...message, sender: clientId });
    } catch {
      // This tab can keep using its own stream when cross-tab delivery fails.
    }
  }

  function openChannel() {
    if (channel || !supportsBroadcastChannel) return;
    channel = new BroadcastChannel(window.PortalCompatibility.identifier("events_channel"));
    channel.addEventListener("message", handleChannelMessage);
  }

  function closeChannel() {
    channel?.removeEventListener("message", handleChannelMessage);
    channel?.close();
    channel = null;
  }

  function publishEvent(name, detail) {
    dispatch(name, detail);
    post({ type: "event", name, detail: detail || {} });
  }

  function publishState() {
    post({ type: "state", connected: !!connected });
  }

  function parseEvent(event) {
    try {
      return JSON.parse(event.data || "{}");
    } catch {
      return {};
    }
  }

  function markStreamSignal() {
    lastStreamSignalAt = Date.now();
  }

  function stopWatchdog() {
    if (watchdogTimer === null) return;
    window.clearInterval(watchdogTimer);
    watchdogTimer = null;
  }

  function startWatchdog() {
    if (watchdogTimer !== null) return;
    watchdogTimer = window.setInterval(() => {
      if (stopped || document.hidden || !leader || !source) return;
      if (Date.now() - lastStreamSignalAt < staleStreamMs) return;
      reconnectAttempts += 1;
      publishEvent("portal:events-error", {
        attempt: reconnectAttempts,
        terminal: false,
        reason: "stale_stream",
      });
      restartSource("stale_stream");
    }, watchdogIntervalMs);
  }

  function closeSource(reason = "closed") {
    const ownedSource = !!source;
    closeNextSource(reason);
    recordClosed(source, sourceUrl, reason);
    source?.close();
    source = null;
    sourceUrl = "";
    sourceReady = false;
    if (interestTimer !== null) window.clearTimeout(interestTimer);
    interestTimer = null;
    connected = false;
    lastStreamSignalAt = 0;
    stopWatchdog();
    if (ownedSource) publishState();
  }

  function openSource() {
    if (
      source
      || stopped
      || document.hidden
      || (sharedLeadership && !leader)
    ) return;
    markStreamSignal();
    sourceUrl = subscriptionUrl();
    source = createSource(sourceUrl);
    startWatchdog();
  }

  function createSource(url) {
    const streamId = `${clientId}-${++streamSequence}`;
    const transportUrl = new URL(url, window.location.href);
    transportUrl.searchParams.set("portal_stream_id", streamId);
    const created = new EventSource(`${transportUrl.pathname}${transportUrl.search}`);
    streamIdentities.set(created, streamId);
    created.addEventListener("open", () => {
      if (created !== source) return;
      markStreamSignal();
      reconnectAttempts = 0;
      connected = true;
      publishState();
      publishEvent("portal:events-open", {});
    });
    created.addEventListener("portal-ready", event => {
      if (created === nextSource) {
        completeHandover();
        return;
      }
      if (created !== source) return;
      markStreamSignal();
      const reconnected = hasConnected;
      hasConnected = true;
      sourceReady = true;
      reconnectAttempts = 0;
      connected = true;
      publishState();
      publishEvent("portal:events-ready", {
        ...parseEvent(event),
        reconnected,
      });
    });
    created.addEventListener("portal-change", event => {
      if (created !== source) return;
      markStreamSignal();
      publishEvent("portal:change", parseEvent(event));
    });
    created.addEventListener("heartbeat", () => {
      if (created === source) markStreamSignal();
    });
    created.onerror = () => {
      if (created === nextSource) {
        restartSource("next_stream_error");
        return;
      }
      if (created !== source) return;
      // The browser reconnects this stream and its ready asks for a fresh read.
      closeNextSource("stream_error");
      sourceReady = false;
      reconnectAttempts += 1;
      connected = false;
      publishState();
      publishEvent("portal:events-error", {
        attempt: reconnectAttempts,
        terminal: false,
      });
    };
    return created;
  }

  function surrenderLeadership() {
    lockAbort?.abort();
    lockAbort = null;
    releaseLeadership?.();
    releaseLeadership = null;
    lockPending = false;
    if (!sharedLeadership) {
      leader = false;
      closeSource("leadership_released");
    }
  }

  function seekLeadership() {
    if (stopped || document.hidden) return;
    if (!sharedLeadership) {
      leader = true;
      openSource();
      return;
    }
    if (leader || lockPending) return;
    lockPending = true;
    lockAbort = new AbortController();
    navigator.locks.request(
      window.PortalCompatibility.identifier("events_leader_lock"),
      { mode: "exclusive", signal: lockAbort.signal },
      async () => {
        lockPending = false;
        lockAbort = null;
        if (stopped || document.hidden) return;
        leader = true;
        post({ type: "presence" });
        openSource();
        await new Promise(resolve => {
          releaseLeadership = resolve;
        });
        releaseLeadership = null;
        leader = false;
        closeSource("leadership_released");
      }
    ).catch(error => {
      lockPending = false;
      lockAbort = null;
      if (error?.name !== "AbortError") dispatch("portal:events-error", {});
    });
  }

  function connect() {
    stopped = false;
    openChannel();
    publishInterests();
    if (sharedLeadership && presenceTimer === null) {
      presenceTimer = window.setInterval(() => {
        publishInterests();
        scheduleSubscriptionRefresh();
      }, 10_000);
    }
    seekLeadership();
    post({ type: "presence" });
  }

  function close() {
    stopped = true;
    publishInterests();
    if (presenceTimer !== null) window.clearInterval(presenceTimer);
    presenceTimer = null;
    peerInterests.clear();
    // Closed here, before leadership goes, so that the stream says why.
    closeSource("page_closed");
    surrenderLeadership();
    stopWatchdog();
    closeChannel();
  }

  function handleChannelMessage(event) {
    const message = event.data || {};
    if (!message || message.sender === clientId) return;
    if (message.type === "presence") {
      if (leader) publishState();
      publishInterests();
      return;
    }
    if (message.type === "interests") {
      const residentIds = Array.isArray(message.residentIds)
        ? message.residentIds.filter(value => typeof value === "string" && value.trim())
        : [];
      if (residentIds.length) peerInterests.set(message.sender, { residentIds, receivedAt: Date.now() });
      else peerInterests.delete(message.sender);
      scheduleSubscriptionRefresh();
      return;
    }
    if (message.type === "state") {
      if (!leader) connected = !!message.connected;
      if (message.connected) hasConnected = true;
      return;
    }
    if (message.type !== "event" || leader) return;
    if (message.name === "portal:events-ready" || message.name === "portal:events-open") {
      connected = true;
      hasConnected = true;
    } else if (message.name === "portal:events-error") {
      connected = false;
    }
    dispatch(String(message.name || ""), message.detail || {});
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      publishInterests();
      surrenderLeadership();
      if (!leader) connected = false;
    } else {
      connect();
    }
  });
  window.addEventListener("pagehide", close);
  window.addEventListener("pageshow", event => {
    if (event.persisted) connect();
  });

  window.PortalEvents = {
    connect,
    close,
    watchResident,
    closedStreams: () => withClosedRequests(closedStreams.map(item => ({ ...item }))),
    isConnected: () => connected,
    isLeader: () => leader,
    retry: () => {
      reconnectAttempts = 0;
      closeSource("retry");
      connect();
    },
  };
  connect();
}());
