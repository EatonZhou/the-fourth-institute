"""Isolated builds of authorized, immutable Git application snapshots.

On Linux, Git plumbing runs on the host and source commands run in bubblewrap.
Windows/macOS use a local Docker Linux engine for both, with a deployment-pinned,
preloaded toolchain image. Missing isolation always fails closed. Script-free npm resolution and
installation may fetch registry packages into a private per-build cache. The
source build runs without network or host npm configuration and credentials.
PORTAL_PROJECTION_ARTIFACT_ROOT is shared by Dev/Release and must be on one
filesystem for atomic publication. Neither setting is accepted from an app.

The build script must produce dist/. Dependency licenses must use the reviewed
SPDX identifiers below (AND requires all terms, OR permits a reviewed choice). Local,
Git and workspace dependencies require a separately reviewed packaging policy.
read_artifact accepts only a receipt from a saved, authorized workbench revision;
the HTTP/store owner performs that authorization, not this byte-storage module.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path
from management_common.file_durability import sync_directory
from management_common.secure_paths import open_target

import base64
import errno
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

from management_portal.resources import get_resource_service
from management_common import process_runtime
from management_common.site_config import read_site_config
from .content import ContentTemplateError
from .page import validate_application
from .store import ProjectionError


SOURCE_LIMIT = 32 * 1024 * 1024
APPLICATION_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 4096
BUILD_TIMEOUT = 360
REVIEWED_LICENSES = frozenset({
    "MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD",
    "CC0-1.0", "Unlicense", "BlueOak-1.0.0", "Python-2.0",
})
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".map": "application/json", ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".wasm": "application/wasm",
}
_HEX = re.compile(r"[0-9a-f]{64}")
_HOST_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
             "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}


def _fail(code: str, message: str):
    raise ProjectionError("projection.application_" + code, message)


def _path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or not re.fullmatch(r"[A-Za-z0-9_.@+/-]+", value)
            or any(part in {"", ".", "..", ".git"} for part in value.split("/"))):
        _fail("path", "应用包路径必须是规范的相对文件路径")
    return value


def _json(data: bytes) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise ProjectionError("projection.application_manifest", "应用包 JSON 文件无效") from error
    if not isinstance(value, dict):
        _fail("manifest", "应用包 JSON 文件必须是对象")
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def _container_settings() -> dict[str, str]:
    """Only deployment may choose the local daemon and immutable build image."""
    try:
        config = read_site_config().get("deployment", {}).get("application_build", {})
        image = config.get("container_image", "")
        executable = config.get("docker_executable") or shutil.which("docker")
        endpoint = config.get("docker_endpoint")
    except (AttributeError, TypeError, ValueError) as error:
        raise ProjectionError("projection.application_toolchain", "应用构建容器配置无效") from error
    if not isinstance(image, str) or not re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[0-9a-f]{64}", image):
        _fail("toolchain", "请配置 deployment.application_build.container_image 为本机已加载镜像的固定 sha256 摘要；不会自动拉取")
    if not isinstance(executable, str) or not Path(executable).is_absolute() or Path(executable).suffix.lower() in {".cmd", ".bat"}:
        _fail("toolchain", "需要本机 Docker 原生可执行文件；不通过 cmd 或 shell 启动构建")
    if endpoint is None:
        if sys.platform == "win32":
            endpoint = "npipe:////./pipe/docker_engine"
        else:
            desktop = Path.home() / ".docker/run/docker.sock"
            endpoint = "unix://" + str(desktop if desktop.exists() else Path("/var/run/docker.sock"))
    if (not isinstance(endpoint, str) or any(ord(c) < 32 for c in endpoint)
            or not (endpoint.startswith("unix:///") if sys.platform != "win32" else
                    endpoint in {"npipe:////./pipe/docker_engine", "npipe:////./pipe/dockerDesktopLinuxEngine"})):
        _fail("toolchain", "构建只允许本机 Docker Unix socket 或命名管道，不能使用远程 daemon")
    return {"docker_executable": executable, "docker_endpoint": endpoint, "container_image": image}


def _docker_cli(argv: list[str], *, env: dict[str, str], timeout: float, limit: int,
                input: bytes | None = None, stop: threading.Event | None = None) -> subprocess.CompletedProcess:
    """Bound Docker control/output bytes; runtime remains the process-tree owner."""
    if stop is not None and stop.is_set():
        raise InterruptedError("Build owner disconnected")
    process = process_runtime.spawn_process(argv, env=env, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers = [bytearray(), bytearray()]
    errors: list[Exception] = []
    overflow = threading.Event()

    def drain(stream, output):
        try:
            while data := stream.read(65536):
                available = limit - len(output)
                output.extend(data[:available])
                if len(data) > available:
                    overflow.set()
                    return
        except Exception as error:
            errors.append(error)

    def feed():
        try:
            process.stdin.write(input)
            process.stdin.flush()
        except BrokenPipeError:
            pass  # Early command exit remains authoritative.
        except Exception as error:
            errors.append(error)
        finally:
            process.stdin.close()

    readers = [threading.Thread(target=drain, args=(process.stdout, buffers[0]), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, buffers[1]), daemon=True)]
    if input is not None:
        readers.append(threading.Thread(target=feed, daemon=True))
    deadline = time.monotonic() + timeout
    try:
        for reader in readers:
            reader.start()
        while process.poll() is None:
            if stop is not None and stop.is_set():
                raise InterruptedError("Build owner disconnected")
            if overflow.is_set():
                _fail("size", "构建命令输出超出限制")
            if errors:
                raise errors[0]
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(argv, timeout)
            time.sleep(0.02)
    finally:
        try:
            process_runtime.terminate_process_tree(process, force=True)
        finally:
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=15)
            if not any(reader.is_alive() for reader in readers):
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        if any(reader.is_alive() for reader in readers):
            _fail("cleanup", "Docker 输出管道未退出，清理尚未完成")
    if overflow.is_set():
        _fail("size", "构建命令输出超出限制")
    if errors:
        raise errors[0]
    return subprocess.CompletedProcess(argv, process.returncode, bytes(buffers[0]), bytes(buffers[1]))


def _container_command(project: Path, command: list[str], **kwargs) -> bytes:
    from .application_container_owner import run
    return run(project, command, config=_container_settings(), **kwargs)


def _remove_owned_container(cli, name: str, token: str, create_observed: bool) -> None:
    found = cli(["container", "ls", "--all", "--format", "{{.ID}}", "--filter", "name=^/" + name + "$"])
    if found.returncode:
        raise RuntimeError("container listing unavailable")
    ids = found.stdout.decode().split()
    if not ids:
        if not create_observed:
            raise RuntimeError("container creation unknown; absence unconfirmed")
        return
    if len(ids) != 1:
        raise RuntimeError("container ownership ambiguous")
    inspected = cli(["container", "inspect", "--format", "{{json .}}", ids[0]])
    info = json.loads(inspected.stdout) if inspected.returncode == 0 else {}
    cid = info.get('Id', '')
    if (not re.fullmatch(r'[0-9a-f]{64}', cid) or info.get('Name') != '/' + name
            or (info.get('Config', {}).get('Labels') or {}).get('portal.application.owner') != token):
        raise RuntimeError("container ownership changed")
    if cli(["container", "rm", "--force", cid]).returncode:
        raise RuntimeError("container removal failed")


def _container_command_owned(project: Path, command: list[str], *, input: bytes | None = None,
                       mounts: tuple[tuple[Path, str], ...] = (), writable_cache: Path | None = None,
                       network: bool = False, readonly_project: bool = False,
                       timeout: float = BUILD_TIMEOUT, limit: int = SOURCE_LIMIT * 2,
                       config=None, stop=None, name=None, token=None, receipt=None) -> bytes:
    """Linux-container equivalent of the build sandbox, including daemon cleanup.

    The preloaded image must supply /usr/bin/{env,timeout,prlimit,git,node,npm}; no image
    build/pull occurs here. Docker CLI termination does not stop daemon containers,
    so creation, uncertain acceptance, normal exit and errors all share forced rm.
    """
    config = config if config is not None else _container_settings()
    receipt = receipt if receipt is not None else {}
    receipt["cleanup_complete"] = True
    if network and (command[0] != "/usr/bin/npm" or "--ignore-scripts" not in command or command[-1] not in {"ci", "install"}):
        _fail("capability", "只有禁用源码脚本的 npm 依赖安装允许访问网络")

    def mount(source, destination, readonly=False):
        source = str(source.resolve())
        if any(c in source for c in (",", "\r", "\n", "\0")):
            _fail("path", "容器挂载路径不能包含逗号或控制字符")
        return f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")

    bindings = ["--mount", mount(project, "/app", readonly_project)]
    if writable_cache is not None:
        bindings += ["--mount", mount(writable_cache, "/npm-cache")]
    for source, destination in mounts:
        if destination != "/npm-cache/_cacache":
            _fail("path", "容器只允许指定 project/cache 挂载")
        bindings += ["--mount", mount(source, destination, True)]
    with tempfile.TemporaryDirectory(prefix="portal-build-docker-config-") as private_config:
        # Exclude client credentials, remote contexts, proxies and helper config.
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env.update({"PATH": os.defpath, "HOME": private_config, "USERPROFILE": private_config,
                    "DOCKER_CONFIG": private_config, "LANG": "C.UTF-8"})
        prefix = [config["docker_executable"], "--host", config["docker_endpoint"], "--config", private_config]

        def cli(arguments, *, seconds=15, cap=1024 * 1024, data=None, cleanup=False):
            return _docker_cli([*prefix, *arguments], env=env, timeout=seconds, limit=cap, input=data,
                               **({"stop": stop} if stop is not None and not cleanup else {}))

        inspected = cli(["image", "inspect", "--format", "{{json .}}", config["container_image"]])
        if inspected.returncode:
            _fail("toolchain", "Docker daemon 不可用、无权限或固定镜像未在本机加载；不会拉取镜像或回退宿主执行")
        try:
            image = json.loads(inspected.stdout)
            image_id = image["Id"]
            if (image["Os"] != "linux" or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                    or image.get("Config", {}).get("Volumes")):
                raise ValueError("image must be Linux without implicit volumes")
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise ProjectionError("projection.application_toolchain", "构建镜像必须为 Linux 且不能声明额外 Volume") from error
        name = name or "portal-app-build-" + uuid.uuid4().hex
        token = token or uuid.uuid4().hex
        create = ["container", "create", "--name", name, "--label", "portal.application.owner=" + token, "--pull=never", "--read-only", "--cap-drop=ALL",
                  "--security-opt=no-new-privileges", "--no-healthcheck", "--log-driver=none",
                  "--network=bridge" if network else "--network=none", "--pids-limit=128",
                  "--memory=1g", "--memory-swap=1g", "--cpus=2", "--ulimit", "nofile=256:256",
                  "--ulimit", "core=0:0", "--user", "0:0", "--workdir", "/app",
                  "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m", "--tmpfs", "/home/builder:rw,nosuid,nodev,size=16m",
                  *bindings, "--entrypoint", "/usr/bin/env"]
        if input is not None:
            create += ["--interactive"]
        sandbox_env = {**_HOST_ENV, "HOME": "/home/builder", "TZ": "UTC", "npm_config_userconfig": "/dev/null",
                       "npm_config_globalconfig": "/tmp/npm-globalrc", "npm_config_logs_dir": "/tmp/npm-logs",
                       "npm_config_update_notifier": "false"}
        create += [image_id, "-i", *(f"{key}={value}" for key, value in sandbox_env.items()),
                   # Daemon workloads retain their deadline if the client dies.
                   "/usr/bin/timeout", "--signal=TERM", "--kill-after=15s", f"{timeout}s",
                   "/usr/bin/prlimit", f"--fsize={limit}", "--core=0", "--nofile=256", "--", *command]
        create_observed = False
        receipt["cleanup_complete"] = False
        try:
            created = cli(create)
            create_observed = True
            receipt["create_observed"] = True
            if created.returncode:
                _fail("toolchain", "无法建立隔离构建容器；请核对挂载共享、Linux engine 与资源限制能力")
            started = cli(["container", "start", "--attach", *(["--interactive"] if input is not None else []), name],
                          seconds=timeout, cap=limit, data=input)
            state_result = cli(["container", "inspect", "--format", "{{json .State}}", name])
            try:
                state = json.loads(state_result.stdout)
                if state_result.returncode or state["Running"] is not False or not isinstance(state["ExitCode"], int):
                    raise ValueError("no terminal state")
            except (ValueError, KeyError, TypeError) as error:
                raise ProjectionError("projection.application_build", "容器未确认完成，不能生成构建结果") from error
            if started.returncode or state["ExitCode"] or state.get("Error"):
                detail = "Git 快照读取失败" if readonly_project else started.stderr[:3000].decode("utf-8", "replace")
                _fail("build", f"隔离容器构建失败（{state['ExitCode']}）：{detail}")
            return started.stdout
        finally:
            # A timed-out create/start might have been accepted by the daemon.
            # Never infer container cleanup from the client process exiting.
            try:
                _remove_owned_container(lambda args: cli(args, cleanup=True), name, token, create_observed)
                receipt["cleanup_complete"] = True
            except Exception as error:
                raise ProjectionError("projection.application_cleanup", f"构建容器清理未确认：{name}") from error


def _command(argv: list[str], *, cwd: Path, timeout: float = 90, limit: int = SOURCE_LIMIT * 2, input: bytes | None = None) -> bytes:
    """Bound output and time; never inherit credentials or shell configuration."""
    if sys.platform != "linux":
        if argv[0] != "/usr/bin/git":
            _fail("capability", "宿主命令仅允许 Git 快照读取；源码命令必须通过隔离沙箱")
        command = ["safe.directory=/app" if item == f"safe.directory={cwd}" else item for item in argv]
        try:
            return _container_command(cwd, command, readonly_project=True, timeout=timeout, limit=limit, input=input)
        except OSError as error:
            raise ProjectionError("projection.application_toolchain", "Docker 构建工具不可用；不会回退宿主执行") from error
        except subprocess.TimeoutExpired as error:
            raise ProjectionError("projection.application_timeout", "应用包快照读取超时") from error
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = process_runtime.run_process(
                ["/usr/bin/prlimit", f"--fsize={limit}", "--core=0", "--nofile=256", "--", *argv],
                cwd=cwd, env=_HOST_ENV,
                stdout=stdout, stderr=stderr,
                timeout=timeout,
                **({"input": input} if input is not None else {"stdin": subprocess.DEVNULL}),
            )
        except OSError as error:
            raise ProjectionError("projection.application_toolchain", "应用包构建工具不可用；不会在宿主执行源码") from error
        except subprocess.TimeoutExpired as error:
            raise ProjectionError("projection.application_timeout", "应用包构建超时，已终止隔离进程") from error
        if process.returncode:
            # The sandbox cannot access host secrets. Do not expose Git's host
            # checkout diagnostics or an unbounded compiler log in API errors.
            if argv[0] == "/usr/bin/bwrap":
                stderr.seek(0)
                detail = stderr.read(3000).decode("utf-8", "replace")
            else:
                detail = "Git 快照读取失败"
            _fail("build", f"隔离构建或快照命令失败（{process.returncode}）：{detail}")
        if stdout.tell() > limit:
            _fail("size", "构建命令输出超出限制")
        stdout.seek(0)
        return stdout.read(limit)


def _snapshot(application: dict, actor: str, destination: Path) -> None:
    if not isinstance(actor, str) or not actor.strip():
        raise PermissionError("应用包构建需要真实调用者")
    resolved = get_resource_service().resolve(application["repo_id"], actor=actor)
    if (resolved.get("root_id") != application["repo_id"] or resolved.get("resource_type") != "repo"
            or not resolved.get("exists") or not resolved.get("checkout_path")):
        _fail("repo", "应用包仓库不可读或没有本地检出")
    checkout = Path(resolved["checkout_path"])
    git = ["/usr/bin/git", "--no-optional-locks", "--no-replace-objects", "-c", "protocol.allow=never",
           "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", f"safe.directory={checkout}"]
    commit = application["commit"]
    actual = _command([*git, "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=checkout).decode().strip()
    if actual != commit:
        _fail("commit", "应用包提交不是已存在的完整 commit SHA")
    tree = commit + (":" + application["directory"] if application["directory"] != "." else "^{tree}")
    listing = _command([*git, "ls-tree", "-r", "-z", "-l", tree], cwd=checkout)
    records = listing.rstrip(b"\0").split(b"\0") if listing else []
    if not records or len(records) > FILE_LIMIT:
        _fail("source", "应用包源码为空或文件数量超限")
    total = 0
    expected = {}
    for record in records:
        header, raw_path = record.split(b"\t", 1)
        mode, kind, digest, size = header.split()
        try:
            path = _path(raw_path.decode("utf-8"))
        except UnicodeError as error:
            raise ProjectionError("projection.application_path", "应用包源码路径必须是 UTF-8") from error
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            _fail("source", "应用包源码不能包含符号链接、子模块或特殊文件")
        if "node_modules" in PurePosixPath(path).parts or PurePosixPath(path).name == ".npmrc" or path.startswith("dist/"):
            _fail("source", "源码不得包含 node_modules、dist 或 npm 配置；依赖由构建器隔离安装")
        total += int(size)
        expected[path] = (int(size), digest.decode(), mode)
    if total > SOURCE_LIMIT:
        _fail("size", "应用包源码超过 32 MB")
    # Read raw blobs: archive attributes, checkout filters, hooks and external
    # archive commands never participate in this snapshot.
    blobs = _command([*git, "cat-file", "--batch"], cwd=checkout,
                     input="".join(digest + "\n" for _, digest, _ in expected.values()).encode())
    offset = 0
    for path, (size, digest, mode) in expected.items():
        header = f"{digest} blob {size}\n".encode()
        if blobs[offset:offset + len(header)] != header:
            _fail("source", "Git blob 快照格式无效")
        offset += len(header)
        content = blobs[offset:offset + size]
        offset += size
        blob_hash = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if blob_hash != digest or blobs[offset:offset + 1] != b"\n":
            _fail("source", "Git blob 快照完整性校验失败")
        offset += 1
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        # Build scripts are invoked explicitly by npm/Node. Executable source
        # files still need their Git executable bit for conventional scripts.
        target.chmod(0o755 if mode == b"100755" else 0o644)
    if offset != len(blobs):
        _fail("source", "Git blob 快照包含多余内容")


def _license(expression) -> bool:
    """Validate SPDX syntax and whether its obligations have a reviewed choice."""
    if not isinstance(expression, str) or len(expression) > 256:
        return False
    tokens = re.findall(r"[A-Za-z0-9.+-]+|[()]", expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return False
    position = 0

    def term():
        nonlocal position
        token = tokens[position]
        position += 1
        if token == "(":
            value = expression_value()
            if tokens[position] != ")":
                raise ValueError
            position += 1
            return value
        if token in {"AND", "OR", ")"}:
            raise ValueError
        return token in REVIEWED_LICENSES

    def conjunction():
        nonlocal position
        value = term()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            other = term()
            value = value and other
        return value

    def expression_value():
        nonlocal position
        value = conjunction()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            other = conjunction()
            value = value or other
        return value

    try:
        accepted = expression_value()
        return accepted and position == len(tokens)
    except (IndexError, ValueError):
        return False


def _lock(project: Path) -> dict:
    """Validate the transient resolution record created for this build only."""
    try:
        package = _json((project / "package.json").read_bytes())
        lock = _json((project / "package-lock.json").read_bytes())
    except FileNotFoundError as error:
        raise ProjectionError("projection.application_lock", "构建时依赖解析没有生成完整性记录") from error
    scripts = package.get("scripts")
    if (package.get("workspaces") or not isinstance(scripts, dict) or not isinstance(scripts.get("build"), str)
            or not scripts["build"].strip() or (project / "npm-shrinkwrap.json").exists()
            or lock.get("lockfileVersion") not in {2, 3} or not isinstance(lock.get("packages"), dict)
            or not isinstance(lock["packages"].get(""), dict) or len(lock["packages"]) > FILE_LIMIT):
        _fail("lock", "需要 build 脚本及有效的构建时依赖记录；暂不支持 workspace 或 shrinkwrap")
    for path, dependency in lock["packages"].items():
        if path == "":
            # The application's own private/UNLICENSED source is not a
            # redistributed third-party library. Audit every dependency below.
            continue
        _path(path)
        if (not re.fullmatch(r"(?:node_modules/(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+/)*node_modules/(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", path)
                or not isinstance(dependency, dict) or dependency.get("link")
                or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?", str(dependency.get("version", "")))):
            _fail("lock", "解析结果必须是登记镜像包，不能使用链接或本地路径")
        try:
            url = urlsplit(str(dependency.get("resolved", "")))
        except ValueError as error:
            raise ProjectionError("projection.application_lock", "依赖镜像地址无效") from error
        integrity = str(dependency.get("integrity", ""))
        try:
            valid_integrity = integrity.startswith("sha512-") and len(base64.b64decode(integrity[7:], validate=True)) == 64
        except ValueError:
            valid_integrity = False
        if (url.scheme != "https" or url.netloc != "registry.npmjs.org" or url.fragment or url.query
                or not valid_integrity):
            _fail("lock", "依赖需要 npm 登记镜像地址与固定 sha512 完整性摘要")
        if "license" in dependency and not _license(dependency["license"]):
            _fail("license", f"依赖许可尚未通过登记检查：{path}")
    return lock


def _sandbox_command(project: Path, command: list[str], *, input: bytes | None = None,
                     mounts: tuple[tuple[Path, str], ...] = (), writable_cache: Path | None = None,
                     network: bool = False) -> bytes:
    if sys.platform in {"win32", "darwin"}:
        try:
            return _container_command(project, command, input=input, mounts=mounts,
                                      writable_cache=writable_cache, network=network)
        except OSError as error:
            raise ProjectionError("projection.application_toolchain", "Docker 构建工具不可用；不会回退宿主执行") from error
        except subprocess.TimeoutExpired as error:
            raise ProjectionError("projection.application_timeout", "应用包构建超时") from error
    argv = ["/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent",
            "--new-session", "--cap-drop", "ALL", "--clearenv", "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--symlink", "usr/bin", "/bin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home/builder",
            "--dir", "/npm-cache", "--bind", str(project), "/app", "--chdir", "/app"]
    if network:
        argv += ["--share-net", "--dir", "/etc"]
        for name in ("resolv.conf", "hosts", "nsswitch.conf"):
            source = Path("/etc") / name
            if source.is_file():
                argv += ["--ro-bind", str(source), str(source)]
    if writable_cache is not None:
        argv += ["--bind", str(writable_cache), "/npm-cache"]
    for source, destination in mounts:
        argv += ["--ro-bind", str(source), destination]
    for name, value in {"PATH": "/usr/bin:/bin", "HOME": "/home/builder", "LANG": "C.UTF-8", "TZ": "UTC",
                        "npm_config_userconfig": "/dev/null", "npm_config_globalconfig": "/tmp/npm-globalrc",
                        "npm_config_logs_dir": "/tmp/npm-logs", "npm_config_update_notifier": "false"}.items():
        argv += ["--setenv", name, value]
    return _command([*argv, "--", *command], cwd=project, timeout=BUILD_TIMEOUT, input=input)


def _sandbox(project: Path, arguments: list[str], *, cache: Path, network: bool = False) -> None:
    flags = [] if network else ["--offline"]
    _sandbox_command(project, ["/usr/bin/npm", *flags, "--ignore-scripts", "--no-audit", "--no-fund",
                               "--cache=/npm-cache", *arguments],
                     writable_cache=cache if network else None,
                     mounts=() if network else ((cache / "_cacache", "/npm-cache/_cacache"),),
                     network=network)


def _resolve_lock(project: Path, cache: Path) -> None:
    """Resolve current registry versions without source scripts.

    The generated lock is temporary evidence for this build; it is never read
    from the source commit or imposed on future installations.
    """
    package = _json((project / "package.json").read_bytes())
    if (package.get("workspaces") or not isinstance(package.get("scripts"), dict)
            or not isinstance(package["scripts"].get("build"), str)
            or not package["scripts"]["build"].strip()
            or (project / "npm-shrinkwrap.json").exists()):
        _fail("manifest", "应用包需要 build 脚本；暂不支持 workspace 或 shrinkwrap")
    # Old source lockfiles have no authority. npm writes a fresh, ephemeral one.
    (project / "package-lock.json").unlink(missing_ok=True)
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        values = package.get(section, {})
        if not isinstance(values, dict):
            _fail("manifest", f"{section} 必须是依赖映射")
        for name, spec in values.items():
            if (not re.fullmatch(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", name)
                    or not isinstance(spec, str) or not re.fullmatch(r"[0-9A-Za-z.*^~<>=| +\-]+", spec)):
                _fail("dependency", "只支持 npm 登记镜像中的版本要求")
    cache.mkdir()
    _sandbox_command(project, ["/usr/bin/npm", "--ignore-scripts", "--no-audit", "--no-fund",
                               "--package-lock-only", "--fetch-retries=0",
                               "--registry=https://registry.npmjs.org", "--cache=/npm-cache", "install"],
                     writable_cache=cache, network=True)



def _check_cache(cache: Path) -> None:
    total = 0
    for root, directories, files in os.walk(cache, followlinks=False):
        for name in [*directories, *files]:
            entry = Path(root) / name
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                _fail("size", "依赖缓存包含不支持的文件类型")
            if stat.S_ISREG(mode):
                size = entry.stat().st_size
                total += size
                if size > 64 * 1024 * 1024 or total > 256 * 1024 * 1024:
                    _fail("size", "依赖缓存超出大小上限")


def _regular_bytes(root: Path, path: str, *, limit: int) -> bytes:
    """Open each component without following links, including the root itself."""
    _path(path)
    try:
        with open_target(root, tuple(path.split("/"))) as (descriptor, info):
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _fail("artifact", "应用包文件必须是独立普通文件")
            if info.st_size > limit:
                _fail("size", "应用包文件超出大小上限")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(limit + 1)
            if len(data) > limit:
                _fail("size", "应用包文件超出大小限制")
            return data
    except ValueError as error:
        raise ProjectionError("projection.application_artifact", "构建产物缺失或包含链接") from error


def _dependencies(project: Path, lock: dict) -> list[dict]:
    checked = []
    for path, dependency in lock["packages"].items():
        if not path:
            continue
        try:
            package = _json(_regular_bytes(project, path + "/package.json", limit=1024 * 1024))
        except FileNotFoundError:
            if dependency.get("optional"):
                continue  # npm omits optional dependencies for other platforms.
            _fail("dependency", f"锁定依赖未安装：{path}")
        if package.get("version") != dependency["version"] or not _license(package.get("license")):
            _fail("license", f"安装依赖版本或许可未通过检查：{path}")
        checked.append({"path": path, "version": dependency["version"], "license": package["license"], "integrity": dependency["integrity"]})
    return checked


def _asset_reference(owner: str, reference: str, contents: dict[str, bytes]) -> str | None:
    """Resolve one URL against its owning file, never against the Portal origin."""
    if not isinstance(reference, str) or not reference or reference != reference.strip():
        _fail("reference", f"产物包含空或不规范的资源引用：{owner}")
    if reference.startswith("#"):
        return None
    if any(char in reference for char in "\\%?#:") or reference.startswith("/"):
        _fail("reference", f"产物只能引用本修订内的相对路径：{owner}")
    parts = list(PurePosixPath(owner).parent.parts)
    for part in reference.split("/"):
        if part == ".":
            continue
        if part == "..":
            if not parts:
                _fail("reference", f"资源引用越过应用包根目录：{owner}")
            parts.pop()
        else:
            parts.append(part)
    path = _path("/".join(parts))
    if path not in contents:
        _fail("reference", f"资源引用没有对应构建产物：{owner} -> {path}")
    return path


_CSS_ESCAPE = r"\\(?:[0-9a-fA-F]{1,6}(?:\r\n|[\t\n\r\f ])?|[^\n\r\f])"
_CSS_STRING = re.compile(r'''"(?:[^"\\\n\r\f]|''' + _CSS_ESCAPE + r'''|\\\r?\n)*"|'(?:[^'\\\n\r\f]|''' + _CSS_ESCAPE + r'''|\\\r?\n)*' ''', re.X)
_CSS_IDENT = re.compile(r"(?:[A-Za-z_-]|[^\x00-\x7f]|" + _CSS_ESCAPE + r")(?:[A-Za-z0-9_-]|[^\x00-\x7f]|" + _CSS_ESCAPE + r")*")


def _css_decode(value: str) -> str:
    def replace(match):
        escaped = match.group()[1:]
        if re.match(r"[0-9a-fA-F]", escaped):
            number = int(escaped.strip(), 16)
            return chr(number) if 0 < number <= 0x10ffff else "\ufffd"
        return escaped
    return re.sub(_CSS_ESCAPE, replace, re.sub(r"\\\r?\n", "", value))


def _css_references(source: str) -> list[tuple[str, bool]]:
    """Tokenize comments, strings and escapes before recognizing URL/imports."""
    tokens = []
    position = 0
    while position < len(source):
        if source[position].isspace():
            position += 1
            continue
        if source.startswith("/*", position):
            end = source.find("*/", position + 2)
            if end < 0:
                _fail("reference", "CSS 注释未闭合")
            position = end + 2
            continue
        if source[position] in "\"'":
            match = _CSS_STRING.match(source, position)
            if not match:
                _fail("reference", "CSS 字符串无效")
            tokens.append(("string", _css_decode(match.group()[1:-1])))
        else:
            match = _CSS_IDENT.match(source, position)
            if match:
                tokens.append(("ident", _css_decode(match.group())))
            else:
                tokens.append((source[position], source[position]))
        position = match.end() if match else position + 1
    references = []
    imports = set()
    position = 0
    while position < len(tokens):
        kind, value = tokens[position]
        if kind == "@" and position + 1 < len(tokens) and tokens[position + 1][1].lower() == "import":
            if position + 2 >= len(tokens):
                _fail("reference", "CSS @import 缺少路径")
            target = tokens[position + 2]
            if target[0] == "string":
                references.append((target[1], True))
            elif target[0] != "ident" or target[1].lower() != "url":
                _fail("reference", "CSS @import 必须使用固定资源路径")
            else:
                imports.add(position + 2)
        if kind == "ident" and value.lower() == "url" and tokens[position + 1:position + 2] == [("(", "(")]:
            end = position + 2
            while end < len(tokens) and tokens[end][0] != ")":
                if tokens[end][0] == "(":
                    _fail("reference", "CSS url 必须使用固定资源路径")
                end += 1
            if end == len(tokens):
                _fail("reference", "CSS url 未闭合")
            items = tokens[position + 2:end]
            if any(item[0] == "string" for item in items) and len(items) != 1:
                _fail("reference", "CSS url 路径无效")
            references.append(("".join(item[1] for item in items), position in imports))
            position = end
        position += 1
    return references


class _ApplicationHTML(HTMLParser):
    def __init__(self, owner, reference, javascript, css):
        super().__init__(convert_charrefs=True)
        self.owner, self.reference, self.javascript, self.css = owner, reference, javascript, css
        self.active, self.text = None, []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if len(attributes) != len(attrs):
            _fail("reference", f"HTML 属性重复：{self.owner}")
        if tag in {"base", "iframe", "frame", "object", "embed"}:
            _fail("reference", f"HTML 包含不支持的嵌入或路径基址：{self.owner}")
        if "style" in attributes:
            self.css.append((self.owner, attributes["style"] or ""))
        for name in ("src", "poster"):
            if name in attributes:
                path = self.reference(self.owner, attributes[name])
                if tag == "script" and (not path or PurePosixPath(path).suffix not in {".js", ".mjs"}):
                    _fail("reference", "HTML script 必须引用 JavaScript 产物")
        if "srcset" in attributes:
            for candidate in (attributes["srcset"] or "").split(","):
                pieces = candidate.split()
                if not pieces:
                    _fail("reference", "HTML srcset 缺少路径")
                self.reference(self.owner, pieces[0])
        if tag == "link":
            path = self.reference(self.owner, attributes.get("href"))
            if "stylesheet" in (attributes.get("rel") or "").lower().split() and (not path or not path.endswith(".css")):
                _fail("reference", "HTML stylesheet 必须引用 CSS 产物")
        if tag == "script":
            kind = (attributes.get("type") or "").lower()
            if kind not in {"", "module", "text/javascript", "application/javascript"}:
                _fail("reference", "HTML 脚本必须是已打包 JavaScript，不支持 importmap")
            self.active = (tag, "module" if kind == "module" else "script", "src" in attributes)
            self.text = []
        elif tag == "style":
            self.active, self.text = (tag, None, False), []

    def handle_data(self, data):
        if self.active:
            self.text.append(data)

    def handle_endtag(self, tag):
        if self.active and self.active[0] == tag:
            _, mode, external = self.active
            source = "".join(self.text)
            if tag == "style":
                self.css.append((self.owner, source))
            elif not external:
                self.javascript.append({"path": self.owner, "source": source, "mode": mode})
            self.active, self.text = None, []


_CHECK_JAVASCRIPT = r"""
const fs = require('node:fs');
// This parser ships with the trusted system Node, never with source dependencies.
const acorn = require('internal/deps/acorn/acorn/dist/acorn');
for (const job of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  try {
    const pending = [acorn.parse(job.source, {ecmaVersion: 'latest', sourceType: job.mode})];
    while (pending.length) {
      const node = pending.pop();
      if (!node || typeof node !== 'object') continue;
      if (node.type === 'ImportDeclaration' || node.type === 'ImportExpression' ||
          ((node.type === 'ExportNamedDeclaration' || node.type === 'ExportAllDeclaration') && node.source))
        throw Error('unbundled static/dynamic import');
      if (node.type === 'CallExpression') {
        const c = node.callee;
        if ((c.type === 'Identifier' && c.name === 'importScripts') ||
            (c.type === 'MemberExpression' && ((c.property.name === 'importScripts' && !c.computed) || c.property.value === 'importScripts')))
          throw Error('Worker importScripts must be bundled');
      }
      for (const value of Object.values(node)) {
        if (Array.isArray(value)) pending.push(...value);
        else if (value && typeof value === 'object') pending.push(value);
      }
    }
  } catch (error) { console.error(job.path + ': ' + error.message); process.exit(1); }
}
"""


def _references(project: Path, contents: dict[str, bytes]) -> dict[str, set[str]]:
    graph = {path: set() for path in contents}
    javascript, css = [], []

    def reference(owner, value):
        target = _asset_reference(owner, value, contents)
        if target:
            graph[owner].add(target)
        return target

    for path, data in contents.items():
        suffix = PurePosixPath(path).suffix
        if suffix not in {".html", ".css", ".js", ".mjs"}:
            continue
        try:
            source = data.decode("utf-8")
        except UnicodeError as error:
            raise ProjectionError("projection.application_reference", f"文本产物必须是 UTF-8：{path}") from error
        if suffix == ".html":
            parser = _ApplicationHTML(path, reference, javascript, css)
            parser.feed(source)
            parser.close()
            if parser.active:
                _fail("reference", f"HTML script/style 标签未闭合：{path}")
        elif suffix == ".css":
            css.append((path, source))
        else:
            javascript.append({"path": path, "source": source, "mode": "module"})
    for owner, source in css:
        for value, stylesheet in _css_references(source):
            if stylesheet or not value or not value.startswith(("data:", "#")):
                _fail("reference", "CSS 必须自包含：请打包 @import 和 url；仅允许 data URL 或 #fragment")
    if javascript:
        _sandbox_command(project, ["/usr/bin/node", "--expose-internals", "-e", _CHECK_JAVASCRIPT],
                         input=json.dumps(javascript).encode())
    return graph


def _output(project: Path, application: dict) -> tuple[dict, dict[str, bytes]]:
    dist = project / "dist"
    if dist.is_symlink() or not dist.is_dir():
        _fail("artifact", "构建必须生成普通 dist 目录")
    limit = application.get("max_bytes", 8 * 1024 * 1024)
    files, contents = {}, {}
    total = 0
    def walk_error(error):
        raise error

    count = 0
    for directory, children, names in os.walk(dist, followlinks=False, onerror=walk_error):
        count += len(children) + len(names)
        if count > FILE_LIMIT:
            _fail("size", "应用包产物文件数量超出上限")
        for child in children:
            _path((Path(directory) / child).relative_to(dist).as_posix())
            if (Path(directory) / child).is_symlink():
                _fail("artifact", "构建产物不能包含符号链接")
        for name in names:
            path = _path((Path(directory) / name).relative_to(dist).as_posix())
            content_type = CONTENT_TYPES.get(PurePosixPath(path).suffix)
            if not content_type:
                _fail("artifact", f"未登记的构建产物类型：{path}")
            data = _regular_bytes(dist, path, limit=APPLICATION_LIMIT)
            total += len(data)
            if total > APPLICATION_LIMIT:
                _fail("size", "应用包产物合计超出 64 MB 上限")
            files[path] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "content_type": content_type}
            contents[path] = data
    if any(path not in files for path in application["entries"].values()):
        _fail("entry", "构建未生成全部登记入口")
    graph = _references(project, contents)
    def closure(seeds):
        seen, pending = set(), list(seeds)
        while pending:
            path = pending.pop()
            if path not in seen:
                seen.add(path)
                pending.extend(graph[path] - seen)
        return seen

    closures = [closure([entry]) for entry in application["entries"].values()]
    # Dynamic assets and standalone Workers have no HTML/CSS edge. Charge each
    # otherwise-unassigned asset to every region; shared paths count only once.
    common = closure(set(files) - set().union(*closures))
    for paths in closures:
        if sum(files[path]["size"] for path in paths | common) > limit:
            _fail("size", "单功能区入口及其共享资产超出登记上限（默认 8 MB）")
    return {"entries": dict(application["entries"]), "files": files}, contents


def _artifact_root() -> Path:
    root = Path(os.environ.get("PORTAL_PROJECTION_ARTIFACT_ROOT", str(portal_path("staging_root") / "shared" / "projection_applications")))
    if not root.is_absolute():
        _fail("artifact", "产物存储必须配置为绝对路径")
    return root


def _store(index: dict, contents: dict[str, bytes]) -> str:
    digest = hashlib.sha256(_canonical(index)).hexdigest()
    root = _artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
        staged = Path(temporary) / "artifact"
        staged.mkdir()
        for path, data in contents.items():
            target = staged / "files" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            target.chmod(0o444)
        manifest = staged / "artifact.json"
        with manifest.open("xb") as handle:
            handle.write(_canonical(index))
            handle.flush()
            os.fsync(handle.fileno())
        manifest.chmod(0o444)
        for parent, _, _ in os.walk(staged, topdown=False):
            sync_directory(Path(parent))
        try:
            os.rename(staged, root / digest)
        except OSError as error:
            if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            # Another identical build may have completed; never overwrite it.
            for path in index["files"]:
                read_artifact({"status": "ready", "artifact_hash": digest, **index}, path)
        sync_directory(root)
    return digest


def build_application(application: dict, actor: str) -> dict:
    """Build one commit or raise; a failed build never yields a publishable receipt."""
    try:
        validate_application(application)
    except ContentTemplateError as error:
        raise ProjectionError("projection.application_manifest", str(error)) from error
    for path in application["entries"].values():
        _path(path)
    try:
        with tempfile.TemporaryDirectory(prefix="portal-projection-build-") as temporary:
            project = Path(temporary) / "source"
            project.mkdir()
            _snapshot(application, actor, project)
            cache = Path(temporary) / "cache"
            _resolve_lock(project, cache)
            lock = _lock(project)
            _check_cache(cache)
            _sandbox(project, ["ci"], cache=cache, network=True)
            _check_cache(cache)
            dependencies = _dependencies(project, lock)
            _sandbox(project, ["run", "build"], cache=cache)
            index, contents = _output(project, application)
            digest = _store(index, contents)
        return {"status": "ready", "commit": application["commit"], "artifact_hash": digest,
                **index, "dependencies": dependencies}
    except PermissionError:
        raise  # Preserve ResourceService's current-actor authorization failure.
    except OSError as error:
        raise ProjectionError("projection.application_io", "应用包文件读取或隔离构建失败") from error


def read_artifact(build: dict, path: str) -> tuple[bytes, str]:
    """Read an indexed asset, checking both the receipt and stored file hashes."""
    if not isinstance(build, dict) or build.get("status") != "ready" or not _HEX.fullmatch(str(build.get("artifact_hash", ""))):
        _fail("artifact", "应用包尚未构建成功")
    _path(path)
    index = {"entries": build.get("entries"), "files": build.get("files")}
    if (not isinstance(index["entries"], dict) or not isinstance(index["files"], dict)
            or path not in index["files"] or hashlib.sha256(_canonical(index)).hexdigest() != build["artifact_hash"]):
        _fail("artifact", "构建产物索引或路径无效")
    metadata = index["files"][path]
    if (not isinstance(metadata, dict) or type(metadata.get("size")) is not int or not 0 <= metadata["size"] <= 64 * 1024 * 1024
            or not _HEX.fullmatch(str(metadata.get("sha256", "")))
            or not metadata.get("content_type") or metadata["content_type"] != CONTENT_TYPES.get(PurePosixPath(path).suffix)):
        _fail("artifact", "构建产物文件元数据无效")
    root = _artifact_root()
    try:
        stored = _regular_bytes(root, build["artifact_hash"] + "/artifact.json", limit=4 * 1024 * 1024)
        if stored != _canonical(index):
            _fail("artifact", "构建产物索引完整性校验失败")
        data = _regular_bytes(root, build["artifact_hash"] + "/files/" + path, limit=metadata["size"])
    except OSError as error:
        raise ProjectionError("projection.application_artifact", "构建产物缺失或包含链接") from error
    if len(data) != metadata["size"] or hashlib.sha256(data).hexdigest() != metadata.get("sha256"):
        _fail("artifact", "构建产物内容完整性校验失败")
    return data, metadata["content_type"]
