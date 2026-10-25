// A page's parts are module scripts whose factory names every dependency it
// takes. Tests create a part with only the dependencies a case replaces.

// A dependency the case does not replace fails loudly when it is used.
function unexpected(owner, name) {
  const fail = () => { throw new Error(`Unexpected use of ${owner} dependency ${name}`); };
  return new Proxy(function unexpectedDependency() {}, {
    apply: fail, construct: fail,
    get(_target, key) { if (key === Symbol.toPrimitive || key === 'then') return undefined; return fail(); },
    set: fail,
  });
}

function createModulePart(owner, module, factory, replaced = {}) {
  const extra = Object.keys(replaced).filter(name => !module.dependencies.includes(name));
  if (extra.length) throw new Error(`${factory} does not depend on ${extra.join(', ')}`);
  const dependencies = Object.fromEntries(module.dependencies.map(name => [
    name, Object.hasOwn(replaced, name) ? replaced[name] : unexpected(owner, name),
  ]));
  return module[factory](dependencies);
}

module.exports = { createModulePart };
