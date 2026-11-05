// Real component/buttons with isolated HTTP responses; no private User session.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const out=process.env.REPORT_QUALITY_ARTIFACTS||'/tmp/home-report-quality';fs.mkdirSync(out,{recursive:true});
const base=path.resolve(__dirname,'../../../management_portal/static');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const result={scope:'isolated API; production HomeReport component and native mouse/keyboard',checks:[],screenshots:[]};
 try{
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:900}});
   await page.setContent('<style>html{font-size:20px}body{margin:0}</style><main id="root"></main>');
   await page.addStyleTag({path:path.join(base,'portal_components.css')});
   await page.addStyleTag({path:path.join(base,'home_report.css')});
   for (const file of ['portal_state.js','portal_text.js','portal_i18n.js','portal_ui_primitives.js']) await page.addScriptTag({path:path.join(base,file)});
   await page.addScriptTag({path:path.join(base,'home_report.js')});
   await page.evaluate(()=>{
    window.sent=[];window.opened=[];window.mode='low';window.mediaMode='failed';window.mediaState=null;
    const plan={available:true,mode:'dependency',question:'当前图能证明什么？',assumption:'仅本地依赖遍历',rule:'反向追踪明确依赖',limit:'不推断业务完成',seed:'a',nodes:[{id:'a',title:'明确起点',x:.28,y:.25},{id:'b',title:'依赖者',x:.78,y:.5}],edges:[{id:'e0',from:'b',to:'a',kind:'depends_on'}],before:{reached:[],trace:[]},after:{reached:['b'],trace:[{from:'a',to:'b'}]},unconnected:[]};
    const snapshot=(level='low',revision=1)=>({title:'报告质量验证',goal:'固定版本、真实审核',quality:level,format:level==='low'?'markdown':'interactive',markdown:'# 报告\n\n直接Markdown正文 <script>window.injected=1</script>',target:{kind:'project',id:'fixture'},version:String(revision).repeat(64),sources:[],warnings:[],rows:[],counts:{},reasoning:plan,report_id:'report_123456789abc',revision,latest_revision:revision,review:{status:'changes_requested',feedback:'修改起点与图位置后重审'},reference:{view:'report',target:{kind:'report',id:'report_123456789abc',revision}},exports:[]});
    window.fetch=async(url,options={})=>{
     const body=options.body&&JSON.parse(options.body);sent.push({url,method:options.method||'GET',body});
     let data=snapshot(mode,Number(new URL(url,'https://fixture.test').searchParams.get('revision')||1));
     if(url.includes('/media/')&&!options.method) data=mediaState;
     else if(url.endsWith('/media/cancel')) data=mediaState={status:'cancelled',quality:body.quality};
     else if(url.endsWith('/media')) data=mediaState=mediaMode==='authoring'?{status:'authoring',quality:body.quality,authoring:{id:'a'.repeat(64),status:'submitted'}}:{status:'failed',quality:body.quality,error:'受控生成失败，未降级'};
     else if(url.endsWith('/refresh')) data=snapshot(body.quality,2);
     else if(url.endsWith('/save')) data=snapshot(body.quality);
     return {ok:true,status:200,json:async()=>data};
    };
    window.view=PortalHomeReportView.create({root:document.querySelector('#root'),onOpen:value=>opened.push(value)});
   });
   await page.evaluate(()=>view.show({kind:'project',id:'fixture'},{originKey:'home-request/v1:12345678-1234-1234-1234-123456789abc',originContext:{resident_id:'resident_123456789abc',message_id:'msg_123456789abc',fence_index:0},readOnly:false,quality:'low'}));
   assert.equal(await page.evaluate(()=>sent[0].body.quality),'low');
   assert.equal(await page.evaluate(()=>sent[0].body.origin_context.message_id),'msg_123456789abc');
   assert.equal(await page.locator('.hr-reasoning').count(),0);
   assert.equal(await page.evaluate(()=>window.injected),undefined);
   const writes=await page.evaluate(()=>sent.filter(r=>r.method==='POST').length);
   await page.evaluate(()=>view.show({kind:'report',id:'report_123456789abc',revision:1},{quality:'high'}));
   assert.equal(await page.evaluate(()=>sent.filter(r=>r.method==='POST').length),writes);
   assert.equal(await page.getByLabel('报告质量',{exact:true}).inputValue(),'low');
   await page.getByLabel('报告质量',{exact:true}).selectOption('high');
   await page.getByRole('button',{name:'刷新为新版本',exact:true}).click();
   assert.equal(await page.evaluate(()=>sent.at(-1).body.quality),'high');
   assert.equal(await page.evaluate(()=>opened.at(-1).target.revision),2);
   await page.evaluate(()=>{mode='high';return view.show({kind:'report',id:'report_123456789abc',revision:2});});
   assert.equal(await page.locator('.hr-reasoning').count(),1);
   assert.ok(await page.getByText('需要修改后重新审核',{exact:true}).isVisible());
   const x=await page.locator('g[data-node="a"] rect').first().getAttribute('x');assert.ok(Math.abs(Number(x)-134.6)<.001);
   await page.getByRole('group',{name:'视频质量'}).locator('input[value=high]').check();
   await page.getByRole('button',{name:'生成讲解视频',exact:true}).click();
   const media=await page.evaluate(()=>sent.findLast(r=>r.url.endsWith('/media')&&r.method==='POST'));
   assert.equal(media.body.quality,'high');assert.equal(media.body.revision,2);
   assert.ok(await page.getByText(/受控生成失败/).isVisible());
   await page.getByText('修改为新版本并重新审核',{exact:true}).click();
   await page.getByLabel('修改报告正文').fill('# 新版\n\n修改后重新审核。');
   await page.getByRole('button',{name:'保存修改并重新审核',exact:true}).focus();
   await page.keyboard.press('Enter');
   assert.equal(await page.evaluate(()=>sent.at(-1).body.content.markdown),'# 新版\n\n修改后重新审核。');
   await page.evaluate(()=>{mediaMode='authoring';mediaState=null;});
   await page.getByRole('group',{name:'视频质量'}).locator('input[value=medium]').check();
   await page.getByRole('button',{name:'生成讲解视频',exact:true}).click();
   assert.ok(await page.getByText('分镜任务已交给负责Resident；受理不代表已生成或达标。',{exact:true}).isVisible());
   const postCount=await page.evaluate(()=>sent.filter(item=>item.method==='POST').length);
   await page.getByRole('button',{name:'检查创作进度',exact:true}).click();
   assert.equal(await page.evaluate(()=>sent.filter(item=>item.method==='POST').length),postCount);
   assert.equal(await page.locator('video').count(),0);
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false);
   const shot=path.join(out,`quality-${width}.png`);await page.screenshot({path:shot,fullPage:true});result.screenshots.push(shot);
   await page.getByRole('button',{name:'取消创作',exact:true}).click();
   assert.ok(await page.getByText('生成已取消；重新生成请创建新版本。',{exact:true}).isVisible());
   result.checks.push({width,rootFont:20,postQuality:true,originContext:true,fixedReadOnly:true,highRetainsInteraction:true,keyboardCorrection:true,authoringNotReady:true,readDoesNotDispatch:true,cancellation:true,noOverflow:true});
   await page.evaluate(()=>view.destroy());await page.close();
  }
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
