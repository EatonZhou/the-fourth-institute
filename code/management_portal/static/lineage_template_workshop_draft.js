(() => {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const lists = ['roles', 'components', 'wires', 'match'];
  const empty = () => ({roles:[],components:[],wires:[],match:[],function_block:true});
  // Local content history; resource versions and server operations stay outside it.
  function history(onChange = () => {}) {
    let entries = [], cursor = -1, group = '', timestamp = 0;
    const state = () => ({undo:cursor > 0,redo:cursor >= 0 && cursor < entries.length - 1});
    function reset(value) {entries = [clone(value)]; cursor = 0; group = ''; onChange(state());}
    function record(value, key = '') {
      if (cursor < 0) return reset(value);
      if (JSON.stringify(entries[cursor]) === JSON.stringify(value)) return;
      const now = Date.now(), merge = key && group === key && now - timestamp < 700 && cursor === entries.length - 1 && cursor > 0;
      entries.splice(cursor + 1);
      if (merge) entries[cursor] = clone(value);
      else {entries.push(clone(value)); cursor++;}
      group = key; timestamp = now; onChange(state());
    }
    function move(step) {
      if (cursor + step < 0 || cursor + step >= entries.length) return null;
      cursor += step; group = ''; onChange(state()); return clone(entries[cursor]);
    }
    return {reset,record,state,undo:() => move(-1),redo:() => move(1),
      rebase(transform) {entries=entries.map(value=>clone(transform(clone(value))));group='';onChange(state());},
      saved(value) {if (cursor < 0) reset(value); else {entries[cursor] = clone(value); group = ''; onChange(state());}}};
  }
  function targets(template) {
    if (template.kind !== 'structure') return template.targets;
    if ((template.body.roles || []).some(row => row.from === 'target')) return ['resident'];
    return (template.body.match || []).length ? ['selection'] : ['canvas'];
  }
  function payload(template) {
    const result = Object.fromEntries(['kind', 'title', 'description', 'parameters', 'body'].map(key => [key, clone(template[key] ?? (key === 'parameters' ? [] : ''))]));
    result.category = template.kind === 'structure' ? 'structure' : template.category || 'apply';
    result.targets = targets(template);
    return result;
  }
  function append(body, incoming, position) {
    const source = clone(incoming);
    const used = new Set(lists.flatMap(list => (body[list] || []).map(row => row.key)));
    const renamed = new Map();
    const sourceKeys = lists.flatMap(list => (source[list] || []).map(row => row.key));
    let prefix = 'workshop_copy_';
    while ([...used, ...sourceKeys].some(key => key.startsWith(prefix))) prefix += '_';
    // Rename through an intermediate key so exchanging keys cannot alter another row's tokens.
    lists.forEach(list => (source[list] || []).forEach(row => {
      const previous = row.key;
      let key = previous, suffix = 1;
      while (used.has(key)) key = `${previous}_${suffix++}`;
      used.add(key); renamed.set(previous, key);
    }));
    for (const [previous] of renamed) {
      const row = lists.flatMap(list => source[list] || []).find(item => item.key === previous);
      row.key = `${prefix}${previous}`;
      window.LineageTemplateGraph.renameKey(source, previous, row.key);
    }
    for (const [previous, key] of renamed) {
      const row = lists.flatMap(list => source[list] || []).find(item => item.key === `${prefix}${previous}`);
      row.key = key;
      window.LineageTemplateGraph.renameKey(source, `${prefix}${previous}`, key);
    }
    const layout = source.layout || {};
    const positions = Object.values(layout);
    const center = {x: positions.length ? positions.reduce((sum, p) => sum + p.x, 0) / positions.length : 50,
      y: positions.length ? positions.reduce((sum, p) => sum + p.y, 0) / positions.length : 50};
    body.layout ||= {};
    let index = 0;
    lists.forEach(list => {
      body[list] ||= [];
      (source[list] || []).forEach(row => {
        body[list].push(row);
        if (list === 'wires' || window.LineageTemplateGraph.isWireKind(row.kind)) return;
        const origin = layout[row.key] || {x: 50 + (index % 3) * 18, y: 50 + Math.floor(index++ / 3) * 15};
        body.layout[row.key] = {x: position.x + origin.x - center.x, y: position.y + origin.y - center.y};
      });
    });
    return renamed.values().next().value;
  }
  function appendTemplate(draft, incoming, position) {
    const source = clone(incoming);
    draft.parameters ||= [];
    const names = new Map();
    for (const parameter of source.parameters || []) {
      const same = draft.parameters.find(row => row.name === parameter.name);
      if (same && JSON.stringify(same) === JSON.stringify(parameter)) continue;
      const previous = parameter.name;
      let suffix = 1;
      while (draft.parameters.some(row => row.name === parameter.name)) parameter.name = `${previous}_${suffix++}`;
      names.set(previous,parameter.name); draft.parameters.push(parameter);
    }
    const renameParameters = value => {
      if (typeof value === 'string') return value.replace(/(\{\{\s*param\.)([A-Za-z_][A-Za-z0-9_]*)(\s*\}\})/g,
        (token,before,name,after) => names.has(name) ? `${before}${names.get(name)}${after}` : token);
      if (Array.isArray(value)) return value.map(renameParameters);
      if (value && typeof value === 'object') Object.keys(value).forEach(key => { value[key] = renameParameters(value[key]); });
      return value;
    };
    return append(draft.body,renameParameters(source.body),position);
  }
  window.LineageTemplateWorkshopDraft = Object.freeze({empty,history,targets, payload, append, appendTemplate});
})();
