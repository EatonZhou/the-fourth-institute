(() => {
  "use strict";

  if (window.PortalHatgFamilyBlocks) return;

  // Function Blocks and Families on the HATG canvas: which objects a block or
  // Family contains, what a folded block or Family facade stands in for, each
  // viewer's open/closed preference, and the Family views and tones.
  const DEPENDENCIES = Object.freeze([
    "applyProgramMutation", "currentPageStateController", "displayNodeById", "esc",
    "executeHatgInstruction", "functionBlockInstanceById", "graphSelectedId",
    "graphVisibleModel", "isFunctionBlockInstance", "nodeById", "nodeResidentId",
    "programComponentById", "programEffectRuntime", "programEventSourceRuntime",
    "programExecutionExplanation", "programFeature", "renderLineageSurface",
    "scheduleHatgViewPreferenceSave", "separateGraphObject", "state",
  ]);

  // A Family (decision_44cf587c1721) shows as its facade: folded, the canvas has
  // the facade alone; expanded, it shows the steward, the members and the
  // Router Resident. Folding is each viewer's preference; Families start folded.
  function familyRoles(instance) {
    const family = instance?.family;
    const facade = String(family?.facade || "");
    if (!facade) return null;
    const steward = String(family.steward || "");
    const router = String(family.router || "");
    const members = Array.isArray(family.members) ? family.members.map(String) : [];
    return { facade, steward, router, members };
  }

  // Relations of a hidden Resident end at the facade that stands in for it, so
  // folding a Family never leaves its members' forks hanging; relations inside
  // one folded Family disappear with it. Program wires already end at the facade.
  function edgesThroughFoldedFamilies(edges, standIn) {
    if (!standIn.size) return edges;
    const shown = new Set();
    const key = (edge, source, target) => `${edge.type}\u0000${source}\u0000${target}`;
    const result = [];
    for (const edge of edges) {
      const source = standIn.get(edge.source) || edge.source;
      const target = standIn.get(edge.target) || edge.target;
      if (source === edge.source && target === edge.target) {
        shown.add(key(edge, edge.source, edge.target));
        result.push(edge);
      }
    }
    for (const edge of edges) {
      const source = standIn.get(edge.source) || edge.source;
      const target = standIn.get(edge.target) || edge.target;
      if (source === edge.source && target === edge.target) continue;
      if (source === target || shown.has(key(edge, source, target))) continue;
      shown.add(key(edge, source, target));
      result.push({ ...edge, source, target, source_port: "", target_port: "", through_family_facade: true, visual_only: true });
    }
    return result;
  }

  // Each Family has its own tone, shared by the facade and the cards it shows.
  const FAMILY_TONE_COUNT = 6;

  function familyTone(roles) {
    let hash = 0;
    for (const character of String(roles?.facade || "")) hash = (Math.imul(hash, 31) + character.codePointAt(0)) >>> 0;
    return hash % FAMILY_TONE_COUNT;
  }

  function createFamilyBlocks(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgFamilyBlocks is missing: ${missing.join(", ")}`);
    const {
      applyProgramMutation, currentPageStateController, displayNodeById, esc,
      executeHatgInstruction, functionBlockInstanceById, graphSelectedId, graphVisibleModel,
      isFunctionBlockInstance, nodeById, nodeResidentId, programComponentById,
      programEffectRuntime, programEventSourceRuntime, programExecutionExplanation,
      programFeature, renderLineageSurface, scheduleHatgViewPreferenceSave, separateGraphObject,
      state,
    } = dependencies;

    // The members of an expanded Family name it in a small tag.
    function familyMemberTag(node) {
      const member = node?._family_member;
      if (member?.role !== "member") return "";
      return window.PortalI18n.html`<span class="familyMemberTag" data-family-role="member" title="${esc(window.PortalI18n.text`Family「${member.title}」的成员`)}">${esc(member.title)}</span>`;
    }

    function functionBlockInstanceFailures(instance) {
      const issues = [];
      for (const component of instanceInnerObjects(instance, "components")) {
        const runtime = component.kind === "effect" ? programEffectRuntime(component.id) : programEventSourceRuntime(component.id);
        const explanation = programExecutionExplanation(component.id);
        if (runtime.status !== "failed" && explanation?.tone !== "danger") continue;
        const error = String(runtime.error || explanation?.blocked_reason || explanation?.why || explanation?.summary || "执行失败").slice(0, 2000);
        issues.push({ ...component, error, at: runtime.at || "", fingerprint: JSON.stringify([component.id, runtime.step_id || runtime.trigger_path_id || runtime.at || "", error]) });
      }
      for (const resident of instanceInnerObjects(instance, "nodes")) {
        const execution = resident.last_execution || {};
        if (execution.status !== "failed") continue;
        const error = String(execution.error || execution.error_code || "执行失败").slice(0, 2000);
        const at = execution.finished_at || execution.started_at || execution.created_at || "";
        // A retry may keep the input and error text; it is a new failure only
        // when the execution identity changes. Layout/title updates are not failures.
        issues.push({ ...resident, kind: "resident", error, at, fingerprint: JSON.stringify([resident.id, execution.execution_id || execution.message_id || at]) });
      }
      return issues;
    }

    function functionBlockInstanceUnacknowledgedFailures(instance) {
      const acknowledged = currentPageStateController()?.read()?.functionBlockFailureAcknowledgements || {};
      return functionBlockInstanceFailures(instance).filter(issue => acknowledged[`${state.lineage.lineage_id}:${issue.id}`] !== issue.fingerprint);
    }

    function acknowledgeFunctionBlockFailures(issues) {
      const acknowledged = { ...(currentPageStateController().read().functionBlockFailureAcknowledgements || {}) };
      for (const issue of issues) {
        const key = `${state.lineage.lineage_id}:${issue.id}`;
        delete acknowledged[key];
        acknowledged[key] = issue.fingerprint;
      }
      currentPageStateController().write({ functionBlockFailureAcknowledgements: Object.fromEntries(Object.entries(acknowledged).slice(-100)) });
      renderLineageSurface();
    }

    // Membership is explicit and may contain nested Function Blocks.
    function functionBlockElements(block, seen = new Set()) {
      if (!block || seen.has(block.id)) return [];
      seen.add(block.id);
      return (block.elements || []).flatMap(element => [
        element,
        ...(element.kind === "function_block"
          ? functionBlockElements(functionBlockInstanceById(element.id), seen) : []),
      ]);
    }

    function instanceInnerObjects(block, collection) {
      const kind = collection === "nodes" ? "resident" : collection === "components" ? "component" : "wire";
      const ids = new Set(functionBlockElements(block).filter(element => element.kind === kind).map(element => element.id));
      return (collection === "nodes" ? state.lineage?.nodes || [] : state.lineage?.program?.[collection] || []).filter(item => ids.has(item.id));
    }

    function instanceManualSources(block) {
      return instanceInnerObjects(block, "components").filter(component =>
        component.kind === "event_source" && component.config?.event_type === "manual.triggered");
    }

    function functionBlockInstanceTriggersHtml(block, readonly, covered = new Set()) {
      const rows = instanceManualSources(block).filter(component => !covered.has(component.id)).map(component => window.PortalI18n.html`<div><dt>${esc(component.title || component.id)}</dt><dd><button class="portalButton" type="button" data-function-block-trigger="${esc(component.id)}"${readonly ? " disabled" : ""}>触发</button></dd></div>`);
      return rows.length ? window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockInstanceTriggers"><span>手动触发</span><dl>${rows.join("")}</dl></section>` : "";
    }

    function expandedFunctionBlockIds() {
      try {
        const value = JSON.parse(localStorage.getItem(functionBlockPreferenceKey()) || "[]");
        return new Set(Array.isArray(value) ? value : []);
      } catch (_) { return new Set(); }
    }

    function functionBlockPreferenceKey() {
      return `portal.function-blocks.expanded.${state.lineage?.lineage_id || ""}`;
    }

    function setFunctionBlockExpanded(id, expanded) {
      const lineageId = state.lineage?.lineage_id;
      const ids = expandedFunctionBlockIds();
      if (expanded) ids.add(id); else ids.delete(id);
      try { localStorage.setItem(functionBlockPreferenceKey(), JSON.stringify([...ids])); } catch (_) { /* Local preferences may be unavailable. */ }
      renderLineageSurface();
      window.requestAnimationFrame(() => {
        if (state.lineage?.lineage_id !== lineageId || state.graphViewMode !== "context"
            || expandedFunctionBlockIds().has(id) !== expanded) return;
        state.graphController?.refresh?.({ measureNodes: true });
        if (expanded) {
          const boundary = graphVisibleModel().functionBlockBoundaries.find(block => block.id === id);
          if (!boundary) return;
          separateGraphObject(id).catch(() => {});
          // Fit visible members together with their enclosing block boundaries.
          state.graphController?.fit?.({ nodeIds: boundary.member_ids, duration: 220, maxScale: 1.15 });
        } else {
          state.graphController?.ensureNodeVisible?.(id);
        }
      });
    }

    // Folded outer blocks take precedence over nested blocks and Family facades.
    function functionBlockProjection(nodes, edges) {
      const blocks = state.lineage?.function_blocks || [];
      const expanded = expandedFunctionBlockIds();
      const standIn = new Map();
      const parent = new Map();
      for (const block of blocks) for (const element of block.elements || []) parent.set(element.id, block.id);
      for (const block of blocks) {
        if (block.family || expanded.has(block.id)) continue;
        for (const element of functionBlockElements(block)) standIn.set(element.id, block.id);
      }
      const representative = id => {
        const seen = new Set();
        let current = id, visible = id;
        while (parent.has(current) && !seen.has(current)) {
          seen.add(current);
          current = parent.get(current);
          const owner = functionBlockInstanceById(current);
          if (owner && !owner.family && !expanded.has(current)) visible = current;
        }
        return visible;
      };
      for (const id of standIn.keys()) standIn.set(id, representative(id));
      const visibleNodes = nodes.filter(node => !standIn.has(node.id)
        && !(isFunctionBlockInstance(node) && !node.family && expanded.has(node.id)));
      const merged = new Map();
      const visibleEdges = [];
      for (const edge of edges) {
        const source = standIn.get(edge.source) || edge.source;
        const target = standIn.get(edge.target) || edge.target;
        if (source === target && (source !== edge.source || target !== edge.target)) continue;
        if (source === edge.source && target === edge.target) { visibleEdges.push(edge); continue; }
        const key = `${source}\u0000${target}`;
        if (merged.has(key)) { merged.get(key).merged_edge_ids.push(edge.id); continue; }
        const projected = { ...edge, source, target, source_port: "", target_port: "",
          through_function_block: true, visual_only: true, merged_edge_ids: [edge.id] };
        merged.set(key, projected);
        visibleEdges.push(projected);
      }
      const visibleIds = new Set(visibleNodes.map(node => node.id));
      const boundaries = blocks.filter(block => !block.family && expanded.has(block.id) && !standIn.has(block.id))
        .map(block => ({ id: block.id, title: block.title, x: block.x, y: block.y,
          child_block_ids: (block.elements || []).filter(element => element.kind === "function_block" && expanded.has(element.id)).map(element => element.id),
          member_ids: [...new Set(functionBlockElements(block)
          .map(element => standIn.get(element.id) || element.id).filter(id => visibleIds.has(id)))] }));
      return { nodes: visibleNodes, edges: visibleEdges, boundaries };
    }

    function graphObjectTitle(objectId) {
      const block = functionBlockInstanceById(objectId);
      if (block) return block.title || objectId;
      const node = displayNodeById(objectId) || nodeById(objectId);
      if (node) return node.title || objectId;
      const component = programComponentById(objectId);
      return component ? (component.title || component.kind || objectId) : objectId;
    }

    function familyInstances() {
      return (state.lineage?.function_blocks || [])
        .map(instance => ({ instance, roles: familyRoles(instance) }))
        .filter(family => family.roles);
    }

    function familyForFacade(residentId) {
      const id = String(residentId || "");
      return id ? familyInstances().find(family => family.roles.facade === id) || null : null;
    }

    function familyExpanded(roles) {
      return Boolean(roles && state.expandedFamilyFacadeIds.has(roles.facade));
    }

    function setFamilyExpanded(facadeId, expanded, { render = true, returnTo = null } = {}) {
      const id = String(facadeId || "");
      if (!id) return;
      if (expanded) state.expandedFamilyFacadeIds.add(id); else state.expandedFamilyFacadeIds.delete(id);
      scheduleHatgViewPreferenceSave(state.lineage?.lineage_id, {
        expanded_family_facade_ids: [...state.expandedFamilyFacadeIds],
      });
      if (!render) return;
      renderLineageSurface();
      window.requestAnimationFrame(() => {
        state.graphController?.refresh?.({ measureNodes: true });
        // Leaving a Family view returns to the canvas as it was, or to the whole canvas.
        if (returnTo === "whole") {
          state.graphController?.fit?.({ duration: 260, maxScale: 1.45 });
          return;
        }
        if (returnTo) {
          state.graphController?.setTransform?.(returnTo, { duration: 260 });
          return;
        }
        // Expanding brings the whole Family into view, wherever its members stand.
        const family = expanded && !state.graphFocusMode ? familyForFacade(id) : null;
        if (family) {
          state.graphController?.fit?.({ nodeIds: [...familyObjectIds(family)], duration: 260, maxScale: 1.15 });
        }
      });
    }

    // A Family's Router and steward live inside its pigeon: they are
    // never cards, and their relations end at the pigeon. A folded Family also
    // hides its pigeon and members behind the facade; an inner Family folds with the outer
    // one whatever its own state, pigeon and all. `standIn` names the visible
    // card that represents each hidden object on the canvas.
    function foldedFamilyObjects() {
      const families = familyInstances();
      const byFacade = new Map(families.map(family => [family.roles.facade, family]));
      const residents = new Set();
      const instances = new Set();
      const standIn = new Map();
      for (const family of families) {
        for (const element of functionBlockElements(family.instance)) {
          if (element.kind !== "component") continue;
          // Ordinary workflows are visible when expanded; roster machinery stays
          // behind the Family card. Folding either ancestor hides the workflow.
          if (String(element.key || "").startsWith("workflow:") && familyExpanded(family.roles)) continue;
          residents.add(element.id);
          standIn.set(element.id, family.instance.id);
        }
        for (const id of [family.roles.steward, family.roles.router]) {
          if (!id) continue;
          residents.add(id);
          standIn.set(id, family.instance.id);
        }
      }
      const hide = (family, facade) => {
        instances.add(family.instance.id);
        standIn.set(family.instance.id, facade);
        for (const id of family.roles.members) {
          if (!id || residents.has(id)) continue;
          residents.add(id);
          standIn.set(id, facade);
          const inner = byFacade.get(id);
          if (inner && inner !== family && !instances.has(inner.instance.id)) {
            hide(inner, facade);
          }
        }
      };
      for (const family of families) {
        if (!familyExpanded(family.roles)) hide(family, family.roles.facade);
      }
      for (const [id, target] of standIn) {
        let visible = target;
        for (let hops = 0; standIn.has(visible) && hops <= families.length * 2; hops += 1) visible = standIn.get(visible);
        standIn.set(id, visible);
      }
      return { residents, instances, standIn };
    }

    // A selected Family pigeon draws thin lines to its facade and the members on
    // the canvas; unselected, it draws none.
    function selectedFamilyPigeonId() {
      const selectedId = graphSelectedId();
      return familyInstances().some(item => item.instance.id === selectedId) ? selectedId : "";
    }

    function familyPigeonLinks() {
      const family = familyInstances().find(item => item.instance.id === selectedFamilyPigeonId());
      if (!family) return [];
      return [family.roles.facade, ...family.roles.members].filter(Boolean).map(target => ({
        id: `${family.instance.id}:family:${target}`,
        type: "familyLink",
        family: "context",
        source: family.instance.id,
        target,
        visual_only: true,
      }));
    }

    // Everything an expanded Family shows on the canvas, with expanded inner Families.
    function familyObjectIds(family, seen = new Set()) {
      const ids = new Set();
      if (!family || seen.has(family.instance.id)) return ids;
      seen.add(family.instance.id);
      const { instance, roles } = family;
      for (const id of [roles.facade, ...roles.members, instance.id]) if (id) ids.add(id);
      for (const element of instance.elements || []) {
        if (element.kind === "component" && String(element.key || "").startsWith("workflow:")) ids.add(element.id);
      }
      for (const member of roles.members) {
        const inner = familyForFacade(member);
        if (inner && familyExpanded(inner.roles)) for (const id of familyObjectIds(inner, seen)) ids.add(id);
      }
      return ids;
    }

    // Cards shown by an expanded Family carry its tone; its Residents also name it.
    function familyMembership(hidden) {
      const membership = new Map();
      for (const family of familyInstances()) {
        const { instance, roles } = family;
        if (!familyExpanded(roles) || hidden.has(roles.facade)) continue;
        const mark = { tone: familyTone(roles), title: String(instance.title || "Family"), facade: roles.facade };
        for (const id of roles.members) if (id) membership.set(id, { ...mark, role: "member" });
        for (const element of instance.elements || []) {
          if (element.kind === "component" && String(element.key || "").startsWith("workflow:")) {
            membership.set(element.id, { ...mark, role: "inner" });
          }
        }
        membership.set(instance.id, { ...mark, role: "inner" });
      }
      return membership;
    }

    // 邻域 on a facade shows the Family alone, where its cards stand.
    function focusedFamily() {
      if (!state.graphFocusMode || state.graphViewMode !== "context") return null;
      const family = familyForFacade(state.graphFocusFamilyFacadeId);
      return family && familyExpanded(family.roles) ? family : null;
    }

    // Selecting an expanded Family's facade brings the Family forward; the rest of
    // the canvas fades but stays usable.
    function familySpotlight() {
      if (state.graphViewMode !== "context" || state.graphFocusMode) return null;
      const family = familyForFacade(graphSelectedId());
      if (!family || !familyExpanded(family.roles)) return null;
      // A newly folded parent may hide the selected inner facade. Its saved
      // selection must not fade the visible parent or the rest of the canvas.
      return foldedFamilyObjects().residents.has(family.roles.facade) ? null : family;
    }

    // A Resident made where an expanded Family is shown joins that Family: on one
    // of its cards, or anywhere in its own view. A folded Family's facade makes one
    // outside.
    function familyForNewResident(nodeId) {
      const id = String(nodeId || "");
      const own = familyForFacade(id);
      if (own && familyExpanded(own.roles)) return own;
      const holder = familyInstances().find(family => family.roles.members.includes(id) || family.roles.steward === id);
      if (holder && familyExpanded(holder.roles)) return holder;
      return focusedFamily();
    }

    // A facade's "+" shows its Family alone, as 邻域 on the facade does; "−"
    // folds it back into the facade and returns to the canvas as it was before "+".
    function toggleFamilyFacade(node) {
      const family = familyForFacade(node?.id);
      if (!family) return false;
      toggleFamilyView(family);
      return true;
    }

    function toggleFamilyView(family) {
      if (!familyExpanded(family.roles)) return showFamilyAlone(family);
      let returnTo = null;
      if (focusedFamily()?.roles.facade === family.roles.facade) {
        returnTo = familyViewReturn(family) || "whole";
        state.graphFocusMode = false;
        state.graphFocusFamilyFacadeId = "";
      }
      state.familyViewReturn = null;
      setFamilyExpanded(family.roles.facade, false, { returnTo });
    }

    // The view the canvas had before this Family's view, while that view is still
    // the Lineage on screen.
    function familyViewReturn(family) {
      const saved = state.familyViewReturn;
      return saved
        && saved.facadeId === family.roles.facade
        && saved.lineageId === String(state.lineage?.lineage_id || "")
        ? saved.transform
        : null;
    }

    // A Family's local view uses a compact projection, without moving the saved
    // objects. “全图” restores their actual positions with the Family still open.
    function showFamilyAlone(family) {
      if (state.graphViewMode !== "context") return setFamilyExpanded(family.roles.facade, true);
      // A deep link may name a Family hidden by a folded ancestor.
      for (const ancestor of familyInstances()) {
        if (functionBlockElements(ancestor.instance).some(element => element.id === family.instance.id)) {
          setFamilyExpanded(ancestor.roles.facade, true, { render: false });
        }
      }
      const transform = focusedFamily() ? null : state.graphController?.getTransform?.();
      state.familyViewReturn = transform ? {
        facadeId: family.roles.facade,
        lineageId: String(state.lineage?.lineage_id || ""),
        transform,
      } : null;
      state.graphFocusMode = true;
      state.graphFocusFamilyFacadeId = family.roles.facade;
      setFamilyExpanded(family.roles.facade, true, { render: false });
      renderLineageSurface();
      window.requestAnimationFrame(() => {
        state.graphController?.refresh?.({ measureNodes: true });
        state.graphController?.fit?.({ duration: 220, maxScale: 1, padding: 40 });
      });
    }

    function revealUnfamilizedMember(lineageId, result) {
      const retained = new Set(result?.member_resident_ids || []);
      if (!retained.size) return;
      const controller = state.graphController;
      window.requestAnimationFrame(() => {
        if (state.lineage?.lineage_id !== lineageId || state.graphController !== controller
          || state.graphViewMode !== "context") return;
        const node = graphVisibleModel().nodes.find(item => retained.has(nodeResidentId(item)));
        if (!node) return;
        // The removed facade may have been the only card in view. Reveal a
        // surviving member (including a nested facade), without selecting it or
        // moving the stored graph. A card already in view keeps the camera.
        controller?.refresh?.({ measureNodes: true });
        controller?.ensureNodeVisible?.(node.id, { persist: true });
      });
    }

    async function removeFamilyInstance(instanceId, action = "unfamilize") {
      const instance = functionBlockInstanceById(instanceId);
      if (!instance) return;
      const lineageId = state.lineage.lineage_id;
      const response = await window.PortalNav.removeFamily({
        family: instance, lineageId, action,
        execute: (instruction, args) => applyProgramMutation(
          () => executeHatgInstruction(instruction, args),
          action === "delete" ? "Family 及其内部对象已删除。" : "Family 已解除。",
        ),
      });
      if (!response) return;
      const roles = familyRoles(instance);
      if (roles) setFamilyExpanded(roles.facade, false);
      state.selectedProgramObjectId = "";
      programFeature.clearSelection();
      if (action === "unfamilize") revealUnfamilizedMember(lineageId, response);
      // The removed facade's Chat reports it deleted; the Leader is not opened
      // in its place.
      return response;
    }

    return Object.freeze({
      familyMemberTag, functionBlockInstanceFailures,
      functionBlockInstanceUnacknowledgedFailures, acknowledgeFunctionBlockFailures,
      functionBlockElements, instanceInnerObjects, instanceManualSources,
      functionBlockInstanceTriggersHtml, expandedFunctionBlockIds, setFunctionBlockExpanded,
      functionBlockProjection, graphObjectTitle, familyRoles, familyForFacade, familyExpanded,
      foldedFamilyObjects, edgesThroughFoldedFamilies, selectedFamilyPigeonId, familyPigeonLinks,
      familyTone, familyObjectIds, familyMembership, focusedFamily, familySpotlight,
      familyForNewResident, toggleFamilyFacade, toggleFamilyView, showFamilyAlone,
      revealUnfamilizedMember, removeFamilyInstance,
    });
  }

  window.PortalHatgFamilyBlocks = Object.freeze({
    createFamilyBlocks, dependencies: DEPENDENCIES, familyRoles, edgesThroughFoldedFamilies,
    familyTone,
  });
})();
