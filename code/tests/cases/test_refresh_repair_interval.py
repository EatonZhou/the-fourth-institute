"""Realtime repair deadlines remain owned by each surface's freshness contract."""
from pathlib import Path
import subprocess


def test_explicit_repair_deadline_with_connected_subscription():
    """实时连接正常时仍按声明时限刷新；后台和销毁会撤销定时器。"""
    source = Path(__file__).parents[2] / "management_portal/static/portal_refresh_coordinator.js"
    script = r"""
const assert = require('node:assert/strict');
const timers = new Map(), listeners = new Map();
let sequence = 0;
global.setTimeout = (callback, delay) => { const id = ++sequence; timers.set(id, {callback, delay}); return id; };
global.clearTimeout = id => timers.delete(id);
global.document = { hidden: false, addEventListener: (name, cb) => listeners.set(name, cb), removeEventListener() {} };
global.window = { addEventListener() {}, removeEventListener() {} };
require(process.argv[1]);
(async () => {
  for (const repairInterval of [undefined, 60000, false]) {
    let fetches = 0, applied = 0, closed = 0;
    const owner = window.PortalRefreshCoordinator.create({
      key: 'notice-board:test', interval: 60000, repairInterval,
      subscribe: ({onOpen}) => { onOpen(); return () => closed++; },
      fetch: async () => ++fetches, apply: value => { applied = value; },
    });
    owner.start();
    if (repairInterval === false) assert.equal(timers.size, 0);
    else {
      assert.equal(timers.size, 1);
      const [id, timer] = [...timers][0];
      assert.equal(timer.delay, repairInterval ?? 300000);
      timers.delete(id); timer.callback();
      await new Promise(setImmediate);
      assert.equal(fetches, 1); assert.equal(applied, 1);
      assert.equal(timers.size, 1, 'repair recurs while stream stays connected');
      document.hidden = true; listeners.get('visibilitychange')();
      assert.equal(timers.size, 0);
      document.hidden = false; listeners.get('visibilitychange')();
      assert.equal(timers.size, 1);
    }
    owner.destroy();
    assert.equal(closed, 1); assert.equal(timers.size, 0);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script, str(source)], text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
