import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { typescript as ts } from "../../node_modules/monaco-editor/esm/vs/languages/features/typescript/lib/typescriptServices.js";

const sourceRoot = new URL("../../management_portal/static/", import.meta.url);
const sources = new Map();
const spans = [];

// The lineage workbench and the chat workspace are each an entry script plus
// module scripts; lineage.html loads all of them, so a declaration is looked
// up in every script of its part, in page order.
const PARTS = {
  lineage: /^lineage_workbench(?:_[a-z_]+)?\.js$/,
  chat: /^(?:chat_workspace_[a-z_]+|portal_chat_workspace)\.js$/,
};
const lineagePage = fs.readFileSync(new URL("../lineage.html", sourceRoot), "utf8");
const pageScripts = [...lineagePage.matchAll(/<script src="\/static\/([a-z_]+\.js)">/g)].map(match => match[1]);

function extract(part, name, method = false) {
  const files = pageScripts.filter(file => PARTS[part].test(file));
  assert.ok(files.length, part);
  const matches = [];
  for (const file of files) {
    if (!sources.has(file)) {
      const text = fs.readFileSync(new URL(file, sourceRoot), "utf8");
      sources.set(file, ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS));
    }
    const source = sources.get(file);
    function visit(node) {
      if ((method ? ts.isMethodDeclaration(node) : ts.isFunctionDeclaration(node)) && node.name?.getText(source) === name) {
        matches.push({ file, source, node });
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  assert.equal(matches.length, 1, name);
  const [{ file, source, node }] = matches;
  spans.push({ file, name, line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1 });
  return node.getText(source);
}

const state = { whichResident: { open: true, query: "draft", submittedQuery: "submitted", correctionSearchResults: [] } };
const saved = [];
const context = vm.createContext({
  state, URL, URLSearchParams, Date,
  esc: value => String(value ?? "").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
  writeLineageUiState: value => saved.push(value),
  writeLineageUiStateSoon: value => saved.push(value),
  whichResidentStateHydrated: false,
  window: { setTimeout() {}, location: { href: "http://fixture.invalid/lineage?resident=old&lineage=lineage_old", search: "?resident=old&lineage=lineage_old" } },
  history: { state: {}, replaceState(_state, _title, target) { saved.push(target); } },
});

vm.runInContext(fs.readFileSync(new URL("portal_text.js", sourceRoot), "utf8"), context);

for (const name of ["whichResidentPanelHtml", "whichResidentCorrectionSearchHtml", "compactWhichResidentResult",
  "persistWhichResidentState", "restoreWhichResidentState", "initialResidentIdFromUrl",
  "initialLineageIdFromUrl", "syncChatTargetLocation"]) {
  vm.runInContext(extract("lineage", name), context);
}

const candidate = { resident_id: "resident_111111111111", title: "Target", lineage_id: "lineage_222222222222", reason: "fixture reason", recommended_action: "fork" };
const cases = {
  card_lineage_fallback() {
    state.whichResident.result = { recommendations: [candidate] };
    const html = context.whichResidentPanelHtml();
    assert.match(html, /<small>lineage_222222222222<\/small>/);
    assert.match(html, /data-which-resident-open-id="resident_111111111111"/);
    assert.match(html, /class="recommended" data-fork-source-resident-id/);
  },
  independent_fork_advice() {
    state.whichResident.result = { recommendations: [], fork_advice: { recommended: true,
      source_resident_id: candidate.resident_id, source_lineage_id: candidate.lineage_id,
      source_title: "Source", reason: "independent advice", suggested_title: "Suggested" } };
    const html = context.whichResidentPanelHtml();
    assert.match(html, /independent advice/);
    assert.match(html, /Suggested/);
    assert.match(html, /<small>lineage_222222222222<\/small>/);
    assert.match(html, /创建分支（推荐）/);
  },
  query_state() {
    context.persistWhichResidentState({ immediate: true });
    const snapshot = saved[0].whichResident;
    assert.equal(snapshot.query, "draft");
    assert.equal(snapshot.submitted_query, "submitted");
    state.whichResident = {};
    context.restoreWhichResidentState(snapshot);
    assert.equal(state.whichResident.query, "draft");
    assert.equal(state.whichResident.submittedQuery, "submitted");
  },
  result_states() {
    state.whichResident.submittedQuery = "";
    assert.match(context.whichResidentPanelHtml(), /描述你要处理的问题/);
    state.whichResident.busy = true;
    assert.match(context.whichResidentPanelHtml(), /正在分析候选 Resident/);
    state.whichResident.busy = false;
    state.whichResident.error = "fixture failure";
    assert.match(context.whichResidentPanelHtml(), /whichResidentStatus error.*fixture failure/);
    state.whichResident.error = "";
    state.whichResident.result = { recommendations: [] };
    assert.match(context.whichResidentPanelHtml(), /没有足够匹配/);
  },
  correction_controls() {
    Object.assign(state.whichResident, { correctionOpen: true, correctionResidentId: candidate.resident_id,
      correctionResidentTitle: "Target", correctionSaving: true, correctionAction: "fork",
      correctionSearchResults: [candidate] });
    const html = context.whichResidentPanelHtml();
    assert.match(html, /name="resident_search" maxlength="300"/);
    assert.match(html, /搜索名称、Lineage 或 Resident ID/);
    assert.match(html, /已选择：.*Target.*resident_111111111111/);
    assert.match(html, /option value="fork" selected/);
    assert.match(html, /option value="open"/);
    assert.match(html, /name="rationale" rows="2" maxlength="1200"/);
    assert.match(html, /disabled>提交中/);
  },
  navigation() {
    assert.equal(context.initialResidentIdFromUrl(), "old");
    assert.equal(context.initialLineageIdFromUrl(), "lineage_old");
    context.syncChatTargetLocation({ kind: "resident", id: candidate.resident_id, lineage_id: candidate.lineage_id });
    const target = new URL(saved[0], "http://fixture.invalid");
    assert.equal(target.searchParams.get("resident"), candidate.resident_id);
    assert.equal(target.searchParams.get("lineage"), candidate.lineage_id);
  },
};

async function resetCase(failure) {
  const invalidations = [];
  const applied = [];
  context.window.PortalResidentProjection = { invalidate: residentId => invalidations.push(residentId) };
  const reset = vm.runInContext("({" + extract("chat", "resetConversation", true) + "}).resetConversation", context);
  const snapshot = vm.runInContext("({" + extract("chat", "applySnapshot", true) + "}).applySnapshot", context);
  const initial = { messages: ["old"], composer: { can_send: true, can_stop: true } };
  const host = {
    questions: { reset() {} }, options: {}, detail: { chat_workspace: initial },
    workspace: { sessionScope: () => "fixture-session", target: () => ({ resident_id: candidate.resident_id }),
      applyContract: contract => applied.push(contract) },
    contractFromDetail: detail => detail.chat_workspace,
    async reload(options) {
      assert.equal(options.force, true);
      this.detail = { chat_workspace: failure ? initial : { ...initial, messages: [] } };
      applied.push(this.detail.chat_workspace);
    },
  };
  let complete;
  let reject;
  const pending = reset.call(host, () => new Promise((resolve, fail) => { complete = resolve; reject = fail; }));
  assert.equal(applied[0].messages.length, 0);
  assert.equal(applied[0].composer.can_send, false);
  assert.equal(applied[0].composer.can_stop, false);
  snapshot.call(host, { messages: ["stale"] });
  assert.equal(host.detail.chat_workspace.messages.length, 0);
  if (failure) {
    reject(new Error("fixture reset failure"));
    await assert.rejects(pending, /fixture reset failure/);
    assert.equal(host.detail.chat_workspace.messages[0], "old");
  } else {
    complete({ reset: true });
    await pending;
    assert.equal(host.detail.chat_workspace.messages.length, 0);
    assert.deepEqual(invalidations, [candidate.resident_id]);
  }
  assert.equal(host.conversationResetInFlight, false);
  assert.equal(host.conversationResetSuppressSnapshots, false);
}

cases.reset_success = () => resetCase(false);
cases.reset_failure = () => resetCase(true);
const selected = process.argv[2];
assert.ok(Object.hasOwn(cases, selected), selected);
await cases[selected]();
process.stdout.write(JSON.stringify({ case: selected, passed: true, fixture: "Node VM; production AST functions; no browser/network/Ground Truth", source_spans: spans }) + "\n");
