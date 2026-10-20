// Reviewable source migration and catalog inventory.
const fs = require("fs");
const path = require("path");
const babelParser = require("@babel/parser");
const traverse = require("@babel/traverse").default;
const babelParse = (source, filename) => babelParser.parse(source, {
  sourceType: "unambiguous", sourceFilename: filename, allowReturnOutsideFunction: true,
  plugins: ["jsx", "typescript", "decorators-legacy"],
});
const root = path.resolve(__dirname, "..");
const apply = process.argv.includes("--apply");
const records = new Set(["Language", "Follow browser", "Unable to save setting."]);
const htmlSources = [];
const changes = [];
const labelKeys = /^(label|title|subtitle|description|placeholder|ariaLabel|emptyText|emptyMessage|loadingText|tooltip|message|confirmText|cancelText|text|heading|hint|help|name)$/i;
const labelNames = /label|title|message|notice|caption|tooltip|heading|placeholder|description|summary|empty|statusText|hint|help|copyText/i;
const excludedNames = /prompt|instruction|seed|export|serialize|markdown|sourceCode|scriptBody|systemMessage/i;
const svgGeometry = value => /^\s*<(?:svg|path|circle|ellipse|rect|line|polyline|polygon|g|defs|use)\b/i.test(value);
const human = value => /[\p{Script=Han}]/u.test(value) || /^[A-Za-z][A-Za-z0-9 ,.!?…:;()\-–—/·+]*$/.test(value.trim().replace(/\{p\d+\}/g, ""));

function discover(directory, result = []) {
  if (!fs.existsSync(directory)) return result;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (["vendor", "node_modules", "__pycache__"].includes(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) discover(file, result);
    else if (/\.(js|html)$/.test(entry.name) && !/portal_i18n|portal_translations/.test(entry.name)) result.push(file);
  }
  return result;
}
const directories = ["management_portal/static", "knowledge_board_lan", "project_board_lan", "management_portal/test_framework/page"];
const files = [...new Set([...directories.flatMap(name => discover(path.join(root, name))), ...fs.readdirSync(path.join(root, "management_portal")).filter(name => name.endsWith(".html")).map(name => path.join(root, "management_portal", name))])];

function excluded(p) {
  return p.findParent(parent => {
    if (parent.isTaggedTemplateExpression()) return true;
    if (parent.isCallExpression() && parent.node.callee.object?.property?.name === "PortalI18n") return true;
    if (parent.isObjectProperty() && /^(prompt|content|instructions|code|script|body|value|id|key|href|path|url|selector|command)$/i.test(parent.node.key.name || parent.node.key.value || "")) return true;
    const fn = parent.isFunctionDeclaration() ? parent.node.id?.name : parent.isVariableDeclarator() ? parent.node.id?.name : "";
    return excludedNames.test(fn || "");
  });
}
function isPresentation(p) {
  if (excluded(p)) return false;
  const parent = p.parentPath;
  if (parent.isObjectProperty() && parent.node.key === p.node) return false;
  if (parent.isMemberExpression() || parent.isSwitchCase() || parent.isImportDeclaration()) return false;
  if (parent.isBinaryExpression() && ["===", "==", "!==", "!=", "in"].includes(parent.node.operator)) return false;
  if (parent.isObjectProperty() && labelKeys.test(parent.node.key.name || parent.node.key.value || "")) return true;
  if (p.findParent(q => q.isAssignmentExpression() && /\.(textContent|innerText|title|placeholder|ariaLabel|alt)\b/.test(q.get("left").toString()))) return true;
  if (p.findParent(q => q.isCallExpression() && /(?:toast|notice|alert|confirm|Message|Error|Tooltip|setAttribute|\.text|\.title|copyText|avatarMessage)\b/i.test((q.node.callee.name || q.node.callee.property?.name || "")))) return true;
  if (p.findParent(q => q.isObjectProperty() && labelKeys.test(q.node.key.name || q.node.key.value || ""))) return true;
  if (p.findParent(q => q.isVariableDeclarator() && labelNames.test(q.node.id?.name || ""))) return true;
  if (p.findParent(q => q.isFunctionDeclaration() && labelNames.test(q.node.id?.name || ""))) return true;
  if (p.findParent(q => q.isTemplateLiteral() && /<[A-Za-z][^>]*>/.test(q.node.quasis.map(part => part.value.cooked || "").join("")))) return true;
  // Localized label dictionaries retain their machine keys.
  if (p.findParent(q => q.isVariableDeclarator() && /LABEL|TITLE|DESCRIPTION|TOOLTIP|STATUS_TEXT/.test(q.node.id?.name || ""))) return true;
  return false;
}
function processJs(source, filename) {
  // Markdown markup and prose belong to the supplied document.
  if (filename.endsWith("/markdown.js")) return { source, count: 0 };
  const edits = [];
  let ast;
  try { ast = babelParse(source, filename.endsWith(".html") ? filename + ".js" : filename, true); }
  catch (error) { throw new Error(`${filename}: ${error.message}`); }
  traverse(ast, {
    TaggedTemplateExpression(p) {
      if (p.node.tag.object?.property?.name !== "PortalI18n") return;
      const skeleton = p.node.quasi.quasis.map((part, index) => (index ? `{p${index - 1}}` : "") + (part.value.cooked ?? part.value.raw)).join("");
      if (p.node.tag.property?.name === "html") htmlSources.push(skeleton);
      else if (p.node.tag.property?.name === "text") records.add(skeleton.trim());
    },
    CallExpression(p) {
      if (p.node.callee.object?.property?.name !== "PortalI18n" || p.node.arguments[0]?.type !== "StringLiteral") return;
      const value = p.node.arguments[0].value;
      if (p.node.callee.property?.name === "t") records.add(value.trim());
      else if (p.node.callee.property?.name === "markup") htmlSources.push(value);
    },
    TemplateLiteral(p) {
      if (excluded(p)) return;
      const values = p.node.quasis.map(part => part.value.cooked ?? part.value.raw);
      const skeleton = values.map((value, index) => (index ? `{p${index - 1}}` : "") + value).join("");
      if (svgGeometry(skeleton)) return;
      if (/<[A-Za-z][^>]*>/.test(skeleton)) {
        htmlSources.push(skeleton);
        edits.push({ start: p.node.start, end: p.node.start, text: "window.PortalI18n.html" });
      } else if (human(skeleton) && isPresentation(p)) {
        records.add(skeleton.trim());
        edits.push({ start: p.node.start, end: p.node.start, text: "window.PortalI18n.text" });
      }
    },
    StringLiteral(p) {
      const value = p.node.value;
      if (svgGeometry(value)) return;
      if (/<[A-Za-z][^>]*>/.test(value) && !excluded(p)) {
        htmlSources.push(value);
        edits.push({ start: p.node.start, end: p.node.end, text: `window.PortalI18n.markup(${source.slice(p.node.start, p.node.end)})` });
        return;
      }
      if (!value.trim() || !human(value) || !isPresentation(p)) return;
      if (/^[.#/]|^data-|^[a-z][a-z0-9_-]*$|\n.*(?:function|const|var)\b/.test(value)) return;
      records.add(value.trim());
      edits.push({ start: p.node.start, end: p.node.end, text: `window.PortalI18n.t(${source.slice(p.node.start, p.node.end)})` });
    },
  });
  edits.sort((a, b) => b.start - a.start);
  for (const edit of edits) source = source.slice(0, edit.start) + edit.text + source.slice(edit.end);
  return { source, count: edits.length };
}
for (const filename of files) {
  if (process.argv.includes("--progress")) console.error(path.relative(root, filename));
  const source = fs.readFileSync(filename, "utf8");
  let output, count = 0;
  if (filename.endsWith(".js")) ({ source: output, count } = processJs(source, filename));
  else {
    output = source.replace(/(<script\b[^>]*>)([\s\S]*?)(<\/script>)/gi, (whole, open, js, close) => {
      if (/\bsrc=|application\/(?:json|ld\+json)/i.test(open) || !js.trim()) return whole;
      const result = processJs(js, filename); count += result.count;
      return open + result.source + close;
    });
    htmlSources.push(output.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "").replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ""));
  }
  if (count) { changes.push({ file: path.relative(root, filename), count }); if (apply) fs.writeFileSync(filename, output); }
}
fs.writeFileSync(process.argv.find(arg => arg.startsWith("--output="))?.slice(9) || "/tmp/portal-i18n-sources.json", JSON.stringify({ records: [...records].sort(), htmlSources, changes }));
console.log(JSON.stringify({ files: files.length, changed: changes.length, calls: changes.reduce((sum, item) => sum + item.count, 0), messages: records.size, htmlTemplates: htmlSources.length }));
