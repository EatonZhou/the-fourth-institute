import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WATCHBOARD = ROOT / "management_portal" / "static" / "lineage_workbench_watchboard.js"


def test_watchboard_refresh_requests_are_merged_and_owned_by_the_resident_lifecycle() -> None:
    """并发的 Watchboard 刷新合并成一个请求，定时刷新在切换 Resident 或页面销毁时取消"""
    script = r"""
const assert = require("node:assert/strict");
const path = process.argv[1];
const coordinatorPath = process.argv[2];
const timers = new Map();
const listeners = new Map();
let timerSequence = 0;
global.window = {
  PortalLineageWorkbenchModules: {},
  setTimeout(callback, delay) {
    const id = ++timerSequence;
    timers.set(id, { callback, delay });
    return id;
  },
  clearTimeout(id) {
    timers.delete(id);
  },
  addEventListener(name, callback) {
    listeners.set(name, callback);
  },
  removeEventListener(name, callback) {
    if (listeners.get(name) === callback) listeners.delete(name);
  },
};
global.setTimeout = window.setTimeout;
global.clearTimeout = window.clearTimeout;
global.document = { hidden: false, addEventListener() {}, removeEventListener() {} };

require(coordinatorPath);
require(require("node:path").join(require("node:path").dirname(path), "watchboard_applications/registry.js"));
require(path);
let applicationView;
const createApplications = window.PortalWatchboardApplications.create;
window.PortalWatchboardApplications = {...window.PortalWatchboardApplications, create: options => {
  applicationView = options.view;
  return createApplications(options);
}};

let selectedResident = "resident_a";
const pendingRequests = [];
const state = {
  detail: { watchboard: { enabled: true, signature: "initial", recent_runs: [] } },
  watchboardHiddenResidents: new Set(),
};
const controller = window.PortalLineageWorkbenchModules.createWatchboard({
  state,
  getChatElement: () => null,
  getElementById: () => null,
  escapeHtml: value => String(value || ""),
  apiGet: async () => ({}),
  apiPost: async () => ({}),
  portalUi: {},
  applyLayout: () => {},
  bindSplitter: () => {},
  currentResident: () => null,
  loadFolder: async () => {},
  notify: () => {},
  openResidentFile: async () => {},
  renderMarkdown: value => String(value || ""),
  residentService: {
    watchboard(residentId) {
      return new Promise((resolve, reject) => pendingRequests.push({ residentId, resolve, reject }));
    },
  },
  selectedResidentId: () => selectedResident,
  submitChatPrompt: async () => {},
  writeUiState: () => {},
});

async function flush() {
  for (let turn = 0; turn < 12; turn++) await Promise.resolve();
}

(async () => {
  const first = applicationView.reload();
  const merged = applicationView.reload();
  await flush();
  assert.equal(pendingRequests.length, 1, "concurrent refreshes must share one request");
  assert.equal(pendingRequests[0].residentId, "resident_a");
  pendingRequests[0].resolve({
    watchboard: { enabled: true, signature: "first", recent_runs: [] },
  });
  await first;
  await merged;
  await flush();
  assert.equal(timers.size, 1, "an overlapping trigger must collapse to one trailing refresh");
  const [trailingId, trailing] = [...timers.entries()][0];
  assert.equal(trailing.delay, 0);
  timers.delete(trailingId);
  trailing.callback();
  await flush();
  assert.equal(pendingRequests.length, 2);
  pendingRequests[1].resolve({
    watchboard: { enabled: true, signature: "trailing", recent_runs: [] },
  });
  await flush();

  state.detail = {
    watchboard: {
      enabled: true,
      signature: "running",
      recent_runs: [{ status: "running" }],
    },
  };
  controller.render(state.detail);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 2000);

  selectedResident = "resident_b";
  controller.render({});
  assert.equal(timers.size, 0, "resident switches must cancel the previous owner's timer");

  selectedResident = "resident_a";
  controller.render(state.detail);
  assert.equal(timers.size, 1);
  listeners.get("pagehide")({ persisted: true });
  assert.equal(timers.size, 1, "bfcache suspension must preserve the owned timer");
  assert.equal(listeners.has("pagehide"), true);
  listeners.get("pagehide")({ persisted: false });
  assert.equal(timers.size, 0, "page destruction must cancel the owned timer");
  assert.equal(listeners.has("pagehide"), false);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", script, str(WATCHBOARD), str(ROOT / "management_portal/static/portal_refresh_coordinator.js")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
