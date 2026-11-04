// Read-only Dev-source harness against the real Portal API as this Resident.
// Home itself requires a User page session. This harness serves only the page
// and static source under the native origin; it never mocks API responses or
// borrows a user's browser credentials. Personal-assistant TAU stays separate.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {openResidentPortal} = require('../../../scripts/resident_browser.cjs');
const homeAction = require('./home_dialogue_controls.cjs');
const portal = path.resolve(__dirname,'../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(portal,'script_groups.json')));
const html = fs.readFileSync(path.join(portal,'index.html'),'utf8').replace(/<script data-portal-script-group="([^"]+)"><\/script>/g, (_,id)=>groups[id].map(src=>`<script src="${src}"></script>`).join(''))
  .replace('<script src="/static/home.js"></script>', '');
(async()=>{
  const origin = process.env.HOME_DIALOGUE_ORIGIN || 'http://127.0.0.1:28401';
  const client = await openResidentPortal({chromium,origin,residentId:'resident_6b7d6cdae31c',lineageId:'lineage_8794980b1bb7'});
  const out = process.env.HOME_DIALOGUE_EVIDENCE || '/tmp/home-dialogue-live'; fs.mkdirSync(out,{recursive:true});
  const errors=[], failures=[]; const page=client.page; page.setDefaultTimeout(15000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',r=>{if(r.status()>=400)failures.push({path:new URL(r.url()).pathname,status:r.status()});});
  try {
    await page.route('**/*', async route=>{
      const url = new URL(route.request().url());
      if(url.origin !== origin) return route.fallback();
      if(url.pathname==='/') return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname.startsWith('/static/')) {
        const file=path.resolve(portal,'.'+url.pathname);
        if(file.startsWith(portal+'/static/') && fs.existsSync(file)) return route.fulfill({path:file});
      }
      return route.fallback();
    });
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(origin); await page.waitForFunction(()=>!!window.PortalHomeDialogue);
    await page.evaluate(()=>PortalIdentity.load());
    await page.evaluate(()=>{
      const root=document.createElement('section'); root.style.height='100%'; document.querySelector('#homeWorkbenchRoot').append(root);
      window.host=PortalHomeDialogue.connect({root,context:{currentUserReady:Promise.reject(new Error('Resident 只读预览验收：个人门面由用户会话验收'))}});
    });
    await page.waitForSelector('.homeDialogueChooser',{state:'attached'});
    await page.evaluate(()=>PortalPreview.request({resourceAddress:'decision_b12782e7f490'}));
    await page.waitForFunction(()=>document.querySelector('.homeDialogueDocument')?.textContent.includes('对话'));
    await page.screenshot({path:path.join(out,'preview.png'),fullPage:true});
    const evidence={principal:client.identity.principal,origin,scope:'Dev source HTML/static harness, real authenticated API; no user session',
      preview:await page.locator('.homeDialogueDocument').innerText(),
      assistant:await page.locator('.homeDialogueConversation').innerText(),errors,failures};
    await page.locator('.homeDialogueOpener > summary').click();
    await page.getByLabel('目标类型',{exact:true}).selectOption('lineage');
    await page.getByLabel('目标 ID 或文件资源地址').fill('lineage_8794980b1bb7');
    await page.locator('.homeDialogueChooser').getByRole('button',{name:'展示',exact:true}).click();
    await page.locator('.home-report h2').waitFor();
    await page.waitForFunction(()=>document.querySelector('.homeDialogueCurrent').textContent !== 'Lineage');
    assert.equal(await page.locator('.homeDialogue > header').count(),0);
    await homeAction(page,'展开');
    evidence.density=await page.evaluate(()=>{
      const display=document.querySelector('.homeDialogueDisplay').getBoundingClientRect(), content=document.querySelector('.home-report').getBoundingClientRect();
      return {hostControlsHeight:content.top-display.top,title:document.querySelector('.homeDialogueCurrent').textContent,manualFormOpen:document.querySelector('.homeDialogueOpener').open,duplicateTimestamp:document.querySelector('.homeDialogueEntryNotice').textContent};
    });
    assert.ok(evidence.density.hostControlsHeight<65); assert.equal(evidence.density.manualFormOpen,false); assert.equal(evidence.density.duplicateTimestamp,'');
    await page.screenshot({path:path.join(out,'report-expanded-1440.png')});
    const search=page.locator('.home-report input'); await search.fill('研究');
    const entries=await page.getByLabel('展示历史',{exact:true}).locator('option').evaluateAll(options=>options.map(o=>o.value));
    await page.locator('.homeDialogueMore > summary').click();
    await page.screenshot({path:path.join(out,'more-1440.png')});
    await homeAction(page,'固定展示');
    assert.equal(await page.getByRole('button',{name:'取消固定',exact:true,includeHidden:true}).getAttribute('aria-pressed'),'true');
    await homeAction(page,'返回上一展示');
    await page.locator('.homeDialogueDocument:visible').waitFor();
    assert.equal(await page.locator('.homeDialogueDocument:visible').count(),1);
    await page.getByLabel('展示历史',{exact:true}).selectOption(entries[2]);
    await search.waitFor({state:'visible'}); assert.equal(await search.inputValue(),'研究');
    await homeAction(page,'取消固定');
    await homeAction(page,'返回原布局');assert.equal(await search.inputValue(),'研究');
    for(const width of [390,720]) {
      await page.setViewportSize({width,height:844});
      await homeAction(page,'展开');
      await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
      await page.screenshot({path:path.join(out,`report-expanded-${width}.png`)});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.locator('.homeDialogueMore > summary').click();
      await page.screenshot({path:path.join(out,`more-${width}.png`)});
      await homeAction(page,'固定展示'); await homeAction(page,'取消固定');
      await homeAction(page,'返回原布局');
    }
    evidence.reportFilterRetained=true;
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(evidence,null,2));
    assert.deepEqual(errors,[]); console.log(JSON.stringify({...evidence,preview:evidence.preview.slice(0,120)}));
  } catch(error) {
    console.error(JSON.stringify({errors,failures,title:await page.title(),body:(await page.locator('body').innerText()).slice(0,700)}));
    throw error;
  } finally {await client.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
