const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,firefox}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root=path.resolve(__dirname,'../../..');
const html=`<!doctype html><link rel="stylesheet" href="/static/paper.css"><style>#host{width:700px;height:450px}button{position:fixed;top:150px;left:200px;width:200px;height:100px}</style><div id="host"></div><button id="result">Saved search results</button><script type="module">
import {openReader} from '/static/paper_fullscreen_reader.js';
window.mode='reader';window.clicks=0;document.querySelector('#result').onclick=()=>clicks++;
window.reader=openReader({paper:{id:'paper_fixture',title:'Fixture',pdf_url:'/fixture.pdf'},principal:'fixture',container:()=>document.querySelector('#host'),isCurrent:()=>mode==='reader'});
window.ready=true;
</script>`;
const server=http.createServer((req,res)=>{const url=new URL(req.url,'http://local');
 if(url.pathname==='/'){res.setHeader('content-type','text/html');return res.end(html);}
 if(url.pathname.startsWith('/paper/api/')){res.setHeader('content-type','application/json');return res.end('{}');}
 const file=url.pathname==='/fixture.pdf'?path.join(root,'tests/cases/fixtures/paper-synthetic-means.pdf'):path.join(root,'management_portal',url.pathname);
 if(!file.startsWith(root)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.statusCode=404;return res.end();}
 res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.pdf':'application/pdf','.html':'text/html'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
try{for(const [name,browserType] of [['chromium',chromium],['firefox',firefox]]){
 const browser=await browserType.launch({headless:true});try{const page=await browser.newPage();
 await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready&&document.querySelector('iframe'));
 await page.evaluate(()=>window.originalFrame=document.querySelector('iframe'));
 for(const mode of ['semantic-history','collector-history','library','today','review','exp','notes']){
  await page.evaluate(mode=>{window.mode=mode;document.querySelector('#host')?.remove();},mode);
  await page.waitForFunction(()=>document.querySelector('dialog').hidden&&getComputedStyle(document.querySelector('dialog')).display==='none');
  await page.locator('#result').click();
  assert.equal(await page.evaluate(()=>document.querySelector('iframe')===originalFrame),true);
  await page.evaluate(()=>{const host=document.createElement('div');host.id='host';document.body.prepend(host);window.mode='reader';});
  await page.waitForFunction(()=>!document.querySelector('dialog').hidden);
 }
 assert.equal(await page.evaluate(()=>clicks),7);
 await page.evaluate(()=>reader.setFullscreen(true));await page.waitForFunction(()=>!document.querySelector('dialog').classList.contains('is-embedded'));
 await page.evaluate(()=>window.mode='library');await page.waitForFunction(()=>document.querySelector('dialog').hidden);
 await page.evaluate(()=>{mode='reader';reader.setFullscreen(false);document.querySelector('#host').style.display='none';});
 await page.waitForFunction(()=>document.querySelector('dialog').hidden);
 console.log(name+': seven navigation modes, click-through, same iframe restore, fullscreen and hidden host passed');
 }finally{await browser.close();}}
}finally{server.close();}})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
