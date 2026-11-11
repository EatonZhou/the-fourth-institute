const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const app=path.resolve(__dirname,'../../../management_portal/paper_reader_app'),esbuild=require(path.join(app,'node_modules/esbuild'));
// Actual Zotero renderer and native text selection. Replace PDF storage/API only, using synthetic local PDF.
(async()=>{
const bundle=await esbuild.build({entryPoints:[path.join(app,'reader.jsx')],bundle:true,write:false,format:'esm',plugins:[{name:'isolated-pdf-owner',setup(build){
 build.onLoad({filter:/zotero_prototype\/http_transport.js$/},()=>({contents:`export async function openHttpDocument(){return {document:{paper_id:'fixture',attachment_id:'pdf',attachment_version:'v1'},options:{load:async()=>({pdf:new Uint8Array(await (await fetch('/fixture.pdf')).arrayBuffer()),annotations:[],position:{},author:'Fixture'}),save:async()=>{},remove:async()=>{},savePosition:async()=>{}},persistence:{snapshot:()=>({})}}}` }));
}}]});
let posts=0;
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture');
 if(url.pathname==='/'){res.setHeader('content-type','text/html');return res.end(`<meta charset="utf-8"><link rel="stylesheet" href="/paper.css"><div id="host" style="width:800px;height:500px"></div><script>window.PortalI18n={t:x=>x};window.PortalWorkbench={layout:{bindSplitter:()=>()=>{}}};window.copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>copied.push(text)}});</script><script type="module">import {openReader} from '/reader.js';window.reader=openReader({paper:{id:'fixture',title:'Fixture',pdf_url:'/pdf'},principal:'fixture',container:()=>document.querySelector('#host')});reader.setFullscreen(true);</script>`);}
 if(url.pathname==='/reader.js'){res.setHeader('content-type','text/javascript');return res.end(bundle.outputFiles[0].text);}
 if(url.pathname==='/paper.css'){res.setHeader('content-type','text/css');return res.end(fs.readFileSync(path.join(app,'../static/paper.css')));}
 if(url.pathname.startsWith('/paper/reader-assets/')){const f=path.join(app,'zotero_prototype/upstream',url.pathname.slice('/paper/reader-assets/'.length));if(fs.existsSync(f)&&fs.statSync(f).isFile()){res.setHeader('content-type',({'.js':'text/javascript','.mjs':'text/javascript','.html':'text/html','.css':'text/css','.wasm':'application/wasm'})[path.extname(f)]||'application/octet-stream');return res.end(fs.readFileSync(f));}res.statusCode=404;return res.end();}
 if(url.pathname==='/fixture.pdf'){res.setHeader('content-type','application/pdf');return res.end(fs.readFileSync(path.join(app,'../../tests/cases/fixtures/paper-synthetic-means.pdf')));}
 res.setHeader('content-type','application/json');
 if(url.pathname==='/paper/api/research/chats')return res.end(JSON.stringify({principal:'fixture',selected:{slot_id:'s',state:'bound'}}));
 if(url.pathname==='/paper/api/reader/translation'){posts++;return res.end(JSON.stringify({task_id:'t'+posts,status:'succeeded',text:'Long translated passage. '.repeat(300),provider:'kimi',cache:'miss'}));}
 res.end('{}');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{for(const engine of [chromium,firefox]){const browser=await engine.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:1100,height:720}});page.on('pageerror',e=>console.error('PAGE',e.message));await page.goto('http://127.0.0.1:'+server.address().port);
 await page.waitForFunction(()=>document.querySelector('iframe')?.contentDocument?.querySelector('iframe')?.contentDocument?.querySelector('.textLayer span'));
 const frame=page.frames()[1];
 await frame.frameLocator('iframe').first().locator('.textLayer span').first().dblclick();
 let before=posts;await frame.getByRole('button',{name:'翻译选区',exact:true}).click();await frame.locator('.paperTranslationText').waitFor();assert.equal(posts,before+1);
 if(process.env.POPUP_EVIDENCE) await page.screenshot({path:path.join(process.env.POPUP_EVIDENCE,engine.name()+'-popup.png')});
 assert(await frame.locator('.portalTranslationPopup').evaluate(e=>e.scrollHeight>e.clientHeight),'long popup scrolls');
 await frame.getByRole('button',{name:'复制译文'}).click();assert.equal(await page.evaluate(()=>copied.length),1);
 await frame.getByRole('button',{name:'展开',exact:true}).click();await page.locator('.paperReaderSidePanel:not([hidden]) .paperTranslationText').waitFor();assert.equal(posts,before+1,'expand never posts');
 if(process.env.POPUP_EVIDENCE) await page.screenshot({path:path.join(process.env.POPUP_EVIDENCE,engine.name()+'-expanded.png')});
 assert.equal(await page.locator('.paperReaderSidePanel .paperTranslationBody').evaluate(e=>getComputedStyle(e).maxHeight),'none');
 const geometry=await page.evaluate(()=>{const f=document.querySelector('iframe').getBoundingClientRect(),s=document.querySelector('.paperReaderSidePanel').getBoundingClientRect();return {pdfRight:f.right,sideLeft:s.left};});assert(geometry.pdfRight<=geometry.sideLeft,'sidebar does not cover PDF');
 await page.getByRole('button',{name:'关闭侧栏'}).click();assert.equal(posts,before+1);
 await page.getByRole('button',{name:'打开 AI',exact:true}).click();await page.locator('.paperAIPanel').waitFor();
 await page.locator('.paperAIPanel details').evaluate(e=>e.open=true);await page.getByLabel('便签预览').fill('AI state persists');
 await page.getByRole('tab',{name:'翻译',exact:true}).click();await page.getByRole('tab',{name:'AI',exact:true}).click();assert.equal(await page.getByLabel('便签预览').inputValue(),'AI state persists');assert.equal(posts,before+1);
 await page.getByRole('tab',{name:'翻译',exact:true}).click();await page.locator('.paperReaderSidePanel').getByLabel('翻译模式').selectOption('google');await page.waitForFunction(n=>document.querySelector('.paperReaderSidePanel .paperTranslation small')?.title.includes('task_id: t'+n),before+2);assert.equal(posts,before+2);
 await page.locator('.paperReaderSidePanel').getByRole('button',{name:'重试翻译'}).click();await page.waitForFunction(n=>document.querySelector('.paperReaderSidePanel .paperTranslation small')?.title.includes('task_id: t'+n),before+3);assert.equal(posts,before+3);
 await page.getByRole('button',{name:'关闭侧栏'}).click();await frame.frameLocator('iframe').first().locator('.textLayer span').first().dblclick();await frame.getByRole('button',{name:'翻译选区',exact:true}).click();await frame.locator('.paperTranslationText').waitFor();
 await frame.frameLocator('iframe').first().locator('.page').first().click({position:{x:300,y:300}});await page.waitForTimeout(50);assert.equal(await page.locator('.paperReadingToolbar .paperTranslationMode').count(),0);
 await page.evaluate(()=>reader.setFullscreen(false));assert.equal(await page.locator('.paperReaderSidePanel').isVisible(),false);
 console.log(engine.name()+': selection hook->popup/result/copy/scroll; expand same task; PDF shrinks; AI tab state survives; explicit source/retry only posts; dismissal and embedded hidden');
}finally{await browser.close();}}}finally{server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
