'use strict';
// Production native-view markup and styles in an isolated host slot. No live
// WALL API or business actions; one Chromium, sequential viewport scenarios.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const portal = path.resolve(__dirname, '../../../management_portal');
const css = ['portal_design_system.css', ...PORTAL_COMPONENTS_STYLES.map(name => `${name}.css`), 'portal_workbench.css', 'workbench_studio.css', 'wall.css']
  .map(name => fs.readFileSync(path.join(portal, 'static', name), 'utf8')).join('\n');
const template = fs.readFileSync(path.join(portal, 'wall.html'), 'utf8');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }, { width: 844, height: 390 }, { width: 330, height: 500 }]) {
      await page.setViewportSize(viewport);
      await page.setContent(`<style>${css}</style><style>
        body { margin:0; overflow:hidden; } #host { position:absolute; top:104px; left:0; width:100%; height:calc(100dvh - 104px); }
        </style>${template}<div id="host" class="developmentView wallPage"></div>
        <aside class="portalAgentInbox" data-has-assistant="true"><button class="portalAgentInboxTrigger">Inbox</button><button class="portalAgentInboxTrigger portalPersonalAssistantTrigger">Chat</button></aside>`);
      await page.evaluate(() => {
        document.getElementById('host').append(document.getElementById('wallNativeView').content.cloneNode(true));
        const project = document.getElementById('wallProject'); project.innerHTML = '<option>Example Project</option>'; project.disabled = false;
        for (const id of ['wallErrors', 'wallWarnings', 'wallJobs']) {
          const node = document.getElementById(id); node.hidden = false;
          node.innerHTML = Array.from({ length: 8 }, (_, index) => `<p>Visible ${id} status ${index}</p>`).join('');
        }
        document.getElementById('wallEmpty').hidden = true;
        document.getElementById('wallBoard').innerHTML = '<section class="wallStage" style="width:100%"><div class="wallGroupHeader">Design</div><div class="wallStageBody"><article class="wallCard" style="left:0;top:0;width:100%;height:180px"><h3>Readable design card</h3><p class="wallCardPreview">Source text and status remain visible with warnings and running jobs.</p><footer class="wallCardFooter" style="margin-top:auto"><span class="wallCardSource">Example source</span><div class="wallActions"><button id="fixtureAction">Discard</button></div></footer></article></div></section>';
        window.clicks = 0; document.getElementById('fixtureAction').addEventListener('click', () => clicks++);
      });
      for (const zoom of ['cards', 'detail']) {
        await page.evaluate(zoom => { document.getElementById('wallBoard').dataset.zoom = zoom; }, zoom);
        const sizes = await page.evaluate(() => ({ board: document.getElementById('wallBoard').clientHeight,
          stage: document.querySelector('.wallStageBody').clientHeight,
          width: document.querySelector('.wallStageBody').clientWidth }));
        assert.ok(sizes.stage >= 180 && sizes.width >= 270, JSON.stringify({ viewport, zoom, sizes }));
        await page.locator('#fixtureAction').scrollIntoViewIfNeeded();
        // Put the movable launcher on the action's vertical coordinate, as in
        // the failed live TAU. It must never intercept the card action.
        await page.evaluate(() => {
          const button = document.getElementById('fixtureAction').getBoundingClientRect();
          document.querySelector('.portalAgentInbox').style.top = `${button.y - 22}px`;
        });
        const reachable = await page.locator('#fixtureAction').evaluate(button => {
          const rect = button.getBoundingClientRect();
          return rect.top >= 104 && rect.bottom <= innerHeight && button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
        });
        assert.equal(reachable, true, JSON.stringify({ viewport, zoom }));
        await page.locator('#fixtureAction').click({ timeout: 2000 });
      }
      assert.equal(await page.evaluate(() => clicks), 2);
      await page.locator('#wallProject').scrollIntoViewIfNeeded();
      assert.ok(await page.locator('#wallProject').isVisible(), 'toolbar remains reachable');
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
