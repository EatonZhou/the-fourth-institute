// The Lineage graph's scripts as names under /static/ without ".js", in the
// order portal_hatg_loader.js loads them: the part modules, then
// lineage_graph.js, which creates the parts.
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const loader = fs.readFileSync(path.join(STATIC, 'portal_hatg_loader.js'), 'utf8');

const LINEAGE_GRAPH_SCRIPTS = Object.freeze(
  [...loader.matchAll(/"\/static\/(lineage_graph(?:_[a-z_]+)?)\.js"/g)].map(match => match[1]),
);

module.exports = { LINEAGE_GRAPH_SCRIPTS };
