// Home embeds the native Preview actions, without a parallel disclosure menu.
module.exports = async function homeAction(page, name) {
  const names = {'返回上一展示':'返回上一个预览文件','固定展示':'锁定当前文件','取消固定':'解除当前文件锁定','展开':'在大视图打开当前文件','返回原布局':'在大视图打开当前文件'};
  const preview=page.locator('.homeDialoguePreview');
  if (!await preview.isVisible()) await page.locator('.homeDialogueTabs').getByRole('button',{name:'展示',exact:true}).click();
  if (name==='关闭当前展示') return preview.locator('.portalDocumentTab.is-active .portalDocumentTabClose').click();
  await preview.getByRole('button',{name:names[name]||name,exact:true}).click();
};
