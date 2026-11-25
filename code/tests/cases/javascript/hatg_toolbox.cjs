(function toolboxTests() {
  const {test}=require('node:test');
  const assert=require('node:assert/strict');
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{JSDOM}=require('jsdom');
  const parser=require('@babel/parser'),traverse=require('@babel/traverse').default;
  const root=path.resolve(__dirname,'../../..');
  const source=name=>fs.readFileSync(path.join(root,'management_portal/static',name+'.js'),'utf8');
  const tick=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
  const pause=()=>new Promise(resolve=>setTimeout(resolve,280));
  const plain=value=>JSON.parse(JSON.stringify(value));
  const recipe=(id,kind,builtin=false,created_by='alice')=>({template_id:id,kind,title:id,created_by,description:'说明只在悬停提示中',
    builtin_key:builtin?'builtin_'+id:null,targets:['resident','canvas'],category:'team',body:{roles:[],components:[],wires:[]},behavior:{persistent:kind==='structure'}});
  function fixture() {
    const dom=new JSDOM('<div id="shelf"></div><div id="canvas"><div class="hatgNode" data-node-id="resident_a"></div></div>',{runScripts:'outside-only'}),w=dom.window;
    const shelf=w.document.getElementById('shelf'),canvas=w.document.getElementById('canvas');
    const calls=[],applied=[],editors=[],errors=[],forbidden=[],pickups=[];
    let user='alice',lineageId='lineage_a',targetKind='resident';
    let templates=[recipe('template_duty','structure',true),recipe('template_org','familize',true),recipe('template_prompt','prompt',true),
      recipe('template_saved','structure'),recipe('template_other','script',false,'bob')];
    templates[0].body={roles:[{key:'target',from:'target'},{key:'worker',from:'new'}],components:[{key:'effect',kind:'effect'}],
      match:[{key:'existing',kind:'event_source',title:'<img src=x>'}],wires:[{key:'a',source:'existing',target:'effect'},{key:'b',source:'effect',target:'target'}]};
    const tag=(parts,...values)=>parts.reduce((text,part,index)=>text+part+(values[index]??''),'');
    w.PortalI18n={t:String,text:tag,message:String};w.PortalCompatibility={identifier:name=>name};
    w.PortalIdentity={username:()=>user};
    w.PortalUIPrimitives={escapeHtml:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')};
    const disallow=path=>{forbidden.push(path);throw Error('toolbox must not access resource favorites: '+path);};
    w.PortalAPI={get:disallow,post:disallow};w.PortalResourceFavorites={set:disallow};
    w.eval(source('lineage_template_graph'));w.eval(source('lineage_templates_shelf'));
    shelf.addEventListener('portal:template-picked',()=>pickups.push(true));
    const part=w.LineageTemplatesShelf.createShelf({MIME:'template_transfer',apply:async(...args)=>applied.push(args),canvas,config:{},
      esc:w.PortalUIPrimitives.escapeHtml,getContext:()=>({lineage_id:lineageId}),openEditor:async id=>editors.push(id),
      request:async(name,args)=>{calls.push([name,args]);return {templates:plain(templates),kinds:[]};},
      resolveTarget:()=>({kind:targetKind,resident_ids:['resident_a']}),shelf,t:value=>w.PortalI18n.t(value),
      targetLabels:()=>({resident:'Resident',canvas:'画布'}),templatesState:{templates:[],kinds:[],disposed:false},
      toast:message=>errors.push(message),promptText:item=>item.body.message||'prompt draft'});
    const tile=id=>shelf.querySelector(`[data-template-id="${id}"]`);
    const click=async id=>{tile(id).dispatchEvent(new w.MouseEvent('click',{bubbles:true,detail:1}));await pause();};
    const key=(id,key,shiftKey=false)=>tile(id).dispatchEvent(new w.KeyboardEvent('keydown',{key,shiftKey,bubbles:true,cancelable:true}));
    const visible=()=>[...shelf.querySelectorAll('[data-template-id]')].map(node=>node.dataset.templateId);
    const transfer={values:{},types:['template_transfer'],setData(type,value){this.values[type]=value;},getData(type){return this.values[type]||'';}};
    const drag=(target,type)=>{const event=new w.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(event,'dataTransfer',{value:transfer});target.dispatchEvent(event);return event;};
    return {w,dom,shelf,canvas,part,calls,applied,editors,errors,forbidden,pickups,tile,click,key,visible,drag,transfer,
      templates,replaceTemplates:value=>{templates=value;},target:kind=>{targetKind=kind;},
      switchLineage:id=>{lineageId=id;part.render();},
      switchUser:async name=>{user=name;w.dispatchEvent(new w.Event('portal:identity-changed'));await tick();},
      close:()=>{part.abort.abort();part.dropHint.remove();w.close();}};
  }
  test('toolbox uses kind groups and shows builtins plus only the signed-in user creations',async()=>{
    const f=fixture();try{await f.part.load();
      assert.deepEqual(['structure','familize','prompt','explore','script'].map(f.w.LineageTemplatesShelf.groupFor),['duties','organizations','errands','errands','errands']);
      assert.deepEqual([...f.shelf.querySelectorAll('[data-toolbox-group]')].map(node=>node.dataset.toolboxGroup),['duties','roles','organizations','errands']);
      assert.equal(f.shelf.querySelector('[data-toolbox-group="errands"]').tagName,'SECTION');
      assert.deepEqual(f.visible(),['template_duty','template_saved','template_org','template_prompt']);
      const tile=f.tile('template_duty');
      assert.equal(tile.children.length,2);assert.equal(tile.firstElementChild.tagName,'svg');
      assert.equal(tile.lastElementChild.textContent,'template_duty');
      assert.match(tile.title,/说明只在悬停提示中/);assert.doesNotMatch(tile.textContent,/说明只在/);
      assert.equal(tile.querySelectorAll('[data-template-slot="true"]').length,2);assert.equal(tile.querySelector('img'),null);
      assert.equal(f.shelf.querySelector('[data-template-favorite], .templateToolboxMore, .templateEdit'),null);
      assert.deepEqual(f.forbidden,[]);
      await f.switchUser('bob');
      assert.deepEqual(f.visible(),['template_duty','template_org','template_prompt','template_other']);
      await f.switchUser('');
      assert.deepEqual(f.visible(),['template_duty','template_org','template_prompt']);
    }finally{f.close();}
  });
  test('single click picks up, follows the pointer, and applies exactly once without graph selection',async()=>{
    const f=fixture();try{await f.part.load();await f.click('template_duty');
      const ghost=f.w.document.querySelector('.templateCarryPreview'),node=f.canvas.firstElementChild;
      assert.equal(ghost.hidden,false);assert.equal(f.pickups.length,1);
      node.dispatchEvent(new f.w.MouseEvent('mousemove',{bubbles:true,clientX:100,clientY:80}));
      assert.equal(ghost.style.left,'114px');assert.equal(f.part.dropHint.dataset.allowed,'true');
      let graphClicks=0;f.canvas.addEventListener('click',()=>graphClicks++);
      let graphPointerDowns=0;f.canvas.addEventListener('pointerdown',()=>graphPointerDowns++);
      const down=new f.w.MouseEvent('pointerdown',{bubbles:true,button:0,cancelable:true});
      node.dispatchEvent(down);assert.equal(down.defaultPrevented,true);assert.equal(graphPointerDowns,0);
      node.dispatchEvent(new f.w.MouseEvent('click',{bubbles:true,button:0}));await tick();
      assert.deepEqual(plain(f.applied),[['template_duty',{kind:'resident',resident_ids:['resident_a']}]]);
      assert.equal(graphClicks,0);assert.equal(ghost.hidden,true);
      assert.equal(f.part.dropHint.hidden,true);assert(!f.canvas.classList.contains('templatePicking'));
    }finally{f.close();}
  });
  test('double click opens workshop without pickup, and Enter / Shift+Enter implement both actions',async()=>{
    const f=fixture();try{await f.part.load();
      const tile=f.tile('template_duty');
      for(const detail of [1,2])tile.dispatchEvent(new f.w.MouseEvent('click',{bubbles:true,detail}));
      tile.dispatchEvent(new f.w.MouseEvent('dblclick',{bubbles:true,detail:2}));await pause();
      assert.deepEqual(f.editors,['template_duty']);assert.deepEqual(f.pickups,[]);assert.deepEqual(f.applied,[]);
      f.key('template_prompt','Enter');assert.equal(f.pickups.length,1);
      f.key('template_prompt','Enter',true);await tick();
      assert.deepEqual(f.editors,['template_duty','template_prompt']);
      assert.equal(f.w.document.querySelector('.templateCarryPreview').hidden,true);
    }finally{f.close();}
  });
  test('unsupported target keeps pickup, and Escape, right click, identity, lineage and disposal cancel it',async()=>{
    const f=fixture();try{await f.part.load();
      const node=f.canvas.firstElementChild,ghost=f.w.document.querySelector('.templateCarryPreview');
      f.key('template_duty','Enter');f.target('unsupported');
      node.dispatchEvent(new f.w.MouseEvent('click',{bubbles:true,button:0}));
      assert.equal(ghost.hidden,false);assert.equal(f.part.dropHint.dataset.allowed,'false');assert.deepEqual(f.applied,[]);
      f.w.document.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
      assert.equal(ghost.hidden,true);
      f.key('template_duty','Enter');node.dispatchEvent(new f.w.MouseEvent('contextmenu',{bubbles:true,cancelable:true}));
      assert.equal(ghost.hidden,true);
      f.key('template_duty','Enter');f.switchLineage('lineage_b');assert.equal(ghost.hidden,true);
      f.key('template_duty','Enter');await f.switchUser('bob');assert.equal(ghost.hidden,true);
      f.tile('template_duty').dispatchEvent(new f.w.MouseEvent('click',{bubbles:true,detail:1}));
      f.part.abort.abort();await pause();assert.equal(f.w.document.querySelector('.templateCarryPreview'),null);
      assert.deepEqual(f.applied,[]);
    }finally{f.close();}
  });
  test('drag keeps the original MIME, target hint, application and prompt-draft paths',async()=>{
    const f=fixture();try{await f.part.load();const node=f.canvas.firstElementChild;
      f.drag(f.tile('template_duty'),'dragstart');assert.equal(f.transfer.values.template_transfer,'template_duty');
      f.drag(node,'dragover');assert.equal(f.part.dropHint.hidden,false);
      f.drag(node,'drop');await tick();
      assert.deepEqual(plain(f.applied),[['template_duty',{kind:'resident',resident_ids:['resident_a']}]]);
      f.drag(f.tile('template_prompt'),'dragstart');assert.equal(f.transfer.values['text/plain'],'prompt draft');
    }finally{f.close();}
  });
  function productionFunction(file,name) {
    let found;
    traverse(parser.parse(source(file)),{FunctionDeclaration(p){if(p.node.id.name===name)found=source(file).slice(p.node.start,p.node.end);}});
    assert(found);return found;
  }
  test('new template opens a structure duty editor directly with no kind chooser',()=>{
    let draft;
    const shared={window:{}};vm.runInNewContext(source('lineage_template_workshop_draft'),shared);
    const result=vm.runInNewContext(productionFunction('lineage_templates_editing','newTemplate')+'\nnewTemplate()',{
      templatesState:{kinds:[{key:'structure',targets:['resident','canvas'],editor:{fields:[]}}]},
      editorHost:{},
      window:shared.window,
      t:String,edit:value=>{draft=plain(value);return 'editor';},
    });
    assert.equal(result,'editor');assert.equal(draft.kind,'structure');assert.equal(draft.category,'structure');
    assert.deepEqual(draft.body,{roles:[],components:[],wires:[],match:[],function_block:true});
    assert.deepEqual(draft.targets,['canvas']);assert.equal(draft.behavior.persistent,true);
  });
  test('real registry payload creates a visible personal template without using resource favorites',
    {skip:!process.env.TFI_TOOLBOX_REAL_CASE},async()=>{
    const payload=JSON.parse(fs.readFileSync(process.env.TFI_TOOLBOX_REAL_DATA_PATH,'utf8')),f=fixture();
    try{
      f.replaceTemplates(payload.before);await f.part.load();
      assert(!f.visible().includes(payload.created.template_id));
      let request;
      f.w.LineageTemplatesDialogs={createDialogs:()=>({dialog(){},dialogs:new Set()})};
      f.w.LineageTemplatesResidentSettings={createResidentSettings:()=>({})};
      f.w.LineageTemplatesApplication={createApplication:()=>({})};
      f.w.LineageTemplatesEditing={createEditing:deps=>{request=deps.request;return {};}};
      f.w.eval(source('lineage_templates'));
      const controller=f.w.LineageTemplates.mount({shelf:f.shelf,canvas:f.canvas,getContext:()=>({}),toast:message=>f.errors.push(message),
        request:async name=>{assert.equal(name,'CreateTemplate');return {template:payload.created};}});
      assert.equal((await request('CreateTemplate',{template:payload.created})).template.template_id,payload.created.template_id);
      f.replaceTemplates(payload.after);await f.part.load();
      assert(f.visible().includes(payload.created.template_id));
      assert(!f.visible().includes(payload.other.template_id));
      assert.deepEqual(f.forbidden,[]);assert.deepEqual(f.errors,[]);
      controller.destroy();
    }finally{f.close();}
  });
  test('generated translations render expanded Errands and ownership-independent builtin groups',async()=>{
    const f=fixture();try{f.w.eval(source('portal_translations'));
      f.w.PortalI18n.t=value=>f.w.__PORTAL_TRANSLATIONS__[value]?.en||value;await f.part.load();
      assert.equal(f.shelf.querySelector('[data-toolbox-group="errands"] h4').textContent,'Errands');
      assert.equal(f.shelf.querySelector('[data-toolbox-group="organizations"] h4').textContent,'Organizations');
    }finally{f.close();}
  });
  test('drawer dock, store entry, pickup close and retained primitive sources use actual markup/bindings',()=>{
    const dom=new JSDOM(fs.readFileSync(path.join(root,'management_portal/hatg-panel.html'),'utf8'),{runScripts:'outside-only'}),w=dom.window;
    try{
      const drawer=w.document.getElementById('hatgToolbox'),toggle=w.document.getElementById('hatgToolboxToggle');
      assert.equal(drawer.hidden,true);assert(w.document.getElementById('hatgViewport').contains(drawer));
      assert.equal(toggle.textContent,'');assert.equal(toggle.getAttribute('aria-label'),'工具箱');
      assert(toggle.parentElement.classList.contains('hatgCanvasCornerTools'));
      w.PortalI18n={t:String};w.eval(source('hatg_panel_bindings'));
      const none=()=>{},deps=Object.fromEntries(w.PortalHatgPanelBindings.dependencies.map(key=>[key,none]));
      Object.assign(deps,{$id:id=>w.document.getElementById(id),$queryAll:s=>[...w.document.querySelectorAll(s)],$eventRoot:()=>w.document,
        state:{},panelHost:{pageStateController:{listen:(_scope,node,event,handler)=>node.addEventListener(event,handler)}},
        bindPrimitiveLibrary:none,bindGraphNodeSearch:none,hatgModule:()=>({observeNavigationHeight:()=>none})});
      w.ResizeObserver=class{observe(){} disconnect(){}};
      w.PortalOverlay={bindPopover:()=>({open(){},close(){},isOpen:()=>false,toggle(){},destroy(){}})};
      w.PortalHatgPanelBindings.createBindings(deps).bindActions();
      let storeOpened=false;w.PortalTemplateStore={open:()=>{storeOpened=true;}};
      const store=w.document.querySelector('#hatgToolboxStore button');assert.equal(store.disabled,false);assert.equal(store.title,'模板商店');
      toggle.click();store.click();assert.equal(storeOpened,true);assert.equal(drawer.hidden,true);
      toggle.click();assert.equal(drawer.hidden,false);
      drawer.dispatchEvent(new w.CustomEvent('portal:template-picked',{bubbles:true}));
      assert.equal(drawer.hidden,true);assert.equal(toggle.getAttribute('aria-expanded'),'false');
      toggle.click();drawer.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(drawer.hidden,true);
      const primitives=drawer.querySelector('#hatgPrimitiveLibrary');assert.equal(primitives.open,false);
      assert.deepEqual([...primitives.querySelectorAll('[data-hatg-library-kind]')].map(node=>node.dataset.hatgLibraryKind),['event_source','filter','selector','reducer','effect','seed']);
      w.PortalCompatibility={identifier:name=>name};w.PortalLineageProgram={componentKinds:['event_source','filter','selector','reducer','effect','seed']};
      w.eval(source('hatg_program_edits'));
      const edits=Object.fromEntries(w.PortalHatgProgramEdits.dependencies.map(key=>[key,none]));
      Object.assign(edits,{$id:deps.$id,$queryAll:deps.$queryAll,$eventRoot:deps.$eventRoot,state:{lineage:{lineage_id:'lineage_a'}}});
      w.PortalHatgProgramEdits.createProgramEdits(edits).bindPrimitiveLibrary();
      const values={},event=new w.Event('dragstart',{bubbles:true,cancelable:true});
      Object.defineProperty(event,'dataTransfer',{value:{setData:(type,value)=>{values[type]=value;}}});
      primitives.querySelector('[data-hatg-library-kind="effect"]').dispatchEvent(event);assert.equal(values.primitive_transfer,'effect');
    }finally{w.close();}
  });
})();
