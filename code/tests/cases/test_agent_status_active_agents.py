""":module: operations.runtime"""
from __future__ import annotations
from management_portal import permission_system
from management_portal.society_service import resident_catalog

import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from management_portal import agent_status_backend as status_backend
from management_portal import resident_avatar
from management_portal.society_service import (
    active_agent_projection,
    resident_directory_domain,
    resident_intake_domain,
    resident_protocol,
    resident_usage_projection,
)


class ActiveAgentStatusTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = mock.patch.dict(os.environ, {"PORTAL_RESIDENT_AVATAR_ROOT": directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)


    def test_usage_snapshot_is_independent_of_blocked_agent_status(self) -> None:
        """用量快照独立刷新，不等待阻塞中的 Agent 状态汇总"""
        release_status = threading.Event()
        status_started = threading.Event()

        def blocked_status(*_args, **_kwargs):
            status_started.set()
            release_status.wait(5)
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(directory) / "cache.json"),
                mock.patch.object(status_backend, "_build_agent_status_summary", side_effect=blocked_status),
                mock.patch.object(status_backend, "get_society_service") as lineage,
                mock.patch.object(status_backend.ExecutionService, "account_usage", return_value={"accounts": []}),
            ):
                lineage.return_value.projection.collect_resident_usage.return_value = {
                    "status": "ok", "residents": [], "daily_by_provider_utc": {}, "excluding_cached_input": {},
                }
                try:
                    summary = status_backend.get_agent_status_summary("alice", force_refresh=True)
                    self.assertTrue(status_started.wait(1))
                    self.assertNotIn("token_usage", summary)
                    usage = status_backend.get_token_usage_summary("alice")
                    deadline = time.monotonic() + 3
                    while usage.get("snapshot_version", 0) == 0 and time.monotonic() < deadline:
                        time.sleep(0.01)
                        usage = status_backend.get_token_usage_summary("alice")
                    self.assertGreater(usage["snapshot_version"], 0)
                    self.assertEqual(usage["status"], "ok")
                    self.assertEqual(set(usage["variants"]), {"including_cached_input", "excluding_cached_input"})
                    lineage.return_value.projection.collect_resident_usage.assert_called_once_with(actor="alice")
                    self.assertTrue(status_backend._refresh_in_progress(status_backend._agent_status_summary_cache_key("alice")))
                finally:
                    release_status.set()
                    deadline = time.monotonic() + 3
                    while status_backend._refresh_in_progress(status_backend._agent_status_summary_cache_key("alice")) and time.monotonic() < deadline:
                        time.sleep(0.01)

    def test_usage_snapshot_version_failure_and_manual_cooldown(self) -> None:
        """用量快照每次刷新版本加一，手动刷新有冷却，刷新失败保留上一版本，各用户分开缓存"""
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(directory) / "cache.json"),
                mock.patch.object(status_backend, "_build_token_usage_summary", return_value={"status": "ok", "windows": {"today": {"total_tokens": 50}}}) as build,
                mock.patch.object(status_backend, "_now_ts", return_value=2000),
                mock.patch.object(status_backend, "_schedule_background_refresh") as schedule,
            ):
                first = status_backend._refresh_token_usage_summary("alice", force_refresh=False)
                second = status_backend._refresh_token_usage_summary("alice", force_refresh=True)
                self.assertEqual(second["snapshot_version"], first["snapshot_version"] + 1)
                throttled = status_backend.get_token_usage_summary("alice", force_refresh=True)
                self.assertTrue(throttled["manual_refresh_skipped"])
                schedule.assert_not_called()
                build.side_effect = RuntimeError("projection unavailable")
                failed = status_backend._refresh_token_usage_summary("alice", force_refresh=False)
                self.assertEqual(failed["snapshot_version"], second["snapshot_version"])
                self.assertEqual(failed["windows"], second["windows"])
                self.assertIn("projection unavailable", failed["refresh_error"])
                other_actor = status_backend.get_token_usage_summary("bob")
                self.assertEqual(other_actor["snapshot_version"], 0)
                self.assertNotIn("windows", other_actor)
                self.assertEqual(other_actor["actor"], "bob")
                build.side_effect = None
                schedule.side_effect = lambda _key, refresh: refresh()
                completed = status_backend.get_token_usage_summary("bob")
                self.assertGreater(completed["snapshot_version"], 0)
                self.assertFalse(completed["refreshing"])
                refreshed = status_backend.get_token_usage_summary("bob", force_refresh=True)
                self.assertGreater(refreshed["snapshot_version"], completed["snapshot_version"])
                self.assertFalse(refreshed["refreshing"])

    def test_one_unreadable_account_does_not_freeze_usage(self) -> None:
        """单个账号原生用量读取失败时其余数据照常刷新并给出警告，该账号提前重试"""
        # codex_zero's login expired: its native usage is stale, the ledger and
        # the other accounts are current.  The summary used to keep the
        # previous snapshot until every account was readable again, so one
        # account froze Home's usage for everyone.
        complete = {
            "status": "ok", "updated_at": "2026-09-29T12:00:00Z",
            "windows": {"month": {"total_tokens": 37_000_000_000}},
        }
        partial = {
            "status": "partial", "updated_at": "2026-09-29T12:05:00Z",
            "windows": {"month": {"total_tokens": 37_200_000_000}},
            "incomplete_accounts": [{"provider": "codex", "account_id": "codex_zero", "status": "stale",
                                     "error_code": "CodexTurnAuthenticationError", "observed_at": 0}],
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(directory) / "cache.json"),
            mock.patch.object(status_backend, "_now_ts", return_value=2000),
            mock.patch.object(status_backend, "_build_token_usage_summary", return_value=complete) as build,
        ):
            first = status_backend._refresh_token_usage_summary("alice", force_refresh=False)
            build.return_value = partial
            refreshed = status_backend._refresh_token_usage_summary("alice", force_refresh=False)
            self.assertEqual(refreshed["windows"]["month"]["total_tokens"], 37_200_000_000)
            self.assertGreater(refreshed["snapshot_version"], first["snapshot_version"])
            self.assertNotIn("refresh_error", refreshed)
            self.assertEqual(refreshed["warning"], "部分账号的原生用量未更新：codex_zero（stale，CodexTurnAuthenticationError）；其余数据已刷新")
            # The unreadable account is retried sooner than a complete summary.
            entry = status_backend.token_usage_snapshot_cache.read(status_backend.STATUS_CACHE_PATH, status_backend._token_usage_cache_key("alice"))
            self.assertEqual(entry["next_refresh_at"], 2060)
            build.return_value = {"status": "ok", "windows": {"month": {"total_tokens": 100}}}
            recovered = status_backend._refresh_token_usage_summary("alice", force_refresh=False)
            self.assertEqual(recovered["windows"]["month"]["total_tokens"], 100)
            self.assertNotIn("warning", recovered)
            # Only a summary that cannot be built at all keeps the previous one.
            build.side_effect = RuntimeError("projection unavailable")
            failed = status_backend._refresh_token_usage_summary("alice", force_refresh=False)
            self.assertEqual(failed["windows"], recovered["windows"])
            self.assertEqual(failed["snapshot_version"], recovered["snapshot_version"])
            self.assertIn("projection unavailable", failed["refresh_error"])

    def test_usage_snapshot_read_does_not_wait_for_shared_status_cache(self) -> None:
        """读取用量快照不等待共享状态缓存的锁"""
        locked = threading.Event()
        release = threading.Event()

        def hold_status_cache():
            with status_backend._CACHE_LOCK:
                locked.set()
                release.wait(5)

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            status_backend, "STATUS_CACHE_PATH", Path(directory) / "status.json",
        ), mock.patch.object(status_backend, "_build_token_usage_summary", return_value={
            "status": "ok", "windows": {"today": {"total_tokens": 321}},
        }):
            status_backend._refresh_token_usage_summary("alice", force_refresh=False)
            thread = threading.Thread(target=hold_status_cache)
            thread.start()
            self.assertTrue(locked.wait(1))
            try:
                with mock.patch.object(status_backend, "_read_status_cache", side_effect=AssertionError("global cache read")):
                    started = time.monotonic()
                    result = status_backend.get_token_usage_summary("alice")
                    self.assertLess(time.monotonic() - started, 0.5)
                    self.assertEqual(result["windows"]["today"]["total_tokens"], 321)
            finally:
                release.set()
                thread.join(1)

    def test_usage_snapshot_serves_cached_data_while_refresh_is_blocked(self) -> None:
        """刷新阻塞期间重复请求返回缓存的用量快照，只发起一次刷新"""
        started = threading.Event()
        release = threading.Event()

        def build(_actor):
            started.set()
            release.wait(5)
            raise RuntimeError("upstream unavailable")

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            status_backend, "STATUS_CACHE_PATH", Path(directory) / "status.json",
        ), mock.patch.object(status_backend, "_build_token_usage_summary", side_effect=build) as builder:
            key = status_backend._token_usage_cache_key("alice")
            status_backend.token_usage_snapshot_cache.write(status_backend.STATUS_CACHE_PATH, key, {
                "payload": {"snapshot_version": 12, "status": "ok", "windows": {"today": {"total_tokens": 321}}},
            })
            try:
                status_backend.get_token_usage_summary("alice")
                self.assertTrue(started.wait(1))
                for _ in range(3):
                    cached = status_backend.get_token_usage_summary("alice", force_refresh=True)
                    self.assertEqual(cached["windows"]["today"]["total_tokens"], 321)
                    self.assertEqual(cached["snapshot_version"], 12)
                    self.assertTrue(cached["refreshing"])
                builder.assert_called_once_with("alice")
            finally:
                release.set()
                deadline = time.monotonic() + 3
                while status_backend._refresh_in_progress(key) and time.monotonic() < deadline:
                    time.sleep(0.01)
            cached = status_backend.get_token_usage_summary("alice")
            self.assertEqual(cached["snapshot_version"], 12)
            self.assertIn("upstream unavailable", cached["refresh_error"])

    def test_run_projects_canonical_resident_identity_and_lineage(self) -> None:
        """活动运行项带上 Resident 的规范 ID、标题和所属 Lineage"""
        run = active_agent_projection._run(
            {
                "resident_id": "resident_exact",
                "title": "Exact Resident",
                "lineage_id": "lineage_123456789abc",
            },
            status="running",
            account_id="zero",
        )

        self.assertEqual(run["resident_id"], "resident_exact")
        self.assertEqual(run["lineage_id"], "lineage_123456789abc")
        self.assertEqual(run["title"], "Exact Resident")

    def test_deferred_queue_projects_one_queue_run_without_blank_active_run(self) -> None:
        """只有排队消息的 Resident 投影为一条排队项，不出现空白运行项"""
        record = {
            "resident_id": "resident_000000000099",
            "lineage_id": "lineage_123456789abc",
            "title": "Deferred Resident",
        }
        with mock.patch.object(
            active_agent_projection,
            "_resident_account_id",
            return_value="zero",
        ):
            runs = active_agent_projection._resident_runs(record, {
                "status": "queued",
                "queued_messages": [{
                    "message_id": "message_deferred",
                    "author_id": "CASE-A",
                }],
            })

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "queued")
        self.assertTrue(
            runs[0]["_sprite_binding_key"].endswith(":message_deferred")
        )

    def test_running_resident_projects_each_queued_message(self) -> None:
        """运行中的 Resident 为当前消息和每条排队消息各投影一项"""
        record = {
            "resident_id": "resident_000000000099",
            "lineage_id": "lineage_123456789abc",
            "title": "Busy Resident",
        }
        with mock.patch.object(
            active_agent_projection, "_resident_account_id", return_value="zero",
        ):
            runs = active_agent_projection._resident_runs(record, {
                "status": "running",
                "active_message": {"message_id": "message_active"},
                "queued_messages": [
                    {"message_id": "message_waiting_1"},
                    {"message_id": "message_waiting_2"},
                ],
            })
        self.assertEqual([run["status"] for run in runs], ["running", "queued", "queued"])
        self.assertEqual(
            [run["_sprite_binding_key"].split(":")[-1] for run in runs],
            ["message_active", "message_waiting_1", "message_waiting_2"],
        )

    def test_starting_session_projects_active_run_not_queue(self) -> None:
        """正在启动会话的 Resident 当前消息显示为运行中，其余消息排队"""
        record = {
            "resident_id": "resident_000000000099",
            "lineage_id": "lineage_123456789abc",
            "title": "Starting Resident",
        }
        with mock.patch.object(
            active_agent_projection, "_resident_account_id", return_value="zero",
        ):
            runs = active_agent_projection._resident_runs(record, {
                "status": "queued",
                "work_phase": "starting_session",
                "active_message": {"message_id": "message_active"},
                "queued_messages": [{"message_id": "message_waiting"}],
            })
        self.assertEqual([run["status"] for run in runs], ["running", "queued"])

    def test_waiting_capacity_still_projects_queue(self) -> None:
        """等待容量的 Resident 当前消息仍显示为排队"""
        record = {
            "resident_id": "resident_000000000099",
            "lineage_id": "lineage_123456789abc",
            "title": "Waiting Resident",
        }
        with mock.patch.object(
            active_agent_projection, "_resident_account_id", return_value="zero",
        ):
            runs = active_agent_projection._resident_runs(record, {
                "status": "queued",
                "work_phase": "waiting_capacity",
                "active_message": {"message_id": "message_active"},
            })
        self.assertEqual([run["status"] for run in runs], ["queued"])

    def test_resident_avatar_is_independent_of_message_author_and_order(self) -> None:
        """Resident 头像按 Resident 只抽取一次，与消息作者和顺序无关"""
        resident_id = "resident_000000000045"
        first = active_agent_projection._run(
            {"resident_id": resident_id}, status="running", account_id="zero",
            message={"message_id": "first", "author_id": "sample_peer"},
        )
        second = active_agent_projection._run(
            {"resident_id": resident_id}, status="queued", account_id="one",
            message={"message_id": "second", "author_id": "CASE-A"},
        )
        self.assertNotIn("_sprite_actor", first)
        with mock.patch.object(resident_avatar, "_draw_sprite", return_value=37) as draw:
            a = resident_avatar.get_avatar(first["resident_id"])
            b = resident_avatar.get_avatar(second["resident_id"])
        self.assertEqual(a, b)
        self.assertEqual(a, {"kind": "sprite", "sprite_index": 37, "sprite_rarity": "SSSR"})
        draw.assert_called_once()

    def test_resident_avatar_rarity_pools_include_sssr(self) -> None:
        """Resident 头像抽取包含 SSSR 稀有度，并按概率划分 R、SR、SSR"""
        with mock.patch.object(resident_avatar.secrets, "randbelow", return_value=0):
            sprite = resident_avatar._draw_sprite()
        self.assertEqual(resident_avatar.SPRITE_RARITIES[sprite], "SSSR")
        for roll, expected in [(0, "R"), (74, "R"), (75, "SR"), (94, "SR"), (95, "SSR"), (99, "SSR")]:
            with mock.patch.object(resident_avatar.secrets, "randbelow", side_effect=[1, roll]):
                self.assertEqual(resident_avatar.SPRITE_RARITIES[resident_avatar._draw_sprite()], expected)

    def test_parallel_status_refreshes_merge_atomic_cache_updates(self) -> None:
        """并行刷新状态缓存时各条更新原子合并，互不丢失"""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "status_cache.json"
            with mock.patch.object(status_backend, "STATUS_CACHE_PATH", cache_path):
                def store(index: int) -> dict:
                    return status_backend._store_payload(
                        f"disk_status:fixture{index}",
                        {"status": "ok", "value": index},
                        {},
                        force=False,
                        refresh_after_seconds=60,
                    )

                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(store, range(20)))

            persisted = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual([item["value"] for item in results], list(range(20)))
        self.assertEqual(set(persisted), {f"disk_status:fixture{index}" for index in range(20)})

    def test_resident_activity_uses_the_shared_batch_projection(self) -> None:
        """读取多个 Resident 的活动时批量读取投影，不逐个查询"""
        resident_ids = ["resident_bbbbbbbbbbbb", "resident_aaaaaaaaaaaa"]
        repository = mock.Mock()
        repository.current_projections.return_value = {
            resident_id: {
                "state": {"work_state": "idle"},
                "queue": {"resident_id": resident_id, "items": []},
            }
            for resident_id in resident_ids
        }
        repository.current_projection.side_effect = AssertionError(
            "Resident activity must not issue per-Resident projection reads"
        )

        with mock.patch.object(
            resident_protocol.resident_conversation_stream,
            "get_repository",
            return_value=repository,
        ):
            result = resident_protocol.read_resident_activity(resident_ids)

        repository.current_projections.assert_called_once_with(sorted(resident_ids))
        repository.current_projection.assert_not_called()
        self.assertEqual(set(result), set(resident_ids))

    def test_resident_activity_preserves_starting_session_phase(self) -> None:
        """Resident 活动保留 starting_session 工作阶段"""
        resident_id = "resident_aaaaaaaaaaaa"
        repository = mock.Mock()
        repository.current_projections.return_value = {
            resident_id: {
                "state": {
                    "work_state": "queued",
                    "work_phase": "starting_session",
                    "active_message_id": "message_active",
                },
                "queue": {"items": []},
            }
        }
        repository.public_message.return_value = {"message_id": "message_active"}
        with mock.patch.object(
            resident_protocol.resident_conversation_stream,
            "get_repository",
            return_value=repository,
        ):
            activity = resident_protocol.read_resident_activity([resident_id])[resident_id]

        self.assertEqual(activity["status"], "queued")
        self.assertEqual(activity["work_phase"], "starting_session")

    def test_background_disk_refresh_skips_large_file_triage(self) -> None:
        """后台磁盘刷新跳过大文件排查"""
        resource = {"resource_id": "disk_test", "kind": "local", "paths": ["/"]}
        scheduled: list = []
        with (
            mock.patch.object(
                status_backend,
                "_cached_payload_state",
                return_value=(None, True),
            ),
            mock.patch.object(
                status_backend,
                "_schedule_background_refresh",
                side_effect=lambda _key, refresh: scheduled.append(refresh),
            ),
            mock.patch.object(
                status_backend,
                "_refresh_disk_status",
            ) as refresh_disk,
            mock.patch.object(
                status_backend,
                "_read_status_cache",
                return_value={},
            ),
            mock.patch.object(
                status_backend,
                "_store_payload",
                side_effect=lambda _key, payload, *_args, **_kwargs: payload,
            ),
        ):
            placeholder = status_backend._collect_disk_status(resource)
            self.assertEqual(placeholder["status"], "loading")
            self.assertEqual(len(scheduled), 1)
            scheduled[0]()

        self.assertTrue(placeholder["refreshing"])
        self.assertEqual(placeholder["stages"]["large_files"]["status"], "skipped")
        refresh_disk.assert_called_once_with(resource, placeholder, include_triage=False)

    def test_explicit_disk_refresh_includes_large_file_triage(self) -> None:
        """手动刷新磁盘状态时包含大文件排查"""
        resource = {"resource_id": "disk_test", "kind": "local", "paths": ["/"]}
        scheduled = []
        with (
            mock.patch.object(status_backend, "DISK_RESOURCE_POOL", (resource,)),
            mock.patch.object(
                status_backend,
                "_refresh_disk_status",
            ) as refresh_disk,
            mock.patch.object(status_backend, "_schedule_background_refresh", side_effect=lambda _key, refresh: scheduled.append(refresh)),
            mock.patch.object(status_backend, "_read_status_cache", return_value={}),
            mock.patch.object(
                status_backend,
                "_store_payload",
                side_effect=lambda _key, payload, *_args, **_kwargs: payload,
            ),
        ):
            snapshot = status_backend.get_disk_resource_status(force_refresh=True)
            refresh_disk.assert_not_called()
            scheduled[0]()

        self.assertTrue(snapshot["refreshing"])
        self.assertEqual(snapshot["resources"][0]["stages"]["large_files"]["status"], "pending")
        refresh_disk.assert_called_once_with(resource, snapshot["resources"][0], include_triage=True)

    def test_disk_publishes_capacity_and_each_diagnostic_without_waiting(self) -> None:
        """磁盘状态先发布容量再逐项发布诊断，单项诊断失败不阻塞其他项"""
        resource = {"resource_id": "disk_progress", "kind": "local", "paths": ["/"]}
        filesystem = {"filesystem": "/dev/test", "mount": "/", "used_percent": 93, "status": "critical"}
        targets_started = threading.Event()
        targets_release = threading.Event()
        scan_started = threading.Event()
        scan_release = threading.Event()
        key = status_backend._disk_status_cache_key(resource)

        def targets(_resource):
            targets_started.set()
            if not targets_release.wait(5):
                raise TimeoutError("test targets were not released")
            return [], ""

        def scan(*_args):
            scan_started.set()
            if not scan_release.wait(5):
                raise TimeoutError("test scan was not released")
            return {"kind": "large_files", "title": "Large file candidate"}, ""

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(temporary) / "status.json"),
            mock.patch.object(status_backend, "DISK_RESOURCE_POOL", (resource,)),
            mock.patch.object(status_backend, "_collect_df_rows", return_value=([filesystem], "")) as collect_capacity,
            mock.patch.object(status_backend, "_collect_transfer_targets", side_effect=targets),
            mock.patch.object(status_backend, "_disk_triage_deleted_open", return_value=({"kind": "deleted_open_files", "title": "Deleted file"}, "")),
            mock.patch.object(status_backend, "_disk_triage_journal", side_effect=RuntimeError("journal timed out")),
            mock.patch.object(status_backend, "_disk_triage_docker", return_value=(None, "")),
            mock.patch.object(status_backend, "_disk_triage_large_files", side_effect=scan),
        ):
            try:
                initial = status_backend.get_disk_resource_status(force_refresh=True)
                self.assertTrue(initial["refreshing"])
                self.assertTrue(targets_started.wait(2))
                capacity = status_backend.get_disk_resource_status()
                self.assertEqual(capacity["status"], "critical")
                self.assertEqual(capacity["filesystems"][0]["filesystem"], "/dev/test")
                self.assertEqual(capacity["resources"][0]["stages"]["transfer_targets"]["status"], "running")
                status_backend.get_disk_resource_status(force_refresh=True)
                collect_capacity.assert_called_once()
                self.assertEqual(initial["resources"][0]["stages"]["capacity"]["status"], "pending")
                targets_release.set()
                self.assertTrue(scan_started.wait(2))
                diagnostic = status_backend.get_disk_resource_status()["resources"][0]
                self.assertEqual(diagnostic["stages"]["journal_usage"]["status"], "error")
                self.assertEqual(diagnostic["stages"]["large_files"]["status"], "running")
                self.assertEqual(diagnostic["triage"]["findings"][0]["kind"], "deleted_open_files")
                self.assertEqual(diagnostic["status"], "critical")
            finally:
                targets_release.set()
                scan_release.set()
                deadline = time.monotonic() + 3
                while status_backend._refresh_in_progress(key) and time.monotonic() < deadline:
                    threading.Event().wait(0.01)
            self.assertFalse(status_backend._refresh_in_progress(key))
            completed = status_backend.get_disk_resource_status()
            self.assertFalse(completed["refreshing"])
            self.assertEqual(completed["resources"][0]["stages"]["suggestions"]["status"], "ok")
            self.assertEqual(len(completed["triage"]["findings"]), 2)
            self.assertEqual(completed["resources"][0]["triage"]["errors"], ["journal timed out"])

    def test_disk_capacity_failure_preserves_old_snapshot_and_finishes_stages(self) -> None:
        """磁盘容量读取失败时保留旧快照并标为过期，其余阶段结束而不挂起"""
        resource = {"resource_id": "disk_failure", "kind": "local", "paths": ["/"]}
        scheduled = []
        for cached in (False, True):
            with (
                self.subTest(cached=cached),
                tempfile.TemporaryDirectory() as temporary,
                mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(temporary) / "status.json"),
                mock.patch.object(status_backend, "_schedule_background_refresh", side_effect=lambda _key, refresh: scheduled.append(refresh)),
                mock.patch.object(status_backend, "_collect_df_rows", return_value=([], "df timed out")),
                mock.patch.object(status_backend, "_collect_transfer_targets") as targets,
            ):
                key = status_backend._disk_status_cache_key(resource)
                if cached:
                    status_backend._store_payload(key, {"status": "warn", "updated_at": "2026-09-01T00:00:00Z", "filesystems": [{"mount": "/", "status": "warn"}]}, {}, force=False)
                status_backend._collect_disk_status(resource, force=True, include_triage=True)
                scheduled.pop()()
                failed = status_backend._collect_disk_status(resource)
                self.assertFalse(failed["refreshing"])
                self.assertEqual(failed["stages"]["capacity"]["error"], "df timed out")
                self.assertEqual(failed["stages"]["large_files"]["status"], "skipped")
                self.assertEqual(failed["status"], "warn" if cached else "error")
                if cached:
                    self.assertTrue(failed["stale"])
                    self.assertEqual(failed["updated_at"], "2026-09-01T00:00:00Z")
                targets.assert_not_called()

    def test_disk_target_failure_does_not_claim_no_external_disk(self) -> None:
        """外接磁盘探测失败时标为错误，不显示为没有外接磁盘，也不给出建议"""
        resource = {"resource_id": "disk_targets", "kind": "ssh", "paths": ["/"]}
        scheduled = []
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(temporary) / "status.json"),
            mock.patch.object(status_backend, "_schedule_background_refresh", side_effect=lambda _key, refresh: scheduled.append(refresh)),
            mock.patch.object(status_backend, "_collect_df_rows", return_value=([{"mount": "/", "status": "warn"}], "one path unavailable")),
            mock.patch.object(status_backend, "_collect_transfer_targets", return_value=([], "lsblk timed out")),
            mock.patch.object(status_backend, "_disk_triage_deleted_open", return_value=(None, "")),
            mock.patch.object(status_backend, "_disk_triage_journal", return_value=(None, "")),
            mock.patch.object(status_backend, "_disk_triage_docker", return_value=(None, "")),
            mock.patch.object(status_backend, "_disk_triage_large_files") as scan,
        ):
            status_backend._collect_disk_status(resource, force=True, include_triage=True)
            scheduled.pop()()
            completed = status_backend._collect_disk_status(resource)
            self.assertEqual(completed["status"], "warn")
            self.assertEqual(completed["stages"]["capacity"]["status"], "partial")
            self.assertEqual(completed["stages"]["transfer_targets"]["status"], "error")
            self.assertEqual(completed["stages"]["suggestions"]["status"], "skipped")
            self.assertEqual(completed["suggestions"], [])
            scan.assert_not_called()

    def test_disk_slow_target_does_not_block_other_capacity(self) -> None:
        """响应慢的磁盘目标不阻塞其他目标的容量显示"""
        slow = {"resource_id": "disk_slow", "kind": "local", "paths": ["/"]}
        fast = {"resource_id": "disk_fast", "kind": "local", "paths": ["/"]}
        slow_started = threading.Event()
        slow_release = threading.Event()
        fast_ready = threading.Event()

        def capacity(resource, _paths):
            if resource == slow:
                slow_started.set()
                if not slow_release.wait(5):
                    raise TimeoutError("test slow target was not released")
                return [], "slow target unavailable"
            return [{"mount": "/", "status": "ok"}], ""

        def targets(resource):
            if resource == fast:
                fast_ready.set()
            return [], ""

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(temporary) / "status.json"),
            mock.patch.object(status_backend, "DISK_RESOURCE_POOL", (slow, fast)),
            mock.patch.object(status_backend, "_collect_df_rows", side_effect=capacity),
            mock.patch.object(status_backend, "_collect_transfer_targets", side_effect=targets),
        ):
            try:
                status_backend.get_disk_resource_status()
                self.assertTrue(slow_started.wait(2))
                self.assertTrue(fast_ready.wait(2))
                partial = status_backend.get_disk_resource_status()
                self.assertTrue(partial["refreshing"])
                self.assertEqual(partial["resources"][0]["stages"]["capacity"]["status"], "running")
                self.assertEqual(partial["resources"][1]["stages"]["capacity"]["status"], "ok")
                self.assertEqual(partial["filesystems"][0]["resource_id"], "disk_fast")
            finally:
                slow_release.set()
                deadline = time.monotonic() + 3
                while any(status_backend._refresh_in_progress(status_backend._disk_status_cache_key(resource)) for resource in (slow, fast)) and time.monotonic() < deadline:
                    threading.Event().wait(0.01)
            completed = status_backend.get_disk_resource_status()
            self.assertFalse(completed["refreshing"])
            self.assertEqual(completed["status"], "partial")

    def test_disk_interrupted_refresh_restarts_without_stuck_pending(self) -> None:
        """中断的磁盘刷新重新开始，不会一直停在刷新中"""
        resource = {"resource_id": "disk_interrupted", "kind": "local", "paths": ["/"]}
        scheduled = []
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(status_backend, "STATUS_CACHE_PATH", Path(temporary) / "status.json"),
            mock.patch.object(status_backend, "_schedule_background_refresh", side_effect=lambda _key, refresh: scheduled.append(refresh)),
            mock.patch.object(status_backend, "_collect_df_rows", return_value=([{"mount": "/", "status": "ok"}], "")),
            mock.patch.object(status_backend, "_collect_transfer_targets", return_value=([], "")),
        ):
            status_backend._store_payload(status_backend._disk_status_cache_key(resource), {"status": "loading", "refreshing": True}, {}, force=False)
            status_backend._collect_disk_status(resource)
            self.assertEqual(len(scheduled), 1)
            scheduled.pop()()
            completed = status_backend._collect_disk_status(resource)
            self.assertFalse(completed["refreshing"])
            self.assertEqual(completed["status"], "ok")
            self.assertEqual(completed["stages"]["large_files"]["status"], "skipped")


    def test_running_and_portal_queue_are_counted_separately(self) -> None:
        """Home 活动分别统计运行中与 Portal 排队数，并按账号分组"""
        record = {"resident_id": "resident_000000000001", "title": "Resident Test"}
        residents = [
            record,
            {
                "resident_id": "resident_000000000002",
                "label": "Starting",
                "state": "running",
            },
            {
                "resident_id": "resident_000000000003",
                "label": "Live",
                "state": "running",
            },
        ]
        projections = {
            "resident_000000000001": {
                "state": {
                    "work_state": "queued",
                    "active_message_id": "message_main",
                },
                "queue": {"items": [{"message_id": "message_main_followup"}]},
            },
            "resident_000000000002": {
                "state": {"work_state": "idle", "active_message_id": ""},
                "queue": {"items": []},
            },
            "resident_000000000003": {
                "state": {
                    "work_state": "running",
                    "active_message_id": "message_live",
                },
                "queue": {"items": [{"message_id": "message_fork_followup"}]},
            },
        }
        catalog = mock.Mock()
        catalog.get_identity_records.return_value = residents
        repository = mock.Mock()
        repository.current_projection.side_effect = lambda resident_id: projections[resident_id]
        repository.public_message.side_effect = lambda _resident_id, message_id: {
            "message_id": message_id,
            "author_id": "sample_admin",
        }
        activities = {
            resident_id: {
                "status": projection["state"]["work_state"],
                "message_id": projection["state"]["active_message_id"],
                "active_message": {
                    "message_id": projection["state"]["active_message_id"],
                    "author_id": "sample_admin",
                },
                "queued_messages": projection["queue"]["items"],
            }
            for resident_id, projection in projections.items()
        }

        with (
            mock.patch.object(resident_catalog, "get_catalog", return_value=catalog),
            mock.patch.object(
                active_agent_projection.resident_protocol,
                "read_resident_activity",
                side_effect=lambda resident_ids: {
                    resident_id: activities[resident_id]
                    for resident_id in resident_ids
                },
            ),
            mock.patch.object(
                active_agent_projection,
                "_resident_account_id",
                return_value="zero",
            ),
            mock.patch.object(
                active_agent_projection,
                "_capacity_snapshot",
                return_value=(4, {"zero": 4}),
            ),
            mock.patch.object(
                permission_system,
                "readable_ids",
                side_effect=lambda instruction, parameter, resource_ids, **_kwargs: set(resource_ids),
            ),
        ):
            payload = active_agent_projection.collect(actor="CASE-A")

        self.assertEqual(payload["running"], 1)
        self.assertEqual(payload["queued"], 3)
        self.assertEqual(payload["queued_display_count"], 2)
        self.assertEqual(len(payload["running_runs"]), 1)
        self.assertEqual(
            payload["running_runs"][0]["resident_id"],
            "resident_000000000003",
        )
        self.assertTrue(all(item["status"] == "queued" for item in payload["queued_runs"]))
        self.assertEqual(
            [item["resident_id"] for item in payload["queued_runs"]],
            ["resident_000000000001", "resident_000000000001"],
        )
        self.assertTrue(
            all(
                "view" not in item
                and "fork_id" not in item
                and "_sprite_actor" not in item
                for item in payload["running_runs"] + payload["queued_runs"]
            )
        )
        self.assertEqual(payload["max_concurrency"], 4)
        self.assertEqual(
            payload["account_groups"],
            [
                {
                    "account_id": "zero",
                    "running": 1,
                    "queued": 3,
                    "queued_display_count": 2,
                    "running_runs": payload["running_runs"],
                    "queued_runs": payload["queued_runs"],
                    "max_concurrency": 4,
                }
            ],
        )

    def test_active_view_anonymizes_unreadable_residents_without_hiding_activity(
        self,
    ) -> None:
        """活动视图把无权读取的 Resident 匿名显示为 Agent，仍计入运行数且不泄露身份"""
        visible_id = "resident_000000000031"
        private_id = "resident_000000000032"
        catalog = mock.Mock()
        catalog.get_identity_records.return_value = [
            {
                "resident_id": visible_id,
                "lineage_id": "lineage_000000000031",
                "title": "Visible Resident",
            },
            {
                "resident_id": private_id,
                "lineage_id": "lineage_000000000032",
                "title": "Private Resident",
            },
        ]
        activities = {
            visible_id: {
                "status": "running",
                "active_message": {
                    "message_id": "message_visible",
                    "author_id": "CASE-A",
                },
                "queued_messages": [],
            },
            private_id: {
                "status": "running",
                "active_message": {
                    "message_id": "message_private",
                    "author_id": "sample_peer",
                },
                "queued_messages": [{"message_id": "message_private_queued"}],
            },
        }

        with (
            mock.patch.object(
                active_agent_projection.resident_catalog,
                "get_catalog",
                return_value=catalog,
            ),
            mock.patch.object(
                active_agent_projection.resident_protocol,
                "read_resident_activity",
                side_effect=lambda resident_ids: {
                    resident_id: activities[resident_id]
                    for resident_id in resident_ids
                },
            ),
            mock.patch.object(
                active_agent_projection,
                "_resident_account_id",
                return_value="zero",
            ),
            mock.patch.object(
                active_agent_projection,
                "_capacity_snapshot",
                return_value=(4, {"zero": 4}),
            ),
            mock.patch.object(
                permission_system,
                "readable_ids",
                return_value={visible_id},
            ),
        ):
            payload = active_agent_projection.collect(actor="CASE-A")

        self.assertEqual(payload["running"], 2)
        self.assertEqual(payload["queued"], 1)
        self.assertEqual(payload["running_runs"][0]["resident_id"], visible_id)
        anonymous = payload["running_runs"][1]
        self.assertEqual(anonymous["title"], "Agent")
        self.assertNotIn("resident_id", anonymous)
        self.assertNotIn("lineage_id", anonymous)
        self.assertNotIn("session_title", anonymous)
        self.assertNotIn("_sprite_actor", anonymous)
        self.assertFalse((Path(os.environ["PORTAL_RESIDENT_AVATAR_ROOT"]) / (private_id + ".json")).exists())
        self.assertEqual(payload["queued_display_count"], 0)
        self.assertEqual(payload["queued_runs"], [])
        self.assertNotIn(private_id, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("Private Resident", json.dumps(payload, ensure_ascii=False))

    def test_activity_display_limits_preserve_real_queue_targets(self) -> None:
        """Home 活动截断前保留真实排队入口与匿名边界

        :module: execution.scheduled-triggered
        :design: decision_e30897dd3dc3
        """
        # The first 48 queued records belong to running Residents. A waiting
        # Resident later in the snapshot must still reach Home with its identity.
        records = [
            {"resident_id": f"resident_{i:012x}", "lineage_id": "lineage_000000000001", "title": f"Agent {i}"}
            for i in range(1, 52)
        ]
        activities = {
            record["resident_id"]: {
                "status": "running" if i < 50 else "idle",
                "message_id": f"active_{i}" if i < 50 else "",
                "queued_messages": [{"message_id": f"queue_{i}_{j}"} for j in range(60 if i == 50 else 2)],
            }
            for i, record in enumerate(records)
        }
        catalog = mock.Mock()
        catalog.get_identity_records.return_value = records
        waiting_id = records[-1]["resident_id"]
        with (
            mock.patch.object(resident_catalog, "get_catalog", return_value=catalog),
            mock.patch.object(active_agent_projection.resident_protocol, "read_resident_activity", return_value=activities),
            mock.patch.object(active_agent_projection, "_resident_account_id", return_value="zero"),
            mock.patch.object(active_agent_projection, "_capacity_snapshot", return_value=(50, {"zero": 50})),
            mock.patch.object(active_agent_projection, "get_avatar", return_value=None),
            mock.patch.object(permission_system, "readable_ids", return_value={r["resident_id"] for r in records}) as readable,
        ):
            for visible in (True, False):
                with self.subTest(waiting_resident_readable=visible):
                    if not visible:
                        readable.return_value = set()
                    payload = active_agent_projection.collect(actor="CASE-A")
                    group = payload["account_groups"][0]
                    self.assertEqual((payload["running"], payload["queued"]), (50, 160))
                    self.assertEqual((group["running"], group["queued"]), (50, 160))
                    self.assertEqual(payload["queued_display_count"], 60)
                    self.assertEqual(group["queued_display_count"], 60)
                    self.assertEqual(len(group["running_runs"]), 48)
                    self.assertEqual(len(group["queued_runs"]), 48)
                    self.assertEqual(group["queued_runs"], payload["queued_runs"])
                    for run in group["queued_runs"]:
                        if visible:
                            self.assertEqual(run["resident_id"], waiting_id)
                            self.assertEqual(run["lineage_id"], "lineage_000000000001")
                            self.assertNotIn("sprite_index", run)
                        else:
                            self.assertEqual(run["title"], "Agent")
                            self.assertNotIn("resident_id", run)
                            self.assertNotIn("lineage_id", run)
                            self.assertNotIn("_resource_id", run)

    def test_active_view_reads_current_resident_state_on_every_request(self) -> None:
        """活动视图每次请求都读取 Resident 的当前状态"""
        resident_id = "resident_000000000001"
        catalog = mock.Mock()
        catalog.get_identity_records.return_value = [
            {"resident_id": resident_id, "title": "Resident Test"}
        ]
        current = {
            resident_id: {
                "status": "running",
                "message_id": "message_main",
                "active_message": {"message_id": "message_main"},
                "queued_messages": [],
            }
        }

        with (
            mock.patch.object(
                active_agent_projection.resident_catalog,
                "get_catalog",
                return_value=catalog,
            ),
            mock.patch.object(
                active_agent_projection.resident_protocol,
                "read_resident_activity",
                side_effect=lambda _resident_ids: current,
            ),
            mock.patch.object(
                active_agent_projection,
                "_resident_account_id",
                return_value="zero",
            ),
            mock.patch.object(
                active_agent_projection,
                "_capacity_snapshot",
                return_value=(4, {"zero": 4}),
            ),
            mock.patch.object(
                permission_system,
                "readable_ids",
                return_value={resident_id},
            ),
        ):
            first = active_agent_projection.collect(actor="CASE-A")
            current[resident_id] = {
                "status": "idle",
                "queued_messages": [],
            }
            second = active_agent_projection.collect(actor="CASE-A")

        self.assertEqual(first["running"], 1)
        self.assertEqual(second["running"], 0)
        self.assertEqual(second["running_runs"], [])


    def test_active_resident_identity_comes_from_canonical_resident_account(self):
        """活动 Resident 的账号取自该 Resident 规范的执行账号"""
        record = {
            "resident_id": "resident_000000000001",
            "backend": "codex",
            "backend_route_id": "five",
            "kind": "project",
            "project_id": "HardwareExplorer",
        }
        with mock.patch.object(
            active_agent_projection.resident_backend,
            "execution_account",
            return_value={"account_id": "future_standard"},
        ) as execution_account:
            account_id = active_agent_projection._resident_account_id(record)
        self.assertEqual(account_id, "future_standard")
        execution_account.assert_called_once_with(record)

    def test_resident_usage_reads_compact_execution_projection_and_filters_unreadable(
        self,
    ) -> None:
        """Resident 用量按执行汇总分段读取，无权查看的 Resident 归入其他"""
        root_id = "resident_000000000021"
        created_resident_id = "resident_000000000022"
        private_id = "resident_000000000023"
        aggregate_rows = [
            {"provider": "codex",
                "bucket_hour": "2026-07-22T08:00:00Z",
                "resident_id": root_id,
                "usage": {
                    "input_tokens": 20,
                    "cached_input_tokens": 40,
                    "output_tokens": 40,
                    "total_tokens": 100,
                },
            },
            {"provider": "codex",
                "bucket_hour": "2026-07-22T08:00:00Z",
                "resident_id": created_resident_id,
                "usage": {
                    "input_tokens": 30,
                    "cached_input_tokens": 20,
                    "output_tokens": 20,
                    "total_tokens": 50,
                },
            },
            {"provider": "codex",
                "bucket_hour": "2026-07-22T08:00:00Z",
                "resident_id": private_id,
                "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            },
            {"provider": "codex",
                "bucket_hour": "2026-07-22T08:00:00Z",
                "resident_id": "others",
                "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            },
        ]

        def token_usage_for_window(*, start_hour, end_hour, provider, include_accounts=False):
            self.assertEqual(provider, "*")
            return {
            "updated_at": "2026-07-22T08:00:00Z",
            "rows": [
                row
                for row in aggregate_rows
                if start_hour <= row["bucket_hour"] < end_hour
            ],
        }
        with (
            mock.patch.object(
                resident_usage_projection,
                "_local_now",
                return_value=datetime(
                    2026,
                    7,
                    22,
                    17,
                    tzinfo=timezone(timedelta(hours=8)),
                ),
            ),
            mock.patch.object(
                resident_usage_projection.execution_client,
                "token_usage",
                side_effect=token_usage_for_window,
            ) as token_usage,
            mock.patch.object(
                resident_usage_projection.resource_client,
                "visible_resident_usage_roots",
                return_value={
                    root_id: {
                        "resident_id": root_id,
                        "title": "Visible",
                        "kind": "project",
                    },
                    created_resident_id: {
                        "resident_id": root_id,
                        "title": "Visible",
                        "kind": "project",
                    },
                },
            ),
        ):
            payload = resident_usage_projection.collect(actor="sample_admin")

        self.assertEqual(
            payload["periods"],
            {"today": 175, "week": 175, "month": 175, "year": 175},
        )
        self.assertEqual(
            payload["period_others"],
            {"today": 5, "week": 5, "month": 5, "year": 5},
        )
        self.assertEqual(payload["daily_tracked"]["2026-07-22"], 180)
        self.assertEqual(payload["daily_assigned"]["2026-07-22"], 175)
        self.assertEqual(
            payload["hourly_today_by_resident"]["16:00"][root_id],
            150,
        )
        self.assertEqual(payload["hourly_today_assigned"]["16:00"], 175)
        self.assertEqual(payload["hourly_today_others"]["16:00"], 5)
        self.assertNotIn(private_id, payload["resident_titles"])
        without_cache = payload["excluding_cached_input"]
        self.assertEqual(without_cache["periods"]["today"], 115)
        self.assertEqual(without_cache["period_others"]["today"], 5)
        with mock.patch.object(
            status_backend,
            "_date_today",
            return_value=date(2026, 7, 22),
        ):
            today_series = status_backend._build_chart_series(payload)["today"]
        self.assertEqual(
            today_series[16]["segments"],
            [
                {"key": root_id, "label": "Visible", "tokens": 150},
                {
                    "key": "other_residents",
                    "label": "Other resident(s)",
                    "tokens": 25,
                },
                {
                    "key": "unregistered",
                    "label": "Unregistered session(s)",
                    "tokens": 5,
                },
            ],
        )
        self.assertGreater(token_usage.call_count, 1)
        self.assertEqual(
            token_usage.call_args_list[0].kwargs["start_hour"],
            "2025-12-31T16:00:00Z",
        )
        windows = [
            (
                datetime.fromisoformat(call.kwargs["start_hour"].replace("Z", "+00:00")),
                datetime.fromisoformat(call.kwargs["end_hour"].replace("Z", "+00:00")),
            )
            for call in token_usage.call_args_list
        ]
        self.assertTrue(all(end - start <= timedelta(days=62) for start, end in windows))
        self.assertTrue(all(left[1] == right[0] for left, right in zip(windows, windows[1:])))

    def test_usage_year_chart_aggregates_daily_points_by_month(self) -> None:
        """年度用量图按月汇总每日数据"""
        resident_id = "resident_000000000021"
        resident = {
            "resident_titles": {resident_id: "Visible"},
            "daily_assigned": {
                "2026-01-05": 10,
                "2026-01-06": 20,
                "2026-03-15": 30,
            },
            "daily_others": {"2026-01-06": 5},
            "daily_by_resident": {
                "2026-01-05": {resident_id: 10},
                "2026-01-06": {resident_id: 20},
                "2026-03-15": {resident_id: 30},
            },
        }
        with mock.patch.object(
            status_backend,
            "_date_today",
            return_value=datetime(2026, 3, 15).date(),
        ):
            series = status_backend._build_chart_series(resident)["year"]

        self.assertEqual([point["date"] for point in series], [
            "2026-01",
            "2026-02",
            "2026-03",
        ])
        self.assertEqual([point["tokens"] for point in series], [35, 0, 30])
        self.assertEqual(series[0]["segments"], [
            {"key": resident_id, "label": "Visible", "tokens": 30},
            {"key": "unregistered", "label": "Unregistered session(s)", "tokens": 5},
        ])

    def test_usage_uses_execution_ledger_for_all_dates(self) -> None:
        """所有日期的用量窗口与图表都取自执行账本，二者总量一致"""
        resident_id = "resident_000000000021"
        resident = {
            "resident_titles": {resident_id: "Visible"},
            "daily_assigned": {
                "2026-09-01": 20,
                "2026-09-02": 100,
                "2026-09-04": 30,
            },
            "daily_others": {"2026-09-04": 10},
            "daily_by_resident": {
                "2026-09-01": {resident_id: 20},
                "2026-09-02": {resident_id: 100},
                "2026-09-04": {resident_id: 30},
            },
            "residents": [{
                "resident_id": resident_id,
                "title": "Visible",
                "periods": {"today": 30, "week": 150, "month": 150, "year": 150},
            }],
            "periods": {"month": 150},
            "period_others": {"month": 10},
        }
        with mock.patch.object(
            status_backend,
            "_date_today",
            return_value=datetime(2026, 9, 4).date(),
        ):
            window = status_backend._make_token_windows(resident)["month"]
            chart = status_backend._build_chart_series(resident)["month"]

        self.assertEqual(window, {
            "total_tokens": 160,
            "assigned_tokens": 150,
            "other_tokens": 10,
        })
        self.assertEqual(chart[1]["tokens"], 100)
        self.assertEqual(chart[1]["assigned_tokens"], 100)
        self.assertEqual(sum(segment["tokens"] for segment in chart[1]["segments"]), 100)
        self.assertEqual(chart[3]["tokens"], 40)
        self.assertEqual(sum(point["tokens"] for point in chart), 160)
        self.assertEqual(window["total_tokens"], sum(point["tokens"] for point in chart))
        with mock.patch.object(
            status_backend,
            "_date_today",
            return_value=datetime(2026, 9, 4).date(),
        ):
            payload = status_backend._token_usage_payload(
                resident,
                include_cached_input=True,
            )
        self.assertEqual(payload["residents"][0]["periods"]["month"], 150)

    def test_recent_and_historical_usage_share_daily_totals(self) -> None:
        """今日、本月用量窗口与月度图表共用同一份每日总量"""
        resident_id = "resident_000000000021"
        resident = {
            "daily_assigned": {
                "2026-09-03": 80,
                "2026-09-04": 70,
                "2026-09-05": 20,
            },
            "daily_others": {
                "2026-09-04": 10,
                "2026-09-05": 5,
            },
            "daily_by_resident": {
                "2026-09-03": {resident_id: 80},
                "2026-09-04": {resident_id: 70},
                "2026-09-05": {resident_id: 20},
            },
        }
        with mock.patch.object(
            status_backend,
            "_date_today",
            return_value=datetime(2026, 9, 5).date(),
        ):
            windows = status_backend._make_token_windows(resident)
            month_chart = status_backend._build_chart_series(resident)["month"]

        self.assertEqual(windows["today"], {
            "total_tokens": 25,
            "assigned_tokens": 20,
            "other_tokens": 5,
        })
        self.assertEqual(windows["month"], {
            "total_tokens": 185,
            "assigned_tokens": 170,
            "other_tokens": 15,
        })
        self.assertEqual(month_chart[-2]["tokens"], 80)
        self.assertEqual(month_chart[-2]["assigned_tokens"], 70)
        self.assertEqual(month_chart[-1]["tokens"], 25)

    def test_historical_cache_is_removed_from_total_not_moved_to_other(self) -> None:
        """排除缓存输入时历史缓存从总量中扣除，不计入其他"""
        including_cache = {
            "daily_assigned": {"2026-09-03": 80},
            "daily_others": {},
            "daily_by_resident": {},
        }
        excluding_cache = {
            "daily_assigned": {"2026-09-03": 50},
            "daily_others": {},
            "daily_by_resident": {},
        }
        with mock.patch.object(
            status_backend,
            "_date_today",
            return_value=datetime(2026, 9, 5).date(),
        ):
            including_window = status_backend._make_token_windows(
                including_cache,
            )["month"]
            excluding_window = status_backend._make_token_windows(
                excluding_cache,
            )["month"]

        self.assertEqual(including_window["total_tokens"], 80)
        self.assertEqual(excluding_window["total_tokens"], 50)
        self.assertEqual(including_window["other_tokens"], 0)
        self.assertEqual(excluding_window["other_tokens"], 0)
    def test_usage_chart_keeps_top_twenty_residents_separate(self) -> None:
        """用量图单独显示用量前 20 的 Resident，其余合并为其他 Resident"""
        by_resident = {
            f"resident_{index:012x}": 100 - index
            for index in range(22)
        }
        points = [{
            "date": "2026-08-26",
            "label": "Today",
            "tokens": sum(by_resident.values()),
            "by_resident": by_resident,
        }]
        top_keys = status_backend._top_resident_keys(
            points,
            limit=status_backend.CHART_TOP_RESIDENT_LIMIT,
        )

        segmented = status_backend._segment_point(points[0], top_keys, {})
        resident_segments = [
            segment
            for segment in segmented["segments"]
            if segment["key"].startswith("resident_")
        ]
        other = next(
            segment
            for segment in segmented["segments"]
            if segment["key"] == "other_residents"
        )

        self.assertEqual(len(resident_segments), 20)
        self.assertEqual(other["tokens"], (100 - 20) + (100 - 21))


if __name__ == "__main__":
    unittest.main()
