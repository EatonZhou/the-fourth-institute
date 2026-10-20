import test from "node:test";
import assert from "node:assert/strict";
import { loadProgress, readText } from "../src/progress/data.js";
import { fixture, report, content, schema, sha } from "./progress-fixtures.mjs";

const tags = ["tfi.portal-stage-report.v1"];
const entry = {
  label: "打开团队对话",
  url: "/workbenches/view?id=workbench_75b51c00005e&region=facade",
};

// The checked-in fixture stays unchanged. Only this isolated store varies.
async function input(tag, changeReport = () => {}, changeContent = () => {}) {
  const f = await fixture();
  f.body.schema = tag;
  changeReport(f.body, f.target);
  f.db[report] = JSON.stringify(f.body, null, 2);
  const supplement = JSON.parse(f.db[content]);
  supplement.report_sources[0].sha256 = await sha(f.db[report]);
  changeContent(supplement, f);
  f.db[content] = JSON.stringify(supplement);
  return f;
}

// The loader receives read/resolve only. Every other capability access is
// recorded and fails, including a caught attempt; frozen input forbids writeback.
async function loadReadOnly(f) {
  const before = structuredClone(f.db), unexpected = [], operations = [];
  Object.freeze(f.db);
  const resources = new Proxy(f.resources, {
    get(target, key) {
      if (key !== "read" && key !== "resolve") {
        unexpected.push(String(key));
        throw new Error(`Unexpected resource capability: ${String(key)}`);
      }
      return (...args) => {
        operations.push({ operation: key, args });
        return target[key](...args);
      };
    },
    set(_, key) {
      unexpected.push(`set:${String(key)}`);
      throw new Error("Read-only resource boundary");
    },
  });
  const result = await loadProgress(resources);
  assert.deepEqual(unexpected, []);
  assert.deepEqual(f.db, before, "Original document bytes must not be rewritten");
  assert.ok(operations.length > 0);
  assert.ok(operations.every(x => x.operation === "read" || x.operation === "resolve"));
  return result;
}

const bubble = result => result.items.find(x => x.id === "bubble");

for (const tag of tags) {
  test(`accepts exact ${tag} with original identity, bytes and read-only loading`, async () => {
    const f = await input(tag);
    const result = await loadReadOnly(f);
    assert.equal(result.reports.length, 1);
    assert.equal(result.items.length, 2);
    assert.deepEqual(result.failures, []);
    assert.equal(result.reports[0].schema, tag);
    assert.equal(result.reports[0].hash, await sha(f.db[report]));
    assert.equal(bubble(result).ref, f.target.ref);
    assert.equal(bubble(result).key, JSON.stringify([report, "conversation", "bubble"]));
    assert.equal(bubble(result).json_pointer, "/modules/0/improvements/0");
    assert.equal(bubble(result).headline, true);
    assert.deepEqual(bubble(result).entry, entry);
    const again = await loadReadOnly(f);
    assert.deepEqual(again.items.map(x => [x.key, x.ref, x.hash, x.entry]),
      result.items.map(x => [x.key, x.ref, x.hash, x.entry]));
  });

  for (const [name, mutate] of [
    ["wrong report ref", d => { d.ref = report + ".other"; }],
    ["invalid end date", d => { d.end_at = "not-a-date"; }],
    ["non-array modules", d => { d.modules = {}; }],
  ]) {
    test(`${tag} still rejects ${name}`, async () => {
      const result = await loadReadOnly(await input(tag, mutate));
      assert.equal(result.reports.length, 0);
      assert.equal(result.items.length, 0);
      assert.ok(result.failures.some(x => x.ref === report && x.kind === "format"));
    });
  }

  for (const [name, mutate] of [
    ["wrong module", t => { t.module_id = "canvas"; }],
    ["wrong improvement", t => { t.improvement_id = "other"; }],
    ["wrong pointer", t => { t.json_pointer = "/modules/1/improvements/0"; }],
    ["wrong line anchor", t => { t.ref = report + "#L999"; }],
    ["foreign report", t => { t.report_ref = report + ".other"; }],
  ]) {
    test(`${tag} does not weaken supplemental binding: ${name}`, async () => {
      const f = await input(tag, undefined, d => {
        mutate(d.item_links[0].target);
        mutate(d.experience_targets[0].target);
      });
      const result = await loadReadOnly(f);
      assert.equal(result.reports.length, 1);
      assert.equal(bubble(result).headline, false);
      assert.equal(bubble(result).entry, null);
      assert.equal(bubble(result).ref, f.target.ref);
    });
  }

  test(`${tag} rejects duplicate bindings and duplicate item identities`, async () => {
    const duplicateLinks = await input(tag, undefined, d => {
      d.item_links.push(structuredClone(d.item_links[0]));
      d.experience_targets.push(structuredClone(d.experience_targets[0]));
    });
    const result = await loadReadOnly(duplicateLinks);
    assert.equal(bubble(result).headline, false);
    assert.equal(bubble(result).entry, null);
    const duplicateItems = await input(tag, d => {
      d.modules[0].improvements.push(structuredClone(d.modules[0].improvements[0]));
    });
    const repeated = await loadReadOnly(duplicateItems);
    assert.equal(bubble(repeated), undefined);
    assert.equal(repeated.reports[0].complete, false);
  });

  test(`${tag} preserves same-document inline binding without supplementary registration`, async () => {
    for (const valid of [true, false]) {
      const f = await input(tag, (d, target) => {
        // Put the headline after modules so its inline fields cannot move #L.
        const headline = d.headlines[0];
        delete d.headlines;
        d.headlines = [{ ...headline, improvement_ref: { ...target,
          ...(valid ? {} : { json_pointer: "/modules/1/improvements/0" }) } }];
      }, d => { d.report_sources = []; d.item_links = []; });
      // Capture the actual frozen line anchor after the in-memory input edit.
      const raw = f.db[report], line = raw.slice(0, raw.indexOf('"id": "bubble"')).split("\n").length;
      const d = JSON.parse(raw);
      d.headlines[0].improvement_ref.ref = report + "#L" + line;
      f.db[report] = JSON.stringify(d, null, 2);
      const result = await loadReadOnly(f);
      assert.equal(bubble(result).headline, valid);
      assert.equal(bubble(result).entry, null);
    }
  });

  test(`${tag} does not accept the opposite prefix's byte hash as a source binding`, async () => {
    const f = await input(tag);
    const other = 'archive.portal-stage-report.v1';
    const wrongHash = await sha(f.db[report].replace(tag, other));
    assert.notEqual(wrongHash, await sha(f.db[report]));
    const supplement = JSON.parse(f.db[content]);
    supplement.report_sources[0].sha256 = wrongHash;
    f.db[content] = JSON.stringify(supplement);
    const result = await loadReadOnly(f);
    assert.equal(result.reports[0].schema, tag);
    assert.equal(result.reports[0].hash, await sha(f.db[report]));
    assert.equal(bubble(result).headline, false);
    assert.equal(bubble(result).entry, null);
  });

  test(`${tag} retains metadata hash validation and original version`, async () => {
    const f = await input(tag), read = f.resources.read;
    f.resources.read = async (ref, format) => {
      const r = await read(ref, format);
      return { ...r, metadata: { ...r.metadata, version: "frozen-version-17" } };
    };
    const text = await readText(f.resources, report);
    assert.equal(text.text, f.db[report]);
    assert.equal(text.metadata.version, "frozen-version-17");
    assert.equal(text.hash, await sha(f.db[report]));
    f.resources.read = async (ref, format) => {
      const r = await read(ref, format);
      return ref === report ? { ...r, metadata: { content_sha256: "0".repeat(64) } } : r;
    };
    const result = await loadReadOnly(f);
    assert.equal(result.reports.length, 0);
    assert.ok(result.failures.some(x => x.ref === report && x.kind === "version"));
  });

  test(`${tag} keeps the supplementary contract revision gate`, async () => {
    const f = await input(tag);
    f.db[schema] = JSON.stringify({ "x-contract-revision": 1 });
    const result = await loadReadOnly(f);
    assert.equal(result.reports.length, 1);
    assert.equal(bubble(result).headline, false);
    assert.equal(bubble(result).entry, null);
  });
}

for (const tag of [
  "archive.portal-stage-report.v1",
  "tfi.portal-stage-report.v2", "archive.portal-stage-report.v2",
  "tfi.portal-stage-report.v0", "archive.portal-stage-report.v0",
  "tfi.portal-stage-report.v01", "archive.portal-stage-report.v01",
  "tfi.other.v1", "archive.other.v1", "portal-stage-report.v1",
  "other.portal-stage-report.v1", "tfi.portal-stage-report.v1.extra",
  "tfi.portal-stage-report.v1 ", " tfi.portal-stage-report.v1",
  "TFI.portal-stage-report.v1", null, 1, {}, ["tfi.portal-stage-report.v1"],
]) {
  test(`rejects non-exact stage tag ${JSON.stringify(tag)}`, async () => {
    const result = await loadReadOnly(await input(tag));
    assert.equal(result.items.length, 0);
    assert.equal(result.reports.length, 0);
    assert.ok(result.failures.some(x => x.ref === report && x.kind === "format"));
  });
}
