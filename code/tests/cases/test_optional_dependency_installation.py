"""安装先准备核心环境，可选工具缺失只影响对应功能。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import tfi_release_dependencies as dependencies


class OptionalDependencies(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.code = root / "checkout/code"
        (self.code / "scripts").mkdir(parents=True)
        self.environment = root / "checkout/dependencies/fixture/venv"
        self.python = self.environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        self.checks = [{"name": "core: fixture", "ok": True}]
        self.doctor = self.enterContext(patch.object(
            dependencies, "run_utf8_capture",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(self.checks))))
        self.install = self.enterContext(patch.object(
            dependencies, "run_process", return_value=subprocess.CompletedProcess([], 0)))
        self.enterContext(patch.object(dependencies, "environment_directory", return_value=self.environment))

    def prepare(self, **options):
        with contextlib.redirect_stderr(io.StringIO()):
            return dependencies.prepare(self.code, **options)

    def test_optional_profiles_do_not_block_or_trigger_downloads(self):
        """已具备核心环境时不探测或下载 TeX、浏览器与测试工具。"""
        result = self.prepare()
        self.install.assert_not_called()
        doctor = self.doctor.call_args.args[0]
        self.assertEqual(doctor[doctor.index("doctor") + 1:doctor.index("--venv")], ["core"])
        self.assertEqual(result["source"], "verified-existing")
        self.assertEqual(result["python"], str(self.python))

    def test_missing_core_installs_only_core_and_verifies_before_proceeding(self):
        """首次缺少核心包时只安装 core，再通过核心核验，不启动可选工具安装。"""
        self.doctor.side_effect = [
            subprocess.CompletedProcess([], 1, "[]"),
            subprocess.CompletedProcess([], 0, json.dumps(self.checks))]
        result = self.prepare(base_python=sys.executable)
        command = self.install.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[command.index("install") + 1:command.index("--venv")], ["core"])
        self.assertEqual(self.doctor.call_count, 2)
        self.assertEqual(result["source"], "code-dependency-bootstrap")

    def test_core_failure_remains_blocking(self):
        """核心依赖安装失败仍阻止后续服务，不能被当成可选缺失忽略。"""
        self.doctor.return_value = subprocess.CompletedProcess([], 1, "[]")
        self.install.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(RuntimeError, "Dependency preparation failed"):
            self.prepare()

    def test_invalid_core_evidence_is_not_accepted_as_optional(self):
        """核心核验进程虽返回成功，失败或损坏的清单仍必须拒绝。"""
        for output in ('[{"name":"core: missing","ok":false}]', "not JSON", "[]"):
            with self.subTest(output=output):
                self.doctor.return_value = subprocess.CompletedProcess([], 0, output)
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_article_bootstrap_never_installs_tex_implicitly(self):
        """即使请求 article profile，通用 bootstrap 也不下载或运行 TeX 安装器。"""
        source = Path(dependencies.__file__).with_name("bootstrap_portal")
        loader = importlib.machinery.SourceFileLoader("optional_bootstrap_fixture", str(source))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        args = argparse.Namespace(profiles=["article"], venv=Path(self.directory.name) / "venv",
                                  dry_run=True, allow_source_builds=False)
        with patch.object(module, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.install(args), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)  # venv and the core requirements
        self.assertTrue(any("requirements.txt" in str(arg) for arg in commands[-1]))
        self.assertFalse(any("tex" in str(command).lower() for command in commands))

    def test_article_without_tex_can_be_inspected_and_rechecks_after_tools_added(self):
        """无 TeX 时资料预检仍可用、编译明确受阻，工具加入后重新检测即可编译。"""
        from management_portal import article_build_profile, article_toolchain
        root = Path(self.directory.name)
        (root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}Hello\\end{document}", encoding="utf-8")
        with patch.object(article_toolchain, "_tex_which", return_value=None) as tool, \
                patch.object(article_toolchain, "_kpsewhich_file", return_value="/fixture/article.cls"), \
                patch.object(article_toolchain, "latex_environment", return_value={}), \
                patch.object(article_build_profile.repo_backend, "project_ids_for_path", return_value=[]):
            inspect = article_build_profile.inspect_article_build_profile(root, require_toolchain=False)
            self.assertEqual(inspect["status"], "warning")
            blocked = article_build_profile.inspect_article_build_profile(root)
            self.assertEqual(blocked["status"], "blocked")
            self.assertTrue(any("请自行安装" in row["message"] for row in blocked["issues"]))
            tool.return_value = "/fixture/tool"
            self.assertEqual(article_build_profile.inspect_article_build_profile(root)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
