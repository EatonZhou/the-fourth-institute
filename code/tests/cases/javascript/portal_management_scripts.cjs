// The Portal Management panel's scripts as file names under /static/, in the
// order portal_management.html loads them: the part modules, then
// portal_management.js, which creates the parts.
const fs = require('node:fs');
const path = require('node:path');

const PAGE = path.resolve(__dirname, '../../../management_portal/portal_management.html');
const page = fs.readFileSync(PAGE, 'utf8');

const PORTAL_MANAGEMENT_SCRIPTS = Object.freeze(
  [...page.matchAll(/"\/static\/(portal_management(?:_[a-z_]+)?\.js)"/g)].map(match => match[1]),
);

module.exports = { PORTAL_MANAGEMENT_SCRIPTS };
