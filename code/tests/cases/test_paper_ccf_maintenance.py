"""RunScript cache maintenance must not consult Portal's registration database.

:module: spec.design-governance
:design: decision_8b510cbd9c58
"""
import json
from management_portal import paper_backend, paper_dblp_metadata


def test_refresh_cache_without_registration_database(monkeypatch, tmp_path):
    """读不到论文登记数据库时，CCF 缓存刷新仍能完成并写入论文列表

    :design-point:"""
    venue={'dblp_conf_key':'ppopp','dblp_section':'conf','rank':'A'}
    entry={'entry_id':'2025','year':2025}
    cache=tmp_path/'papers.json'
    monkeypatch.setattr(paper_backend,'CCF_REFRESH_REPORT_PATH',tmp_path/'refresh.json')
    monkeypatch.setattr(paper_backend,'_ccf_catalog',lambda **kwargs:[venue])
    monkeypatch.setattr(paper_backend,'_ccf_venue_by_key',lambda key:venue)
    monkeypatch.setattr(paper_backend,'_ccf_cache_path',lambda *args:cache)
    monkeypatch.setattr(paper_backend,'_dblp_entries_for_venue',lambda *args,**kwargs:[entry])
    monkeypatch.setattr(paper_dblp_metadata,'papers_for_entry',lambda *args:[{'title':'Fixture','dblp_key':'fixture/2025'}])
    def no_database():
        raise RuntimeError('database credentials unavailable in sandbox')
    monkeypatch.setattr(paper_backend,'resource_index_papers',no_database)
    result=paper_backend.refresh_ccf_cache()
    assert result['errors']==[]
    assert result['paper_count']==1
    assert json.loads(cache.read_text())['papers'][0]['title']=='Fixture'
