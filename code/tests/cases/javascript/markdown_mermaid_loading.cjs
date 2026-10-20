// Full production Markdown and real DOM; the dynamic-import boundary records
// module attempts and leaves loading pending. It provides no renderer output.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const flowchart = `flowchart LR
  A[Resident 信息和已完成对话] --> B[后台更新摘要和向量索引]
  C[用户描述任务] --> D[检索候选并检查可见权限]
  B --> D
  D --> E[专用推荐 Resident 排序]
  E --> F[校验结果并显示推荐卡片]
  F --> G[用户选择 Open 或 Fork]`;

async function loadingContract(transform = source => source) {
  const dom = new JSDOM('<!doctype html><html><head></head><body><section id="chat"></section></body></html>', {
    url: 'http://markdown-mermaid.invalid', runScripts: 'outside-only',
  });
  const { window } = dom;
  const imports = [];
  // A transport module load that has not completed. The test never supplies
  // observe(), SVG, a ready state or another renderer implementation.
  const modulePending = new Promise(() => {});
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
    const filename = path.join(staticRoot, 'markdown.js');
    new vm.Script(transform(fs.readFileSync(filename, 'utf8')), {
      filename,
      importModuleDynamically(specifier) {
        imports.push(specifier);
        return modulePending;
      },
    }).runInContext(dom.getInternalVMContext());
    const chat = window.document.getElementById('chat');
    const incomplete = ['```text', flowchart, '```', '', '```mermaid', flowchart].join('\n');
    chat.innerHTML = window.MD.render(incomplete);
    await new Promise(resolve => setImmediate(resolve));
    const incompleteHtml = chat.innerHTML;
    const incompleteImports = [...imports];
    assert.equal(chat.querySelectorAll('pre code').length, 2, 'plain and incomplete fences both remain code');
    assert.equal(window.document.querySelectorAll('[data-portal-mermaid]').length, 0,
      'incomplete fences do not create a diagram');
    assert.deepEqual(imports, [], 'plain and incomplete fences do not request the diagram module');
    assert.deepEqual([...chat.querySelectorAll('pre code')].map(node => node.textContent), [flowchart, flowchart],
      'unrendered source stays intact');

    chat.innerHTML = window.MD.render('```mermaid\n' + flowchart + '\n```');
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(imports, ['/static/portal_mermaid.mjs'], 'a complete fence requests the actual module exactly once');
    const figures = chat.querySelectorAll('figure[data-portal-mermaid]');
    assert.equal(figures.length, 1, 'complete fence creates one renderer placeholder');
    assert.equal(figures[0].dataset.mermaidState, 'pending', 'pending module load cannot claim rendered output');
    assert.equal(figures[0].querySelector('[data-portal-mermaid-source]').textContent, flowchart);
    assert.equal(figures[0].querySelector('svg'), null, 'loading contract does not fabricate SVG');
    if (process.env.MARKDOWN_MERMAID_LOADING_EVIDENCE) {
      fs.mkdirSync(process.env.MARKDOWN_MERMAID_LOADING_EVIDENCE, { recursive: true });
      const evidence = process.env.MARKDOWN_MERMAID_LOADING_EVIDENCE;
      fs.writeFileSync(path.join(evidence, 'incomplete.html'), incompleteHtml);
      fs.writeFileSync(path.join(evidence, 'complete-pending.html'), chat.innerHTML);
      fs.writeFileSync(path.join(evidence, 'module-attempts.json'), JSON.stringify({ incomplete: incompleteImports, complete: imports }, null, 2) + '\n');
    }
  } finally {
    await new Promise(resolve => setImmediate(resolve));
    for (const observer of observers) observer.disconnect();
    await new Promise(resolve => setImmediate(resolve));
    window.close();
  }
}

module.exports = { loadingContract };
if (require.main === module) {
  loadingContract().then(() => console.log('contract passed: complete_mermaid_loading')).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
