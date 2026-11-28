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
      for (let index = 0; index < queue.length; index += 1) {
        const key = queue[index];
        next.get(key).forEach(target => {
          if (depth.has(target)) return;
          depth.set(target, depth.get(key) + 1);
          queue.push(target);
        });
      }
    }
    const columns = new Map();
    for (const key of keys) {
      const level = depth.get(key);
      if (!columns.has(level)) columns.set(level, []);
      columns.get(level).push(key);
    }
    const positions = {};
    columns.forEach((column, level) => column.forEach((key, row) => {
      positions[key] = { x: 18 + level * 20, y: 50 + (row - (column.length - 1) / 2) * 16 };
    }));
    return positions;
  }

  // Toolbox/store previews use the editor's collections and topology layout,
  // without creating an interactive graph controller for every small card.
  function thumbnail(source) {
    const body = collections(JSON.parse(JSON.stringify(source)));
    const slots = [...body.roles.filter(item => !['new', 'fork'].includes(item.from)), ...foundNodes(body)];
    const slotKeys = new Set(slots.map(item => item.key));
    const nodes = [...body.roles, ...body.components, ...foundNodes(body)];
    const layout = {...autoLayout(body), ...(body.layout || {})};
    const owned = nodes.filter(item => !slotKeys.has(item.key));
    const xs = owned.map(item => layout[item.key]?.x || 50);
    const left = Math.min(...xs, 30) - 22, right = Math.max(...xs, 70) + 22;
    const positions = new Map(nodes.map(item => [item.key, layout[item.key] || {x: 50, y: 50}]));
    const roleSlots = new Set(body.roles);
    const sides = [slots.filter(item => roleSlots.has(item)), slots.filter(item => !roleSlots.has(item))];
    sides.forEach((items, side) => items.forEach((item, index) => positions.set(item.key,
      {x: side ? right : left, y: 50 + (index - (items.length - 1) / 2) * 18})));
    const points = [...positions.values()];
    const minX = Math.min(...points.map(point => point.x), 30) - 9;
    const minY = Math.min(...points.map(point => point.y), 30) - 9;
    const width = Math.max(...points.map(point => point.x), 70) - minX + 9;
    const height = Math.max(...points.map(point => point.y), 70) - minY + 9;
    const wires = [...body.wires, ...foundWires(body)];
    // Watch wires can originate from another wire: use that wire's midpoint.
    for (const wire of wires) {
      const start = positions.get(wire.source), end = positions.get(wire.target);
      if (start && end) positions.set(wire.key, {x: (start.x + end.x) / 2, y: (start.y + end.y) / 2});
    }
    return `<svg class="templateThumbnail" viewBox="${minX} ${minY} ${width} ${height}" aria-hidden="true">`
      + wires.map(wire => {
        const start = positions.get(wire.source), end = positions.get(wire.target);
        return start && end ? `<path class="templateThumbnailWire" d="M${start.x} ${start.y}L${end.x} ${end.y}"/>` : '';
      }).join('')
      + nodes.map(item => {
        const {x, y} = positions.get(item.key);
        const slot = slotKeys.has(item.key);
        const role = body.roles.includes(item) || item.kind === 'resident';
        const shape = role ? `<circle cx="${x}" cy="${y}" r="5"/>`
          : `<rect x="${x - 6}" y="${y - 4}" width="12" height="8" rx="${item.kind === 'event_source' ? 4 : 1}"/>`;
        return `<g class="templateThumbnailNode${slot ? ' is-slot' : ''}" data-template-slot="${slot}"><title>${esc(item.title || item.key)}</title>${shape}</g>`;
      }).join('') + '</svg>';
  }

  function componentDetail(component) {
    const config = component.config || {};
    const program = window.PortalLineageProgram;
    if (component.kind === 'event_source') return program.manifest.event_types?.[config.event_type]?.title || config.event_type || t('选择系统事件');
    if (component.kind === 'effect') {
      if (config.script_draft) return t(config.script_draft.script ? '待确认候选脚本' : '待编写') + (config.script ? ` · ${t('原脚本继续运行')}` : '');
      return (config.endpoints || []).length ? window.PortalI18n.text`端点：${config.endpoints.join(', ')}` : t('Effect 脚本');
    }
    if (component.kind === 'loop') return window.PortalI18n.text`最多 ${config.max_rounds ?? 5} 轮`;
    return program.manifest.component_kinds[component.kind]?.title || component.kind;
  }

  function nodeContent(node) {
    const item = node._template;
    if (node._template_binding) {
      const binding = node._template_binding;
      return `<span class="templateBinding" data-node-drag-handle data-binding-key="${esc(binding.key)}"><strong>${esc(item.title || binding.key)}</strong><small>${esc(binding.type)} · ${esc(t(binding.cardinality === 'many' ? '集合' : '单个'))}</small><small>${esc(t(binding.required ? '必需' : '可选'))} · ${esc(binding.direction)}</small>${node._template_found ? `<small class="templateStatePill" data-template-state="${esc(item.op || 'keep')}">${esc(stateLabels()[item.op || 'keep'])}</small>` : ''}</span>`;
    }
    const state = node._template_found ? `<span class="templateStatePill" data-template-state="${esc(item.op || 'keep')}">${esc(stateLabels()[item.op || 'keep'])}</span>` : '';
    if (node._template_role) {
      const source = node._template_found ? 'Resident' : roleLabels()[item.from] || item.from;
      return `<span class="nodeTopline" data-node-drag-handle><span class="nodeIdentity">${esc(item.key)}</span>${state}</span>
        <strong title="${esc(item.title || source)}">${esc(item.title || source)}</strong>
        <span class="nodeFooter"><span class="nodeTypePill" data-role-source="${esc(item.from || 'found')}">${esc(node._template_menu ? t('模板 Resident') : source)}${item.from === 'fork' ? ` · ${esc(item.fork_of || '')}` : ''}</span></span>`;
    }
    const definition = window.PortalLineageProgram.manifest.component_kinds[item.kind] || {};
    const kind = item.kind === 'event_source' ? 'EVENT' : String(item.kind || '').toUpperCase();
    const shown = node._template_found ? { ...item, config: effectiveConfig(item) } : item;
    return `<span class="programNodeTopline" data-node-drag-handle><span class="programNodeGlyph" aria-hidden="true" data-portal-icon="${esc(definition.icon || item.kind)}"></span><span class="programNodeKind">${esc(kind)}</span>${state}</span>
      <strong>${esc(node.title || item.title || item.key)}</strong>
      <span class="programNodeDetail">${esc(readableText(componentDetail(shown)))}</span>`;
  }
  function readableText(value) {
    const labels={target:'目标',role:'角色',max_rounds:'最多轮数',calendar_time:'时间',reminder:'提醒内容',interval_seconds:'间隔秒数'};
    return String(value || '').replace(/\{\{\s*([^{}]+)\s*\}\}/g,(_,token)=>{const parts=token.split('.');const name=parts[0]==='role'?parts[1]:parts.at(-1);return `〈${t(labels[name] || name)}〉`;});
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

  // In the workshop, the slots bound at application (target and Leader roles,
  // found elements) are dimmed. The graph engine owns node markup, so the
  // rules address nodes by ID, like the found-wire rules above.
  function slotRules(body) {
    const slots = [
      ...(body.roles || []).filter(role => role.from === 'target' || role.from === 'lineage_leader'),
      ...foundNodes(body),
    ];
    return slots.filter(item=>!body.presentation?.residents?.[item.key]).map(item => `.templateGraph .hatgNode[data-node-id="${CSS.escape(item.key)}"]{background:transparent;border:0;box-shadow:none}`).join('\n');
  }

  function bindings(body) {
    const result = {...body.presentation?.bindings};
    const wires = [...(body.wires || []), ...foundWires(body)];
    const inputs = new Set(wires.map(wire => wire.target));
    const outputs = new Set(wires.map(wire => wire.source));
    for (const item of [...(body.roles || []).filter(item=>['target','lineage_leader'].includes(item.from)),...foundNodes(body)]) {
      if (body.presentation?.residents?.[item.key] || result[item.key]) continue;
      const input = inputs.has(item.key), output = outputs.has(item.key);
      result[item.key]={key:item.key,type:item.from ? 'resident' : item.kind,direction:input&&output?'both':input?'in':output?'out':'both',
        cardinality:item.count==='all'?'many':'one',required:true,source:item.from || 'match'};
    }
    return result;
  }

  function residentContexts(body) {
    if(body.presentation)return body.presentation.residents || {};
    return Object.fromEntries((body.roles || []).filter(role=>['new','fork'].includes(role.from)).map(role=>[role.key,
      {key:role.key,title:role.title || role.key,structure:true,duties:[],memories:[],settings:role.settings || {}}]));
  }

  function project(body, {workshop = false, groups = [], expanded = [], scope = null} = {}) {
    const program = window.PortalLineageProgram;
    const source = collections(JSON.parse(JSON.stringify(body)));
    const saved = source.layout || {};
    const automatic = autoLayout(source);
    const place = key => (saved[key] ? { ...saved[key] } : { ...automatic[key] });
    const found = new Set(source.match.map(item => item.key));
    const roles = [...source.roles, ...foundNodes(source).filter(item => item.kind === 'resident')]
      .map(role => ({ id: role.key, kind: 'agent', title: role.title || role.key, ...place(role.key), _template: role, _template_role: true }));
    roles.forEach(node=>{node.title=readableText(node.title);node._template.title=readableText(node._template.title);});
    const components = [...source.components, ...foundNodes(source).filter(item => item.kind !== 'resident')]
      .map(component => ({ ...component, ...(found.has(component.key) ? { config: effectiveConfig(component) } : {}), id: component.key, ...place(component.key) }));
    const labels = stateLabels();
    const wires = [...source.wires, ...foundWires(source)].map(wire => ({ ...wire, id: wire.key,
      ...(found.has(wire.key) ? { config: effectiveConfig(wire), label: `${labels[wire.op || 'keep']} · ${wire.kind}${wire.count === 'all' ? ` · ${t('全部')}` : ''}` } : {}) }));
    const authored = { components, wires };
    const graph = program.projectGraph(authored, roles);
    const nodesById = new Map(graph.nodes.map(node => [node.id, node]));
    const templates = new Map([...source.roles, ...source.components, ...source.match].map(item => [item.key, item]));
    const bindingRows=bindings(source), residents=residentContexts(source);
    graph.nodes.forEach(node => { node._template = templates.get(node.id); node._template_found = found.has(node.id);
      node._template_binding=bindingRows[node.id]; node._template_menu=residents[node.id];
      node.title=readableText(node.title);
      if(node.title==='Family 触发失败')node.title=({router_failed:'Router 触发失败',relay_failed:'转交动作失败',route_failed:'选人任务投递失败',delivery_failed:'成员投递失败'})[node.id] || node.title;
    });
    // A wire without ports is drawn from the first ones that fit, as applying resolves it.
    graph.edges.forEach(edge => {
      const sourceNode = nodesById.get(edge.source);
      const targetNode = nodesById.get(edge.target);
      if (!edge.source_port && sourceNode) edge.source_port = program.portsForNode(sourceNode).find(port => port.direction === 'out' && port.type === edge.type)?.port_id || '';
      if (!edge.target_port && targetNode && ['route'].includes(edge.type)) {
        const ports = program.portsForNode(targetNode).filter(port => port.direction === 'in');
        const wanted = ['invoke', edge.type];
        edge.target_port = wanted.map(type => ports.find(port => port.type === type)).find(Boolean)?.port_id || '';
      }
    });
    const expandedIds = new Set(expanded);
    graph.edges.push(...(source.presentation?.interactions || []).filter(row=>!row.expanded_only||expandedIds.has(row.owner)||scope?.includes(row.owner)).map(row=>({
      id:row.key,source:row.source,target:row.target,type:row.kind,label:row.label,_template_interaction:true,_template_collapsed_label:row.collapsed_label,_template_owner:row.owner,
    })));
    const representative = new Map(), grouped = [];
    if (source.presentation && !scope) for (const [key,owner] of Object.entries(source.presentation.owners)) {
      const expansionOwner=source.presentation.expansion_owners?.[key];
      if (expansionOwner && expandedIds.has(expansionOwner)) continue;
      if (!expandedIds.has(owner)) representative.set(key,owner);
    }
    for (const group of (source.presentation ? [] : groups).filter(group => !group.expanded)) {
      const resident = nodesById.get(group.resident);
      if (!resident) continue;
      for (const key of group.members) representative.set(key, group.key);
      grouped.push({...resident, id:group.key, kind:'function_block', title:group.title,
        _template_role:false,_template:{key:group.key,title:group.title}});
    }
    let edges = graph.edges.filter(edge=>!(representative.has(edge.source) && representative.get(edge.source)===(representative.get(edge.target)||edge.target)))
      .map(edge=>({...edge, source:representative.get(edge.source)||edge.source,
        ...(representative.has(edge.source)&&edge._template_collapsed_label?{label:edge._template_collapsed_label}:{}),
        target:representative.get(edge.watch_subject_wire_id)||representative.get(edge.target)||edge.target,
        ...(representative.size && edge.watch_subject_wire_id ? {_template_watch_subject:edge.watch_subject_wire_id,canonical_target:representative.get(edge.watch_subject_wire_id)||representative.get(edge.target)||edge.target,watch_subject_wire_id:''} : {}),
        ...(representative.has(edge.source)?{source_port:''}:{}),...(representative.has(edge.target)?{target_port:''}:{})})).filter(edge=>edge.source!==edge.target);
    if(representative.size){
      const summary=new Map();
      for(const edge of edges){const key=JSON.stringify([edge.type,edge.source,edge.target]);const previous=summary.get(key);
        if(previous)previous._template_edges.push(edge.id);else summary.set(key,{...edge,_template_edges:[edge.id]});}
      edges=[...summary.values()];
    }
    let nodes = [...graph.nodes.filter(node=>!representative.has(node.id)),...grouped];
    let visibleEdges=edges;
    if (scope) {
      const selected=new Set(scope), wanted=new Set(scope);
      for(const [key,binding] of Object.entries(bindingRows)){
        if(binding.uses?.some(use=>wanted.has(use.component)))selected.add(key);
      }
      visibleEdges=edges.filter(edge=>wanted.has(edge.id) || wanted.has(edge.source)&&wanted.has(edge.target) || edge._template_interaction&&(wanted.has(edge.source)||wanted.has(edge._template_owner)));
      for (const edge of visibleEdges) {selected.add(edge.source);selected.add(edge.target);}
      nodes=nodes.filter(node=>selected.has(node.id));
      // A local duty may observe a shared wire outside its ownership. Expose a
      // typed reference, rather than inventing that wire or monitoring its
      // target Resident instead. The full graph retains the actual wire ID.
      const references=new Map();
      const visibleWireIds = new Set(visibleEdges.map(edge => edge.id));
      visibleEdges=visibleEdges.map(edge=>{
        const subject=edge.watch_subject_wire_id;
        if(!subject || visibleWireIds.has(subject))return edge;
        const key=`binding:${subject}`,target=nodesById.get(edge.target);
        if(!references.has(key))references.set(key,{id:key,kind:'event_source',x:(target?.x || 50)+18,y:target?.y || 50,
          _template:{key,title:subject,kind:'wire'},_template_binding:{key:subject,type:'wire',direction:'in',cardinality:'one',required:true}});
        return {...edge,target:key,canonical_target:key,watch_subject_wire_id:'',_template_watch_subject:subject};
      });
      nodes.push(...references.values());
    }
    if (grouped.length) {
      const compact = autoLayout({roles:nodes.map(node=>({key:node.id})),components:[],match:[],
        wires:edges.map(edge=>({...edge,key:edge.id}))});
      nodes = nodes.map(node=>({...node,...compact[node.id]}));
    }
    const visibleIds=new Set(nodes.map(node=>node.id));
    visibleEdges=visibleEdges.filter(edge=>visibleIds.has(edge.source) && visibleIds.has(edge.target));
    if(workshop)visibleEdges.forEach(edge=>{edge._template_inner_route=true;});
    return { nodes, edges:visibleEdges, wires: program.normalizeProgram(authored).wires };
  }

  function placement(body) {
    const source=collections(JSON.parse(JSON.stringify(body)));
    const layout={...autoLayout(source),...source.layout};
    const slot=source.roles.find(row=>row.from==='target');
    const points=Object.values(layout);
    const anchor=slot ? layout[slot.key] : {x:points.length ? points.reduce((n,p)=>n+p.x,0)/points.length : 50,
      y:points.length ? points.reduce((n,p)=>n+p.y,0)/points.length : 50};
    return {layout,anchor,slot:slot?.key || ''};
  }

  // A temporary graph uses the same node/port/edge projection and camera as
  // the destination. Its anchor, rather than the browser drag image, is hit-tested.
  function placementPreview(view, body, onViewportChange) {
    const geometry=placement(body), graph=project(body);
    const bindingIds = new Set(graph.nodes.filter(node => node._template_binding).map(node => node.id));
    const root=document.createElement('div');root.className='hatgCanvas templatePlacementPreview';
    root.setAttribute('aria-hidden','true');
    root.innerHTML='<div class="hatgWorld"><svg class="hatgEdges" viewBox="0 0 100 100" preserveAspectRatio="none"><g data-layer="edges"></g><g data-layer="effects"></g></svg><div class="hatgEdgeLabels"></div><div class="hatgNodes"></div></div>';
    view.root.append(root);
    const controller=window.PortalHatgGraph.createGraph({root,world:root.firstElementChild,
      edgeLayer:root.querySelector('[data-layer="edges"]'),effectLayer:root.querySelector('[data-layer="effects"]'),
      labelLayer:root.querySelector('.hatgEdgeLabels'),nodeLayer:root.querySelector('.hatgNodes'),
      worldWidth:1280,worldHeight:900,scaleExtent:[.01,10],nodeContent,
      portsForNode:node=>window.PortalLineageProgram.portsForNode(node)});
    controller.render({nodes:graph.nodes,edges:graph.edges,layoutInteractive:false,selectionEnabled:false});
    root.dataset.anchor=geometry.slot;
    root.querySelectorAll('[data-node-id]').forEach(element=>{
      element.classList.add('templatePlacementNode');
      element.classList.toggle('is-template-binding',bindingIds.has(element.dataset.nodeId));
    });
    const anchorNode = geometry.slot && root.querySelector(`[data-node-id="${CSS.escape(geometry.slot)}"]`);
    const worldWidth = Number(root.dataset.worldWidth), worldHeight = Number(root.dataset.worldHeight);
    let lastEvent=null,destroyed=false;
    const observer=new MutationObserver(()=>{if(lastEvent&&!destroyed)onViewportChange(lastEvent);});
    observer.observe(view.root.querySelector('.hatgWorld'),{attributes:true,attributeFilter:['style']});
    function move(event) {
      lastEvent=event;
      // The anchor is held a little ahead of the pointer so even a pointer
      // outside a card can bind via the visible target slot.
      const anchor=view.controller.screenToPosition(event.clientX+28,event.clientY+28);
      let hit=document.elementFromPoint(event.clientX+28,event.clientY+28)?.closest('[data-node-id]');
      if(!view.root.contains(hit)||!geometry.slot)hit=null;
      const candidate=hit?.dataset.nodeId;
      const resident=candidate && view.residents?.().find(row=>row.id===candidate && row.accessible!==false);
      const bounds=resident ? hit.getBoundingClientRect() : null;
      const target=resident ? view.controller.screenToPosition(bounds.left+bounds.width/2,bounds.top+bounds.height/2) : anchor;
      // Translation preserves the shape of every route. Keep the rendered
      // graph and move its world with the pointer at the host camera's scale.
      const camera = view.controller.getTransform();
      controller.setTransform({
        x: camera.x + (target.x - geometry.anchor.x) * worldWidth * camera.k / 100,
        y: camera.y + (target.y - geometry.anchor.y) * worldHeight * camera.k / 100,
        k: camera.k,
      }, {duration:0});
      root.dataset.boundResident=resident?.id || '';
      if (anchorNode) anchorNode.classList.toggle('templateDropTarget', !!resident);
      return {x:target.x,y:target.y,hit:resident ? hit : null,
        event:{clientX:event.clientX+28,clientY:event.clientY+28,target:resident ? hit : view.root}};
    }
    return {move,destroy(){destroyed=true;observer.disconnect();controller.destroy();root.remove();}};
  }

  function mount({ root, body, workshop = false, readOnly = false, viewState = {}, groups = () => [], expanded = () => [], scope = () => null, residentInfo = () => null, onOpenResident = () => {}, onToggleRole = () => {}, isReadonly = () => false, onBeforeChange = () => {}, onChange = () => {}, onSelect = () => {} }) {
    const program = window.PortalLineageProgram;
    const factory = window.PortalHatgGraph?.createGraph;
    if (!program || !factory) throw new Error(t('HATG 图形引擎未加载'));
    let selected = '';
    let wireDraft = null;
    let current = { nodes: [], edges: [], wires: [] };
    let validateWireTarget;
    let currentNodesById = new Map();
    let hintTimer = null;
    const labels = roleLabels();
    root.classList.add('portalHatgOverlayComponent', 'templateGraph');
    root.classList.toggle('is-workshop',workshop);
    root.innerHTML = `<div class="templateGraphPalette" ${workshop ? 'hidden' : ''} role="toolbar" aria-label="${esc(t('添加到结构'))}">
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
        <div class="templateGraphNavigation" role="toolbar" aria-label="${esc(t('画布视图'))}">
          <button type="button" class="portalButton is-icon" data-graph-zoom-out aria-label="${esc(t('缩小'))}" data-portal-icon="minus"></button>
          <output data-graph-zoom aria-label="${esc(t('当前比例'))}"></output>
          <button type="button" class="portalButton is-icon" data-graph-zoom-in aria-label="${esc(t('放大'))}" data-portal-icon="plus"></button>
          <button type="button" class="portalButton is-compact" data-graph-reset aria-label="${esc(t('回到100%'))}" title="${esc(t('回到100%'))}">1:1</button>
          <button type="button" class="portalButton is-icon" data-graph-fit aria-label="${esc(t('适合窗口'))}" title="${esc(t('适合窗口'))}" data-portal-icon="fit-view"></button>
          <button type="button" class="portalButton is-compact" data-graph-arrange>${esc(t('整理'))}</button>
        </div>
        <p class="templateGraphEmpty">${esc(t('从上方添加角色或组件，开始搭建结构。'))}</p>
        <p class="templateGraphHint" data-graph-hint role="status" hidden></p>
      </div>`;
    const canvas = root.querySelector('.hatgCanvas');
    const roleControls=document.createElement('div');roleControls.className='templateRoleViewControls';root.append(roleControls);
    function positionRoleControls(){
      const bounds=root.getBoundingClientRect();
      roleControls.querySelectorAll('[data-collapse-role]').forEach(button=>{
        const node=canvas.querySelector(`[data-node-id="${CSS.escape(button.dataset.collapseRole)}"]`);
        button.hidden=!node;if(!node)return;
        const card=node.getBoundingClientRect();
        button.style.left=`${Math.max(8,Math.min(bounds.width-button.offsetWidth-8,card.left-bounds.left))}px`;
        button.style.top=`${Math.max(52,Math.min(bounds.height-button.offsetHeight-8,card.top-bounds.top-button.offsetHeight-10))}px`;
      });
    }
    let summaryBindings=[],summaryResume='';
    function bindDutySummary(element,node) {
      const dutySummary=document.createElement('aside');
      dutySummary.className='templateDutySummary';dutySummary.hidden=true;dutySummary.tabIndex=0;
      dutySummary.dataset.residentSummary=node.id;
      dutySummary.setAttribute('role','region');dutySummary.setAttribute('aria-label',t('模板职责清单'));root.append(dutySummary);
      const hide=()=>{dutySummary.hidden=true;};
      const show=()=>{
        if(canvas.matches(':active'))return;
        const meta=residentInfo(node.id) || residentContexts(body())[node.id] || node._template_menu;
        dutySummary.innerHTML=`<strong>${esc(meta.title)}</strong><ol>${meta.duties.map(duty=>`<li>${esc(duty.title)}</li>`).join('')}</ol>${meta.duties.length?'':`<p>${esc(t('暂无职责'))}</p>`}<button type="button" class="portalButton is-compact" data-summary-menu>${esc(t('打开模板 Resident 菜单'))}</button>`;
        dutySummary.querySelector('button').onclick=()=>{hide();onOpenResident(node.id);};
        dutySummary.hidden=false;
        const host=root.getBoundingClientRect(),anchor=element.getBoundingClientRect();
        dutySummary.style.width=`${Math.max(0,Math.min(360,host.width-16))}px`;
        dutySummary.style.maxHeight=`${Math.max(0,host.height-16)}px`;
        const width=dutySummary.offsetWidth;
        let x=anchor.right-host.left+8,y=Math.max(8,Math.min(anchor.top-host.top,host.height-dutySummary.offsetHeight-8));
        if(x+width>host.width-8){
          if(anchor.left-host.left-width-8>=8)x=anchor.left-host.left-width-8;
          else {
            x=Math.max(8,Math.min(anchor.left-host.left,host.width-width-8));
            const below=host.bottom-anchor.bottom-16,above=anchor.top-host.top-16;
            dutySummary.style.maxHeight=`${Math.max(0,Math.max(below,above))}px`;
            y=below>=above?anchor.bottom-host.top+8:anchor.top-host.top-dutySummary.offsetHeight-8;
          }
        }
        dutySummary.style.left=`${x}px`;dutySummary.style.top=`${y}px`;
      };
      const binding=window.PortalHatgPanelBindings.bindHoverPanel({trigger:element,panel:dutySummary,open:show,close:hide,isOpen:()=>!dutySummary.hidden});
      summaryBindings.push({destroy(){binding.destroy();dutySummary.remove();}});
    }
    const states = root.appendChild(document.createElement('style'));
    const hintNode = root.querySelector('[data-graph-hint]');
    const hint = message => {
      clearTimeout(hintTimer);
      hintNode.textContent = message || '';
      hintNode.hidden = !message;
      if (message) hintTimer = setTimeout(() => { hintNode.hidden = true; }, 3600);
    };


    function render() {
      summaryResume='';
      summaryBindings.forEach(binding=>binding.destroy());summaryBindings=[];
      current = project(body(), {workshop,groups:groups(),expanded:expanded(),scope:scope()});
      if (readOnly) {
        const positions=viewState.positions ||= {};
        const automatic=arrangedPositions();
        current.nodes.forEach(node=>Object.assign(node,positions[node.id] || automatic.get(node.id)));
      }
      currentNodesById = new Map(current.nodes.map(node => [node.id, node]));
      const selectedEdge = current.edges.some(edge => edge.id === selected);
      validateWireTarget = program.createConnectionValidator({ existingWires: current.wires, nodes: current.nodes });
      states.textContent = edgeStateRules(body()) + (workshop ? slotRules(body()) : '');
      if (selected && !currentNodesById.has(selected) && !selectedEdge) selected = '';
      controller.render({
        nodes: current.nodes, edges: current.edges, layoutInteractive: true, selectionEnabled: false,
        selectedNodeId: currentNodesById.has(selected) ? selected : '',
        selectedEdgeId: selectedEdge ? selected : '',
        wireDraft,
      });
      canvas.querySelectorAll('[data-node-id]').forEach(element=>{
        const node=currentNodesById.get(element.dataset.nodeId);
        element.classList.toggle('is-template-binding',!!node?._template_binding);
        if(node?._template_menu){element.tabIndex=0;element.setAttribute('aria-label',`${node.title} · ${t('模板 Resident 菜单')}`);element.removeAttribute('title');element.querySelectorAll('[title]').forEach(n=>n.removeAttribute('title'));bindDutySummary(element,node);}
      });
      canvas.querySelectorAll('[data-node-open-menu]').forEach(button=>{button.dataset.templateResidentMenu=button.dataset.nodeOpenMenu;});
      roleControls.innerHTML=expanded().filter(key=>body().presentation?.residents[key]).map(key=>`<button type="button" class="portalButton is-compact" data-collapse-role="${esc(key)}" aria-label="${esc(t('收起内部结构'))} · ${esc(body().presentation.residents[key].title)}">${esc(t('收起内部结构'))}</button>`).join('');
      positionRoleControls();
      summaryResume='';
      root.dataset.graphEmpty = String(!current.nodes.length);
    }

    function select(key) {
      selected = key || '';
      render();
      onSelect(selected,currentNodesById.get(selected));
    }

    // `work` edits the body and returns the key to select, or nothing to keep the selection.
    function change(work) {
      if (readOnly) return;
      try { onBeforeChange(); } catch (error) { hint(String(error.message || error)); return; }
      const key = work(collections(body()));
      if (key !== undefined) selected = key;
      render();
      onChange(selected);
      onSelect(selected);
    }

    const point = position => ({ x: Math.round(position.x * 100) / 100, y: Math.round(position.y * 100) / 100 });
    function arrangedPositions(){
      const result=window.PortalHatgGraph.computeBranchAwareLayout(current.nodes,current.edges,{horizontalGap:24,verticalGap:22,nodeWidth:17,nodeHeight:14,feedbackEdgeTypes:['watch','target'],componentGap:24});
      const chain=body().presentation?.main_chain?.filter(key=>current.nodes.some(node=>node.id===key));
      if(chain?.length){chain.forEach((key,index)=>result.set(key,{x:12+index*24,y:50}));
        const others=current.nodes.filter(node=>!chain.includes(node.id));others.forEach((node,index)=>result.set(node.id,{x:36+index*25,y:82}));}
      return result;
    }
    function moveEnd(moved){
      if(readOnly){const nodes=Array.isArray(moved)?moved:[moved];viewState.positions={...viewState.positions,...Object.fromEntries(nodes.filter(Boolean).map(node=>[node.id,point(node)]))};render();}
      else change(keepPlaces);
    }
    function arrange(){
      const positions=Object.fromEntries(arrangedPositions());
      if(readOnly){viewState.positions={};render();}
      else change(source=>{source.layout={...source.layout,...positions};});
      fit();
    }
    const fitOptions={duration:0,maxScale:workshop?1:1.1,...(workshop?{minScale:.6,padding:40}:{})};
    function fit(){controller.fit(fitOptions);}

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
      if (isReadonly(key)) { hint(t('外部对象只读，请编辑引用它的连线')); return; }
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
      if (readOnly) return;
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
      zoomValue: root.querySelector('[data-graph-zoom]'),
      zoomInButton: null,
      zoomOutButton: null,
      resetButton: root.querySelector('[data-graph-reset]'),
      fitButton: root.querySelector('[data-graph-fit]'),
      fitOptions,
      nodeContent,
      nodeOpenMenu:node=>node._template_menu ? {label:t('打开模板 Resident 菜单')} : null,
      onNodeOpenMenu:node=>onOpenResident(node.id),
      portsForNode: node => program.portsForNode(node),
      connectionTargets: (source, item) => program.connectionTargets(source, item),
      isPortCompatible: (source, target) => validateWireTarget(source, target).ok,
      onPortClick: port => connect(port),
      onWireDraftCancel: () => { wireDraft = null; hint(''); render(); },
      onPrimitiveDrop: (kind, position) => addComponent(kind, position),
      onNodeClick: node => select(node.id),
      onNodeDoubleClick: node => {select(node.id);if(node._template_menu)onOpenResident(node.id);},
      onEdgeClick: edge => select(edge.id),
      onBackgroundClick: () => { if (selected) select(''); },
      onNodeMoveEnd: moveEnd,
      onNodesMoveEnd: moveEnd,
    });

    const abort = new AbortController();
    roleControls.addEventListener('click',event=>{const key=event.target.closest('[data-collapse-role]')?.dataset.collapseRole;if(key){event.stopPropagation();onToggleRole(key);canvas.querySelector(`[data-node-id="${CSS.escape(key)}"]`)?.focus({preventScroll:true});}},{signal:abort.signal});
    const roleCamera=new MutationObserver(()=>{positionRoleControls();if(readOnly)viewState.camera=controller.getTransform();});roleCamera.observe(canvas.querySelector('.hatgWorld'),{attributes:true,attributeFilter:['style']});
    const summaryResize=new ResizeObserver(()=>{positionRoleControls();root.querySelectorAll('.templateDutySummary').forEach(panel=>{panel.hidden=true;});});summaryResize.observe(root);
    root.addEventListener('click', event => {
      if(event.target.closest('[data-graph-arrange]')){arrange();return;}
      const zoom=event.target.closest('[data-graph-zoom-in],[data-graph-zoom-out]');
      if(zoom){const old=controller.getTransform(),k=Math.max(.2,Math.min(2,old.k*(zoom.hasAttribute('data-graph-zoom-in')?1.25:.8))),ratio=k/old.k;
        controller.setTransform({x:canvas.clientWidth/2-(canvas.clientWidth/2-old.x)*ratio,y:canvas.clientHeight/2-(canvas.clientHeight/2-old.y)*ratio,k},{duration:0});return;}
      const chip = event.target.closest('[data-add-role], [data-add-component], [data-add-found]');
      if (!chip) return;
      if (chip.dataset.addRole) addRole(chip.dataset.addRole);
      else if (chip.dataset.addComponent) addComponent(chip.dataset.addComponent);
      else if (chip.hasAttribute('data-add-found')) addFound();
    }, { signal: abort.signal });
    root.addEventListener('dragstart', event => {
      const chip = event.target.closest('[data-add-component]');
      if (!chip) return;
      event.dataTransfer.setData(PRIMITIVE_MIME, chip.dataset.addComponent);
      event.dataTransfer.effectAllowed = 'copy';
    }, { signal: abort.signal });
    canvas.addEventListener('keydown', event => {
      const node=event.target.closest('[data-node-id]');
      if(node && ['Enter','ContextMenu'].includes(event.key) && current.nodes.find(item=>item.id===node.dataset.nodeId)?._template_menu){event.preventDefault();onOpenResident(node.dataset.nodeId);return;}
      if (!['Delete', 'Backspace'].includes(event.key) || !selected || event.target.closest('input, textarea, select')) return;
      event.preventDefault();
      deleteSelected();
    }, { signal: abort.signal });

    render();
    if(readOnly&&viewState.camera)controller.setTransform(viewState.camera,{duration:0});else fit();
    return Object.freeze({
      render, select, addComponent, insertionPosition:center,
      position: event => controller.screenToPosition(event.clientX, event.clientY),
      placementView: () => ({root:canvas,controller}),
      selected: () => selected,
      fit, arrange,
      remove: key => { if (!isReadonly(key)) change(source => { removeElement(source, key); return ''; }); },
      destroy() { roleCamera.disconnect();summaryResize.disconnect();summaryBindings.forEach(binding=>binding.destroy());clearTimeout(hintTimer); abort.abort(); controller.destroy(); root.replaceChildren(); },
    });
  }

  function authoringItems() {
    const items = [], body = (list,row) => ({roles:[],components:[],wires:[],match:[],[list]:[row]});
    const add = (key,title,group,content,icon) => items.push({template_id:`workshop_${key}`,kind:'structure',title,targets:['canvas'],toolbox_group:group,body:content,icon});
    for (const kind of ['event_source','effect','loop','filter','selector','reducer','seed','blackboard']) {
      const definition = window.PortalLineageProgram.manifest.component_kinds[kind];
      add(kind,definition.title,'primitives',body('components',{key:kind,kind,title:definition.default_title || definition.title,config:JSON.parse(JSON.stringify(definition.default_config || {}))}),definition.icon || kind);
    }
    for (const [from,title,icon] of [['target','目标 Resident','pin'],['lineage_leader','Lineage Leader','user'],['new','新建 Resident','plus'],['fork','Fork','fork']]) {
      add(from,t(title),'slots',body('roles',{key:from === 'lineage_leader' ? 'leader' : from,from,...(from === 'new' ? {settings:{}} : {})}),icon);
    }
    add('existing',t('已有元素'),'slots',body('match',{key:'existing',kind:'event_source',config:{},where:{},op:'keep'}),'search');
    return items;
  }
  function mountTools({root,items = authoringItems(),onInsert,primitiveDrag = false}) {
    root.innerHTML = ['primitives','slots'].filter(group=>items.some(item=>item.toolbox_group===group)).map(group => `<div class="workshopToolGroup" role="group" aria-label="${esc(t(group === 'primitives' ? '原语' : '绑定空位'))}"><small>${esc(t(group === 'primitives' ? '原语' : '空位'))}</small>${items.filter(item=>item.toolbox_group===group).map(item=>`<button type="button" class="portalButton is-icon" data-template-id="${esc(item.template_id)}" draggable="true" aria-label="${esc(t(item.title))}" data-portal-tooltip="${esc(t(item.title))}" title="${esc(t(item.title))}"><span data-portal-icon="${esc(item.icon)}" aria-hidden="true"></span></button>`).join('')}</div>`).join('');
    const abort = new AbortController();
    root.addEventListener('click',event => {
      const button = event.target.closest('[data-template-id]');
      if (button && !button.disabled) onInsert(button.dataset.templateId);
    },{signal:abort.signal});
    if (primitiveDrag) root.addEventListener('dragstart',event => {
      const item = items.find(item=>item.template_id === event.target.closest('[data-template-id]')?.dataset.templateId);
      if (!item?.body.components[0]) return;
      event.dataTransfer.setData(PRIMITIVE_MIME,item.body.components[0].kind);
      event.dataTransfer.effectAllowed = 'copy';
    },{signal:abort.signal});
    return {setEnabled(enabled) {root.querySelectorAll('[data-template-id]').forEach(button=>{button.disabled=!enabled;button.draggable=enabled;});},destroy(){abort.abort();root.replaceChildren();}};
  }
  window.LineageTemplateGraph = Object.freeze({ mount, project, bindings, residentContexts, placement, placementPreview, thumbnail, removeElement, renameKey, effectiveConfig, isWireKind, authoringItems, mountTools });
})();
