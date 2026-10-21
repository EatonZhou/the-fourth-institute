"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const window = {
  PortalResourceChips: {}, PortalImageEditor: { open() {} },
  PortalI18n: { t: value => value }, PortalUIPrimitives: { escapeHtml: value => String(value) },
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, "../../../management_portal/static/portal_chat_workspace.js"), "utf8"), { window });
const parse = text => JSON.parse(JSON.stringify(window.PortalChatWorkspace.residentHandoffParts(text)));
const card = { resident_id: "resident_123456789abc", title: "阅读", ask_message: "请解释" };
const fence = value => "```portal-resident-handoff\n" + JSON.stringify(value) + "\n```";
const cards = value => parse(value).flatMap(part => part.cards || []);

test("handoff validates IDs and normalizes optional fields without trusting URLs", () => {
  const result = cards(fence([card, { resident_id: "resident_123456789abc?x=1" }, { ...card, lineage_id: "javascript:alert(1)", recommended_action: "bad", url: "https://evil.invalid" }]));
  assert.equal(result.length, 2);
  assert.equal(result[1].lineage_id, "");
  assert.equal(result[1].recommended_action, "open");
  assert.equal(result[1].url, undefined);
  assert.equal(cards(fence({ ...card, ask_message: null }))[0].ask_message, "");
  assert.equal(cards(fence({ ...card, recommended_action: "ask" }))[0].recommended_action, "ask");
});

test("only complete top-level handoff fences become cards and prose stays ordered", () => {
  const source = `before\n${fence(card)}\nafter\n${fence(card)}\ntail`;
  assert.equal(cards(source).length, 2);
  assert.deepEqual(parse(source).filter(part => "text" in part).map(part => part.text), ["before", "after", "tail"]);
  for (const source of ["```portal-resident-handoff\n{" , "```portal-resident-handoff\n{}\n```", `\`\`\`\`markdown\n${fence(card)}\n\`\`\`\``, "```portal-resident-handoff\n" + JSON.stringify(card)]) {
    assert.equal(cards(source).length, 0);
    assert.equal(parse(source)[0].text, source);
  }
  assert.equal(cards(fence(card).replaceAll("```", "~~~")).length, 1);
});
