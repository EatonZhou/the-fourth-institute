"""Application builds exercise real Git, npm and bubblewrap without networking.

:module: workbench.panels
:importance: P1
:design: decision_7a5ae487e2e9
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from management_portal.workbench_projections import application_build as builder
from management_portal.workbench_projections.store import ProjectionError


REPO = "repo_0123456789ab"
ACTOR = "resident_0123456789ab"


def command(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "app"
    source.mkdir(parents=True)
    command("git", "init", "-q", cwd=repo)
    command("git", "config", "user.name", "Application build fixture", cwd=repo)
    command("git", "config", "user.email", "fixture@example.invalid", cwd=repo)
    package = {"name": "test-projection", "version": "1.0.0", "private": True, "license": "UNLICENSED",
               "scripts": {"build": "node build.mjs", "prebuild": "exit 91", "postbuild": "exit 92", "postinstall": "exit 93"}}
    (source / "package.json").write_text(json.dumps(package))
    (source / "build.mjs").write_text("import fs from 'node:fs'; fs.mkdirSync('dist'); fs.writeFileSync('dist/architecture.html', '<p>committed</p>');")
    monkeypatch.setenv("PORTAL_PROJECTION_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PORTAL_PROJECTION_NPM_CACHE", str(tmp_path / "cache"))
    calls = []

    def resolve(address, *, actor):
        calls.append((address, actor))
        assert address == REPO and actor == ACTOR
        return {"root_id": REPO, "resource_type": "repo", "exists": True, "checkout_path": str(repo)}

    monkeypatch.setattr(builder, "get_resource_service", lambda: SimpleNamespace(resolve=resolve))

    def commit():
        command("git", "add", "-A", cwd=repo)
        command("git", "commit", "-qm", "fixture", "--allow-empty", cwd=repo)
        return {"repo_id": REPO, "directory": "app", "commit": command("git", "rev-parse", "HEAD", cwd=repo),
                "entries": {"architecture": "architecture.html"}}

    return SimpleNamespace(root=tmp_path, repo=repo, source=source, commit=commit, calls=calls, monkeypatch=monkeypatch)


def cache_dependency(project, *, license="MIT"):
    """Create a tarball and populate an isolated npm cache, without an npm server."""
    package = {"name": "projection-build-fixture", "version": "1.0.0", "license": license,
               "scripts": {"install": "node -e \"require('fs').writeFileSync('/app/HOOK_EXECUTED','bad')\""}}
    tarball = project.root / "dependency.tgz"
    with tarfile.open(tarball, "w:gz") as archive:
        data = json.dumps(package).encode()
        member = tarfile.TarInfo("package/package.json")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    command("/usr/bin/npm", "cache", "add", str(tarball), "--offline", "--ignore-scripts", "--no-audit", "--no-fund",
            "--cache=" + str(project.root / "cache"), cwd=project.root)
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
    package_path = project.source / "package.json"
    root_package = json.loads(package_path.read_text())
    root_package["dependencies"] = {package["name"]: "*"}
    package_path.write_text(json.dumps(root_package))
    lock = {"name": root_package["name"], "version": "1.0.0", "lockfileVersion": 3,
            "packages": {"": {"name": root_package["name"], "version": "1.0.0", "dependencies": root_package["dependencies"]},
                         "node_modules/" + package["name"]: {"version": "1.0.0", "license": license, "integrity": integrity,
                                                                "resolved": "https://registry.npmjs.org/projection-build-fixture/-/projection-build-fixture-1.0.0.tgz"}}}
    # The fixture supplies resolver output without relying on registry metadata.
    # The application source itself has no lockfile.
    resolution = project.root / "resolved.json"
    resolution.write_text(json.dumps(lock))
    def resolve(source, cache):
        shutil.copytree(project.root / "cache", cache)
        (source / "package-lock.json").write_bytes(resolution.read_bytes())

    project.monkeypatch.setattr(builder, "_resolve_lock", resolve)
    original_sandbox = builder._sandbox
    project.monkeypatch.setattr(builder, "_sandbox",
                                lambda source, args, *, cache, network=False: original_sandbox(source, args, cache=cache))
    return lock


def test_dependency_cache_rejects_oversized_package(tmp_path):
    """A build refuses a registry package beyond the per-file size limit."""
    cache = tmp_path / "cache"
    cache.mkdir()
    with (cache / "oversized").open("wb") as output:
        output.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(ProjectionError, match="依赖缓存超出大小上限"):
        builder._check_cache(cache)


def test_source_lock_is_replaced_by_current_resolution(tmp_path):
    """A committed lock cannot choose the dependency set for a new build."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(json.dumps({
        "name": "unlocked-build", "version": "1.0.0", "private": True,
        "scripts": {"build": "node build.mjs"},
    }))
    (source / "package-lock.json").write_text('{"lockfileVersion": 999, "packages": {}}')
    builder._resolve_lock(source, tmp_path / "cache")
    assert set(builder._lock(source)["packages"]) == {""}


def test_real_build_isolates_network_host_environment_and_scripts(project, monkeypatch):
    """应用源码构建隔离网络、宿主环境与生命周期脚本

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    cache_dependency(project, license="(MPL-2.0 OR Apache-2.0)")
    host_cache = project.root / "cache/_cacache"
    original_cache = {str(path.relative_to(host_cache)): (path.stat().st_size, path.stat().st_mtime_ns)
                      for path in host_cache.rglob("*") if path.is_file()}
    secret = project.root / "private-sentinel"
    secret.write_text("must not be visible")
    monkeypatch.setenv("PROJECTION_TEST_SECRET", "must not inherit")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        (project.source / "build.mjs").write_text(f"""
import fs from 'node:fs';
import net from 'node:net';
import assert from 'node:assert/strict';
import {{createRequire}} from 'node:module';
const cacache = createRequire(import.meta.url)('/usr/lib/node_modules/npm/node_modules/cacache');
assert.equal(process.env.PROJECTION_TEST_SECRET, undefined);
assert.equal(fs.existsSync({json.dumps(str(secret))}), false);
assert.equal(fs.existsSync('/source-cache'), false);
assert.throws(() => fs.writeFileSync('/npm-cache/_cacache/attempt', 'bad'), {{code:'EROFS'}});
assert.equal(fs.existsSync('/app/HOOK_EXECUTED'), false);
assert.equal(JSON.parse(fs.readFileSync('node_modules/projection-build-fixture/package.json')).version, '1.0.0');
await new Promise((resolve, reject) => {{
  const client = net.connect({port}, '127.0.0.1');
  client.once('connect', () => {{client.destroy(); reject(new Error('host network reachable'));}});
  client.once('error', resolve);
  client.setTimeout(1500, () => {{client.destroy(); reject(new Error('network check timed out'));}});
}});
fs.mkdirSync('dist');
fs.writeFileSync('dist/architecture.html', '<p>committed</p>');
fs.writeFileSync('dist/layout.worker.js', 'self.onmessage = e => postMessage(e.data);');
""")
        application = project.commit()
        # The current checkout can be dirty; only the pinned Git commit runs.
        (project.source / "build.mjs").write_text("throw new Error('uncommitted script ran');")
        build = builder.build_application(application, ACTOR)
        server.settimeout(0.01)
        with pytest.raises(TimeoutError):
            server.accept()
    assert project.calls == [(REPO, ACTOR)]
    assert original_cache == {str(path.relative_to(host_cache)): (path.stat().st_size, path.stat().st_mtime_ns)
                              for path in host_cache.rglob("*") if path.is_file()}
    assert build["status"] == "ready" and build["commit"] == application["commit"]
    assert len(build["artifact_hash"]) == 64
    assert build["dependencies"][0]["license"] == "(MPL-2.0 OR Apache-2.0)"
    assert builder.read_artifact(build, "architecture.html") == (b"<p>committed</p>", "text/html; charset=utf-8")
    assert "layout.worker.js" in build["files"]
    artifact = project.root / "artifacts" / build["artifact_hash"] / "files/architecture.html"
    mtime = artifact.stat().st_mtime_ns
    # The deduplication path verifies, rather than replaces, the first bytes.
    assert builder._store({key: build[key] for key in ("entries", "files")},
                          {path: builder.read_artifact(build, path)[0] for path in build["files"]}) == build["artifact_hash"]
    assert artifact.stat().st_mtime_ns == mtime


def test_source_authorization_precedes_git_and_cannot_be_bypassed(project, monkeypatch):
    """应用包源码鉴权先于 Git 读取且不可绕过

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    application = project.commit()
    seen = []

    def denied(address, *, actor):
        seen.append((address, actor))
        raise PermissionError("repo forbidden")

    monkeypatch.setattr(builder, "get_resource_service", lambda: SimpleNamespace(resolve=denied))
    monkeypatch.setattr(builder, "_command", lambda *a, **kw: pytest.fail("unauthorized source reached Git"))
    with pytest.raises(PermissionError, match="repo forbidden"):
        builder.build_application(application, ACTOR)
    assert seen == [(REPO, ACTOR)]
    with pytest.raises(PermissionError, match="真实调用者"):
        builder.build_application(application, "")
    with pytest.raises(ProjectionError):
        builder.build_application({**application, "actor": ACTOR}, ACTOR)
    assert not (project.root / "artifacts").exists()


def test_source_snapshot_rejects_links_submodules_and_npm_config(project, subtests):
    """应用包源码快照拒绝链接、子模块与 npm 配置

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    for path, setup in [
        ("escape.js", lambda path: path.symlink_to("/etc/passwd")),
        (".npmrc", lambda path: path.write_text("ignore-scripts=false")),
        ("node_modules/x.js", lambda path: (path.parent.mkdir(), path.write_text("bad"))),
    ]:
        target = project.source / path
        setup(target)
        with subtests.test(path=path), pytest.raises(ProjectionError):
            builder.build_application(project.commit(), ACTOR)
        target.unlink()
        if target.parent != project.source:
            target.parent.rmdir()
    baseline = project.commit()
    command("git", "update-index", "--add", "--cacheinfo", "160000," + baseline["commit"] + ",app/submodule", cwd=project.repo)
    command("git", "commit", "-qm", "submodule", cwd=project.repo)
    with pytest.raises(ProjectionError, match="子模块"):
        builder.build_application({**baseline, "commit": command("git", "rev-parse", "HEAD", cwd=project.repo)}, ACTOR)
    assert not (project.root / "artifacts").exists()


def test_snapshot_uses_blobs_without_export_attributes_or_archive_commands(project):
    """应用包快照读取 Git 对象且不受导出属性影响

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    (project.source / ".gitattributes").write_text("build.mjs export-ignore\narchitecture.html export-subst\n")
    (project.source / "architecture.html").write_text("$Format:%H$")
    (project.source / "helper.sh").write_text("#!/bin/sh\nexit 0\n")
    (project.source / "helper.sh").chmod(0o755)
    application = project.commit()
    # An external archive command must never be selected on the host.
    command("git", "config", "tar.tar.command", "/does-not-exist", cwd=project.repo)
    destination = project.root / "snapshot"
    destination.mkdir()
    builder._snapshot(application, ACTOR, destination)
    assert (destination / "build.mjs").is_file()
    assert (destination / "architecture.html").read_text() == "$Format:%H$"
    assert (destination / "helper.sh").stat().st_mode & 0o111


def test_resolved_dependencies_and_license_policy_fail_before_scripts(project, monkeypatch, subtests):
    """构建时解析结果与许可不合规时禁止执行构建脚本

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    lock = cache_dependency(project)
    dependency_path = "node_modules/projection-build-fixture"
    monkeypatch.setattr(builder, "_sandbox", lambda *a, **kw: pytest.fail("invalid lock reached npm"))
    for change in [{"version": "^1.0.0"}, {"integrity": "sha512-bad"}, {"resolved": "file:../../escape"},
                   {"license": "GPL-3.0-only"}, {"license": "MIT OR ("}, {"link": True}]:
        edited = json.loads(json.dumps(lock))
        edited["packages"][dependency_path].update(change)
        (project.root / "resolved.json").write_text(json.dumps(edited))
        with subtests.test(change=change), pytest.raises(ProjectionError):
            builder.build_application(project.commit(), ACTOR)
    monkeypatch.setattr(builder, "_resolve_lock", lambda source, cache: None)
    with pytest.raises(ProjectionError, match="完整性记录"):
        builder.build_application(project.commit(), ACTOR)
    assert not (project.root / "artifacts").exists()


def test_npm_cache_miss_and_build_error_leave_no_artifact(project, subtests):
    """离线缓存缺失与构建失败不产生可发布产物

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    cache_dependency(project)
    shutil.rmtree(project.root / "cache")
    with subtests.test(stage="offline dependency miss"), pytest.raises(ProjectionError):
        builder.build_application(project.commit(), ACTOR)
    cache_dependency(project)
    (project.source / "build.mjs").write_text("throw new Error('fixture build failure');")
    with subtests.test(stage="compiler error"), pytest.raises(ProjectionError, match="fixture build failure"):
        builder.build_application(project.commit(), ACTOR)
    assert not (project.root / "artifacts").exists()


def test_bubblewrap_unavailable_has_no_host_execution_fallback(project, monkeypatch):
    """隔离器不可用时禁止回退到宿主构建

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    application = project.commit()
    original = builder.subprocess.Popen
    launches = []

    def unavailable(argv, **kwargs):
        launches.append(argv)
        assert "/usr/bin/npm" not in argv or "/usr/bin/bwrap" in argv
        if "/usr/bin/bwrap" in argv:
            raise PermissionError("namespace unavailable")
        return original(argv, **kwargs)

    monkeypatch.setattr(builder.subprocess, "Popen", unavailable)
    with pytest.raises(ProjectionError, match="宿主"):
        builder.build_application(application, ACTOR)
    assert any("/usr/bin/bwrap" in argv for argv in launches)
    assert not (project.root / "artifacts").exists()
    assert not (project.source / "dist").exists()


def test_artifact_checks_reject_escapes_links_oversize_and_missing_entry(project, subtests):
    """构建产物拒绝越界、链接、超限与缺失入口

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    application = project.commit()
    dist = project.source / "dist"
    dist.mkdir()
    (dist / "architecture.html").write_text("12345")
    cases = [
        ("file link", lambda: (dist / "escape.js").symlink_to(project.source / "build.mjs")),
        ("directory link", lambda: (dist / "assets").symlink_to(project.source, target_is_directory=True)),
        ("hard link", lambda: (dist / "escape.js").hardlink_to(project.source / "build.mjs")),
        ("unknown MIME", lambda: (dist / "escape.sh").write_text("exit 0")),
    ]
    for name, setup in cases:
        setup()
        with subtests.test(name=name), pytest.raises((OSError, ProjectionError)):
            builder._output(project.source, application)
        for file in dist.iterdir():
            if file.name != "architecture.html":
                file.unlink()
    with pytest.raises(ProjectionError, match="上限"):
        builder._output(project.source, {**application, "max_bytes": 4})
    with pytest.raises(ProjectionError, match="入口"):
        builder._output(project.source, {**application, "entries": {"missing": "missing.html"}})
    for path in ["../escape.js", "/etc/passwd", "a//b.js", "a/../b.js", "%2e%2e/b.js", "a\\b.js"]:
        with subtests.test(path=path), pytest.raises(ProjectionError):
            builder._path(path)


def test_artifact_integrity_and_immutable_collision_fail_closed(project, subtests):
    """不可变产物的完整性与哈希冲突校验

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    data = b"<p>fixture</p>"
    index = {"entries": {"architecture": "architecture.html"}, "files": {
        "architecture.html": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "content_type": "text/html; charset=utf-8"}}}
    digest = builder._store(index, {"architecture.html": data})
    build = {"status": "ready", "artifact_hash": digest, **index}
    for invalid, path in [({**build, "status": "failed"}, "architecture.html"),
                          ({**build, "entries": {"wrong": "architecture.html"}}, "architecture.html"),
                          (build, "unindexed.js"), (build, "../artifact.json")]:
        with subtests.test(path=path, status=invalid["status"]), pytest.raises(ProjectionError):
            builder.read_artifact(invalid, path)
    target = project.root / "artifacts" / digest / "files/architecture.html"
    target.chmod(0o644)
    target.write_bytes(b"X" * len(data))
    with pytest.raises(ProjectionError, match="完整性"):
        builder.read_artifact(build, "architecture.html")
    with pytest.raises(ProjectionError, match="完整性"):
        builder._store(index, {"architecture.html": data})
    assert target.read_bytes() == b"X" * len(data)
    target.unlink()
    target.symlink_to(project.source / "build.mjs")
    with pytest.raises(ProjectionError):
        builder.read_artifact(build, "architecture.html")


def test_build_timeout_terminates_child_and_reports_failure(project):
    """构建超时终止子进程并报告失败

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    with pytest.raises(ProjectionError, match="超时"):
        builder._command(["/usr/bin/sleep", "30"], cwd=project.root, timeout=0.03)


def test_license_or_choice_and_installed_dependency_are_checked(project, subtests):
    """构建依赖许可选择与实际安装依赖一致

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    for expression, expected in [
        ("(MPL-2.0 OR Apache-2.0)", True), ("MIT AND (ISC OR BSD-3-Clause)", True),
        ("MIT AND GPL-3.0-only", False), ("GPL-3.0-only OR UNLICENSED", False),
        ("UNLICENSED", False), ("(MIT OR Apache-2.0", False), ("MIT OR", False),
    ]:
        with subtests.test(expression=expression):
            assert builder._license(expression) is expected
    lock = {"packages": {"": {"license": "UNLICENSED"}, "node_modules/library": {
        "version": "1.0.0", "license": "MIT", "integrity": "locked"}}}
    package = project.source / "node_modules/library/package.json"
    package.parent.mkdir(parents=True)
    # A permissive lock declaration cannot hide the installed package's license.
    package.write_text(json.dumps({"version": "1.0.0", "license": "UNLICENSED"}))
    with pytest.raises(ProjectionError, match="许可"):
        builder._dependencies(project.source, lock)


def test_html_css_references_must_stay_in_artifact(project, subtests):
    """HTML 与 CSS 引用限定在构建产物内

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    contents = {"entry.html": b'<script type="module" src="./assets/main.js"></script><link rel="stylesheet" href="assets/main.css">',
                "assets/main.js": b'const text = "import(./missing.js)"; const r = /import\\(/; // import x from "missing"',
                "assets/main.css": b'.x {background:url(data:image/png;base64,aW1hZ2U=); filter:url(#local)}',
                "assets/shared.css": b'.x{content:"url(https://invalid.example)"} /* url(missing) */',
                "image.png": b"image"}
    graph = builder._references(project.source, contents)
    assert graph["entry.html"] == {"assets/main.js", "assets/main.css"}
    assert graph["assets/main.css"] == set()
    assert graph["assets/shared.css"] == set()
    for source in [b'<script src="https://invalid.example/a.js"></script>', b'<script src="missing.js"></script>',
                   b'<base href="https://invalid.example/">', b'<link rel="stylesheet" href="//invalid.example/a.css">',
                   b'<script src="%61ssets/main.js"></script>', b'<script src="../assets/main.js"></script>',
                   b'<script type="importmap">{}</script>', b'<script src="image.png"></script>',
                   b'<style>@import "https://invalid.example/a.css";</style>',
                   b'<style>.x{background:u\\72l(https://invalid.example/image.png)}</style>',
                   b'<style>@\\69mport "missing.css";</style>', b'<img srcset="image.png 1x, missing.png 2x">',
                   b'<style>@import URL("assets/main.js");</style>',
                   b'<style>@import "assets/main.css";</style>', b'<style>.x{background:url(image.png)}</style>',
                   b'<style>@import "data:text/css,.x{}";</style>']:
        with subtests.test(source=source), pytest.raises(ProjectionError):
            builder._references(project.source, {**contents, "entry.html": source})


def test_unbundled_javascript_and_worker_imports_fail_before_publication(project, subtests):
    """未打包的脚本与 Worker 导入阻止发布

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    for source in [b'import "./chunk.js";', b'export * from "./chunk.js";',
                   b'export { x } from "./chunk.js";', b'import("./chunk.js");',
                   b'const x = `value:${import("./chunk.js")}`;', b'importScripts("./chunk.js")',
                   b'self["importScripts"]("./chunk.js")', b'const invalid = ;']:
        with subtests.test(source=source), pytest.raises(ProjectionError):
            builder._references(project.source, {"entry.js": source, "chunk.js": b'export const x=1;'})
    with pytest.raises(ProjectionError):
        builder._references(project.source, {"entry.html": b'<script type="module">import("chunk.js")</script>'})
    assert not (project.root / "artifacts").exists()


def test_size_limit_is_per_entry_with_deduplicated_shared_assets(project, monkeypatch):
    """应用包按入口限制体积并对共享资源去重

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    dist = project.source / "dist"
    dist.mkdir()
    contents = {
        "one.html": b'<script src="one.js"></script><script src="one.js"></script><link rel="stylesheet" href="shared.css">',
        "two.html": b'<script src="two.js"></script><link rel="stylesheet" href="shared.css">',
        "one.js": b'/*' + b'x' * 200 + b'*/', "two.js": b'/*' + b'y' * 200 + b'*/',
        "shared.css": b'.x{background:url(data:image/png;base64,aW1hZ2U=)}',
        "layout.worker.js": b'self.onmessage=()=>postMessage(1);',
    }
    for path, data in contents.items():
        (dist / path).write_bytes(data)
    common = sum(len(contents[path]) for path in ("shared.css", "layout.worker.js"))
    limit = common + len(contents["one.html"]) + len(contents["one.js"])
    application = {"entries": {"one": "one.html", "two": "two.html"}, "max_bytes": limit}
    index, _ = builder._output(project.source, application)
    assert sum(file["size"] for file in index["files"].values()) > limit
    with pytest.raises(ProjectionError, match="单功能区"):
        builder._output(project.source, {**application, "max_bytes": limit - 1})
    monkeypatch.setattr(builder, "APPLICATION_LIMIT", sum(len(value) for value in contents.values()) - 1)
    with pytest.raises(ProjectionError, match="合计"):
        builder._output(project.source, application)


@pytest.mark.skipif(not os.environ.get("PORTAL_TEST_PROJECTION_TEMPLATE_CACHE"), reason="requires populated template npm cache")
def test_real_development_template_store_publish_and_read_asset(project, monkeypatch):
    """真实应用模板离线构建、保存发布与资源读取

    真实应用模板离线构建、保存发布与资源读取。 定向门禁须设置 PORTAL_TEST_PROJECTION_TEMPLATE_CACHE 指向已填充的 npm 缓存；缺失或跳过不能作为通过。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    from management_portal.workbench_projections import application_http
    from management_portal.workbench_projections.store import Store

    template = Path(builder.__file__).with_name("development_app")
    shutil.rmtree(project.source)
    project.source.mkdir()
    # Copy maintained source/lock only, never node_modules, dist or another
    # resident's in-progress region implementation. Commit only this temp repo.
    for name in ("package.json", "build.mjs", "src/shared/lifecycle.js", "src/shared/resources.js",
                 "src/example/index.js", "src/example/layout.worker.js"):
        target = project.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template / name, target)
    # Test both regional worker names without reading an owner's in-progress
    # production source. Only this temporary Git fixture receives these files.
    architecture = project.source / "src/architecture"
    architecture.mkdir()
    shutil.copyfile(project.source / "src/example/index.js", architecture / "index.js")
    shutil.copyfile(project.source / "src/example/layout.worker.js", architecture / "layout.worker.js")
    (architecture / "graph-layout.worker.js").write_text(
        "import dagre from '@dagrejs/dagre'; self.onmessage = ({data}) => "
        "postMessage({requestId: data.requestId, version: dagre.version});"
    )
    (project.source / "entries.json").write_text(json.dumps({"example": "src/example/index.js", "architecture": "src/architecture/index.js"}))
    application = {**project.commit(), "entries": {"example": "example.html", "architecture": "architecture.html"}}
    monkeypatch.setenv("PORTAL_PROJECTION_NPM_CACHE", os.environ["PORTAL_TEST_PROJECTION_TEMPLATE_CACHE"])
    store = Store()
    app_id = "workbench_0123456789ab"
    created = store.create(app_id, {
        "title": "实际应用包模板构建测试", "application": application,
        "manifest": {"schema": "tfi.projection.v2", "sidebar": [
            {"id": "example", "title": "D3 与 Worker", "source": "package:example"},
            {"id": "architecture", "title": "独立区域 Worker", "source": "package:architecture"}]}, "panels": {},
    }, ACTOR)
    build = created["application_build"]
    assert build["status"] == "ready"
    dependencies = {item["path"]: item for item in build["dependencies"]}
    assert dependencies["node_modules/dompurify"]["license"] == "(MPL-2.0 OR Apache-2.0)"
    assert any(path.startswith("node_modules/@esbuild/") for path in dependencies)
    assert dependencies["node_modules/d3"]["version"] == "7.9.0"
    assert dependencies["node_modules/marked"]["version"] == "18.1.1"
    published = store.publish(app_id, ACTOR, created["revision"])
    assert published["published_revision"] == created["revision"]
    prefix = f'{application_http.PREFIX}{app_id}/{created["revision"]}/{build["artifact_hash"]}/'
    for path in build["files"]:
        content, content_type, headers = application_http.read_asset(prefix + path, ACTOR, store)
        assert (content, content_type) == builder.read_artifact(build, path)
        assert headers["Content-Security-Policy"].startswith("sandbox;")
    with pytest.raises(PermissionError):
        application_http.read_asset(prefix + "example.js", "", store)
    assert "layout.worker.js" in build["files"]
    assert "architecture.worker.js" in build["files"]
    assert builder.read_artifact(build, "architecture.worker.js")[0] == builder.read_artifact(build, "layout.worker.js")[0]
    graph_worker, graph_mime = builder.read_artifact(build, "architecture-layout.worker.js")
    assert graph_mime.split(";", 1)[0] == "text/javascript"
    assert graph_worker != builder.read_artifact(build, "layout.worker.js")[0]
    print(json.dumps({"artifact_hash": build["artifact_hash"], "files": len(build["files"]),
                      "bytes": sum(file["size"] for file in build["files"].values()),
                      "dependencies": len(dependencies)}, sort_keys=True))
