// The Resident Menu's scripts as names under /static/ without ".js", in the
// order portal_nav.js loads them to open a Resident Menu: the part modules,
// then portal_resident_menu.js, which creates the parts.
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const nav = fs.readFileSync(path.join(STATIC, 'portal_nav.js'), 'utf8');

const RESIDENT_MENU_SCRIPTS = Object.freeze(
  [...nav.matchAll(/\["(portal_resident_menu(?:_[a-z_]+)?)", "PortalResidentMenu[A-Za-z]*"\]/g)].map(match => match[1]),
);

// The Menu's sources joined in load order, for cases that read a function
// from whichever script declares it.
function residentMenuSource() {
  return RESIDENT_MENU_SCRIPTS.map(name => fs.readFileSync(path.join(STATIC, `${name}.js`), 'utf8')).join('\n');
}

module.exports = { RESIDENT_MENU_SCRIPTS, residentMenuSource };
