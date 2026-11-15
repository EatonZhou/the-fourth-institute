'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const base=path.resolve(__dirname,'../../../management_portal/static'), source=process.env.TYPOGRAPHY_SOURCE||base;
const {PORTAL_COMPONENTS_STYLES}=require('./portal_components_styles.cjs');
const out=process.env.TYPOGRAPHY_EVIDENCE;fs.mkdirSync(out,{recursive:true});
const groups={home:['home'],lineage:['lineage_workbench'],paper:['paper'],knowledge:['knowledge_board'],resources:['resource_catalog'],settings:['settings'],article:['article_workspace'],repo:['repo_workspace','repo_workspace_changes','repo_workspace_code','repo_workspace_editor','repo_workspace_catalog']};
(async()=>{
 const browser=await chromium.launch({headless:true});const rows=[];
 try{
  for(const [name,extra] of Object.entries(groups))for(const lang of ['en','zh-CN']){
   const page=await browser.newPage({viewport:{width:Number(process.env.TYPOGRAPHY_WIDTH||390),height:750}});
   await page.setContent(`<!doctype html><html lang="${lang}"><body class="portalBoardShell" data-portal-workbench-entry="lineage"><div class="portalNavShell"><span id="nav">Portal 页面</span></div><main style="display:grid;gap:6px"><aside class="sideView"><header class="paneHead"><div class="paneTitle" id="side">Portal 开发</div></header></aside><section class="portalChatWorkspaceRoot"><header class="paneHead portalWorkspaceHead chatHead"><div class="paneHeadText chatHeadText"><div class="chatTitleLine"><div class="paneTitle" id="chat">原生界面 Chat</div></div></div></header><p><span id="regular">统一字体，中文 English</span> <strong id="strong">统一加粗 Bold</strong></p></section><section><header class="paneHead"><div class="paneTitle" id="terminal">Terminal 终端</div></header><pre style="font-family:var(--mono,monospace)">code 123 终端输出</pre></section><div class="portalWorkbenchTabs"><button class="portalWorkbenchTab" id="tab">Chat 页签</button></div><button class="portalButton" id="button">保存 Save</button><input id="input" value="输入 Input"><div class="residentMenuGroupTab" id="local">设置 Settings</div></main></body></html>`);
   // Keep the sidebar specimen mounted on narrow screens; real pages may hide
   // their navigation there, in which case Chromium reports no rendered glyphs.
   await page.locator('.sideView').evaluate(el=>el.style.display='block');
   for(const file of ['portal_design_system','portal_themes',...PORTAL_COMPONENTS_STYLES,'portal_workbench','portal_resident_menu',...extra]){
    const filename=file+'.css',p=path.join(source,filename);await page.addStyleTag({path:fs.existsSync(p)?p:path.join(base,filename)});
   }
   await page.evaluate(()=>document.fonts.ready);
   const styles=await page.evaluate(()=>Object.fromEntries(['nav','side','chat','terminal','regular','strong','tab','button','input','local'].map(id=>{const s=getComputedStyle(document.getElementById(id));return[id,{family:s.fontFamily,weight:s.fontWeight,size:s.fontSize,line:s.lineHeight}];})));
   const cdp=await page.context().newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');const {root}=await cdp.send('DOM.getDocument');const fonts={};
   for(const id of ['regular','strong','side','chat','terminal']){const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector:'#'+id});fonts[id]=(await cdp.send('CSS.getPlatformFontsForNode',{nodeId})).fonts.map(f=>({family:f.familyName,postScript:f.postScriptName,glyphs:f.glyphCount}));}
   rows.push({page:name,lang,styles,fonts});fs.writeFileSync(path.join(out,'matrix.json'),JSON.stringify(rows,null,2));
   if(name==='lineage'&&lang==='en')await page.screenshot({path:path.join(out,'lineage-en.png')});
   for(const id of ['nav','side','chat','terminal','tab','button','input','local'])assert.equal(styles[id].family,styles.regular.family,`${name}/${lang}/${id}: font family differs`);
   for(const id of ['side','terminal'])assert.deepEqual(styles[id],styles.chat,`${name}/${lang}: matching panel headers must share font, weight and metrics`);
   for(const id of ['strong','side','chat','terminal'])assert.ok(fonts[id].some(f=>/CJK SC.*Bold|CJKsc-Bold/i.test(f.postScript)),`${name}/${lang}/${id}: Chinese bold must resolve to the native bold face, got ${JSON.stringify(fonts[id])}`);
   await page.close();
  }
 }finally{await browser.close();}
 console.log(JSON.stringify({pages:rows.length,matrix:'matrix.json'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
