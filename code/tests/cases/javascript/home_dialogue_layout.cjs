// Native Home/Workbench geometry with isolated API transport. No user session.
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const homeAction=require('./home_dialogue_controls.cjs');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../../management_portal');
const groups=JSON.parse(fs.readFileSync(path.join(root,'script_groups.json')));
const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,(_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''));
const out=process.env.HOME_DIALOGUE_LAYOUT_EVIDENCE||'/tmp/home-dialogue-layout'; fs.mkdirSync(out,{recursive:true});
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/') {res.setHeader('Content-Type','text/html');return res.end(html);}
  if(url.pathname==='/static/portal_resource_routes.js') {res.setHeader('Content-Type','text/javascript');return res.end('window.PortalResourceRoutes={schema:"tfi.resource-portal-routes.v1",routes:[],text_resource_types:[],identity_parameters:[]};');}
  if(url.pathname.startsWith('/api/')) {res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(url.pathname==='/api/me'?{username:'layout-fixture',principal:{kind:'user',id:'layout-fixture'},access:{allows:{}}}:{items:[],accounts:[],usage:[],result:{},schema:'tfi.hatg-instruction-result.v2',status:'committed'}));}
  const file=path.resolve(root,'.'+url.pathname); if(!file.startsWith(root+'/static/')||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript');res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true});const evidence=[];
  try {
    for(const viewport of [{width:1440,height:1000},{width:390,height:844},{width:720,height:500}]) {
      const page=await browser.newPage({viewport,locale:'zh-CN'});page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/?home_component=dialogue-display`);
      await page.locator('.homeDialogueMore').waitFor({state:'attached'});
      assert.equal(await page.locator('.homeDialogue > header').count(),0,'component header is removed, not visually hidden');
      const layout=await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout));
      const close=page.locator('.portalWorkbenchTabClose[aria-label$="对话与展示"]');
      const geometry=()=>close.evaluate(el=>{
        const rect=el.getBoundingClientRect(),hit=document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2),header=document.querySelector('.portalSiteHeader').getBoundingClientRect();
        return {rect:rect.toJSON(),hit:el===hit||el.contains(hit),hitClass:hit?.className,headerBottom:header.bottom,scrollY,ancestors:Array.from((function*(n){while(n){yield n;n=n.parentElement;}})(el)).filter(n=>n.scrollTop).map(n=>({class:n.className,top:n.scrollTop}))};
      });
      const before=await geometry();await homeAction(page,'展开');
      await page.screenshot({path:path.join(out,`expanded-${viewport.width}.png`)});
      await homeAction(page,'返回原布局');
      const after=await geometry();evidence.push({viewport,before,after,errors});
      await page.screenshot({path:path.join(out,`returned-${viewport.width}.png`)});
      if(process.env.HOME_DIALOGUE_EXPECT_OBSTRUCTION==='1'){assert.equal(after.hit,false);break;}
      assert.equal(after.hit,true,'close center must be unobstructed without hover, keyboard or forced click');
      assert.ok(after.rect.y>=after.headerBottom,'tab has space below navigation');
      assert.equal(await page.evaluate(()=>JSON.stringify(PortalWorkbench.get('home').getState().layout)),layout);
      await close.click();
      assert.ok(await page.evaluate(()=>PortalWorkbench.get('home').getState().hidden.includes('dialogue-display')));
      evidence.at(-1).mouseClose=true;assert.deepEqual(errors,[]);await page.close();
    }
    console.log(JSON.stringify(evidence));
  } finally {fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
