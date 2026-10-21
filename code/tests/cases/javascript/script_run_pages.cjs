'use strict';

// Exercise the actual renderer with DOM, ISA and clock ports. JSDOM does not
// enforce browser sandbox/CSP isolation; the Python owner tests check headers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { babelParse, traverse } = require('./babel_parse.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const waitFor = async predicate => {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail('DOM/ISA continuation did not settle');
};

async function main() {
  const dom = new JSDOM('<main id="root"></main>', {
    url: 'http://script-runs.invalid/', runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  const w = dom.window;
  const root = w.document.querySelector('#root');
  const calls = [];
  const timers = new Map();
  let nextTimer = 0;
  let finished = false;
  const artifactName = 'report & notes.html';
  let view;
  try {
    w.TextDecoder = TextDecoder;
    w.setTimeout = (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, { callback, delay });
      return id;
    };
    w.clearTimeout = id => timers.delete(id);
    w.PortalInstructionClient = { execute: async (name, args, options) => {
      assert.equal(args.lineage_id, 'lineage_test');
      calls.push({ name, args, signal: options.signal });
      if (name === 'GetTriggerPath') {
        assert.equal(args.trigger_path_id, 'path_test');
        assert.equal(args.limit, 50);
        const ids = args.offset === 50
          ? ['failed', 'timed_out', 'cancelled', 'lost'] : ['script_one'];
        return { path: { status: 'completed' }, steps: {
          items: [{ kind: 'effect', result: { calls: Object.fromEntries(
            ids.map(id => [id, { business_ids: { script_run_id: id } }]),
          ) } }], next_offset: args.offset === 50 ? null : 50,
        } };
      }
      if (name === 'GetScriptRun') return {
        status: args.script_run_id === 'script_one' ? (finished ? 'succeeded' : 'running') : args.script_run_id,
        path: '/scripts/test.py', duration_seconds: 3,
        outputs: { text: '<img src=x onerror="window.injected=1">' },
        artifacts: [{ name: artifactName, mime_type: 'text/html', size: 10 }],
      };
      if (name === 'ReadScriptRunOutput') {
        assert.equal(args.script_run_id, 'script_one');
        assert.equal(args.limit, 16384);
        const bytes = Buffer.from(args.stream === 'stdout' ? '甲<script>inert</script>' : '错误');
        const end = args.offset === 0 ? 1 : bytes.length;
        return { base64: bytes.subarray(args.offset, end).toString('base64'),
          offset: args.offset, next_offset: end, size: bytes.length,
          eof: end === bytes.length, truncated: args.stream === 'stderr' };
      }
      throw new Error(`Unexpected ISA read: ${name}`);
    } };
    // Only escapeHtml is a renderer dependency. Extract that production helper
    // rather than starting the unrelated document-wide icon enhancer owner.
    const primitives = fs.readFileSync(path.join(staticRoot, 'portal_ui_primitives.js'), 'utf8');
    let escapeSource;
    traverse(babelParse(primitives, 'portal_ui_primitives.js'), { FunctionDeclaration(p) {
      if (p.node.id.name === 'escapeHtml') escapeSource = primitives.slice(p.node.start, p.node.end);
    } });
    assert.ok(escapeSource);
    w.eval(`window.PortalUIPrimitives = {escapeHtml: (${escapeSource})};`);
    for (const name of ['portal_text.js', 'portal_script_runs.js']) {
      w.eval(fs.readFileSync(path.join(staticRoot, name), 'utf8'));
    }
    view = w.PortalScriptRuns.mount(root, {
      lineageId: 'lineage_test', triggerPathId: 'path_test',
      response: { path: { status: 'completed' }, steps: {
        items: [{ kind: 'effect', result: { calls: {
          first: { business_ids: { script_run_id: 'script_one' } },
        } } }], next_offset: 50,
      } },
    });
    const row = root.querySelector('[data-script-run="script_one"]');
    await waitFor(() => row.dataset.scriptStatus === 'running' && timers.size === 1);
    assert.match(row.querySelector('summary').textContent, /运行中 · 3.0 秒/);
    row.open = true;
    row.dispatchEvent(new w.Event('toggle'));
    await waitFor(() => row.querySelector('[data-script-note="stdout"]').textContent.includes('已读取 1 /'));
    // The first byte is only part of a UTF-8 character; no replacement glyph.
    assert.equal(row.querySelector('[data-script-log="stdout"]').textContent, '');
    row.querySelector('[data-script-read="stdout"]').click();
    await waitFor(() => row.querySelector('[data-script-log="stdout"]').textContent === '甲<script>inert</script>');
    assert.equal(row.querySelectorAll('[data-script-log] script').length, 0);
    assert.match(row.querySelector('[data-script-note="stderr"]').textContent, /已截断/);
    assert.equal(row.querySelectorAll('[data-script-outputs] img').length, 0);
    assert.equal(w.injected, undefined);
    assert.deepEqual(calls.filter(c => c.name === 'ReadScriptRunOutput' && c.args.stream === 'stdout').map(c => c.args.offset), [0, 1]);

    finished = true;
    const [timerId, timer] = [...timers][0];
    assert.equal(timer.delay, 5000);
    timers.delete(timerId);
    timer.callback();
    await waitFor(() => row.dataset.scriptStatus === 'succeeded' && !root.querySelector('[data-script-more]').disabled);
    assert.match(row.querySelector('summary').textContent, /成功/);
    root.querySelector('[data-script-more]').click();
    await waitFor(() => root.querySelector('[data-script-run="lost"]')?.dataset.scriptStatus === 'lost');
    for (const [status, label] of Object.entries({ failed: '失败', timed_out: '超时', cancelled: '已取消', lost: '运行丢失' })) {
      assert.ok(root.querySelector(`[data-script-run="${status}"] summary`).textContent.includes(label));
    }
    assert.equal(root.querySelector('[data-script-more]').hidden, true);
    await waitFor(() => !root.querySelector('[data-script-more]').disabled);
    assert.equal(timers.size, 0, 'terminal runs stop polling');
    assert.ok(calls.some(c => c.name === 'GetTriggerPath' && c.args.offset === 50));

    row.querySelector('[data-script-preview]').click();
    const frame = row.querySelector('iframe');
    assert.equal(frame.getAttribute('sandbox'), 'allow-scripts');
    assert.equal(frame.getAttribute('referrerpolicy'), 'no-referrer');
    assert.equal(frame.title, artifactName);
    for (const [element, download] of [[frame, false], [row.querySelector('[download]'), true]]) {
      const url = new URL(element.src || element.href);
      assert.equal(url.pathname, '/api/script-runs/artifact');
      assert.equal(url.searchParams.get('lineage_id'), 'lineage_test');
      assert.equal(url.searchParams.get('script_run_id'), 'script_one');
      assert.equal(url.searchParams.get('name'), artifactName);
      assert.equal(url.searchParams.get('download'), download ? '1' : null);
    }
    view.destroy();
    assert.equal(timers.size, 0);
    assert.ok(calls.every(c => c.signal.aborted), 'destroy aborts pending ISA reads');
    console.log(JSON.stringify({ browser_launched: false, native_sandbox_enforcement_tested: false,
      utf8_pages: true, text_only_output: true, terminal_polling_stops: true,
      call_pagination: true, preview_policy: true, dispose: true }));
  } finally {
    view?.destroy();
    dom.window.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
