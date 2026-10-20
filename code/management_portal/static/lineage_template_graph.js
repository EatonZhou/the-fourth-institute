(() => {
  'use strict';

  // The structure editor draws a template's roles, components and wires with the HATG
  // graph engine. Node and wire IDs are the template's local keys; positions live in
  // body.layout, which only this editor reads.
  const t = value => window.PortalI18n.t(value);
  const esc = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ''));
  const PALETTE = ['event_source', 'effect', 'loop', 'filter', 'selector', 'reducer'];
  const PRIMITIVE_MIME = window.PortalCompatibility.identifier("primitive_transfer");
  const ROLE_PREFIX = { target: 'target', lineage_leader: 'leader', new: 'resident', fork: 'fork' };
  const WIRE_KINDS = ['route', 'target', 'watch'];
  const roleLabels = () => ({ target: t('目标 Resident'), lineage_leader: 'Lineage Leader', new: t('新建 Resident'), fork: 'Fork' });
  const stateLabels = () => ({ keep: t('已有'), update: t('修改'), delete: t('删除') });
  const failures = () => ({
    direction_mismatch: t('请从输出端口连到输入端口'), self_connection: t('不能连到自身'),
    type_mismatch: t('端口类型不匹配'), kind_mismatch: t('这两个节点之间不能用这种连线'),
  });
  const LISTS = ['roles', 'components', 'wires', 'match'];

  function collections(body) {
    LISTS.forEach(key => { if (!Array.isArray(body[key])) body[key] = []; });
    return body;
  }

  // body.match lists elements the Lineage already has; a found node is a Resident
  // or component, a found wire joins the target, the Leader and found nodes.
  const isWireKind = kind => WIRE_KINDS.includes(kind);
  const foundNodes = body => (body.match || []).filter(item => !isWireKind(item.kind));
  const foundWires = body => (body.match || []).filter(item => isWireKind(item.kind));

  // A found element shows the config it would have after applying: the example
  // config captured with it, overlaid with the update (null clears a field).
  function effectiveConfig(item) {
    const config = { ...(item.config || {}) };
    Object.entries(item.op === 'update' ? item.update?.config || {} : {}).forEach(([key, value]) => {
      if (value === null) delete config[key];
      else config[key] = value;
    });
    return config;
  }

  function nextKey(body, prefix) {
    const used = new Set(LISTS.flatMap(list => (body[list] || []).map(item => item.key)));
    let index = 1;
    while (used.has(`${prefix}_${index}`)) index += 1;
    return `${prefix}_${index}`;
  }

  // Removing a node takes its wires along, and a wire takes the Watch wires observing it.
  function removeElement(body, key) {
    collections(body);
    const doomed = new Set([key]);
    let grown = true;
    while (grown) {
      grown = false;
      [...body.wires, ...foundWires(body)].forEach(wire => {
        if (!doomed.has(wire.key) && (doomed.has(wire.source) || doomed.has(wire.target))) { doomed.add(wire.key); grown = true; }
      });
    }
    LISTS.forEach(list => { body[list] = body[list].filter(item => !doomed.has(item.key)); });
    [...body.components, ...body.wires].forEach(item => { if (doomed.has(item.config_from)) delete item.config_from; });
    if (body.layout) doomed.forEach(item => delete body.layout[item]);
  }

  // A renamed key is renamed wherever the body refers to it.
  function renameKey(body, from, to) {
    if (!from || !to || from === to) return;
    collections(body);
    [...body.wires, ...foundWires(body)].forEach(wire => {
      if (wire.source === from) wire.source = to;
      if (wire.target === from) wire.target = to;
    });
    body.roles.forEach(role => { if (role.fork_of === from) role.fork_of = to; });
    [...body.components, ...body.wires].forEach(item => { if (item.config_from === from) item.config_from = to; });
    // Role and found-element tokens can appear in titles, settings, scripts and
    // nested wire config. Preserve whitespace and only replace the complete key.
    const kind = body.roles.some(role => role.key === to) ? 'role' : body.match.some(item => item.key === to) ? 'match' : '';
    const fields = kind === 'role' ? 'id|ids|title' : 'id|title';
    const pattern = new RegExp(`(\\{\\{\\s*${kind}\\.)([A-Za-z_][A-Za-z0-9_]*)(\\.(?:${fields})\\s*\\}\\})`, 'g');
    const renameReferences = value => {
      if (typeof value === 'string') return value.replace(pattern, (token, prefix, key, suffix) => key === from ? `${prefix}${to}${suffix}` : token);
      if (Array.isArray(value)) return value.map(renameReferences);
      if (value && typeof value === 'object') Object.keys(value).forEach(key => { value[key] = renameReferences(value[key]); });
      return value;
    };
    if (kind) renameReferences(body);
    if (body.layout?.[from]) { body.layout[to] = body.layout[from]; delete body.layout[from]; }
  }

  // Without saved places, nodes are laid out in layers from the ones nothing feeds.
  function autoLayout(body) {
    const keys = [...body.roles, ...body.components, ...foundNodes(body)].map(item => item.key);
    const wires = [...body.wires, ...foundWires(body)];
    const wireSources = new Map(wires.map(wire => [wire.key, wire.source]));
    const next = new Map(keys.map(key => [key, []]));
    const fed = new Set();
    wires.forEach(wire => {
      const source = wireSources.get(wire.source) || wire.source;
      if (!next.has(source) || !next.has(wire.target) || source === wire.target) return;
      next.get(source).push(wire.target);
      fed.add(wire.target);
    });
    const depth = new Map();
    for (const root of [...keys.filter(key => !fed.has(key)), ...keys]) {
      if (depth.has(root)) continue;
      depth.set(root, 0);
      const queue = [root];
      while (queue.length) {
        const key = queue.shift();
        next.get(key).forEach(target => {
          if (depth.has(target)) return;
          depth.set(target, depth.get(key) + 1);
          queue.push(target);
        });
      }
    }
    const columns = new Map();
    keys.forEach(key => columns.set(depth.get(key), [...(columns.get(depth.get(key)) || []), key]));
    const positions = {};
    columns.forEach((column, level) => column.forEach((key, row) => {
      positions[key] = { x: 18 + level * 20, y: 50 + (row - (column.length - 1) / 2) * 16 };
    }));
    return positions;
  }

  function componentDetail(component) {
    const config = component.config || {};
    const program = window.PortalLineageProgram;
    if (component.kind === 'event_source') return program.manifest.event_types?.[config.event_type]?.title || config.event_type || t('选择系统事件');
    if (component.kind === 'effect') return (config.endpoints || []).length ? window.PortalI18n.text`端点：${config.endpoints.join(', ')}` : t('Effect 脚本');
    if (component.kind === 'loop') return window.PortalI18n.text`最多 ${config.max_rounds ?? 5} 轮`;
    return program.manifest.component_kinds[component.kind]?.title || component.kind;
  }

  function nodeContent(node) {
    const item = node._template;
    const state = node._template_found ? `<span class="templateStatePill" data-template-state="${esc(item.op || 'keep')}">${esc(stateLabels()[item.op || 'keep'])}</span>` : '';
    if (node._template_role) {
      const source = node._template_found ? 'Resident' : roleLabels()[item.from] || item.from;
      return `<span class="nodeTopline" data-node-drag-handle><span class="nodeIdentity">${esc(item.key)}</span>${state}</span>
        <strong title="${esc(item.title || source)}">${esc(item.title || source)}</strong>
        <span class="nodeFooter"><span class="nodeTypePill" data-role-source="${esc(item.from || 'found')}">${esc(source)}${item.from === 'fork' ? ` · ${esc(item.fork_of || '')}` : ''}</span></span>`;
    }
    const definition = window.PortalLineageProgram.manifest.component_kinds[item.kind] || {};
    const kind = item.kind === 'event_source' ? 'EVENT' : String(item.kind || '').toUpperCase();
    const shown = node._template_found ? { ...item, config: effectiveConfig(item) } : item;
    return `<span class="programNodeTopline" data-node-drag-handle><span class="programNodeGlyph" aria-hidden="true" data-portal-icon="${esc(definition.icon || item.kind)}"></span><span class="programNodeKind">${esc(kind)}</span>${state}</span>
      <strong title="${esc(item.title || item.key)}">${esc(item.title || item.key)}</strong>
      <span class="programNodeDetail">${esc(componentDetail(shown))}</span>`;
  }

  // Found wires are drawn dashed; deleted ones red and updated ones amber. The
  // graph engine owns the edge markup, so the rules address edges by ID.
  const EDGE_STATES = {
    keep: 'stroke-dasharray:5 4',
    update: 'stroke-dasharray:5 4;stroke:#c58a1c',
    delete: 'stroke-dasharray:5 4;stroke:#c2453b;opacity:.6',
  };

  function edgeStateRules(body) {
    return foundWires(body).map(item => {
      const id = CSS.escape(item.key);
      const state = item.op || 'keep';
      return `.templateGraph .hatgEdge[data-edge-id="${id}"]{${EDGE_STATES[state] || EDGE_STATES.keep}}
        .templateGraph .edgeLabelBtn[data-edge-id="${id}"]{border-style:dashed${state === 'delete' ? ';color:#c2453b;text-decoration:line-through' : state === 'update' ? ';color:#c58a1c' : ''}}`;
    }).join('\n');
  }

  function mount({ root, body, onBeforeChange = () => {}, onChange = () => {}, onSelect = () => {} }) {
    const program = window.PortalLineageProgram;
    const factory = window.PortalHatgGraph?.createGraph;
    if (!program || !factory) throw new Error(t('HATG 图形引擎未加载'));
    let selected = '';
    let wireDraft = null;
    let current = { nodes: [], edges: [], wires: [] };
    let hintTimer = null;
    const labels = roleLabels();
    root.classList.add('portalHatgOverlayComponent', 'templateGraph');
    root.innerHTML = `<div class="templateGraphPalette" role="toolbar" aria-label="${esc(t('添加到结构'))}">
        <span class="templateGraphPaletteLabel">${esc(t('角色'))}</span>${Object.keys(ROLE_PREFIX).map(from => `<button type="button" class="templateGraphChip" data-add-role="${from}" title="${esc(t('添加角色'))}：${esc(labels[from])}">+ ${esc(labels[from])}</button>`).join('')}
        <span class="templateGraphPaletteLabel">${esc(t('组件'))}</span>${PALETTE.map(kind => {
          const definition = program.manifest.component_kinds[kind] || {};
          return `<button type="button" class="templateGraphChip" draggable="true" data-add-component="${kind}" title="${esc(t('点击添加，或拖到图中'))}"><span aria-hidden="true" data-portal-icon="${esc(definition.icon || kind)}"></span>${esc(definition.title || kind)}</button>`;
        }).join('')}
        <span class="templateGraphPaletteLabel">${esc(t('已有'))}</span><button type="button" class="templateGraphChip" data-add-found title="${esc(t('施加时在目标附近查找的已有元素'))}">+ ${esc(t('已有元素'))}</button>
      </div>
      <div class="templateGraphStage">
        <div class="hatgCanvas templateGraphCanvas" tabindex="0" aria-label="${esc(t('模板结构图：拖动节点调整位置，先点输出端口再点输入端口连线'))}">
          <div class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><defs>${WIRE_KINDS.map(kind => `<marker id="templateGraph-${kind}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker>`).join('')}</defs><g data-layer="edges"></g><g data-layer="effects"></g></svg>
            <div class="hatgEdgeLabels"></div><div class="hatgNodes"></div></div>
        </div>
        <button type="button" class="templateGraphFit" data-graph-fit aria-label="${esc(t('适合窗口'))}" title="${esc(t('适合窗口'))}" data-portal-icon="fit-view"></button>
        <p class="templateGraphEmpty">${esc(t('从上方添加角色或组件，开始搭建结构。'))}</p>
        <p class="templateGraphHint" data-graph-hint role="status" hidden></p>
      </div>`;
    const canvas = root.querySelector('.hatgCanvas');
    const states = root.appendChild(document.createElement('style'));
    const hintNode = root.querySelector('[data-graph-hint]');
    const hint = message => {
      clearTimeout(hintTimer);
      hintNode.textContent = message || '';
      hintNode.hidden = !message;
      if (message) hintTimer = setTimeout(() => { hintNode.hidden = true; }, 3600);
    };

    function project() {
      const source = collections(body());
      const saved = source.layout || {};
      const automatic = autoLayout(source);
      const place = key => (saved[key] ? { ...saved[key] } : { ...automatic[key] });
      const found = new Set(source.match.map(item => item.key));
      const roles = [...source.roles, ...foundNodes(source).filter(item => item.kind === 'resident')]
        .map(role => ({ id: role.key, kind: 'agent', title: role.title || role.key, ...place(role.key), _template: role, _template_role: true }));
      const components = [...source.components, ...foundNodes(source).filter(item => item.kind !== 'resident')]
        .map(component => ({ ...component, ...(found.has(component.key) ? { config: effectiveConfig(component) } : {}), id: component.key, ...place(component.key) }));
      const labels = stateLabels();
      const wires = [...source.wires, ...foundWires(source)].map(wire => ({ ...wire, id: wire.key,
        ...(found.has(wire.key) ? { config: effectiveConfig(wire), label: `${labels[wire.op || 'keep']} · ${wire.kind}${wire.count === 'all' ? ` · ${t('全部')}` : ''}` } : {}) }));
      const authored = { components, wires };
      const graph = program.projectGraph(authored, roles);
      const templates = new Map([...source.roles, ...source.components, ...source.match].map(item => [item.key, item]));
      graph.nodes.forEach(node => { node._template = templates.get(node.id); node._template_found = found.has(node.id); });
      // A wire without ports is drawn from the first ones that fit, as applying resolves it.
      graph.edges.forEach(edge => {
        const sourceNode = graph.nodes.find(node => node.id === edge.source);
        const targetNode = graph.nodes.find(node => node.id === edge.target);
        if (!edge.source_port && sourceNode) edge.source_port = program.portsForNode(sourceNode).find(port => port.direction === 'out' && port.type === edge.type)?.port_id || '';
        if (!edge.target_port && targetNode && ['route'].includes(edge.type)) {
          const ports = program.portsForNode(targetNode).filter(port => port.direction === 'in');
          const wanted = ['invoke', edge.type];
          edge.target_port = wanted.map(type => ports.find(port => port.type === type)).find(Boolean)?.port_id || '';
        }
      });
      return { nodes: graph.nodes, edges: graph.edges, wires: program.normalizeProgram(authored).wires };
    }

    function render() {
      current = project();
      states.textContent = edgeStateRules(body());
      if (selected && !current.nodes.some(node => node.id === selected) && !current.edges.some(edge => edge.id === selected)) selected = '';
      controller.render({
        nodes: current.nodes, edges: current.edges, layoutInteractive: true, selectionEnabled: false,
        selectedNodeId: current.nodes.some(node => node.id === selected) ? selected : '',
        selectedEdgeId: current.edges.some(edge => edge.id === selected) ? selected : '',
        wireDraft,
      });
      root.dataset.graphEmpty = String(!current.nodes.length);
    }

    function select(key) {
      selected = key || '';
      render();
      onSelect(selected);
    }

    // `work` edits the body and returns the key to select, or nothing to keep the selection.
    function change(work) {
      try { onBeforeChange(); } catch (error) { hint(String(error.message || error)); return; }
      const key = work(collections(body()));
      if (key !== undefined) selected = key;
      render();
      onChange(selected);
      onSelect(selected);
    }

    const point = position => ({ x: Math.round(position.x * 100) / 100, y: Math.round(position.y * 100) / 100 });

    // Once anything is placed by hand, every node keeps the place it is drawn at.
    function keepPlaces(source) {
      source.layout = Object.fromEntries(current.nodes.map(node => [node.id, point(node)]));
    }

    // A clicked palette item lands on the first free place near the middle of the view.
    function center() {
      const bounds = controller.getVisibleWorldBounds();
      const middle = { x: (bounds.minX + bounds.maxX) / 2, y: (bounds.minY + bounds.maxY) / 2 };
      const offsets = [[0, 0], [1, 0], [0, 1], [1, 1], [-1, 0], [0, -1], [-1, 1], [1, -1], [-1, -1], [2, 0], [2, 1], [0, 2], [2, 2]];
      const free = offsets.map(([dx, dy]) => ({ x: middle.x + dx * 18, y: middle.y + dy * 14 }))
        .find(spot => current.nodes.every(node => Math.abs(node.x - spot.x) >= 17 || Math.abs(node.y - spot.y) >= 13));
      return free || middle;
    }

    function addComponent(kind, position = center()) {
      const definition = program.manifest.component_kinds[kind];
      if (!definition) return;
      change(source => {
        const key = nextKey(source, kind === 'event_source' ? 'event' : kind);
        keepPlaces(source);
        source.components.push({ key, kind, title: definition.default_title || definition.title || kind, config: JSON.parse(JSON.stringify(definition.default_config || {})) });
        source.layout[key] = point(position);
        return key;
      });
    }

    function addRole(from) {
      const origin = collections(body()).roles.find(role => role.key === selected) || body().roles[0];
      if (from === 'fork' && !origin) { hint(t('先添加一个角色，Fork 才有来源')); return; }
      const position = center();
      change(source => {
        const taken = ['roles', 'components', 'wires'].some(list => source[list].some(item => item.key === ROLE_PREFIX[from]));
        const key = taken ? nextKey(source, ROLE_PREFIX[from]) : ROLE_PREFIX[from];
        keepPlaces(source);
        source.roles.push({ key, from, title: '', ...(from === 'new' ? { provider: 'codex', settings: {} } : {}), ...(from === 'fork' ? { fork_of: origin.key, fork_mode: 'full', settings: {} } : {}) });
        source.layout[key] = point(position);
        return key;
      });
    }

    // A found element starts as a kept Event; the inspector changes its kind.
    function addFound() {
      const position = center();
      change(source => {
        const key = nextKey(source, 'existing');
        keepPlaces(source);
        source.match.push({ key, kind: 'event_source', title: '', config: {}, where: {}, op: 'keep' });
        source.layout[key] = point(position);
        return key;
      });
    }

    // Delete marks a found element for deletion and a second Delete keeps it again;
    // anything the template creates is removed from the template.
    function deleteSelected() {
      const key = selected;
      const item = collections(body()).match.find(row => row.key === key);
      if (item?.kind === 'resident') { hint(t('已有 Resident 不能删除；可在右侧移出查找')); return; }
      change(source => {
        if (!item) { removeElement(source, key); return ''; }
        if (item.op === 'delete') item.op = 'keep';
        else { item.op = 'delete'; delete item.update; }
        return key;
      });
    }

    function connect(port) {
      const normalized = program.normalizePort(port);
      if (!normalized?.node_id || (!normalized.port_id && normalized.type !== "object")) return;
      if (!wireDraft) {
        if (normalized.direction !== 'out') { hint(t('请先选择一个输出端口开始连线。')); return; }
        wireDraft = normalized;
        hint(t('选择高亮的卡片或入口完成连接；Esc 或点击空白处取消。'));
        render();
        return;
      }
      if (normalized.node_id === wireDraft.node_id && normalized.port_id === wireDraft.port_id && normalized.direction === wireDraft.direction) {
        wireDraft = null; hint(''); render(); return;
      }
      const validation = program.validateConnection(wireDraft, normalized, { existingWires: current.wires, nodes: current.nodes });
      if (!validation?.ok) { hint(failures()[validation?.reason] || t('无法连接这两个端口')); return; }
      wireDraft = null;
      hint('');
      change(source => {
        const key = nextKey(source, 'wire');
        source.wires.push({ key, kind: validation.type, source: validation.source.node_id, source_port: validation.source.port_id,
          target: validation.target.node_id,
          ...(validation.target.port_id ? { target_port: validation.target.port_id } : {}), config: {},
          });
        return key;
      });
    }

    const controller = factory({
      root: canvas,
      world: canvas.querySelector('.hatgWorld'),
      edgeLayer: canvas.querySelector('[data-layer="edges"]'),
      effectLayer: canvas.querySelector('[data-layer="effects"]'),
      labelLayer: canvas.querySelector('.hatgEdgeLabels'),
      nodeLayer: canvas.querySelector('.hatgNodes'),
      worldWidth: 1280,
      worldHeight: 900,
      scaleExtent: [0.2, 2],
      nodeContent,
      portsForNode: node => program.portsForNode(node),
      connectionTargets: (source, item) => program.connectionTargets(source, item),
      isPortCompatible: (source, target) => Boolean(program.validateConnection(source, target, { existingWires: current.wires, nodes: current.nodes })?.ok),
      onPortClick: port => connect(port),
      onWireDraftCancel: () => { wireDraft = null; hint(''); render(); },
      onPrimitiveDrop: (kind, position) => addComponent(kind, position),
      onNodeClick: node => select(node.id),
      onNodeDoubleClick: node => select(node.id),
      onEdgeClick: edge => select(edge.id),
      onBackgroundClick: () => { if (selected) select(''); },
      onNodeMoveEnd: () => { change(keepPlaces); },
      onNodesMoveEnd: () => { change(keepPlaces); },
    });

    const abort = new AbortController();
    root.addEventListener('click', event => {
      const chip = event.target.closest('[data-add-role], [data-add-component], [data-add-found], [data-graph-fit]');
      if (!chip) return;
      if (chip.dataset.addRole) addRole(chip.dataset.addRole);
      else if (chip.dataset.addComponent) addComponent(chip.dataset.addComponent);
      else if (chip.hasAttribute('data-add-found')) addFound();
      else controller.fit({ duration: 0, maxScale: 1.1 });
    }, { signal: abort.signal });
    root.addEventListener('dragstart', event => {
      const chip = event.target.closest('[data-add-component]');
      if (!chip) return;
      event.dataTransfer.setData(PRIMITIVE_MIME, chip.dataset.addComponent);
      event.dataTransfer.effectAllowed = 'copy';
    }, { signal: abort.signal });
    canvas.addEventListener('keydown', event => {
      if (!['Delete', 'Backspace'].includes(event.key) || !selected || event.target.closest('input, textarea, select')) return;
      event.preventDefault();
      deleteSelected();
    }, { signal: abort.signal });

    render();
    controller.fit({ duration: 0, maxScale: 1.1 });
    return Object.freeze({
      render, select,
      selected: () => selected,
      fit: () => controller.fit({ duration: 0, maxScale: 1.1 }),
      remove: key => change(source => { removeElement(source, key); return ''; }),
      destroy() { clearTimeout(hintTimer); abort.abort(); controller.destroy(); root.replaceChildren(); },
    });
  }

  window.LineageTemplateGraph = Object.freeze({ mount, removeElement, renameKey, effectiveConfig, isWireKind });
})();
