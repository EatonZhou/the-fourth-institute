// Directed recovery cases. Real Home adapter and native Project/Frontier;
// only the stale-cleanup case substitutes a controllable surface lifecycle.
const assert = require('node:assert/strict');
const path = require('node:path');

module.exports = async ({page,mode,out,direction,checks,setDenied}) => {
  if (mode === 'retry-focus') {
    setDenied(true);
    await page.evaluate(async focus => {
      window.initialFailure = await view.show({kind:'project',id:'test',focus},{view:'frontier',requestId:'recovery'}).catch(e=>e.message);
    }, direction);
    assert.match(await page.evaluate(()=>initialFailure), /No access/);
    assert.equal(await page.locator('.homeMapSurface:not([hidden])').count(),0);
    await page.getByRole('status').filter({hasText:'No access'}).waitFor();
    setDenied(false);
    await page.getByRole('button',{name:'重试',exact:true}).click();
    await page.locator('.homeMapSurface:not([hidden]) .graphNode[data-kind="direction"].is-selected').waitFor({timeout:4000});
    assert.equal(await page.locator('.homeMapSurface:not([hidden]) .graphNode[data-kind="direction"].is-selected').count(),1,
      'retry must restore the requested Frontier layer and exact direction, not the default Project map');
    assert.equal(await page.getByRole('button',{name:'重试',exact:true}).isVisible(),false);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>document.documentElement.style.fontSize='20px');
    await page.screenshot({path:path.join(out,'retry-focus-390.png')});
    checks.push('permission denial hides native content; retry restores original frontier/direction and clears failure');
    return;
  }
  assert.equal(mode,'stale-error');
  await page.evaluate(()=>{
    view.destroy();
    window.releaseCleanup = null;
    window.cleaned = false;
    let badHidden = 0;
    window.PortalProjectMap = {create({root}) {
      let id;
      return {async load(value){id=value;root.textContent=value;}, async setPresented(value){
        if (value && id==='old') throw new Error('old denied');
        if (!value && id==='old' && ++badHidden===1) {
          await new Promise(resolve=>{releaseCleanup=resolve;}); cleaned=true;
        }
      },destroy(){},editing(){return false;}};
    }};
    window.view = PortalHomeMapView.create({root:document.querySelector('#root')});
    window.oldRequest = view.show({kind:'project',id:'old'}).catch(e=>e.message);
  });
  await page.waitForFunction(()=>typeof releaseCleanup==='function');
  await page.evaluate(()=>view.show({kind:'project',id:'new'}));
  assert.equal(await page.locator('.homeMapIdentityName').textContent(),'new');
  assert.equal(await page.locator('.homeMapSurface:not([hidden])').textContent(),'new');
  await page.evaluate(async()=>{releaseCleanup();await oldRequest;});
  assert.equal(await page.getByRole('status').count(),0,'late old failure cannot replace the current successful status');
  assert.equal(await page.getByRole('button',{name:'重试',exact:true}).isVisible(),false,'no stale retry on current map');
  checks.push('new navigation completes during old asynchronous cleanup; late failure never changes current status');
};
