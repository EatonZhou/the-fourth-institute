"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");
const { PORTAL_COMPONENTS_STYLES } = require("./portal_components_styles.cjs");

const repo = path.resolve(__dirname, "../../..");
const css = ["portal_design_system", ...PORTAL_COMPONENTS_STYLES, "portal_workbench"]
  .map(name => fs.readFileSync(path.join(repo, "management_portal/static", `${name}.css`), "utf8"))
  .join("\n");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setContent(`<!doctype html><html data-portal-viewport="mobile"><head><style>
      :root { --bg:#081017; --panel:#101a23; --panel-2:#14212b; --text:#e7edf3; --muted:#93a4b5;
        --border:#435363; --accent:#46a39d; --accent-2:#46a39d; --radius:8px; --shadow:none; }
      * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--text); }
      .fixtureButton { width:30px; height:30px; border:1px solid var(--border); background:transparent; }
      .portalWorkbenchToolbar { min-height:32px; }
      .portalWorkbenchPageLayout { height:calc(100dvh - 76px); padding:6px; }
      .portalActivityRail { position:relative; inset:auto; }
      ${css}
    </style></head><body class="portalBoardShell">
      <header class="portalSiteHeader"><div class="portalNavShell"><div class="portalNavTop">
        <div class="portalNavMain"><a class="portalBrand" data-portal-site-compact-name="TFI">The Fourth Institute</a><nav class="portalModuleNav">
          <a class="portalModuleLink">Resource</a><a class="portalModuleLink">Project</a><a class="portalModuleLink active">Society</a>
          <button class="portalNavOverflowToggle">更多</button></nav></div>
        <div class="portalWorkbenchChrome"><div class="portalWorkbenchToolbar">
          <button class="fixtureButton"></button><button class="fixtureButton"></button><button class="fixtureButton"></button><button class="fixtureButton"></button>
        </div></div>
        <div class="portalUser"><button class="fixtureButton"></button><button class="fixtureButton"></button><button class="fixtureButton"></button></div>
      </div></div></header>
      <main class="portalWorkbenchPageLayout"><aside class="portalActivityRail">
        <button class="portalActivityRailView active"></button><button class="portalActivityRailView"></button>
        <button class="portalActivityRailView"></button><button class="portalActivityRailView"></button>
      </aside><section class="portalWorkbenchViewportRoot">Readable mobile work area</section></main>
      <aside class="portalAgentInbox" data-has-assistant="true" data-expanded="true" data-mobile-overlay="true" data-active-panel="mailbox">
        <button class="portalAgentInboxTrigger"></button><section class="portalAgentInboxPanel" data-inbox-panel="mailbox"><header><strong>Mailbox</strong></header><div class="portalAgentInboxFilters"><button aria-pressed="true">消息</button><button>Todo</button></div><div class="portalAgentInboxList">${Array.from({length: 30}, (_, index) => `<button class="portalAgentInboxItem">Message ${index + 1}</button>`).join("")}</div></section>
        <button class="portalAgentInboxTrigger portalPersonalAssistantTrigger"></button>
        <button class="portalAgentInboxMobileClose" aria-label="关闭 Mailbox"><span>关闭</span></button>
      </aside>
    </body></html>`);

    await page.evaluate(async () => {
      await Promise.all(document.querySelector('.portalAgentInboxPanel').getAnimations().map(animation => animation.finished));
    });
    const geometry = await page.evaluate(() => {
      const rect = selector => {
        const value = document.querySelector(selector).getBoundingClientRect();
        return { x:value.x, y:value.y, width:value.width, height:value.height, right:value.right, bottom:value.bottom };
      };
      const list = document.querySelector(".portalAgentInboxList");
      list.scrollTop = 120;
      const triggerStyle = getComputedStyle(document.querySelector(".portalAgentInboxTrigger"));
      const closeStyle = getComputedStyle(document.querySelector(".portalAgentInboxMobileClose"));
      return { header:rect(".portalSiteHeader"), main:rect(".portalNavMain"), user:rect(".portalUser"),
        chrome:rect(".portalWorkbenchChrome"), rail:rect(".portalActivityRail"), panel:rect(".portalAgentInboxPanel"),
        close:rect(".portalAgentInboxMobileClose"), triggerVisibility:triggerStyle.visibility,
        closeDisplay:closeStyle.display, list:{clientHeight:list.clientHeight, scrollHeight:list.scrollHeight, scrollTop:list.scrollTop} };
    });
    assert.ok(geometry.header.height <= 82, JSON.stringify(geometry));
    assert.ok(Math.abs(geometry.main.y - geometry.user.y) <= 2, JSON.stringify(geometry));
    assert.ok(geometry.chrome.y >= geometry.main.bottom - 1 && geometry.chrome.height <= 38, JSON.stringify(geometry));
    assert.equal(Math.round(geometry.rail.height), 44);
    assert.ok(geometry.panel.width >= 360 && geometry.panel.x >= 7 && geometry.panel.right <= 383, JSON.stringify(geometry));
    assert.ok(geometry.panel.y <= 9 && geometry.panel.bottom >= 835, JSON.stringify(geometry));
    assert.equal(geometry.triggerVisibility, "hidden");
    assert.notEqual(geometry.closeDisplay, "none");
    assert.ok(geometry.close.width >= 40 && geometry.close.height >= 40, JSON.stringify(geometry));
    assert.ok(geometry.list.scrollHeight > geometry.list.clientHeight && geometry.list.scrollTop > 0, JSON.stringify(geometry));
    assert.deepEqual(errors, []);

    const output = path.join(repo, "test-results", "portal-mobile-shell.png");
    fs.mkdirSync(path.dirname(output), { recursive: true });
    await page.screenshot({ path: output, fullPage: true });
    console.log(JSON.stringify({ status: "passed", geometry, screenshot: output }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
