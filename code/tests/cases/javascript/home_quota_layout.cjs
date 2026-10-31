const assert = require('node:assert/strict');
const path = require('node:path');
const {homeStatements} = require('./home_modules.cjs');
const root = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const functions = ['executionAccounts','accountQuotaWindowIsStale','accountQuotaWindows',
  'accountSummaryQuotaWindows','refreshAccountQuotaWindows','accountQuotaWindow','accountLimitRow',
  'accountRows','accountApiRow','accountBalanceHtml'];
const renderer = homeStatements().filter(({node:n})=>n.type==='FunctionDeclaration' && functions.includes(n.id.name)).map(s=>s.source).join('\n');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page=await browser.newPage();
    await page.setContent('<main data-home-component="agent-accounts"><div class="agent-status-limit-list"></div></main>');
    await page.addStyleTag({content:'body {margin:16px;font:14px Arial;--muted:#687584;--border:#ccd6de;--chip:#f1f4f6;}'});
    for(const file of ['home_workbench.css','home.css']) await page.addStyleTag({path:path.join(root,'management_portal/static',file)});
    await page.addScriptTag({content:`
      const tag=(parts,...values)=>parts.reduce((s,p,i)=>s+p+(values[i]??''),'');
      window.PortalI18n={t:x=>x,html:tag,text:tag};
      const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const formatDateTime=x=>x?new Date(x).toISOString():'';
      const currentCanSetScheduler=()=>false;
      const currentAgentStatusData=()=>data;
      ${renderer}
      const quota=(id,label,value)=>({id,label,remaining_percent:value,resets_at:Date.now()/1000+3600});
      const api=(id,balance,capabilities)=>({account_id:id,kind:'api_profile',balance,capabilities});
      const data={accounts:[
        api('deepseek', {status:'ok',display:'123.45 CNY',comparison_amount:123,comparison_currency:'CNY'}),
        {account_id:'claude_one',provider:'claude',kind:'subscription_login',rate_limits:[quota('five_hour','5 小时',72),quota('weekly','每周',64)]},
        {account_id:'antigravity_one',provider:'antigravity',kind:'subscription_login',rate_limits:[quota('Gemini Models/Weekly Limit Remaining','Gemini Weekly',100),quota('Gemini Models/Five Hour Limit Remaining','Gemini Five Hour',90),quota('other','other models',10)]},
        {account_id:'kimi-code',provider:'kimi',kind:'subscription_login',rate_limits:[quota('limit5h','5 小时',75),quota('limit7d','每周',58),quota('monthCode','每月编程额度',85)]},
        api('a_very_long_api_account_name', {status:'ok',display:'0 USD'}, {resident_details:false}),
        api('stale', {status:'unavailable',display:'52.12 USD',stale:true}),
        api('unknown', {status:'not_supported'}),
        api('availability', {status:'ok',metric_kind:'availability'})]};
      document.querySelector('main > div').innerHTML=accountRows(data.accounts);
    `});
    for(const id of ['claude_one','antigravity_one','kimi-code']) {
      const row=page.locator('[data-account-limit-account="'+id+'"]');
      assert.deepEqual(await row.locator('.agent-status-window-name').allTextContents(),['5 小时','每周']);
    }
    assert.deepEqual(await page.locator('[data-account-api] [data-account-balance]').allTextContents(),['123.45 CNY','0 USD','52.12 USD*','—','—']);
    assert.equal(await page.locator('.agent-status-balance-coins').count(),0);
    assert.equal(await page.locator('[data-account-api="a_very_long_api_account_name"] [data-account-details]').count(),0);
    await page.evaluate(()=>{data.accounts[3].rate_limits[0].stale=true;refreshAccountQuotaWindows();});
    assert.deepEqual(await page.locator('[data-account-limit-account="kimi-code"] .agent-status-limit-meta').allTextContents(),['75%*','58%']);
    for(const width of [1920,1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:1000});
      const problems=await page.evaluate(()=>{
        const issues=[],box=el=>el.getBoundingClientRect();
        const overlap=(a,b)=>a.left<b.right-1 && a.right>b.left+1 && a.top<b.bottom-1 && a.bottom>b.top+1;
        if(document.documentElement.scrollWidth>innerWidth) issues.push('page overflow');
        const apis=[...document.querySelectorAll('[data-account-api]')];
        if(innerWidth>1082 && Math.abs(box(apis[0].querySelector('[data-account-details]')).left-box(document.querySelector('[data-account-limit-account] [data-account-details]')).left)>1) issues.push('API and subscription actions not aligned');
        if(box(apis[1]).top<=box(apis[0]).top) issues.push('APIs not single column');
        for(const row of [...apis,...document.querySelectorAll('.agent-status-account-window')]) {
          const children=[...row.children], bounds=box(row);
          for(let i=0;i<children.length;i++) {
            const a=box(children[i]);
            if(a.left<bounds.left-1 || a.right>bounds.right+1 || children[i].scrollWidth>children[i].clientWidth+1) issues.push('content overflows');
            for(let j=i+1;j<children.length;j++) if(overlap(a,box(children[j]))) issues.push('content overlaps');
          }
        }
        return issues;
      });
      assert.deepEqual(problems,[],width+'px');
    }
    console.log('Subscription summaries unchanged; API and subscription rows aligned, no coins; six widths pass');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
