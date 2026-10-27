// The chat workspace is portal_chat_workspace.js together with the module
// scripts (chat_workspace_*.js) that every page loads just before it. Tests
// load the same scripts in the same order, and look a declaration up in
// whichever script holds it, so moving code between them changes no case.
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const ENTRY = 'portal_chat_workspace.js';

// The chat workspace's scripts in the order pages load them: the
// "chat-workspace" group of script_groups.json. A case that runs a copy of the
// code passes that copy's static directory.
function chatWorkspaceScripts(staticRoot = STATIC) {
  const groups = JSON.parse(fs.readFileSync(path.join(staticRoot, '..', 'script_groups.json'), 'utf8'));
  const files = groups['chat-workspace'].map(source => source.replace(/^\/static\//, ''));
  if (files.at(-1) !== ENTRY) throw new Error(`the chat-workspace script group must load the modules before ${ENTRY}`);
  return files;
}

// The chat workspace's scripts, by file name.
function chatWorkspaceSources(staticRoot = STATIC) {
  return Object.fromEntries(chatWorkspaceScripts(staticRoot).map(file => [
    file, fs.readFileSync(path.join(staticRoot, file), 'utf8'),
  ]));
}

// The only node of the chat workspace's scripts that `predicate` selects,
// with its file and source text. The predicate receives the node and its source.
function findChatWorkspaceNode(predicate, label, sources = chatWorkspaceSources()) {
  const { babelParse, traverse } = require('./babel_parse.cjs');
  const found = [];
  for (const [file, text] of Object.entries(sources)) {
    traverse(babelParse(text, file), { enter(p) {
      const source = text.slice(p.node.start, p.node.end);
      if (predicate(p.node, source)) found.push({ file, node: p.node, source });
    } });
  }
  if (found.length !== 1) throw new Error(`The chat workspace has ${found.length} ${label}`);
  return found[0];
}

// The declaration of a named function, for cases that run one function by
// itself. A nested helper of the same name never shadows the outermost one.
function findChatWorkspaceFunction(name, sources = chatWorkspaceSources()) {
  const { babelParse, traverse } = require('./babel_parse.cjs');
  for (const [file, text] of Object.entries(sources)) {
    let found = null;
    traverse(babelParse(text, file), { FunctionDeclaration(p) {
      if (p.node.id.name !== name) return;
      const depth = p.getAncestry().length;
      if (!found || depth < found.depth) found = { depth, node: p.node };
    } });
    if (found) return { file, node: found.node, source: text.slice(found.node.start, found.node.end) };
  }
  throw new Error(`No chat workspace function ${name}`);
}

module.exports = {
  STATIC, chatWorkspaceScripts, chatWorkspaceSources, findChatWorkspaceNode, findChatWorkspaceFunction,
};
