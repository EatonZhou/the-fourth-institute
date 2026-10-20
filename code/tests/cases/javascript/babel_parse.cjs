// The parser the Node-only source tests use to read production browser
// scripts: no browser, runner or DOM implementation.
const { parse } = require('@babel/parser');
const traverse = require('@babel/traverse').default;

function babelParse(code, fileName) {
  return parse(code, { sourceType: 'unambiguous', sourceFilename: fileName });
}

module.exports = { babelParse, traverse };
