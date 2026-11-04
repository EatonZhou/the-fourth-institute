// Native Home display actions live in its existing More disclosure.
module.exports = async function homeAction(page, name) {
  const summary = page.locator('.homeDialogueMore > summary');
  if (!await summary.isVisible()) await page.locator('.homeDialogueTabs').getByRole('button', {name:'展示',exact:true}).click();
  if (!await summary.evaluate(el => el.parentElement.open)) await summary.click();
  await page.getByRole('button', {name,exact:true}).click();
};
