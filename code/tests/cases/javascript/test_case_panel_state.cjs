'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHarness} = require('./workbench_visibility_state.cjs');

test('nested implementation prompts retain their own expansion when reselecting a case', async () => {
  const h = createHarness();
  const {window, root} = h;
  for (const name of ['test_workbench_core.js', 'test_case_panel.js']) {
    window.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/test_framework/page', name), 'utf8'));
  }
  const T = window.PortalTest;
  T.store.cases = ['a', 'b'].map(id => ({id, key: id, title: id, description: 'Fixture',
    module: 'operations.test-workbench', importance: 'normal', implementations: [
      {id: 'tau', runner: 'tau', entrypoint: 'fixture', source: 'fixture', task_prompt: 'Use the UI'}]}));
  T.store.selectedId = 'a';
  const controller = T.panelDefinitions.get('test-case').create({root});
  const prompt = () => root.querySelector('.testImplementationPrompt');
  try {
    assert.equal(prompt().open, false);
    prompt().open = true;
    prompt().dispatchEvent(new window.Event('toggle'));
    controller.setCase('a');
    assert.equal(prompt().open, true, 'same case render retains expanded prompt');
    controller.setCase('b');
    assert.equal(prompt().open, false, 'another case has independent state');
    controller.setCase('a');
    assert.equal(prompt().open, true, 'returning to a case restores its prompt');
    prompt().open = false;
    prompt().dispatchEvent(new window.Event('toggle'));
    controller.setCase('a');
    assert.equal(prompt().open, false, 'explicit collapse is retained too');
  } finally {
    controller.destroy();
    await h.cleanup();
  }
});
