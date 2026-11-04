"""Workspace 文件预览与原始下载的原生路径及内存边界。

:module: resident.conversation
:design: decision_61e50306cc79
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path

from management_common.filesystem_paths import filesystem_path
from management_portal import http_files
from management_portal.society_service import workspace_domain


def authorize(monkeypatch, root):
    monkeypatch.setattr(workspace_domain, '_authorize_link_path', lambda path, actor:
                        workspace_domain._LinkFileAuthorization(root, False, 'fixture'))
    monkeypatch.setattr(workspace_domain, '_preview_root', lambda path: root)


def test_long_binary_preview_keeps_logical_identity_without_reading_payload(tmp_path, monkeypatch):
    """长路径只在I/O转换，二进制预览不读取完整附件。"""
    root = tmp_path
    folder = root.joinpath(*(['组合路径-' + 'guitar' * 8] * 5))
    filesystem_path(folder).mkdir(parents=True)
    target = folder / '配对参考.zip'
    filesystem_path(target).write_bytes(b'known-attachment')
    assert len(str(target)) > 260
    authorize(monkeypatch, root)
    native_open = Path.open

    def reject_payload(path, *args, **kwargs):
        if path.name == target.name:
            raise AssertionError('binary metadata preview must not open attachment bytes')
        return native_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', reject_payload)
        result = workspace_domain.get_workspace_link_file(str(target), actor='fixture')
    assert result['path'] == str(target)
    assert result['entry']['size'] == len(b'known-attachment')
    assert result['binary'] and not result['content']
    assert not result['path'].startswith('\\\\?\\')
    returned = workspace_domain.resolve_workspace_link_file_path(str(target), actor='fixture')
    raw = http_files._society_service_resolved_path(str(returned), 'fixture')
    assert raw.read_bytes() == b'known-attachment'


def test_text_preview_reads_only_limit_plus_one(tmp_path, monkeypatch):
    """超大文本在读取时限流，不在全量分配之后再截断。"""
    target = tmp_path / 'large.txt'
    target.write_bytes(b'a' * 64)
    authorize(monkeypatch, tmp_path)
    monkeypatch.setattr(workspace_domain, 'FILE_PREVIEW_MAX_BYTES', 16)
    native_open = Path.open
    reads = []

    class Reader:
        def __init__(self, file): self.file = file
        def __enter__(self): return self
        def __exit__(self, *args): self.file.close()
        def read(self, size=-1):
            reads.append(size)
            assert 0 <= size <= 17
            return self.file.read(size)

    def traced_open(path, *args, **kwargs):
        file = native_open(path, *args, **kwargs)
        return Reader(file) if path == target else file

    monkeypatch.setattr(Path, 'open', traced_open)
    result = workspace_domain.get_workspace_link_file(str(target), actor='fixture')
    assert reads == [17]
    assert result['truncated'] and result['content'] == 'a' * 16
