"""Native-asset selection and failed replacement preserve installed tools."""
import hashlib
import io
from pathlib import Path
import tarfile
from types import SimpleNamespace
import zipfile

import pytest

from scripts import install_article_tooling as article
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
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    target = bin_dir / 'texlab'
    target.write_bytes(b'previous executable')
    monkeypatch.setenv('ARTICLE_TOOL_BIN_DIR', str(bin_dir))
    monkeypatch.setattr(article, 'release_asset', lambda:('https://invalid.test/fixture', '0'*64, '5.26.0', 'texlab'))
    monkeypatch.setattr(article, '_version', lambda _: 'texlab 0.0.0')
    monkeypatch.setattr(article.shutil, 'which', lambda *a,**kw:'/fixture/synctex')
    monkeypatch.setattr(article.urllib.request, 'urlopen', lambda *a,**kw:io.BytesIO(b'untrusted archive'))
    from scripts import article_dependencies
    monkeypatch.setattr(article_dependencies, 'prepare_tex', lambda *_a, **_kw: {})
    with pytest.raises(ValueError, match='checksum'):
        article.install()
    assert target.read_bytes() == b'previous executable'
    assert list(bin_dir.iterdir()) == [target]


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


def test_dsh_profile_imports_code_dependencies_without_copying_or_linking(tmp_path, monkeypatch):
    source, profile = tmp_path/'runtime', tmp_path/'profile'
    source.mkdir(); profile.mkdir()
    monkeypatch.setattr(dsh_client, 'RUNTIME', source)
    modules = {name: str(source / (name.rsplit('/', 1)[-1] + '.mjs'))
               for name in ('@deepseek-ai/dsh-llm', '@deepseek-ai/dsh-llm-deepseek')}
    dsh_client._profile_code(profile, modules)
    for filename in ('bridge.mjs', 'api_provider.mjs'):
        text = (profile / filename).read_text()
        assert "from '@deepseek-ai/" not in text
        assert Path(modules['@deepseek-ai/dsh-llm']).as_uri() in text
    assert not (profile / 'node_modules').exists()
    assert (profile / 'request_timing.mjs').is_file()


def test_verified_native_executable_replaces_previous_install(tmp_path, monkeypatch):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as bundle:
        bundle.writestr('texlab.exe', b'verified fixture executable')
    payload = data.getvalue()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    target = bin_dir / 'texlab.exe'
    target.write_bytes(b'previous')
    monkeypatch.setenv('ARTICLE_TOOL_BIN_DIR', str(bin_dir))
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
    assert list(bin_dir.iterdir()) == [target]


def test_windows_tex_preparation_uses_native_sfx_and_runtime_discovery_root(tmp_path, monkeypatch):
    from scripts import article_dependencies as tex
    from management_common import tex_tool_paths
    from management_portal import article_toolchain

    monkeypatch.setattr(tex.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(tex.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(tex_tool_paths.sys, 'platform', 'win32')
    monkeypatch.setenv('ARTICLE_TOOL_HOME', str(tmp_path))
    payload = b'MZ pinned native installer fixture'
    flavor, digest = tex.ASSETS[('Windows', 'x86_64')]
    assert flavor == 'windows' and digest == '36c329c644683d981a668419d13c8804696e2816ae4d287ad755788fee231a31'
    monkeypatch.setitem(tex.ASSETS, ('Windows', 'x86_64'), (flavor, hashlib.sha256(payload).hexdigest()))
    urls, commands = [], []
    def download(url, **kwargs):
        urls.append(url)
        assert url.endswith('/TinyTeX-1-windows-v2026.10.exe')
        return io.BytesIO(payload)
    def process(command, **kwargs):
        commands.append(command)
        if command[0].endswith('download.exe'):
            assert Path(command[0]).read_bytes() == payload
            native = Path(command[1][2:]) / 'TinyTeX/bin/windows'
            native.mkdir(parents=True)
            (native / 'tlmgr.bat').write_text('native fixture')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(tex.urllib.request, 'urlopen', download)
    monkeypatch.setattr(tex, 'run_process', process)
    monkeypatch.setattr(tex, 'run_native_capture', lambda *_a, **_kw: SimpleNamespace(stdout='latexmk,1'))
    root = tex_tool_paths.tinytex_root(tmp_path)
    monkeypatch.setattr(tex.shutil, 'which', lambda *_a, **_kw: str(root / 'bin/windows/tlmgr.bat'))
    assert tex_tool_paths.tinytex_bin_dirs() == ()
    environment = tex.prepare_tex(tmp_path)
    native = root / 'bin/windows'
    assert root == tmp_path / 'AppData/Roaming/TinyTeX'
    assert str(native) in environment['PATH'] and str(native) in article_toolchain._tex_search_path()
    assert (root / 'tfi-dependencies.json').is_file()
    assert len(urls) == 1 and commands[0][-1] == '-y'
    assert not (tmp_path / '.TinyTeX').exists()


def test_windows_tex_checksum_failure_never_executes_asset(tmp_path, monkeypatch):
    from scripts import article_dependencies as tex
    monkeypatch.setattr(tex.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(tex.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(tex.urllib.request, 'urlopen', lambda *_a, **_kw: io.BytesIO(b'changed executable'))
    monkeypatch.setattr(tex, 'run_process', lambda *_a, **_kw: pytest.fail('unverified executable ran'))
    with pytest.raises(ValueError, match='checksum mismatch'):
        tex.prepare_tex(tmp_path)
