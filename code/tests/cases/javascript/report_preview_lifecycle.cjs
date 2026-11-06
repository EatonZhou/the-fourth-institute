'use strict';
// Control network ordering while exercising the production Preview presenters.
const assert=require('node:assert/strict');
module.exports=async({page,id,packageAddress,lifecycle})=>{
  const start=async(address=id,display=false)=>page.evaluate(async({address,display})=>{
    const payload=display?{source:'display',displayIntent:{view:'report',target:{kind:'report',id:address,revision:3}}}:
      await PortalResourceChips.loadPreview(address,{reference:{resource_type:'report',content_kind:'json',media_type:'application/json',filename:'report.json'}});
    const handle=PortalPreview.render({root:'#preview',document:payload});
    window.readResult=handle.promise.then(()=>({ok:true}),error=>({ok:false,name:error.name,message:error.message}));
  },{address,display});
  const done=()=>page.evaluate(()=>window.readResult);
  const visible=async()=>{
    await page.getByRole('heading',{name:'汇报中的解释',exact:true}).waitFor();
    assert.equal(await page.locator('#preview').evaluate(el=>el.hidden),false);
    await page.getByText('这是固定解释包的正文。',{exact:true}).waitFor();
  };
  // ResourceChips canonicalizes a package to report; direct package documents
  // below exercise the other shared renderer without an extra product branch.
  if(lifecycle.startsWith('cold-switch-')) {
    const kind=lifecycle.slice('cold-switch-'.length);
    let release,seen;
    const held=new Promise(resolve=>{release=resolve;});
    const reached=new Promise(resolve=>{seen=resolve;});
    await page.route(`**/static/${kind==='explanation'?'home_explanation':'home_report'}.js`,async route=>{seen();await held;await route.continue();});
    try {
      if(kind==='explanation')await page.evaluate(async address=>{
        const content=await (await fetch('/api/resources/content?address='+encodeURIComponent(address))).text();
        const handle=PortalPreview.render({root:'#preview',document:{source:'resource',resource_address:address,content,content_kind:'json',media_type:'application/json',path:'manifest.json'}});
        window.readResult=handle.promise.then(()=>({ok:true}),error=>({ok:false,name:error.name}));
      },packageAddress);
      else await start(id,kind==='display');
      await reached;
      await page.evaluate(()=>PortalPreview.render({root:'#preview',document:{path:'current.md',content_kind:'markdown',content:'# 当前文件\n\n切换后的正文'}}));
      await page.getByRole('heading',{name:'当前文件',exact:true}).waitFor();
      release();await done();
      await page.getByRole('heading',{name:'当前文件',exact:true}).waitFor();
      assert.equal(await page.locator('#preview').evaluate(el=>el.hidden),false,'disposed presenter must not hide the new document');
      assert.equal(await page.locator('#preview.home-report,#preview.homeExplanation').count(),0,'late factory must not mutate the new document');
    } finally {release();}
  } else if(lifecycle==='validation-switch') {
    await start();await done();await visible();
    let release,seen;
    const held=new Promise(resolve=>{release=resolve;});
    const reached=new Promise(resolve=>{seen=resolve;});
    await page.route('**/api/home-report/validate',async route=>{seen();await held;await route.continue();});
    try {
      await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await reached;
      assert.equal(await page.locator('#preview').evaluate(el=>el.hidden),true,'accepted report stays hidden during access recheck');
      await page.evaluate(()=>PortalPreview.render({root:'#preview',document:{path:'current.md',content_kind:'markdown',content:'# 当前文件'}}));
      await page.getByRole('heading',{name:'当前文件',exact:true}).waitFor();
      release();
    } finally {release();}
  } else if(lifecycle==='asset-retry') {
    let failed=false;
    await page.route('**/static/home_report.js',route=>{
      if(!failed){failed=true;return route.fulfill({status:503,contentType:'text/plain',body:'temporary unavailable'});}
      return route.continue();
    });
    await start();assert.equal((await done()).ok,false,'cold asset failure reaches host');
    await start();assert.equal((await done()).ok,true,'failed asset can be retried without page reload');await visible();
  } else if(lifecycle==='focus-error-retry') {
    let failed=false;
    await page.route('**/api/home-report?*',route=>{
      if(!failed){failed=true;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'暂时断网'})});}
      return route.continue();
    });
    await start();assert.equal((await done()).ok,false);
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await page.getByRole('button',{name:'重新读取',exact:true}).click();
    await visible();
  } else {
    let release,seen,reads=0;
    const held=new Promise(resolve=>{release=resolve;});
    const reached=new Promise(resolve=>{seen=resolve;});
    await page.route('**/api/home-report?*',async route=>{
      if(++reads===1){const response=await route.fetch();seen();await held;await route.fulfill({response});}
      else if(lifecycle==='identity-loading')await route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({error:'权限已撤回'})});
      else await route.continue();
    });
    try {
      await start();await reached;
      await page.evaluate(event=>window.dispatchEvent(new Event(event)),lifecycle==='identity-loading'?'portal:identity-changed':'focus');
      release();await done();
      if(lifecycle==='identity-loading') {
        await page.getByText(/权限已撤回/).waitFor();
        assert.equal(await page.getByRole('heading',{name:'汇报中的解释',exact:true}).count(),0,'old authorized response cannot survive an identity change');
        assert.equal(reads,2,'identity change restarts the authorized read');
      } else await visible();
    } finally {release();}
  }
  console.log(`report lifecycle ${lifecycle}: passed`);
};
