"""Shared single-paper discussion owner, no model/Execution requests."""
import json
import pytest
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR
from tests.cases.test_reader_ai_owner_chain import bind_paper


def test_shared_paper_identity_history_and_context(owners, monkeypatch, tmp_path):
    from management_portal import resident_workspace_adapter as adapter, paper_store, paper_resources, web_resources, paper_chat_context
    from management_portal.resources.providers import paper
    from management_portal.resources.directory_registrations import register_owned
    from management_portal.paper_resource_index import PaperManifestAdapter
    from management_portal.paper_reader_service import production_service
    from management_portal.permission_system.facts import collect_portal_facts
    from management_portal.society_service import resident_conversation_stream, resident_protocol
    bind_paper(owners,monkeypatch)
    monkeypatch.setattr(adapter,'PORTAL_ROOT',tmp_path)
    paper.install_resolver(owners.resource)
    monkeypatch.setattr(paper_resources,'get_resource_service',lambda:owners.resource)
    monkeypatch.setattr(web_resources,'get_resource_service',lambda:owners.resource)
    store=paper_store.PaperStore(tmp_path)
    monkeypatch.setattr(paper_store,'PAPER_STORE',store)
    monkeypatch.setattr(paper_resources,'PAPER_STORE',store)
    index=PaperManifestAdapter();owners.index.adapter_layer.adapters[index.name]=index
    records=[];docs=[]
    for i,pid in enumerate(('paper_0123456789ab','paper_abcdef012345')):
        folder=tmp_path/str(i);folder.mkdir()
        (folder/'paper.pdf').write_bytes(b'%PDF synthetic '+str(i).encode())
        (folder/'summary.md').write_text('Trusted summary excerpt')
        (folder/'analysis.md').write_text('Trusted analysis excerpt')
        (folder/'text.txt').write_text('Entire PDF text is a reference, not inline')
        (folder/'manifest.json').write_text(json.dumps({'paper_id':pid,'title':'Single paper '+str(i),'authors':['Alice','Bob'],'venue':'Test Venue','year':2026,'abstract':'Trusted bounded material','files':{'pdf':'paper.pdf','analysis':'analysis.md','summary':'summary.md','pdf_text_clean':'text.txt'}}))
        register_owned('paper',pid,[folder]);records.append(index._record_for_manifest(folder/'manifest.json'))
    owners.index_store.publish_snapshot('paper',records,{})
    owners.permissions.instance.sync(collect_portal_facts(owners.resource))
    for pid in ('paper_0123456789ab','paper_abcdef012345'):
        doc=production_service().open(ACTOR,pid)['document'];docs.append({k:doc[k] for k in ('paper_id','attachment_id','attachment_version')})
    # Neither entry may fall back to the library while preparing material.
    monkeypatch.setattr(paper_chat_context,'resource_index_papers',lambda:pytest.fail('library scan'))
    monkeypatch.setattr(store,'paper_by_id',lambda *a,**k:pytest.fail('undirected lookup'))
    pid=docs[0]['paper_id'];key='paper:'+pid
    from management_portal.http_portal_pages import PortalPagesHttp
    from urllib.parse import urlparse
    route=PortalPagesHttp.__new__(PortalPagesHttp)
    route.workspace_adapter=adapter
    route.respond=lambda request,status,body:(status,body)
    route.exception=lambda request,error:(_ for _ in ()).throw(error)
    status,session=route.chat_workspace_session(None,urlparse('/chat-workspace/api/session?surface=paper&workspace_key='+key+'&paper_id='+pid),ACTOR)
    assert status==200 and session['resident_id']==''
    # Explicit foreign fields remain rejected; only absent route defaults vanish.
    status,_=route.chat_workspace_session(None,urlparse('/chat-workspace/api/session?surface=paper&workspace_key='+key+'&paper_id='+pid+'&project_id=foreign'),ACTOR)
    assert status==400
    from management_portal.http_portal_session import PortalSessionHttp
    prepare_route=PortalSessionHttp.__new__(PortalSessionHttp)
    prepare_route.workspace_adapter=adapter
    prepare_route.respond=route.respond
    prepare_route.session_actor=lambda request:ACTOR
    prepare_route.read_json=lambda request:request
    status,normal=prepare_route.chat_workspace_prepare({'surface':'paper','workspace_key':key,'message':'normal question','context':{'paper_id':pid}},None,None)
    assert status==200
    status,_=prepare_route.chat_workspace_prepare({'surface':'paper','workspace_key':key,'message':'bad context','context':{'paper_id':pid,'collection_items':[]}},None,None)
    assert status==400
    reader_payload={'surface':'paper','workspace_key':key,'message':'selection question','context':{'document':docs[0],'expected_principal':ACTOR,'selection':'This turn selection'}}
    full=adapter.prepare_workspace(ACTOR,reader_payload)
    assert full['resident_id']==normal['resident_id'] and full['lineage_id']==normal['lineage_id']
    for prepared in (normal,full):
        material=prepared['initial_context']['context_text']
        for required in ('Alice','Bob','Test Venue 2026','Trusted summary excerpt','Trusted analysis excerpt',
                         str(tmp_path/'0'/'paper.pdf'),str(tmp_path/'0'/'analysis.md'),str(tmp_path/'0'/'text.txt')):
            assert required in material
        assert 'Entire PDF text is a reference, not inline' not in material
    rid=full['resident_id'];repo=resident_conversation_stream.get_repository()
    repo.append_message(rid,{'message_id':'msg_0123456789abcdef0123456789abcdef','role':'agent','author_id':rid,'content_parts':[{'type':'text','text':'Seeded prior discussion; no model.'}]})
    views=[adapter.get_workspace(ACTOR,'paper',key,context) for context in ({'paper_id':pid},reader_payload['context'])]
    for v in views:
        assert v['resident_id']==rid
        snapshot=resident_protocol.get_resident_conversation_snapshot(v['resident_id'],ACTOR)
        assert 'Seeded prior discussion' in str(snapshot['messages'])
    assert 'This turn selection' in full['initial_context']['context_text']
    follow=adapter.prepare_workspace(ACTOR,{**reader_payload,'context':{**reader_payload['context'],'selection':''}})
    assert 'This turn selection' not in follow['initial_context']['context_text']
    assert 'Trusted bounded material' in follow['initial_context']['context_text'] and 'Library Chat' not in str(follow)
    other=adapter.prepare_workspace('storage-outsider',{**reader_payload,'context':{**reader_payload['context'],'expected_principal':'storage-outsider'}})
    assert other['resident_id']!=rid
    second=adapter.prepare_workspace(ACTOR,{**reader_payload,'workspace_key':'paper:'+docs[1]['paper_id'],'context':{**reader_payload['context'],'document':docs[1]}})
    assert second['resident_id']!=rid
    with pytest.raises(PermissionError):adapter.prepare_workspace('storage-outsider',reader_payload)
    with pytest.raises(ValueError):adapter.prepare_workspace(ACTOR,{**reader_payload,'context':{**reader_payload['context'],'document':docs[1]}})
    with pytest.raises(ValueError):adapter.prepare_workspace(ACTOR,{**reader_payload,'context':{**reader_payload['context'],'paper_ids':[docs[1]['paper_id']]}})
    with pytest.raises(Exception):adapter.prepare_workspace(ACTOR,{**reader_payload,'context':{**reader_payload['context'],'document':{**docs[0],'attachment_version':'stale'}}})
