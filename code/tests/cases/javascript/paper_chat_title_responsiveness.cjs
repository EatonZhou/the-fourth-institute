// A restored research title must not starve the Paper page's event loop.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../../management_portal');
const {chromium} = require(path.join(root, 'test_framework/tau_runtime/node_modules/playwright'));
const groups = JSON.parse(fs.readFileSync(path.join(root, 'script_groups.json')));
const html = fs.readFileSync(path.join(root, 'paper.html'), 'utf8')
  .replace(/<script data-portal-script-group="([^"]+)"><\/script>/g,
    (_, key) => groups[key].map(src => `<script src="${src}"></script>`).join(''))
  .replace('<head>', '<head><script>window.PortalSite={apply(){}};window.__PORTAL_IDENTITY_BOOTSTRAP__={username:"fixture",principal:{kind:"user",id:"fixture"},access:{allows:{}}};</script>');
const paper = {id:'paper_000000000001', title:'Fixture paper', year:'2026', venue:'Fixture', session:'General', authors:[], tags:[], has_pdf:false};

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    for (const [title, displayed] of [
      ['Chat Workspace - Chat Workspace - Research Chat', 'Chat Workspace - Research Chat'],
      ['Chat Workspace - Research Chat', 'Research Chat'],
      ['Existing research assistant', 'Existing research assistant'],
    ]) {
      const page = await browser.newPage({viewport:{width:1440,height:900},locale:'en-US'});
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const resident = {resident_id:'resident_111111111111',lineage_id:'lineage_111111111111',title};
      const slot = {slot_id:'slot_111111111111',resident_id:resident.resident_id,title,state:'bound'};
      const snapshot = {resident,controls:{can_send:true},messages:[],queue:{items:[]}};
      const projection = {resident_id:resident.resident_id,resident,chat_workspace:{
        schema:'portal.chat_workspace.v3',title,target:{kind:'resident',id:resident.resident_id},
        composer:{can_send:true,can_edit:true},messages:[],rendering:{empty_text:'Ready'}}};
      await page.addInitScript(() => {window.__heartbeat=0;setInterval(()=>window.__heartbeat++,50);});
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const send = body => route.fulfill({json:body});
        if (url.pathname === '/paper') return route.fulfill({contentType:'text/html',body:html});
        if (url.pathname === '/static/portal_resource_routes.js') return route.fulfill({contentType:'text/javascript',path:process.env.PORTAL_TEST_RESOURCE_ROUTES});
        if (url.pathname.startsWith('/static/')) {
          const file=path.resolve(root,'.'+url.pathname);
          if (!file.startsWith(root+path.sep) || !fs.existsSync(file)) return route.fulfill({status:404});
          return route.fulfill({path:file,contentType:file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':'text/javascript'});
        }
        if (url.pathname === '/api/instructions/execute') {
          const call=route.request().postDataJSON();
          const result=call.instruction === 'GetConversation' ? snapshot
            : call.instruction === 'ListResidentStates' ? {states:[{resident_id:resident.resident_id,state:{status:'idle'}}]}
            : call.instruction === 'GetResident' ? resident : {};
          return send({status:'committed',result});
        }
        if (url.pathname.includes('/events')) return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
        if (url.pathname === '/paper/api/library') return send({papers:[paper],favorite_ids:[],filters:{years:['2026'],venues:['Fixture'],tags:[],projects:[]},summary:{paper_count:1,visible_count:1,analysis_count:0,review_count:0},folders:[]});
        if (url.pathname === '/paper/api/research/chats') return send({principal:'fixture',slots:[slot],legacy:[],selected:slot});
        if (url.pathname === '/chat-workspace/api/prepare') return send({...projection,lineage_id:resident.lineage_id});
        if (url.pathname.endsWith('/residents/projection')) return send(projection);
        if (url.pathname === '/chat-workspace/api/session') return send({binding_state:'unbound',chat_workspace:{schema:'portal.chat_workspace.v3',composer:{can_send:false,can_edit:true},messages:[]}});
        if (url.pathname.endsWith('/me')) return send({username:'fixture',principal:{kind:'user',id:'fixture'},user:{username:'fixture'},authenticated:true});
        if (url.pathname.endsWith('/semantic-searches')) return send({searches:[]});
        if (url.pathname.endsWith('/collector-jobs')) return send({jobs:[]});
        if (url.pathname.includes('navigation')) return send({modules:[]});
        return send({});
      });
      try {
        await page.goto('http://portal.fixture/paper', {waitUntil:'domcontentloaded'});
        await page.waitForSelector('.paperRow');
        await page.waitForFunction(expected => document.querySelector('#paperResearchChatTitle')?.textContent === expected, displayed);
        const before=await page.evaluate(()=>window.__heartbeat);
        await page.locator('#newPaperBtn').click();
        await page.getByText('Register Paper & Analysis', {exact:true}).waitFor();
        await page.waitForFunction(count => window.__heartbeat > count+2, before);
        assert.deepEqual(errors,[]);
        console.log(`PASS restored title ${JSON.stringify(title)}: library actions and timers remain responsive`);
      } catch(error) {
        console.error(await page.evaluate(()=>({heartbeat:window.__heartbeat,
          titles:[...document.querySelectorAll('.paneTitle')].map(node=>({id:node.id,text:node.textContent})),
          chat:document.querySelector('.portalChatWorkspaceRoot')?.innerText.slice(0,800)})));
        throw error;
      } finally {await page.close();}
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
