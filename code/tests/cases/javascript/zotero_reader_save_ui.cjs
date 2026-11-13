const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../..'),base=path.join(root,'management_portal/paper_reader_app/zotero_prototype');
const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.wasm':'application/wasm'};
const server=http.createServer((req,res)=>{const file=path.resolve(base,'.'+new URL(req.url,'http://local').pathname);if(!file.startsWith(base+path.sep)||!fs.existsSync(file)||fs.statSync(file).isDirectory()){res.writeHead(404);return res.end();}res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));for(const [name,engine] of [['Chromium',chromium],['Firefox',firefox]]) {
 const browser=await engine.launch({headless:true});try {
 const page=await browser.newPage({viewport:{width:1400,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
 await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(...args){if(window.failWrites)throw Error('test storage failure');return original.apply(this,args);};});
 const open=async()=>{await page.locator('#pdf').setInputFiles([]);await page.locator('#pdf').setInputFiles(path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf'));await page.waitForFunction(()=>document.querySelector('iframe')?.contentWindow?._reader);const outer=page.frames()[1];await outer.frameLocator('iframe').first().locator('.textLayer span').first().waitFor();return outer;};
 const stored=()=>page.evaluate(()=>JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('zotero-prototype:')))||'{"annotations":[]}').annotations);
 let outer=await open();await outer.locator('button.note').click();await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:160,y:160}});
 const edit=outer.locator('[contenteditable=true]').last();await edit.waitFor();await edit.fill('Focused note close');
 // Call host while comment retains focus, rather than clicking a button that blurs first.
 await page.evaluate(()=>window.prototypeHost.close());assert((await stored()).some(a=>a.comment.includes('Focused note close')));console.log(name,'focused note close saved');
 outer=await open();const id=(await stored())[0].id;
 const item=outer.locator(`[data-sidebar-annotation-id="${id}"]`);
 // Diagnostic-only observation of the PDF view's navigation, never a host dependency.
 await outer.evaluate(()=>{const view=window._reader._primaryView,original=view.navigate;window.observedLocations=[];view.navigate=function(location,...args){window.observedLocations.push(location);return original.call(this,location,...args);};});
 await item.locator('header').click();await page.waitForFunction(id=>document.querySelector('iframe').contentDocument.querySelector(`[data-sidebar-annotation-id="${id}"]`).classList.contains('selected'),id);
 assert(await outer.evaluate(id=>window.observedLocations.some(location=>location.annotationID===id),id),'sidebar click navigated to annotation ID');
 await item.focus();await page.keyboard.press('Delete');
 await page.evaluate(()=>window.prototypeHost.close());assert.equal((await stored()).length,0);
 outer=await open();await outer.locator('button.note').click();await outer.frameLocator('iframe').first().locator('.page').first().click({position:{x:170,y:180}});await outer.locator('[contenteditable=true]').last().fill('retry me');
 await page.evaluate(()=>window.failWrites=true);await assert.rejects(page.evaluate(()=>window.prototypeHost.close()),/test storage failure/);assert.equal(await page.locator('iframe').count(),1);await page.evaluate(()=>window.failWrites=false);await page.evaluate(()=>window.prototypeHost.close());assert((await stored()).some(a=>a.comment.includes('retry me')));
 outer=await open();await outer.locator('button.highlight').click();const span=outer.frameLocator('iframe').first().locator('.textLayer span').first();const box=await span.boundingBox();
 await page.mouse.move(box.x+1,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+Math.max(8,box.width-2),box.y+box.height/2,{steps:6});await page.mouse.up();
 await page.evaluate(()=>window.prototypeHost.close());assert((await stored()).some(a=>a.type==='highlight'));outer=await open();assert(await outer.locator('[data-sidebar-annotation-id]').count()>=2);await page.evaluate(()=>window.prototypeHost.close());
 assert.deepEqual(errors,[]);console.log(name,'UI list selection/delete/reopen, failed-close retry, rapid highlight close passed');
 }finally{await browser.close();}
 }server.close();})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
