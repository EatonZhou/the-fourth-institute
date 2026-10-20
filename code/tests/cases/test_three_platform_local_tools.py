"""External-tool boundaries only: no accounts, models or live installers."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from management_common import process_runtime
from management_portal import article_texlab_backend as texlab
from management_portal import embedding_setup as embeddings
from management_portal import paper_backend as paper


def test_texlab_initialization_failure_releases_process(monkeypatch, tmp_path):
    session = texlab.TexLabSession('article_test', tmp_path)
    process = SimpleNamespace(pid=123, stdin=io.BytesIO(), stdout=None, stderr=None,
                              poll=lambda: None)
    stopped = []
    monkeypatch.setattr(texlab, '_texlab_command', lambda: '/tool path/texlab')
    monkeypatch.setattr(process_runtime, 'spawn_process', lambda *a, **kw: process)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda p, **kw: stopped.append(p))
    monkeypatch.setattr(session, 'request', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError('init deadline')))
    with pytest.raises(TimeoutError, match='init deadline'):
        session.ensure_started()
    assert stopped == [process]
    assert session.process is None
    assert process.stdin.closed


def test_texlab_close_cleans_descendants_even_after_leader_exit(monkeypatch, tmp_path):
    session = texlab.TexLabSession('article_test', tmp_path)
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: 0)
    session.process = process
    stopped = []
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda p, **kw: stopped.append(p))
    session.close()
    assert stopped == [process]
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


def test_texlab_local_install_uses_native_executable_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(texlab.Path, 'home', lambda: tmp_path)
    candidate = str(tmp_path / '.local' / 'bin' / 'texlab')
    resolved = candidate + '.exe'
    monkeypatch.setattr(texlab.shutil, 'which', lambda name: resolved if name == candidate else None)
    assert texlab._texlab_command() == resolved


def test_missing_required_poppler_has_install_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(paper.shutil, 'which', lambda _: None)
    with pytest.raises(RuntimeError, match='pdftotext.*Poppler'):
        paper._extract_pdf_text_file(tmp_path / 'paper.pdf')


def test_optional_pdfinfo_keeps_metadata_fallback_with_diagnostic(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(paper.shutil, 'which', lambda _: None)
    # A missing optional tool cannot turn metadata extraction into registration failure.
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError('missing')))
    assert paper._pdf_document_properties(tmp_path / 'paper.pdf') == {}
    assert 'pdfinfo' in caplog.text and 'Poppler' in caplog.text


@pytest.mark.parametrize('platform, executable', [
    ('linux', '/opt/poppler tools/pdftotext'),
    ('darwin', '/opt/homebrew/bin/pdftotext'),
    ('win32', r'C:\Program Files\Poppler\bin\pdftotext.exe'),
])
def test_poppler_argv_encoding_and_timeout_contract(monkeypatch, tmp_path, platform, executable):
    calls = []
    monkeypatch.setattr(paper.shutil, 'which', lambda name: executable)
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '论文文字'.encode(), b'')
    monkeypatch.setattr(process_runtime, 'run_process', run)
    monkeypatch.setattr(sys, 'platform', platform)
    target = tmp_path / '论文 a & b.pdf'
    assert paper._extract_pdf_text_file(target) == '论文文字'
    argv, options = calls[0]
    assert argv == [executable, '-layout', '-enc', 'UTF-8', str(target), '-']
    assert options['timeout'] == 120
    assert not options.get('shell')


def test_embedding_probe_timeout_is_readiness_diagnostic(monkeypatch, tmp_path):
    python = tmp_path / 'python'
    python.touch()
    monkeypatch.setattr(embeddings, 'runtime_python', lambda: python)
    monkeypatch.setattr(embeddings, 'model_directory', lambda: tmp_path)
    monkeypatch.setattr(embeddings, 'platform_issue', lambda: None)
    monkeypatch.setattr(embeddings, 'enabled', lambda: False)
    monkeypatch.setattr(embeddings, 'job_status', lambda: {'state': 'idle'})
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('probe', 20)
    monkeypatch.setattr(process_runtime, 'run_process', timeout)
    monkeypatch.setattr(subprocess, 'run', timeout)
    result = embeddings.readiness()
    assert result['ready'] is False
    assert any('超时' in item for item in result['missing'])


def test_embedding_launch_uses_owned_detached_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, 'job_file', lambda: tmp_path / 'job.json')
    monkeypatch.setattr(embeddings, 'platform_issue', lambda: None)
    calls = []
    process = SimpleNamespace(pid=456, wait=lambda: 0)
    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        embeddings._write_job({'state': 'complete'})
        return process
    monkeypatch.setattr(process_runtime, 'spawn_process', spawn)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda *a, **kw: None)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: process)
    assert embeddings.start_install()['pid'] == 456
    assert len(calls) == 1
    assert calls[0][1]['kill_on_owner_exit'] is False
    assert 'creationflags' not in calls[0][1] and 'start_new_session' not in calls[0][1]
    assert embeddings.job_status()['state'] == 'complete'


LSP_STUB = r'''
import json, pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path('child.pid').write_text(str(child.pid))
while True:
    size = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line == b'\r\n':
            break
        if line.lower().startswith(b'content-length:'):
            size = int(line.split(b':')[1])
    message = json.loads(sys.stdin.buffer.read(size))
    if message['method'] == 'exit':
        sys.exit(0)
    if message['method'] == 'initialize' and pathlib.Path('hang').exists():
        time.sleep(60)
    if 'id' in message:
        result = {'capabilities': {}} if message['method'] == 'initialize' else message.get('params')
        data = json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}, ensure_ascii=False).encode('utf-8')
        sys.stdout.buffer.write(f'Content-Length: {len(data)}\r\n\r\n'.encode('ascii') + data)
        sys.stdout.buffer.flush()
'''


@pytest.mark.parametrize('hang', [False, True])
def test_native_texlab_lsp_utf8_and_cleanup(monkeypatch, tmp_path, hang):
    """Exercise the actual transport and owned tree with a harmless LSP stand-in."""
    stub = tmp_path / 'language server.py'
    stub.write_text(LSP_STUB, encoding='utf-8')
    if hang:
        (tmp_path / 'hang').touch()
    native_spawn = process_runtime.spawn_process
    processes = []
    def spawn(argv, **kwargs):
        process = native_spawn([sys.executable, str(stub)], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(process_runtime, 'spawn_process', spawn)
    monkeypatch.setattr(texlab, '_texlab_command', lambda: sys.executable)
    session = texlab.TexLabSession('article_test', tmp_path)
    request = session.request
    if hang:
        monkeypatch.setattr(session, 'request', lambda method, params, **kw:
                            request(method, params, **{**kw, 'timeout': .5}))
    try:
        if hang:
            with pytest.raises(TimeoutError, match='initialize'):
                session.ensure_started()
        else:
            session.ensure_started()
            assert session.request('echo', {'text': '论文 α 🚀'}) == {'text': '论文 α 🚀'}
            session.sync(tmp_path / '论文.tex', '中文正文', 1)
            session.close()
        assert session.process is None
        assert not process_runtime.process_alive(int((tmp_path / 'child.pid').read_text()))
        assert all(not process_runtime.process_alive(p.pid) for p in processes)
        assert all(p.stdin.closed and p.stdout.closed and p.stderr.closed for p in processes)
    finally:
        for process in processes:
            process_runtime.terminate_process_tree(process, force=True)


def test_texlab_old_reader_cannot_fail_new_pending_request(tmp_path):
    session = texlab.TexLabSession('article_test', tmp_path)
    old = SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO(b'old log\n'))
    session.process = SimpleNamespace()
    event = threading.Event()
    session._pending[9] = {'event': event, 'error': None}
    session._read_stdout(old)
    session._read_stderr(old)
    assert not event.is_set()
    assert not session._log


def test_texlab_reader_start_failure_releases_owned_tree(monkeypatch, tmp_path):
    process = SimpleNamespace(pid=123, stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                              poll=lambda: None)
    monkeypatch.setattr(texlab, '_texlab_command', lambda: 'texlab')
    monkeypatch.setattr(process_runtime, 'spawn_process', lambda *a, **kw: process)
    monkeypatch.setattr(threading.Thread, 'start', lambda self: (_ for _ in ()).throw(RuntimeError('no thread')))
    stopped = []
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda p, **kw: stopped.append(p))
    session = texlab.TexLabSession('article_test', tmp_path)
    with pytest.raises(RuntimeError, match='no thread'):
        session.ensure_started()
    assert stopped == [process]
    assert session.process is None


def test_embedding_cleanup_failure_is_diagnostic_after_completion(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(embeddings, 'job_file', lambda: tmp_path / 'job.json')
    embeddings._write_job({'state': 'complete'})
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda *a, **kw:
                        (_ for _ in ()).throw(TimeoutError('tree still alive')))
    embeddings._reap_install(SimpleNamespace(wait=lambda: 0), 'this-job')
    assert 'cleanup failed' in caplog.text and 'tree still alive' in caplog.text


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux native PATH executable fixture')
@pytest.mark.parametrize('timeout', [False, True])
def test_native_poppler_cleanup_on_exit_and_timeout(monkeypatch, tmp_path, timeout):
    tool = tmp_path / 'pdftotext'
    tool.write_text(f'#!{sys.executable}\n' + '''
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[-2]).with_suffix('.pid').write_text(str(child.pid))
print('论文文字', flush=True)
''' + ('time.sleep(60)\n' if timeout else ''), encoding='utf-8')
    tool.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ.get('PATH', ''))
    native_run = process_runtime.run_process
    def run(argv, **kw):
        return native_run(argv, **{**kw, 'timeout': .5 if timeout else 5})
    monkeypatch.setattr(process_runtime, 'run_process', run)
    target = tmp_path / '论文 & space.pdf'
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            paper._extract_pdf_text_file(target)
        assert '论文文字'.encode() in caught.value.output
    else:
        assert paper._extract_pdf_text_file(target) == '论文文字'
    assert not process_runtime.process_alive(int(target.with_suffix('.pid').read_text()))


def test_native_embedding_installer_log_and_tree_cleanup(monkeypatch, tmp_path):
    """Use only a temporary stand-in; never execute the real installer."""
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    job = tmp_path / 'job.json'
    child_pid = tmp_path / 'child.pid'
    (scripts / 'install_portal_embeddings.py').write_text(f'''
import json, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))
print('本地安装器替身', flush=True)
pathlib.Path({str(job)!r}).write_text(json.dumps({{'state': 'complete'}}))
''', encoding='utf-8')
    monkeypatch.setattr(embeddings, 'ROOT', tmp_path)
    monkeypatch.setattr(embeddings, 'job_file', lambda: job)
    monkeypatch.setattr(embeddings, 'platform_issue', lambda: None)
    done = threading.Event()
    reaper = embeddings._reap_install
    def reap(*args):
        try:
            reaper(*args)
        finally:
            done.set()
    monkeypatch.setattr(embeddings, '_reap_install', reap)
    result = embeddings.start_install()
    assert done.wait(8), 'installer tree did not finish'
    assert embeddings.job_status()['state'] == 'complete'
    assert '本地安装器替身' in job.with_suffix('.log').read_text(encoding='utf-8')
    assert not process_runtime.process_alive(result['pid'])
    assert not process_runtime.process_alive(int(child_pid.read_text()))


def test_embedding_unexpected_exit_does_not_leave_running_job(monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, 'job_file', lambda: tmp_path / 'job.json')
    embeddings._write_job({'state': 'running', 'started_at': 'this-job'})
    stopped = []
    process = SimpleNamespace(wait=lambda: 17)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', lambda p, **kw: stopped.append(p))
    embeddings._reap_install(process, 'this-job')
    assert stopped == [process]
    assert embeddings.job_status()['state'] == 'failed'
    assert 'exit 17' in embeddings.job_status()['error']
    embeddings._write_job({'state': 'running', 'started_at': 'newer-job'})
    embeddings._reap_install(process, 'this-job')
    assert embeddings.job_status()['started_at'] == 'newer-job'
    assert embeddings.job_status()['state'] == 'running'


def test_embedding_missing_installer_is_explicit_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, 'ROOT', tmp_path)
    monkeypatch.setattr(embeddings, 'job_file', lambda: tmp_path / 'job.json')
    monkeypatch.setattr(embeddings, 'platform_issue', lambda: None)
    with pytest.raises(FileNotFoundError, match='installer is missing'):
        embeddings.start_install()
    assert embeddings.job_status()['state'] == 'failed'


@pytest.mark.parametrize('directory', ['/opt/poppler tools/', '/opt/homebrew/bin/', 'C:\\Program Files\\Poppler\\bin\\'])
def test_all_poppler_commands_use_native_argv_and_managed_deadlines(monkeypatch, tmp_path, directory):
    calls = []
    monkeypatch.setattr(paper.shutil, 'which', lambda name: directory + name + ('.exe' if directory.startswith('C:') else ''))
    monkeypatch.setattr(paper, '_all_papers', lambda: [])
    monkeypatch.setattr(paper, 'PDF_TEXT_ROOT', tmp_path / 'text')
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if 'pdfinfo' in command[0]:
            return subprocess.CompletedProcess(command, 0, 'Title: 中文标题\n'.encode(), b'')
        if '-raw' in command:
            return subprocess.CompletedProcess(command, 0, '中文原文'.encode(), b'')
        if '-bbox-layout' in command:
            return subprocess.CompletedProcess(command, 0, '<doc><page width="10" height="10"/></doc>', '')
        if 'pdftoppm' in command[0]:
            Path(command[-1]).with_suffix('.png').touch()
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(process_runtime, 'run_process', run)
    source = tmp_path / '论文 a & b.pdf'
    assert paper._pdf_document_properties(source) == {'title': '中文标题'}
    assert paper._extract_pdf_text_raw(source, paper_id='paper_test') == '中文原文'
    assert paper._extract_figure_caption_blocks(source) == []
    assert paper._render_pdf_page_png(source, 1, tmp_path / 'page').is_file()
    extracted = tmp_path / 'extracted'
    extracted.mkdir()
    assert paper._extract_embedded_pdf_images(source, extracted, tmp_path, 'paper_test', 'fixture') == []
    assert len(calls) == 6
    assert [options['timeout'] for _, options in calls] == [30, 120, 120, 90, 90, 120]
    for command, options in calls:
        assert command[0].startswith(directory)
        assert str(source) in command
        assert not options.get('shell')
        if options.get('text'):
            assert options['encoding'] == 'utf-8' and options['errors'] == 'replace'


def test_texlab_windows_lsp_file_uri_contract(monkeypatch):
    import nturl2path
    from pathlib import PureWindowsPath
    class WindowsPathView(PureWindowsPath):
        def resolve(self, strict=False):
            return self
    monkeypatch.setattr(texlab, 'Path', WindowsPathView)
    # Windows urllib uses this converter; no claim of native Windows execution.
    if hasattr(texlab, 'url2pathname'):
        monkeypatch.setattr(texlab, 'url2pathname', nturl2path.url2pathname)
    root = WindowsPathView('C:/article workspace')
    assert texlab._uri_to_relative(root, 'file:///C:/article%20workspace/%E8%AE%BA%E6%96%87.tex') == '论文.tex'
    assert texlab._uri_to_relative(root, 'file:///D:/outside.tex') == ''
    assert texlab._uri_to_relative(root, 'file://another-host/share/outside.tex') == ''


def test_texlab_native_file_uri_preserves_root_containment(tmp_path):
    source = tmp_path / '论文 & space.tex'
    assert texlab._uri_to_relative(tmp_path, source.as_uri()) == source.name
    assert texlab._uri_to_relative(tmp_path, (tmp_path.parent / 'outside.tex').as_uri()) == ''
