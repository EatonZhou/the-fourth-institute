"""
:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
import pytest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


from management_portal.kimi_code_server import _write_runtime_config
from tests.cases.model_api_test_support import register_apis


class KimiCodeServerTest(unittest.TestCase):
    def test_runtime_config_registers_all_portal_models(self) -> None:
        """kimi 运行配置登记全部 Portal 模型并限制文件权限

        :importance: P2"""
        repo_root = Path(__file__).resolve().parents[2]
        template = repo_root / "systemd" / "portal-kimi-code-config.toml"
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config_path = _write_runtime_config(
                home,
                template,
                {"base_url": "https://api.moonshot.cn/v1"},
                "test-secret",
                "kimi-k3",
            )
            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["default_model"], "kimi-k3")
            self.assertEqual(set(payload["models"]), {
                "kimi-k3",
                "kimi-k2.7-code",
                "kimi-k2.7-code-highspeed",
                "kimi-k2.6",
            })
            self.assertEqual(payload["models"]["kimi-k3"]["max_context_size"], 1048576)
            self.assertEqual(payload["providers"]["portal_kimi"]["api_key"], "test-secret")
            self.assertEqual(os.stat(config_path).st_mode & 0o777, 0o600)

    def test_server_reads_its_profile_from_postgres_and_hides_the_database_from_kimi(self) -> None:
        """Kimi Code 服务从 PostgreSQL 读取登记且不把数据库地址交给 Kimi

        Kimi Code 启动器从 PostgreSQL 读取所配置的模型接口登记并写入运行配置；启动 kimi web 时的环境中没有 PORTAL_DATABASE_URL。
        """
        from management_portal import kimi_code_server

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary, secret = root / "kimi", root / "kimi.key"
            binary.write_text("")
            secret.write_text("test-secret\n")
            register_apis({"id": "kimi_fixture", "kind": "openai_compatible_chat",
                           "base_url": "https://kimi.invalid/v1", "api_key_file": str(secret)})
            with mock.patch.dict(os.environ, {
                "PORTAL_KIMI_CODE_PROVIDER_PROFILE": "kimi_fixture",
                "PORTAL_KIMI_CODE_BINARY": str(binary), "PORTAL_KIMI_CODE_HOME": str(root / "home"),
            }), mock.patch.object(kimi_code_server.kimi_portal_tools, "install"), \
                    mock.patch.object(kimi_code_server.os, "execvpe") as execvpe:
                self.assertTrue(os.environ["PORTAL_DATABASE_URL"])
                kimi_code_server.main()
            payload = tomllib.loads((root / "home" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(payload["providers"]["portal_kimi"]["base_url"], 'https://kimi.invalid/v1')
        self.assertEqual(execvpe.call_args.args[0], str(binary))
        self.assertNotIn("PORTAL_DATABASE_URL", execvpe.call_args.args[2])

    def test_windows_server_runs_npm_shim_through_the_process_owner(self):
        from management_portal import kimi_code_server as server
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, secret = root / 'kimi.cmd', root / 'key'
            binary.write_text('')
            secret.write_text('fixture-key')
            register_apis({'id': 'windows_kimi', 'kind': 'openai_compatible_chat',
                           'base_url': 'https://kimi.invalid/v1', 'api_key_file': str(secret)})
            with mock.patch.dict(os.environ, {'PORTAL_KIMI_CODE_PROVIDER_PROFILE': 'windows_kimi',
                    'PORTAL_KIMI_CODE_BINARY': str(binary), 'PORTAL_KIMI_CODE_HOME': str(root / 'home')}), \
                    mock.patch.object(server, 'sys', SimpleNamespace(platform='win32')), \
                    mock.patch.object(server.kimi_portal_tools, 'install'), \
                    mock.patch.object(server, 'run_process', return_value=SimpleNamespace(returncode=7)) as run, \
                    mock.patch.object(server.os, 'execvpe') as execute:
                assert server.main() == 7
            execute.assert_not_called()
            assert run.call_args.args[0][:2] == [str(binary), 'web']
            assert 'PORTAL_DATABASE_URL' not in run.call_args.kwargs['env']


if __name__ == "__main__":
    unittest.main()
