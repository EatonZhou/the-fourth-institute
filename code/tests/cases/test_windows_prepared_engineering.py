"""Desktop installers consume build-prepared cases with bounded file progress.

:module: operations.deployment
:design: decision_d88306d0bd17
"""
import hashlib
import json
from pathlib import Path
import zipfile
from unittest.mock import patch

import pytest

from scripts import tfi_windows_launcher as launcher
from scripts import tfi_release_install as installer
from scripts.tfi_install_progress import FileProgress
from scripts.windows.build import bundle_engineering
from tests.cases.instance_test_support import write_instance


def archive(platform, included=True, extra=None):
    source = platform / ('source-example' if included else 'source-base')
    files = {'code/scripts/tfi_release_install.py': b'# installer',
             'database/distribution.sql': b'SELECT 1;',
             'data/projects/platform.txt': b'platform ${code_root}'}
    if included:
        files['data/projects/Lenia/example.txt'] = b'example'
    rows = []
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        rows.append({'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    manifest = {'schema': 'tfi.release-engineering.v1', 'include_examples': included,
                'run_id': 'fixture', 'supervisor_resident_id': '', 'inventory': rows,
                'files': {area: 'data/' + area for area in ('projects', 'features')},
                'code': {'path': 'code'}, 'database': {'path': 'database/distribution.sql'},
                'directory_mappings': [{'placeholder': '${code_root}', 'root': 'code_root', 'path': '.'}]}
    (source / 'manifest.json').write_text(json.dumps(manifest))
    result = launcher.selected_engineering(platform, included)
    bundle_engineering(source, result)
    if extra:
        with zipfile.ZipFile(result, 'a') as zipped:
            zipped.writestr(*extra)
    return result, manifest


def test_prepared_case_extracts_once_without_seed_or_hashing_and_reports_progress(tmp_path):
    archive(tmp_path)
    messages = []
    with patch.object(hashlib, 'file_digest', side_effect=AssertionError('file hash')):
        path = launcher.unpack_engineering(tmp_path, progress=messages.append)
    assert (path.parent / 'data/projects/Lenia/example.txt').read_text() == 'example'
    assert (path.parent / 'data/features').is_dir()  # Empty areas have no inventory rows.
    assert '0/' in messages[0] and '100%' in messages[-1]
    assert 'MiB' in messages[-1]
    with patch.object(zipfile.ZipFile, 'open', autospec=True, wraps=None) as opened:
        # Only the small completion manifest may be read on a completed retry.
        def read_manifest(zipped, name, *args, **kwargs):
            assert name == 'manifest.json'
            return path.read_bytes()
        with patch.object(zipfile.ZipFile, 'read', read_manifest):
            assert launcher.unpack_engineering(tmp_path) == path
        opened.assert_not_called()


def test_selection_change_removes_case_and_interrupted_copy_is_replaced(tmp_path):
    archive(tmp_path)
    archive(tmp_path, False)
    launcher.unpack_engineering(tmp_path)
    pending = tmp_path / '.build/w/engineering.pending'
    pending.mkdir()
    (pending / 'incomplete.txt').write_text('old interruption')
    path = launcher.unpack_engineering(tmp_path, included=False)
    assert not (path.parent / 'data/projects/Lenia').exists()
    assert not (path.parent / 'incomplete.txt').exists()
    assert json.loads(path.read_text())['include_examples'] is False


@pytest.mark.parametrize('name', ['../escape', '/escape', 'C:/escape', 'data\\escape'])
def test_prepared_archive_keeps_path_boundary(tmp_path, name):
    archive(tmp_path, extra=(name, b'bad'))
    with pytest.raises(ValueError, match='Invalid engineering'):
        launcher.unpack_engineering(tmp_path)
    assert not (tmp_path / '.build/w/engineering').exists()


def test_failed_extraction_never_exposes_completed_candidate_and_can_retry(tmp_path):
    archive(tmp_path)
    original = zipfile.ZipFile.open
    def broken(zipped, name, *args, **kwargs):
        if getattr(name, 'filename', name) == 'data/projects/Lenia/example.txt':
            raise OSError('interrupted')
        return original(zipped, name, *args, **kwargs)
    with patch.object(zipfile.ZipFile, 'open', broken), pytest.raises(OSError, match='interrupted'):
        launcher.unpack_engineering(tmp_path)
    assert not (tmp_path / '.build/w/engineering/manifest.json').exists()
    assert launcher.unpack_engineering(tmp_path).is_file()


def test_desktop_creates_the_instance_from_prepared_engineering_without_hash_pass(tmp_path):
    archive(tmp_path)
    flow = launcher.DefaultStartup(tmp_path)
    with patch.object(flow, 'execute') as execute:
        flow.initialize()
    command = execute.call_args.args[0]
    assert execute.call_count == 1
    assert command[1].endswith('tfi_release_install.py') and command[2] == 'create'
    assert command[command.index('--name') + 1] == 'example_instance'
    assert Path(command[command.index('--engineering') + 1]) == tmp_path / '.build/w/engineering/manifest.json'
    assert '--skip-file-verification' in command
    assert '--root' not in command  # The installer's default location, outside the program.


def test_incomplete_registered_instance_is_retired_before_creating_again(tmp_path):
    archive(tmp_path)
    flow = launcher.DefaultStartup(tmp_path)
    with patch.object(flow, 'registered', return_value=True), patch.object(flow, 'execute') as execute:
        flow.initialize()
    assert [call.args[0][2:5] for call in execute.call_args_list][0] == ['retire', '--instance', 'example_instance']
    assert execute.call_args_list[1].args[0][2] == 'create'


def test_area_copy_reports_progress_and_maps_only_the_code_root(tmp_path, capsys):
    _, manifest = archive(tmp_path)
    bundle = launcher.unpack_engineering(tmp_path).parent
    from management_common.instance_manifest import load
    instance = load(write_instance(tmp_path / 'instance'))
    code = tmp_path / '代码 目录/code'
    installer.copy_area_content(manifest, bundle, instance, installer.directory_replacements(manifest, code))
    copied = instance.area('projects') / 'platform.txt'
    assert copied.read_text() == 'platform ' + str(code)
    messages = [json.loads(line.removeprefix('@TFI_PROGRESS '))['message']
                for line in capsys.readouterr().err.splitlines() if line.startswith('@TFI_PROGRESS ')]
    assert any(message and '正在复制实例文件' in message and '100%' in message for message in messages)


def test_long_path_check_covers_the_default_instance_root(tmp_path):
    archive(tmp_path)
    deep = 'x' * 230
    root = tmp_path / 'instances' / deep
    assert launcher.required_long_path(tmp_path, root=root).startswith(str(root / 'data/projects'))
    assert launcher.required_long_path(tmp_path, root=tmp_path / 'short') is None


def test_progress_throttles_updates_and_always_reports_completion():
    messages = []
    with patch('scripts.tfi_install_progress.time.monotonic', return_value=10):
        meter = FileProgress('复制', 100, 200, messages.append)
        for _ in range(100):
            meter.advance(2)
    assert len(messages) == 2
    assert '100/100' in messages[-1] and '100%' in messages[-1]


def test_large_file_reports_bytes_before_completion():
    import io
    from itertools import count
    from scripts.tfi_install_progress import copy_stream
    messages = []
    content = b'x' * (3 * 1024 * 1024 + 17)
    output = io.BytesIO()
    with patch('scripts.tfi_install_progress.time.monotonic', side_effect=count()):
        meter = FileProgress('解压', 1, len(content), messages.append)
        copy_stream(io.BytesIO(content), output, meter)
    assert output.getvalue() == content
    assert any('0/1' in m and '33%' in m and '1.0/' in m for m in messages)
    assert '1/1' in messages[-1] and '100%' in messages[-1]


def test_macos_materialization_omits_archives_and_preserves_runtime(tmp_path):
    from scripts.tfi_macos_launcher import materialize
    bundle, target = tmp_path / '应用包', tmp_path / 'Library/TFI'
    binary = bundle / 'python/bin/python3'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'embedded runtime')
    binary.chmod(0o755)
    (binary.parent / 'python').symlink_to('python3')
    (bundle / 'macos-package.json').write_text('{"version": "test"}')
    (bundle / 'base-engineering.zip').write_bytes(b'do not copy')
    (bundle / 'example-engineering.zip').write_bytes(b'do not copy')
    opened = []
    original_open = Path.open
    def tracked_open(path, *args, **kwargs):
        if args and args[0] == 'rb':
            opened.append(path)
        return original_open(path, *args, **kwargs)
    messages = []
    with patch.object(Path, 'open', tracked_open):
        materialize(bundle, target, progress=messages.append)
    assert opened == [bundle / 'macos-package.json', binary]
    assert (target / 'python/bin/python3').stat().st_mode & 0o777 == 0o755
    assert (target / 'python/bin/python').readlink() == Path('python3')
    assert (target / 'python/bin/python').read_bytes() == b'embedded runtime'
    assert not list(target.glob('*-engineering.zip'))
    assert '100%' in messages[-1] and 'MiB' in messages[-1]
    opened.clear()
    with patch.object(Path, 'open', tracked_open):
        materialize(bundle, target)
    assert not opened


def test_macos_uses_selected_app_archive_directly_and_retains_choice(tmp_path):
    from scripts import tfi_macos_launcher as mac
    app, target = tmp_path / '应用包', tmp_path / 'Library/TFI'
    app.mkdir()
    archive(app, False)
    flow = mac.MacStartup(target, included=False, bundle=app)
    with patch.object(flow, 'execute') as execute:
        flow.initialize()
        assert execute.call_count == 1
        command = execute.call_args.args[0]
        assert command[0] == str(target / 'dependencies/python/bin/python3')
        assert '--skip-file-verification' in command and '--offline' in command
        assert command[2] == 'create' and command[command.index('--name') + 1] == 'desktop'
        manifest_path = Path(command[command.index('--engineering') + 1])
        assert manifest_path == target / '.build/m/engineering/manifest.json'
        assert not (manifest_path.parent / 'data/projects/Lenia').exists()
        assert not list(target.glob('*-engineering.zip'))
        # A new launch with the default checked choice must keep the saved base selection.
        retried = mac.MacStartup(target, bundle=app)
        with patch.object(retried, 'execute'), patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('repeat content read')):
            with patch.object(zipfile.ZipFile, 'read', return_value=manifest_path.read_bytes()):
                retried.initialize()
    assert json.loads((target / '.build/macos/options.json').read_text())['include_examples'] is False


def test_mac_archive_extracts_modes_and_rejects_redirected_parent(tmp_path):
    import os
    if os.name == 'nt':
        pytest.skip('POSIX modes and symlinks')
    app, target = tmp_path / '应用 包', tmp_path / 'Library/TFI'
    app.mkdir()
    zipped, _ = archive(app, extra=('code/run', b'#!/bin/sh\n'))
    # zipfile.writestr defaults to a non-executable regular file; explicitly set executable mode.
    with zipfile.ZipFile(zipped, 'a') as stream:
        script = zipfile.ZipInfo('code/launch')
        script.create_system = 3
        script.external_attr = 0o100755 << 16
        stream.writestr(script, b'#!/bin/sh\n')
    manifest = launcher.unpack_engineering(target, archive=zipped, parent=target / '.build/m')
    assert (manifest.parent / 'code/launch').stat().st_mode & 0o777 == 0o755
    outside = tmp_path / 'outside'
    outside.mkdir()
    (target / '.build/link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='Redirected'):
        launcher.unpack_engineering(target, archive=zipped, parent=target / '.build/link/deeper')
    assert not list(outside.iterdir())


def test_file_progress_and_shared_progress_roundtrip_on_legacy_console(monkeypatch):
    import io
    import sys
    from management_common.installation_progress import report, progress_message
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stderr", console)
    report("正在准备 C:\\中文 目录")
    FileProgress("复制中文区域", 1, 8).advance(8)
    console.flush()
    messages = [progress_message(line) for line in raw.getvalue().decode("cp1252").splitlines()]
    assert messages[0] == "正在准备 C:\\中文 目录"
    assert messages[-1].startswith("复制中文区域：1/1 个文件（100%")
