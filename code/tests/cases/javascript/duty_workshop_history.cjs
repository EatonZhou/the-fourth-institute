'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const copy=value=>JSON.parse(JSON.stringify(value));
const context={window:{},crypto:require('node:crypto').webcrypto};
for(const name of ['lineage_template_workshop_draft','lineage_duty_workshop_model'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../../management_portal/static',name+'.js'),'utf8'),context);
test('committed identities rebase every history frame, wire and layout across saves',()=>{
  const holder='resident_0123456789ab',duty='function_block_0123456789ab';
  const lineage={lineage_id:'lineage_0123456789ab',revision:1,nodes:[{id:holder,title:'Holder'}],program:{components:[],wires:[]},
    function_blocks:[{id:duty,title:'Duty',elements:[],duty:{holder_id:holder,category:'custom',description:'',memory:{prompt:'',level:'session'}}}]};
  let model=context.window.LineageDutyWorkshopModel.create(copy(lineage),duty);
  const history=context.window.LineageTemplateWorkshopDraft.history(),snapshot=()=>({content:copy(model.draft),selected:'loop'});
  history.reset(snapshot());
  model.draft.body.components.push({key:'loop',kind:'loop',title:'Original',config:{max_rounds:3}});
  model.draft.body.wires.push({key:'wire',kind:'route',source:'loop',source_port:'end',target:holder,config:{}});
  model.draft.body.layout.loop={x:40,y:45};history.record(snapshot());
  model.draft.body.components[0].title='Edited';model.draft.body.components[0].config.max_rounds=4;history.record(snapshot());
  const operations=copy(model.request().operations),component=operations.find(row=>row.kind==='component').value,wire=operations.find(row=>row.kind==='wire').value;
  assert.equal(wire.source,component.id);
  history.rebase(value=>({...value,content:model.committedDraft(value.content),selected:model.committedKey(value.selected)}));
  const saved=copy(lineage);saved.revision=2;saved.program.components=[component];saved.program.wires=[wire];saved.function_blocks[0].elements=[{id:component.id,kind:'component'},{id:wire.id,kind:'wire'}];
  model=context.window.LineageDutyWorkshopModel.create(saved,duty);history.saved({content:copy(model.draft),selected:component.id});
  let previous=history.undo();assert.equal(previous.selected,component.id);assert.ok(previous.content.body.layout[component.id]);assert.equal(previous.content.body.wires[0].source,component.id);
  model.restore(previous.content);let edits=copy(model.request().operations);
  assert.deepEqual(edits.map(row=>[row.op,row.id]),[['update',component.id]]);assert.equal(edits[0].changes.config.max_rounds,3);
  model.restore(history.redo().content);assert.equal(model.dirty(),false);
  history.undo();model.restore(history.undo().content);edits=copy(model.request().operations);
  assert.deepEqual(edits.map(row=>[row.op,row.id]),[['delete',wire.id],['delete',component.id]]);
  assert.ok(model.draft.body.match.some(row=>row.key===holder));
  model.restore(history.redo().content);assert.equal(model.draft.body.wires[0].key,wire.id);
  // A later save allocates a different new element without reassigning old IDs.
  model.draft.body.components.push({key:'second',kind:'loop',title:'Second',config:{max_rounds:2}});history.record(snapshot());
  const second=copy(model.request().operations).find(row=>row.op==='create').value;
  history.rebase(value=>({...value,content:model.committedDraft(value.content),selected:model.committedKey(value.selected)}));
  const again=copy(saved);again.revision=3;again.program.components=[{...component,title:'Original',config:{max_rounds:3}},second];again.function_blocks[0].elements.push({id:second.id,kind:'component'});
  model=context.window.LineageDutyWorkshopModel.create(again,duty);history.saved({content:copy(model.draft)});
  model.restore(history.undo().content);edits=copy(model.request().operations);
  assert.deepEqual(edits.map(row=>[row.op,row.id]),[['delete',second.id]]);assert.equal(model.draft.body.wires[0].source,component.id);
});

test('duty workshop previews through the read instruction and only applies on confirmation', async () => {
  const {JSDOM} = require('jsdom');
  const dom = new JSDOM('<main><form id="workshopEditor"></form><p id="workshopError"></p><aside id="workshopTools"></aside></main>', {
    runScripts: 'outside-only',
  });
  const w = dom.window;
  try {
    const graph = {destroy() {}};
    let confirmed = false;
    Object.assign(w, {
      PortalI18n: {t: value => value, message: value => value},
      PortalUIPrimitives: {escapeHtml: value => value},
      PortalUI: {confirm: async () => confirmed},
      TemplateWorkshopSurface: {mount: () => ({destroy() {}})},
      LineageTemplates: {mount: () => ({load: async () => {}, destroy() {}})},
      LineageTemplatesEditorElements: {createEditorElements: () => ({})},
      LineageTemplateGraph: {mount: () => graph, mountTools: () => graph, authoringItems: () => []},
    });
    for (const name of ['lineage_template_workshop_draft', 'lineage_duty_workshop_model', 'lineage_duty_workshop']) {
      w.eval(fs.readFileSync(path.join(__dirname, '../../../management_portal/static', name + '.js'), 'utf8'));
    }
    const lineageId = 'lineage_0123456789ab', functionBlockId = 'function_block_0123456789ab';
    const block = {id: functionBlockId, title: 'Duty', elements: [], duty: {category: 'custom', memory: {level: 'session'}}};
    const lineage = {lineage_id: lineageId, revision: 7, nodes: [], program: {components: [], wires: []}, function_blocks: [block]};
    const draft = {lineage_id: lineageId, function_block_id: functionBlockId, revision: 7, operations: [], duty_changes: {title: 'New duty'}};
    const {revision, ...args} = draft;
    const edited = {...block, title: 'New duty'};
    const calls = [];
    const execute = async (name, parameters, options) => {
      if (name === 'GetLineage') return {lineage: copy(lineage)};
      calls.push(copy({name, parameters, options}));
      if (name === 'ApplyLineageChanges' && confirmed) {
        lineage.function_blocks[0] = edited;
        lineage.revision++;
      }
      return {preview: {elements: [], duty: {after: edited}}};
    };
    const workshop = w.LineageDutyWorkshop.mount({root: w.document.querySelector('main'), lineageId, functionBlockId, execute});
    await workshop.load();
    await workshop.acceptDraft(draft);
    const read = {name: 'GetLineageChangesPreview', parameters: args, options: {expectedVersion: revision}};
    assert.deepEqual(calls, [read]);
    assert.match(workshop.assistantContext(), /GetLineageChangesPreview.*expected_version:7/);
    assert.doesNotMatch(workshop.assistantContext(), /dry_run/);
    await workshop.save();
    assert.deepEqual(calls, [read, read], 'declining confirmation must keep the graph unchanged');
    confirmed = true;
    await workshop.save();
    assert.deepEqual(calls, [read, read, read, {...read, name: 'ApplyLineageChanges'}]);
    assert.equal(lineage.revision, 8);
    workshop.destroy();
  } finally {
    w.close();
  }
});
