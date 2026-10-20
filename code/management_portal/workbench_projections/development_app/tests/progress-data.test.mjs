import { test } from "node:test";
import assert from "node:assert/strict";
import * as data from "../src/progress/data.js";
import { fixture, content, report, map } from "./progress-fixtures.mjs";
import { filterItems } from "../src/progress/model.js";
test("formal module attribution changes browsing but preserves original achievement identity", async () => {
  const f = await attributedFixture();
  const s = await data.loadProgress(f.resources);
  const row = s.items.find((x) => x.improvement_id === "bubble");
  assert.ok(row.module_attribution);
  assert.equal(row.module_attribution.id, "canvas");
  assert.equal(row.module_attribution.ref, f.attribution.module.ref);
  assert.equal(row.module_attribution.source, f.fixedMap);
  assert.equal(row.ref, f.target.ref);
  assert.equal(row.module_id, "conversation");
  assert.equal(row.key, JSON.stringify([report, "conversation", "bubble"]));
  assert.ok(s.watched.includes(f.fixedMap));
  assert.ok(filterItems(s.items, { module_filter: "canvas" }).includes(row));
  assert.ok(
    !filterItems(s.items, { module_filter: "conversation" }).includes(row),
  );
});
test("unreadable, conflicting or mismatched attribution never replaces the reported module", async () => {
  const cases = [
    (c) => (c.report_sources[0].sha256 = "0".repeat(64)),
    (c) => c.module_attributions.push(c.module_attributions[0]),
    (c) => (c.module_attributions[0].target.ref = report + "#L1"),
    (c) =>
      (c.module_attributions[0].target.json_pointer =
        "/modules/1/improvements/0"),
    (c) => (c.module_attributions[0].module.map_sha256 = "0".repeat(64)),
    (c) => (c.module_attributions[0].module.json_pointer = "/modules/0"),
    (c) => (c.module_attributions[0].module.title = "Unconfirmed label"),
    (c) => (c.module_attributions[0].module.ref += "9"),
    (c, f) => f.denied.add(f.fixedMap),
    (c, f) => f.failed.add(f.fixedMap),
    (c, f) => {
      const read = f.resources.read;
      f.resources.read = async (r, format) => {
        const v = await read(r, format);
        if (r === f.fixedMap && format === "yaml")
          v.metadata.content_sha256 = "0".repeat(64);
        return v;
      };
    },
  ];
  for (const mutate of cases) {
    const f = await attributedFixture();
    const c = JSON.parse(f.db[content]);
    mutate(c, f);
    f.db[content] = JSON.stringify(c);
    const s = await data.loadProgress(f.resources);
    const row = s.items.find((x) => x.improvement_id === "bubble");
    assert.equal(row.module_attribution, undefined);
    assert.equal(row.module_id, "conversation");
    assert.equal(row.ref, f.target.ref);
  }
});
async function attributedFixture() {
  const f = await fixture();
  const { sha } = await import("./progress-fixtures.mjs");
  f.fixedMap = map;
  f.attribution = {
    target: f.target,
    module: {
      id: "canvas",
      title: "团队画布",
      ref: f.fixedMap + "#L4",
      map_ref: f.fixedMap,
      map_sha256: await sha(f.db[map]),
      json_pointer: "/modules/1",
    },
    reason: "正式成果归属修正",
  };
  const c = JSON.parse(f.db[content]);
  c.module_attributions = [f.attribution];
  f.db[content] = JSON.stringify(c);
  return f;
}
test("historical map attribution is neither read nor watched; ordinary stage Git evidence remains readable", async () => {
  const f = await attributedFixture();
  const historical = "repo_711b6ab6d5c8/commit/58ed6ce7a7bd170077382f4ded53400a41dc9a58/file/architecture/modules.yaml";
  const gitSource = "repo_711b6ab6d5c8/commit/58ed6ce7a7bd170077382f4ded53400a41dc9a58/file/example.py";
  const c = JSON.parse(f.db[content]);
  c.module_attributions[0].module.map_ref = historical;
  c.module_attributions[0].module.ref = historical + "#L4";
  f.db[content] = JSON.stringify(c);
  f.db[gitSource] = "ordinary stage source";
  const read = f.resources.read;
  f.resources.read = (ref, format) => {
    assert.notEqual(ref, historical, "exported_tree cannot serve historical architecture");
    return read(ref, format);
  };
  const facts = await data.loadProgress(f.resources);
  const row = facts.items.find(x => x.improvement_id === "bubble");
  assert.equal(row.module_attribution, undefined);
  assert.equal(row.module_id, "conversation");
  assert.equal(row.ref, f.target.ref);
  assert.equal(facts.items.length, 2);
  assert.equal(facts.watched.includes(historical), false);
  assert.equal(f.calls.some(x => x.ref === historical), false);
  assert.equal((await data.readText(f.resources, gitSource)).text, "ordinary stage source");
});
test("formal relation deduplicates headline; explicit target overrides only navigation", async () => {
  const f = await fixture();
  const s = await data.loadProgress(f.resources);
  assert.equal(s.items.length, 2);
  const row = s.items[0];
  assert.equal(row.ref, f.target.ref);
  assert.equal(
    row.entry.url,
    "/workbenches/view?id=workbench_75b51c00005e&region=facade",
  );
  assert.equal(row.report_ref, report);
  assert.equal(row.headline, true);
});
test("same-report inline identity deduplicates without a supplemental report hash", async () => {
  const f = await inlineFixture();
  const s = await data.loadProgress(f.resources);
  assert.equal(s.items.length, 2);
  assert.equal(s.items[0].ref, f.body.headlines[0].improvement_ref.ref);
  assert.equal(s.items[0].headline, true);
  assert.equal(s.items[0].standalone, undefined);
});
test("inline links with wrong id, pointer, anchor or another report never merge equal titles", async () => {
  for (const patch of [
    { improvement_id: "other" },
    { json_pointer: "/modules/1/improvements/0" },
    { ref: report + "#L1" },
    { report_ref: report + ".other" },
  ]) {
    const f = await inlineFixture();
    Object.assign(f.body.headlines[0].improvement_ref, patch);
    f.db[report] = JSON.stringify(f.body, null, 2);
    const s = await data.loadProgress(f.resources);
    assert.equal(s.items.length, 3);
    assert.equal(s.items.filter((x) => x.standalone).length, 1);
  }
});
async function inlineFixture() {
  const f = await fixture();
  f.db[content] = JSON.stringify({ report_sources: [], item_links: [] });
  f.body.headlines[0].improvement_ref = { ...f.target };
  const raw = JSON.stringify(f.body, null, 2);
  const line = raw.slice(0, raw.indexOf('"id": "bubble"')).split("\n").length;
  f.body.headlines[0].improvement_ref.ref = report + "#L" + line;
  f.db[report] = JSON.stringify(f.body, null, 2);
  return f;
}
test("wrong hash, pointer or duplicate target never supplies guessed navigation", async () => {
  for (const mutate of [
    (c) => (c.report_sources[0].sha256 = "0".repeat(64)),
    (c) =>
      (c.experience_targets[0].target.json_pointer =
        "/modules/1/improvements/0"),
    (c) => c.experience_targets.push(c.experience_targets[0]),
  ]) {
    const f = await fixture();
    const c = JSON.parse(f.db[content]);
    mutate(c);
    f.db[content] = JSON.stringify(c);
    const s = await data.loadProgress(f.resources);
    assert.equal(
      s.items.find((x) => x.improvement_id === "bubble").entry,
      null,
    );
  }
});
test("denied report disappears before counts; service error remains a localized failure", async () => {
  const f = await fixture();
  f.denied.add(report);
  let s = await data.loadProgress(f.resources);
  assert.equal(s.items.length, 0);
  assert.equal(s.failures[0].kind, "unreadable");
  f.denied.clear();
  f.failed.add(report);
  s = await data.loadProgress(f.resources);
  assert.equal(s.items.length, 0);
  assert.equal(s.failures[0].kind, "error");
});
test("map YAML/text race cannot construct mixed-version focus ref", async () => {
  const f = await fixture();
  const read = f.resources.read;
  f.resources.read = async (r, format) => {
    const v = await read(r, format);
    if (r === map && format === "yaml")
      v.metadata.content_sha256 = "0".repeat(64);
    return v;
  };
  const s = await data.loadProgress(f.resources);
  assert.equal(s.modules.length, 0);
  assert.equal(s.items.length, 2);
});
test("repeated identities are excluded, never arrival-order winners", async () => {
  const f = await fixture();
  f.body.modules[0].improvements.push(
    { ...f.body.modules[0].improvements[0] },
    { ...f.body.modules[0].improvements[0] },
  );
  f.db[report] = JSON.stringify(f.body, null, 2);
  const s = await data.loadProgress(f.resources);
  assert.equal(s.items.filter((x) => x.improvement_id === "bubble").length, 0);
  assert.equal(s.reports[0].complete, false);
});
test("inline and supplemental headline targets must agree", async () => {
  const f = await fixture();
  f.body.headlines[0].improvement_ref = {
    ...f.target,
    improvement_id: "other",
  };
  f.db[report] = JSON.stringify(f.body, null, 2);
  const c = JSON.parse(f.db[content]);
  const { sha } = await import("./progress-fixtures.mjs");
  c.report_sources[0].sha256 = await sha(f.db[report]);
  f.db[content] = JSON.stringify(c);
  const s = await data.loadProgress(f.resources);
  assert.equal(s.items.filter((x) => x.standalone).length, 1);
});
test("historical rollup generation time cannot override period ordering", async () => {
  const f = await fixture();
  const { stage } = await import("./progress-fixtures.mjs");
  const old = {
    ...f.body,
    ref: report.replace("test", "historic"),
    end_at: "2026-10-14T00:00:00Z",
    generated_at: "2026-10-16T00:00:00Z",
  };
  f.db[old.ref] = JSON.stringify(old);
  const i = JSON.parse(f.db[stage]);
  i.report_refs.push(old.ref);
  f.db[stage] = JSON.stringify(i);
  const s = await data.loadProgress(f.resources);
  assert.equal(s.reports[0].ref, report);
});
