"""Bounded work at resource storage and private-access boundaries, using synthetic data."""
from contextlib import contextmanager
from types import SimpleNamespace
import stat

import pytest

from knowledge_board_lan import knowledge_source
from management_portal.permission_system import personal_ama, temporary
from management_portal.permission_system.engine import Entry, PERMIT
from management_portal.resources.providers import folder_files


@pytest.mark.parametrize("count", [64, 256])
def test_private_delegation_does_not_rescan_all_loans_per_resource(monkeypatch, count):
    class ObservedEntry(Entry):
        reads = 0

        def __getattribute__(self, name):
            if name == "r":
                type(self).reads += 1
            return super().__getattribute__(name)

    owners = {f"resident_{i:012x}": "owner" for i in range(count)}
    entries = [ObservedEntry(str(i), PERMIT, "recipient", "@viewer", resource,
                             issued_by="owner", expires_at=100)
               for i, resource in enumerate(owners)]
    action_path = lambda action: ("@actions", "@viewer", "ReadResource") if action == "ReadResource" else ("@actions", action)
    engine = SimpleNamespace(
        grants=lambda principal, ids: ((), SimpleNamespace(entries=entries),
                                      {resource: ("@portal", resource) for resource in ids}),
        clock=lambda: 50, a_path=action_path,
    )
    monkeypatch.setattr(personal_ama, "owner_of", lambda principal: "")
    monkeypatch.setattr(personal_ama, "owners_of", lambda principals: {})
    ObservedEntry.reads = 0
    assert temporary.delegated_ids(engine, principal="recipient", owners=owners, action="ReadResource") == set(owners)
    # A fixed number of reads per credential; no wall-clock threshold.
    assert ObservedEntry.reads <= 20 * count, {"loans": count, "resource_field_reads": ObservedEntry.reads}
    print({"loans": count, "resource_field_reads": ObservedEntry.reads})


def test_directory_listing_retains_only_the_returned_page(monkeypatch):
    count = 2048
    rows = [(f".hidden{i}" if i % 13 == 0 else f"entry{count-i:05d}",
             stat.S_IFDIR if i % 11 == 0 else stat.S_IFLNK if i % 17 == 0 else stat.S_IFREG)
            for i in range(count)]
    rows += [("line\nname", stat.S_IFREG), ("C:bad", stat.S_IFREG), ("Aa", stat.S_IFREG), ("aa", stat.S_IFREG)]
    observed = {"live": 0, "peak": 0, "stats": 0}

    class Info:
        def __init__(self, mode):
            self.st_mode, self.st_size, self.st_mtime, self.st_mtime_ns = mode, 10, 1, 1000000000
            observed["live"] += 1
            observed["peak"] = max(observed["peak"], observed["live"])

        def __del__(self):
            observed["live"] -= 1

    class DirectoryEntry:
        def __init__(self, name, mode):
            self.name, self.mode = name, mode

        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            observed["stats"] += 1
            if self.name == "vanished":
                raise FileNotFoundError(self.name)
            return Info(self.mode)

    @contextmanager
    def scan(fd):
        assert fd == 123
        yield (DirectoryEntry(name, mode) for name, mode in [*rows, ("vanished", stat.S_IFREG)])

    monkeypatch.setattr(folder_files.secure_paths, "scandir", scan)
    result = folder_files._listing(123, "workspace_abcdef012345", ())
    visible = [(name, mode) for name, mode in rows if not name.startswith(".") and name != "C:bad"]
    expected = sorted(visible, key=lambda row: (not stat.S_ISDIR(row[1]), row[0].casefold()))[:200]
    assert [item["name"] for item in result["entries"]] == [name for name, mode in expected]
    assert result["truncated"] is True
    assert result["hidden_count"] == len(rows) - len(visible)
    assert observed["stats"] == len(rows) + 1
    assert observed["peak"] <= 2 * folder_files.MAX_DIRECTORY_ENTRIES + 5, observed
    print(observed)


@pytest.mark.parametrize("reported_size", [0, 1])
def test_knowledge_text_read_remains_bounded_after_growth(tmp_path, monkeypatch, reported_size):
    target = tmp_path / "growing.md"
    target.write_bytes("abc汉字".encode("utf-8") * 1000)
    # A source can grow between stat and open; the read itself must enforce the byte bound.
    monkeypatch.setattr(knowledge_source.os.path, "getsize", lambda path: reported_size)
    assert knowledge_source._read_file_text(str(target), max_bytes=5) == "abc�"


@pytest.mark.parametrize("content,limit,expected", [
    (b"a\r\nb\rc\n", 100, "a\nb\nc\n"),
    (b"a\r\nb\rc\n", 4, "a\r\nb"),
    (b"abc", 0, ""),
    (b"\xff", 10, ""),
])
def test_knowledge_text_read_keeps_decoding_contract(tmp_path, content, limit, expected):
    target = tmp_path / "text.md"
    target.write_bytes(content)
    assert knowledge_source._read_file_text(str(target), max_bytes=limit) == expected
    assert knowledge_source._read_file_text(str(tmp_path / "missing.md"), max_bytes=limit) == ""
