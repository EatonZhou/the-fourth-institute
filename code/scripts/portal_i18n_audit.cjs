const fs = require("fs");
const path = require("path");
const babelParser = require("@babel/parser");
const traverse = require("@babel/traverse").default;
const babelParse = (source, filename) => babelParser.parse(source, {
  sourceType: "unambiguous", sourceFilename: filename, allowReturnOutsideFunction: true,
  plugins: ["jsx", "typescript", "decorators-legacy"],
});
const root = path.resolve(__dirname, "..");
const uiWords = new Set("Resource Project Future Paper Workbenches Save Cancel Close Copy Delete Edit Refresh Menu Settings Appearance System Full Reduced Continue Password Username Login Name Status Search Title Type Loading Content Description Send Stop Resume Pause Tools Queue Back Default Device Repo Article Experiment Skill Knowledge Backup Create Open Apply Reset Terminal Select Clear Upload Download Done Enabled Disabled Off On User Users None All Run Draft Publish Help Test Home Preview Auto Light Dark Fire Forest Sci-Fi Decision Idea Information Insight Issue Review Thinking Workspace Untitled Summary Actions Details Unread Read Yes No".split(" "));
const files = [];
for (const directory of ["management_portal/static", "management_portal/test_framework/page", "knowledge_board_lan", "management_portal"]) {
  for (const name of fs.readdirSync(path.join(root, directory))) if (/\.(js|html)$/.test(name) && !/portal_translations|portal_i18n/.test(name)) files.push(path.join(root, directory, name));
}
let removed = 0;
function machineDestination(p) {
  const parent = p.parentPath.node;
  const target = parent.type === "AssignmentExpression" ? parent.left : parent.type === "ObjectProperty" ? parent.key : null;
  const name = target?.type === "MemberExpression" ? target.property.name : target?.name || target?.value;
  if (/^(className|class|id|href|src|role)$/.test(name || "")) return true;
  if (target?.object?.property?.name === "style") return true;
  if (target?.object?.property?.name === "dataset" && !/label|title|tooltip/i.test(name || "")) return true;
  if (parent.type === "CallExpression" && parent.callee.property?.name === "setAttribute") {
    const attr = parent.arguments[0]?.value || "";
    return /^(class|id|href|src|role|name|type)$/.test(attr) || /^data-/.test(attr) && attr !== "data-portal-tooltip";
  }
  return false;
}
function audit(source, filename) {
  const ast = babelParse(source, filename + ".js", true);
  const edits = [];
  traverse(ast, { CallExpression(p) {
    const callee = p.node.callee;
    if (callee.type !== "MemberExpression" || callee.property.name !== "t" || callee.object.type !== "MemberExpression" || callee.object.property.name !== "PortalI18n") return;
    const arg = p.node.arguments[0];
    if (arg?.type !== "StringLiteral") return;
    const value = arg.value.trim();
    const machine = /^[A-Za-z][A-Za-z0-9_.:/-]*$/.test(value) && !uiWords.has(value);
    const call = p.parentPath.isCallExpression() && /^(includes|has|get|set|delete|startsWith|endsWith|match|replace|querySelector|querySelectorAll|closest|getAttribute|createElement|addEventListener|removeEventListener)$/.test(p.parentPath.node.callee.property?.name || "");
    if (machine || call || machineDestination(p)) { edits.push([p.node.start, p.node.end, source.slice(arg.start, arg.end)]); removed++; }
  }, TaggedTemplateExpression(p) {
    if (p.node.tag?.property?.name !== "text" || p.node.tag.object?.property?.name !== "PortalI18n" || !machineDestination(p)) return;
    edits.push([p.node.start, p.node.end, source.slice(p.node.quasi.start, p.node.quasi.end)]); removed++;
  }});
  for (const [start, end, value] of edits.sort((a,b)=>b[0]-a[0])) source = source.slice(0,start)+value+source.slice(end);
  return source;
}
for (const file of files) {
  const source = fs.readFileSync(file,"utf8");
  const result = file.endsWith(".js") ? audit(source,file) : source.replace(/(<script\b[^>]*>)([\s\S]*?)(<\/script>)/gi,(whole,open,js,close)=>/\bsrc=|application\/(?:json|ld\+json)/i.test(open)||!js.trim()?whole:open+audit(js,file)+close);
  if (source !== result) fs.writeFileSync(file,result);
}
console.log(JSON.stringify({removedMachineTokens: removed}));
