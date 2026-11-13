// Real Paper Chat owner and Chat factory. Only remote conversation content is
// synthetic; the Reader borrows the original DOM, target, history and draft.
const fs=require('node:fs'),path=require('node:path');
const {chatWorkspaceScripts}=require('./chat_workspace_modules.cjs');
const {portalWorkbenchScripts}=require('./portal_workbench_scripts.cjs');
const root=path.resolve(__dirname,'../../..'),staticDir=path.join(root,'management_portal/static');
async function installReaderChat(page){
 for(const [,file] of fs.readFileSync(path.join(root,'management_portal/paper.html'),'utf8').matchAll(/href="\/static\/([^\"]+\.css)"/g))await page.addStyleTag({path:path.join(staticDir,file)});
 await page.evaluate(()=>{
  window.PortalIdentity={username:()=> 'reader_one'};
  window.PortalEvents={isConnected:()=>false,connect(){}};
 });
 await page.addScriptTag({content:fs.readFileSync(process.env.READER_ROUTES,'utf8')});
 const scripts=['portal_state.js','portal_page_state.js','portal_text.js','portal_ui_primitives.js','portal_ui.js','portal_overlay.js','portal_resource_chips.js','portal_image_editor.js','portal_editor_open_service.js','portal_command_registry.js','portal_workbench_context.js','portal_api.js','portal_instruction_client.js','portal_resident_service.js','resident_protocol.js','resident_projection.js','portal_resident_runtime_controller.js',...portalWorkbenchScripts(),...chatWorkspaceScripts(),'paper_chat.js'];
 for(const file of [...new Set([...scripts,'paper_research_controls.js'])])await page.addScriptTag({path:path.join(staticDir,file)});
 await page.evaluate(()=>{
  const home=document.createElement('section');home.id='ordinaryChat';home.className='portalWorkbenchPanelFrame';
  home.style.cssText='position:fixed;right:0;top:40px;width:320px;height:600px;font:14px sans-serif';
  home.innerHTML='<section id="sharedChat" data-portal-workbench-panel></section>';document.body.append(home);
  window.chatState={principalId:'reader_one',username:'reader_one',selectedId:'paper_0123456789ab',chat:[{id:'msg_0123456789abcdef0123456789abcdef',role:'assistant',content:'合成回答：仅当前论文选区。'}]};
  const dependencies=Object.fromEntries(PortalPaperChat.dependencies.map(k=>[k,()=>{}]));
  window.chatCalls=[];
  Object.assign(dependencies,{$:id=>document.getElementById(id),state:chatState,esc:String,md:String,
   currentPaperWorkbench:()=>({isPanelVisible:()=>true,isPanelCollapsed:()=>false}),
   paperApi:{request:async()=>{throw Error('Reader must borrow Chat without preparing a new conversation');}}});
  window.chatOwner=PortalPaperChat.createChat(dependencies);
  const descriptor=chatOwner.paperChatDescriptor();
  window.sharedPanel=PortalChatWorkspace.panelPackage.create({...descriptor.options,root:document.getElementById('sharedChat')});
  descriptor.onShow({panel:{runtime:sharedPanel}});
  sharedPanel.workspace.applyContract({composer:{can_send:true,can_edit:true},messages:chatState.chat,rendering:{empty_text:'Synthetic existing discussion'}});
  sharedPanel.workspace.setDraftText('Existing draft');
  window.sharedChatRoot=sharedPanel.workspace.root;
  window.chatSurface={surface:'paper',workspace_key:'paper:paper_0123456789ab'};
  sharedPanel.surface=()=>chatSurface;
  sharedPanel.send=async payload=>{chatCalls.push(payload.text);};
  Object.assign(readerChat,{width:()=>chatOwner.paperChatWidth(320),mount:node=>chatOwner.mountChat(node),addSelection:text=>chatOwner.addSelectionToChatDraft(text),answers:()=>chatOwner.selectionAnswers()});
 });
}
module.exports={installReaderChat};
