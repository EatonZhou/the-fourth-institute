// The Lineage panel's style sheets as names under /static/ without ".css", in
// the order lineage.html loads them: hatg_panel.css, then its parts.
const fs = require('node:fs');
const path = require('node:path');

const PORTAL = path.resolve(__dirname, '../../../management_portal');
const page = fs.readFileSync(path.join(PORTAL, 'lineage.html'), 'utf8');

const HATG_PANEL_STYLES = Object.freeze(
  [...page.matchAll(/href="\/static\/(hatg_panel(?:_[a-z_]+)?)\.css"/g)].map(match => match[1]),
);

module.exports = { HATG_PANEL_STYLES };
