'use strict';
const assert = require('node:assert/strict');
module.exports = async ({page, releaseCatalog, errors}) => {
  page.setDefaultTimeout(5000);
  await page.getByRole('button',{name:'Show HATG',exact:true}).click();
  const panel=page.getByRole('region',{name:'HATG',exact:true});
  await panel.locator('[data-node-id]').waitFor({state:'visible'});
  await page.waitForFunction(()=>!document.querySelector('.portalHatgPanelComponent[aria-busy="true"]'));
  await panel.getByRole('button',{name:'Fit all HATG nodes',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('hatgCanvas')?.dataset.zoom==='1.000');
  const before=await panel.locator('#hatgCanvas').getAttribute('data-zoom');
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/agent-workbench/favorites');
  releaseCatalog();
  await response;
  // Delayed secondary catalogs complete the native Workbench initialization.
  // Allow its 240ms camera animation to finish; this is an isolated regression,
  // not a fabricated readiness deadline for a real TAU action.
  await page.waitForTimeout(700);
  const after=await panel.locator('#hatgCanvas').getAttribute('data-zoom');
  console.log(JSON.stringify({before,after,errors}));
  assert.equal(after,before,'late sidebar catalogs must preserve the user Fit after the graph is ready');
  assert.deepEqual(errors,[]);
};
