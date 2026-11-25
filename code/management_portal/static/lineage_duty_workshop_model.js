(() => {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const canonical = value => JSON.stringify(value, function (_key, item) {
    return item && !Array.isArray(item) && typeof item === 'object'
      ? Object.fromEntries(Object.keys(item).sort().map(key => [key,item[key]])) : item;
  });
  const programItems = lineage => [...(lineage.program?.components || []), ...(lineage.program?.wires || [])];
  const metadata = block => ({title:block.title,description:block.duty.description || '',category:block.duty.category,
    prompt:block.duty.memory?.prompt || '',memory_level:block.duty.memory?.level || 'session'});
  function create(lineage, blockId) {
    const block = lineage.function_blocks.find(item => item.id === blockId);
    if (!block?.duty) throw new Error('这不是职责实例');
    const owned = new Set(block.elements.filter(item => ['component','wire'].includes(item.kind)).map(item => item.id));
    const all = new Map([...programItems(lineage), ...(lineage.nodes || []).map(node => ({...node,kind:'resident'}))].map(item => [item.id,item]));
    const body = {roles:[],components:[],wires:[],match:[],layout:{}};
    const asRow = item => { const row = copy(item); row.key = row.id; delete row.id; return row; };
    function reference(id) {
      if (owned.has(id) || body.match.some(row => row.key === id)) return;
      const item = all.get(id);
      if (!item) return;
      body.match.push({...asRow(item),op:'keep'});
      if (item.source) { reference(item.source); reference(item.target); }
    }
    for (const item of lineage.program?.components || []) if (owned.has(item.id)) body.components.push(asRow(item));
    for (const item of lineage.program?.wires || []) if (owned.has(item.id)) {
      body.wires.push(asRow(item)); reference(item.source); reference(item.target);
    }
    reference(block.duty.holder_id);
    for (const item of [...body.components,...body.match]) if (Number.isFinite(item.x) && Number.isFinite(item.y)) body.layout[item.key] = {x:item.x,y:item.y};
    const draft = {body,meta:metadata(block)};
    const allocated = new Map();
    const idFor = row => {
      if (all.has(row.key) || /^[a-z_]+_[0-9a-f]{12}$/.test(row.key)) return row.key;
      if (!allocated.has(row.key)) allocated.set(row.key,`${row.source ? 'wire' : row.kind}_${crypto.randomUUID().replaceAll('-','').slice(0,12)}`);
      return allocated.get(row.key);
    };
    function request() {
      const ids = new Map([...draft.body.components,...draft.body.wires].map(row => [row.key,idFor(row)]));
      const operations = [], current = new Set(ids.values());
      for (const [list,kind,fields] of [['components','component',['title','config','x','y']],['wires','wire',['label','source','target','source_port','target_port','config']]]) {
        for (const row of draft.body[list]) {
          const id = ids.get(row.key), before = all.get(id);
          const value = {id,kind:row.kind};
          for (const key of fields) {
            let valueAtKey = ['x','y'].includes(key) ? draft.body.layout[row.key]?.[key] ?? row[key] : row[key];
            if (['source','target'].includes(key)) valueAtKey = ids.get(valueAtKey) || valueAtKey;
            if (valueAtKey !== undefined) value[key] = copy(valueAtKey);
          }
          if (!before) operations.push({op:'create',kind,value});
          else {
            const changes = Object.fromEntries(fields.filter(key => canonical(value[key]) !== canonical(before[key])).map(key => [key,value[key] ?? null]));
            if (Object.keys(changes).length) operations.push({op:'update',kind,id,changes});
          }
        }
      }
      // Explicit wire removals precede component removals, including cascades.
      for (const list of ['wires','components']) for (const row of lineage.program?.[list] || []) {
        if (owned.has(row.id) && !current.has(row.id)) operations.push({op:'delete',kind:list === 'wires' ? 'wire' : 'component',id:row.id});
      }
      const original = metadata(block);
      const duty_changes = Object.fromEntries(Object.entries(draft.meta).filter(([key,value]) => canonical(value) !== canonical(original[key])));
      return {lineage_id:lineage.lineage_id,function_block_id:blockId,operations,duty_changes};
    }
    function fromPreview(preview) {
      const next = copy(lineage), edits = new Map(preview.elements.map(item => [item.id,item.after]));
      for (const list of ['components','wires']) {
        next.program[list] = next.program[list].filter(row => !edits.has(row.id));
        for (const item of edits.values()) if (item && Boolean(item.source) === (list === 'wires')) next.program[list].push(item);
      }
      next.function_blocks = next.function_blocks.map(item => item.id === blockId ? preview.duty.after : item);
      return create(next,blockId).draft;
    }
    function restore(value) {
      const next = copy(value);
      Object.keys(body).forEach(key=>delete body[key]); Object.assign(body,next.body);
      draft.body = body; draft.meta = next.meta;
    }
    return {draft,block,revision:lineage.revision,all,owned,reference,request,fromPreview,restore,
      dirty:() => {const value=request();return !!value.operations.length || !!Object.keys(value.duty_changes).length;}};
  }
  window.LineageDutyWorkshopModel = Object.freeze({create});
})();
