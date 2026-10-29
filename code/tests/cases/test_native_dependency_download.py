"""原生依赖离线归档、续传和桌面安装反馈。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

from management_common.installation_progress import PREFIX, relay_log_progress
from scripts import tfi_release_native_dependencies as native
from scripts import tfi_windows_launcher as launcher
from scripts.windows.build import bundle_native_dependencies


class Response:
    def __init__(self, chunks, *, status=200, headers=None):
        self.chunks = iter(chunks)
        self.status = status
        self.headers = headers or {}

    def geturl(self):
        return "https://downloads.example.invalid/artifact"

    def read(self, size):
        chunk = next(self.chunks, b"")
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class NativeDownloads(unittest.TestCase):
    def entry(self, content):
        return {"name": "nats", "version": "2.15.1", "url": "https://downloads.example.invalid/artifact",
                "sha256": hashlib.sha256(content).hexdigest()}

    def test_timeout_resumes_saved_bytes_and_verifies_complete_digest(self):
        """读取超时后保留已写字节，自动发送 Range 并校验完整归档。"""
        content = b"abcdefgh"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            responses = [Response([b"abc", TimeoutError("read timeout")], headers={"Content-Length": "8"}),
                         Response([b"defgh"], status=206, headers={"Content-Range": "bytes 3-7/8"})]
            with patch.object(native, "urlopen", side_effect=responses) as opened, \
                    patch.object(native.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(native.download(self.entry(content), target), target)
            self.assertEqual(opened.call_args_list[1].args[0].get_header("Range"), "bytes=3-")
            self.assertEqual(target.read_bytes(), content)
            self.assertFalse(target.with_name("artifact.part").exists())

    def test_retry_in_later_launch_reuses_partial_download(self):
        """重试次数耗尽保留缓存，下次启动继续下载并显示依赖名称。"""
        content = b"abcde"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            with patch.object(native, "urlopen", return_value=Response([b"ab", TimeoutError()])), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "NATS.*已保留.*重试"):
                    native.download(self.entry(content), target, attempts=1)
            self.assertFalse(target.exists())
            self.assertEqual(target.with_name("artifact.part").read_bytes(), b"ab")
            with patch.object(native, "urlopen", return_value=Response(
                    [b"cde"], status=206, headers={"Content-Range": "bytes 2-4/5"})), \
                    contextlib.redirect_stderr(io.StringIO()):
                native.download(self.entry(content), target)
            self.assertEqual(target.read_bytes(), content)

    def test_server_without_range_restarts_without_appending_duplicate_bytes(self):
        """服务器忽略 Range 时从头写入，不能把完整响应拼接到旧文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            target.with_name("artifact.part").write_bytes(b"ab")
            with patch.object(native, "urlopen", return_value=Response([b"abc"])), \
                    contextlib.redirect_stderr(io.StringIO()):
                native.download(self.entry(b"abc"), target)
            self.assertEqual(target.read_bytes(), b"abc")

    def test_packaged_archive_needs_no_network_and_corruption_is_rejected(self):
        """安装包内的正确归档完全离线复用，损坏归档不能绕过摘要校验。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            target.write_bytes(b"abc")
            with patch.object(native, "urlopen", side_effect=AssertionError("Unexpected network")), \
                    contextlib.redirect_stderr(io.StringIO()):
                native.download(self.entry(b"abc"), target)
                target.write_bytes(b"bad")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    native.download(self.entry(b"abc"), target)

    def test_bad_range_and_bad_digest_never_publish_an_archive(self):
        """偏移错误或内容摘要错误都不能生成可安装的归档。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            target.with_name("artifact.part").write_bytes(b"ab")
            with patch.object(native, "urlopen", return_value=Response(
                    [b"c"], status=206, headers={"Content-Range": "bytes 1-1/3"})), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "resume response"):
                    native.download(self.entry(b"abc"), target)
            with patch.object(native, "urlopen", return_value=Response([b"bad"])), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    native.download(self.entry(b"abc"), target)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name("artifact.part").exists())

    def test_short_response_retries_from_received_offset(self):
        """响应声明未传完即结束时，保留内容并续传。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact"
            with patch.object(native, "urlopen", side_effect=[
                    Response([b"ab"], headers={"Content-Length": "3"}),
                    Response([b"c"], status=206, headers={"Content-Range": "bytes 2-2/3"})]), \
                    patch.object(native.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
                native.download(self.entry(b"abc"), target)
            self.assertEqual(target.read_bytes(), b"abc")

    def test_launcher_shows_progress_and_error_instead_of_traceback_tail(self):
        """真实子进程进度显示到窗口，错误卡片只显示原因，堆栈保留在日志。"""
        with tempfile.TemporaryDirectory() as tmp:
            updates = []
            flow = launcher.DefaultStartup(Path(tmp), progress=updates.append)
            progress = PREFIX + json.dumps({"message": "正在解压数据库"}, ensure_ascii=False)
            failure = json.dumps({"status": "failed", "error": "NATS 下载未完成，请重试"}, ensure_ascii=False)
            program = f"print({progress!r}); print('internal_file.py:81 in download'); print({failure!r}); raise SystemExit(1)"
            with self.assertRaisesRegex(RuntimeError, "NATS 下载未完成") as caught:
                flow.execute([sys.executable, "-c", program])
            self.assertEqual(updates, ["正在解压数据库"])
            self.assertNotIn("internal_file.py", str(caught.exception))
            self.assertIn("internal_file.py", flow.log_path.read_text(encoding="utf-8"))

    def test_dependency_log_relays_only_new_progress_and_flushes_final_record(self):
        """日志转发跳过旧记录与普通诊断，子程序退出时仍转发最后的阶段提示。"""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "dependency.log"
            log.write_text(PREFIX + '{"message":"old"}\n', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stderr(output), relay_log_progress(log):
                with log.open("ab") as stream:
                    stream.write(b"ordinary diagnostic with private values\n")
                    stream.write((PREFIX + json.dumps({"message": "正在准备浏览器"}, ensure_ascii=False) + "\n").encode())
            record = json.loads(output.getvalue().removeprefix(PREFIX))
            self.assertEqual(record, {"message": "正在准备浏览器"})

    def test_builder_archives_are_consumed_by_installer_without_network(self):
        """构建器内置的三种归档可由正式安装入口离线解压，重复准备沿用已核验程序。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            packages = {}
            for name, executable in {"postgres": "bin/postgres.exe",
                                     "nats": "nats-server.exe", "node": "node.exe"}.items():
                archive = cache / (name + ".zip")
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr("package/" + executable, b"fixture executable")
                entry = {"version": "1", "url": "https://downloads.example.invalid/" + name + ".zip",
                         "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
                packages[name] = entry
                archive.rename(cache / native.artifact_name(entry))
            catalog = root / "source/code/config/release-native-dependencies.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"packages": {"windows/x86_64": packages}}), encoding="utf-8")
            platform = root / "platform"
            target = {"shared_code_root": str(platform / "code"),
                      "install_root": str(platform / "instance/example_instance")}
            api = SimpleNamespace(
                installation_lock=lambda path: contextlib.nullcontext(),
                write_json=lambda path, value: path.write_text(json.dumps(value), encoding="utf-8"))
            # Windows-only PostgreSQL steps (UTF-8 manifests, C++ runtime) have
            # their own cases; here they only have to run for PostgreSQL.
            from scripts.windows import msvc_runtime, utf8_manifest
            windows_steps = []
            def prepare_postgres(directory):
                windows_steps.append(("utf8_manifest", Path(directory).name))
                return {"utf8_manifest": "fixture"}
            def prepare_crt(api, target, entry):
                windows_steps.append(("msvc_runtime", entry["name"]))
                return {"version": "fixture"}
            with patch.object(native, "urlopen", side_effect=AssertionError("Unexpected network")), \
                    patch.object(native, "verify", return_value={"fixture": "1"}), \
                    patch.object(utf8_manifest, "prepare_postgres", side_effect=prepare_postgres), \
                    patch.object(msvc_runtime, "prepare", side_effect=prepare_crt), \
                    contextlib.redirect_stderr(io.StringIO()):
                bundled = bundle_native_dependencies(root / "source", platform, cache)
                entries = [{key: value for key, value in row.items() if key not in {"path", "size"}}
                           | {"directory": str(platform / "dependencies/native" / row["name"])}
                           for row in bundled]
                installed = native.prepare(api, target, entries)
                self.assertEqual(len(installed), 3)
                self.assertEqual(windows_steps, [("utf8_manifest", "postgres"), ("msvc_runtime", "postgres")])
                self.assertEqual({row["artifact"]["name"]: row.get("windows_compatibility") for row in installed},
                                 {"postgres": {"utf8_manifest": "fixture", "msvc_runtime": {"version": "fixture"}},
                                  "nats": None, "node": None})
                for entry in entries:
                    receipt = Path(entry["directory"]) / "tfi-native-dependency.json"
                    self.assertEqual(json.loads(receipt.read_text())["status"], "verified")
                with patch.object(native, "download", side_effect=AssertionError("Unexpected download")), \
                        patch.object(native, "unpack", side_effect=AssertionError("Unexpected unpack")):
                    self.assertEqual(native.prepare(api, target, entries), installed)


if __name__ == "__main__":
    unittest.main()
