"""HEAD Reader + explicitly selected personal-tree/CSS candidate, no Dev AI."""
import subprocess
from pathlib import Path
import pytest
from tests.cases.test_paper_reader_fullscreen import run_host


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_head_reader_personal_candidate(tmp_path, monkeypatch, browser):
    from management_portal import http_files
    repo=Path(__file__).resolve().parents[2]
    paths=['management_portal/static/'+name for name in (
        'paper_fullscreen_reader.js','paper_reader.js','paper_detail.js')]
    upstream='management_portal/paper_reader_app/zotero_prototype/upstream/reader.js'
    paths.append(upstream)
    snapshots={repo/p:subprocess.check_output(['git','show','HEAD:code/'+p],cwd=repo) for p in paths}
    assert b'paperAIPanel' not in snapshots[repo/paths[0]]
    original_read=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:snapshots.get(p) if p in snapshots else original_read(p))
    old_reader=tmp_path/'head-upstream-reader.js'
    old_reader.write_bytes(snapshots[repo/upstream])
    original_serve=http_files._serve_media_file
    def serve(request,path,content_type,*args,**kwargs):
        return original_serve(request,old_reader if Path(path)==repo/upstream else path,content_type,*args,**kwargs)
    monkeypatch.setattr(http_files,'_serve_media_file',serve)
    run_host(tmp_path,monkeypatch,browser,'paper_static_candidate.cjs')
