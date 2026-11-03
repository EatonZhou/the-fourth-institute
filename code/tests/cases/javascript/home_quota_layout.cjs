const assert = require('node:assert/strict');
const path = require('node:path');
const {homeStatements} = require('./home_modules.cjs');
const root = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const functions = ['executionAccounts','accountQuotaWindowIsStale','accountQuotaWindows',
  'accountSummaryQuotaWindows','accountQuotaWarning','accountQuotaBlockers','accountLimitStatus','refreshAccountQuotaWindows','accountQuotaWindow','accountLimitRow',
  'accountRows','accountApiRow','accountBalanceHtml','accountCreditsHtml','renderAccountDetails'];
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
      window.PortalI18n={t:x=>x,html:tag,text:tag,markup:x=>x};
      const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      const formatDateTime=x=>x?new Date(x).toISOString():'';
      const currentCanSetScheduler=()=>false;
      const currentAgentStatusData=()=>data;
      const accountIntakePending=new Map();
      const syncAccountDetailsButtons=()=>{};
      let accountDetails=null;
      ${renderer}
      const quota=(id,label,value,group)=>({id,label,quota_group:group,remaining_percent:value,resets_at:Date.now()/1000+3600});
      const api=(id,balance,capabilities)=>({account_id:id,kind:'api_profile',balance,capabilities});
      const data={accounts:[
        api('deepseek', {status:'ok',display:'123.45 CNY',comparison_amount:123,comparison_currency:'CNY'}),
        {account_id:'claude_one',provider:'claude',kind:'subscription_login',credits:{balance:44904.19},rate_limits:[quota('five_hour','5 小时',72,'claude'),quota('seven_day','每周',64,'claude')]},
        {account_id:'antigravity_one',provider:'antigravity',kind:'subscription_login',rate_limits:[quota('Gemini Models/Weekly Limit Remaining','Gemini Weekly',100,'Gemini Models'),quota('Gemini Models/Five Hour Limit Remaining','Gemini Five Hour',90,'Gemini Models'),quota('other','other models',10,'Claude and GPT models'),quota('Gemini 4 Argon/Gemini 4 Argon Weekly Limit','Gemini 4 Argon · Gemini 4 Argon Weekly Limit',100,'Gemini 4 Argon')]},
        {account_id:'kimi-code',provider:'kimi',kind:'subscription_login',rate_limits:[quota('limit5h','5 小时',75,'kimi-code'),quota('limit7d','每周',58,'kimi-code'),quota('monthCode','每月编程额度',85,'kimi-code')]},
        api('a_very_long_api_account_name', {status:'ok',display:'0 USD'}, {resident_details:false}),
        api('stale', {status:'unavailable',display:'52.12 USD',stale:true}),
        api('unknown', {status:'not_supported'}),
        api('large', {status:'ok',display:'100000 CNY',currency:'CNY',comparison_amount:100000,comparison_currency:'CNY'}),
        api('availability', {status:'ok',metric_kind:'availability'}),
        {account_id:'codex_one',provider:'codex',kind:'subscription_login',rate_limits:[quota('primary','5 小时',100,'codex'),quota('secondary','每周',0,'codex')]}]};
      document.querySelector('main > div').innerHTML=accountRows(data.accounts);
    `});
    for(const id of ['claude_one','antigravity_one','kimi-code']) {
      const row=page.locator('[data-account-limit-account="'+id+'"]');
      assert.deepEqual(await row.locator('.agent-status-window-name').allTextContents(),id==='antigravity_one'?['5 小时','每周','Argon']:['5 小时','每周']);
    }
    assert.deepEqual(await page.locator('[data-account-api] .agent-status-balance-value').allTextContents(),['123.45 CNY','0 USD','52.12 USD*','—','100000 CNY','—']);
    assert.equal(await page.locator('[data-account-api=deepseek] .agent-status-balance-coins i').count(),12);
    assert.equal(await page.locator('[data-account-api=large] .agent-status-balance-coins i').count(),24);
    assert.equal(await page.locator('[data-account-api="a_very_long_api_account_name"] [data-account-details]').count(),0);
    await page.evaluate(()=>{data.accounts[3].rate_limits[0].stale=true;refreshAccountQuotaWindows();});
    assert.deepEqual(await page.locator('[data-account-limit-account="kimi-code"] .agent-status-limit-meta').allTextContents(),['75%*','58%']);
    const antigravity=page.locator('[data-account-limit-account="antigravity_one"]');
    const notice=antigravity.locator('[data-account-quota-warning]');
    assert.equal(await notice.count(),0);
    await page.evaluate(()=>{
      Object.assign(data.accounts[2].rate_limits[2],{id:'Claude and GPT models/Weekly Limit Remaining',label:'Claude and GPT models · Weekly Limit Remaining',remaining_percent:0,observed_at:Date.now()/1000,resets_at:Date.now()/1000+6*86400});
      refreshAccountQuotaWindows();
    });
    assert.match(await notice.innerText(),/其他额度已耗尽/);
    assert.match(await notice.getAttribute('title'),/Claude and GPT models.*剩余 0%.*采样于.*预计重置/);
    assert.deepEqual(await antigravity.locator('.agent-status-window-name').allTextContents(),['5 小时','每周','Argon']);
    assert.deepEqual(await antigravity.locator('.agent-status-limit-meta').allTextContents(),['90%','100%','100%']);
    const argon=antigravity.locator('[data-account-quota-window="Gemini 4 Argon/Gemini 4 Argon Weekly Limit"]');
    assert.match(await argon.getAttribute('title'),/Gemini 4 Argon Weekly Limit/);
    for(const [remaining,stale,expected] of [[null,false,'—'],[0,false,'0%'],[100,true,'100%*']]) {
      await page.evaluate(({remaining,stale})=>{Object.assign(data.accounts[2].rate_limits[3],{remaining_percent:remaining,stale});refreshAccountQuotaWindows();},{remaining,stale});
      assert.equal(await argon.locator('.agent-status-limit-meta').innerText(),expected);
      assert.equal(await antigravity.locator('[data-account-quota-window="Gemini Models/Five Hour Limit Remaining"] .agent-status-limit-meta').innerText(),'90%','Argon cannot block the shared Gemini pool');
    }
    const codex=page.locator('[data-account-limit-account="codex_one"]');
    assert.deepEqual(await codex.locator('.agent-status-limit-meta').allTextContents(),['受限','0%']);
    for(const [index,id,shortId,weekIndex] of [[1,'claude_one','five_hour',1],[2,'antigravity_one','Gemini Models/Five Hour Limit Remaining',0],[3,'kimi-code','limit5h',1]]) {
      const short=page.locator('[data-account-limit-account="'+id+'"] [data-account-quota-window="'+shortId+'"]');
      await page.evaluate(({index,weekIndex})=>{Object.assign(data.accounts[index].rate_limits[weekIndex],{remaining_percent:0,stale:false});refreshAccountQuotaWindows();},{index,weekIndex});
      assert.equal(await short.locator('.agent-status-limit-meta').innerText(),'受限');
      assert.equal(await short.locator('.agent-status-limit-fill, [role=meter]').count(),0,'blocked windows never look full or expose a full meter');
      assert.match(await short.getAttribute('title'),/本窗口剩余.*受同组额度限制.*预计重置/);
      await page.evaluate(({index,weekIndex})=>{Object.assign(data.accounts[index].rate_limits[weekIndex],{stale:true,resets_at:1});refreshAccountQuotaWindows();},{index,weekIndex});
      assert.equal(await short.locator('.agent-status-limit-meta').innerText(),'待确认','a reset time alone does not confirm recovery');
      await page.evaluate(({index,weekIndex})=>{Object.assign(data.accounts[index].rate_limits[weekIndex],{remaining_percent:25,stale:false});refreshAccountQuotaWindows();},{index,weekIndex});
      assert.match(await short.locator('.agent-status-limit-meta').innerText(),/%/);
      assert.equal(await short.locator('.is-blocked').count(),0);
    }
    await page.evaluate(()=>{data.accounts[3].rate_limits[2].remaining_percent=0;refreshAccountQuotaWindows();});
    assert.equal(await page.locator('[data-account-limit-account="kimi-code"] [data-account-quota-window="limit5h"] .agent-status-limit-meta').innerText(),'受限','omitted monthly limits also constrain the same pool');
    await page.evaluate(()=>{data.accounts[3].rate_limits[2].remaining_percent=85;refreshAccountQuotaWindows();});
    for(const width of [1920,1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:1000});
      const problems=await page.evaluate(()=>{
        const issues=[],box=el=>el.getBoundingClientRect();
        const overlap=(a,b)=>a.left<b.right-1 && a.right>b.left+1 && a.top<b.bottom-1 && a.bottom>b.top+1;
        if(document.documentElement.scrollWidth>innerWidth) issues.push('page overflow');
        const apis=[...document.querySelectorAll('[data-account-api]')];
        if(innerWidth>1082 && Math.abs(box(apis[0].querySelector('[data-account-details]')).left-box(document.querySelector('[data-account-limit-account] [data-account-details]')).left)>1) issues.push('API and subscription actions not aligned');
        if(box(apis[1]).top<=box(apis[0]).top) issues.push('APIs not single column');
        for(const row of [...apis,...document.querySelectorAll('.agent-status-account-windows, .agent-status-account-window, .agent-status-account-balance, .agent-status-balance-visual')]) {
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
    await page.evaluate(()=>{
      const panel=document.createElement('section'); panel.className='account-details portalDetailSurface';
      document.body.append(panel);
      accountDetails={accountId:'claude_one',panel,snapshot:{total:1,intake:{},work:{}},
        popover:{isOpen:()=>true,reposition:()=>{},close:()=>{}}};
      renderAccountDetails();
    });
    await page.evaluate(()=>{accountDetails.accountId='antigravity_one';renderAccountDetails();});
    const detailsNotice=page.locator('.account-details-quota-warning');
    assert.match(await detailsNotice.innerText(),/Claude and GPT models.*剩余 0%.*预计重置/);
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.locator('.account-details-body').evaluate(el=>el.scrollWidth>el.clientWidth),false);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    }
    await page.evaluate(()=>{
      data.accounts[2].rate_limits[2].stale=true;
      data.accounts[2].rate_limits[2].resets_at=1;
      refreshAccountQuotaWindows();
    });
    assert.match(await notice.innerText(),/其他额度待确认/);
    assert.match(await detailsNotice.innerText(),/剩余 0%\*，上次读数，待刷新确认/);
    for(const remaining of [null, '0', 10]) {
      await page.evaluate(value=>{Object.assign(data.accounts[2].rate_limits[2],{remaining_percent:value,stale:false});refreshAccountQuotaWindows();},remaining);
      assert.equal(await notice.count(),0,'unknown and recovered limits do not signal exhaustion');
      assert.equal(await detailsNotice.count(),0);
    }
    await page.evaluate(()=>{
      Object.assign(data.accounts[2].rate_limits[2],{remaining_percent:0,label:'<img src=x onerror=alert(1)>',resets_at:Date.now()/1000+6*86400});
      refreshAccountQuotaWindows();
    });
    assert.equal(await detailsNotice.locator('img').count(),0,'quota names are escaped');
    assert.match(await detailsNotice.innerText(),/<img src=x onerror=alert\(1\)>/);
    await page.evaluate(()=>{
      data.accounts[2].rate_limits[2].label='Claude and GPT models · Weekly Limit Remaining';
      refreshAccountQuotaWindows();
      accountDetails.accountId='claude_one';renderAccountDetails();
    });
    assert.match(await page.locator('[data-account-credits] dd').innerText(),/44.?904[.,]19/);
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.locator('.account-details-body').evaluate(el=>el.scrollWidth>el.clientWidth),false);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    }
    await page.evaluate(()=>{data.accounts[1].credits={balance:0,stale:true};refreshAccountQuotaWindows();});
    assert.equal(await page.locator('[data-account-credits] dd').innerText(),'0*');
    if (process.env.PORTAL_LAYOUT_SCREENSHOT) { await page.setViewportSize({width:1440,height:650}); await page.screenshot({path:process.env.PORTAL_LAYOUT_SCREENSHOT}); }
    console.log('Omitted quota exhaustion, stale/unknown/recovery and details refresh pass; compact summaries and six widths pass');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
