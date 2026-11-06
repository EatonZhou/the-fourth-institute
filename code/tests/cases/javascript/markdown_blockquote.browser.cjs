// Production Markdown and theme/component CSS, with synthetic forward content
// in the shared chat bubble markup. No server, messages or preferences mutate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { PORTAL_COMPONENTS_STYLES } = require('./portal_components_styles.cjs');
const { source } = require('./markdown_blockquote.cjs');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const output = process.env.FORWARD_LAYOUT_EVIDENCE;
const changed = new Set(['markdown.js', 'portal_components_markdown.css', 'portal_themes.css']);
const read = (name, baseline) => baseline && changed.has(name)
  ? execFileSync('git', ['show', 'HEAD:code/management_portal/static/' + name], { cwd: staticRoot, encoding: 'utf8' })
  : fs.readFileSync(path.join(staticRoot, name), 'utf8');

async function main() {
  if (output) fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    const cases = [
      { name: 'light-wide', theme: 'light', width: 1200 },
      { name: 'light-narrow', theme: 'light', width: 390 },
      { name: 'dark-wide', theme: 'dark', width: 1200 },
      { name: 'dark-narrow', theme: 'dark', width: 390 },
      { name: 'filled-wide', theme: 'auto', width: 1200 },
      { name: 'filled-narrow', theme: 'auto', width: 390 },
      { name: 'incoming-narrow', theme: 'auto', width: 390, incoming: true },
    ];
    if (process.env.FORWARD_LAYOUT_BASELINE) cases.unshift({ name: 'before-filled', theme: 'auto', width: 1200, baseline: true });
    for (const item of cases) {
      const page = await browser.newPage({ viewport: { width: item.width, height: 950 }, colorScheme: 'light' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setContent('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><main><article class="messageItem human"><div class="messageHead"><span class="messageTitle">转发者 · 测试会话</span></div><div class="messageBubble"><div class="messageContent markdownBody"></div></div></article></main></body></html>');
      for (const style of ['portal_design_system', ...PORTAL_COMPONENTS_STYLES, 'portal_themes']) {
        await page.addStyleTag({ content: read(style + '.css', item.baseline) });
      }
      await page.addStyleTag({ content: 'body{margin:0;padding:20px;box-sizing:border-box;background:var(--panel);color:var(--text)}main{display:flex;flex-direction:column;max-width:900px;margin:auto}.messageItem{width:100%}html{--portal-auto-bubble-strength:100%;--portal-auto-bubble-text-light:#fff;--portal-auto-bubble-link-light:#fff}' });
      for (const script of ['portal_text.js', 'portal_ui_primitives.js', 'markdown.js']) {
        await page.addScriptTag({ content: read(script, item.baseline) });
      }
      await page.evaluate(({ item, source }) => {
        document.documentElement.dataset.portalTheme = item.theme;
        document.querySelector('.messageItem').classList.toggle('messageIncoming', !!item.incoming);
        document.querySelector('.messageContent').innerHTML = window.MD.render(source);
      }, { item, source });
      const metrics = await page.evaluate(() => {
        const quote = document.querySelector('.messageContent > blockquote');
        const content = document.querySelector('.messageContent');
        const style = getComputedStyle(quote);
        const bubbleStyle = getComputedStyle(document.querySelector('.messageBubble'));
        const canvas = document.createElement('canvas').getContext('2d');
        function rgba(value) {
          canvas.clearRect(0, 0, 1, 1);
          canvas.fillStyle = value;
          canvas.fillRect(0, 0, 1, 1);
          return [...canvas.getImageData(0, 0, 1, 1).data];
        }
        const back = rgba(style.backgroundColor);
        const front = rgba(style.color);
        const rgb = front.slice(0, 3).map((c, i) => (c * front[3] + back[i] * (255 - front[3])) / 255);
        const luminance = color => color.slice(0, 3).map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4)
          .reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
        const a = luminance(rgb), b = luminance(back);
        const table = quote.querySelector('.markdownTableScroll');
        return {
          tableCount: quote.querySelectorAll('table').length,
          codeCount: quote.querySelectorAll('pre code').length,
          contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05),
          quoteBackground: style.backgroundColor,
          bubbleBackground: bubbleStyle.backgroundColor,
          borderWidth: parseFloat(style.borderLeftWidth),
          pageOverflow: document.documentElement.scrollWidth - innerWidth,
          contentOverflow: content.scrollWidth - content.clientWidth,
          tableScroll: table ? getComputedStyle(table).overflowX : null,
          noteOutside: content.lastElementChild.tagName === 'P' && content.lastElementChild.textContent.startsWith('我的附言'),
        };
      });
      if (!item.baseline) {
        assert.equal(metrics.tableCount, 1, item.name);
        assert.equal(metrics.codeCount, 1, item.name);
        assert.ok(metrics.noteOutside, item.name + ': current body remains outside reference');
        assert.ok(metrics.pageOverflow <= 1 && metrics.contentOverflow <= 1, item.name + ': local scrolling, not page overflow');
        assert.equal(metrics.tableScroll, 'auto');
        assert.ok(metrics.borderWidth >= 3, item.name + ': visible source boundary');
        assert.notEqual(metrics.quoteBackground, metrics.bubbleBackground, item.name + ': distinct quote surface');
        assert.ok(metrics.contrast >= 4.5, item.name + ': readable quote ' + metrics.contrast);
        assert.deepEqual(errors, []);
      } else {
        assert.equal(metrics.tableCount, 0, 'baseline reproduces literal table pipes');
      }
      if (output) await page.screenshot({ path: path.join(output, item.name + '.png'), fullPage: true });
      results.push({ name: item.name, ...metrics });
      await page.close();
    }
    if (output) fs.writeFileSync(path.join(output, 'metrics.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
