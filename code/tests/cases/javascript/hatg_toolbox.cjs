const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../..');
const source=name=>fs.readFileSync(path.join(root,'management_portal/static',name+'.js'),'utf8');
const tick=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
const plain=value=>JSON.parse(JSON.stringify(value));
const recipe=(id,kind,builtin=false)=>({template_id:id,kind,title:id,description:'说明只在悬停提示中',builtin_key:builtin?'builtin_'+id:null,
 targets:['resident','canvas'],category:'team',body:{roles:[],components:[],wires:[]},behavior:{persistent:kind==='structure'}});

function fixture(){
 const dom=new JSDOM('<div id="shelf"></div><div id="canvas"><div class="hatgNode" data-node-id="resident_a"></div></div>',{runScripts:'outside-only'}),w=dom.window;
 const shelf=w.document.getElementById('shelf'),canvas=w.document.getElementById('canvas');
 const calls=[],applied=[],editors=[],favorites=[],errors=[];
 let ids=new Set(['template_saved']), failFavorites=false, failWrite=false;
 const templates=[recipe('template_duty','structure',true),recipe('template_org','familize',true),recipe('template_prompt','prompt',true),recipe('template_saved','structure'),recipe('template_other','script')];
 templates[0].body={roles:[{key:'target',from:'target'},{key:'worker',from:'new'}],components:[{key:'effect',kind:'effect'}],
  match:[{key:'existing',kind:'event_source',title:'<img src=x>'}],wires:[{key:'a',source:'existing',target:'effect'},{key:'b',source:'effect',target:'target'}]};
 const tag=(parts,...values)=>parts.reduce((text,part,index)=>text+part+(values[index]??''),'');
 w.PortalI18n={t:String,text:tag,message:String};
 w.PortalCompatibility={identifier:name=>name};
 w.PortalUIPrimitives={escapeHtml:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')};
 w.PortalAPI={get:async()=>{if(failFavorites)throw Error('favorite read failed');return {items:[...ids].map(address=>({address,status:'available'}))};}};
 w.PortalResourceFavorites={set:async(address,favorite)=>{favorites.push([address,favorite]);if(failWrite)throw Error('favorite write failed');
  if(favorite)ids.add(address);else ids.delete(address);
  w.dispatchEvent(new w.CustomEvent('portal:resource-favorite-changed',{detail:{address,favorite}}));}};
 w.eval(source('lineage_template_graph'));w.eval(source('lineage_templates_shelf'));
 const part=w.LineageTemplatesShelf.createShelf({MIME:'template_transfer',apply:async(...args)=>applied.push(args),canvas,config:{},
  esc:w.PortalUIPrimitives.escapeHtml,getContext:()=>({lineage_id:'lineage_a'}),openEditor:async id=>editors.push(id),
  request:async(name,args)=>{calls.push([name,args]);return {templates:plain(templates),kinds:[]};},
  resolveTarget:()=>({kind:'resident',resident_ids:['resident_a']}),shelf,t:value=>w.PortalI18n.t(value),targetLabels:()=>({resident:'Resident',canvas:'画布'}),
  templatesState:{templates:[],kinds:[],disposed:false},toast:message=>errors.push(message),promptText:item=>item.body.message||'prompt draft'});
 const click=async selector=>{shelf.querySelector(selector).click();await tick();};
 const visible=()=>[...shelf.querySelectorAll('[data-template-id]')].map(node=>node.dataset.templateId);
 const transfer={values:{},types:['template_transfer'],setData(type,value){this.values[type]=value;},getData(type){return this.values[type]||'';}};
 const drag=(target,type)=>{const event=new w.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(event,'dataTransfer',{value:transfer});target.dispatchEvent(event);return event;};
 return {w,dom,shelf,canvas,part,calls,applied,editors,favorites,errors,templates,click,visible,drag,transfer,
  failRead:()=>{failFavorites=true;},failWrite:()=>{failWrite=true;},close:()=>{part.abort.abort();part.dropHint.remove();w.close();}};
}

test('toolbox groups are based on module kind and include builtins plus personal favorites only',async()=>{
 const f=fixture();try{await f.part.load();
  assert.deepEqual(['structure','familize','prompt','explore','script'].map(f.w.LineageTemplatesShelf.groupFor),['duties','organizations','errands','errands','errands']);
  assert.deepEqual([...f.shelf.querySelectorAll('[data-toolbox-group]')].map(node=>node.dataset.toolboxGroup),['duties','roles','organizations','errands']);
  assert.equal(f.shelf.querySelector('[data-toolbox-group="errands"]').tagName,'SECTION','Errands is expanded');
  assert.match(f.shelf.querySelector('[data-toolbox-group="roles"]').textContent,/还没有角色模板/);
  assert.deepEqual(f.visible(),['template_duty','template_saved','template_org','template_prompt']);
  const tile=f.shelf.querySelector('[data-template-id="template_duty"]');
  assert.equal(tile.children.length,2);assert.equal(tile.firstElementChild.tagName,'svg');
  assert.equal(tile.lastElementChild.textContent,'template_duty');
  assert.match(tile.title,/说明只在悬停提示中/);assert.doesNotMatch(tile.textContent,/说明只在/);
  assert.equal(tile.querySelectorAll('[data-template-slot="true"]').length,2);
  assert.equal(tile.querySelector('img'),null);
  assert.equal(tile.querySelectorAll('.templateThumbnailWire').length,2);
  await f.click('[data-template-edit="template_duty"]');assert.deepEqual(f.editors,['template_duty']);
 }finally{f.close();}
});

test('More templates adds and removes toolbox membership using resource favorites and keeps builtins',async()=>{
 const f=fixture();try{await f.part.load();f.shelf.querySelector('.templateToolboxMore').open=true;
  await f.click('[data-template-favorite="template_other"]');
  assert(f.visible().includes('template_other'));assert.equal(f.shelf.querySelector('.templateToolboxMore').open,true);
  await f.click('[data-template-favorite="template_other"]');assert(!f.visible().includes('template_other'));
  assert.deepEqual(f.favorites,[['template_other',true],['template_other',false]]);
  assert.equal(f.shelf.querySelector('[data-template-favorite="template_duty"]'),null);
  f.failWrite();await f.click('[data-template-favorite="template_saved"]');
  assert(f.visible().includes('template_saved'),'failed removal keeps committed membership');assert.match(f.errors.at(-1),/favorite write failed/);
 }finally{f.close();}
});

test('generated translations label the expanded Errands group and toolbox actions in English',async()=>{
 const f=fixture();try{
  f.w.eval(source('portal_translations'));
  const catalog=f.w.__PORTAL_TRANSLATIONS__;
  f.w.PortalI18n.t=value=>catalog[value]?.en||value;
  await f.part.load();
  assert.equal(f.shelf.querySelector('[data-toolbox-group="errands"] h4').textContent,'Errands');
  assert.equal(f.shelf.querySelector('[data-toolbox-group="organizations"] h4').textContent,'Organizations');
  assert.equal(f.shelf.querySelector('[data-template-edit]').textContent,'Open in workshop');
  assert.match(f.shelf.querySelector('.templateToolboxMore').textContent,/More templates/);
 }finally{f.close();}
});

test('favorite read failure retains builtins, gives a retry, and account changes clear previous favorites',async()=>{
 const f=fixture();try{await f.part.load();f.failRead();
  f.w.dispatchEvent(new f.w.Event('portal:identity-changed'));await tick();
  assert.deepEqual(f.visible(),['template_duty','template_org','template_prompt']);
  assert(f.shelf.querySelector('[data-toolbox-retry]'));
 }finally{f.close();}
});

test('drag uses the existing transfer, target hint and application callback, including prompt drafts',async()=>{
 const f=fixture();try{await f.part.load();
  const node=f.canvas.firstElementChild;
  f.drag(f.shelf.querySelector('[data-template-id="template_duty"]'),'dragstart');
  assert.equal(f.transfer.values.template_transfer,'template_duty');
  f.drag(node,'dragover');assert.equal(f.part.dropHint.hidden,false);assert(f.canvas.classList.contains('templateDropAllowed'));
  f.drag(node,'drop');await tick();
  assert.deepEqual(plain(f.applied),[['template_duty',{kind:'resident',resident_ids:['resident_a']}]]);
  assert.equal(f.part.dropHint.hidden,true);
  f.drag(f.shelf.querySelector('[data-template-id="template_prompt"]'),'dragstart');
  assert.equal(f.transfer.values['text/plain'],'prompt draft');
 }finally{f.close();}
});

test('all successful template creation paths auto-favorite, and dry-run does not',async()=>{
 const f=fixture();try{
  let captured;
  f.w.LineageTemplatesDialogs={createDialogs:()=>({dialog(){},dialogs:new Set()})};
  f.w.LineageTemplatesResidentSettings={createResidentSettings:()=>({})};
  f.w.LineageTemplatesApplication={createApplication:()=>({})};
  f.w.LineageTemplatesEditing={createEditing:deps=>{captured=deps.request;return {};}};
  f.w.eval(source('lineage_templates'));
  const controller=f.w.LineageTemplates.mount({shelf:f.shelf,canvas:f.canvas,getContext:()=>({}),toast(){},
   request:async()=>({template:{template_id:'template_created'}})});
  await captured('CreateTemplate',{template:{kind:'prompt'}});
  await captured('CreateTemplate',{from_selection:{element_ids:['a']}});
  await captured('CreateTemplate',{template:{kind:'structure'},dry_run:true});
  await captured('UpdateTemplate',{template_id:'template_created'});
  assert.deepEqual(f.favorites,[['template_created',true],['template_created',true]]);
  controller.destroy();
 }finally{f.close();}
});

test('drawer toggles, closes with Escape, and retains the actual primitive drag sources',()=>{
 const dom=new JSDOM(fs.readFileSync(path.join(root,'management_portal/hatg-panel.html'),'utf8'),{runScripts:'outside-only'}),w=dom.window;
 try{
  const drawer=w.document.getElementById('hatgToolbox'),toggle=w.document.getElementById('hatgToolboxToggle');
  assert.equal(drawer.hidden,true);assert(w.document.getElementById('hatgViewport').contains(drawer));
  assert.equal(w.document.querySelector('.functionShelf,.componentLibrarySplitter'),null);
  w.PortalI18n={t:String};w.eval(source('hatg_panel_bindings'));
  const none=()=>{};
  const deps=Object.fromEntries(w.PortalHatgPanelBindings.dependencies.map(key=>[key,none]));
  Object.assign(deps,{$id:id=>w.document.getElementById(id),$queryAll:selector=>[...w.document.querySelectorAll(selector)],$eventRoot:()=>w.document,
   state:{},panelHost:{pageStateController:{listen:(_scope,node,event,handler)=>node.addEventListener(event,handler)}},
   bindPrimitiveLibrary:none,bindGraphNodeSearch:none,hatgModule:()=>({observeNavigationHeight:()=>none})});
  w.ResizeObserver=class{observe(){} disconnect(){}};
  const bindings=w.PortalHatgPanelBindings.createBindings(deps);bindings.bindActions();
  toggle.click();assert.equal(drawer.hidden,false);assert.equal(toggle.getAttribute('aria-expanded'),'true');
  drawer.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(drawer.hidden,true);
  const primitives=drawer.querySelector('#hatgPrimitiveLibrary');assert.equal(primitives.open,false);
  assert.deepEqual([...primitives.querySelectorAll('[data-hatg-library-kind]')].map(node=>node.dataset.hatgLibraryKind),['event_source','filter','selector','reducer','effect','seed']);
  w.PortalCompatibility={identifier:name=>name};
  w.PortalLineageProgram={componentKinds:['event_source','filter','selector','reducer','effect','seed']};
  w.eval(source('hatg_program_edits'));
  const edits=Object.fromEntries(w.PortalHatgProgramEdits.dependencies.map(key=>[key,none]));
  Object.assign(edits,{$id:deps.$id,$queryAll:deps.$queryAll,$eventRoot:deps.$eventRoot,state:{lineage:{lineage_id:'lineage_a'}}});
  w.PortalHatgProgramEdits.createProgramEdits(edits).bindPrimitiveLibrary();
  const values={};
  const event=new w.Event('dragstart',{bubbles:true,cancelable:true});
  Object.defineProperty(event,'dataTransfer',{value:{setData:(type,value)=>{values[type]=value;}}});
  primitives.querySelector('[data-hatg-library-kind="effect"]').dispatchEvent(event);
  assert.equal(values.primitive_transfer,'effect','the retained primitive uses its production drag binding');
 }finally{w.close();}
});
