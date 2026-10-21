""":module: execution.persistence"""
import json
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from management_portal import token_usage_snapshot_cache as snapshots
from management_portal import status_cache_store as shared, usage_snapshot_codec as codec




class TokenUsageSnapshotCacheTest(unittest.TestCase):
    def test_newer_partial_replaces_complete_snapshot_but_late_writers_do_not(self):
        """版本更新的部分快照取代完整快照，较旧或加载中的写入不覆盖"""
        # A partial summary (one account's native usage unreadable) is current
        # data; refusing it froze Home's usage while that account was down.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "status.json"
            key = "token_usage:v9:dev:alice"
            complete = {"payload": {"status": "ok", "snapshot_version": 10, "windows": {"month": {"total_tokens": 37}}}}
            snapshots.write(source, key, complete)
            snapshots.write(source, key, {"payload": {"status": "partial", "snapshot_version": 9, "windows": {"month": {"total_tokens": 36}}}})
            self.assertEqual(snapshots.read(source, key), complete)
            snapshots.write(source, key, {"payload": {"status": "loading", "snapshot_version": 11}})
            self.assertEqual(snapshots.read(source, key), complete)
            partial = {"payload": {"status": "partial", "snapshot_version": 12, "windows": {"month": {"total_tokens": 38}}}}
            snapshots.write(source, key, partial)
            self.assertEqual(snapshots.read(source, key), partial)

    def test_entry_isolation_and_warm_reads_do_not_parse_files(self):
        """用量快照按用户和实例隔离，热读取不再读磁盘文件"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "status.json"
            alice, bob = "token_usage:v9:dev:alice", "token_usage:v9:dev:bob"
            snapshots.write(source, alice, {"payload": {"snapshot_version": 1}})
            self.assertEqual(snapshots.read(source, alice)["payload"]["snapshot_version"], 1)
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("disk read")):
                self.assertEqual(snapshots.read(source, alice)["payload"]["snapshot_version"], 1)
                self.assertEqual(snapshots.read(source, bob), {})
                self.assertEqual(snapshots.read(source, "token_usage:v9:release:alice"), {})
            snapshots.write(source, alice, {"payload": {"snapshot_version": 2}})
            self.assertEqual(snapshots.read(source, alice)["payload"]["snapshot_version"], 2)
            self.assertFalse(source.exists())

    def test_dictionary_roundtrip_deduplicates_without_aliasing_variants(self):
        """用量快照编码时去重共享值，解码后各条目互不共享引用且保留类型"""
        row = {"resident_id": "resident_common", "name": "Shared resident title", "tokens": 123,
               "enabled": True, "absent": None, "decimal": 1.0, "values": ["list", "dict", [], {}]}
        entry = {"payload": {"including": [row] * 40, "excluding": [dict(row, tokens=1)] * 40}}
        encoded = codec.encode("token_usage:v9:dev:alice", entry)
        restored = codec.decode(json.loads(json.dumps(encoded)))
        self.assertEqual(entry, restored)
        self.assertLess(len(json.dumps(encoded)), len(json.dumps(entry)) / 3)
        self.assertEqual(encoded["nodes"].count("Shared resident title"), 1)
        restored["payload"]["including"][0]["tokens"] = 0
        self.assertEqual(restored["payload"]["including"][1]["tokens"], 123)
        self.assertIs(type(restored["payload"]["excluding"][0]["decimal"]), float)
        with self.assertRaises(ValueError):
            codec.decode({"format": codec.FORMAT, "root": 0, "nodes": [["list", 0]]})

    def test_new_contract_replaces_old_but_late_old_writers_cannot_restore_it(self):
        """新版本快照键取代旧版本，迟到的旧版本写入不能恢复旧条目"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "status.json"
            old, new = "token_usage:v9:dev:alice", "token_usage:v10:dev:alice"
            snapshots.write(source, old, {"payload": {"snapshot_version": 100}})
            snapshots.write(source, new, {"payload": {"snapshot_version": 1}})
            snapshots.write(source, old, {"payload": {"snapshot_version": 200}})
            self.assertEqual(snapshots.read(source, old), {})
            self.assertEqual(snapshots.read(source, new)["payload"]["snapshot_version"], 1)
            self.assertEqual(len(list(source.with_suffix(".usage").glob("*.json"))), 1)





def test_status_cache_write_retires_obsolete_contracts(tmp_path):
    """写入状态缓存时清除过时版本和已废弃的缓存条目

    :module: platform.core"""
    source = tmp_path / 'status.json'
    entries = {'agent_status_summary:v10:dev': {}, 'agent_status_summary:v9:dev': {},
               'agent_status_summary:v10:dev:alice': {}, 'provider_balances:v5': {},
               'resident_usage:v3:alice': {}, 'disk_status:local': {'available': 123}}
    shared.write(source, entries)
    assert shared.read(source) == {'agent_status_summary:v10:dev': {}, 'disk_status:local': {'available': 123}}
