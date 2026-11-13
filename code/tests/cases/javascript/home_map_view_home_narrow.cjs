// Actual Home host + native Frontier. Transport/chat are isolated fixtures;
// all layout, view lifecycle, selection and edit dialog code is the real component.
const homeAction=require('./home_dialogue_controls.cjs');
const {writeFileSync} = require('node:fs');
const path = require('node:path'), assert = require('node:assert/strict');
module.exports = async ({page,out,lineage,teamTitle,checks,calls}) => {
  const evidence={snapshots:[],pointerEvents:[]};
  const save=()=>writeFileSync(path.join(out,'home-hit.json'),JSON.stringify(evidence,null,2));
  if(process.env.HOME_MAP_HOST_HEIGHT_AUTO==='1') {
    // Diagnostic only, never a product patch: separate the host's fixed-panel
    // height conflict from native Frontier's inner scrolling.
    evidence.diagnosticOverride='.portalWorkbenchPanelFrame > .homeDialogueExpanded { height:auto; }';
    await page.addStyleTag({content:evidence.diagnosticOverride});
  }
  await page.evaluate(title=>{
    window.PortalNav={personalAssistantTarget:async()=>({resident_id:'resident_0123456789ab',title:'Fixture chat'})};
    window.PortalResourceChips={...window.PortalResourceChips,loadReference:async()=>({title})};
    window.PortalChatWorkspace={panelPackage:{create:({root})=>{
      root.textContent='Isolated chat transport';
      return {workspace:{addReference(){}},setPresented(){},destroy(){}};
    }}};
    window.host=PortalHomeDialogue.connect({root:document.querySelector('#root'),context:{currentUserReady:Promise.resolve({username:'fixture'})}});
  },teamTitle);
  const display=page.locator('.homeDialogueDisplay');
  await page.evaluate(id=>PortalPreview.request(PortalDisplayProtocol.previewRequest({view:'frontier',target:{kind:'lineage',id}})),lineage);
  const surface=page.locator('.homeMapSurface:not([hidden])');
  const seed=surface.locator('.graphNode[data-kind="seed"]');
  await seed.waitFor();await surface.getByRole('button',{name:'适应',exact:true}).click();await seed.click();
  await surface.getByRole('button',{name:'缩小',exact:true}).click();
  await homeAction(page,'展开');
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(()=>document.documentElement.style.fontSize='20px');
  await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
  const detail=surface.getByRole('complementary',{name:'节点详情',exact:true});
  await detail.waitFor();
  const action=detail.getByRole('button',{name:'更新节点',exact:true});
  await action.evaluate(button=>{
    window.hitEvents=[];
    window.measureHit=()=>{
      const bounds=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
      const r=button.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
      const doc=button.ownerDocument,shadow=button.getRootNode();
      let hit=doc.elementFromPoint(x,y);const hitPath=[];
      while(hit){hitPath.push(hit.tagName+'.'+hit.className);const next=hit.shadowRoot?.elementFromPoint(x,y);if(!next||next===hit)break;hit=next;}
      const parents=[];
      for(let el=button;el;el=el.parentElement||el.getRootNode().host){
        const css=getComputedStyle(el);
        parents.push({tag:el.tagName,id:el.id,class:el.className,...bounds(el),clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,
          scrollTop:el.scrollTop,overflowY:css.overflowY,height:css.height,minHeight:css.minHeight,flex:css.flex});
      }
      const localHit=shadow.elementFromPoint(x,y);
      return {viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY,visualHeight:visualViewport.height,visualTop:visualViewport.offsetTop},
        topLevel:window===window.top,rootType:shadow.constructor.name,button:bounds(button),center:{x,y},
        pointInViewport:x>=0&&x<innerWidth&&y>=0&&y<innerHeight,
        localHitOwned:localHit===button||button.contains(localHit),documentHitOwned:hit===button||button.contains(hit),hitPath,parents};
    };
    button.addEventListener('pointerdown',event=>window.hitEvents.push({pointerType:event.pointerType,x:event.clientX,y:event.clientY,measurement:window.measureHit()}));
  });
  const sample=async phase=>{const value=await page.evaluate(()=>window.measureHit());evidence.snapshots.push({phase,...value});save();return value;};
  await sample('loaded');
  await action.focus();
  await sample('after-focus');
  // Page screenshot retains the viewport; Locator screenshot may scroll ancestors.
  await page.screenshot({path:path.join(out,'viewport-after-focus.png')});
  await display.screenshot({path:path.join(out,'display-after-focus.png')});
  await sample('after-display-screenshot');
  try {
    await action.click({timeout:5000});
    await surface.locator('#editor').waitFor({state:'visible'});
    await surface.locator('#editor').getByRole('button',{name:'关闭',exact:true}).click();
    await sample('after-ordinary-mouse-click');
    await page.screenshot({path:path.join(out,'viewport-mouse-action.png')});
    // Start from the top again; a normal touch activation must scroll and open.
    await detail.evaluate(el=>el.scrollTop=0);
    await action.tap({timeout:5000});
    await surface.locator('#editor').waitFor({state:'visible'});
    await surface.locator('#editor').getByRole('button',{name:'关闭',exact:true}).click();
    await sample('after-ordinary-touch');
    await page.screenshot({path:path.join(out,'viewport-touch-action.png')});
    evidence.pointerEvents=await page.evaluate(()=>window.hitEvents);
    evidence.frames=page.frames().map(f=>({url:f.url(),main:f===page.mainFrame()}));
    save();
    assert.deepEqual(evidence.pointerEvents.map(e=>e.pointerType),['mouse','touch']);
    for(const e of evidence.pointerEvents){assert.equal(e.measurement.pointInViewport,true);assert.equal(e.measurement.documentHitOwned,true);}
    assert.ok(!calls.some(c=>['DeliverMessage','UpdateSharedState','ApplyResearchMapChanges'].includes(c.name)));
    checks.push('actual Home expanded host at 390x844 / 20px: normal mouse and touch reach native edit action with document hit ownership; no business writes');
  }catch(error){evidence.failure=error.message;evidence.pointerEvents=await page.evaluate(()=>window.hitEvents);save();await page.screenshot({path:path.join(out,'viewport-failed-action.png')});throw error;}
};
