// Production Home monitor functions and ISA client with DOM/transport/clock ports.
// No browser navigation, visual layout, scheduling service or real ISA mutations.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {JSDOM} = require('jsdom');
const {babelParse} = require('./babel_parse.cjs');
const [caseName, payloadFile, _routeFile, resultFile] = process.argv.slice(2);
const monitor = JSON.parse(fs.readFileSync(payloadFile, 'utf8'));
const root = path.resolve(__dirname, '../../../management_portal/static');
const record = {case: caseName, status: 'running', monitor_reads: 0, instructions: [], page_errors: [], unexpected_requests: [], browser_launched: false};
const plain = value => JSON.parse(JSON.stringify(value));
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

async function main() {
  const dom = new JSDOM('<section data-home-component="scheduled-triggered"></section>', {url: 'https://monitor.invalid/', runScripts: 'outside-only', pretendToBeVisual: true});
  const w = dom.window, panel = w.document.querySelector('section');
  const timers = new Map();
  let nextTimer = 0, visible = true;
  let releaseOldRead;
  w.setTimeout = callback => { timers.set(++nextTimer, callback); return nextTimer; };
  w.clearTimeout = id => timers.delete(id);
  const advance = async () => {
    const pending = [...timers.values()]; timers.clear();
    for (const callback of pending) callback();
    await tick();
  };
  const tag = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  Object.assign(w, {
    console, CSS: {escape: String},
    PortalI18n: {t: String, html: tag, text: tag, message: String},
    PortalUIPrimitives: {randomUUID},
    PortalUI: {toast(message, kind) { if (kind === 'error' || kind === 'warning') record.page_errors.push(message); }, errorMessage: (_, error) => String(error)},
    esc: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    displayDateTime: String, scheduledTriggeredExpanded: new Set(),
    scheduledTriggeredStatusData: {}, agentStatusData: {}, scheduledTriggeredLoadInFlight: null,
    scheduledTriggeredFollowupTimer: 0, scheduledTriggeredFollowupCount: 0,
    SCHEDULED_TRIGGERED_FOLLOWUP_MS: 5000, SCHEDULED_TRIGGERED_IDLE_REFRESH_MS: 30000, SCHEDULED_TRIGGERED_FOLLOWUP_LIMIT: 120,
    homeController: {isComponentVisible: () => visible},
    listen: (target, event, callback, signal) => target.addEventListener(event, callback, {signal}),
    homeApi: {get: async url => {
      assert.match(url, /^\/api\/agent-status\/scheduled-triggered(?:\?refresh=1(?:&fresh=1)?)?$/);
      record.monitor_reads++;
      record.last_monitor_forced = url.includes('refresh=1');
      record.last_monitor_fresh = url.includes('fresh=1');
      if (caseName === 'post_mutation_fresh' && record.monitor_reads === 2) {
        return new Promise(resolve => { releaseOldRead = () => resolve(plain(monitor)); });
      }
      if (caseName === 'post_mutation_fresh' && record.monitor_reads === 3) {
        assert.equal(record.last_monitor_fresh, true, 'the queued control read must bypass the old plan');
        const fresh = plain(monitor);
        fresh.items[0].sources[0].paused = true;
        fresh.items[0].sources[0].runtime = {state: 'paused', paused: true};
        return fresh;
      }
      if (caseName === 'cold_loading' && record.monitor_reads === 1) return {status: 'loading', loaded: false, items: []};
      return plain(monitor);
    }},
    PortalAPI: {requestResult: async (url, options) => {
      assert.equal(url, '/api/instructions/execute');
      assert.equal(options.method, 'POST');
      record.instructions.push(plain(options.body));
      return {response: {ok: true, status: 200}, payload: {status: 'committed', result: {}}};
    }},
    // Other Home panels are outside this contract; retain the monitor's state handoff.
    renderAgentStatus() {
      w.agentStatusData = {scheduled_triggered: w.scheduledTriggeredStatusData};
      w.renderScheduledTriggeredStatus(panel);
    },
  });
  w.addEventListener('error', event => record.page_errors.push(event.error?.stack || event.message));
  w.eval(fs.readFileSync(path.join(root, 'portal_instruction_client.js'), 'utf8'));
  const source = fs.readFileSync(path.join(root, 'home.js'), 'utf8');
  const wanted = new Set(['scheduledTriggeredAllows', 'scheduledTriggeredScheduleText', 'scheduledTriggeredNextAt', 'scheduledTriggeredWaitingForPlan', 'scheduledTriggeredItemId', 'scheduledTriggeredScriptStatus', 'scheduledTriggeredSourceState', 'scheduledTriggeredSourceHtml', 'scheduledTriggeredLineageHtml', 'renderScheduledTriggeredStatus', 'controlScheduledTriggered', 'scheduledTriggeredWorkInFlight', 'scheduleScheduledTriggeredFollowup', 'loadScheduledTriggeredStatus', 'connectScheduledTriggered']);
  const declared = node => node.type === 'FunctionDeclaration' ? [node.id] : node.type === 'VariableDeclaration' ? node.declarations.map(item => item.id) : [];
  const selected = babelParse(source, 'home.js').program.body.filter(node => declared(node).some(id => wanted.has(id.name)));
  for (const node of selected) for (const id of declared(node)) wanted.delete(id.name);
  assert.equal(wanted.size, 0);
  // One evaluation retains the production lexical binding for the allows predicate.
  w.eval(selected.map(node => source.slice(node.start, node.end)).join('\n'));
  const controller = new w.AbortController();
  try {
    w.connectScheduledTriggered({root: panel, context: {currentUserReady: Promise.resolve()}, signal: controller.signal});
    await tick();
    if (caseName === 'cold_loading') {
      assert.match(panel.textContent, /正在读取定时与自动触发/);
      await advance();
      assert.ok(record.monitor_reads > 1);
      assert.equal(record.last_monitor_forced, false);
    }
    const rowId = caseName === 'script_effect' ? 'lineage_100000000009/effect_100000000009' : 'resident_100000000009';
    const row = panel.querySelector(`[data-scheduled-triggered-row="${rowId}"]`);
    assert.ok(row);
    assert.equal(panel.querySelector('[data-scheduled-triggered-provider-select]'), null);
    const toggle = () => panel.querySelector('[data-scheduled-triggered-toggle]').click();
    const act = async selector => {
      const button = panel.querySelector(selector); assert.ok(button, selector);
      button.click(); await tick();
    };
    if (caseName === 'post_mutation_fresh') {
      const oldRead = w.loadScheduledTriggeredStatus();
      await tick();
      toggle();
      await act('[data-scheduled-triggered-action="pause_schedule"]');
      assert.equal(record.instructions.length, 1);
      assert.equal(record.instructions[0].instruction, 'PauseComponent');
      assert.match(panel.textContent, /已提交，正在刷新状态/);
      assert.equal(record.monitor_reads, 2, 'the earlier read is still in flight');
      releaseOldRead(); await oldRead; await tick();
      assert.equal(record.monitor_reads, 3);
      assert.equal(record.last_monitor_fresh, true);
      assert.match(panel.textContent, /已暂停/);
      assert.ok(panel.querySelector('[data-scheduled-triggered-action="resume_schedule"]'));
      assert.equal(record.instructions.length, 1, 'read recovery must not replay the mutation');
    } else if (caseName === 'resume_visible_monitor') {
      assert.match(row.textContent, /queued/);
      visible = false;
      panel.dispatchEvent(new w.CustomEvent('portal:home-panel-lifecycle', {detail: {phase: 'hide'}}));
      const reads = record.monitor_reads;
      monitor.items[0].state = {work_state: 'idle', queued_count: 0};
      await advance(); assert.equal(record.monitor_reads, reads);
      visible = true;
      panel.dispatchEvent(new w.CustomEvent('portal:home-panel-lifecycle', {detail: {phase: 'show'}}));
      await tick(); assert.match(panel.textContent, /idle/); assert.equal(record.last_monitor_forced, true);
    } else if (caseName === 'cold_loading') {
      assert.match(panel.textContent, /Daily Event/); assert.equal(panel.querySelector('.agent-status-error'), null);
    } else if (caseName === 'script_effect') {
      assert.match(panel.textContent, /1 个脚本 Effect/); assert.match(panel.textContent, /\/jobs\/维护.py/);
      assert.match(panel.querySelector('.scheduled-triggered-recent').textContent, /成功/);
      toggle(); assert.equal(panel.querySelector('[data-scheduled-triggered-settings]'), null);
      await act('[data-scheduled-triggered-action="pause_schedule"]');
      assert.equal(record.instructions[0].instruction, 'PauseComponent');
      assert.deepEqual(record.instructions[0].arguments, {lineage_id: 'lineage_100000000009', component_id: 'event_source_daily', paused: true});
    } else if (caseName === 'lineage_errors_visible_with_valid_neighbor') {
      const error = panel.querySelector('.agent-status-error'); assert.match(error.textContent, /Lineage read failed/);
      assert.equal(error.querySelector('img,script,[onerror]'), null); assert.equal(w.__monitorInjected, undefined);
    } else if (caseName === 'read_only_lineage') {
      toggle(); assert.match(panel.textContent, /Daily Event/); assert.equal(panel.querySelector('[data-scheduled-triggered-action]'), null);
    } else if (caseName === 'resident_without_timer') {
      assert.match(panel.textContent, /无定时 Event/); assert.match(panel.textContent, /running · 等待 1/);
      assert.equal(panel.querySelector('[data-scheduled-triggered-action="run_once"]'), null);
      assert.ok(panel.querySelector('.scheduled-triggered-action.run-once[disabled]'));
      toggle(); assert.match(panel.textContent, /此 Resident 当前没有连接的时间或文件 Event/);
    } else {
      assert.equal(caseName, 'event_schedule_and_identity');
      const due = '2026-09-29T00:00:07Z';
      assert.equal(panel.querySelector('.scheduled-triggered-summary-value').textContent, due);
      toggle();
      const daily = panel.querySelector('[data-scheduled-triggered-source="event_source_daily"]');
      assert.ok(daily.textContent.includes('下一次 ' + due));
      assert.ok(!panel.querySelector('[data-scheduled-triggered-source="event_source_evening"]').textContent.includes('下一次'));
      assert.match(panel.textContent, /Provider codex · 账号 zero/); assert.match(panel.textContent, /Summarize yesterday/);
      daily.querySelector('[data-scheduled-triggered-schedule-field="interval_seconds"]').value = '120';
      daily.querySelector('[data-scheduled-triggered-schedule-field="jitter_seconds"]').value = '9';
      await act('[data-scheduled-triggered-source="event_source_daily"] [data-scheduled-triggered-action="update_schedule"]');
      const update = record.instructions[0];
      assert.equal(update.schema, 'tfi.hatg-instruction.v2'); assert.equal(update.instruction, 'UpdateComponent');
      assert.equal(update.arguments.lineage_id, 'lineage_100000000009'); assert.equal(update.arguments.component_id, 'event_source_daily');
      assert.equal(update.arguments.changes.config.interval_seconds, 120); assert.equal(update.arguments.changes.config.jitter_seconds, 9);
      assert.ok(update.idempotency_key); assert.ok(record.monitor_reads > 1);
      assert.equal(record.last_monitor_fresh, true);
      await act('[data-scheduled-triggered-action="resume_schedule"]');
      assert.equal(record.instructions[1].instruction, 'PauseComponent');
      assert.deepEqual(record.instructions[1].arguments, {lineage_id: 'lineage_100000000009', component_id: 'event_source_evening', paused: false});
      await act('[data-scheduled-triggered-action="run_once"]');
      assert.equal(record.instructions[2].instruction, 'TriggerEvent');
      assert.deepEqual(record.instructions[2].arguments, {lineage_id: 'lineage_100000000009', event_source_id: 'event_source_manual'});
    }
    assert.deepEqual(record.page_errors, []);
    record.status = 'passed';
  } finally { controller.abort(); timers.clear(); w.close(); fs.writeFileSync(resultFile, JSON.stringify(record, null, 2) + '\n'); }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
