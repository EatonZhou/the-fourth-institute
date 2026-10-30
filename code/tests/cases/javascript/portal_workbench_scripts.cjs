// The workbench is portal_workbench.js together with the part scripts
// (portal_workbench_<part>.js) whose factories it creates each workbench and
// activity rail from. Tests load the same scripts in the same order as pages,
// and look a declaration up in whichever script holds it.
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const ROOT_SCRIPT = 'portal_workbench.js';

// The workbench's scripts in the order pages load them: the "portal-workbench"
// group of script_groups.json. A case that runs a copy of the code passes that
// copy's static directory.
function portalWorkbenchScripts(staticRoot = STATIC) {
  const groups = JSON.parse(fs.readFileSync(path.join(staticRoot, '..', 'script_groups.json'), 'utf8'));
  const files = groups['portal-workbench'].map(source => source.replace(/^\/static\//, ''));
  if (files.at(-1) !== ROOT_SCRIPT) throw new Error(`the portal-workbench script group must load the parts before ${ROOT_SCRIPT}`);
  return files;
}

// The workbench's scripts, by file name.
function portalWorkbenchSources(staticRoot = STATIC) {
  return Object.fromEntries(portalWorkbenchScripts(staticRoot).map(file => [
    file, fs.readFileSync(path.join(staticRoot, file), 'utf8'),
  ]));
}

module.exports = { STATIC, ROOT_SCRIPT, portalWorkbenchScripts, portalWorkbenchSources };
