// Controlled browser checks for races, history restoration and permission loss.
// Real ISA + generated video are separately exercised in the live sample run.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent('<main id="root"></main>');
    await page.addScriptTag({path:path.resolve(__dirname,'../../../management_portal/static/home_report.js')});
    const result = await page.evaluate(async () => {
      const root = document.querySelector('#root');
      const pending = [], requests = [], selected = [], opened = [];
      window.fetch = (url, options) => {requests.push({url,options});return new Promise(resolve => pending.push(resolve));};
      const reply = (body, status=200) => ({ok:status<400,status,json:async()=>body});
      const report = title => ({title,goal:'实际来源在服务端核对',target:{kind:'project',id:title},version:title,checked_at:'2026-11-04T00:00:00Z',warnings:[],sources:[],counts:{'已有回复':1},rows:[{id:'r1',title:'<img src=x onerror=alert(1)>',body:'第一条正文',group:'任务',status:'已有回复',sources:[],open:{view:'map',target:{id:title}}}]});
      const view = PortalHomeReportView.create({root,onOpen:v=>opened.push(v),onSelection:v=>selected.push(v)});
      await view.setPresented(false);
      await view.show({kind:'project',id:'old'},{requestId:'old'});
      const hiddenRequests = requests.length;
      const restore = view.setPresented(true);
      const newer = view.show({kind:'project',id:'new'},{requestId:'new'});
      pending[1](reply(report('new'))); await newer;
      pending[0](reply(report('old'))); await restore;
      const finalTitle = root.querySelector('h2').textContent;
      const xss = root.querySelector('img') === null;
      [...root.querySelectorAll('button')].find(b=>b.textContent==='带入对话').click();
      [...root.querySelectorAll('button')].find(b=>b.textContent==='打开原始对象').click();
      const generating = [...root.querySelectorAll('button')].find(b=>b.textContent==='生成讲解视频');generating.click();
      pending[2](reply({error:'speech temporarily unavailable'},503)); await new Promise(r=>setTimeout(r,0));
      const textSurvivesVideoFailure = root.querySelector('.hr-text').textContent.includes('第一条正文') && root.textContent.includes('重试视频');
      const aborter = new AbortController();aborter.abort();
      await view.show({kind:'project',id:'aborted'},{signal:aborter.signal});
      const abortClears = root.childElementCount === 0;
      const show = view.show({kind:'project',id:'again'});pending[3](reply(report('again')));await show;
      window.dispatchEvent(new Event('portal:permissions-changed'));
      pending[4](reply({readable:true}));await new Promise(r=>setTimeout(r,0));
      pending[5](reply(report('fresh')));await new Promise(r=>setTimeout(r,0));
      const historyRetained=root.querySelector('h2').textContent==='again' && root.textContent.includes('来源已有新版本');
      window.dispatchEvent(new Event('portal:permissions-changed'));
      pending[6](reply({error:'revoked'},403)); await new Promise(r=>setTimeout(r,0));
      const permissionClears = !root.querySelector('.hr-card') && root.textContent.includes('已撤去');
      view.destroy();
      const count = requests.length; await view.setPresented(true);
      return {hiddenRequests,finalTitle,xss,textSurvivesVideoFailure,abortClears,permissionClears,historyRetained,selected,opened,requestsAfterDestroy:requests.length-count};
    });
    assert.equal(result.hiddenRequests,0);assert.equal(result.finalTitle,'new');
    assert.ok(result.xss && result.textSurvivesVideoFailure && result.abortClears && result.permissionClears && result.historyRetained);
    assert.equal(result.selected.length,1);assert.equal(result.selected[0].text,'<img src=x onerror=alert(1)>\n已有回复\n第一条正文');
    assert.equal(result.opened[0].view,'map');assert.equal(result.requestsAfterDestroy,0);
    console.log(JSON.stringify(result,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
