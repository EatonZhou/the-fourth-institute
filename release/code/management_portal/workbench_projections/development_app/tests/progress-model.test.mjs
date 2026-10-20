import { test } from "node:test";
import assert from "node:assert/strict";
import * as model from "../src/progress/model.js";
const items = [
  { key: "before", report: { end_at: "2026-10-14T15:59:59.999Z" } },
  { key: "start", title: "新成果", module_id: "m", report: { end_at: "2026-10-14T16:00:00Z", observed_at: "2030-01-01T00:00:00Z" }, scene_report_ref: "later" },
  { key: "end", title: "新成果", module_id: "n", report: { end_at: "2026-10-16T15:59:59.999Z" } },
  { key: "after", report: { end_at: "2026-10-16T16:00:00Z" } },
  { key: "unknown", report: { end_at: "invalid" } },
];
const keys = (filter) => model.filterItems(items, filter).map(x => x.key);
test("inclusive Hong Kong dates use the owning report, not load or recommendation date", () => {
  assert.equal(model.achievementDate(items[1]), "2026-10-15");
  assert.deepEqual(keys({ start_date: "2026-10-15", end_date: "2026-10-16" }), ["start", "end"]);
  assert.deepEqual(keys({ start_date: "2026-10-15", end_date: "2026-10-15" }), ["start"]);
});
test("one-sided ranges, clear and no match", () => {
  assert.deepEqual(keys({ start_date: "2026-10-17" }), ["after"]);
  assert.deepEqual(keys({ end_date: "2026-10-14" }), ["before"]);
  assert.deepEqual(keys({ start_date: "2027-01-01" }), []);
  assert.deepEqual(keys({}), items.map(x => x.key));
});
test("date range rejects reversed or invalid calendar dates; leap day is valid", () => {
  for (const filter of [
    { start_date: "2026-10-16", end_date: "2026-10-15" },
    { start_date: "2026-02-29" }, { end_date: "2026-13-01" },
    { end_date: "yesterday" },
  ]) {
    assert.ok(model.dateRangeError(filter));
    assert.deepEqual(keys(filter), []);
  }
  assert.equal(model.dateRangeError({ start_date: "2028-02-29" }), "");
});
test("dates combine with search and module scope", () => {
  assert.deepEqual(keys({ start_date: "2026-10-15", end_date: "2026-10-16", query: "新", module_filter: "m" }), ["start"]);
});
test("parent module scope follows declared parents, not ID prefixes; cycles terminate", () => {
  const modules = [
    { id: "interface" },
    { id: "conversation", parent: "interface" },
    { id: "composer", parent: "conversation" },
    { id: "interface.lookalike" },
    { id: "cycleA", parent: "cycleB" },
    { id: "cycleB", parent: "cycleA" },
  ];
  const ids = model.moduleScope(modules, "interface");
  assert.deepEqual(ids, ["interface", "conversation", "composer"]);
  assert.deepEqual(
    model
      .filterItems(
        [
          { module_id: "composer", key: "a" },
          { module_id: "interface.lookalike", key: "b" },
        ],
        { module_filter: "interface", module_ids: ids },
      )
      .map((x) => x.key),
    ["a"],
  );
  assert.equal(model.moduleScope(modules, "cycleA").length, 2);
});
