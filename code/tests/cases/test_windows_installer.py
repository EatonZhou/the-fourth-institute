"""Windows installer boundaries: native output encodings, user services, launcher and package.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import importlib.util
import importlib.machinery
import io
import json
import os
import locale
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import hashlib
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(REPO))
from management_common.instance_manifest import load
from scripts import tfi_windows_launcher as launcher
from scripts import tfi_windows_user_services as services
from scripts import tfi_release_platform_windows as windows
from scripts import tfi_release_install as installer
from management_common import native_output
from scripts import portal_service_control
from tests.cases.instance_test_support import write_instance
import prepare_install

PYTHON = Path("C:/TFI/dependencies/0123/venv/Scripts/python.exe")


@contextmanager
def code_environment(python=PYTHON):
    """Definitions name the code environment; tests do not need a real one."""
    with patch.object(windows, "runtime_python", return_value=python), \
            patch("management_common.code_environment.runtime_python", return_value=python):
        yield


class WindowsInstallerTests(unittest.TestCase):
    @contextmanager
    def native_probe(self, stdout, stderr=b"", exitcode=0):
        """Exercise real byte pipes while substituting only the native binary."""
        from scripts import article_dependencies as article
        from scripts import install_article_tooling as tooling

        run = native_output.run_process
        command = [sys.executable, "-c",
                   f"import os; os.write(1, {stdout!r}); os.write(2, {stderr!r}); "
                   f"raise SystemExit({exitcode})"]

        calls = []

        def execute(_command, **options):
            calls.append(_command)
            result = run(command, **options)
            # POSIX truncates exit statuses to eight bits; SCM returns DWORDs.
            if os.name != "nt" and exitcode > 255:
                self.assertEqual(result.returncode, exitcode % 256)
                result.returncode = exitcode
            return result

        with patch.object(locale, "getencoding", return_value="cp936"), \
                patch.object(native_output, "run_process", side_effect=execute), \
                patch.object(article, "run_process", side_effect=execute), \
                patch.object(tooling, "run_process", side_effect=execute):
            yield calls

    def instance(self, base, name="instance"):
        return load(write_instance(Path(base) / name))

    def test_absent_scm_service_is_read_from_localized_output(self):
        """SCM 查询接受中文输出和不存在服务的 1060。"""
        diagnostic = (" " * 43 + "安装检查：指定服务不存在。\r\n").encode("cp936")
        with self.native_probe(diagnostic, exitcode=1060) as calls:
            status = portal_service_control.ServiceControl(platform="windows").status("tfi-example-web")
        self.assertEqual(status["load"], "not-found")
        self.assertEqual([list(args) for args in calls], [["sc.exe", "queryex", "tfi-example-web"]])

    def test_scm_permission_error_is_not_mistaken_for_absent_service(self):
        """查询被拒绝时保留实际退出码，诊断里偶然含有 1060 也不能放行。"""
        with self.native_probe("服务 1060：拒绝访问".encode("cp936"), exitcode=5):
            with self.assertRaisesRegex(RuntimeError, r"exit 5.*拒绝访问"):
                portal_service_control.ServiceControl(platform="windows").status("tfi-example-web")

    def test_scm_running_service_keeps_localized_diagnostics(self):
        """原生诊断解码后仍保留服务状态与 PID。"""
        output = "服务说明：知识平台\r\nSTATE : 4 RUNNING\r\nPID : 1234\r\n".encode("cp936")
        with self.native_probe(output):
            status = portal_service_control.ServiceControl(platform="windows").status("tfi-example-web")
        self.assertEqual((status["load"], status["active"], status["pid"]), ("loaded", "active", 1234))

    def test_helper_json_and_localized_stderr_are_separate(self):
        """安装器子程序的 JSON 保持 UTF-8，中文 stderr 不混入 JSON。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp)
            response = json.dumps({"name": "示例", "ok": True}, ensure_ascii=False)
            with self.native_probe(response.encode("utf-8"), "安装提示".encode("cp936")), \
                    patch.object(installer, "runtime_python", return_value=Path(sys.executable)), \
                    patch("management_portal.service_runtime.clean_environment", return_value=dict(os.environ)):
                output = installer.code_child(instance, "tfi_release_initialization.py", "verify-frozen", timeout=10)
        self.assertEqual(json.loads(output), {"name": "示例", "ok": True})

    def test_helper_failure_preserves_code_and_both_diagnostic_streams(self):
        """失败子程序的日志保留两路诊断及真正的退出码。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp)
            (instance.root / "runtime").mkdir()
            with self.native_probe("准备失败\n".encode("cp936"), "拒绝访问".encode("cp936"), 7), \
                    patch.object(installer, "runtime_python", return_value=Path(sys.executable)), \
                    patch("management_portal.service_runtime.clean_environment", return_value=dict(os.environ)):
                with self.assertRaisesRegex(RuntimeError, "exited 7"):
                    installer.code_child(instance, "tfi_release_initialization.py", "verify-frozen", timeout=10)
            self.assertEqual((instance.root / "runtime/command-error.log").read_text(encoding="utf-8"),
                             "准备失败\n拒绝访问")

    def test_helper_control_output_remains_strict_utf8(self):
        """Python 控制输出不采用本地编码容错。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp)
            with self.native_probe(b"\xb0\xb2"), \
                    patch.object(installer, "runtime_python", return_value=Path(sys.executable)), \
                    patch("management_portal.service_runtime.clean_environment", return_value=dict(os.environ)):
                with self.assertRaises(UnicodeDecodeError):
                    installer.code_child(instance, "tfi_release_initialization.py", "verify-frozen", timeout=10)

    def test_bootstrap_probes_keep_json_and_localized_diagnostics_separate(self):
        """依赖引导的 pip、Python 与 Node 探测经过真实管道编码边界。"""
        loader = importlib.machinery.SourceFileLoader(
            "bootstrap_encoding_fixture", str(REPO / "code/scripts/bootstrap_portal"))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        with self.native_probe(b'[{"name":"PyYAML","version":"6.0.2"}]', "安装提示".encode("cp936")):
            self.assertEqual(module.installed_versions(Path(sys.executable)), {"pyyaml": "6.0.2"})
        with self.native_probe(b"3.12.8\n", "检查完成".encode("cp936")):
            self.assertEqual(module.interpreter_version(Path(sys.executable)), (3, 12, 8))
        with self.native_probe(b"v22.1.0\n", "检查完成".encode("cp936")), \
                patch.object(module.shutil, "which", return_value="fixture-node"):
            self.assertEqual(module.node_major(), 22)

    def test_dependency_doctor_accepts_localized_stderr_without_reinstalling(self):
        """已满足依赖的 doctor 带中文 stderr 时仍完成检查，不误触发重新安装。"""
        from scripts import tfi_release_dependencies as dependencies
        from management_common.code_environment import environment_directory

        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp) / "program/code"
            (code / "scripts").mkdir(parents=True)
            (code / "scripts/bootstrap_portal").write_text("# fixture\n")
            python = environment_directory(code) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.write_text("")
            checks = [{"name": "core: example", "ok": True}]
            with self.native_probe(json.dumps(checks).encode("utf-8"), "检查提示".encode("cp936")), \
                    patch.object(dependencies, "run_process", side_effect=AssertionError("Unexpected reinstall")):
                result = dependencies.prepare(code)
            self.assertEqual(result["inventory"], checks)
            self.assertEqual(result["source"], "verified-existing")

    def test_installer_error_includes_stack_without_local_values(self):
        """失败日志包含调用位置与异常类型，标准输出仍是 JSON，不泄露局部值。"""
        def fail(*args, **kwargs):
            private_local = "private-value-not-for-diagnostics"
            raise UnicodeDecodeError("utf-8", b" " * 43 + b"\xb0", 43, 44, "invalid start byte")

        output, diagnostic = io.StringIO(), io.StringIO()
        with patch.object(installer, "select", side_effect=fail), \
                redirect_stdout(output), redirect_stderr(diagnostic):
            self.assertEqual(installer.main(["status", "--instance", "example_instance"]), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["error_type"], "UnicodeDecodeError")
        self.assertIn("position 43", result["error"])
        self.assertIn("tfi_release_install.py:", diagnostic.getvalue())
        self.assertIn("in fail", diagnostic.getvalue())
        self.assertNotIn("private-value-not-for-diagnostics", diagnostic.getvalue())

    def test_article_doctor_preserves_localized_paths_and_tool_diagnostics(self):
        """文章依赖检查在 UTF-8 模式下完整保留中文工具路径与诊断。"""
        from scripts import article_dependencies as article
        from scripts import install_article_tooling as tooling

        path = "C:/Users/安装 用户/.TinyTeX/texmf/ctexbook.cls"
        with self.native_probe((path + "\r\n").encode("cp936"), "检查".encode("cp936")), \
                patch.object(article, "TOOLS", ("kpsewhich",)), \
                patch.object(article, "TEX_FILES", ("ctexbook.cls",)), \
                patch.object(article.shutil, "which", return_value=sys.executable):
            checks = article.inventory()
        self.assertTrue(all(row["ok"] for row in checks))
        self.assertEqual([row["detail"] for row in checks], [path, path])
        with self.native_probe(b"texlab 5.26.0\r\n", "检查完成".encode("cp936")):
            self.assertEqual(tooling._version("texlab.exe"), "texlab 5.26.0")

    def test_article_compile_error_retains_exit_code_and_localized_output(self):
        """试编译失败保留实际退出码和诊断，不被中文解码异常替换。"""
        from scripts import article_dependencies as article

        with self.native_probe("编译失败".encode("cp936"), "字体不存在".encode("cp936"), 7):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                article.smoke_compile()
        self.assertEqual(caught.exception.returncode, 7)
        self.assertEqual(caught.exception.stdout, "编译失败")
        self.assertEqual(caught.exception.stderr, "字体不存在")

    def test_native_command_accepts_chinese_code_page_in_utf8_environment(self):
        """原生命令的中文输出不受父进程 UTF-8 模式限制。"""
        text = " " * 43 + "安装完成\r\n"
        raw = text.encode("cp936")
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("utf-8")
        command = [sys.executable, "-c",
                   f"import os; os.write(1, {raw!r}); os.write(2, {'警告'.encode('cp936')!r})"]
        with patch.object(locale, "getencoding", return_value="cp936"):
            result = native_output.run_native_capture(command, env={**os.environ, "PYTHONUTF8": "1"}, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, text.replace("\r\n", "\n"))
        self.assertEqual(result.stderr, "警告")

    def test_native_output_keeps_utf8_and_unknown_bytes(self):
        """原生输出优先识别 UTF-8，未知字节作为转义诊断保留。"""
        with patch.object(locale, "getencoding", return_value="cp936"):
            self.assertEqual(native_output.decode_native_output("安装 ✓".encode()), "安装 ✓")
            self.assertEqual(native_output.decode_native_output(b"\xff"), r"\xff")

    def test_native_failure_keeps_real_exit_code_and_chinese_diagnostic(self):
        """原生命令失败保留退出码和中文日志，不被解码异常遮蔽。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp)
            (instance.root / "runtime").mkdir()
            command = [sys.executable, "-c",
                       f"import os; os.write(2, {'安装失败'.encode('cp936')!r}); raise SystemExit(7)"]
            with patch.object(locale, "getencoding", return_value="cp936"):
                with self.assertRaisesRegex(RuntimeError, "exited 7"):
                    installer.run_checked(command, instance=instance, env=dict(os.environ), timeout=10)
            self.assertEqual((instance.root / "runtime/command-error.log").read_text(encoding="utf-8"), "安装失败")

    def test_database_tools_use_native_output_boundary_and_manifest_postgres(self):
        """数据库初始化及导入统一经过原生命令边界，使用清单记录的 PostgreSQL。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp)
            with patch.object(installer, "postgres_environment", return_value={}), \
                    patch.object(installer, "run_checked", return_value="") as run:
                installer.initialize_database(instance)
                installer.import_database(instance, Path(tmp) / "distribution.sql")
            programs = [Path(call.args[0][0]) for call in run.call_args_list]
            self.assertEqual([p.stem for p in programs], ["initdb", "createdb", "psql"])
            self.assertTrue(all(p.parent == Path(instance.document["dependencies"]["postgres"]["bin"])
                                for p in programs))

    def test_native_dependency_probe_handles_localized_stderr(self):
        """原生依赖版本探测接受本地编码的诊断输出。"""
        from scripts import tfi_release_native_dependencies as native
        with self.native_probe(b"postgres (PostgreSQL) 16.15\n", "安装检查".encode("cp936")):
            result = native.verify({"directory": "/fixture/pg", "platform": "windows/x86_64",
                                    "name": "postgres", "version": "16.15"})
        self.assertEqual(result, {"postgres": "postgres (PostgreSQL) 16.15"})

    def definition(self, base, name="instance"):
        instance = self.instance(base, name)
        (instance.root / "runtime").mkdir(exist_ok=True)
        with code_environment():
            content = windows.manifest_unit_text(instance, "web")
        return instance, json.loads(content), instance.service_name("web")

    def test_instances_share_the_code_environment_but_not_root_or_identity(self):
        """两个实例共用代码与运行环境，定义中的实例根和服务名不同。"""
        with tempfile.TemporaryDirectory() as tmp:
            one, first, _ = self.definition(tmp, "one")
            two, second, _ = self.definition(tmp, "two")
            self.assertEqual((first["python"], first["code"]), (second["python"], second["code"]))
            self.assertNotEqual(first["root"], second["root"])
            self.assertEqual(first["environment"], {"PORTAL_INSTANCE_ROOT": str(one.root),
                                                    "PORTAL_DEPLOYMENT": "release"})

    def test_running_launcher_blocks_duplicate_window_and_uninstall_until_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with launcher.launcher_lock(base):
                with self.assertRaisesRegex(RuntimeError, "已经打开"):
                    with launcher.launcher_lock(base):
                        self.fail("A second owner acquired the launcher lock")
            # The lock file can remain; only the OS lock determines ownership.
            with launcher.launcher_lock(base):
                pass

    def test_long_path_success_restarts_unelevated_after_releasing_launcher_lock(self):
        events = []

        @contextmanager
        def lock(_):
            events.append("locked")
            yield
            events.append("released")

        def spawn(argv, **kwargs):
            self.assertEqual(events, ["locked", "released"])
            self.assertEqual(argv, [sys.executable, str(Path(launcher.__file__))])
            self.assertNotIn("--enable-long-paths", argv)

        with patch.object(launcher, "os", SimpleNamespace(name="nt")), \
                patch.object(sys, "argv", ["launcher"]), \
                patch.object(launcher, "launcher_lock", side_effect=lock), \
                patch.object(launcher, "launch_desktop", return_value=True), \
                patch.object(launcher.subprocess, "CREATE_NO_WINDOW", 0, create=True), \
                patch.object(launcher.subprocess, "Popen", side_effect=spawn) as process:
            launcher.main()
            process.assert_called_once()

    def test_long_path_setup_shows_failures_and_only_restarts_after_success(self):
        for failure in (None, RuntimeError("authorization cancelled")):
            with self.subTest(failure=failure):
                root = Mock()
                root.after.side_effect = lambda delay, callback: setattr(root, "scheduled", callback)
                root.mainloop.side_effect = lambda: root.scheduled()
                messagebox = Mock()
                tk = SimpleNamespace(ttk=Mock(), messagebox=messagebox)
                with patch.dict(sys.modules, {"tkinter": tk}), \
                        patch.object(launcher, "enable_long_paths_elevated", side_effect=failure), \
                        patch.object(launcher.threading, "Thread",
                                     side_effect=lambda target, **kwargs: Mock(start=target)):
                    restart = launcher.wait_for_long_path_setup(root)
                self.assertEqual(restart, failure is None)
                root.deiconify.assert_called_once()
                root.destroy.assert_called_once()
                if failure:
                    messagebox.showerror.assert_called_once()
                else:
                    messagebox.showerror.assert_not_called()

    def test_first_admin_opens_only_invitation_not_full_console_text(self):
        text = ("Setup link for the first administrator:\n"
                "  http://127.0.0.1:28199/join#invite=test-token\n"
                "Open in browser. Next steps: http://127.0.0.1:28199/guide#admin")
        self.assertEqual(launcher.invitation_url(text), "http://127.0.0.1:28199/join#invite=test-token")
        with self.assertRaises(ValueError):
            launcher.invitation_url("no invitation")

    def test_user_service_registration_replaces_only_its_own_component(self):
        """同一组件的旧定义（代码更新后环境不同）被替换；其他实例的定义被拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance, value, name = self.definition(tmp)
            directory = instance.root / "runtime/service-units"
            content = json.dumps(value)
            with code_environment():
                self.assertEqual(services.register(directory, {name: content}), [name])
                self.assertEqual(services.register(directory, {name: content}), [])
                stale = {**value, "python": "C:/TFI/dependencies/old/venv/Scripts/python.exe"}
                (directory / (name + ".json")).write_text(json.dumps(stale))
                self.assertEqual(services.register(directory, {name: content}), [])
                self.assertEqual(json.loads((directory / (name + ".json")).read_text()), value)
                other, foreign, _ = self.definition(tmp, "other")
                (directory / (name + ".json")).write_text(json.dumps(foreign))
                with self.assertRaisesRegex(ValueError, "another instance"):
                    services.register(directory, {name: content})

    def test_stale_definition_is_stoppable_and_removable_but_not_startable(self):
        """代码更新后旧定义仍可停止和删除，但启动要求当前定义。"""
        with tempfile.TemporaryDirectory() as tmp:
            instance, value, name = self.definition(tmp)
            directory = instance.root / "runtime/service-units"
            directory.mkdir(parents=True)
            stale = {**value, "python": "C:/TFI/dependencies/old/venv/Scripts/python.exe"}
            (directory / (name + ".json")).write_text(json.dumps(stale))
            control = services.WindowsServiceControl(directory)
            with code_environment():
                self.assertEqual(control.status(name)["active"], "inactive")
                control.stop(name)
                with self.assertRaisesRegex(ValueError, "does not match"):
                    control.start(name)
                windows.remove_services(directory, [name])
            self.assertFalse((directory / (name + ".json")).exists())

    def test_uninstall_unregisters_owned_definitions_and_preserves_instance_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            platform = base / "program"
            (platform / "code").mkdir(parents=True)
            instance = load(write_instance(base / "instance", code=platform / "code"))
            directory = instance.root / "runtime/service-units"
            with code_environment(), patch("scripts.instance_services.platform_name", return_value="windows"):
                windows.register_services(directory, {instance.service_name("web"):
                                                      windows.manifest_unit_text(instance, "web")})
                unrelated = directory / "other-installation.json"
                unrelated.write_text("leave me")
                with patch("management_common.host_instances.read_registry",
                           return_value={instance.name: str(instance.root)}), \
                        patch.object(launcher.subprocess, "run") as run:
                    launcher.stop_all(platform, unregister=True)
            run.assert_called_once()
            self.assertTrue((instance.root / "instance.json").is_file())
            self.assertFalse((directory / (instance.service_name("web") + ".json")).exists())
            self.assertEqual(unrelated.read_text(), "leave me")

    def test_recycled_pid_never_reports_active_or_gets_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance, value, name = self.definition(tmp)
            directory = instance.root / "runtime/service-units"
            with code_environment():
                services.register(directory, {name: json.dumps(value)})
                services.state_path(value).write_text(json.dumps(
                    {"pid": 15, "created": 10, "child_pid": 16, "child_created": 11}))
                controller = services.WindowsServiceControl(directory)
                with patch.object(services, "process_stamp", return_value=999):
                    self.assertEqual(controller.status(name)["pid"], 0)
                    controller.stop(name)
            self.assertFalse(services.state_path(value).with_suffix(".stop").exists())

    def test_exited_process_with_exit_code_259_is_not_alive(self):
        # STILL_ACTIVE is also a legal application exit code. Only the process
        # handle's signalled state proves whether it has actually terminated.
        api = SimpleNamespace(**{name: Mock() for name in (
            "OpenProcess", "CloseHandle", "WaitForSingleObject",
            "GetProcessTimes", "GetExitCodeProcess")})
        api.OpenProcess.return_value = 123
        api.WaitForSingleObject.return_value = 0  # WAIT_OBJECT_0: exited

        def exit_code(handle, output):
            output._obj.value = 259
            return True

        api.GetExitCodeProcess.side_effect = exit_code
        with patch.object(services.ctypes, "WinDLL", return_value=api, create=True):
            self.assertIsNone(services.process_stamp(45))
        api.CloseHandle.assert_called_once_with(123)

    def test_active_owned_child_and_supervisor_are_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance, value, name = self.definition(tmp)
            directory = instance.root / "runtime/service-units"
            with code_environment():
                services.register(directory, {name: json.dumps(value)})
                services.state_path(value).write_text(json.dumps(
                    {"pid": 15, "created": 10, "child_pid": 16, "child_created": 11}))
                controller = services.WindowsServiceControl(directory)
                with patch.object(services, "process_stamp", side_effect=lambda pid: {15: 10, 16: 11}[pid]):
                    self.assertEqual(controller.status(name)["active"], "active")
                # A living supervisor restarting its child stays visible, never "inactive".
                with patch.object(services, "process_stamp", side_effect=lambda pid: {15: 10, 16: None}[pid]):
                    self.assertEqual(controller.status(name)["active"], "activating")
                    self.assertEqual(controller.status(name)["pid"], 15)

    def test_installer_requires_x64_keeps_instances_outside_and_updates_program_in_place(self):
        script = (REPO / "code/scripts/windows/setup.nsi").read_text()
        self.assertIn("RunningX64", script)
        self.assertIn("RequestExecutionLevel user", script)
        self.assertNotIn('RMDir /r "$INSTDIR"', script)
        self.assertNotIn('$INSTDIR\\instance', script)
        # A code update stops the instances running this program before replacing it.
        update = script.index('IfFileExists "$INSTDIR\\windows-package.json"')
        self.assertLess(script.index("--stop-all"), script.index('File /r /x example-engineering.zip'))
        self.assertGreater(update, 0)

    def test_windows_derived_bundle_does_not_mutate_seed_or_linux_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "instance/example_instance/seed"
            files = {area: "data/" + area for area in ("projects", "features")}
            for folder in ("database", *files.values()):
                (seed / folder).mkdir(parents=True, exist_ok=True)
            (seed / "data/projects/readme.txt").write_text("project\n")
            (seed / "database/distribution.sql").write_text("-- fixture\n")
            (root / "code/scripts").mkdir(parents=True)
            (root / "code/app.py").write_text("pass\n")
            (root / "code/scripts/tfi_release_install.py").write_text("# installer fixture\n")
            (root / "INSTALL.md").write_text("fixture")
            spec = {"schema": "tfi.instance-seed.v1", "engineering": {
                "files": files, "run_id": "fixture", "supervisor_resident_id": "",
                "database": {"path": "database/distribution.sql", "format": "postgresql-sql",
                             "sha256": prepare_install.digest(seed / "database/distribution.sql")},
                "selected_targets": ["linux"], "features": []}, "inventory": prepare_install.inventory(seed)}
            marker = seed / "manifest.json"
            marker.write_text(json.dumps(spec))
            original = marker.read_bytes()
            with patch("management_portal.distribution.core_skills.export_core_skills", return_value={}):
                bundle = prepare_install.prepare(root, target_platforms=["windows"])
            built = json.loads((bundle / "manifest.json").read_text())
            self.assertEqual(built["selected_targets"], ["windows"])
            self.assertEqual(built["support_matrix"][0]["verification"], "not_run")
            self.assertEqual(marker.read_bytes(), original)
            self.assertFalse((root / ".build/example_instance").exists())
            # Exercise the actual consumer, not just the producer's output shape.
            validated, _ = installer.validate_engineering(bundle / "manifest.json")
            self.assertEqual(validated["installer"]["entrypoint"], "code/scripts/tfi_release_install.py")
            validated["installer"] = {"path": validated["installer"]["entrypoint"]}
            (bundle / "manifest.json").write_text(json.dumps(validated))
            with self.assertRaisesRegex(ValueError, "installer.entrypoint"):
                installer.validate_engineering(bundle / "manifest.json", skip_file_verification=True)


class WindowsPreviewPublishing(unittest.TestCase):
    def test_only_matching_verified_installer_is_sent_to_github(self):
        """发布只上传与固定提交和收据一致的安装包，错误版本或损坏文件不得上传。"""
        from scripts.windows import publish
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build = root / '.build/windows'
            build.mkdir(parents=True)
            exe = build / 'TheFourthInstitute-Setup-x64.exe'
            exe.write_bytes(b'test installer')
            receipt = {'source_commit': 'a' * 40, 'source_dirty': False,
                       'prepared_engineering_validation': 'passed',
                       'exe_bytes': exe.stat().st_size,
                       'exe_sha256': hashlib.sha256(exe.read_bytes()).hexdigest()}
            (build / 'receipt.json').write_text(json.dumps(receipt))
            with patch.object(publish, 'REPO', root), \
                    patch.object(sys, 'argv', ['publish.py', '--commit', 'a' * 40]), \
                    patch.object(publish.subprocess, 'run') as upload:
                publish.main()
                self.assertIn('--prerelease', upload.call_args_list[0].args[0])
                self.assertIn(str(exe), upload.call_args_list[0].args[0])
                self.assertIn(str(build / 'receipt.json'), upload.call_args_list[0].args[0])
                self.assertEqual(upload.call_count, 2)
                self.assertEqual(upload.call_args.args[0][-2:], ['--platform', 'windows'])
                self.assertTrue(upload.call_args.args[0][1].endswith('tfi_preview_retention.py'))
                upload.reset_mock()
                for field, value in [('source_commit', 'b' * 40), ('source_dirty', True),
                                     ('prepared_engineering_validation', 'not_run'),
                                     ('exe_sha256', '0' * 64), ('exe_bytes', 0)]:
                    (build / 'receipt.json').write_text(json.dumps({**receipt, field: value}))
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        publish.main()
                    upload.assert_not_called()
                (build / 'receipt.json').write_text(json.dumps(receipt))
                upload.side_effect = subprocess.CalledProcessError(1, ['gh', 'release', 'create'])
                with self.assertRaises(subprocess.CalledProcessError):
                    publish.main()
                self.assertEqual(upload.call_count, 1)


if __name__ == "__main__":
    unittest.main()
