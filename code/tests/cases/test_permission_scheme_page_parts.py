"""权限方案页的部件：调用方可以省略的选项。

:module: security.identity-access
:design: decision_b820faf42218
:importance: P2
"""
import json
from pathlib import Path
import subprocess

STATIC = Path(__file__).resolve().parents[2] / "management_portal" / "static"


def test_scheme_page_loads_without_a_system_change_callback():
    """权限方案页不传 onSystemChange 也能读取方案和刷新权限系统

    PortalPermissionSchemes.mount 的 onSystemChange 是可选项；读取部件不能因为它缺省而拒绝创建。
    """
    script = f"const PART = {json.dumps(str(STATIC / 'portal_permission_schemes_loading.js'))};" + r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({});
context.window = context;
vm.runInContext(fs.readFileSync(PART, 'utf8'), context);
const calls = [];
const state = {schemes: []};
const answers = {GetPermissionSystem: {enabled: true}, ListPermissionSchemes: {schemes: []}};
const loading = context.PortalPermissionSchemesLoading.createLoading({
  clone: value => structuredClone(value), dirty: () => false, freshInstanceState: () => ({}),
  mountState: {systemRevision: 0, compileSerial: 0}, nodeTitles: () => ({}),
  render: () => calls.push('render'), run: async name => { calls.push(name); return answers[name]; },
  scheduleCompile: () => {}, setNotice: (...args) => calls.push(['notice', ...args]), state, t: text => text,
  tx: (strings, ...values) => strings.reduce((text, part, index) => text + part + (values[index] ?? ''), ''),
});
(async () => {
  await loading.load();
  await loading.refreshSystem();
  assert.equal(state.system.enabled, true);
  assert.deepEqual(calls.filter(call => Array.isArray(call)), []);
  assert.deepEqual(calls.filter(call => call !== 'render'), ['GetPermissionSystem', 'ListPermissionSchemes', 'GetPermissionSystem']);
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
