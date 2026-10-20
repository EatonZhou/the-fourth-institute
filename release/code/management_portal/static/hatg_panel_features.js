(() => {
  "use strict";

  if (window.PortalHatgPanelFeatures) return;

  const PROGRAM_ALIASES = Object.freeze({
    selectedCanvasObjectIds: "selectedObjectIds",
    canvasSelectionAnchorId: "selectionAnchorId",
    canvasSelectionDeleteInFlight: "selectionDeleteInFlight",
    programWireDraft: "wireDraft",
    programMutationInFlight: "mutationInFlight",
    canvasPlacementReservations: "placementReservations",
    manualProgramEventsInFlight: "manualEventsInFlight",
    programEventSourceResetsInFlight: "eventSourceResetsInFlight",
  });

  function createProgramController() {
    const state = {
      selectedObjectIds: new Set(),
      selectionAnchorId: "",
      selectionDeleteInFlight: false,
      wireDraft: null,
      mutationInFlight: false,
      placementReservations: [],
      manualEventsInFlight: new Set(),
      eventSourceResetsInFlight: new Set(),
    };

    function replaceSelection(ids) {
      state.selectedObjectIds = new Set(ids || []);
      return state.selectedObjectIds;
    }

    function reconcileSelection(isValid) {
      return replaceSelection([...state.selectedObjectIds].filter(isValid));
    }

    function clearSelection() {
      if (!state.selectedObjectIds.size) return false;
      replaceSelection();
      return true;
    }

    function setWireDraft(draft) {
      state.wireDraft = draft || null;
      return state.wireDraft;
    }

    function beginMutation() {
      state.mutationInFlight = true;
    }

    function endMutation() {
      state.mutationInFlight = false;
    }

    function beginSelectionDelete() {
      if (state.selectionDeleteInFlight) return false;
      state.selectionDeleteInFlight = true;
      return true;
    }

    function endSelectionDelete(selectedIds = []) {
      state.selectionDeleteInFlight = false;
      replaceSelection(selectedIds);
    }

    function beginKeyedOperation(collection, key) {
      if (collection.has(key)) return false;
      collection.add(key);
      return true;
    }

    function beginManualEvent(key) {
      return beginKeyedOperation(state.manualEventsInFlight, key);
    }

    function endManualEvent(key) {
      state.manualEventsInFlight.delete(key);
    }

    function beginEventSourceReset(key) {
      return beginKeyedOperation(state.eventSourceResetsInFlight, key);
    }

    function endEventSourceReset(key) {
      state.eventSourceResetsInFlight.delete(key);
    }

    function reconcileWireDraft(isValid) {
      if (state.wireDraft && !isValid(state.wireDraft)) setWireDraft(null);
      return state.wireDraft;
    }

    function prunePlacementReservations(actual, now = Date.now()) {
      state.placementReservations = state.placementReservations.filter(reservation => (
        reservation.expiresAt > now
        && !(actual || []).some(position => (
          Math.abs(position.x - reservation.x) < 1
          && Math.abs(position.y - reservation.y) < 1
        ))
      ));
      return state.placementReservations;
    }

    function reservePlacement(position, ttlMs = 30_000) {
      state.placementReservations.push({ ...position, expiresAt: Date.now() + ttlMs });
      return position;
    }

    function nextPlacement(actual, visibleBounds) {
      const positions = (actual || []).filter(position => (
        Number.isFinite(position.x) && Number.isFinite(position.y)
      ));
      prunePlacementReservations(positions);
      const occupied = [...positions, ...state.placementReservations];
      const isAvailable = candidate => !occupied.some(position => (
        Math.abs(position.x - candidate.x) < 18
        && Math.abs(position.y - candidate.y) < 20
      ));
      const hasVisibleBounds = [
        visibleBounds?.minX,
        visibleBounds?.maxX,
        visibleBounds?.minY,
        visibleBounds?.maxY,
      ].every(Number.isFinite);
      const axisPositions = (minimum, maximum, spacing) => {
        if (maximum <= minimum) return [(minimum + maximum) / 2];
        const values = [];
        for (let value = minimum; value <= maximum + 0.01; value += spacing) values.push(value);
        if (maximum - values.at(-1) > spacing * 0.55) values.push(maximum);
        return values;
      };
      const columns = hasVisibleBounds
        ? axisPositions(
          Math.max(10, Number(visibleBounds.minX) + 10),
          Math.min(90, Number(visibleBounds.maxX) - 10),
          20,
        )
        : [20, 42, 64, 86];
      const rows = hasVisibleBounds
        ? axisPositions(
          Math.max(12, Number(visibleBounds.minY) + 12),
          Math.min(88, Number(visibleBounds.maxY) - 12),
          23,
        )
        : [25, 50, 75];
      const grid = rows.flatMap(y => columns.map(x => ({ x, y })));
      const start = positions.length % grid.length;
      const candidates = [...grid.slice(start), ...grid.slice(0, start)];
      return reservePlacement(candidates.find(isAvailable) || grid[start] || { x: 50, y: 50 });
    }

    function clearForLineageChange() {
      replaceSelection();
      state.selectionAnchorId = "";
      setWireDraft(null);
      state.placementReservations = [];
    }

    return Object.freeze({
      state,
      replaceSelection,
      reconcileSelection,
      clearSelection,
      setWireDraft,
      beginMutation,
      endMutation,
      beginSelectionDelete,
      endSelectionDelete,
      beginManualEvent,
      endManualEvent,
      beginEventSourceReset,
      endEventSourceReset,
      reconcileWireDraft,
      prunePlacementReservations,
      reservePlacement,
      nextPlacement,
      clearForLineageChange,
    });
  }

  function attachAliases(target, features) {
    for (const [alias, field] of Object.entries(PROGRAM_ALIASES)) {
      Object.defineProperty(target, alias, {
        enumerable: true,
        configurable: false,
        get: () => features.program.state[field],
        set: value => { features.program.state[field] = value; },
      });
    }
    return target;
  }

  function createFeatures() {
    return Object.freeze({ program: createProgramController() });
  }

  window.PortalHatgPanelFeatures = Object.freeze({ createFeatures, attachAliases });
})();
