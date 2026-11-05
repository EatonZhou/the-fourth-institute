// Adapter-only browser contract. Actual media bytes are checked by the live run.
const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:390,height:800}});
  await page.setContent('<main id="root"></main>');
  for(const file of ['portal_state.js','portal_text.js','portal_i18n.js','portal_ui_primitives.js','home_report.js']) await page.addScriptTag({path:path.resolve(__dirname,'../../../management_portal/static/'+file)});
  const result=await page.evaluate(async()=>{
   const requests=[],references=[],opened=[];let missing=false,denied=false;
   const id='report_123456789abc',originKey='home-request/v1:12345678-1234-1234-1234-123456789abc';
   const snapshot=(revision=1)=>({title:'可读汇报名称',goal:'快照正文',target:{kind:'project',id:'fixture'},version:'v'+revision,checked_at:'2026-11-04T00:00:00Z',sources:[],warnings:[],counts:{},rows:[],reasoning:{available:true},report_id:id,revision,latest_revision:revision,
    reference:{view:'report',title:'可读汇报名称',target:{kind:'report',id,revision},address:`${id}/revision/${revision}`,version:'v'+revision},exports:[],
    media:{status:'ready',base_url:`/api/home-report/reports/${id}/${revision}/file`,chapters:[],transcript:'固定讲稿',duration:1,generation_seconds:1,version:'v'+revision}});
   const data=revision=>({...snapshot(revision),reasoning:{available:true,mode:'insufficient',question:'核对关系',assumption:'本地演示',rule:'仅depends_on',limit:'当前范围',seed:'r1',nodes:[{id:'r1',title:'对象',x:.4,y:.5}],edges:[],before:{reached:[],trace:[]},after:{reached:[],trace:[]},unconnected:[]}});
   window.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body&&JSON.parse(options.body)});
    let body=data(1),status=200;
    if(url.includes('/validate')) {body={readable:!denied,source_changed:false};if(denied){status=403;body={error:'权限撤回'};}}
    else if(url.includes('/refresh')) body=data(2);
    else if(url.includes('/origin')&&missing){status=404;body={error:'尚未生成'};}
    return {ok:status<400,status,json:async()=>body};
   };
   const root=document.querySelector('#root');
   const make=()=>PortalHomeReportView.create({root,onReference:value=>references.push(value),onOpen:value=>opened.push(value)});
   let view=make();
   await view.show({kind:'project',id:'fixture'},{originKey,readOnly:false});
   const initialSource=root.querySelector('video').getAttribute('src');
   const savedVisible=root.textContent.includes('视频已保存')&&!root.textContent.includes('生成讲解视频')&&!root.querySelector('.hr-media').hidden;
   view.destroy();view=make();
   const count=requests.filter(r=>r.method==='POST').length;
   await view.show({kind:'report',id,revision:1});
   const reopenedSame=root.querySelector('video').getAttribute('src')===initialSource;
   const reopeningReadOnly=requests.filter(r=>r.method==='POST').length===count;
   [...root.querySelectorAll('button')].find(b=>b.textContent==='刷新为新版本').click();
   await new Promise(r=>setTimeout(r,0));
   const refreshKeepsOriginal=root.querySelector('video').getAttribute('src')===initialSource&&opened[0]?.target.revision===2;
   view.destroy();missing=true;view=make();
   await view.show({kind:'project',id:'fixture'},{originKey,readOnly:true}).catch(()=>{});
   const before=requests.filter(r=>r.method==='POST').length;
   [...root.querySelectorAll('button')].find(b=>b.textContent==='重新读取').click();
   await new Promise(r=>setTimeout(r,0));
   const retriesReadOnly=requests.filter(r=>r.method==='POST').length===before;
   const noSnapshotCreation=!root.textContent.includes('生成汇报')&&requests.filter(r=>r.method==='POST').length===before;
   await view.show({kind:'project',id:'fixture'},{originKey,readOnly:false}).catch(()=>{});
   const manualOldLinkReadOnly=requests.at(-1).method==='GET'&&requests.at(-1).url.includes('/origin');
   const requestCount=requests.length;
   await view.show({kind:'lineage',id:'lineage_123456789abc'}).catch(()=>{});
   const noSourceFallback=requests.length===requestCount&&root.textContent.includes('正式模板');
   await view.show({kind:'report',id,revision:1});
   denied=true;window.dispatchEvent(new Event('portal:permissions-changed'));
   await new Promise(r=>setTimeout(r,0));
   const revoked=!root.querySelector('video')&&!root.textContent.includes('快照正文');
   view.destroy();
   return {savedVisible,reopenedSame,reopeningReadOnly,refreshKeepsOriginal,retriesReadOnly,noSnapshotCreation,manualOldLinkReadOnly,noSourceFallback,revoked,
    title:references[0].title,origin:new URL(requests[0].url,'http://fixture').searchParams.get('origin_key')};
  });
  for(const [key,value]of Object.entries(result)) if(typeof value==='boolean') assert.equal(value,true,key);
  assert.equal(result.title,'可读汇报名称');console.log(JSON.stringify(result,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
