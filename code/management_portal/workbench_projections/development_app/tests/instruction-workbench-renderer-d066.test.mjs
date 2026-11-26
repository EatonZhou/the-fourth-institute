import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

// Exercise the actual renderer without a server, browser or ISA execution.
const bundled = await build({
  entryPoints: [fileURLToPath(new URL('../src/architecture/instruction-workbench.js', import.meta.url))],
  bundle: true, format: 'esm', write: false, loader: {'.css': 'empty'},
});
const {createInstructionWorkbench} = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
const content = JSON.parse(readFileSync(new URL('./fixtures/instruction-catalog.json', import.meta.url), 'utf8'));

function mount(t) {
  const dom = new JSDOM('<main></main>');
  const originalAbortController = globalThis.AbortController;
  globalThis.AbortController = dom.window.AbortController;
  const root = dom.window.document.querySelector('main');
  const view = createInstructionWorkbench(root, {content});
  t.after(() => {view.destroy(); dom.window.close(); globalThis.AbortController = originalAbortController;});
  return {root, view};
}

test('ApplyLineageChanges wire lessons show activation, observation and target binding locally', t => {
  const {root, view} = mount(t);
  for (const [variant, source, target] of [['route', '事件源', 'Effect'], ['watch', '观察事件源', 'Resident'], ['target', 'Effect', 'Resident']]) {
    view.restoreViewState({instruction: 'ApplyLineageChanges', variant, step: 0});
    const before = root.querySelector('.iw-wire-diagram');
    assert.ok(before, `${variant} has a relation diagram`);
    assert.equal(before.dataset.revealed, 'false');
    assert.equal(before.querySelector('.iw-wire-edge').textContent, '尚未创建');
    root.querySelector('[data-role="reveal"]').click();
    const after = root.querySelector('.iw-wire-diagram');
    assert.equal(after.dataset.revealed, 'true');
    assert.deepEqual([...after.querySelectorAll('.iw-wire-node')].map(n => n.textContent), [source, target]);
    assert.equal(after.querySelector('.iw-wire-edge').textContent, variant);
    assert.match(root.querySelector('.iw-receipt-fields').textContent, /created_ids/);
    assert.match(root.textContent, /本地解释.*不执行指令/);
  }
});

test('an unauthorized batch shows unchanged structure rather than a created relation', t => {
  const {root, view} = mount(t);
  view.restoreViewState({instruction: 'ApplyLineageChanges', variant: 'atomic', step: 1});
  assert.equal(root.querySelector('.iw-wire-diagram'), null);
  assert.match(root.querySelector('.iw-object-group').textContent, /标题未改.*连线未删.*版本仍为 7/);
  assert.match(root.querySelector('.iw-receipt-fields').textContent, /instruction.forbidden/);
});
