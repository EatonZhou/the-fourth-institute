'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const css=process.env.CJK_FONT_SOURCE||path.resolve(__dirname,'../../../management_portal/static');
const out=process.env.CJK_FONT_EVIDENCE;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});const results=[];
 try{
  for(const lang of ['en','zh-CN']){
   const page=await browser.newPage({viewport:{width:390,height:650}});const network=[];page.on('request',r=>network.push(r.url()));
   await page.setContent(`<!doctype html><html lang="${lang}"><body><p id="body">直置改起，。：；</p><section class="portalChatWorkspaceRoot"><p id="chat">直置改起，。：；</p><strong id="bold">直置改起，。：；</strong><span id="latin">English 123</span><span id="reference" style="font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">English 123</span><p>中文 English 😀</p></section></body></html>`);
   for(const file of ['portal_design_system.css','portal_components_chat_frame.css'])await page.addStyleTag({path:path.join(css,file)});
   await page.evaluate(()=>document.fonts.ready);
   const cdp=await page.context().newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');
   const {root}=await cdp.send('DOM.getDocument');const fonts={};
   for(const id of ['body','chat','bold','latin','reference']){const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector:'#'+id});fonts[id]=(await cdp.send('CSS.getPlatformFontsForNode',{nodeId})).fonts.map(f=>({family:f.familyName,glyphs:f.glyphCount}));}
   const geometry=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,lang:document.documentElement.lang}));
   results.push({lang,fonts,geometry,network});fs.writeFileSync(path.join(out,'fonts.json'),JSON.stringify(results,null,2));
   await page.screenshot({path:path.join(out,lang+'.png')});
   for(const id of ['body','chat','bold'])assert.ok(fonts[id].some(f=>/CJK SC|PingFang SC|Hiragino Sans GB|Microsoft YaHei|Source Han Sans SC|Noto Sans SC/.test(f.family)),`${lang}/${id} needs actual simplified Chinese glyph font: ${JSON.stringify(fonts[id])}`);
   assert.deepEqual(fonts.latin,fonts.reference,'Latin keeps its existing platform font');assert.equal(geometry.lang,lang);assert.equal(geometry.overflow,false);assert.deepEqual(network,[],'local fonts need no network download');
   await page.close();
  }
 }finally{await browser.close();}
 console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1;});
