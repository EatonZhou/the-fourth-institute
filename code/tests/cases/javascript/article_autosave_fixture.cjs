'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { STATIC, portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');

function articleAutosaveFixture() {
  const timers = new Map(), requests = [], notices = [], errors = [], builds = [];
  let timerId = 0;
  const window = {
    PortalI18n: { t: value => value, text: (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] ?? ''), '') },
    PortalUIPrimitives: { escapeHtml: value => value },
    PortalUI: { toast: (...args) => errors.push(args), confirm: async () => true },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout: id => timers.delete(id), requestAnimationFrame() {},
  };
  const context = vm.createContext({ window, console });
  const run = name => vm.runInContext(fs.readFileSync(path.join(STATIC, name), 'utf8'), context);
  portalWorkspaceSurfaceScripts().forEach(run);
  window.PortalWorkspaceSurface = { ...window.PortalWorkspaceSurface, renderDocumentTabs() {} };
  run('article_workspace_documents.js'); run('article_workspace_syntax.js');
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, { value: '', textContent: '', dataset: {}, querySelector: () => null });
    return elements.get(id);
  };
  const state = { selectedId: 'A', openFile: 'main.tex', source: 'old', savedSource: 'old',
    documents: new Map(), openTabs: ['main.tex'], settings: { auto_compile: false } };
  const session = window.PortalDocumentSession.create({ continuousEdit: true, canEdit: () => true });
  session.present({ content: 'old', path: 'main.tex' });
  const doc = { path: 'main.tex', editable: true, textDocument: true, source: 'old', savedSource: 'old', session };
  state.documents.set('main.tex', doc); $('articleSourceEditor').value = 'old';
  const deps = module => Object.fromEntries(module.dependencies.map(key => [key, () => {}]));
  let syntax;
  const documents = window.PortalArticleWorkspaceDocuments.createDocuments({ ...deps(window.PortalArticleWorkspaceDocuments),
    $, state, editorValue: () => $('articleSourceEditor').value,
    updateDirtyState: () => syntax.updateDirtyState(),
    compileArticle: options => builds.push(options),
    detailResourceGuest: { changed: value => notices.push(value) }, loadSemanticIndex: async () => {},
    request: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  });
  syntax = window.PortalArticleWorkspaceSyntax.createSyntax({ ...deps(window.PortalArticleWorkspaceSyntax), $, state,
    activeDocument: documents.activeDocument, renderDocumentTabs: documents.renderDocumentTabs,
  });
  const edit = value => { state.source = value; $('articleSourceEditor').value = value; syntax.updateDirtyState(); };
  const flush = async () => { const current = [...timers.values()]; timers.clear(); current.forEach(t => t.fn()); await tick(); };
  return { window, documents, state, doc, session, $, syntax, edit, flush, timers, requests, notices, errors, builds };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
module.exports = { articleAutosaveFixture, tick };
