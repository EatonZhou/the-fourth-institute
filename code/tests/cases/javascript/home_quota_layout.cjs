// Exercise the production quota renderer and styles in a real layout engine.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {babelParse} = require('./babel_parse.cjs');
const root = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const source = fs.readFileSync(path.join(root, 'management_portal/static/home.js'), 'utf8');
const functions = ['executionAccounts', 'accountQuotaWindowIsStale', 'accountQuotaWindows',
  'accountSummaryQuotaWindows', 'refreshAccountQuotaWindows', 'accountQuotaWindow', 'accountLimitRow'];
const renderer = babelParse(source, 'home.js').program.body.filter(n => n.type === 'FunctionDeclaration' && functions.includes(n.id.name))
  .map(n => source.slice(n.start, n.end)).join('\n');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.PORTAL_TEST_CHROMIUM || undefined, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent('<main data-home-component="agent-accounts"><div class="agent-status-limit-list"></div></main><aside class="account-details"><div class="agent-status-account-rate-windows"></div></aside>');
    await page.addStyleTag({content: 'body { margin:16px; font:14px Arial,sans-serif; --muted:#687584; --border:#ccd6de; --chip:#f1f4f6; } aside { width:min(356px,100%); margin-top:32px; }'});
    await page.addStyleTag({path: path.join(root, 'management_portal/static/home_workbench.css')});
    await page.addStyleTag({path: path.join(root, 'management_portal/static/home.css')});
    await page.addScriptTag({content: `
      const tag = (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '');
      window.PortalI18n = {t: x => x, html: tag, text: tag};
      const esc = x => String(x ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
      const formatDateTime = x => x ? new Date(x).toISOString() : '';
      const canSetScheduler = false;
      ${renderer}
      const labels = ['Gemini Models · Weekly Limit Remaining', 'Gemini Models · Five Hour Limit Remaining',
        'Claude and GPT models · Weekly Limit Remaining', 'Claude and GPT models · Five Hour Limit Remaining',
        'Gemini 4 Argon · Gemini 4 Argon Weekly Limit'];
      const account = {account_id:'antigravity_one', provider:'antigravity', rate_limits: labels.map((label, i) => ({id:label.replace(' · ', '/'),label,
        remaining_percent: i === 3 ? null : 100, stale:i === 2, observed_at:Date.now()/1000, resets_at:Date.now()/1000+3600}))};
      const agentStatusData = {accounts: [account,
        {account_id:'codex_one', rate_limits:[{id:'weekly',label:'Weekly',remaining_percent:3}]},
        {account_id:'claude_one', rate_limits:[{id:'five_hour',label:'5 小时',remaining_percent:72},{id:'weekly',label:'每周',remaining_percent:64}]}]};
      document.querySelector('main > div').innerHTML = agentStatusData.accounts.map(accountLimitRow).join('');
      document.querySelector('aside > div').innerHTML = account.rate_limits.map(w => accountQuotaWindow(account,w)).join('');
    `});
    const home = page.locator('[data-account-limit-account="antigravity_one"]');
    assert.deepEqual(await home.locator('.agent-status-window-name').allTextContents(), ['5 小时', '每周']);
    assert.equal(await home.locator('[data-account-quota-window]').count(), 2);
    assert.equal(await page.locator('aside [data-account-quota-window]').count(), 5);
    assert.deepEqual(await page.locator('[data-account-limit-account="claude_one"] .agent-status-window-name').allTextContents(), ['5 小时', '每周']);
    assert.deepEqual(await page.locator('[data-account-limit-account="codex_one"] .agent-status-window-name').allTextContents(), ['Weekly']);
    assert.match(await home.locator('[data-account-quota-window]').first().getAttribute('title'), /Gemini Models · Five Hour Limit Remaining/);
    // Polling updates both views without restoring long labels or hidden windows on Home.
    await page.evaluate(() => {
      account.rate_limits[0].stale = true;
      account.rate_limits[1].remaining_percent = null;
      refreshAccountQuotaWindows();
    });
    assert.deepEqual(await home.locator('.agent-status-window-name').allTextContents(), ['5 小时', '每周']);
    assert.deepEqual(await home.locator('.agent-status-limit-meta').allTextContents(), ['—', '100%*']);
    assert.equal(await page.locator('aside [data-account-quota-window]').count(), 5);
    assert.deepEqual(await page.locator('aside .agent-status-window-name').allTextContents(), await page.evaluate(() => labels));
    for (const width of [1920, 1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height:1100});
      const problems = await page.evaluate(() => {
        const problems = [];
        const box = el => el.getBoundingClientRect();
        const overlaps = (a,b) => a.left < b.right-1 && a.right > b.left+1 && a.top < b.bottom-1 && a.bottom > b.top+1;
        if (document.documentElement.scrollWidth > innerWidth) problems.push('horizontal page overflow');
        for (const group of document.querySelectorAll('.agent-status-account-rate-windows')) {
          const windows = [...group.querySelectorAll('.agent-status-account-window')];
          for (let i=0;i<windows.length;i++) {
            const row=windows[i], bounds=box(row), nodes=[...row.children];
            if (i && group.closest('aside') && box(windows[i-1]).bottom > bounds.top+1) problems.push('detail quotas share a line');
            for (let k=i+1;k<windows.length;k++) if(overlaps(bounds,box(windows[k]))) problems.push('quota windows overlap');
            if (group.closest('main')) {
              if (nodes.some(node => Math.abs(box(node).top + box(node).height/2 - (bounds.top + bounds.height/2)) > 1))
                problems.push('Home label, bar and percentage are not on one row');
              if (i && box(group).width >= 370 && Math.abs(box(windows[0]).top - bounds.top) > 1)
                problems.push('Home quota windows are not side by side');
            }
            for (let j=0;j<nodes.length;j++) {
              const b=box(nodes[j]);
              if (b.left < bounds.left-1 || b.right > bounds.right+1 || nodes[j].scrollWidth > nodes[j].clientWidth+1)
                problems.push(`quota content overflows: ${nodes[j].className} ${nodes[j].textContent} (${nodes[j].clientWidth}/${nodes[j].scrollWidth})`);
              for(let k=j+1;k<nodes.length;k++) if(overlaps(b,box(nodes[k]))) problems.push('label, bar or percentage overlap');
            }
          }
        }
        for(const row of document.querySelectorAll('.agent-status-account-limit')) {
          const quotas=box(row.querySelector('.agent-status-account-rate-windows'));
          for(const selector of ['.agent-status-account-name','.agent-status-account-details','.agent-status-account-refresh'])
            if(overlaps(quotas,box(row.querySelector(selector)))) problems.push('quota overlaps account controls');
        }
        return problems;
      });
      assert.deepEqual(problems, [], `quota layout at ${width}px`);
      if (process.env.PORTAL_TEST_SCREENSHOTS && [1440,390].includes(width)) {
        fs.mkdirSync(process.env.PORTAL_TEST_SCREENSHOTS, {recursive:true});
        await page.screenshot({path:path.join(process.env.PORTAL_TEST_SCREENSHOTS, `quota-${width}.png`),fullPage:true});
      }
    }
    console.log('Home shows only Gemini 5-hour/weekly side by side; all five windows remain in details, including after refresh; layout fits six widths');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
