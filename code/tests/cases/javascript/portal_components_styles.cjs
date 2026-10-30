// The shared component style sheets as names under /static/ without ".css", in
// the order index.html loads them: portal_components.css, then its parts.
// Every page that loads portal_components.css loads the parts right after it.
const fs = require('node:fs');
const path = require('node:path');

const PORTAL = path.resolve(__dirname, '../../../management_portal');
const page = fs.readFileSync(path.join(PORTAL, 'index.html'), 'utf8');

const PORTAL_COMPONENTS_STYLES = Object.freeze(
  [...page.matchAll(/href="\/static\/(portal_components(?:_[a-z_]+)?)\.css"/g)].map(match => match[1]),
);

module.exports = { PORTAL_COMPONENTS_STYLES };
