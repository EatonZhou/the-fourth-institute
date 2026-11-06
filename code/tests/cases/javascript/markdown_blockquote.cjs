const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const fence = String.fromCharCode(96).repeat(3);
// Same container format as resident_protocol.forward_content(quoted=True).
// Fictional authors/content: no real messages or user preferences are used.
const source = [
  '转发消息', '',
  '> 访客: 请比较以下方案。',
  '> 助手: **对比结果**',
  '> ',
  '> | 方案 | 支持内容 | 说明 |',
  '> | --- | --- | --- |',
  '> | A | 音频、文字 | [查看说明](https://example.com/docs) |',
  '> | B | 长文件 | 支持较长内容，详情以服务说明为准 |',
  '> ',
  '> - 保留原始内容',
  '> - 核对来源',
  '> ',
  '> ' + fence + 'js',
  '> const text = "作者: 这里只是代码";',
  '> console.log(text);',
  '> ' + fence,
  '> ',
  '> > 原始引用中的 **重点**',
  '',
  '我的附言：请优先核对 **方案 A**，这是本次消息的正文。',
].join('\n');

function contract() {
  const dom = new JSDOM('<!doctype html><main></main>', { runScripts: 'outside-only' });
  const { window } = dom;
  try {
    for (const name of ['portal_text.js', 'portal_ui_primitives.js', 'markdown.js']) {
      window.eval(fs.readFileSync(path.join(staticRoot, name), 'utf8'));
    }
    const main = window.document.querySelector('main');
    main.innerHTML = window.MD.render(source);
    const quote = main.querySelector(':scope > blockquote');
    assert.ok(quote, 'forwarded content keeps a real quote boundary');
    assert.equal(quote.querySelectorAll('thead th').length, 3);
    assert.equal(quote.querySelectorAll('tbody tr').length, 2);
    assert.equal(quote.querySelectorAll('ul > li').length, 2);
    assert.equal(quote.querySelector('pre code').textContent,
      'const text = "作者: 这里只是代码";\nconsole.log(text);');
    assert.equal(quote.querySelector('a').getAttribute('href'), 'https://example.com/docs');
    assert.equal(quote.querySelector(':scope > blockquote strong').textContent, '重点');
    assert.ok(quote.textContent.includes('访客:'));
    assert.ok(quote.textContent.includes('助手:'));
    assert.equal(main.querySelector(':scope > p:last-child > strong').textContent, '方案 A');
    assert.equal(quote.textContent.includes('我的附言'), false, 'note stays outside the forwarded quote');
    assert.equal(main.querySelectorAll(':scope > blockquote').length, 1,
      'author-like strings do not invent new source boundaries');

    main.innerHTML = window.MD.render('正文\n\n> 第一条\n\n> 第二条\n\n结尾');
    assert.equal(main.querySelectorAll(':scope > blockquote').length, 2);
    assert.equal(main.querySelector(':scope > p:last-child').textContent, '结尾');

    main.innerHTML = window.MD.render('> <script>alert(1)</script>\n> [unsafe](javascript:alert(1))\n> <img src=x onerror=alert(1)>');
    assert.equal(main.querySelectorAll('script, img, [onerror], a[href^="javascript:"]').length, 0);
    assert.ok(main.textContent.includes('<script>'));

    main.innerHTML = window.MD.render(fence + '\n> literal quote\n' + fence);
    assert.equal(main.querySelectorAll('blockquote').length, 0);
    assert.equal(main.querySelector('pre code').textContent, '> literal quote');

    const fences = [];
    main.innerHTML = window.MD.render('> ' + fence + 'test\n> quoted\n> ' + fence + '\n\n' + fence + 'test\nbody\n' + fence, {
      renderCodeFence(block) {
        fences.push(block);
        return '<pre class="customFence">custom</pre>';
      },
    });
    assert.equal(fences.length, 1, 'quoted examples must not become message-level actions');
    assert.equal(main.querySelector('blockquote pre code').textContent, 'quoted');
    assert.ok(main.querySelector(':scope > .customFence'));
    console.log('contract passed: forwarded_markdown_blocks');
  } finally {
    window.close();
  }
}

module.exports = { source };
if (require.main === module) contract();
