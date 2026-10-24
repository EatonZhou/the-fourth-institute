// The HATG panel's runtime takes its parts from module scripts through
// hatgModule("<global>"). Tests load those scripts as production does and
// create a part with only the dependencies a case replaces.
const fs = require('node:fs');
const path = require('node:path');

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

// A dependency the case does not replace fails loudly when it is used.
function unexpected(name) {
  const fail = () => { throw new Error(`Unexpected use of HATG dependency ${name}`); };
  return new Proxy(function unexpectedDependency() {}, {
    apply: fail, construct: fail,
    get(_target, key) { if (key === Symbol.toPrimitive || key === 'then') return undefined; return fail(); },
    set: fail,
  });
}

function createHatgModule(module, factory, replaced = {}) {
  const extra = Object.keys(replaced).filter(name => !module.dependencies.includes(name));
  if (extra.length) throw new Error(`${factory} does not depend on ${extra.join(', ')}`);
  const dependencies = Object.fromEntries(module.dependencies.map(name => [
    name, Object.hasOwn(replaced, name) ? replaced[name] : unexpected(name),
  ]));
  return module[factory](dependencies);
}

// Module parts receive runtime functions when the runtime starts, so a case
// that replaces a runtime function rewrites its declaration in the source:
// every part then calls the replacement. Each replacement is an expression
// evaluated in the runtime scope at call time.
function overrideRuntimeFunctions(source, overrides) {
  const { babelParse } = require('./babel_parse.cjs');
  const runtime = babelParse(source, 'hatg_panel.js').program.body[0].expression.callee.body.body
    .find(node => node.type === 'FunctionDeclaration' && node.id.name === 'createHatgRuntime');
  const edits = Object.entries(overrides).map(([name, replacement]) => {
    const node = runtime.body.body.find(item => item.type === 'FunctionDeclaration' && item.id.name === name);
    if (!node) throw new Error(`No runtime function ${name}`);
    return [node.start, node.end, `function ${name}(...args) { return (${replacement})(...args); }`];
  }).sort((a, b) => b[0] - a[0]);
  return edits.reduce((text, [start, end, value]) => text.slice(0, start) + value + text.slice(end), source);
}

module.exports = { STATIC, hatgModuleFiles, readHatgModule, createHatgModule, overrideRuntimeFunctions };
