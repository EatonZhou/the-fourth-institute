const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { babelParse } = require('./babel_parse.cjs');
const file = path.resolve(__dirname, '../../../management_portal/static/hatg_panel.js');
const source = fs.readFileSync(file, 'utf8');
const wanted = new Set(['familyWorkGuidesHtml', 'bindFamilyWorkGuideForms', 'familyTaskExecutionHtml',
  'functionBlockOverviewWorkflowHtml', 'functionBlockInstanceTriggersHtml', 'functionBlockElementListHtml', 'familyProgressSettingsHtml']);
const functions = [];
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'FunctionDeclaration' && wanted.has(node.id?.name)) functions.push(source.slice(node.start, node.end));
  for (const [key, child] of Object.entries(node)) {
    if (['loc', 'comments', 'tokens'].includes(key)) continue;
    if (Array.isArray(child)) child.forEach(visit); else if (child && typeof child === 'object') visit(child);
  }
}
visit(babelParse(source, file));
assert.equal(functions.length, wanted.size);
const tasks = {
  route: { role: 'router', title: '门面收到消息', guide: '判断原请求由哪个当前成员承接；按需读取成员职责，输出 route packet。', prompt: '为原请求选择成员。\n${facts}\n指南：${guide_path}（${guide_version}）' },
  summary: { role: 'steward', title: '摘要达到时间或工作量阈值', guide: '按需读取到期成员的维护视图，归纳阶段与长期摘要，保留未解决事项和资料入口。', prompt: '归纳到期摘要。\n${facts}\n指南：${guide_path}（${guide_version}）' },
};
function pageScript() {
  const state = { lineage: { revision: 1, program: { components: [
    {id:'event_1', kind:'event_source', title:'门面收到消息', config:{event_type:'manual.triggered'}},
    {id:'effect_1', kind:'effect', title:'派发角色任务'},
    {id:'manual_extra', kind:'event_source', title:'检查运行状况', config:{event_type:'manual.triggered'}},
  ], wires: [
    {id:'wire_1', source:'event_1', target:'effect_1', config:{invoke_profiles:{effect:{arguments:{family_tasks:{value:tasks}}}}}},
    {id:'wire_progress', source:'event_1', target:'effect_1', config:{invoke_profiles:{effect:{arguments:{
      every_turns:{value:20}, family_tasks:{value:{progress:{role:'steward',title:'进展简报',guide:'撰写进展简报',prompt:'归纳进展 ${facts}'}}},
    }}}}},
  ] } } };
  return `
    const state = ${JSON.stringify(state)};
    const block = {family:{}, elements: [{id:'wire_1', kind:'wire'}, {id:'wire_progress',kind:'wire',key:'request_progress'}] };
    const drafts = new Map();
    const expanded = new Set();
    const familyRoles = value => value.family ? ({router:'router', steward:'steward'}) : null;
    const instanceInnerObjects = () => state.lineage.program.components;
    const instanceManualSources = () => state.lineage.program.components.filter(c => c.config?.event_type === 'manual.triggered');
    const functionBlockElements = () => block.elements;
    const graphObjectTitle = id => id;
    const functionBlockRecipientsSummaryHtml = () => '<section class="functionBlockRecipientsSummary">失败通知汇总</section>';
    const programEventSourceRuntime = () => ({paused:false});
    const functionBlockObjectStatusHtml = component => '<span class="portalStatus">' + (component.kind === 'event_source' ? '监听中' : '执行成功') + '</span>';
    const effectRecipientsButtonHtml = component => '<button type="button" class="portalButton" data-block-recipients="' + component.id + '">管理员 · 修改</button>';
    const esc = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
    const tag = (parts, ...values) => parts.reduce((out, part, index) => out + part + (values[index] ?? ''), '');
    window.PortalI18n = {html:tag, text:tag, t:String};
    window.calls = []; window.rejectSave = false;
    const executeHatgInstruction = async (instruction, args, options) => {
      window.calls.push({instruction, args, options});
      if (window.rejectSave || options.expectedVersion !== state.lineage.revision) throw Error('版本冲突，请保留草稿并刷新');
      state.lineage.program.wires[0].config.invoke_profiles = args.operations[0].changes['config.invoke_profiles'];
      state.lineage.revision += 1;
      return {ok:true};
    };
    const applyProgramMutation = callback => callback();
    ${functions.join('\n')}
    window.draw = () => {
      const root = document.getElementById('guide');
      root.innerHTML = functionBlockOverviewWorkflowHtml(block, false, drafts, expanded);
      bindFamilyWorkGuideForms(root, window.draw, drafts, expanded);
    };
    window.showPlain = () => {delete block.family; window.draw();};
    window.saved = () => state.lineage.program.wires[0].config.invoke_profiles.effect.arguments.family_tasks.value;
    window.draw();
  `;
}
module.exports = { pageScript };

if (require.main === module) {
  const { test } = require('node:test');
  test('editing a scenario persists through redraw, preserves other drafts and rejects unknown variables', async () => {
    const dom = new JSDOM('<main id="guide"></main>', { runScripts: 'outside-only' });
    dom.window.structuredClone = structuredClone;
    dom.window.eval(pageScript());
    const root = dom.window.document;
    const form = trigger => root.querySelector(`[data-trigger="${trigger}"]`);
    form('summary').elements.guide.value = '未保存的摘要草稿';
    form('summary').dispatchEvent(new dom.window.Event('input', {bubbles:true}));
    form('route').elements.guide.value = '已修改的路由指南 <script>不执行</script>';
    form('route').dispatchEvent(new dom.window.Event('input', {bubbles:true}));
    form('route').dispatchEvent(new dom.window.Event('submit', {bubbles:true,cancelable:true}));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(dom.window.calls[0].instruction, 'ApplyLineageChanges');
    assert.equal(dom.window.calls[0].options.expectedVersion, 1);
    assert.equal(form('route').elements.guide.value, '已修改的路由指南 <script>不执行</script>');
    assert.equal(form('summary').elements.guide.value, '未保存的摘要草稿');
    form('route').elements.prompt.value = '${bad_variable}';
    form('route').dispatchEvent(new dom.window.Event('submit', {bubbles:true,cancelable:true}));
    assert.match(form('route').querySelector('[role="status"]').textContent, /未知变量/);
    assert.equal(dom.window.calls.length, 1);
    dom.window.close();
  });
  test('version conflict preserves edited text and reports failure in place', async () => {
    const dom = new JSDOM('<main id="guide"></main>', { runScripts: 'outside-only' });
    dom.window.structuredClone = structuredClone;
    dom.window.eval(pageScript()); dom.window.rejectSave = true;
    const form = dom.window.document.querySelector('form');
    form.elements.guide.value = '冲突时保留的草稿';
    form.dispatchEvent(new dom.window.Event('submit', {bubbles:true,cancelable:true}));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(form.elements.guide.value, '冲突时保留的草稿');
    assert.match(form.querySelector('[role="status"]').textContent, /版本冲突/);
    dom.window.close();
  });
  test('Family overview combines runtime controls with each scenario without a duplicate inventory', () => {
    const dom = new JSDOM('<main id="guide"></main>', { runScripts: 'outside-only' });
    dom.window.eval(pageScript());
    const root = dom.window.document;
    const card = root.querySelector('[data-family-scene-key="wire_1:route"]');
    assert.match(root.querySelector('header').textContent, /工作流程/);
    for (const selector of ['[data-block-object-edit="event_1"]', '[data-block-event-switch="event_1"]',
      '[data-function-block-trigger="event_1"]', '[data-block-object-edit="effect_1"]', '[data-block-recipients="effect_1"]',
      'textarea[name="guide"]', 'textarea[name="prompt"]']) assert.ok(card.querySelector(selector), selector);
    assert.match(card.querySelector('.familyTaskShared').textContent, /3 个场景共用/);
    assert.equal(root.querySelectorAll('.functionBlockRecipientsSummary, .functionBlockElementList').length, 0);
    assert.equal(root.querySelectorAll('.functionBlockInstanceTriggers [data-function-block-trigger="event_1"]').length, 0);
    assert.equal(root.querySelectorAll('[data-function-block-trigger="manual_extra"]').length, 1);
    assert.ok(root.querySelector('[data-block-open-objects]'));
    const progress = root.querySelector('[data-family-scene-key="wire_progress:progress"]');
    assert.equal(progress.querySelector('[name="every_turns"]').value, '20');
    assert.equal(progress.querySelectorAll('form').length, 2);
    assert.equal(progress.querySelectorAll('form form').length, 0);
    // Plain Function Blocks keep their existing controls and full inventory.
    dom.window.showPlain();
    assert.ok(root.querySelector('.functionBlockRecipientsSummary'));
    assert.ok(root.querySelector('.functionBlockElementList'));
    assert.equal(root.querySelectorAll('.familyTaskCard').length, 0);
    dom.window.close();
  });
  test('opening runtime settings can redraw without folding the active scene', () => {
    const dom = new JSDOM('<main id="guide"></main>', { runScripts: 'outside-only' });
    dom.window.eval(pageScript());
    const selector = '[data-family-scene-key="wire_1:route"]';
    let card = dom.window.document.querySelector(selector);
    card.open = true; card.dispatchEvent(new dom.window.Event('toggle'));
    dom.window.draw();
    card = dom.window.document.querySelector(selector);
    assert.equal(card.open, true);
    card.open = false; card.dispatchEvent(new dom.window.Event('toggle'));
    dom.window.draw();
    assert.equal(dom.window.document.querySelector(selector).open, false);
    dom.window.close();
  });
}
