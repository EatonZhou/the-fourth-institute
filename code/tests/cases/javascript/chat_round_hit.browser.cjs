'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
module.exports = async ({ page, browser, setup, output, errors }) => {
  const checks = [], evidence = {};
  const check = (name, value) => { assert.ok(value, name); checks.push(name); };
  const settle = p => p.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  const seed = async p => {
    await p.evaluate(() => {
      items = Array.from({ length:80 }, (_, i) => human('hit' + i, '第' + i + '条独立输入，请准确定位这条消息，不切换邻近请求。'));
      workspace.setMessages(items); window.hitTrace = [];
      for (const type of ['pointerover', 'pointerdown', 'focusin', 'pointerup', 'click']) document.addEventListener(type, event => {
        if (!event.target.closest('.chatRoundNode,.chatRoundMarks')) return;
        hitTrace.push({ type, target:event.target.dataset.roundKey, preview:[...document.querySelectorAll('.chatRoundPreview [data-round-follow]')].map(n => n.dataset.roundFollow) });
      }, true);
    }); await settle(p);
  };
  const point = (p,id) => p.locator(`[data-round-key="${id}"]`).evaluate(n => { const r = n.getBoundingClientRect(); return { x:r.x+r.width/2, y:r.y+r.height/2 }; });
  const preview = p => p.locator('.chatRoundPreview [data-round-follow]').evaluateAll(nodes => nodes.map(n => n.dataset.roundFollow));
  const located = (p,id) => p.locator(`[data-message-key="${id}"]`).evaluate(n => n.classList.contains('messageQuoteTarget'));
  await seed(page);
  let p = await point(page,'hit35');
  await page.mouse.move(p.x,p.y); await page.waitForTimeout(260);
  evidence.beforeClick = await preview(page);
  if(output && !process.env.RAIL_HIT_BASELINE)await page.screenshot({path:path.join(output,'desktop-single-preview.png')});
  await page.mouse.down(); evidence.afterDown = await preview(page); await page.mouse.up(); await settle(page);
  evidence.afterClick = await preview(page); evidence.located = await located(page,'hit35');
  evidence.events = await page.evaluate(() => hitTrace);
  if (process.env.RAIL_HIT_BASELINE) {
    assert.ok(evidence.beforeClick.length > 1 && !evidence.located);
    if (output) { fs.writeFileSync(path.join(output,'hit-result.json'), JSON.stringify({baseline:'reproduced',evidence},null,2)); await page.screenshot({path:path.join(output,'baseline.png')}); }
    console.log('Reproduced native mouse hover/focus/click identity mismatch'); return;
  }
  check('hover is exactly nearest measured message', JSON.stringify(evidence.beforeClick) === '["hit35"]');
  check('pointerdown focus cannot replace preview with topmost overlapping DOM target', JSON.stringify(evidence.afterDown) === '["hit35"]');
  check('click locates same ID instead of opening group', evidence.located && evidence.afterClick.length === 0);
  await page.waitForTimeout(280); check('no delayed hover callback reopens after click', (await preview(page)).length === 0);
  // Move across a midpoint while the same overlapping DOM button still owns the pointer.
  await page.mouse.move(10,10);
  const adjacent = [await point(page,'hit78'),await point(page,'hit79')];
  evidence.overlappingDOM = await page.evaluate(points=>points.map(p=>document.elementFromPoint(p.x,p.y).closest('.chatRoundNode')?.dataset.roundKey),adjacent);
  check('adjacent visible centers are covered by the same DOM button',evidence.overlappingDOM[0]===evidence.overlappingDOM[1]);
  await page.mouse.move(adjacent[0].x,adjacent[0].y); await page.waitForTimeout(260);
  check('first center in shared DOM coverage previews only its ID',JSON.stringify(await preview(page))==='["hit78"]');
  p=adjacent[1]; await page.mouse.move(p.x,p.y); await page.waitForTimeout(260);
  check('moving within the same DOM button resolves next center', JSON.stringify(await preview(page)) === '["hit79"]');
  await page.mouse.click(p.x,p.y); check('second center click stays exact', await located(page,'hit79'));
  await page.mouse.move(adjacent[0].x,adjacent[0].y);await page.waitForTimeout(260);
  await page.mouse.move(adjacent[1].x,adjacent[1].y);await page.mouse.down();
  check('down before hover delay never retains previous message preview',(await preview(page)).every(id=>id==='hit79'));
  await page.mouse.up();await page.waitForTimeout(280);
  check('quick click cancels pending hover and targets new identity',await located(page,'hit79')&&(await preview(page)).length===0);
  await page.mouse.move(10,10); p=await point(page,'hit35'); await page.mouse.move(p.x,p.y);
  await page.locator('[data-round-key="hit12"]').focus(); await page.waitForTimeout(280);
  check('keyboard focus cancels pending pointer preview', JSON.stringify(await preview(page)) === '["hit12"]');
  await page.keyboard.press('Enter'); check('keyboard Enter targets focused identity', await located(page,'hit12'));
  await page.mouse.move(10,10); p=await point(page,'hit35'); await page.mouse.move(p.x,p.y); await settle(page);
  const pressTop=await page.evaluate(()=>workspace.els.messages.scrollTop);
  await page.mouse.down(); await page.waitForTimeout(600); await page.mouse.up(); await settle(page);
  check('long press toggles only nearest message', await page.evaluate(() => workspace.isRoundFollowed('hit35') && !workspace.isRoundFollowed('hit36')));
  check('release after long press does not navigate', await page.evaluate(()=>workspace.els.messages.scrollTop)===pressTop);
  await page.keyboard.press('Escape'); await page.mouse.move(10,10); p=await point(page,'hit35');
  await page.mouse.move(p.x,p.y); await page.waitForTimeout(260); await page.mouse.down();
  await page.evaluate(()=>{
    workspace.setRequestProgress({cursor:80,verified:true,input_states:[workInput('hit35','queued')]});
    document.querySelector('[data-message-key="hit10"]').style.paddingBottom='120px';
  }); await settle(page); await page.mouse.up(); await settle(page);
  check('layout/status change after down preserves accepted target ID',await located(page,'hit35'));
  await page.waitForTimeout(280);check('layout/capture release does not reopen stationary hover',(await preview(page)).length===0);
  await page.keyboard.press('Escape'); await page.locator('.chatRoundDirectory').click();
  check('only explicit directory offers full multi-message list', (await preview(page)).length === 80);
  if (output) await page.screenshot({path:path.join(output,'desktop-directory.png')});
  await page.keyboard.press('Escape');
  await page.evaluate(() => {
    window.hitReads={rows:0,all:0}; const original=Element.prototype.getBoundingClientRect;
    window.restoreHitReads=()=>Element.prototype.getBoundingClientRect=original;
    Element.prototype.getBoundingClientRect=function(){hitReads.all++; if(this.matches('.messageList > [data-message-key]'))hitReads.rows++; return original.call(this);};
  });
  p=await point(page,'hit35'); await page.mouse.move(p.x,p.y); await page.waitForTimeout(260);
  await page.evaluate(()=>hitReads={rows:0,all:0});
  for(let i=0;i<10;i++) await page.mouse.move(p.x+(i%2),p.y);
  evidence.steadyMove = await page.evaluate(()=>({...hitReads}));
  check('same-point pointer moves perform no geometry reads', evidence.steadyMove.all===0);
  await page.mouse.move(10,10); await page.keyboard.press('Escape'); await settle(page);
  await page.evaluate(()=>hitReads={rows:0,all:0});
  for(let i=0;i<15;i++){await page.evaluate(i=>workspace.els.messages.scrollTop=i*30,i);await settle(page);}
  evidence.scroll = await page.evaluate(()=>({...hitReads}));
  check('ordinary scrolling adds no layout reads', evidence.scroll.all===0);
  await page.evaluate(()=>restoreHitReads());
  // Exact co-location remains deterministic; keyboard can still reach both IDs.
  await page.evaluate(()=>{
    const a=document.querySelector('[data-message-key="hit35"]'),b=document.querySelector('[data-message-key="hit36"]');
    a.style.position=b.style.position='absolute'; a.style.top=b.style.top='40px';
  });
  await page.setViewportSize({width:1001,height:850}); await settle(page);
  p=await point(page,'hit35');
  check('fixture has truly co-located layout anchors', Math.abs(p.y-(await point(page,'hit36')).y)<.1);
  await page.mouse.move(p.x,p.y); await page.waitForTimeout(260); evidence.tie=await preview(page);
  check('same-position pointer tie uses stable layout order', JSON.stringify(evidence.tie)==='["hit35"]');
  await page.mouse.click(p.x,p.y); check('same-position click keeps previewed tie identity',await located(page,'hit35'));
  await page.locator('[data-round-key="hit36"]').focus(); await page.keyboard.press('Enter');
  check('co-located second identity remains keyboard addressable',await located(page,'hit36'));
  const mobileContext = await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  const mobile=await mobileContext.newPage(); mobile.on('pageerror',error=>errors.push(error.message)); await setup(mobile); await mobile.evaluate(()=>document.documentElement.style.fontSize='20px'); await seed(mobile);
  const q=await point(mobile,'hit35'); await mobile.touchscreen.tap(q.x,q.y); await settle(mobile);
  check('390 font20 real tap resolves nearest ID directly',await located(mobile,'hit35') && (await preview(mobile)).length===0);
  const cdp=await mobileContext.newCDPSession(mobile),hold=await point(mobile,'hit35');
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:hold.x,y:hold.y}]});
  await mobile.waitForTimeout(600);await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await settle(mobile);
  check('390 touch long press toggles same nearest ID only',await mobile.evaluate(()=>workspace.isRoundFollowed('hit35')&&!workspace.isRoundFollowed('hit36')));
  await cdp.detach();await mobile.locator('.chatRoundDirectory').focus();
  await mobile.locator('[data-round-key="hit35"]').focus();
  check('touch and keyboard preview agree on single identity',JSON.stringify(await preview(mobile))==='["hit35"]');
  if(output)await mobile.screenshot({path:path.join(output,'390-single-preview.png')});
  await mobile.keyboard.press('ArrowDown'); await mobile.keyboard.press('Enter');check('390 next keyboard node remains independent',await located(mobile,'hit36'));
  check('no page errors',errors.length===0);
  if(output)fs.writeFileSync(path.join(output,'hit-result.json'),JSON.stringify({checks,evidence,errors},null,2));
  await mobileContext.close(); console.log('Single target hit checks passed: '+checks.length);
};
