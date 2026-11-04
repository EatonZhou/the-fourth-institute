'use strict';
// Production usage panel with one immutable, privacy-filtered local snapshot.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const ranges = ['today', 'week', 'month', 'year'];
const dimensions = {resident: ['residents', 'chart_series', 'resident_id'], lineage: ['lineages', 'lineage_chart_series', 'lineage_id'], project: ['projects', 'project_chart_series', 'project_id']};
const fmt = n => n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n);
function variant(multiplier, anonymousOnly = false) {
  const value = {status: 'ok', windows: {}};
  for (const [group, [ranking, series, id]] of Object.entries(dimensions)) {
    value[ranking] = anonymousOnly ? [] : Array.from({length: 21}, (_, i) => ({[id]: group + '_' + i,
      title: group + ' ' + (i + 1), periods: Object.fromEntries(ranges.map((r, j) => [r, (i + 1) * multiplier * (j + 1)]))}));
    value[series] = Object.fromEntries(ranges.map((r, j) => {
      const scale = multiplier * (j + 1), total = anonymousOnly ? 15 : 246;
      const segments = anonymousOnly ? [] : Array.from({length: 20}, (_, i) => ({key: group + '_' + (i + 1), label: group + ' ' + (i + 2), tokens: (i + 2) * scale}));
      segments.push({key: 'other_residents', label: 'Other resident(s)', tokens: (anonymousOnly ? 10 : 11) * scale},
        {key: 'unregistered', label: 'Unregistered session(s)', tokens: 5 * scale});
      value.windows[r] = {total_tokens: total * scale, assigned_tokens: (total - 5) * scale, other_tokens: 5 * scale};
      return [r, [{label: r, tokens: total * scale, segments}]];
    }));
  }
  return value;
}
async function exercise(anonymousOnly = false) {
  const dom = new JSDOM('<section data-home-component="usage-attribution"></section>', {url: 'http://usage.fixture.invalid', runScripts: 'outside-only'});
  const w = dom.window, panel = w.document.querySelector('section'), stored = new Map();
  const variants = scale => ({excluding_cached_input: variant(scale, anonymousOnly), including_cached_input: variant(scale * 2, anonymousOnly)});
  const snapshot = {snapshot_version: 1, updated_at: 'fixture', windows: variant(1).windows,
    filters: {backends: ['codex'], accounts: [{provider: 'codex', account_id: 'fixture'}]}, variants: variants(1),
    scopes: [{provider: 'codex', account_id: 'fixture', variants: variants(3)}]};
  let reads = 0;
  const tag = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
  Object.assign(w, {
    PortalCompatibility: {identifier: x => x},
    PortalI18n: {t: String, html: tag, text: tag},
    PortalState: {storage: {readText: (key, fallback) => stored.get(key) ?? fallback, writeText: (key, value) => stored.set(key, value)}},
    PortalRefreshCoordinator: {create: options => ({start() {}, destroy() {}, refresh: async () => options.apply(await options.fetch())})},
  });
  w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/home_usage.js'), 'utf8'));
  const owner = w.PortalHomeUsage.createUsage({currentPortalUsername: () => 'fixture', formatDateTime: String,
    esc: text => String(text ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    homeApi: {get: async url => {assert.equal(url, '/api/agent-status/usage'); reads++; return snapshot}}});
  const controller = new w.AbortController();
  const dispose = owner.connectUsageAttribution({context: {currentUserReady: Promise.resolve()}, signal: controller.signal});
  try {
    for (let i = 0; i < 12; i++) await Promise.resolve();
    assert.equal(reads, 1);
    const setAccount = enabled => {
      const select = panel.querySelector('[data-usage-account]');
      select.value = enabled ? JSON.stringify(['codex', 'fixture']) : '';
      select.dispatchEvent(new w.Event('change'));
      // Clearing an account under a selected backend chooses the provider
      // scope; reset Backend to restore the full scope used by this fixture.
      if (!enabled) {const backend = panel.querySelector('[data-usage-backend]'); backend.value = ''; backend.dispatchEvent(new w.Event('change'))}
    };
    for (const account of [false, true]) {
      setAccount(account);
      for (const cached of [false, true]) {
        const input = panel.querySelector('[data-agent-status-cache-hits]'); input.checked = cached; input.dispatchEvent(new w.Event('change'));
        for (const group of Object.keys(dimensions)) {
          panel.querySelector(`[data-agent-status-grouping="${group}"]`).click();
          for (const [j, range] of ranges.entries()) {
            panel.querySelector(`[data-agent-status-range="${range}"]`).click();
            const scale = (account ? 3 : 1) * (cached ? 2 : 1) * (j + 1);
            const entries = [...panel.querySelectorAll('.agent-status-row')].map(row => [row.querySelector('.agent-status-row-name').textContent, row.querySelector('.agent-status-row-value').textContent]);
            assert.equal(entries.length, anonymousOnly ? 2 : 22, 'Details retain both anonymous categories outside the 20 named groups');
            assert.deepEqual(entries.slice(-2), [['Other resident(s)', fmt((anonymousOnly ? 10 : 11) * scale)], ['Unregistered session(s)', fmt(5 * scale)]]);
            if (!anonymousOnly) {assert.deepEqual(entries[0], [group + ' 21', fmt(21 * scale)]); assert.deepEqual(entries[19], [group + ' 2', fmt(2 * scale)])}
            assert.equal(panel.querySelector('.agent-status-details').textContent.includes('No ' + group + ' token data yet.'), false);
            assert.equal(panel.querySelector('.agent-status-details').textContent.includes('Total ' + fmt((anonymousOnly ? 15 : 246) * scale)), true);
          }
        }
      }
    }
    assert.equal(reads, 1, 'local control changes retain the original snapshot without extra requests');
  } finally {controller.abort(); dispose(); w.close()}
}
(async () => {await exercise(); await exercise(true); console.log('Usage Details: named and anonymous totals across all groupings, periods, cache variants and scopes')})().catch(error => {console.error(error); process.exitCode = 1});
