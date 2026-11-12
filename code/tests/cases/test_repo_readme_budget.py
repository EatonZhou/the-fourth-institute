"""Repo README previews bound the actual read, including a growing file."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.repo_workspace_files import _read_text_file


def test_readme_growth_cannot_bypass_the_preview_byte_limit(tmp_path, monkeypatch):
    path = tmp_path / 'README.md'
    path.write_bytes(b'x' * 8192)
    original_stat, original_open = Path.stat, Path.open
    reads = []

    def stale_stat(current, *args, **kwargs):
        if current == path:
            return SimpleNamespace(st_size=1)
        return original_stat(current, *args, **kwargs)

    @contextmanager
    def observed_open(current, *args, **kwargs):
        with original_open(current, *args, **kwargs) as stream:
            if current != path:
                yield stream
                return

            def read(size=-1):
                reads.append(size)
                return stream.read(size)

            yield SimpleNamespace(read=read)

    monkeypatch.setattr(Path, 'stat', stale_stat)
    monkeypatch.setattr(Path, 'open', observed_open)
    assert _read_text_file(path, max_bytes=16) == ('', True)
    assert reads == [17]


@pytest.mark.parametrize('data,limit,expected', [
    ('说明\r\n'.encode(), 20, ('说明\r\n', False)),
    (b'abcd', 4, ('abcd', False)),
    (b'abcde', 4, ('', True)),
    (b'a\x00b', 4, ('', True)),
    (b'\xff', 4, ('', True)),
])
def test_readme_preview_keeps_complete_text_and_binary_rules(tmp_path, data, limit, expected):
    path = tmp_path / 'README.md'
    path.write_bytes(data)
    assert _read_text_file(path, max_bytes=limit) == expected
