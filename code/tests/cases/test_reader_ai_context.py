import pytest
from management_portal import resident_workspace_adapter as adapter

PAPER='paper_0123456789ab'
KEY='paper-reader:'+PAPER+':00000000-0000-4000-8000-000000000001'

@pytest.fixture
def setup(monkeypatch):
    from management_portal import paper_reader_service,paper_store
    calls=[]
    def read(actor,document):
        calls.append((actor,document))
        if actor!='owner' or document['attachment_version']!='v1':raise PermissionError()
    monkeypatch.setattr(paper_reader_service,'production_service',lambda:type('R',(),{'read':staticmethod(read)})())
    def manifest(paper,**options):
        assert options=={'allow_scan':False,'registered_only':True}
        return None,{'title':'Only selected paper','abstract':'Trusted abstract'}
    monkeypatch.setattr(paper_store.PAPER_STORE,'manifest_for_paper_id',manifest)
    monkeypatch.setattr(paper_store.PAPER_STORE,'manifest_to_paper',lambda value,path:{**value,'id':PAPER})
    monkeypatch.setattr(paper_store.PAPER_STORE,'paper_by_id',lambda *a,**k:pytest.fail('legacy lookup'))
    return calls,dict(expected_principal='owner',document=dict(paper_id=PAPER,attachment_id='a',attachment_version='v1'),selection='quoted data')

def test_reader_context_directed_and_quoted(setup):
    calls,context=setup
    value=adapter._context_for('paper',KEY,'question',context,actor='owner')
    assert 'Trusted abstract' in value['prompt_context'] and 'quoted data' in value['prompt_context']
    assert calls and value['paper_id']==PAPER

def test_reader_scope_isolation(setup):
    _,context=setup
    assert adapter._workspace_identity('owner','paper',KEY)!=adapter._workspace_identity('other','paper',KEY)
    assert adapter._workspace_identity('owner','paper',KEY)!=adapter._workspace_identity('owner','paper',KEY[:-1]+'2')
    assert adapter._workspace_identity('owner','paper',KEY)!=adapter._workspace_identity('owner','paper','paper:'+PAPER)
    with pytest.raises(PermissionError):adapter._context_for('paper',KEY,'q',context,actor='other')
    with pytest.raises(ValueError):adapter._context_for('paper',KEY.replace(PAPER,'paper_ffffffffffff'),'q',context,actor='owner')
    changed={**context,'document':{**context['document'],'attachment_version':'v2'}}
    with pytest.raises(PermissionError):adapter._context_for('paper',KEY,'q',changed,actor='owner')
    with pytest.raises(ValueError):adapter._context_for('paper',KEY,'q',{**context,'working_directory':'/untrusted'},actor='owner')


def test_reader_preview_never_allocates_before_send(monkeypatch):
    calls=[]
    monkeypatch.setattr(adapter,'get_workspace',lambda *args,**kwargs:calls.append(kwargs['create']))
    adapter.prepare_workspace('owner',dict(surface='paper',workspace_key=KEY,message='preview',read_only=True))
    adapter.prepare_workspace('owner',dict(surface='paper',workspace_key=KEY,message='real question'))
    assert calls==[False,True]


def test_single_paper_material_bounds_and_safe_references(tmp_path,monkeypatch):
    from management_portal import paper_store
    from management_portal.paper_chat_context import single_paper_prompt_context
    root=tmp_path/'paper';root.mkdir()
    monkeypatch.setattr(paper_store,'ALLOWED_FILE_ROOTS',(root,))
    (root/'summary.md').write_text('S'*2400+'SUMMARY_OVER_LIMIT')
    (root/'analysis.md').write_text('A'*12000+'ANALYSIS_OVER_LIMIT')
    (root/'text.txt').write_text('FULL_TEXT_NOT_INLINED')
    (root/'paper.pdf').write_bytes(b'%PDF synthetic')
    selected={'id':PAPER,'title':'Research title','authors':'Alice; Bob','venue':'Venue','year':'2026',
        'abstract':'B'*3000+'ABSTRACT_OVER_LIMIT','_store_root':str(root),
        '_files':{'pdf':'paper.pdf','pdf_text_clean':'text.txt','analysis':'analysis.md'}}
    prompt=single_paper_prompt_context(selected)['prompt_context']
    for value in ('Alice; Bob','Venue 2026','S'*2400,'A'*12000,str(root/'paper.pdf'),str(root/'analysis.md'),str(root/'text.txt')):
        assert value in prompt
    for excluded in ('SUMMARY_OVER_LIMIT','ANALYSIS_OVER_LIMIT','ABSTRACT_OVER_LIMIT','FULL_TEXT_NOT_INLINED'):
        assert excluded not in prompt
    outside=tmp_path/'private.txt';outside.write_text('OUTSIDE_SECRET')
    (root/'escape.txt').symlink_to(outside)
    unsafe={**selected,'analysis_path':str(outside),'pdf_path':str(outside),
        '_files':{'analysis':'../private.txt','pdf':'escape.txt','pdf_text_clean':'../private.txt','summary':'escape.txt'}}
    rejected=single_paper_prompt_context(unsafe)['prompt_context']
    assert str(outside) not in rejected and 'OUTSIDE_SECRET' not in rejected
