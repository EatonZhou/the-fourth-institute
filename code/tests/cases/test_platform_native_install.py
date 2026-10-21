"""Native-asset selection and failed replacement preserve installed tools."""
import hashlib
import io
import tarfile
from types import SimpleNamespace
import zipfile

import pytest

from scripts import install_article_tooling as article
from scripts import install_portal_embeddings as embeddings
from management_portal import dsh_client


@pytest.mark.parametrize('system,arch,name', [
    ('Linux','x86_64','linux'), ('Linux','aarch64','linux'),
    ('Darwin','x86_64','macos'), ('Darwin','arm64','macos'),
    ('Windows','AMD64','windows'), ('Windows','ARM64','windows')])
def test_native_artifact_matches_host_and_has_pinned_digest(monkeypatch, system, arch, name):
    monkeypatch.setattr(article.platform, 'system', lambda:system)
    monkeypatch.setattr(article.platform, 'machine', lambda:arch)
    monkeypatch.delenv('TEXLAB_VERSION', raising=False)
    monkeypatch.delenv('TEXLAB_SHA256', raising=False)
    url, digest, version, binary = article.release_asset()
    assert f'-{name}.' in url and len(digest) == 64
    assert binary.endswith('.exe') == (name == 'windows')
    monkeypatch.setenv('TEXLAB_VERSION', '999.0.0')
    with pytest.raises(ValueError, match='SHA256'):
        article.release_asset()


def test_checksum_failure_leaves_previous_install_and_cleans_staging(tmp_path, monkeypatch):
    target = tmp_path / 'texlab'
    target.write_bytes(b'previous executable')
    monkeypatch.setenv('ARTICLE_TOOL_BIN_DIR', str(tmp_path))
    monkeypatch.setattr(article, 'release_asset', lambda:('https://invalid.test/fixture', '0'*64, '5.26.0', 'texlab'))
    monkeypatch.setattr(article, '_version', lambda _: 'texlab 0.0.0')
    monkeypatch.setattr(article.shutil, 'which', lambda *a,**kw:'/fixture/synctex')
    monkeypatch.setattr(article.urllib.request, 'urlopen', lambda *a,**kw:io.BytesIO(b'untrusted archive'))
    with pytest.raises(ValueError, match='checksum'):
        article.install()
    assert target.read_bytes() == b'previous executable'
    assert list(tmp_path.iterdir()) == [target]


def test_archive_links_are_rejected_and_zip_extracts_only_named_executable(tmp_path):
    archive = tmp_path / 'archive'
    with tarfile.open(archive, 'w:gz') as out:
        info = tarfile.TarInfo('texlab'); info.type = tarfile.SYMTYPE; info.linkname = '../../outside'
        out.addfile(info)
    with pytest.raises(ValueError, match='executable'):
        article._extract_binary(archive, tmp_path/'texlab', 'texlab')
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('../outside', b'no'); out.writestr('texlab.exe', b'native binary fixture')
    article._extract_binary(archive, tmp_path/'texlab.exe', 'texlab.exe')
    assert (tmp_path/'texlab.exe').read_bytes() == b'native binary fixture'
    assert not (tmp_path.parent/'outside').exists()


def test_windows_dsh_profile_updates_atomically_without_symlinks(tmp_path, monkeypatch):
    source, profile = tmp_path/'runtime', tmp_path/'profile'
    (source/'node_modules').mkdir(parents=True); profile.mkdir()
    (source/'node_modules/module.js').write_text('old')
    lock = source/'package-lock.json'; lock.write_text('v1')
    monkeypatch.setattr(dsh_client, 'RUNTIME', source)
    monkeypatch.setattr(dsh_client, 'os', SimpleNamespace(name='nt'))
    dsh_client._profile_modules(profile)
    assert (profile/'node_modules/module.js').read_text() == 'old'
    assert not (profile/'node_modules').is_symlink()
    lock.write_text('v2'); (source/'node_modules/module.js').write_text('new')
    original = dsh_client.shutil.copytree
    def fail(*args, **kwargs):
        raise OSError('copy interrupted')
    monkeypatch.setattr(dsh_client.shutil, 'copytree', fail)
    with pytest.raises(OSError, match='interrupted'):
        dsh_client._profile_modules(profile)
    assert (profile/'node_modules/module.js').read_text() == 'old'
    monkeypatch.setattr(dsh_client.shutil, 'copytree', original)
    dsh_client._profile_modules(profile)
    assert (profile/'node_modules/module.js').read_text() == 'new'
    assert not list(profile.glob('.modules-*'))


def test_verified_native_executable_replaces_previous_install(tmp_path, monkeypatch):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as bundle:
        bundle.writestr('texlab.exe', b'verified fixture executable')
    payload = data.getvalue()
    target = tmp_path / 'texlab.exe'
    target.write_bytes(b'previous')
    monkeypatch.setenv('ARTICLE_TOOL_BIN_DIR', str(tmp_path))
    monkeypatch.setattr(article, 'release_asset', lambda:('https://fixture.invalid/asset', hashlib.sha256(payload).hexdigest(), '5.26.0', 'texlab.exe'))
    monkeypatch.setattr(article, '_version', lambda p: 'texlab 5.26.0' if p.read_bytes() == b'verified fixture executable' else 'texlab 0.0.0')
    monkeypatch.setattr(article.shutil, 'which', lambda *a, **kw:'/fixture/synctex')
    monkeypatch.setattr(article.urllib.request, 'urlopen', lambda *a, **kw:io.BytesIO(payload))
    monkeypatch.setattr(article, 'run_process', lambda *a, **kw:None)
    from scripts import article_dependencies
    monkeypatch.setattr(article_dependencies, 'prepare_tex', lambda *_a, **_kw: {})
    monkeypatch.setattr(article_dependencies, 'smoke_compile', lambda *_a, **_kw: None)
    assert article.install() == target
    assert target.read_bytes() == b'verified fixture executable'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('failure', ['stop', 'state'])
def test_embedding_install_restores_handlers_and_records_failure(tmp_path, monkeypatch, failure):
    writes, restored, handler = [], [], []
    monkeypatch.setattr(embeddings, 'install_shutdown_handlers', lambda fn: handler.append(fn) or 'previous')
    monkeypatch.setattr(embeddings, 'restore_shutdown_handlers', restored.append)
    def status():
        if failure == 'state':
            raise OSError('job state unavailable')
        return {'started_at':'fixture'}
    monkeypatch.setattr(embeddings, 'job_status', status)
    monkeypatch.setattr(embeddings, '_write_job', writes.append)
    monkeypatch.setattr(embeddings, 'runtime_python', lambda:tmp_path/'venv/bin/python')
    monkeypatch.setattr(embeddings, 'run_process', lambda *a, **kw:handler[0]())
    assert embeddings.main() == 1
    assert restored == ['previous']
    assert writes[-1]['state'] == 'failed'
    assert ('stopped' if failure == 'stop' else 'unavailable') in writes[-1]['error']


def test_non_linux_live_directory_exchange_rejected_before_writing(tmp_path, monkeypatch):
    from scripts import portal_instance_storage as storage
    source, destination = tmp_path/'source', tmp_path/'destination'
    source.mkdir()
    (source/'preserved').write_text('untouched')
    monkeypatch.setattr(storage.sys, 'platform', 'darwin')
    with pytest.raises(NotImplementedError, match='Linux'):
        storage.migrate(source, destination, apply=True)
    assert source.is_dir() and not source.is_symlink()
    assert not destination.exists() and not destination.is_symlink()
    assert (source/'preserved').read_text() == 'untouched'
