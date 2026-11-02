'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const codeRoot=process.env.TFI_TAU_CODE_ROOT || path.resolve(__dirname,'../../..');
const frameworkRoot=path.join(codeRoot,'management_portal/test_framework');
const {chromium}=require(path.join(frameworkRoot,'tau_runtime/node_modules/playwright'));
const prompt=fs.readFileSync(path.join(frameworkRoot,'config/tau_runner_prompt.md'),'utf8');
const example=prompt.match(/```js\n(\/\/ tau-workspace-root-read\n[\s\S]*?)\n```/);
assert.ok(example,'Workspace root response example must cover the exact /file address');
const install=new Function('require','frameworkRoot','portal','origin','workspaceId','record',example[1]+'; return directoryObserver;');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page=await browser.newPage(),origin='http://workspace-root.fixture.invalid';
    const workspaceId='workspace_111111111111',rootAddress=workspaceId+'/file';
    let sequence=0;const identities=new WeakMap(),rows=[];
    page.on('request',r=>identities.set(r,'original-'+(++sequence)));
    await page.route('**/*',route=>{
      const request=route.request();
      if(request.method()==='GET')return route.fulfill({contentType:'text/html',body:'<!doctype html><title>Directory transport fixture</title>'});
      const args=request.postDataJSON().arguments;
      return route.fulfill({json:{status:'committed',instruction:'ReadResource',instruction_id:'original-root',result:{
        kind:'directory',file_reference:{workspace_id:args.address.split('/')[0],relative_path:''},
        entries:[{name:'sample.txt',kind:'text',address:args.address+'/sample.txt'}],truncated:false}}});
    });
    await page.goto(origin);
    const post=async address=>{
      const response=page.waitForResponse(r=>r.request().postDataJSON()?.arguments?.address===address);
      await page.evaluate(address=>fetch('/api/instructions/execute',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({instruction:'ReadResource',arguments:{address}})}).then(r=>r.json()),address);
      return response;
    };
    const previous=await post(rootAddress);
    const observer=install(require,frameworkRoot,{page,requestId:r=>identities.get(r)},origin,workspaceId,r=>rows.push(r));
    try {
      await assert.rejects(observer.original(previous),/UnobservedOriginal/);
      for(const address of [workspaceId,rootAddress+'/sample.txt','workspace_222222222222/file'])
        await assert.rejects(observer.original(await post(address)),/UnobservedOriginal/);
      const response=await post(rootAddress),receipt=await observer.original(response);
      assert.equal(receipt.request_id,identities.get(response.request()));
      assert.equal(receipt.http_status,200);assert.equal(receipt.body_status,'read');
      assert.equal(receipt.body.result.kind,'directory');
      assert.equal(receipt.body.result.file_reference.workspace_id,workspaceId);
      assert.equal(receipt.body.result.entries[0].address,rootAddress+'/sample.txt');
      assert.equal(rows.filter(r=>r.phase==='request_observed').length,1);
      assert.equal((await observer.close()).observation_complete,true);
      assert(!JSON.stringify(rows).includes('sample.txt'),'shared metadata must not dump directory contents');
    }finally{await observer.close();}
    console.log(JSON.stringify({status:'passed',checks:['exact native root address','original request identity','root body returned to caller','late/child/foreign/bare target rejected'],production_calls:0}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
