const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {readFileSync,mkdirSync}=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../../management_portal');
const out=process.env.FRONTIER_EVIDENCE || '/tmp/frontier-evidence';mkdirSync(out,{recursive:true});
const lineage='lineage_0123456789ab',owner='resident_0123456789ab';
const seeds=[
  {id:'one',title:'让研究判断随着工作积累',line:'长期研究能力',attention:'optional',progress:'已经区分技术、方法和认识框架三个贡献层次。',reason:'保留选择与放弃方向的理由，让下一次判断有所依据。',next:'选择一项实际研究，记录框架如何改变了方法选择。',sources:['thinking_ecb9709daa08'],owner},
  {id:'two',title:'什么证据会改变我们的判断？',line:'长期研究能力',attention:'explore',progress:'长期偏好能够带来连续性，也可能放大盲点。',reason:'反例可以让研究品味持续修订，而不是不断自我确认。',next:'为一个已有判断寻找有理由的不同解释。',sources:['thinking_ecb9709daa08'],owner,parent:'one',relation:'builds_on'},
  {id:'three',title:'把值得介入的地方呈现出来',line:'人和 Agent 的协作',attention:'optional',progress:'将介入要求和实际执行状态分开表达。',reason:'人可以选择关注位置，自主推进的工作保持原有节奏。',next:'用同一批真实工作对比阅读与接续过程。',sources:['plan_9c8df049cf09'],owner}
];
let state={schema:'tfi.exploration-frontier.v1',items:seeds}, denied=false, conflict=false, delivered=0, input='running';
const server=http.createServer(async(req,res)=>{
  let name=new URL(req.url,'http://localhost').pathname;
  if(name==='/api/instructions/execute'){
    let text='';for await(const chunk of req)text+=chunk;const body=JSON.parse(text),a=body.arguments;let result={};
    if(body.instruction==='GetLineage')result={lineage:{title:'研究协作 · 实际内容预览',nodes:[{resident_id:owner,title:'研究伙伴'}],project_id:'example'}};
    if(body.instruction==='GetSharedState')result={value:state};
    if(body.instruction==='ResolveResource'){
      if(denied){res.writeHead(403,{'Content-Type':'application/json'});return res.end(JSON.stringify({error:{message:'来源不可读'}}));}
      result={resource:{title:a.address.startsWith('thinking')?'科研选题的思想框架与研究品味':'线头视图：持续呈现探索前沿',version:{number:1}}};
    }
    if(body.instruction==='UpdateSharedState'){
      const matched=!conflict && JSON.stringify(a.expected_value)===JSON.stringify(state);
      if(matched)state=a.value;result={matched,value:state};
    }
    if(body.instruction==='DeliverMessage'){delivered++;result={accepted:true,message_id:a.message.message_id};}
    if(body.instruction==='GetInputState')result={input_state:{state:input,reply_message_ids:input==='completed'?['msg_result']:[]}};
    res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify({status:'committed',result}));
  }
  if(name.startsWith('/api/')){res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify(name==='/api/me'?{username:'fixture',principal:{kind:'user',id:'fixture'}}:{}));}
  try{const file=name==='/frontier'?path.join(root,'frontier.html'):path.join(root,name);res.writeHead(200,{'Content-Type':name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html'});res.end(readFileSync(file));}catch{res.writeHead(404);res.end();}
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto(`http://127.0.0.1:${server.address().port}/frontier?lineage=${lineage}`);await page.waitForSelector('.card');
    assert.equal(await page.locator('.card').count(),3);
    await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'关系图',exact:true}).click();assert.equal(await page.locator('.graph-line').count(),1);
    await page.getByRole('button',{name:'让研究判断随着工作积累',exact:true}).last().click();
    await page.getByRole('button',{name:'稍后关注',exact:true}).click();await page.locator('#detail [data-close]').click();assert.equal(await page.locator('.card').count(),2);
    await page.locator('[data-filter="later"]').click();assert.equal(await page.locator('.card').count(),1);
    await page.locator('.card h3 button').click();await page.getByRole('button',{name:'恢复关注'}).click();await page.locator('#detail [data-close]').click();await page.locator('[data-filter="all"]').click();
    await page.locator('.card h3 button').first().click();await page.getByRole('button',{name:'更新线头'}).click();
    await page.locator('[name="reason"]').fill('寻找可以实际反驳的方法。');conflict=true;
    await page.getByRole('button',{name:'保存线头'}).click();await page.getByText('其他人已更新这条工作线。',{exact:false}).waitFor();assert.equal(await page.locator('[name="reason"]').inputValue(),'寻找可以实际反驳的方法。');
    conflict=false;await page.getByRole('button',{name:'保存线头'}).click();await page.waitForFunction(()=>!document.querySelector('#editor').open);
    await page.locator('.card h3 button').first().click();await page.getByRole('button',{name:'交给 Agent',exact:true}).click();await page.getByRole('button',{name:'确认交给 Agent',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#dispatch').open);assert.equal(delivered,1);
    await page.getByText('推进中',{exact:true}).waitFor();input='completed';await page.getByRole('button',{name:'刷新',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.card').length===2);await page.locator('[data-filter="history"]').click();await page.getByText('已有回复',{exact:true}).waitFor();
    denied=true;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByText('内容已隐藏',{exact:false}).waitFor();assert.equal(await page.locator('.card').count(),0);
    denied=false;await page.getByRole('button',{name:'刷新',exact:true}).click();await page.waitForSelector('.card');await page.locator('[data-filter="all"]').click();
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
    console.log('PASS: render, relationships, personal defer/restore, conflict preserves draft, dispatch receipt, result convergence, permission loss, mobile overflow. Fixture execution only; no live messages.');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
