'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../../../management_portal/static');
const context={window:{PortalCompatibility:{identifier:String},PortalI18n:{t:String},PortalUIPrimitives:{escapeHtml:String}}};
for(const file of ['lineage_template_graph.js','lineage_template_workshop_draft.js','lineage_template_workshop_chat.js'])vm.runInNewContext(fs.readFileSync(path.join(root,file),'utf8'),context);
const data=context.window.LineageTemplateWorkshopDraft;
test('blank drafts and local history coalesce text, branch redo and rebase saves',()=>{
  assert.equal(data.empty().roles.length,0);
  const history=data.history();history.reset({title:'original'});
  history.record({title:'a'},'title');history.record({title:'ab'},'title');
  assert.equal(history.undo().title,'original');assert.equal(history.redo().title,'ab');
  history.saved({title:'ab'});assert.equal(history.undo().title,'original');
  history.record({title:'branch'});assert.equal(history.state().redo,false);
  history.reset({title:'other resource'});assert.equal(history.state().undo,false);
});
const plain=value=>JSON.parse(JSON.stringify(value));
test('application target is inferred from slots before matching, and preserves module targets',()=>{
  assert.deepEqual(plain(data.targets({kind:'structure',body:{roles:[{from:'target'}],match:[{}]}})),['resident']);
  assert.deepEqual(plain(data.targets({kind:'structure',body:{roles:[{from:'lineage_leader'}],match:[{}]}})),['selection']);
  assert.deepEqual(plain(data.targets({kind:'structure',body:{roles:[{from:'new'}]}})),['canvas']);
  assert.deepEqual(plain(data.targets({kind:'prompt',body:{},targets:['resident','selection']})),['resident','selection']);
});
test('copying structures renames tokens, forks, config references, wires and layouts without mutating recipes',()=>{
  const body={roles:[{key:'target',from:'target'}],components:[{key:'event',kind:'event_source'}],wires:[],match:[]};
  const source={roles:[{key:'target',from:'target'},{key:'worker',from:'fork',fork_of:'target'}],
    components:[{key:'event',kind:'event_source',title:'{{role.target.title}}'}, {key:'workshop_copy_target',kind:'effect',config_from:'event'}],
    wires:[{key:'route',kind:'route',source:'event',target:'worker'}],match:[],layout:{target:{x:8,y:20},event:{x:40,y:40}}};
  const original=JSON.stringify(source);
  data.append(body,source,{x:50,y:50}); data.append(body,source,{x:70,y:70});
  const keys=['roles','components','wires'].flatMap(list=>body[list].map(row=>row.key));
  assert.equal(keys.length,new Set(keys).size);
  assert.equal(body.roles.find(row=>row.key==='worker_1').fork_of,'target_2');
  assert.equal(body.components.find(row=>row.key==='event_2').title,'{{role.target_2.title}}');
  assert.equal(body.components.find(row=>row.key==='workshop_copy_target_1').config_from,'event_2');
  assert.equal(body.wires.find(row=>row.key==='route_1').source,'event_2');
  assert.equal(JSON.stringify(source),original);
  assert.ok(body.layout.target_2);
});
test('assistant discovery follows its Function Block template identity rather than names',()=>{
  const find=context.window.LineageTemplateWorkshopChat.findAssistant;
  const lineage={function_blocks:[{title:'工坊助手',template_id:'other',elements:[{kind:'resident',id:'wrong'}]},
    {title:'Renamed',template_id:'builtin',elements:[{kind:'component',id:'component'},{kind:'resident',id:'correct'}]}]};
  assert.equal(find(lineage,'builtin'),'correct');assert.equal(find(lineage,'absent'),'');
});
test('copied recipes keep their parameter definitions and remap conflicting names once',()=>{
  const draft={parameters:[{name:'rounds',type:'integer',default:3}],body:{roles:[],components:[],wires:[]}};
  const source={parameters:[{name:'rounds',type:'integer',default:5}],body:{roles:[{key:'target',from:'target'}],components:[{key:'loop',kind:'loop',config:{max_rounds:'{{param.rounds}}'}}],wires:[]}};
  data.appendTemplate(draft,source,{x:50,y:50});
  assert.equal(draft.parameters[1].name,'rounds_1');
  assert.equal(draft.body.components[0].config.max_rounds,'{{param.rounds_1}}');
  assert.equal(source.parameters[0].name,'rounds');
});
