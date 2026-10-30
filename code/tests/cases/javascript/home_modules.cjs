// Home is home.js and the part scripts index.html loads before it (home_*.js);
// each part declares its code inside its factory, and home.js creates the
// parts. Node-only cases that run a few of Home's declarations by themselves
// take them from whichever file declares them.
const fs = require('node:fs');
const path = require('node:path');
const { babelParse } = require('./babel_parse.cjs');

const PORTAL = path.resolve(__dirname, '../../../management_portal');

// home.js and its part scripts, in the order index.html loads them.
function homeFiles(portal = PORTAL) {
  const page = fs.readFileSync(path.join(portal, 'index.html'), 'utf8');
  return [...page.matchAll(/<script src="\/static\/(home(?:_[a-z_]+)?\.js)"><\/script>/g)].map(match => match[1]);
}

// The statements of a part's factory: the part's own declarations.
function factoryStatements(ast, file) {
  const iife = ast.program.body[0]?.expression?.callee;
  const factory = iife?.body?.body?.find(node => node.type === 'FunctionDeclaration' && node.id.name.startsWith('create'));
  if (!factory) throw new Error(`${file} declares no part factory`);
  return factory.body.body;
}

// Home's top-level statements: home.js's own and each part factory's, as
// { file, node, source } with `source` the statement's text. `portal` is the
// directory holding index.html and static/.
function homeStatements(portal = PORTAL) {
  return homeFiles(portal).flatMap(file => {
    const text = fs.readFileSync(path.join(portal, 'static', file), 'utf8');
    const ast = babelParse(text, file);
    const body = file === 'home.js' ? ast.program.body : factoryStatements(ast, file);
    return body.map(node => ({ file, node, source: text.slice(node.start, node.end) }));
  });
}

module.exports = { homeFiles, homeStatements };
