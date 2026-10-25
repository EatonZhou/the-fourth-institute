// The HATG panel's runtime takes its parts from module scripts through
// hatgModule("<global>"). Tests load those scripts as production does and
// create a part with only the dependencies a case replaces.
const fs = require('node:fs');
const path = require('node:path');
const { createModulePart } = require('./module_parts.cjs');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');

// Module scripts the runtime asks for, in the loader's order.
function hatgModuleFiles() {
  const panel = fs.readFileSync(path.join(STATIC, 'hatg_panel.js'), 'utf8');
  const globals = new Set([...panel.matchAll(/hatgModule\("([A-Za-z]+)"\)/g)].map(match => match[1]));
  const loader = fs.readFileSync(path.join(STATIC, 'portal_hatg_loader.js'), 'utf8');
  const files = [...loader.matchAll(/"\/static\/(hatg_[a-z_]+\.js)"/g)].map(match => match[1]);
  const modules = files.filter(file => {
    const source = fs.readFileSync(path.join(STATIC, file), 'utf8');
    return [...globals].some(name => source.includes(`window.${name} = Object.freeze(`));
  });
  const registered = modules.flatMap(file => [...globals].filter(name => (
    fs.readFileSync(path.join(STATIC, file), 'utf8').includes(`window.${name} = Object.freeze(`)
  )));
  const missing = [...globals].filter(name => !registered.includes(name));
  if (missing.length) throw new Error(`No loader script registers ${missing.join(', ')}`);
  return modules;
}

function readHatgModule(file) {
  return fs.readFileSync(path.join(STATIC, file), 'utf8');
}

function createHatgModule(module, factory, replaced = {}) {
  return createModulePart('HATG', module, factory, replaced);
}

// The runtime and its module scripts, by file name.
function hatgSources() {
  return Object.fromEntries(['hatg_panel.js', ...hatgModuleFiles()].map(file => [file, readHatgModule(file)]));
}

// The declaration of a named function in the runtime or in the module script
// that owns it.
function findHatgFunction(sources, name) {
  const { babelParse, traverse } = require('./babel_parse.cjs');
  for (const [file, text] of Object.entries(sources)) {
    const ast = babelParse(text, file);
    let node = null;
    if (file === 'hatg_panel.js') {
      const runtime = ast.program.body[0].expression.callee.body.body
        .find(item => item.type === 'FunctionDeclaration' && item.id.name === 'createHatgRuntime');
      node = runtime.body.body.find(item => item.type === 'FunctionDeclaration' && item.id.name === name) || null;
    } else {
      traverse(ast, { FunctionDeclaration(p) {
        if (p.node.id.name === name) { node = p.node; p.stop(); }
      } });
    }
    if (node) return { file, node, source: text.slice(node.start, node.end) };
  }
  throw new Error(`No HATG function ${name}`);
}

// Module parts receive runtime functions when the runtime starts, so a case
// that replaces a function rewrites its declaration in the runtime or in the
// module script that owns it: every caller then reaches the replacement. Each
// replacement is an expression evaluated in its owner's scope at call time.
function overrideHatgFunctions(sources, overrides) {
  const result = { ...sources };
  for (const [name, replacement] of Object.entries(overrides)) {
    const target = findHatgFunction(result, name);
    const text = result[target.file];
    result[target.file] = text.slice(0, target.node.start)
      + `function ${name}(...args) { return (${replacement})(...args); }` + text.slice(target.node.end);
  }
  return result;
}

// Create the real runtime in a vm context without mounting it. The context
// already holds the scripts the panel needs before its own. Each part's factory
// is wrapped, so the case reaches what the runtime passed to a part and what
// the part returned: get(name) finds a part's export, or a runtime value that
// some part depends on, such as state. A value the runtime declares itself and
// passes to no part is reached when the case lists it in `runtimeNames`; a
// listed name the runtime does not declare must be a part's export.
function startHatgRuntime(context, sources = hatgSources(), runtimeNames = []) {
  const vm = require('node:vm');
  const { babelParse } = require('./babel_parse.cjs');
  let runtimeSource = sources['hatg_panel.js'];
  if (runtimeNames.length) {
    const runtime = babelParse(runtimeSource, 'hatg_panel.js').program.body[0].expression.callee.body.body
      .find(node => node.type === 'FunctionDeclaration' && node.id.name === 'createHatgRuntime');
    const declared = new Set(runtime.body.body.flatMap(node => (
      node.type === 'FunctionDeclaration' ? [node.id.name]
        : node.type === 'VariableDeclaration' ? node.declarations.filter(item => item.id.type === 'Identifier').map(item => item.id.name)
          : []
    )));
    const returned = runtime.body.body.find(node => node.type === 'ReturnStatement');
    runtimeSource = runtimeSource.slice(0, returned.start)
      + `window.__hatgRuntimeScope = { ${runtimeNames.filter(name => declared.has(name)).join(', ')} };\n`
      + runtimeSource.slice(returned.start);
  }
  const globals = [...new Set([...runtimeSource.matchAll(/hatgModule\("([A-Za-z]+)"\)/g)].map(match => match[1]))];
  for (const [file, text] of Object.entries(sources)) {
    if (file !== 'hatg_panel.js') vm.runInContext(text, context, { filename: file });
  }
  const parts = {};
  for (const name of globals) {
    const module = context[name];
    if (!module) throw new Error(`${name} is not loaded`);
    const factory = Object.keys(module).find(key => key.startsWith('create') && typeof module[key] === 'function');
    context[name] = Object.freeze({ ...module, [factory]: dependencies => {
      const exports = module[factory](dependencies);
      parts[name] = { dependencies, exports };
      return exports;
    } });
  }
  const outer = babelParse(runtimeSource, 'hatg_panel.js').program.body[0].expression.callee.body;
  vm.runInContext(runtimeSource.slice(0, outer.end - 1) + '\nwindow.__createHatgRuntime = createHatgRuntime;\n'
    + runtimeSource.slice(outer.end - 1), context, { filename: 'hatg_panel.js' });
  const runtime = context.__createHatgRuntime();
  const scope = context.__hatgRuntimeScope || {};
  const get = name => {
    for (const part of Object.values(parts)) if (Object.hasOwn(part.exports, name)) return part.exports[name];
    for (const part of Object.values(parts)) if (Object.hasOwn(part.dependencies, name)) return part.dependencies[name];
    if (Object.hasOwn(scope, name)) return scope[name];
    throw new Error(`No HATG part exports or depends on ${name}`);
  };
  return { runtime, parts, get, state: get('state') };
}

module.exports = {
  STATIC, hatgModuleFiles, hatgSources, readHatgModule, createHatgModule, findHatgFunction,
  overrideHatgFunctions, startHatgRuntime,
};
