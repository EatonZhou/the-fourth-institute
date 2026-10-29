(() => {
  "use strict";

  if (window.PortalHatgGraphElementIndex) return;

  // The rendered elements of each node and edge, indexed after every render so
  // that a drag, a runtime patch or a selection change updates them without
  // querying the layers. Rendering also indexes the edges by id, including the
  // overview edges the minimap draws.

  const DEPENDENCIES = Object.freeze([
    "layers", "minimapLayers", "state",
  ]);

  function createElementIndex(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphElementIndex is missing: ${missing.join(", ")}`);
    const {
      layers, minimapLayers, state,
    } = dependencies;

    let positionElementsByNode = new Map();
    let primaryNodeElementsById = new Map();
    let portElementsByNode = new Map();
    let ownerChromeElementsByNode = new Map();

    let incidentEdgeIdsByNode = new Map();

    let edgePathElementsById = new Map();
    let edgeLabelElementsById = new Map();
    let minimapNodeElementsById = new Map();
    let minimapEdgeElementsById = new Map();

    function appendIndexedElement(index, key, target) {
      const cleanKey = String(key || "");
      if (!cleanKey || !target) return;
      if (!index.has(cleanKey)) index.set(cleanKey, []);
      index.get(cleanKey).push(target);
    }

    function refreshInteractionIndexes(edges) {
      const indexedEdges = [...edges];
      const indexedEdgeIds = new Set(indexedEdges.map(edge => String(edge?.id || "")));
      state.model.overviewEdges.forEach(edge => {
        const edgeId = String(edge?.id || "");
        if (!edgeId || indexedEdgeIds.has(edgeId)) return;
        indexedEdges.push(edge);
        indexedEdgeIds.add(edgeId);
      });
      positionElementsByNode = new Map();
      primaryNodeElementsById = new Map();
      portElementsByNode = new Map();
      ownerChromeElementsByNode = new Map();
      incidentEdgeIdsByNode = new Map();
      state.edgeById = new Map(indexedEdges.map(edge => [String(edge.id || ""), edge]));
      edgePathElementsById = new Map();
      edgeLabelElementsById = new Map();
      minimapNodeElementsById = new Map();
      minimapEdgeElementsById = new Map();

      layers.nodes.selectAll([
        ".hatgNode",
        "button.hatgNodeQuickFork",
        "button.hatgNodeHoverAction",
        "button.hatgNodeOpenMenu",
        "button.hatgNodeControl",
        "div.hatgNodeDisclosureGroup",
      ].join(",")).each(function rememberNodeElement() {
        const nodeId = this.dataset.nodeId
          || this.dataset.sourceNodeId
          || this.dataset.actionNodeId;
        appendIndexedElement(positionElementsByNode, nodeId, this);
        if (this.classList.contains("hatgNode")) {
          primaryNodeElementsById.set(String(nodeId || ""), this);
        } else if (
          this.classList.contains("hatgNodeHoverAction")
          || this.classList.contains("hatgNodeOpenMenu")
          || (
            this.classList.contains("hatgNodeControl")
            && this.classList.contains("is-hover-action")
          )
        ) {
          appendIndexedElement(ownerChromeElementsByNode, nodeId, this);
        }
      });
      layers.nodes.selectAll("button.hatgPort").each(function rememberPort(port) {
        appendIndexedElement(portElementsByNode, port?.node_id, this);
      });
      layers.edges.selectAll("path.hatgEdge").each(function rememberEdgePath(edge) {
        edgePathElementsById.set(String(edge?.id || ""), this);
      });
      layers.labels.selectAll("button.edgeLabelBtn").each(function rememberEdgeLabel(edge) {
        edgeLabelElementsById.set(String(edge?.id || ""), this);
      });
      minimapLayers?.nodes.selectAll(".hatgMinimapNode")
        .each(function rememberMinimapNode(node) {
          minimapNodeElementsById.set(String(node?.id || ""), this);
        });
      minimapLayers?.edges.selectAll("line.hatgMinimapEdge")
        .each(function rememberMinimapEdge(edge) {
          minimapEdgeElementsById.set(String(edge?.id || ""), this);
        });
      indexedEdges.forEach(edge => {
        const edgeId = String(edge?.id || "");
        const observed = edge.type === "watch" ? state.edgeById.get(edge.watch_subject_wire_id || edge.target) : null;
        [edge?.source, edge?.target, observed?.source, observed?.target].forEach(nodeId => {
          const cleanNodeId = String(nodeId || "");
          if (!cleanNodeId || !edgeId) return;
          if (!incidentEdgeIdsByNode.has(cleanNodeId)) {
            incidentEdgeIdsByNode.set(cleanNodeId, new Set());
          }
          incidentEdgeIdsByNode.get(cleanNodeId).add(edgeId);
        });
      });
    }

    const positionedElements = nodeId => positionElementsByNode.get(nodeId) || [];
    const nodeElement = nodeId => primaryNodeElementsById.get(nodeId);
    const hasNodeElement = nodeId => primaryNodeElementsById.has(nodeId);
    const portElements = nodeId => portElementsByNode.get(nodeId) || [];
    const ownerChromeElements = nodeId => ownerChromeElementsByNode.get(nodeId) || [];
    const incidentEdgeIds = nodeId => incidentEdgeIdsByNode.get(nodeId);
    const edgePathElement = edgeId => edgePathElementsById.get(edgeId);
    const edgeLabelElement = edgeId => edgeLabelElementsById.get(edgeId);
    const minimapNodeElement = nodeId => minimapNodeElementsById.get(nodeId);
    const minimapEdgeElement = edgeId => minimapEdgeElementsById.get(edgeId);

    return {
      edgeLabelElement, edgePathElement, hasNodeElement, incidentEdgeIds, minimapEdgeElement, minimapNodeElement,
      nodeElement, ownerChromeElements, portElements, positionedElements, refreshInteractionIndexes,
    };
  }

  window.PortalHatgGraphElementIndex = Object.freeze({ createElementIndex, dependencies: DEPENDENCIES });
})();
