module.exports=async(page,assert)=>{
 const decision='decision_d05e95850311';
 const workspace='workspace_0123456789ab/file/notes.md';
 const types=await page.evaluate(({decision,workspace})=>{
  const normalize=value=>PortalPreviewContract.normalizeRequest({path:value,href:value}).resourceAddress;
  return [normalize(decision),normalize(workspace),normalize('/r/'+decision),normalize('https://example.com/'+decision),normalize('/tmp/'+decision)];
 },{decision,workspace});
 assert.deepEqual(types,[decision,workspace,decision,'','']);
 await page.evaluate(()=>PortalPreview.request({resourceAddress:'article_0123456789ab'}));
 await page.locator('.portalFileDocument:visible').waitFor();
 async function enter(value){
  await page.locator('.homeDialoguePreview [data-portal-preview-path]').click();
  const input=page.getByRole('textbox',{name:'输入文件路径或资源 ID，回车打开',exact:true});
  await input.fill(value);await input.press('Enter');
 }
 await enter(decision);
 await page.waitForFunction(id=>previewCalls.some(c=>c.address===id),decision);
 await page.locator('.portalFileDocument:visible').waitFor();
 assert.equal(await page.locator('.homeDialoguePreview [data-portal-document-tab]').count(),1);
 assert.equal(await page.locator('.homeDialoguePreview .portalPreviewRemoteLink').count(),0);
 await enter(workspace);
 await page.waitForFunction(id=>previewCalls.some(c=>c.address===id),workspace);
 await page.evaluate(()=>window.deny=true);
 await enter(decision);
 await page.getByText('当前来源不可读',{exact:true}).first().waitFor();
 assert.equal(await page.locator('.homeDialoguePreview [data-portal-document-tab]').count(),1,'denied resource never adds a successful tab');
 console.log('Native address input routes bare/nested IDs and /r links to resources; external URLs/files stay distinct; source denial retained');
};
