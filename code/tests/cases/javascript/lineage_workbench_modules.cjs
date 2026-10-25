// The lineage workbench takes its parts from module scripts through
// workbenchModule("<global>"). Tests load a part's script as lineage.html does
// and create the part with only the dependencies a case replaces.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createModulePart } = require('./module_parts.cjs');

const PORTAL = path.resolve(__dirname, '../../../management_portal');
const STATIC = path.join(PORTAL, 'static');
const read = file => fs.readFileSync(path.join(STATIC, file), 'utf8');
const registered = text => [...text.matchAll(/window\.([A-Za-z]+) = Object\.freeze\(/g)].map(match => match[1]);

// Module scripts the workbench asks for, in lineage.html's order.
function workbenchModuleFiles() {
  const globals = new Set([...read('lineage_workbench.js').matchAll(/workbenchModule\("([A-Za-z]+)"\)/g)]
    .map(match => match[1]));
  const page = fs.readFileSync(path.join(PORTAL, 'lineage.html'), 'utf8');
  const files = [...page.matchAll(/<script src="\/static\/(lineage_workbench_[a-z_]+\.js)">/g)].map(match => match[1])
    .filter(file => registered(read(file)).some(name => globals.has(name)));
  const loaded = new Set(files.flatMap(file => registered(read(file))));
  const missing = [...globals].filter(name => !loaded.has(name));
  if (missing.length) throw new Error(`lineage.html loads no script that registers ${missing.join(', ')}`);
  return files;
}

// The workbench and its module scripts, by file name.
function workbenchSources() {
  return Object.fromEntries(['lineage_workbench.js', ...workbenchModuleFiles()].map(file => [file, read(file)]));
}

// Run a module script with `window` as its global object and return what it registers.
function loadWorkbenchModule(file, window = {}) {
  const [name] = registered(read(file));
  vm.runInContext(read(file), vm.createContext({ window }), { filename: file });
  return window[name];
}

function createWorkbenchModule(module, factory, replaced = {}) {
  return createModulePart('workbench', module, factory, replaced);
}

// The declaration of a named function in the workbench closure or in the
// module script that owns it, for cases that run one function by itself. A
// nested helper of the same name never shadows the outermost declaration.
function findWorkbenchFunction(name, sources = workbenchSources()) {
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
  throw new Error(`No workbench function ${name}`);
}

// The lineage page as a browser loads it: lineage.html's DOM and every script
// it names, in order, against a fake Portal server. `answers` maps a request
// path to its JSON body and `instructions` maps an ISA instruction to its
// result; anything else answers an empty object. Every request is recorded,
// and every script, page or console error is collected. Each workbench part's
// factory is wrapped, so `parts[global]` holds what the workbench passed to
// the part and what the part returned.
function startWorkbenchPage({ answers = {}, instructions = {}, url = 'https://portal.fixture.invalid/lineage' } = {}) {
  const { JSDOM, VirtualConsole } = require('jsdom');
  const { execFileSync } = require('node:child_process');
  const html = fs.readFileSync(path.join(PORTAL, 'lineage.html'), 'utf8');
  const errors = [], requests = [];
  const log = new VirtualConsole();
  log.on('jsdomError', error => errors.push(String(error?.message || error)));
  log.on('error', (...args) => errors.push(args.map(String).join(' ')));
  const dom = new JSDOM(html.replace(/<script\b[^>]*\bsrc="[^"]*"[^>]*><\/script>/g, ''), {
    url, pretendToBeVisual: true, runScripts: 'outside-only', virtualConsole: log,
  });
  const w = dom.window;
  w.addEventListener('error', event => errors.push(String(event.error?.stack || event.message)));
  w.addEventListener('unhandledrejection', event => errors.push(String(event.reason?.stack || event.reason)));
  // Browser services JSDOM lacks; the page only needs them to exist here.
  const observer = class { observe() {} unobserve() {} disconnect() {} };
  Object.assign(w, {
    ResizeObserver: observer, IntersectionObserver: observer,
    EventSource: class extends w.EventTarget { close() {} },
    CSS: { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`), supports: () => false },
    matchMedia: media => Object.assign(new w.EventTarget(), { media, matches: false, addListener() {}, removeListener() {} }),
    // The server injects the site's name and links into each page.
    PortalSite: { apply() {} },
  });
  let closed = false;
  // A closed page runs no more microtasks; jsdom cannot report their errors.
  w.queueMicrotask = callback => queueMicrotask(() => {
    if (closed) return;
    try { callback(); } catch (error) { errors.push(String(error?.stack || error)); }
  });
  w.fetch = async (input, init = {}) => {
    // A closed page gets no more answers.
    if (closed) return new Promise(() => {});
    const at = new URL(String(input), w.location.href);
    let body = init.body ?? null;
    try { body = typeof body === 'string' ? JSON.parse(body) : body; } catch { /* keep the raw body */ }
    requests.push({ method: init.method || 'GET', path: at.pathname, search: at.search, body });
    const result = at.pathname === '/api/instructions/execute'
      ? { status: 'committed', result: instructions[body.instruction]?.(body.arguments) ?? {} }
      : (typeof answers[at.pathname] === 'function' ? answers[at.pathname](at, body) : answers[at.pathname]) ?? {};
    return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  // The server publishes the resource route contract as a generated script.
  const routes = execFileSync(process.env.TFI_TEST_PYTHON || 'python3', ['-c',
    'from management_portal.resources.portal import portal_route_script; print(portal_route_script().decode())'],
  { cwd: path.dirname(PORTAL), encoding: 'utf8' });
  const partFiles = new Set(workbenchModuleFiles());
  const parts = {};
  for (const [, file] of html.matchAll(/<script\b[^>]*\bsrc="\/static\/([^"]+)"/g)) {
    if (file.startsWith('vendor/')) continue;
    try { w.eval(file === 'portal_resource_routes.js' ? routes : `${read(file)}\n//# sourceURL=${file}`); }
    catch (error) { errors.push(`${file}: ${error?.stack || error}`); }
    if (!partFiles.has(file)) continue;
    for (const name of registered(read(file))) {
      const module = w[name];
      const factory = Object.keys(module).find(key => key.startsWith('create') && typeof module[key] === 'function');
      w[name] = Object.freeze({ ...module, [factory]: dependencies => {
        const exports = module[factory](dependencies);
        parts[name] = { dependencies, exports };
        return exports;
      } });
    }
  }
  async function until(predicate, timeout = 8000) {
    const end = Date.now() + timeout;
    while (!predicate()) {
      if (Date.now() > end) throw new Error(`The page did not reach the expected state; errors: ${errors.join('\n')}`);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  }
  // The page releases its subscriptions on pagehide, as when a browser leaves it.
  async function close() {
    w.dispatchEvent(new w.Event('pagehide'));
    closed = true;
    await new Promise(resolve => setTimeout(resolve, 50));
    w.close();
  }
  return { window: w, document: w.document, requests, errors, parts, until, close };
}

module.exports = {
  STATIC, workbenchModuleFiles, workbenchSources, loadWorkbenchModule, createWorkbenchModule, findWorkbenchFunction,
  startWorkbenchPage,
};
