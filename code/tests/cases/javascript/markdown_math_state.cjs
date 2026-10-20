// Full Markdown and Primitives modules hydrate real repository KaTeX output
// into a DOM. Only the module URL maps from the browser route to its local ESM.
// Monaco coexistence, font loading and visual rendering stay browser tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const { JSDOM } = require('jsdom');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const markdownPath = path.join(staticRoot, 'markdown.js');
const mathSource = [
  String.raw`Inline \(\frac{a_0}{\sqrt{b}}\) and $\sum_{i=1}^{n} i^2$.`,
  '',
  '```latex', String.raw`\begin{aligned}a&=b+c\\d&=e\end{aligned}`, '```',
  '',
  String.raw`Literal: \`$x^2$\` and \`\(x\)\`.`,
  '',
  '```js', String.raw`const source = "$x^2$";`, '```',
  '',
  String.raw`\(\href{javascript:alert(1)}{unsafe}\)`,
  '',
  String.raw`\(\frac{\)`,
].join('\n').replaceAll('\\`', '`');

async function mathContract(transform = source => source) {
  const dom = new JSDOM('<!doctype html><html><head></head><body><section id="chat"></section></body></html>', {
    url: 'http://markdown-math.invalid', runScripts: 'outside-only',
  });
  const { window } = dom;
  const observers = [];
  const NativeMutationObserver = window.MutationObserver;
  window.MutationObserver = class extends NativeMutationObserver {
    constructor(callback) {
      super(callback);
      observers.push(this);
    }
  };
  try {
    for (const filename of ['portal_text.js', 'portal_ui_primitives.js']) {
      window.eval(fs.readFileSync(path.join(staticRoot, filename), 'utf8'));
    }
    const browserUrl = '"/static/vendor/katex/katex.mjs"';
    const localUrl = JSON.stringify(pathToFileURL(path.join(staticRoot, 'vendor/katex/katex.mjs')).href);
    const source = transform(fs.readFileSync(markdownPath, 'utf8'));
    assert.equal(source.split(browserUrl).length, 2, 'one KaTeX import URL boundary');
    new vm.Script(source.replace(browserUrl, localUrl), {
      filename: markdownPath,
      importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER,
    }).runInContext(dom.getInternalVMContext());
    const chat = window.document.getElementById('chat');
    chat.innerHTML = window.MD.render(mathSource);
    const engine = await window.MD.ensureKatex();
    assert.equal(typeof engine?.renderToString, 'function', 'actual repository KaTeX module must load');
    await new Promise(resolve => setImmediate(resolve));
    if (process.env.MARKDOWN_MATH_STATE_EVIDENCE) {
      fs.mkdirSync(process.env.MARKDOWN_MATH_STATE_EVIDENCE, { recursive: true });
      fs.writeFileSync(path.join(process.env.MARKDOWN_MATH_STATE_EVIDENCE, 'rendered.html'), chat.innerHTML);
      fs.writeFileSync(path.join(process.env.MARKDOWN_MATH_STATE_EVIDENCE, 'input.md'), mathSource);
    }
    assert.equal(chat.querySelectorAll('.katex').length, 4, 'inline and fenced math render through KaTeX');
    assert.equal(window.document.querySelectorAll('code .katex').length, 0, 'code content must not render as math');
    assert.deepEqual([...window.document.querySelectorAll('code')].map(node => node.textContent),
      ['$x^2$', String.raw`\(x\)`, 'const source = "$x^2$";'], 'inline and fenced code remain literal');
    assert.equal(chat.querySelectorAll('a[href^="javascript:"], script').length, 0,
      'untrusted KaTeX commands cannot create executable markup');
    assert.ok(window.document.querySelector('.katex-error')?.textContent.includes(String.raw`\frac{`),
      'invalid formula remains visible as a KaTeX error');
    assert.equal(chat.querySelectorAll('[data-portal-math-source]').length, 0, 'loaded renderer hydrates every pending math node');
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve));
    window.close();
  }
}

module.exports = { mathContract };
if (require.main === module) {
  mathContract().then(() => console.log('contract passed: inline_fenced_math')).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
