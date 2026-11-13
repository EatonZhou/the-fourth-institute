const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '../../../management_portal');
const groups = JSON.parse(fs.readFileSync(path.join(root, 'script_groups.json'), 'utf8'));
const errors = [];
const console = new VirtualConsole();
console.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM('<header></header>', {url:'https://shell.fixture.invalid',
  runScripts:'outside-only', pretendToBeVisual:true, virtualConsole:console});
(async () => {
  try {
    await new Promise(resolve => dom.window.addEventListener('load', resolve, {once:true}));
    assert.equal(dom.window.document.readyState, 'complete');
    dom.window.matchMedia = media => Object.assign(new dom.window.EventTarget(), {matches:false, media});
    const overlay = groups['portal-shell'].indexOf('/static/portal_overlay.js');
    for (const src of [...groups['portal-foundation'], ...groups['portal-shell'].slice(0, overlay+1)]) {
      dom.window.eval(fs.readFileSync(path.join(root, src.slice(1)), 'utf8'));
    }
    assert.deepEqual(errors, []);
    assert.ok(dom.window.document.querySelector('[data-portal-theme-toggle]'), 'theme toggle mounts on the ready page');
  } finally { dom.window.close(); }
})().catch(error => { process.stderr.write(String(error.stack)); process.exitCode=1; });
