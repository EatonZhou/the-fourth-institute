'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const portal=path.resolve(__dirname,'../../../management_portal');
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:960,height:900}});
    await page.setContent('<!doctype html><meta charset="utf-8"><main></main>');
    for(const name of ['portal_design_system','portal_components_conversation'])
      await page.addStyleTag({path:path.join(portal,'static',name+'.css')});
    await page.addStyleTag({content:'main{display:flex;gap:24px;align-items:flex-start}.sample{flex:none}'});
    for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','chat_workspace_messages'])
      await page.addScriptTag({path:path.join(portal,'static',name+'.js')});
    for(const width of [220,320]) for(const residentLabel of [false,true]) {
      const name='[TAU:testrun_498d9f4c11bd2ec3:resident.identity] Family '+ 'ContinuousName'.repeat(8);
      await page.evaluate(({width,name,residentLabel})=>{
        const module=PortalChatWorkspaceMessages;
        const dependencies=Object.fromEntries(module.dependencies.map(key=>[key,()=>{throw new Error('Unused message dependency '+key);} ]));
        dependencies.escapeHtml=PortalUIPrimitives.escapeHtml;
        const renderer=module.createMessages(dependencies);
        const quote={message_id:'msg_original',author_id:residentLabel?'resident_111111111111':name,text:'Original excerpt '.repeat(60)};
        document.querySelector('main').innerHTML='<section class="sample" id="composer" style="width:'+width+'px">'
          +renderer.composerQuoteHtml(quote)+'</section><section class="sample" id="sent" style="width:'+width+'px">'
          +renderer.messageQuoteHtml(quote)+'</section>';
        // The resource-label boundary supplies the same display title as a live
        // Resident lookup; retain the real renderer's nested label and identity.
        for(const label of document.querySelectorAll('[data-resource-label]'))label.textContent=name;
      },{width,name,residentLabel});
      const geometry=await page.locator('.sample').evaluateAll(roots=>roots.map(root=>{
        const author=root.querySelector('.composerQuoteAuthor,.messageQuoteAuthor');
        const excerpt=root.querySelector('.composerQuoteText,.messageQuoteText');
        const control=root.querySelector('.composerQuoteCancel,.messageQuoteJump');
        const r=root.getBoundingClientRect(),a=author.getBoundingClientRect(),c=control.getBoundingClientRect();
        const range=document.createRange();range.selectNodeContents(author);
        return {id:root.id,root_width:root.clientWidth,root_scroll:root.scrollWidth,
          author_text:author.textContent,author_scroll:author.scrollWidth,author_width:author.clientWidth,
          glyphs_inside:[...range.getClientRects()].every(g=>g.left>=a.left-1&&g.right<=a.right+1),
          control_inside:c.left>=r.left&&c.right<=r.right&&c.width>0&&c.height>0,
          excerpt_text:excerpt.textContent,excerpt_overflow:getComputedStyle(excerpt).textOverflow,
          excerpt_whitespace:getComputedStyle(excerpt).whiteSpace,
          quote_id:root.querySelector('[data-composer-quote],[data-message-quote]').getAttribute(root.id==='composer'?'data-composer-quote':'data-message-quote')};
      }));
      for(const row of geometry) {
        assert.equal(row.author_text,name);
        assert.ok(row.author_scroll<=row.author_width+1&&row.root_scroll<=row.root_width+1&&row.glyphs_inside,JSON.stringify({width,residentLabel,...row}));
        assert.ok(row.control_inside,JSON.stringify(row));
        assert.equal(row.excerpt_text,'Original excerpt '.repeat(60).trim());
        assert.equal(row.excerpt_overflow,'ellipsis');assert.equal(row.excerpt_whitespace,'nowrap');
        assert.equal(row.quote_id,'msg_original');
      }
      if(process.env.TFI_QUOTE_SCREENSHOT && width===220 && residentLabel)
        await page.screenshot({path:process.env.TFI_QUOTE_SCREENSHOT});
    }
    console.log('Native composer and sent quotes contain complete long authors at 220/320px; excerpt data and controls remain intact');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
