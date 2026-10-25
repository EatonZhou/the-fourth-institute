(() => {
  "use strict";

  if (window.PortalLineageWorkbenchRecent) return;

  // The activity rail: the side views it switches between and the Residents the
  // user most recently sent a command to, kept per user and shown as shortcuts
  // that can be opened, dismissed or cleared.
  const DEPENDENCIES = Object.freeze([
    "fmtDateTime", "focusPreferredSidebarTarget", "registeredLineageIdForResident",
    "residentExactId", "residentRootForExactId", "selectedResidentId", "selectResident",
    "setSideView", "state",
  ]);

  const RECENT_AGENT_SESSION_LIMIT = 32;

  const RECENT_AGENT_SESSION_VISIBLE_LIMIT = 8;

  function recentAgentSessionKey(entry) {
    const residentId = String(entry?.resident_id || "").trim();
    return residentId ? `resident:${residentId}` : "";
  }

  function normalizeRecentAgentSession(raw) {
    if (!raw || typeof raw !== "object") return null;
    if (String(raw.type || "resident") !== "resident") return null;
    const t = Number(raw.t || 0) || Date.now();
    const residentId = String(raw.resident_id || "").trim();
    if (!residentId) return null;
    return {
      type: "resident", resident_id: residentId, t,
      title: String(raw.title || "").trim(),
      lineage_id: String(raw.lineage_id || "").trim(),
    };
  }

  function rememberRecentAgentSession(entry) {
    const normalized = normalizeRecentAgentSession({ ...entry, t: Date.now() });
    if (!normalized) return;
    const key = recentAgentSessionKey(normalized);
    window.PortalConversationStore?.remember?.({ ...normalized, key });
  }

  function clearRecentAgentSessions() {
    const removed = window.PortalConversationStore?.clearRecent?.() || [];
    if (!removed.length) return false;
    window.PortalUI?.toast?.(`已清空 ${removed.length} 个最近快捷入口。`, "success", {
      key: "lineage-recent-cleared",
      duration: 6000,
      retryLabel: "撤销",
      retry: () => {
        if (!window.PortalConversationStore?.restoreRecent?.(removed)) return;
        window.PortalUI?.toast?.("已恢复最近快捷入口。", "success", {
          key: "lineage-recent-restored",
          duration: 2600,
        });
      },
    });
    return true;
  }

  function rememberResidentShortcutSession(residentId, target = {}) {
    rememberRecentAgentSession({ ...target, type: "resident", resident_id: residentId });
  }

  function recentTimeMs(...values) {
    for (const value of values) {
      if (Number.isFinite(Number(value)) && Number(value) > 0) return Number(value);
      const parsed = Date.parse(String(value || ""));
      if (Number.isFinite(parsed) && parsed > 0) return parsed;
    }
    return 0;
  }

  function recentTitleGlyph(label) {
    const text = String(label || "").trim();
    const characters = Array.from(text);
    const firstMeaningful = characters.find(character => (
      /[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u.test(character)
    ));
    if (/^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]$/u.test(firstMeaningful || "")) {
      return firstMeaningful;
    }
    const englishWords = text.match(/[A-Za-z]+/g) || [];
    if (englishWords.length >= 2) {
      return `${englishWords[0][0]}${englishWords[1][0]}`.toUpperCase();
    }
    if (englishWords.length === 1) {
      return englishWords[0].slice(0, 2);
    }
    return characters.slice(0, 2).join("").toUpperCase() || "?";
  }

  function recentAccentHue(key) {
    let hash = 0;
    for (const character of String(key || "")) {
      hash = ((hash * 31) + character.codePointAt(0)) >>> 0;
    }
    return hash % 360;
  }

  function recentIdentity(item) {
    return String(item?.residentId || "").trim();
  }

  function createRecent(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchRecent is missing: ${missing.join(", ")}`);
    const {
      fmtDateTime, focusPreferredSidebarTarget, registeredLineageIdForResident, residentExactId,
      residentRootForExactId, selectedResidentId, selectResident, setSideView, state,
    } = dependencies;

    let lineageActivityRail = null;

    function dismissRecentAgentSession(key) {
      const cleanKey = String(key || "").trim();
      if (!cleanKey) return false;
      const label = recentAgentSessionInfos().find(item => item.key === cleanKey)?.label || window.PortalI18n.t("快捷入口");
      const removed = window.PortalConversationStore?.dismissRecent?.(cleanKey) === true;
      if (removed) {
        window.PortalUI?.toast?.(`已从快捷栏移除“${label}”。`, "success", {
          key: "lineage-recent-dismissed",
          duration: 2600,
        });
      }
      return removed;
    }

    function recentInfoForEntry(entry) {
      const residentId = String(entry?.resident_id || "").trim();
      if (!residentId) return null;
      // Navigation is a display projection, not the authority for a recent
      // conversation's existence. Keep acknowledged targets reachable even
      // when hidden from Navigation; opening still checks current access.
      const resident = residentRootForExactId(residentId)
        || (residentExactId(state.detail?.resident) === residentId ? state.detail.resident : null)
        || { resident_id: residentId, title: entry.title, lineage_id: entry.lineage_id };
      const label = String(resident.title || resident.resident_id || "Resident").trim();
      const active = selectedResidentId() === residentId;
      return {
        key: recentAgentSessionKey(entry),
        type: "resident",
        residentId,
        lineageId: registeredLineageIdForResident(resident) || String(entry.lineage_id || "").trim(),
        derived: false,
        label,
        subtitle: "Resident",
        glyph: recentTitleGlyph(label || "R"),
        active,
        t: Number(entry.t || 0) || recentTimeMs(resident?.updated_at, resident?.created_at),
      };
    }

    function recentAgentSessionInfos() {
      const byKey = new Map();
      for (const entry of window.PortalConversationStore?.recent?.() || []) {
        const key = recentAgentSessionKey(entry);
        if (key) byKey.set(key, entry);
      }
      return [...byKey.values()]
        .map(recentInfoForEntry)
        .filter(Boolean)
        .sort((a, b) => Number(b.t || 0) - Number(a.t || 0))
        .slice(0, RECENT_AGENT_SESSION_LIMIT);
    }

    function initLineageActivityRail() {
      lineageActivityRail?.destroy?.();
      lineageActivityRail = window.PortalWorkbench.createActivityRail({
        id: "lineage",
        root: "#lineageActivityRail",
        label: window.PortalI18n.t("Society 工作台视图"),
        itemsId: "activityRecentRail",
        itemsLabel: window.PortalI18n.t("最近发出命令的 Society 工作台会话"),
        activeView: state.sideView || "agents",
        views: [
          { id: "agents", icon: "users", label: "Agent" },
          { id: "chatrooms", icon: "chat-room", label: "Chatroom" },
          { id: "favorites", icon: "star", label: window.PortalI18n.t("我的收藏") },
          { id: "workspaces", icon: "folder", label: "Workspace", title: window.PortalI18n.t("查看当前对话的工作区、整理个人文件夹，并在 Preview 中浏览文件") },
        ],
        itemLimit: RECENT_AGENT_SESSION_VISIBLE_LIMIT,
        itemFallbackView: "agents",
        onViewSelect: view => setSideView(view, { userInitiated: true }),
        onItemDismiss: key => dismissRecentAgentSession(key),
        itemsAction: {
          icon: "trash",
          label: window.PortalI18n.t("清空最近 Resident 快捷入口"),
        },
        onItemsAction: () => clearRecentAgentSessions(),
        onItemSelect: async key => {
          const item = recentAgentSessionInfos().find(candidate => candidate.key === key);
          if (!item) return;
          if (item.residentId) {
            await selectResident(item.residentId, { lineageId: item.lineageId, forceView: true });
            focusPreferredSidebarTarget({ residentId: item.residentId });
          }
        },
      });
      renderActivityRecentRail();
      return lineageActivityRail;
    }

    function renderActivityRecentRail() {
      if (!lineageActivityRail) return;
      const items = recentAgentSessionInfos().slice(0, RECENT_AGENT_SESSION_VISIBLE_LIMIT);
      const glyphCounts = new Map();
      for (const item of items) glyphCounts.set(item.glyph, Number(glyphCounts.get(item.glyph) || 0) + 1);
      lineageActivityRail.setItems(items.map(item => ({
        ...(() => {
          const identity = recentIdentity(item);
          const suffix = identity.slice(-4).toUpperCase();
          const timeLabel = item.t ? fmtDateTime(new Date(Number(item.t || 0)).toISOString()) : "";
          const duplicateGlyph = Number(glyphCounts.get(item.glyph) || 0) > 1;
          return {
            key: item.key,
            kind: "resident",
            mark: duplicateGlyph ? suffix.slice(-2) : item.derived ? "D" : "",
            label: item.label,
            glyph: item.glyph,
            active: item.active,
            accentHue: recentAccentHue(identity),
            ariaLabel: [item.label, suffix ? window.PortalI18n.text`Resident 尾号 ${suffix}` : ""].filter(Boolean).join("，"),
            ariaDescription: timeLabel ? `最近使用 ${timeLabel}` : "",
            title: [item.label, item.subtitle, suffix ? window.PortalI18n.text`ID 尾号 ${suffix}` : "", timeLabel ? window.PortalI18n.text`最近使用 ${timeLabel}` : ""].filter(Boolean).join("\n"),
          };
        })(),
      })));
    }

    return Object.freeze({
      recentAgentSessionKey, dismissRecentAgentSession, rememberResidentShortcutSession,
      recentTimeMs, initLineageActivityRail, renderActivityRecentRail,
      currentLineageActivityRail: () => lineageActivityRail,
    });
  }

  window.PortalLineageWorkbenchRecent = Object.freeze({
    createRecent, dependencies: DEPENDENCIES, recentAgentSessionKey,
    rememberResidentShortcutSession, recentTimeMs,
  });
})();
