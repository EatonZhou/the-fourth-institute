from __future__ import annotations
from management_portal.society_service.resident_domain_common import LINEAGE_FOLDER_SCHEMA_VERSION

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from management_portal.society_service import lineage_state
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import watchboard_lineage, lineage_program, resident_record_domain
from management_portal.society_service import resident_watchboard_domain
from management_portal.society_service import resident_access


class ResidentWatchboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "lineage"
        self.owned_workspace = (
            self.workspace
            / "residents"
            / "resident_aaaaaaaaaaaa"
            / "workspace"
        )
        self.record = {
            "resident_id": "resident_aaaaaaaaaaaa",
            "folder_schema": LINEAGE_FOLDER_SCHEMA_VERSION,
            "lineage_id": "lineage_000000000001",
            "lineage_membership": "formal",
            "title": "Test Resident",
            "kind": "system",
            "backend": "codex",
            "workspace_root": str(self.root),
            "workspace_path": str(self.workspace),
            "owned_workspace_id": "workspace_aaaaaaaaaaaa",
            "default_cwd": str(self.owned_workspace),
            "owned_workspace": {
                "workspace_id": "workspace_aaaaaaaaaaaa",
                "path": str(self.owned_workspace),
            },
            "workspace_bindings": [],
            "session_state": "active",
            "registration_state": "ready",
            "created_at": "2026-07-17T00:00:00Z",
            "updated_at": "2026-07-17T00:00:00Z",
        }
        self.find_patch = mock.patch.object(
            resident_record_domain,
            "find_resident",
            side_effect=lambda resident_id: (
                self.record
                if resident_id == self.record["resident_id"]
                else None
            ),
        )
        self.graph = {"revision": 1, "program": {"components": [], "wires": []}}
        self.save_patch = mock.patch.object(watchboard_lineage, "definition", side_effect=lambda record: self.graph)
        self.lineages_root_patch = mock.patch.object(
            lineage_state,
            "_lineages_root",
            return_value=self.root / "canonical-lineages",
        )
        self.lineage_access_patch = mock.patch.object(
            lineage_state,
            "lineage_access_projection",
            return_value={
                "lineage_id": self.record["lineage_id"],
                "lineage_node_ids": [self.record["resident_id"]],
                "leader_resident_id": self.record["resident_id"],
            },
        )
        # Cleanups also run when setUp fails, so a patch never leaks into later tests.
        self.addCleanup(self.temp_dir.cleanup)
        for patch in (self.find_patch, self.save_patch, self.lineages_root_patch, self.lineage_access_patch):
            patch.start()
            self.addCleanup(patch.stop)

    def install(self):
        plan = resident_watchboard_domain.installation_plan(self.record)
        from management_portal.society_service.isa.definition import translate_changes
        lineage_program.normalize_graph_mutations(translate_changes(plan["install"])[0])
        for item in plan["files"]:
            prefix = self.record["owned_workspace_id"] + "/file/watchboard/"
            self.assertTrue(item["address"].startswith(prefix))
            path = watchboard_lineage.root(self.record) / item["address"][len(prefix):]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")
        self.graph["program"] = lineage_program.normalize_program({
            "components": [item["value"] for item in plan["install"] if item["kind"] == "component"],
            "wires": [item["value"] for item in plan["install"] if item["kind"] == "wire"],
        }, lineage_nodes=[])
        return plan

    def test_reinstall_preserves_files_and_uses_new_live_command_identities(self):
        first = self.install()
        old_ids = {op["value"]["id"] for op in first["install"]}
        config = watchboard_lineage.root(self.record) / "config/config.yaml"
        config.write_text('{"custom": true}\n', encoding="utf-8")
        self.graph["revision"] += 1
        removal = resident_watchboard_domain.installation_plan(self.record)
        self.assertEqual({op["id"] for op in removal["remove"]}, old_ids)
        self.graph["program"] = {"components": [], "wires": []}
        self.graph["revision"] += 1
        second = self.install()
        new_ids = {op["value"]["id"] for op in second["install"]}
        self.assertTrue(old_ids.isdisjoint(new_ids))
        self.assertEqual(second["files"], [])
        self.assertEqual(json.loads(config.read_text()), {"custom": True})
        # Later unrelated edits must not change the installed button targets.
        self.graph["revision"] += 10
        payload = backend.get_resident_watchboard(self.record["resident_id"], "CASE-A")
        commands = payload["watchboard"]["commands"]
        self.assertTrue(commands)
        for command in commands:
            self.assertIn(command["event_source_id"], new_ids)
            self.assertIn(command["effect_id"], new_ids)

    def test_installation_is_read_only_until_client_applies_files_and_graph(self):
        """生成 Watchboard 安装计划不写文件，客户端写入文件和图组件后才启用"""
        plan = resident_watchboard_domain.installation_plan(self.record)
        self.assertFalse(self.owned_workspace.exists())
        self.assertFalse(resident_watchboard_domain.has_watchboard(self.record))
        self.install()
        payload = backend.get_resident_watchboard(self.record["resident_id"], "CASE-A")
        self.assertTrue(payload["watchboard"]["enabled"])
        self.assertEqual(payload["watchboard"]["execution_model"], "lineage")
        self.assertEqual(resident_watchboard_domain.installation_plan(self.record)["install"], [])
        self.assertEqual(resident_watchboard_domain.installation_plan(self.record)["files"], [])
        self.assertEqual(self.record["backend"], "codex")
        self.assertNotIn("watchboard", self.record)
        for operation in plan["install"]:
            if operation["kind"] == "component" and operation["value"]["kind"] == "effect":
                script = operation["value"]["config"]["script"]
                self.assertIn("RunScript ", script)
                self.assertIn('"prompt":${event.payload.prompt}', script)
                self.assertNotIn("network:=", script)
                self.assertNotIn("memory_mb:=", script)

    def test_wireguard_uses_installed_lineage_commands_and_owned_workspace(self):
        """WireGuard Watchboard 使用自有 Workspace 中的脚本，命令经 Lineage 组件执行"""
        self.record["resident_id"] = "resident_8df311142bc2"
        self.assertFalse(resident_watchboard_domain.has_watchboard(self.record))
        script_root = watchboard_lineage.root(self.record)
        script_root.mkdir(parents=True)
        (script_root / "agent.yaml").write_text(json.dumps({
            "entrypoint": "src/main.py", "config": "config/config.json", "status_path": "state/status.json",
            "commands": {"status": {"timeout_seconds": 30}, "save-metadata": {
                "timeout_seconds": 30, "args": {"label": {"required": True}, "notes": {}}}},
        }))
        (script_root / "watchboard.yaml").write_text("layout: wireguard-device-manager\n")
        plan = self.install()
        board = resident_watchboard_domain._watchboard_for_record(self.record, "CASE-A")
        self.assertEqual(board["execution_model"], "lineage")
        self.assertEqual(board["script_root"], str(script_root))
        self.assertEqual(board["view_manifest"]["payload"]["layout"], "wireguard-device-manager")
        self.assertEqual({item["id"] for item in board["commands"]}, {"status", "save-metadata"})
        self.assertTrue(all(item["event_source_id"] and item["effect_id"] for item in board["commands"]))
        self.assertFalse(any(item["address"].endswith("agent.yaml") for item in plan["files"]))

    def test_telepus_display_only_installation_has_no_script_or_configuration(self):
        """Telepus 只读展示型 Watchboard 只安装视图组件，活跃用户可直接打开"""
        self.record["resident_id"] = "resident_7fdebb465cdf"
        script_root = watchboard_lineage.root(self.record)
        script_root.mkdir(parents=True)
        (script_root / "agent.yaml").write_text(json.dumps({"commands": {}, "configs": []}))
        (script_root / "watchboard.yaml").write_text("title: Telepus\nlayout: generic-three-panel\n")
        plan = self.install()
        self.assertEqual(len(plan["install"]), 1)
        self.assertEqual(plan["install"][0]["value"]["client_key"], "watchboard.resident_7fdebb465cdf.view")
        self.assertFalse((script_root / "src").exists())
        self.assertFalse((script_root / "config").exists())
        # Telepus is a regular Resident: an active User can open its board
        # without an owner credential and can bind their own contact.
        with mock.patch.object(resident_access, "get_portal_access_store", return_value=SimpleNamespace(
            is_active=lambda actor: actor == "alice",
        )):
            board = backend.get_resident_watchboard(self.record["resident_id"], "alice")["watchboard"]
        # No config file to edit; running asks TriggerEvent on its Lineage (all signed-in callers while the switch is off).
        self.assertEqual(board["permissions"], {"can_run": True, "can_edit_config": False})
        self.assertTrue(board["enabled"])
        self.assertEqual(board["execution_model"], "lineage")
        self.assertEqual(board["commands"], [])
        self.assertEqual(board["configs"], [])
        self.assertEqual(board["manifest"]["error"], "")
        self.assertTrue(resident_watchboard_domain.installation_plan(self.record)["remove"])

    def test_effect_preserves_declared_timeout_and_omits_unspecified_timeout(self):
        """Watchboard 命令的 Effect 保留声明的超时，未声明时不设超时"""
        plan = resident_watchboard_domain.installation_plan(self.record)
        manifest = json.loads(next(item["content"] for item in plan["files"] if item["address"].endswith("agent.yaml")))
        manifest.pop("default_timeout_seconds")
        manifest["commands"] = {"long": {"timeout_seconds": 90000}, "unbounded": {}}
        path = self.owned_workspace / "watchboard/agent.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(manifest))
        plan = resident_watchboard_domain.installation_plan(self.record)
        scripts = {item["value"]["title"]: item["value"]["config"]["script"] for item in plan["install"]
                   if item["kind"] == "component" and item["value"]["kind"] == "effect"}
        self.assertIn("timeout_seconds:=90000", scripts["Watchboard · long"])
        self.assertNotIn("timeout_seconds:=", scripts["Watchboard · unbounded"])

    def test_removal_detaches_graph_without_deleting_files(self):
        """移除 Watchboard 只拆除图组件，脚本文件保留"""
        self.install()
        plan = resident_watchboard_domain.installation_plan(self.record)
        self.assertTrue(plan["remove"])
        self.graph["program"] = {"components": [], "wires": []}
        self.assertFalse(resident_watchboard_domain.has_watchboard(self.record))
        self.assertTrue((self.owned_workspace / "watchboard/src/main.py").exists())
        self.assertEqual(resident_watchboard_domain.installation_plan(self.record)["files"], [])

    def test_config_projection_uses_content_hash(self):
        """Watchboard 配置文件的版本号取内容的 sha256，地址是节点自有 Workspace 中的文件地址"""
        import hashlib
        from management_portal.society_service import lineage_access_policy
        self.install()
        owned = (str(self.owned_workspace.resolve()), "workspace_aaaaaaaaaaaa", "")
        with mock.patch.object(lineage_access_policy, "path_resource", return_value=owned):
            payload = backend.get_resident_watchboard(self.record["resident_id"], "CASE-A")
        config = payload["watchboard"]["configs"][0]
        self.assertEqual(config["version"], hashlib.sha256(Path(config["path"]).read_bytes()).hexdigest())
        relative = Path(config["path"]).resolve().relative_to(owned[0]).as_posix()
        self.assertTrue(relative.startswith("watchboard/"))
        self.assertEqual(config["address"], f"workspace_aaaaaaaaaaaa/file/{relative}")

    def test_buttons_ask_the_instructions_they_call(self):
        """运行按钮问 TriggerEvent（Resident 所在 Lineage），每个配置文件问 UpdateWorkspaceFile（它的 Workspace 地址）"""
        from management_portal import permission_system
        from management_portal.society_service import lineage_access_policy
        self.install()
        owned = (str(self.owned_workspace.resolve()), "workspace_aaaaaaaaaaaa", "")
        asked = []

        def allows(actor, instruction, arguments=None):
            asked.append((instruction, dict(arguments or {})))
            return instruction in {"GetResident", "TriggerEvent"}

        with mock.patch.object(lineage_access_policy, "path_resource", return_value=owned), \
                mock.patch.object(permission_system, "allows", side_effect=allows):
            board = backend.get_resident_watchboard(self.record["resident_id"], "CASE-A")["watchboard"]
        self.assertEqual(board["permissions"], {"can_run": True, "can_edit_config": False})
        self.assertIn(("GetResident", {"resident_id": self.record["resident_id"]}), asked)
        self.assertTrue(board["configs"] and not any(config["editable"] for config in board["configs"]))
        self.assertIn(("UpdateWorkspaceFile", {"address": board["configs"][0]["address"]}), asked)
        self.assertIn("TriggerEvent", {name for name, _ in asked})

    def test_the_generated_watchboard_script_runs_on_its_own(self) -> None:
        """生成的 Watchboard 脚本可独立运行 status 与 run-once 并写出状态文件

        :module: workbench.panels"""
        self.install()
        script_root = self.owned_workspace / "watchboard"
        for command, outcome in (("status", "success"), ("run-once", "no_op")):
            completed = subprocess.run(
                [sys.executable, "-I", str(script_root / "src" / "main.py"), command],
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], outcome)
        status = json.loads((script_root / "state" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "idle")
        self.assertTrue(status["last_run_at"])
        self.assertEqual(sorted(path.name for path in (script_root / "state").iterdir()), ["status.json"])

    def test_watchboard_manifest_rejects_retired_shape_aliases(self) -> None:
        """agent.yaml 使用已退役的列表格式 commands 时报错，并在 Watchboard 中显示

        :module: workbench.panels"""
        self.install()
        script_root = self.owned_workspace / "watchboard"
        manifest_path = script_root / "agent.yaml"

        manifest_path.write_text(
            "schema: tfi.resident_watchboard_script.v1\n"
            "main: src/main.py\n"
            "commands:\n"
            "  - id: status\n"
            "    description: retired shape\n",
            encoding="utf-8",
        )
        resident_watchboard_domain._WATCHBOARD_YAML_CACHE.clear()

        manifest, error = resident_watchboard_domain.read_watchboard_agent_manifest(self.record)

        self.assertEqual(manifest, {})
        self.assertEqual(error, "agent.yaml commands must be a YAML mapping")
        payload = backend.get_resident_watchboard(self.record["resident_id"], "CASE-A")
        self.assertEqual(payload["watchboard"]["manifest"]["error"], error)
        self.assertTrue(payload["watchboard"]["installation"]["remove"])

    def test_watchboard_with_a_missing_script_folder_reports_the_error(self) -> None:
        """Watchboard 脚本目录缺失时命令列表为空并报告错误

        :module: workbench.panels"""
        self.install()
        script_root = self.owned_workspace / "watchboard"
        shutil.rmtree(script_root)
        resident_watchboard_domain._WATCHBOARD_YAML_CACHE.clear()

        payload = backend.get_resident_watchboard("resident_aaaaaaaaaaaa", "CASE-A")

        self.assertEqual(payload["watchboard"]["commands"], [])
        self.assertTrue(payload["watchboard"]["manifest"]["error"])

    def test_child_files_are_independent_and_need_its_own_graph(self):
        """子 Resident 继承 Watchboard 文件副本，但需自行安装图组件，命令 ID 与父级不同"""
        self.install()
        child = {**self.record, "resident_id": "resident_bbbbbbbbbbbb",
                 "owned_workspace": {"path": str(self.root / "child")}}
        resident_watchboard_domain.inherit_watchboard(self.record, child)
        self.assertTrue((watchboard_lineage.root(child) / "agent.yaml").exists())
        self.assertFalse(resident_watchboard_domain.has_watchboard(child))
        self.assertNotEqual(watchboard_lineage.command_ids(child, "status"),
                            watchboard_lineage.command_ids(self.record, "status"))

if __name__ == "__main__":
    unittest.main()
