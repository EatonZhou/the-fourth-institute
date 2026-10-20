"""Resource file access keeps its scope on native and alternate path platforms."""
from __future__ import annotations

import os
import ctypes
import sys
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from management_portal import article_identity_backend as article
from management_portal import paper_backend as paper
from management_portal import project_rename, repo_workspace_backend as repo
from management_portal.resources.providers import folder_files
from management_portal.workbench_projections import host_directories
from management_common import secure_paths
from management_common import file_lock, file_permissions
from knowledge_board_lan import knowledge_backend


@pytest.mark.parametrize("value", ["../secret", "a/../secret", "/absolute"])
@pytest.mark.parametrize("cleaner", [article._safe_relative_path, paper._safe_paper_relative_path, repo._safe_relative_path])
def test_resource_relative_names_cannot_become_native_absolute_or_alias_paths(value, cleaner):
    with pytest.raises(ValueError):
        cleaner(value)


@pytest.mark.parametrize("value", [r"file/C:\outside", "file/C:outside", "file/note:stream", r"file/a\..\secret", "file/NUL", "file/COM1", "file/trailing."])
def test_folder_addresses_reject_native_path_escape(value, monkeypatch):
    monkeypatch.setattr(secure_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    with pytest.raises(ValueError):
        folder_files.file_parts(value)


@pytest.mark.parametrize("cleaner", [article._safe_relative_path, paper._safe_paper_relative_path, repo._safe_relative_path])
@pytest.mark.parametrize("value", ["C:/outside.txt", "C:outside.txt", r"a\..\secret", "note:stream", "NUL", "COM1"])
def test_consumer_windows_names_refuse_native_escape(cleaner, value, monkeypatch):
    monkeypatch.setattr(secure_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    with pytest.raises(ValueError):
        cleaner(value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX native names")
@pytest.mark.parametrize("name", ["note:stream", r"literal\name", "with\nnewline"])
def test_posix_root_and_file_names_retain_literal_colon_and_backslash(tmp_path, name):
    root = tmp_path.resolve() / name
    root.mkdir()
    (root / name).write_bytes(b"native contents")
    assert folder_files.file_parts("file/" + name) == (name,)
    with folder_files.open_target(root, (name,)) as (descriptor, _):
        assert os.read(descriptor, 100) == b"native contents"
    with secure_paths.open_directory(root, ("child", name), create=True):
        assert (root / "child" / name).is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX native names")
def test_posix_consumer_name_compatibility_is_preserved():
    for cleaner in (article._safe_relative_path, paper._safe_paper_relative_path):
        assert cleaner(r"dir:label/literal\name") == r"dir:label/literal\name"
        assert cleaner("dir//./file") == "dir/file"
    assert repo._safe_relative_path("dir:label/.Git/file") == "dir:label/.Git/file"
    with pytest.raises(ValueError):
        repo._safe_relative_path(r"literal\name")  # Existing Repo contract.


def test_paper_images_require_a_path_component_boundary(tmp_path, monkeypatch):
    root = tmp_path / "paper"
    root.mkdir()
    sibling = tmp_path / "paper-other"
    sibling.mkdir()
    outside = sibling / "image.png"
    outside.write_bytes(b"image")
    inside = root / "figure.png"
    inside.write_bytes(b"inside")
    monkeypatch.setattr(paper, "_paper_root", lambda _: root)
    assert paper._figure_image_path({}, {"image_rel_path": str(outside)}) is None
    assert paper._generated_figure_image_path({}, {"generated": [{"generated_id": "g", "image_rel_path": str(outside)}]}, "g") is None
    assert paper._figure_image_path({}, {"image_rel_path": "figure.png"}) == inside


def test_folder_reads_and_directory_browse_do_not_need_linux_proc(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "child").mkdir()
    (root / "text.txt").write_text("pinned", encoding="utf-8")
    real_open = os.open
    def without_proc(path, *args, **kwargs):
        if isinstance(path, (str, bytes)) and str(path).startswith("/proc/"):
            raise AssertionError("Linux procfs used for a resource file")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", without_proc)
    monkeypatch.setattr(host_directories, "authorize", lambda path, actor: None)
    monkeypatch.delattr(os, "O_PATH", raising=False)
    with folder_files.open_target(root, ("text.txt",)) as (fd, info):
        assert os.read(fd, 100) == b"pinned"
        assert info.st_size == 6
    assert host_directories.browse({"path": str(root)}, "alice")["directories"] == [{"name": "child", "path": str(root / "child")}]


def test_folder_open_pins_inode_and_refuses_links(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    target = root / "text.txt"
    target.write_bytes(b"authorized")
    outside = root / "outside.txt"
    outside.write_bytes(b"different")
    real_open = os.open
    def swap_after_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if path == "text.txt" and os.name != "nt":
            target.rename(root / "held.txt")
            target.symlink_to(outside)
        return fd
    monkeypatch.setattr(os, "open", swap_after_open)
    with folder_files.open_target(root, ("text.txt",)) as (fd, _):
        assert os.read(fd, 100) == b"authorized"
    if os.name != "nt":
        with pytest.raises(ValueError):
            with folder_files.open_target(root, ("text.txt",)):
                pytest.fail("symlink accepted")


def test_project_rename_rewrites_native_and_portable_windows_paths():
    change = project_rename.ProjectRename("Old", "New", PureWindowsPath("C:/share/Projects/Old"), PureWindowsPath("C:/share/Projects/New"), PureWindowsPath("C:/share"))
    rewrite = project_rename.Rewriter(change)
    assert rewrite.path("C:/share/Projects/Old/docs/main.md") == "C:/share/Projects/New/docs/main.md"
    assert rewrite.path(r"C:\share\Projects\Old\docs\main.md") == r"C:\share\Projects\New\docs\main.md"
    assert rewrite.path("C:/share/Projects/Older/main.md") == "C:/share/Projects/Older/main.md"


def test_project_text_discovery_needs_no_posix_grep(tmp_path, monkeypatch):
    (tmp_path / "file.txt").write_text("path /old/项目", encoding="utf-8")
    (tmp_path / "blob").write_bytes(b"\x00/old/\xe9\xa1\xb9\xe7\x9b\xae")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "cached.txt").write_text("/old/项目")
    monkeypatch.setattr(project_rename.subprocess, "run", lambda *a, **kw: pytest.fail("external grep used"))
    assert project_rename._grep_files(["/old/项目"], [str(tmp_path)], extra=("--exclude-dir=state",)) == [str(tmp_path / "file.txt")]


def test_directory_creation_only_creates_children_and_refuses_symlink_parents(tmp_path):
    root = tmp_path.resolve()
    with secure_paths.open_directory(root, (root.name, "nested"), create=True) as directory:
        fd = secure_paths.open_child(directory, "text", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.write(fd, b"new")
        os.close(fd)
    assert (root / root.name / "nested" / "text").read_bytes() == b"new"
    with pytest.raises(FileNotFoundError):
        with secure_paths.open_directory(root / "missing-root", ("child",), create=True):
            pytest.fail("missing registered root created")
    assert not (root / "missing-root").exists()
    if os.name != "nt":
        (root / "link").symlink_to(root / root.name, target_is_directory=True)
        with pytest.raises(ValueError):
            with secure_paths.open_directory(root, ("link", "escape"), create=True):
                pytest.fail("symlink traversed")
        assert not (root / root.name / "escape").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX special file")
def test_fifo_is_rejected_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ValueError, match="regular file"):
        with secure_paths.open_target(tmp_path.resolve(), ("pipe",)):
            pytest.fail("FIFO accepted")


@pytest.mark.parametrize("attributes,directory,error", [
    (0x10, True, None), (0x410, True, ValueError), (0x80, True, NotADirectoryError),
    (0x80, False, None), (0x10, False, IsADirectoryError),
])
def test_windows_open_checks_pinned_reparse_attributes_and_handle_ownership(monkeypatch, attributes, directory, error):
    from ctypes import wintypes
    calls, closed, converted = [], [], []
    def create(*args):
        calls.append(args)
        return 42
    def info(handle, kind, pointer, size):
        assert handle == 42 and kind == 9
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = attributes
        return 1
    def convert(handle, flags):
        converted.append((handle, flags))
        return 101
    monkeypatch.setattr(secure_paths, "_windows_api", lambda: SimpleNamespace(CreateFileW=create, GetFileInformationByHandleEx=info, CloseHandle=closed.append))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=convert))
    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    if error:
        with pytest.raises(error):
            secure_paths._windows_open(r"\\server\share\folder", os.O_RDONLY, directory=directory)
        assert closed == [42] and converted == []
    else:
        assert secure_paths._windows_open(r"\\server\share\folder", os.O_RDONLY, directory=directory) == 101
        assert closed == [] and converted == [(42, os.O_RDONLY | os.O_BINARY)]
    path, access, sharing, _, creation, flags, _ = calls[0]
    assert path == r"\\server\share\folder"
    assert access == 0x80000000 and sharing == (1 if directory else 3) and creation == 3
    assert flags == 0x02200000


def test_windows_open_closes_handle_when_crt_conversion_fails(monkeypatch):
    closed = []
    monkeypatch.setattr(secure_paths, "_windows_api", lambda: SimpleNamespace(CreateFileW=lambda *a: 42, GetFileInformationByHandleEx=lambda *a: 1, CloseHandle=closed.append))
    def fail(*args):
        raise OSError("descriptor limit")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=fail))
    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    with pytest.raises(OSError, match="descriptor limit"):
        secure_paths._windows_open("C:/root/file", os.O_RDONLY)
    assert closed == [42]


@pytest.mark.parametrize("root,children", [
    ("C:/root/docs", ["C:\\", "C:\\root", "C:\\root\\docs", "C:\\root\\docs\\child"]),
    ("//server/share/root", ["\\\\server\\share\\", "\\\\server\\share\\root", "\\\\server\\share\\root\\child"]),
])
def test_windows_directory_walk_pins_all_native_ancestors_until_exit(monkeypatch, root, children):
    opened, closed = {}, []
    def native_open(path, flags, *, directory=False):
        assert directory
        fd = len(opened) + 10
        opened[fd] = str(path)
        return fd
    monkeypatch.setattr(secure_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt", "close": closed.append}))
    monkeypatch.setattr(secure_paths, "Path", PureWindowsPath)
    monkeypatch.setattr(secure_paths, "_windows_open", native_open)
    monkeypatch.setattr(secure_paths, "directory_path", lambda fd: PureWindowsPath(opened[fd]))
    with secure_paths.open_directory(root, ("child",)) as fd:
        assert list(opened.values()) == children and closed == []
        assert fd == max(opened)
    assert closed == list(reversed(opened))


def test_host_listing_rechecks_pinned_directory_after_rename(tmp_path, monkeypatch):
    path = tmp_path.resolve() / "root"
    path.mkdir()
    (path / "child").mkdir()
    called = 0
    def authorize(current, actor):
        nonlocal called
        if current == path:
            called += 1
            if called == 2:
                path.rename(tmp_path / "moved")
                path.mkdir()
    monkeypatch.setattr(host_directories, "authorize", authorize)
    if os.name == "nt":
        with pytest.raises(PermissionError):
            host_directories.browse({"path": str(path)}, "alice")
    else:
        with pytest.raises(PermissionError, match="位置已变化"):
            host_directories.browse({"path": str(path)}, "alice")


def test_project_large_text_reference_still_reports_unrewritten_file(tmp_path, monkeypatch):
    path = tmp_path / "large.txt"
    path.write_text("x" * (128 * 1024 - 3) + "/old/project" + "y" * 30)
    monkeypatch.setattr(project_rename, "MAX_REWRITE_BYTES", 8)
    found = project_rename._grep_files(["/old/project"], [str(tmp_path)])
    assert found == [str(path)]
    result = project_rename.FileChanges()
    assert not project_rename._rewrite_file(path, lambda value: value.replace("/old", "/new"), result)
    assert result.errors[0]["path"] == str(path)
    assert "/old/project" in path.read_text()


def test_project_native_mount_blocks_rename_without_proc(tmp_path, monkeypatch):
    old = tmp_path / "Old"
    mount = old / "volume"
    mount.mkdir(parents=True)
    monkeypatch.setattr(project_rename, "MOUNTINFO", tmp_path / "absent")
    monkeypatch.setattr(project_rename, "SYSTEMD_UNIT_DIRS", ())
    monkeypatch.setattr(project_rename.os.path, "ismount", lambda value: Path(value) == mount)
    change = project_rename.ProjectRename("Old", "New", old, tmp_path / "New", tmp_path)
    assert project_rename.external_blockers(change, project_rename.Rewriter(change)) == [{"kind": "mount", "path": str(mount)}]


@pytest.mark.parametrize("platform,relative", [("linux", ".TinyTeX/bin/aarch64-linux"), ("darwin", "Library/TinyTeX/bin/universal-darwin"), ("win32", "AppData/Roaming/TinyTeX/bin/windows")])
def test_tex_lookup_uses_installed_user_platform_directory(tmp_path, monkeypatch, platform, relative):
    tools = tmp_path / relative
    tools.mkdir(parents=True)
    monkeypatch.setattr(article.sys, "platform", platform)
    monkeypatch.setattr(article.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    assert article._tinytex_bin_dirs() == (tools,)


@pytest.mark.parametrize("deny", [False, True])
def test_git_credential_permissions_precede_payload_and_cleanup(tmp_path, monkeypatch, deny):
    paths = []
    create = repo.tempfile.mkstemp
    def temporary(**kwargs):
        return create(**kwargs, dir=tmp_path)
    def permissions(path, mode):
        paths.append(path)
        assert mode == 0o600 and path.read_bytes() == b""
        if deny:
            raise PermissionError("ACL unavailable")
    monkeypatch.setattr(repo.tempfile, "mkstemp", temporary)
    monkeypatch.setattr(file_permissions, "set_created_file_permissions", permissions)
    if deny:
        with pytest.raises(PermissionError, match="ACL unavailable"):
            with repo._git_credential_material({}, username="fixture", secret="synthetic-test"):
                pytest.fail("unprotected credential returned")
    else:
        with repo._git_credential_material({}, username="fixture", secret="synthetic-test") as path:
            assert paths == [path] and "synthetic-test" in path.read_text()
    assert len(paths) == 1 and not paths[0].exists()


def test_knowledge_atomic_writes_set_permissions_before_payload_and_never_overwrite_create(tmp_path, monkeypatch):
    target = tmp_path / "record.md"
    target.write_text("original")
    calls = []
    def permissions(temporary, source, mode):
        assert temporary.read_bytes() == b"" and source == target
        calls.append(mode)
    monkeypatch.setattr(file_permissions, "prepare_atomic_permissions", permissions)
    knowledge_backend._write_text_atomic(target, "updated")
    assert target.read_text() == "updated" and calls
    with pytest.raises(FileExistsError):
        knowledge_backend._write_new_text_atomic(target, "replacement")
    assert target.read_text() == "updated" and sorted(tmp_path.iterdir()) == [target]


def _assert_held(path):
    with path.open("a+b") as other:
        acquired = file_lock.try_lock_descriptor(other.fileno())
        if acquired:
            file_lock.unlock_descriptor(other.fileno())
        assert not acquired


def _assert_released(path):
    with path.open("a+b") as other:
        assert file_lock.try_lock_descriptor(other.fileno())
        file_lock.unlock_descriptor(other.fileno())


@pytest.mark.parametrize("failure", [False, True])
def test_paper_enrichment_lock_covers_cache_read_and_releases_on_exit(tmp_path, monkeypatch, failure):
    cache = tmp_path / "enrichment.json"
    lock = cache.with_suffix(".lock")
    lock.write_bytes(b"existing lock contents")
    monkeypatch.setattr(paper, "_ccf_enrichment_cache_path", lambda _: cache)
    def read(*args):
        _assert_held(lock)
        if failure:
            raise OSError("cache failed")
        return {"status": "done"}
    monkeypatch.setattr(paper, "_load_json", read)
    if failure:
        with pytest.raises(OSError, match="cache failed"):
            paper.ccf_paper_enrichment({"paper": {"title": "fixture"}}, actor="fixture")
    else:
        assert paper.ccf_paper_enrichment({"paper": {"title": "fixture"}}, actor="fixture") == {"status": "done"}
    _assert_released(lock)
    assert lock.read_bytes() == b"existing lock contents"


@pytest.mark.parametrize("missing", [False, True])
def test_paper_asset_lock_covers_lookup_and_preserves_contents(tmp_path, monkeypatch, missing):
    lock = tmp_path / "assets.lock"
    lock.write_bytes(b"stable lock")
    monkeypatch.setattr(paper, "_paper_asset_lock_path", lambda: lock)
    monkeypatch.setattr(paper, "_paper_run_record_by_job_id", lambda *a: {"status": "queued"})
    monkeypatch.setattr(paper, "_update_paper_run_record", lambda *a, **kw: None)
    def lookup(*args):
        _assert_held(lock)
        return None if missing else {"paper_id": "fixture"}
    monkeypatch.setattr(paper, "_paper_by_id", lookup)
    monkeypatch.setattr(paper, "_local_pdf_for_paper", lambda _: None)
    if missing:
        with pytest.raises(KeyError):
            paper._run_assets_job("fixture", job_id="fixture")
    else:
        assert paper._run_assets_job("fixture", job_id="fixture")["skipped"]
    _assert_released(lock)
    assert lock.read_bytes() == b"stable lock"


@pytest.mark.parametrize("root,expected", [(r"\\?\C:\root", r"C:\root"), (r"\\?\UNC\server\share\root", r"\\server\share\root")])
def test_pinned_windows_acl_delegates_drive_and_unc_paths(monkeypatch, root, expected):
    calls = []
    monkeypatch.setattr(secure_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(secure_paths, "Path", PureWindowsPath)
    monkeypatch.setattr(secure_paths, "directory_path", lambda fd: PureWindowsPath(root))
    monkeypatch.setattr(file_permissions, "prepare_atomic_permissions", lambda *args: calls.append(args))
    secure_paths.prepare_child_permissions(10, "temp", "target", 0o664)
    assert calls == [(PureWindowsPath(expected) / "temp", PureWindowsPath(expected) / "target", 0o664)]


def test_windows_directory_failure_closes_all_acquired_ancestors(monkeypatch):
    opened, closed = {}, []
    def native_open(path, flags, *, directory=False):
        if PureWindowsPath(path).name == "forbidden":
            raise PermissionError("ACL denied")
        fd = len(opened) + 10
        opened[fd] = str(path)
        return fd
    monkeypatch.setattr(secure_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt", "close": closed.append}))
    monkeypatch.setattr(secure_paths, "Path", PureWindowsPath)
    monkeypatch.setattr(secure_paths, "_windows_open", native_open)
    monkeypatch.setattr(secure_paths, "directory_path", lambda fd: PureWindowsPath(opened[fd]))
    with pytest.raises(PermissionError, match="ACL denied"):
        with secure_paths.open_directory("C:/root/forbidden"):
            pytest.fail("denied directory accepted")
    assert list(opened.values()) == ["C:\\", "C:\\root"]
    assert closed == list(reversed(opened))


def test_windows_write_lock_uses_stable_sidecar_released_on_failure(tmp_path, monkeypatch):
    calls = []
    lock = tmp_path / ".portal-write.lock"
    lock.write_bytes(b"keep")
    def child(directory, name, flags, mode):
        calls.append((directory, name))
        return os.open(tmp_path / name, flags, mode)
    monkeypatch.setattr(secure_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(secure_paths, "open_child", child)
    for _ in range(2):
        with pytest.raises(OSError, match="replace failed"):
            with secure_paths.lock_directory(42):
                _assert_held(lock)
                raise OSError("replace failed")
        _assert_released(lock)
    assert calls == [(42, ".portal-write.lock")] * 2 and lock.read_bytes() == b"keep"


def test_windows_native_open_failure_does_not_close_invalid_handle(monkeypatch):
    closed = []
    monkeypatch.setattr(secure_paths, "_windows_api", lambda: SimpleNamespace(CreateFileW=lambda *a: ctypes.c_void_p(-1).value, CloseHandle=closed.append))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=lambda *a: pytest.fail("invalid native handle converted")))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: PermissionError("native access denied"), raising=False)
    with pytest.raises(PermissionError, match="native access denied"):
        secure_paths._windows_open("C:/root/file", os.O_RDONLY)
    assert closed == []


@pytest.fixture
def workspace_writer(tmp_path, monkeypatch):
    from management_portal.resources import isa
    from management_portal.society_service import lineage_access_policy
    target = tmp_path / "document.md"
    target.write_text("original")
    notifications = []
    service = SimpleNamespace(
        get=lambda *a, **kw: {"attributes": {"path": str(tmp_path)}},
        notify_source_changed=lambda **kw: notifications.append(kw),
    )
    monkeypatch.setattr(isa, "_service", lambda: service)
    monkeypatch.setattr(lineage_access_policy, "workspace_reaches", lambda *a, **kw: True)
    def write(text):
        return isa.update_workspace_file(SimpleNamespace(
            argument={"address": "workspace_0123456789ab/file/document.md", "content": text}.get,
            caller="fixture", expected_version=hashlib.sha256(b"original").hexdigest(),
        ))
    return SimpleNamespace(write=write, target=target, service=service, notifications=notifications)


def test_workspace_concurrent_compare_replace_has_one_winner(workspace_writer):
    from management_portal.hatg.contracts import InstructionError
    barrier = threading.Barrier(2)
    get = workspace_writer.service.get
    def synchronized_get(*args, **kw):
        record = get(*args, **kw)
        barrier.wait(timeout=5)
        return record
    workspace_writer.service.get = synchronized_get
    def write(text):
        try:
            return workspace_writer.write(text)
        except InstructionError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["first", "second"]))
    assert results.count("instruction.version_conflict") == 1
    assert workspace_writer.target.read_text() in {"first", "second"}
    assert len(workspace_writer.notifications) == 1


def test_workspace_replace_failure_preserves_original_and_releases_lock(workspace_writer, monkeypatch):
    def fail(*args):
        raise OSError("replacement failed")
    with monkeypatch.context() as context:
        context.setattr(secure_paths, "replace_child", fail)
        with pytest.raises(OSError, match="replacement failed"):
            workspace_writer.write("first")
    assert workspace_writer.target.read_text() == "original"
    assert not list(workspace_writer.target.parent.glob("*.tmp"))
    assert workspace_writer.notifications == []
    workspace_writer.write("after failure")
    assert workspace_writer.target.read_text() == "after failure"


@pytest.mark.skipif(os.name == "nt", reason="native symlink setup requires Windows privilege")
def test_workspace_cas_refuses_symlink_substituted_after_stat(workspace_writer, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.write_text("outside")
    original_open = secure_paths.open_child
    def substitute(directory, name, flags, *args, **kwargs):
        if name == "document.md":
            workspace_writer.target.unlink()
            workspace_writer.target.symlink_to(outside)
        return original_open(directory, name, flags, *args, **kwargs)
    monkeypatch.setattr(secure_paths, "open_child", substitute)
    with pytest.raises(OSError):
        workspace_writer.write("write attempt")
    assert outside.read_text() == "outside" and workspace_writer.notifications == []


def test_project_resident_bindings_keep_native_and_portable_path_spellings(monkeypatch):
    from management_portal.society_service import resource_client
    change = project_rename.ProjectRename("Old", "New", PureWindowsPath("C:/share/Old"), PureWindowsPath("C:/share/New"), PureWindowsPath("C:/share"))
    monkeypatch.setattr(project_rename, "portal_database", lambda **kw: SimpleNamespace(snapshot=lambda: nullcontext(None)))
    monkeypatch.setattr(project_rename, "_current_resident_workspaces", lambda *a: [("resident", "workspace")])
    monkeypatch.setattr(resource_client, "workspace_paths_many", lambda *a, **kw: {
        "a": "C:/share/New/child", "b": r"C:\share\New\child", "c": "C:/share/Newer/child",
    })
    assert project_rename._resident_paths(change, ["resident"]) == [
        ("C:/share/Old/child", "C:/share/New/child"), (r"C:\share\Old\child", r"C:\share\New\child"),
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX native names")
@pytest.mark.parametrize("name", ["note:stream", r"literal\name"])
def test_posix_workspace_write_preserves_native_root_and_suffix(workspace_writer, tmp_path, name):
    from management_portal.resources import isa
    root = tmp_path / name
    root.mkdir()
    target = root / name
    target.write_text("original")
    workspace_writer.service.get = lambda *a, **kw: {"attributes": {"path": str(root)}}
    isa.update_workspace_file(SimpleNamespace(
        argument={"address": "workspace_0123456789ab/file/" + name, "content": "native saved"}.get,
        caller="fixture", expected_version=hashlib.sha256(b"original").hexdigest(),
    ))
    assert target.read_text() == "native saved"
    with folder_files.open_target(root, folder_files.file_parts("file/" + name)) as (fd, _):
        assert os.read(fd, 100) == b"native saved"
