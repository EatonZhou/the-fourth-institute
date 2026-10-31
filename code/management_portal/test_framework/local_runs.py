"""Local pytest implementations executed in batches.

The planner splits the local part of a plan into groups. The Test executor
runs each group as one pytest process and maps the streamed per-test events to the planned
implementations. Isolated groups run in a bwrap sandbox without network and
without the host's credentials or Portal state; live groups act on the
current DEV as test-actor and are not sandboxed.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from management_common import process_runtime
from management_portal.test_framework import slots
from management_portal.test_framework.catalog import ROOT, planned_source_changed
from management_portal.test_framework.catalog_source import parse_source


LIVE_PROFILE = "live.controlled"
TEXT_LIMIT = 4000
TERMINATE_GRACE_SECONDS = 10
POLL_SECONDS = 0.5


# ---------------------------------------------------------------- planning


def lane(item: dict[str, Any]) -> str:
    profiles = (item.get("implementation") or {}).get("profiles") or []
    return "live" if LIVE_PROFILE in profiles else "isolated"


def plan_groups(
    selected: list[dict[str, Any]], *, continue_on_failure: bool = True, root: Path = ROOT,
) -> list[dict[str, Any]]:
    """Split local pytest implementations into shards."""
    by_kind: dict[tuple[str, str], list[int]] = {}
    for index, item in enumerate(selected):
        by_kind.setdefault((str(item["runner"]["id"]), lane(item)), []).append(index)
    groups: list[dict[str, Any]] = []

    def add(group_id: str, label: str, runner: str, lane_name: str, slot: str, indexes: list[int]) -> None:
        groups.append({
            "group_id": group_id,
            "module_id": "",
            "label": label,
            "runner": runner,
            "lane": lane_name,
            "slot": slot,
            "item_indexes": indexes,
            "continue_on_failure": continue_on_failure,
        })

    shards = _pytest_shards(selected, by_kind.get(("pytest", "isolated"), []), root)
    for number, indexes in enumerate(shards, 1):
        add(f"pytest-{number}", f"pytest {number}/{len(shards)}", "pytest", "isolated", "pytest", indexes)
    if by_kind.get(("pytest", "live")):
        add("pytest-live", "pytest · Live", "pytest", "live", "live", by_kind[("pytest", "live")])
    return groups


def _pytest_shards(selected: list[dict[str, Any]], indexes: list[int], root: Path) -> list[list[int]]:
    """Whole files per shard, balanced by file size; a file's cases keep their plan order."""
    files: dict[str, list[int]] = {}
    for index in indexes:
        files.setdefault(str(selected[index]["implementation"]["source"]), []).append(index)
    if not files:
        return []

    def size(source: str) -> int:
        try:
            return max(1, (root / source).stat().st_size)
        except OSError:
            return 1

    count = min(slots.max_concurrency(), slots.CAPACITY["pytest"], len(files))
    loads = [0] * count
    shards: list[list[int]] = [[] for _ in range(count)]
    for source in sorted(files, key=lambda value: (-size(value), value)):
        target = loads.index(min(loads))
        loads[target] += size(source)
        shards[target].extend(files[source])
    return shards


# ---------------------------------------------------------------- execution


class GroupContext(Protocol):
    run_dir: Path

    def output(self, text: str) -> None: ...
    def started(self, item_index: int) -> None: ...
    def result(self, item_index: int, status: str, returncode: int | None, details: dict[str, Any]) -> None: ...
    def patch(self, fields: dict[str, Any]) -> None: ...
    def stop_reason(self) -> str: ...


STOP_SUMMARIES = {
    "cancelled": "运行已中止，本项没有执行完成。",
    "fail-fast": "失败即停：前面的实现未通过，本项没有执行完成。",
}


def _signal_text(returncode: int) -> str:
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = "未知信号"
    return f"{name}（{-returncode}）"


def _first_error_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    marked = next((line[1:].strip() for line in lines if line.startswith("E ")), "")
    return (marked or (lines[-1] if lines else ""))[:300]


class PytestGroup:
    """Runs a pytest shard and reports one result per planned implementation."""

    label = "pytest"

    def __init__(self, group: dict[str, Any], selected: list[dict[str, Any]], *, root: Path = ROOT,
                 state_roots: list[Path] | None = None):
        self.group = group
        self.selected = selected
        self.root = root
        self.lane = str(group.get("lane") or "isolated")
        self.slot = str(group.get("slot") or "pytest")
        self.continue_on_failure = bool(group.get("continue_on_failure", True))
        self.state_roots = state_roots
        self._reported: set[int] = set()
        self._declarations: dict[str, set[str] | None] = {}
        self._owners: dict[str, list[int]] = {}
        self._nodes: dict[str, dict[str, Any]] = {}
        self._collect_errors: dict[str, str] = {}

    def report(self, ctx: GroupContext, item_index: int, status: str, returncode: int | None,
               details: dict[str, Any]) -> None:
        if item_index in self._reported:
            return
        self._reported.add(item_index)
        ctx.result(item_index, status, returncode, details)

    def report_rest(self, ctx: GroupContext, items: list[tuple[int, dict[str, Any]]], status: str,
                    returncode: int | None, details: dict[str, Any]) -> None:
        for item_index, _item in items:
            self.report(ctx, item_index, status, returncode, dict(details))

    def run(self, ctx: GroupContext) -> dict[str, Any]:
        """Run the group; return group-level facts (error text, isolation holes)."""
        items: list[tuple[int, dict[str, Any]]] = []
        for item_index in self.group.get("item_indexes") or []:
            item = self.selected[int(item_index)]
            if planned_source_changed(item, self.root):
                self.report(ctx, int(item_index), "error", None, {
                    "summary": f"运行计划对应的源码已变化：{item['implementation']['source']}",
                })
            elif self.missing_declaration(int(item_index), item):
                self.report(ctx, int(item_index), "error", None, {
                    "summary": f"测试声明已不在源码中：{item['implementation'].get('entrypoint', '')}",
                    "failure_class": "test",
                })
            else:
                items.append((int(item_index), item))
        if not items:
            return {}
        ctx.patch({"status": "queued", "waiting": "等待并发名额"})
        lease = slots.acquire(self.slot, 1, stop=lambda: bool(ctx.stop_reason()))
        if lease is None:
            reason = ctx.stop_reason() or "cancelled"
            self.report_rest(ctx, items, "cancelled", None, {"summary": STOP_SUMMARIES.get(reason, reason)})
            return {}
        try:
            ready = []
            for item_index, item in items:
                if planned_source_changed(item, self.root):
                    self.report(ctx, item_index, "error", None, {
                        "summary": f"等待并发名额期间源码已变化：{item['implementation']['source']}",
                    })
                else:
                    ready.append((item_index, item))
            return self._run_process(ready, ctx, lease.count) if ready else {}
        finally:
            lease.release()

    def _run_process(self, items: list[tuple[int, dict[str, Any]]], ctx: GroupContext, slot_count: int) -> dict[str, Any]:
        group_dir = ctx.run_dir / str(self.group["group_id"])
        group_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        results_file = group_dir / "results.jsonl"
        results_file.write_text("", encoding="utf-8")
        command, environment = self.command(items, group_dir)
        environment["PORTAL_TEST_RESULTS_FILE"] = str(results_file)
        if self.lane == "isolated":
            environment["PORTAL_ACCOUNT_HOST_HOME"] = str(group_dir.resolve() / "host-home")
        scratch: list[tuple[Path, Path]] = []
        container = None
        if self.lane == "isolated":
            if sys.platform in {"win32", "darwin"}:
                container = ContainerPytest(self.root, group_dir, command, self.protected_roots())
                command, environment = container.command, container.environment
            else:
                prefix, scratch = self.sandbox(group_dir)
                command = [*prefix, "--", *command]
        try:
            ctx.patch({"status": "running", "waiting": "", "slots": slot_count})
            output_log = (group_dir / "output.log").open("a", encoding="utf-8")
        except BaseException:
            if container:
                container.close(None)
            raise
        with output_log as log:
            try:
                process = process_runtime.spawn_process(
                    command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", env=environment,
                    **({"stdin": subprocess.PIPE, "kill_on_owner_exit": False,
                        "tree_id": container.plan["name"] + "-guardian"} if container else {}),
                )
            except BaseException:
                if container:
                    container.close(None)
                raise
            output_errors: list[Exception] = []

            def pump() -> None:
                assert process.stdout is not None
                try:
                    for chunk in iter(process.stdout.readline, ""):
                        log.write(chunk)
                        ctx.output(chunk)
                except Exception as error:
                    output_errors.append(error)

            reader = threading.Thread(target=pump, name=f"{self.group['group_id']}-output", daemon=True)
            position = 0
            stop_reason = ""
            try:
                reader.start()
                while True:
                    if output_errors:
                        raise output_errors[0]
                    position = self._consume(results_file, position, ctx, group_dir)
                    if not stop_reason:
                        stop_reason = ctx.stop_reason()
                        if stop_reason:
                            if container:
                                container.close(process)
                            else:
                                process_runtime.terminate_process_tree(process, timeout=TERMINATE_GRACE_SECONDS)
                    if process.poll() is not None:
                        break
                    time.sleep(POLL_SECONDS)
                returncode = process.wait()
            finally:
                # Also reap helpers after normal leader exit and callback errors.
                # The runtime owns POSIX sessions / Windows Jobs and escalation.
                try:
                    if container:
                        container.close(process)
                    process_runtime.terminate_process_tree(process, force=True)
                finally:
                    if reader.ident is not None:
                        reader.join(timeout=5)
                    if not reader.is_alive() and process.stdout is not None:
                        process.stdout.close()
                if reader.is_alive():
                    raise RuntimeError("pytest 输出进程未退出，清理尚未完成")
            if output_errors:
                raise output_errors[0]
            self._consume(results_file, position, ctx, group_dir)
            stop_reason = stop_reason or ctx.stop_reason()
            self.finish(items, ctx, group_dir, returncode, stop_reason)
        holes = self.isolation_holes(scratch, group_dir)
        if scratch and not holes:
            shutil.rmtree(scratch[0][1].parent, ignore_errors=True)
        if holes:
            return {
                "isolation_holes": holes,
                "error": "测试写入了被隔离的真实状态目录：" + "、".join(holes[:5]),
            }
        return {}

    def _consume(self, path: Path, position: int, ctx: GroupContext, group_dir: Path) -> int:
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(group_dir.resolve()):
                raise RuntimeError("pytest 结果路径越过本组产物目录")
            with path.open("r", encoding="utf-8") as stream:
                stream.seek(position)
                while True:
                    line = stream.readline()
                    if not line or not line.endswith("\n"):
                        return position
                    position += len(line.encode("utf-8"))
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        self.handle_event(event, ctx, group_dir)
        except FileNotFoundError:
            return position

    # Sandbox for isolated groups.
    def protected_roots(self) -> list[Path]:
        if self.state_roots is not None:
            return list(self.state_roots)
        from management_portal.test_framework.paths import state_root, user_state_root

        return [user_state_root(), state_root()]

    def sandbox(self, group_dir: Path) -> tuple[list[str], list[tuple[Path, Path]]]:
        if sys.platform != "linux" or not shutil.which("bwrap"):
            raise RuntimeError("isolated pytest 需要 Linux namespace 与 bwrap；不能降级为宿主执行")
        from management_portal.test_framework.isolation import (
            run_root, credential_paths, isolation_args, mask_args,
        )

        args = isolation_args(network=False)
        if self.root.is_relative_to("/tmp"):
            args += ["--bind", str(self.root), str(self.root)]  # the private /tmp would hide it
        args += mask_args([*credential_paths(), run_root()])
        # Real Portal state is replaced by empty scratch directories whose
        # content shows afterwards whether a test wrote there.
        scratch: list[tuple[Path, Path]] = []
        roots = sorted({path.resolve() for path in self.protected_roots() if path.exists()}, key=lambda p: len(str(p)))
        for number, root in enumerate(roots):
            if any(root != other and root.is_relative_to(other) for other in roots):
                continue
            target = group_dir.parent / f".{group_dir.name}-isolation" / str(number)
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            scratch.append((root, target))
            args += ["--bind", str(target), str(root)]
        args += ["--bind", str(group_dir), str(group_dir), "--chdir", str(self.root)]
        return args, scratch

    @staticmethod
    def isolation_holes(scratch: list[tuple[Path, Path]], group_dir: Path) -> list[str]:
        """Paths a sandboxed test wrote under a protected root (shown by its scratch directory)."""
        holes: list[str] = []
        group = group_dir.resolve()
        for root, target in scratch:
            # bwrap creates the directories leading to the group's own mount.
            mount_chain: set[Path] = set()
            if group.is_relative_to(root):
                parts = group.relative_to(root).parts
                mount_chain = {target.joinpath(*parts[:depth]) for depth in range(1, len(parts) + 1)}
            for path in sorted(target.rglob("*")):
                if path in mount_chain and path.is_dir() and not path.is_symlink():
                    continue
                holes.append(str(root / path.relative_to(target)))
        return holes

    def environment(self, extra: dict[str, str]) -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "TZ",
                        "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                        "USERPROFILE", "LOCALAPPDATA", "PLAYWRIGHT_BROWSERS_PATH")
            if os.environ.get(key)
        }
        environment.setdefault("HOME", str(Path.home()))
        environment.setdefault("PATH", os.defpath)
        # Reuse installed executables, never the deployed database or its DSN.
        # The fixture creates a separate cluster and socket for every session.
        from management_common.postgres_configuration import postgres_settings

        postgres = postgres_settings()
        if (Path(postgres["bin"]) / "initdb").is_file():
            environment["PORTAL_TEST_POSTGRES_BIN"] = postgres["bin"]
            environment["PORTAL_TEST_POSTGRES_LIB"] = postgres["lib"]
        if self.lane == "live":
            for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                if os.environ.get(key):
                    environment[key] = os.environ[key]
        environment.update({
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "FORCE_COLOR": "0",
            "NO_COLOR": "1",
            **extra,
        })
        return environment

    def missing_declaration(self, item_index: int, item: dict[str, Any]) -> bool:
        source, _separator, name = str(item["implementation"]["entrypoint"]).partition("::")
        if not name:
            return not (self.root / source).is_file()
        if source not in self._declarations:
            parsed = parse_source(self.root / source, source)
            self._declarations[source] = (None if parsed.error else {
                test.entrypoint.partition("::")[2] for test in parsed.tests
            })
        names = self._declarations[source]
        return names is None or name.split("[", 1)[0] not in names

    def command(self, items, group_dir):
        self._owners = {}
        for item_index, item in items:
            self._owners.setdefault(str(item["implementation"]["entrypoint"]), []).append(item_index)
        arguments = group_dir / "pytest-args.txt"
        arguments.write_text("\n".join(self._owners) + "\n", encoding="utf-8")
        command = [
            sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
            "-p", "management_portal.test_framework.pytest_results", "--continue-on-collection-errors",
            f"@{arguments}",
        ]
        if self.lane == "isolated":
            # The sandbox supplies a private /tmp; live pytest uses its own
            # normal unique temporary directory instead of a shared fixed path.
            command.insert(-1, f"--basetemp=/tmp/tfi-pytest-{uuid.uuid4().hex}")
        if not self.continue_on_failure:
            command.insert(-1, "--maxfail=1")
        return command, self.environment({})

    def owner(self, nodeid: str) -> list[int]:
        base = nodeid.split("[", 1)[0]
        while base:
            if base in self._owners:
                return self._owners[base]
            if "::" not in base:
                return []
            base = base.rsplit("::", 1)[0]
        return []

    def handle_event(self, event, ctx, group_dir):
        kind = event.get("event")
        nodeid = str(event.get("nodeid") or "")
        if kind == "collect-error":
            self._collect_errors[nodeid] = str(event.get("text") or "")
            return
        owners = self.owner(nodeid)
        if not owners:
            return
        if kind == "start":
            ctx.started(owners[0])
            return
        state = self._nodes.setdefault(nodeid, {"outcome": "", "text": "", "when": ""})
        if kind == "report":
            outcome, when = str(event.get("outcome") or ""), str(event.get("when") or "")
            if outcome == "failed":
                state.update(outcome="failed", text=str(event.get("text") or ""), when=when)
                for item_index in owners:
                    self.report(ctx, item_index, "failed", 1, self._failure_details(state, group_dir))
            elif outcome == "skipped" and state["outcome"] != "failed":
                state.update(outcome="passed" if event.get("xfail") else "skipped", text=str(event.get("text") or ""))
            elif outcome == "passed" and state["outcome"] == "":
                state["outcome"] = "passed"

    def _failure_details(self, state: dict[str, Any], group_dir: Path) -> dict[str, Any]:
        phase = {"setup": "准备", "teardown": "清理"}.get(str(state.get("when") or ""), "")
        headline = _first_error_line(state.get("text"))
        prefix = f"pytest 失败（{phase}阶段）" if phase else "pytest 失败"
        return {
            "summary": f"{prefix}：{headline}" if headline else f"{prefix}。",
            "issues": [str(state.get("text") or "")[-TEXT_LIMIT:]],
            "evidence": [f"{group_dir.name}/output.log"],
        }

    def finish(self, items, ctx, group_dir, returncode, stop_reason):
        outcomes: dict[int, list[dict[str, Any]]] = {}
        for nodeid, state in self._nodes.items():
            for item_index in self.owner(nodeid):
                outcomes.setdefault(item_index, []).append(state)
        for item_index, item in items:
            states = [state for state in outcomes.get(item_index, []) if state.get("outcome")]
            source = str(item["implementation"]["source"])
            if any(state["outcome"] == "failed" for state in states):
                continue  # already reported when the failure streamed in
            if states and all(state["outcome"] == "skipped" for state in states):
                reason = _first_error_line(states[0].get("text")) or "用例被跳过"
                self.report(ctx, item_index, "unsupported", returncode, {"summary": f"pytest 跳过：{reason}"})
            elif states:
                self.report(ctx, item_index, "passed", returncode, {"summary": "pytest 通过。"})
            elif source in self._collect_errors:
                text = self._collect_errors[source]
                self.report(ctx, item_index, "failed", returncode, {
                    "summary": f"pytest 无法收集测试文件：{_first_error_line(text)}",
                    "issues": [text],
                    "evidence": [f"{group_dir.name}/output.log"],
                })
            elif stop_reason:
                self.report(ctx, item_index, "cancelled", returncode, {"summary": STOP_SUMMARIES.get(stop_reason, stop_reason)})
            elif returncode < 0:
                self.report(ctx, item_index, "error", returncode, {
                    "summary": f"pytest 执行进程被 {_signal_text(returncode)}终止，本项没有结果。",
                    "evidence": [f"{group_dir.name}/output.log"],
                })
            else:
                self.report(ctx, item_index, "error", returncode, {
                    "summary": f"pytest 结束时没有报告本项结果（退出码 {returncode}）。",
                    "evidence": [f"{group_dir.name}/output.log"],
                })


def group_runner(group: dict[str, Any], selected: list[dict[str, Any]], **kwargs: Any) -> PytestGroup:
    runner = str(group.get("runner") or "")
    if runner == "pytest":
        return PytestGroup(group, selected, **kwargs)
    raise ValueError(f"本地运行不支持的执行方式：{runner}")


# Docker is a transport for the isolated lane only. Its daemon resources are
# not descendants of the CLI, so a separate pipe-watching guardian owns them.
def _container_env(home: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=os.defpath, HOME=str(home), USERPROFILE=str(home),
               DOCKER_CONFIG=str(home), LANG="C.UTF-8", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return env


def _docker_control(plan: dict, args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess:
    from management_portal.workbench_projections.application_build import _docker_cli
    return _docker_cli([*plan["prefix"], *args], env=plan["environment"], timeout=timeout, limit=1024 * 1024)


def _remove_container(plan: dict, *, uncertain_create: bool = False) -> None:
    # Never issue rm against a name with a different ownership label. A failed
    # inspect is not evidence of absence: require a successful exact listing.
    found = _docker_control(plan, ["container", "ls", "--all", "--filter", "name=^/" + plan["name"] + "$", "--format", "{{.ID}}"])
    if found.returncode:
        raise RuntimeError("pytest 容器状态不可读取，清理未完成")
    ids = found.stdout.decode().split()
    if not ids:
        if uncertain_create:
            raise RuntimeError("pytest 容器创建结果未知，不能凭一次空列表确认清理")
        return
    if len(ids) != 1:
        raise RuntimeError("pytest 容器归属不唯一，拒绝删除")
    inspected = _docker_control(plan, ["inspect", "--format", "{{json .Config.Labels}}", ids[0]])
    if inspected.returncode or (json.loads(inspected.stdout or b"null") or {}).get('portal.test.owner') != plan["name"]:
        raise RuntimeError("pytest 容器归属已改变，拒绝删除")
    removed = _docker_control(plan, ["rm", "--force", ids[0]])
    if removed.returncode:
        raise RuntimeError("pytest 容器删除失败，清理未完成")


class ContainerPytest:
    """Private source snapshot + preloaded local Linux image, never host pytest."""
    def __init__(self, root: Path, group_dir: Path, command: list[str], protected: list[Path]):
        from management_portal.workbench_projections.application_build import _container_settings
        settings = _container_settings()  # Same pinned-image/local-engine contract as application builds.
        self.directory = Path(tempfile.gettempdir()).resolve() / ("portal-pytest-" + uuid.uuid4().hex)
        self.closed = False
        try:
            from management_common.file_permissions import ensure_private_directory
            ensure_private_directory(self.directory)
            source, config = self.directory / "source", self.directory / "docker"
            source.mkdir(); config.mkdir()
            self.environment = _container_env(config)
            # Only versioned source and the selected test files are eligible.
            # Never copy .git, untracked host state, credentials or symlink targets.
            listed = process_runtime.run_process([shutil.which("git") or "git", "ls-files", "-z", "--cached"], cwd=root,
                                                 env=self.environment, capture_output=True, check=True, timeout=30)
            paths = set(listed.stdout.decode("utf-8").split("\0")) - {""}
            args_file = group_dir / "pytest-args.txt"
            entries = args_file.read_text(encoding="utf-8").splitlines()
            paths.update(entry.split("::", 1)[0] for entry in entries)
            protected = [p.resolve() for p in protected]
            from management_common.instance_paths import secrets_dir
            from management_common.provider_account_paths import provider_root, PROVIDERS
            protected += [secrets_dir().resolve(), *(provider_root(p).resolve() for p in PROVIDERS)]
            for name in sorted(paths):
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("pytest 源码必须位于仓库内")
                original = root / relative
                if not original.exists():
                    continue
                if (any(part.startswith('.env') or part in {'.git', '.ssh', '.gnupg', 'portal_instances', '.codex-accounts', '.claude-accounts', '.kimi-accounts', '.antigravity-accounts'} for part in relative.parts)
                        or original.suffix.lower() in {'.env', '.key', '.pem', '.p12'}
                        or any(original.resolve().is_relative_to(p) for p in protected)):
                    continue
                if any(p.is_symlink() for p in [original, *original.parents] if p != root.parent and p.is_relative_to(root)):
                    raise RuntimeError(f"pytest 容器源码含符号链接，拒绝挂载：{relative}")
                if not original.is_file():
                    raise RuntimeError(f"pytest 容器源码不是普通文件：{relative}")
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
            for entry in entries:
                if not (source / entry.split('::', 1)[0]).is_file():
                    raise RuntimeError("选中的 pytest 源码被隔离边界排除")
            args_file.write_text('\n'.join(entries) + '\n', encoding='utf-8')
            translated = ["python3", *command[1:]]
            translated = ["@/artifacts/pytest-args.txt" if arg == '@' + str(args_file) else arg for arg in translated]
            for mount in (source, group_dir.resolve()):
                if any(c in str(mount) for c in ',\n\r'):
                    raise RuntimeError("Docker 挂载路径含不支持的分隔符")
            name = "portal-pytest-" + uuid.uuid4().hex
            self.plan = {"name": name, "prefix": [settings['docker_executable'], '--host', settings['docker_endpoint'], '--config', str(config)],
                         "environment": self.environment, "image": settings['container_image'],
                         "source": str(source), "artifacts": str(group_dir.resolve()), "argv": translated,
                         "user": str(os.getuid()) if hasattr(os, "getuid") else "0"}
            import psutil
            self.plan['executor'] = {'pid': os.getpid(), 'created_at': psutil.Process(os.getpid()).create_time()}
            plan_file = self.directory / 'plan.json'
            from management_portal.shared_state import atomic_replace_json
            atomic_replace_json(plan_file, self.plan, mode=0o600)
            self.command = [sys.executable, '-m', __name__, '--container-worker', str(plan_file)]
        except BaseException:
            if self.directory.exists():
                shutil.rmtree(self.directory)
            raise

    def close(self, process) -> None:
        if self.closed:
            return
        try:
            if process is not None:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()  # EOF is also delivered when the executor dies.
                try:
                    process.wait(timeout=100)
                except subprocess.TimeoutExpired as error:
                    # Do not kill a guardian that could still be creating a
                    # container. Retain its plan and report unproven cleanup.
                    raise RuntimeError("pytest 容器 guardian 未完成清理，保留恢复计划") from error
                receipt_path = self.directory / 'cleanup.json'
                receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
                if receipt.get('complete') is not True:
                    # After the guardian has exited no create can race this recovery.
                    _remove_container(self.plan, uncertain_create=receipt.get('uncertain_create', True))
                    from management_portal.shared_state import atomic_replace_json
                    atomic_replace_json(Path(self.plan['artifacts']) / 'container-cleanup.json', {
                        'schema': 'tfi.pytest-container-cleanup.v1', 'complete': True,
                        'name': self.plan['name'], 'recovered_by_executor': True,
                    }, mode=0o600)
            self.closed = True
        finally:
            if self.closed:
                shutil.rmtree(self.directory, ignore_errors=True)


def _container_executor_alive(plan: dict) -> bool:
    import psutil
    identity = plan.get('executor')
    if not identity:
        return False
    try:
        process = psutil.Process(identity['pid'])
        return process.create_time() == identity['created_at'] and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True  # Retain the private receipt when liveness is uncertain.


def _watch_container_parent(stopped: threading.Event) -> None:
    # BufferedReader holds its lock while blocked and can abort CPython at exit.
    try:
        while os.read(0, 4096):
            pass
    finally:
        stopped.set()


def _container_worker(plan_file: Path) -> int:
    plan = json.loads(plan_file.read_text(encoding='utf-8'))
    stopped = threading.Event()
    threading.Thread(target=_watch_container_parent, args=(stopped,), daemon=True).start()
    process = None
    create_attempted = False
    create_confirmed = False
    try:
        image = _docker_control(plan, ['image', 'inspect', '--format', '{{json .}}', plan['image']])
        if image.returncode:
            raise RuntimeError('pytest 需要本机预加载的固定镜像；不会拉取或回退宿主')
        info = json.loads(image.stdout)
        if (info.get('Os') != 'linux' or info.get('Config', {}).get('Volumes')
                or not (info.get('Id') == plan['image'] or plan['image'] in (info.get('RepoDigests') or []))):
            raise RuntimeError('pytest 镜像摘要/平台/隐式卷不满足隔离合同')
        if stopped.is_set():
            return 130
        argv = ['create', '--pull=never', '--name', plan['name'], '--label', 'portal.test.owner=' + plan['name'],
                '--init', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--no-healthcheck',
                '--log-driver=none', '--network=none', '--pids-limit=256', '--memory=2g', '--memory-swap=2g', '--cpus=2',
                '--ulimit', 'core=0', '--ulimit', 'nofile=1024:1024', '--user=' + plan['user'], '--workdir=/app',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=512m,mode=1777',
                '--mount', f"type=bind,src={plan['source']},dst=/app,readonly",
                '--mount', f"type=bind,src={plan['artifacts']},dst=/artifacts",
                '--entrypoint', '/usr/bin/env', info['Id'], '-i', 'PATH=/usr/local/bin:/usr/bin:/bin',
                'HOME=/tmp', 'PORTAL_ACCOUNT_HOST_HOME=/tmp/host-home', 'LANG=C.UTF-8', 'PYTHONUNBUFFERED=1', 'PYTHONIOENCODING=utf-8', 'PYTHONDONTWRITEBYTECODE=1',
                'PORTAL_TEST_RESULTS_FILE=/artifacts/results.jsonl', *plan['argv']]
        create_attempted = True
        created = _docker_control(plan, argv)
        if created.returncode:
            raise RuntimeError('pytest 容器创建失败：' + created.stderr.decode('utf-8', 'replace')[-1000:])
        create_confirmed = True
        if stopped.is_set():
            return 130
        process = process_runtime.spawn_process([*plan['prefix'], 'start', '--attach', plan['name']], env=plan['environment'],
                                                stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)
        while process.poll() is None and not stopped.wait(0.1):
            pass
        if stopped.is_set():
            return 130
        state = _docker_control(plan, ['inspect', '--format', '{{json .State}}', plan['name']])
        data = json.loads(state.stdout) if state.returncode == 0 else {}
        if data.get('Running') is not False or type(data.get('ExitCode')) is not int or data.get('Error'):
            raise RuntimeError('pytest 容器没有可确认的终态')
        return data['ExitCode']
    finally:
        complete = False
        try:
            if create_attempted:
                _remove_container(plan, uncertain_create=not create_confirmed)
            if process is not None:
                process_runtime.terminate_process_tree(process, force=True)
            complete = True
        finally:
            from management_portal.shared_state import atomic_replace_json
            receipt = {
                'schema': 'tfi.pytest-container-cleanup.v1', 'complete': complete,
                'uncertain_create': create_attempted and not create_confirmed,
                'name': plan['name'], 'recovery_plan': '' if complete else str(plan_file),
            }
            # Authoritative control state is never mounted into the test. The
            # public copy is evidence only and must not follow a test's symlink.
            atomic_replace_json(plan_file.parent / 'cleanup.json', receipt, mode=0o600)
            atomic_replace_json(Path(plan['artifacts']) / 'container-cleanup.json', receipt, mode=0o600)
            if complete and not _container_executor_alive(plan):
                shutil.rmtree(plan_file.parent)
            elif process is not None:
                process_runtime.terminate_process_tree(process, force=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--container-worker':
        raise SystemExit(_container_worker(Path(sys.argv[2])))
    raise SystemExit('Only --container-worker <plan> is supported')
