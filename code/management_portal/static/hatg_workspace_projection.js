(function () {
  "use strict";

  function workspaces(binding) {
    const roots = (binding?.roots || []).filter(root => (
      root.root_kind === "workspace"
      && String(root.workspace_id || "")
      && /^(?:\/|[a-z]:[\\/]|\\\\[^\\]+\\[^\\]+)/i.test(String(root.path || ""))
    ));
    return [...new Map(roots.map(root => [root.workspace_id, root])).values()];
  }

  function project(residents = [], relations = [], bindings = new Map()) {
    const nodes = new Map();
    const edges = [];
    const rootsByResident = new Map();
    for (const resident of residents) {
      const roots = workspaces(bindings.get(resident.id));
      const byId = new Map(), byPath = new Map();
      rootsByResident.set(resident.id, { byId, byPath });
      const seen = new Set();
      nodes.set(resident.id, {
        id: resident.id, resident_id: resident.id,
        kind: "workspace_resident", title: resident.title || resident.id,
      });
      for (const root of roots) {
        const id = `workspace::${root.workspace_id}`;
        byId.set(root.workspace_id, id);
        byPath.set(root.path, byPath.has(root.path) ? "" : id);
        if (seen.has(id)) continue;
        seen.add(id);
        if (!nodes.has(id)) nodes.set(id, {
          id, kind: "workspace", workspace_id: root.workspace_id,
          workspace_path: root.path,
          title: root.title || root.label || root.path.split("/").filter(Boolean).at(-1) || root.workspace_id,
          member_ids: [],
        });
        nodes.get(id).member_ids.push(resident.id);
        edges.push({
          id: `binding::${root.workspace_id}::${resident.id}`,
          type: "workspace_binding", source: id, target: resident.id,
          visual_only: true,
        });
      }
    }
    const endpoint = (residentId, workspaceId, path) => {
      const roots = rootsByResident.get(residentId);
      // A stale explicit ID must not resolve to a different Workspace at the same path.
      return (workspaceId ? roots?.byId.get(workspaceId) : path && roots?.byPath.get(path)) || "";
    };
    for (const relation of relations) {
      if (relation.type !== "handoff" || relation.draft_preview) continue;
      (relation.items || []).forEach((item, index) => {
        const source = endpoint(relation.source, item.source?.workspace_id, item.source?.path);
        const targetId = item.result?.target_workspace_id || item.target?.workspace_id;
        const targetPath = item.result?.target_path || item.target?.path;
        const target = endpoint(relation.target, targetId, targetPath)
          || (!targetId && !targetPath && item.transfer?.mode === "direct"
            ? endpoint(relation.target, item.source?.workspace_id, item.source?.path) : "");
        if (!source || !target) return;
        edges.push({
          ...relation,
          id: `${relation.id}::${item.item_id || index}`,
          original_edge_id: relation.id, interaction_edge_id: relation.id,
          source_resident_id: relation.source, target_resident_id: relation.target,
          source, target, items: [item],
        });
      });
    }
    return { nodes: [...nodes.values()], edges };
  }

  window.PortalHatgWorkspaceProjection = Object.freeze({ project, workspaces });
})();
