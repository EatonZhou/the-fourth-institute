"""Focused real-browser regression actions, using the existing owner fixture."""

def verify_interactions(page, http, selected, actor, tmp_path, support):
    # These are the real production endpoints; only the model transport in the
    # surrounding fixture is synthetic. Never manufacture prepare/panel replies.
    rid=selected['resident_id']
    page.once('dialog',lambda dialog:dialog.accept('Renamed persistent assistant'))
    page.get_by_role('button',name='重命名',exact=True).click()
    page.wait_for_function("document.querySelector('.paperResearchSelector option:checked').textContent==='Renamed persistent assistant'")
    assert page.evaluate('researchChat.target().resident_id')==rid
    assert 'Renamed persistent assistant' in page.locator('#research .paneTitle').inner_text()
    catalog=page.evaluate("async()=>{const r=await fetch('/paper/api/research/chats');if(!r.ok)throw Error('catalog '+r.status);return r.json()}")
    assert catalog['selected']['title']=='Renamed persistent assistant'
    assert catalog['selected']['slot_id']==selected['slot_id']
    # Explicitly create a legacy single-paper discussion through its old public
    # prepare contract, then select it through the same visible control.
    legacy='paper:paper_0123456789ab'
    result=page.evaluate('''async key=>{let r=await fetch('/chat-workspace/api/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({surface:'paper',workspace_key:key,message:'Legacy setup',context:{paper_id:'paper_0123456789ab'}})});return {status:r.status,body:await r.json()}}''',legacy)
    assert result['status']==200,result
    # Reload the catalog via loadMe; the identity leaf stays synthetic, owner HTTP stays real.
    page.evaluate('''actor=>{window.PortalUI={...PortalUI,loadCurrentUser:async()=>({ok:true,data:{username:actor,principal:{id:actor}}})};}''',actor)
    page.evaluate('research.loadMe()')
    page.wait_for_function("Array.from(document.querySelector('.paperResearchSelector select').options).some(o=>o.value==='legacy:paper:paper_0123456789ab')")
    page.get_by_label('选择研究助手',exact=True).select_option('legacy:'+legacy)
    page.wait_for_function("researchState.researchLegacyKey==='paper:paper_0123456789ab'")
    assert page.evaluate("async()=>{const r=await fetch('/paper/api/research/chats');if(!r.ok)throw Error('catalog '+r.status);return r.json()}")['selected_legacy']['workspace_key']==legacy
    page.evaluate('''async actor=>{
      const {openReader}=await import('/static/paper_fullscreen_reader.js');
      const box=document.createElement('div');box.id='syntheticPdfCard';box.style.cssText='width:900px;height:520px';document.body.append(box);
      window.readerHost=openReader({paper:{id:'paper_0123456789ab',title:'Synthetic Paper',pdf_url:'/unused.pdf'},principal:actor,container:()=>box,isCurrent:()=>PortalIdentity.username()===actor,onReady:s=>signals.push(s.paperId)});
    }''',actor)
    page.wait_for_function('signals.length===1')
    frame=page.frames[1]
    identity=page.evaluate('readerHost.instanceId')
    page.evaluate('readerHost.setFullscreen(true)')
    page.get_by_role('button',name='打开 AI',exact=True).click()
    page.wait_for_selector('.paperAIChat [role=textbox][contenteditable=true]')
    page.locator('.paperAIChat [role=textbox]').fill('Editable legacy draft')
    # Native layout toggle reuses the same iframe and keeps its draft.
    page.evaluate('readerHost.setFullscreen(false)');page.evaluate('readerHost.setFullscreen(true)')
    assert page.evaluate('readerHost.instanceId')==identity
    assert page.locator('.paperAIChat [role=textbox]').inner_text()=='Editable legacy draft'
    # Select research while the Reader already exists. Event invalidates the
    # old AI target, never the Reader or its PDF worker.
    page.evaluate('readerHost.setFullscreen(false)')
    page.get_by_label('选择研究助手',exact=True).select_option(selected['slot_id'])
    page.wait_for_function('id=>researchState.researchSlot?.slot_id===id',arg=selected['slot_id'])
    page.evaluate('readerHost.setFullscreen(true)')
    page.get_by_role('button',name='打开 AI',exact=True).click()
    page.wait_for_selector('.paperAIChat [role=textbox][contenteditable=true]')
    assert 'Renamed persistent assistant' in page.locator('.paperAIChat .paneTitle').inner_text()
    assert page.locator('.paperAIChat [role=textbox]').inner_text()==''
    assert page.evaluate('readerHost.instanceId')==identity
    editor=page.locator('.paperAIChat [role=textbox]')
    editor.fill('Keep draft after rejected context')
    def reject(route):
        route.fulfill(status=403,content_type='application/json',body='{"error":{"message":"Synthetic revoked material"}}')
    page.route('**/chat-workspace/api/prepare',reject)
    editor.press('Control+Enter')
    page.get_by_role('button',name='重新连接讨论',exact=True).wait_for()
    page.wait_for_function('document.querySelector(".paperAIChat [role=textbox]")?.innerText==="Keep draft after rejected context"')
    assert editor.inner_text()=='Keep draft after rejected context'
    assert editor.get_attribute('contenteditable')=='true'
    assert not support.transport.turns
    # A failed recovery remains editable, cannot send; another explicit recovery
    # uses current official ACL/version/target and never auto-resends the draft.
    page.get_by_role('button',name='重新连接讨论',exact=True).click()
    page.wait_for_function('document.querySelector(".paperAIChat [role=textbox]")?.getAttribute("contenteditable")=="true"')
    page.wait_for_function('document.querySelector(".paperAIChat [role=textbox]")?.innerText==="Keep draft after rejected context"')
    assert editor.inner_text()=='Keep draft after rejected context'
    page.unroute('**/chat-workspace/api/prepare',reject)
    page.get_by_role('button',name='重新连接讨论',exact=True).click()
    page.wait_for_function('!document.querySelector(".paperAIPanel [role=alert]")')
    page.wait_for_function('document.querySelector(".paperAIChat [role=textbox]")?.innerText==="Keep draft after rejected context"')
    assert editor.inner_text()=='Keep draft after rejected context'
    assert not support.transport.turns
    with page.expect_response(lambda response:response.url.endswith('/api/instructions/execute') and response.request.post_data_json.get('instruction')=='DeliverMessage') as sent:
        editor.press('Control+Enter')
    assert sent.value.json()['status']=='committed'
    result=support.run_until_idle(rid,timeout=15)
    assert result['input_state']['state']=='completed'
    assert len(support.transport.turns)==1
    page.screenshot(path=str(tmp_path/'research-rename-selected-reader.png'))
    page.evaluate('readerHost.close()')


def verify_identity_and_late_rename(page, http, selected, actor, tmp_path, support):
    """Additional regressions only: collection identity and legacy title updates."""
    page.evaluate('''actor=>{window.PortalUI={...PortalUI,loadCurrentUser:async()=>({ok:true,data:{username:actor,principal:{id:actor}}})};}''',actor)
    # Former UI request shape reproduces the reported exact backend rejection.
    invalid=page.evaluate('''async()=>{const r=await fetch('/chat-workspace/api/session?surface=paper&workspace_key=library&paper_id=paper_0123456789ab');return {status:r.status,body:await r.json()}}''')
    assert invalid['status']==400 and 'workspace_key does not match paper_id' in str(invalid['body']),invalid
    created=page.evaluate('''async()=>{const r=await fetch('/chat-workspace/api/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({surface:'paper',workspace_key:'library',message:'Explicit synthetic collection',context:{collection_label:'Synthetic collection'}})});return {status:r.status,body:await r.json()}}''')
    assert created['status']==200,created
    legacy_rid=created['body']['resident_id']
    page.evaluate('research.loadMe()')
    page.wait_for_function("Array.from(document.querySelector('.paperResearchSelector select').options).some(o=>o.value==='legacy:library')")
    page.get_by_label('选择研究助手',exact=True).select_option('legacy:library')
    page.wait_for_function('id=>researchChat.target().resident_id===id',arg=legacy_rid)
    assert page.evaluate('researchChat.surface().paper_id')==''
    assert page.evaluate('researchState.selectedId')=='paper_0123456789ab'
    # A real legacy rename must refresh all history options, not only the header.
    page.once('dialog',lambda dialog:dialog.accept('Legacy renamed persistent title'))
    page.get_by_role('button',name='重命名',exact=True).click()
    page.wait_for_function("document.querySelector('.paperResearchSelector option:checked').textContent==='Legacy renamed persistent title'")
    assert 'Legacy renamed persistent title' in page.locator('#research .paneTitle').inner_text()
    page.evaluate('research.loadMe()')
    page.wait_for_function("document.querySelector('.paperResearchSelector option:checked')?.textContent==='Legacy renamed persistent title'")
    page.wait_for_function('id=>researchChat.target().resident_id===id',arg=legacy_rid)
    # A newer selected slot wins over an already committed but late rename reply.
    page.get_by_label('选择研究助手',exact=True).select_option(selected['slot_id'])
    page.wait_for_function('id=>researchChat.target().resident_id===id',arg=selected['resident_id'])
    held=[]
    def delay(route):
        request=route.request
        response=http.request(request.method,request.url,headers=request.headers,content=request.post_data_buffer)
        assert response.status_code==200
        held.append((route,response))
    page.route('**/paper/api/research/chats/rename',delay)
    page.once('dialog',lambda dialog:dialog.accept('Late title for research A'))
    page.get_by_role('button',name='重命名',exact=True).click()
    page.wait_for_timeout(30)
    assert held
    page.get_by_label('选择研究助手',exact=True).select_option('legacy:library')
    page.wait_for_function('id=>researchChat.target().resident_id===id',arg=legacy_rid)
    held[0][0].fulfill(status=held[0][1].status_code,headers=dict(held[0][1].headers),body=held[0][1].content)
    page.wait_for_function('!document.querySelector("[data-research-rename]").disabled')
    assert page.get_by_label('选择研究助手',exact=True).input_value()=='legacy:library'
    assert 'Legacy renamed persistent title' in page.locator('#research .paneTitle').inner_text()
    page.unroute('**/paper/api/research/chats/rename',delay)
    # Cancellation does not submit; actual selected identity/draft survives it.
    requests=[]
    page.on('request',lambda r:requests.append(r.url) if r.url.endswith('/chats/rename') else None)
    page.evaluate("researchChat.workspace.setDraftText('请围绕本篇论文回答： user-authored text stays')")
    page.once('dialog',lambda dialog:dialog.dismiss())
    page.get_by_role('button',name='重命名',exact=True).click()
    assert not requests
    assert page.evaluate('researchChat.workspace.draftText()')=='请围绕本篇论文回答： user-authored text stays'
    page.set_viewport_size({'width':390,'height':850})
    page.locator('#research').evaluate("e=>{e.style.width='280px';e.parentElement.style.width='280px'}")
    # Apply the real Page CSS; measure buttons against their actual host.
    page.wait_for_timeout(40)
    assert page.locator('.paperResearchSelector').evaluate('''nav=>{const n=nav.getBoundingClientRect();return [...nav.querySelectorAll('button:not([hidden])')].every(b=>{const r=b.getBoundingClientRect();return r.left>=n.left-1&&r.right<=n.right+1})}''')
    page.get_by_role('button',name='重命名',exact=True).focus()
    page.screenshot(path=str(tmp_path/'research-legacy-narrow-rename.png'))
    assert not support.transport.turns
