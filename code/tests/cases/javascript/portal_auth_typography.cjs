'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const base=path.resolve(__dirname,'../../../management_portal'),out=process.env.TYPOGRAPHY_EVIDENCE;
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});const rows=[];
 try{
  for(const name of ['login','login_verify','forgot_password','reset_password','join','user_guide'])for(const lang of ['en','zh-CN']){
   const context=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:800}});
   await context.route('**/*',async route=>{
    const p=new URL(route.request().url()).pathname;
    if(p==='/' + name)return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(base,name+'.html'),'utf8')});
    if(p.startsWith('/static/')&&p.endsWith('.css')){
     const f=path.join(base,'static',path.basename(p));if(fs.existsSync(f))return route.fulfill({contentType:'text/css',body:fs.readFileSync(f)});
    }
    await route.abort();
   });
   const page=await context.newPage();await page.goto('http://portal.invalid/'+name);
   const value=await page.evaluate(lang=>{document.documentElement.lang=lang;const family=getComputedStyle(document.body).fontFamily;return{family,controls:[...document.querySelectorAll('button,input,select,textarea')].filter(e=>e.type!=='hidden').map(e=>({tag:e.tagName,tokenDisplay:e.matches('textarea[data-token]'),family:getComputedStyle(e).fontFamily})),overflow:document.documentElement.scrollWidth>innerWidth};},lang);
   rows.push({name,lang,...value});fs.writeFileSync(path.join(out,'auth-pages.json'),JSON.stringify(rows,null,2));
   assert.ok(value.family.includes('Portal Simplified Chinese'),name+' body does not use shared font');
   for(const control of value.controls){
    if(control.tokenDisplay)assert.ok(control.family.includes('monospace'),'machine-readable token retains its monospace presentation');
    else assert.equal(control.family,value.family,name+'/'+control.tag+' font differs');
   }
   assert.equal(value.overflow,false,name+' narrow page overflows');
   if(name==='login'&&lang==='en')await page.screenshot({path:path.join(out,'login-en.png')});
   await context.close();
  }
 }finally{await browser.close();}
 console.log(JSON.stringify({authentication_pages:rows.length}));
})().catch(e=>{console.error(e);process.exitCode=1;});
