'use strict';
const assert = require('node:assert/strict'), path = require('node:path');
const code = path.resolve(__dirname, '../../..');
const {chromium} = require(path.join(code, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const p = await browser.newPage({viewport: {width: 1100, height: 900}}), errors = [], requests = [];
    p.setDefaultTimeout(5000);
    p.on('pageerror', e => errors.push(e.message)); p.on('request', r => requests.push(r.url()));
    await p.setContent('<!doctype html><main>Local fixture</main>');
    for (const name of ['portal_design_system', 'portal_themes', 'hatg_panel', 'lineage_function_blocks', 'portal_resident_menu']) await p.addStyleTag({path: path.join(code, 'management_portal/static', name + '.css')});
    for (const name of ['portal_text', 'portal_state', 'portal_translations', 'portal_i18n', 'portal_ui_primitives', 'portal_overlay', 'portal_ui', 'portal_detail_surface', 'lineage_element_fields', 'portal_resident_menu', 'hatg_function_block_menu']) await p.addScriptTag({path: path.join(code, 'management_portal/static', name + '.js')});
    await p.evaluate(() => {
      PortalI18n.apply('en');
      window.calls = []; window.failNext = false; window.hold = false; window.notices = [];
      window.PortalSocietyService = {createClient: () => ({})};
      window.PortalInstructionClient = {execute: async name => {if (name !== 'ListUsers') throw Error(name); return {users: []};}};
      const parts = ['effect_a', 'effect_b', 'event_manual', 'effect_other', 'effect_generated', 'effect_parent'];
      const components = parts.map(id => ({id, title: id, kind: id === 'event_manual' ? 'event_source' : 'effect',
        config: id === 'event_manual' ? {event_type: 'manual.triggered'} : {failure_recipients: []}}));
      const family = {id: 'fbi_target', title: 'Target Family', family: {facade: 'resident_facade', members: ['resident_member']}, elements: []};
      window.state = {programMutationInFlight: false, lineage: {lineage_id: 'lineage_fixture', revision: 1,
        nodes: [{id: 'resident_member'}], program: {components, wires: [{id: 'wire_ab', kind: 'route', source: 'effect_a', target: 'effect_b'}, {id: 'wire_outside', kind: 'route', source: 'effect_a', target: 'effect_other'}]},
        function_blocks: [family,
          {id: 'fbi_other', elements: [{id: 'effect_other', kind: 'component'}]},
          {id: 'fbi_parent', family: {facade: 'resident_parent'}, elements: [{id: family.id, kind: 'function_block'}, {id: 'effect_generated', kind: 'component', key: 'router'}, {id: 'effect_parent', kind: 'component', key: 'workflow:effect_parent'}]}]}};
      window.originalProgram = JSON.stringify(state.lineage.program);
      const module = PortalHatgFunctionBlockMenu;
      const deps = Object.fromEntries(module.dependencies.map(k => [k, () => {throw Error('Unexpected dependency ' + k);} ]));
      Object.assign(deps, {state, esc: PortalUIPrimitives.escapeHtml,
        familyRoles: block => block.family, functionBlockInstanceById: id => state.lineage.function_blocks.find(b => b.id === id),
        residentMenuHandlers: () => ({}), functionBlockInstanceFailures: () => [], functionBlockInstanceUnacknowledgedFailures: () => [],
        instanceInnerObjects: block => state.lineage.program.components.filter(c => block.elements.some(e => e.id === c.id)),
        displayNodeById: () => null, familyExpanded: () => false, functionBlockElements: block => block.elements,
        graphObjectTitle: id => id, programEventSourceRuntime: () => ({paused: false}), programEffectRuntime: () => ({}),
        functionBlockInstanceTriggersHtml: () => '', toast: text => notices.push(text),
        applyProgramMutation: async callback => callback(),
        executeHatgInstruction: async (name, args, options) => {
          calls.push({name, args: structuredClone(args), options});
          if (name !== 'UpdateFamily') throw Error('Unexpected mutation ' + name);
          if (hold) await new Promise(r => {window.release = r;});
          if (failNext) {failNext = false; throw Error('fixture rejected');}
          assertLocal(state.lineage.lineage_id === 'lineage_fixture');
          for (const id of (args.add_element_ids || [])) {
            for (const b of state.lineage.function_blocks) b.elements = b.elements.filter(e => e.id !== id);
            family.elements.push({id, kind: id.startsWith('wire_') ? 'wire' : 'component', key: 'workflow:' + id});
          }
          family.elements = family.elements.filter(e => !(args.remove_element_ids || []).includes(e.id));
          if (typeof args.auto_migrate === "boolean") family.family.auto_migrate = args.auto_migrate;
          state.lineage.revision++;
          return {lineage: state.lineage};
        }});
      function assertLocal(value) {if (!value) throw Error('wrong lineage');}
      window.PortalNav = {openResidentMenu: options => PortalResidentMenu.open(options)};
      window.menu = module.createFunctionBlockMenu(deps);
      window.openFixture = () => menu.openFunctionBlockMenu('fbi_target', 'objects');
    });
    await p.evaluate(() => openFixture());
    if (process.env.TAU_FAMILY_WORKFLOW_BASELINE) {
      const snapshot = await p.getByRole('dialog').ariaSnapshot();
      assert.equal(await p.locator('[data-family-workflow-form]').count(), 0);
      assert.match(snapshot, /This module has no components/);
      console.log(JSON.stringify({status: 'reproduced',snapshot, gap: 'Native Family objects pane has no way to adopt disconnected ordinary components', formal_acceptance: false}));
    } else {
      const form = () => p.locator('[data-family-workflow-form]');
      const choice = id => form().locator('[data-family-workflow-id="' + id + '"]');
      const expand = async () => {if (!await p.locator('.familyWorkflowOwnership').evaluate(e => e.open)) await p.getByText('Manage workflows', {exact:true}).click();};
      const save = () => form().getByRole('button', {name:'Save ownership', exact:true});
      await expand();
      assert.equal(await form().count(), 1);
      for (const id of ['effect_a','effect_b','event_manual','effect_parent']) assert.equal(await choice(id).isEnabled(), true, id);
      for (const id of ['effect_other','effect_generated','resident_member']) assert.equal(await choice(id).count(), 0, id);
      assert.equal(await choice('wire_ab').isDisabled(), true);
      assert.equal(await choice('wire_outside').isDisabled(), true);
      await choice('effect_a').check(); await choice('effect_b').check(); await choice('event_manual').check();
      const rowGeometry = await choice('effect_a').evaluate(input => ({input:input.getBoundingClientRect().toJSON(), text:input.nextElementSibling.getBoundingClientRect().toJSON()}));
      assert.ok(rowGeometry.text.x >= rowGeometry.input.right, 'workflow labels sit beside their checkboxes');
      assert.equal(await choice('wire_ab').isEnabled(), true);
      await choice('wire_ab').check();
      if (process.env.TAU_FAMILY_WORKFLOW_ARTIFACTS) await p.screenshot({path: path.join(process.env.TAU_FAMILY_WORKFLOW_ARTIFACTS, 'family-workflow-ui.png')});
      await choice('effect_b').uncheck();
      assert.equal(await choice('wire_ab').isChecked(), false, 'a wire loses its choice when an endpoint is deselected');
      assert.equal(await choice('wire_ab').isDisabled(), true);
      await choice('effect_b').check(); await choice('wire_ab').check();
      assert.equal(await choice('wire_outside').isDisabled(), true);
      // A rejected request keeps the user's draft. No automatic retry follows.
      await p.evaluate(() => {failNext = true;}); await save().click();
      await form().getByRole('alert').filter({hasText:'fixture rejected'}).waitFor();
      assert.equal(await p.evaluate(() => calls.length), 1);
      for (const id of ['effect_a','effect_b','event_manual','wire_ab']) assert.equal(await choice(id).isChecked(), true);
      assert.deepEqual(await p.evaluate(() => state.lineage.function_blocks[0].elements), []);
      // Explicit second save; a duplicate submit during the pending operation is ignored.
      await p.evaluate(() => {hold = true;}); await save().click();
      await p.waitForFunction(() => calls.length === 2 && typeof release === 'function');
      assert.equal(await save().isDisabled(), true);
      await form().dispatchEvent('submit');
      assert.equal(await p.evaluate(() => calls.length), 2);
      await p.evaluate(() => {hold = false; release();});
      await p.waitForFunction(() => state.lineage.revision === 2 && !document.querySelector('[data-family-workflow-mode]').disabled);
      const request = await p.evaluate(() => calls[1]);
      assert.equal(request.name, 'UpdateFamily'); assert.equal(request.args.family_id, 'fbi_target');
      assert.deepEqual([...request.args.add_element_ids].sort(), ['effect_a','effect_b','event_manual','wire_ab'].sort());
      assert.deepEqual(request.args.remove_element_ids, []); assert.equal(request.options.expectedVersion, 1);
      await expand();
      assert.equal(await choice('effect_a').count(), 0, 'already owned objects are not additions');
      await form().locator('[data-family-workflow-mode]').selectOption('remove');
      for (const id of ['effect_a','effect_b','event_manual','wire_ab']) assert.equal(await choice(id).isEnabled(), true);
      assert.equal(await choice('effect_parent').count(), 0);
      await choice('event_manual').check(); await save().click();
      await p.waitForFunction(() => state.lineage.revision === 3);
      assert.deepEqual(await p.evaluate(() => calls[2].args), {family_id:'fbi_target', add_element_ids:[], remove_element_ids:['event_manual']});
      assert.equal(await p.evaluate(() => JSON.stringify(state.lineage.program) === originalProgram), true, 'ownership changes preserve object IDs/configuration');
      // Parent Family workflows are offered; sibling/generated objects are never offered.
      await expand(); await choice('effect_parent').check();
      await save().click(); await p.waitForFunction(() => state.lineage.revision === 4);
      assert.equal(await p.evaluate(() => state.lineage.function_blocks[2].elements.some(e => e.id === 'effect_parent')), false);
      // Same Lineage revision changes cannot silently retarget an unsaved selection.
      await expand(); await choice('event_manual').check();
      await p.evaluate(() => {state.lineage.revision++;});
      await save().click();
      await form().getByRole('alert').filter({hasText:'The Lineage has changed'}).waitFor();
      assert.equal(await p.evaluate(() => calls.length), 4);
      await form().getByRole('button', {name:'Select again',exact:true}).click();
      await expand(); assert.equal(await choice('event_manual').isChecked(), false);
      // Mutation in flight disables the ordinary workflow control too.
      await p.evaluate(() => {state.programMutationInFlight = true; menu.refreshFunctionBlockMenu();});
      assert.equal(await choice('event_manual').isDisabled(), true);
      await p.evaluate(() => {state.programMutationInFlight = false; PortalI18n.apply('zh-CN'); menu.refreshFunctionBlockMenu();});
      await p.getByText('管理工作流', {exact:true}).waitFor();
      assert.equal(await form().getByRole('button', {name:'保存归属',exact:true}).count(), 1);
      // Family automatic account migration defaults on; failures keep the server value.
      await p.evaluate(() => menu.openFunctionBlockMenu('fbi_target', 'block'));
      const toggle = () => p.locator('[data-family-auto-migrate]');
      assert.equal(await toggle().getAttribute('aria-checked'), 'true');
      await p.evaluate(() => {failNext = true;}); await toggle().click();
      await p.waitForFunction(() => calls.length === 5 && !document.querySelector('[data-family-auto-migrate]').disabled);
      assert.equal(await toggle().getAttribute('aria-checked'), 'true');
      await p.evaluate(() => {hold = true;}); await toggle().click();
      await p.waitForFunction(() => calls.length === 6);
      assert.equal(await toggle().isDisabled(), true);
      await toggle().dispatchEvent('click');
      assert.equal(await p.evaluate(() => calls.length), 6);
      await p.evaluate(() => {hold = false; release();});
      await p.waitForFunction(() => document.querySelector('[data-family-auto-migrate]').getAttribute('aria-checked') === 'false');
      const policyRequest = await p.evaluate(() => calls[5]);
      assert.deepEqual(policyRequest.args, {family_id:'fbi_target', auto_migrate:false});
      assert.equal(policyRequest.options.expectedVersion, 5);
      await p.evaluate(() => {state.programMutationInFlight = true; menu.refreshFunctionBlockMenu();});
      assert.equal(await toggle().isDisabled(), true);
      await p.evaluate(() => {state.programMutationInFlight = false; menu.refreshFunctionBlockMenu();});
      await toggle().click();
      await p.waitForFunction(() => document.querySelector('[data-family-auto-migrate]').getAttribute('aria-checked') === 'true');
      if (process.env.TAU_FAMILY_WORKFLOW_ARTIFACTS) await p.screenshot({path: path.join(process.env.TAU_FAMILY_WORKFLOW_ARTIFACTS, 'family-auto-migrate.png')});
      await p.evaluate(() => menu.openFunctionBlockMenu('fbi_target', 'objects'));
      // Ordinary blocks get no Family ownership controls.
      await p.evaluate(() => {delete state.lineage.function_blocks[0].family; menu.refreshFunctionBlockMenu();});
      assert.equal(await form().count(), 0);
      // An old menu cannot mutate an identically named block in another Lineage.
      await p.evaluate(() => {state.lineage.function_blocks[0].family = {facade:'resident_facade'}; menu.refreshFunctionBlockMenu();});
      if (!await p.locator('.familyWorkflowOwnership').evaluate(e => e.open)) await p.getByText('管理工作流', {exact:true}).click();
      await choice('event_manual').check();
      await p.evaluate(() => {state.lineage.lineage_id = 'another_lineage';});
      await form().dispatchEvent('submit');
      await p.getByText('Lineage 已变化，请重新选择工作流。', {exact:true}).waitFor();
      assert.equal(await p.evaluate(() => calls.length), 7);
      console.log(JSON.stringify({status:'passed',native:'PortalResidentMenu/Overlay/DetailSurface/HATG menu/i18n/CSS',instruction_boundary:'isolated UpdateFamily fixture',formal_acceptance:false}));
    }
    assert.deepEqual(errors, []); assert.deepEqual(requests, []);
  } finally {await browser.close();}
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
