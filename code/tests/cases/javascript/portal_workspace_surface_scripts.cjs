// The workspace surface is portal_workspace_surface.js together with the part
// scripts (portal_workspace_surface_<part>.js) whose factories it creates its
// Preview panels, workspace browsers, document sessions, document tabs and
// selection controllers from. Tests load the same scripts in the same order as
// pages.
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const ROOT_SCRIPT = 'portal_workspace_surface.js';

// The workspace surface's scripts in the order pages load them: the
// "portal-workspace-surface" group of script_groups.json. A case that runs a
// copy of the code passes that copy's static directory.
function portalWorkspaceSurfaceScripts(staticRoot = STATIC) {
  const groups = JSON.parse(fs.readFileSync(path.join(staticRoot, '..', 'script_groups.json'), 'utf8'));
  const files = groups['portal-workspace-surface'].map(source => source.replace(/^\/static\//, ''));
  if (files.at(-1) !== ROOT_SCRIPT) throw new Error(`the portal-workspace-surface script group must load the parts before ${ROOT_SCRIPT}`);
  return files;
}

module.exports = { STATIC, ROOT_SCRIPT, portalWorkspaceSurfaceScripts };
