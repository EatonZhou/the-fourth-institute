"""Reader chrome/shared-key presentation: real Chat factory, no model submission."""
from pathlib import Path
import subprocess,shutil
import pytest
from tests.cases.test_paper_reader_fullscreen import run_host


def _run_chat_case(tmp_path,monkeypatch,browser):
    from management_portal import http_paper,http_responses,http_request_body,http_auth,resident_workspace_adapter as adapter,http_files
    from management_portal.http_routes import RouteDefinition
    repo=Path(__file__).resolve().parents[2]
    for name,path in [('before_bundle','management_portal/static/paper_fullscreen_reader.js'),('before_css','management_portal/static/paper.css'),('before_upstream','management_portal/paper_reader_app/zotero_prototype/upstream/reader.js')]:
        target=tmp_path/name;target.write_bytes(subprocess.check_output(['git','show','HEAD:code/'+path],cwd=repo));monkeypatch.setenv('READER_'+name.upper(),str(target))
    from management_portal.resources.portal import portal_route_script
    target=tmp_path/'route.js';target.write_bytes(portal_route_script());monkeypatch.setenv('READER_ROUTES',str(target))
    # Layout tests do not allocate Residents. Real binding/history is separately
    # checked with real owners in test_reader_shared_chat.py.
    monkeypatch.setattr(adapter,'_ensure',lambda *a,**k:{})
    routes=http_paper.routes
    def prepare(request,parsed,actor):
        payload=http_request_body._read_request_json(request)
        result=adapter._project(http_auth._get_session_username(request),'paper',payload['workspace_key'],{'title':'Research Chat','subtitle':'Synthetic single-paper context','empty_text':'围绕本篇论文提问。'},create=False)
        return http_responses._serve_json(request,200,result)
    monkeypatch.setattr(http_paper,'routes',lambda portal:(*routes(portal),RouteDefinition('POST','/chat-workspace/api/prepare',prepare,'reader-style-fixture',phase='protected')))
    serve=http_files._serve_media_file
    monkeypatch.setattr(http_files,'_serve_media_file',lambda request,path,ct:serve(request,tmp_path/'before_upstream' if str(path).endswith('zotero_prototype/upstream/reader.js') else path,ct))
    run_host(tmp_path,monkeypatch,browser,'reader_chat_style.cjs')
    dest=Path('/share/Projects/AI员工/agents/lineage_4d9182d0fa61/residents/resident_51b2afb5f2e8/workspace/reader-chat-style');dest.mkdir(exist_ok=True)
    for source in tmp_path.glob('reader-style-*.png'):shutil.copyfile(source,dest/source.name)


@pytest.mark.parametrize('browser',['chromium','firefox'])
def test_reader_chat_style(tmp_path,monkeypatch,browser):
    _run_chat_case(tmp_path,monkeypatch,browser)


@pytest.mark.parametrize('browser',['chromium','firefox'])
def test_reader_chat_toggle(tmp_path,monkeypatch,browser):
    monkeypatch.setenv('READER_TOGGLE_ONLY','1')
    _run_chat_case(tmp_path,monkeypatch,browser)


@pytest.mark.parametrize('browser',['chromium','firefox'])
def test_reader_chat_timeline_style(tmp_path,monkeypatch,browser):
    monkeypatch.setenv('READER_TIMELINE_ONLY','1')
    _run_chat_case(tmp_path,monkeypatch,browser)


@pytest.mark.parametrize('browser',['chromium','firefox'])
def test_reader_chat_splitter(tmp_path,monkeypatch,browser):
    monkeypatch.setenv('READER_SPLIT_ONLY','1')
    _run_chat_case(tmp_path,monkeypatch,browser)
