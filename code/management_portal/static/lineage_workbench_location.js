(() => {
  "use strict";

  if (window.PortalLineageWorkbenchLocation) return;

  // The workbench's address: what the opening URL asks for, and keeping the
  // URL in step with the selected Lineage, Resident and program object.
  const DEPENDENCIES = Object.freeze([
    "openingNavigation", "registeredLineageIdForResident", "selectedResidentId", "state",
  ]);

  function initialResidentIdFromUrl() {
    const params = new URLSearchParams(window.location.search);
    return String(params.get("resident") || "").trim();
  }

  function initialLineageIdFromUrl() {
    const params = new URLSearchParams(window.location.search);
    return String(params.get("lineage") || "").trim();
  }

  function initialOpenChatFromUrl() {
    return new URLSearchParams(window.location.search).get("open_chat") === "1";
  }

  function consumeOpenChatNavigationIntent() {
    const target = new URL(window.location.href);
    if (!target.searchParams.has("open_chat")) return;
    target.searchParams.delete("open_chat");
    history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
  }

  function initialProgramItemIdFromUrl() {
    return String(new URLSearchParams(window.location.search).get("program_item") || "").trim();
  }

  function initialChatroomIdFromUrl() {
    const value = String(new URLSearchParams(window.location.search).get("chatroom") || "").trim();
    return /^chatroom_[0-9a-f]{12}$/.test(value) ? value : "";
  }

  function createLocation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchLocation is missing: ${missing.join(", ")}`);
    const {
      openingNavigation, registeredLineageIdForResident, selectedResidentId, state,
    } = dependencies;

    function syncHatgLocation(detail = {}) {
      if (!state.chatTarget && (
        state.chatNavigationError
        || (!state.loadAllComplete && (openingNavigation.residentId || openingNavigation.lineageId))
      )) return;
      const target = new URL(window.location.href);
      const lineageId = String(detail.lineageId || "").trim();
      const programItemId = String(detail.programItemId || "").trim();
      if (lineageId) target.searchParams.set("lineage", lineageId);
      else target.searchParams.delete("lineage");
      // A reload opens the graph of the URL's Resident: one of another
      // Lineage (the source of a moved subgraph) would take it back there.
      const residentId = String(target.searchParams.get("resident") || "").trim();
      const resident = residentId
        ? (state.residents || []).find(item => String(item?.resident_id || "").trim() === residentId)
        : null;
      const residentLineageId = resident ? registeredLineageIdForResident(resident) : "";
      if (lineageId && residentLineageId && residentLineageId !== lineageId) {
        target.searchParams.delete("resident");
        target.searchParams.delete("open_chat");
      }
      if (lineageId && programItemId) target.searchParams.set("program_item", programItemId);
      else target.searchParams.delete("program_item");
      history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
    }

    function syncChatTargetLocation(chatTarget = state.chatTarget) {
      if (!chatTarget) return;
      const target = new URL(window.location.href);
      if (chatTarget.kind !== "resident") return;
      target.searchParams.set("resident", chatTarget.id);
      if (chatTarget.lineage_id) target.searchParams.set("lineage", chatTarget.lineage_id);
      else target.searchParams.delete("lineage");
      history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
    }

    function clearDeletedLineageLocation(lineageId, residentIds) {
      const deletedId = String(lineageId || "").trim();
      const deletedResidents = residentIds instanceof Set
        ? residentIds
        : new Set(residentIds || []);
      const target = new URL(window.location.href);
      if (target.searchParams.get("lineage") === deletedId) {
        target.searchParams.delete("lineage");
        target.searchParams.delete("program_item");
      }
      if (deletedResidents.has(String(target.searchParams.get("resident") || "").trim())) {
        target.searchParams.delete("resident");
        target.searchParams.delete("open_chat");
      }
      history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
    }

    function clearDeletedResidentLocation(residentIds) {
      const deletedResidents = residentIds instanceof Set
        ? residentIds
        : new Set(residentIds || []);
      const target = new URL(window.location.href);
      const residentId = String(target.searchParams.get("resident") || "").trim();
      if (deletedResidents.has(residentId) && residentId !== selectedResidentId()) {
        target.searchParams.delete("resident");
        target.searchParams.delete("open_chat");
        target.searchParams.delete("program_item");
      }
      history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
    }

    return Object.freeze({
      syncHatgLocation, syncChatTargetLocation, clearDeletedLineageLocation,
      clearDeletedResidentLocation,
    });
  }

  window.PortalLineageWorkbenchLocation = Object.freeze({
    createLocation, dependencies: DEPENDENCIES, initialResidentIdFromUrl, initialLineageIdFromUrl,
    initialOpenChatFromUrl, consumeOpenChatNavigationIntent, initialProgramItemIdFromUrl,
    initialChatroomIdFromUrl,
  });
})();
