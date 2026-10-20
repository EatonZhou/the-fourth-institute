import tempfile
import unittest
from pathlib import Path
from unittest import mock

from knowledge_board_lan import knowledge_backend


class KnowledgeEntryRootsTest(unittest.TestCase):
    def test_skill_companion_file_resolves_to_the_primary_resource_id(self):
        """skill 附属文件解析到主资源标识"""
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "project" / "knowledge_base" / "skill" / "extend-portal"
            entry.mkdir(parents=True)
            primary = entry / "SKILL.md"
            companion = entry / "implementation-workflow.md"
            primary.write_text(
                "---\nname: extend-portal\nmetadata:\n  id: skill_4763ed766f6f\n---\n\n# Extend Portal\n",
                encoding="utf-8",
            )
            companion.write_text("# Implementation workflow\n", encoding="utf-8")

            with mock.patch.object(
                knowledge_backend,
                "_get_tag_config",
                return_value=(
                    ["skill"],
                    {"skill": "skill"},
                    {"skill": "skill"},
                    "test",
                ),
            ):
                resource_id = knowledge_backend.knowledge_resource_id_for_path(
                    str(companion),
                )

            self.assertEqual(resource_id, "skill_4763ed766f6f")

    def test_external_markdown_without_index_owner_does_not_scan_knowledge_items(self):
        """未命中索引归属的外部 Markdown 不扫描知识条目"""
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "latest.md"
            external.write_text("# External report\n", encoding="utf-8")
            resource_service = mock.Mock()
            resource_service.query.side_effect = [
                {"resources": [], "total": 0},
                {"resources": [], "total": 0},
            ]

            with (
                mock.patch.object(
                    knowledge_backend,
                    "get_resource_service",
                    return_value=resource_service,
                ),
                mock.patch.object(
                    knowledge_backend,
                    "resource_index_snapshot",
                ) as index_scan,
                mock.patch.object(
                    knowledge_backend,
                    "resolve_item_record",
                ) as resolve_item,
            ):
                with self.assertRaisesRegex(KeyError, "knowledge item not found"):
                    knowledge_backend.item_metadata_for_path(str(external))

            index_scan.assert_not_called()
            resolve_item.assert_not_called()
            self.assertEqual(resource_service.query.call_count, 2)

    def test_indexed_external_markdown_resolves_only_its_owner(self):
        """已索引外部 Markdown 仅解析其所属条目"""
        resource_id = "skill_4763ed766f6f"
        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "extend-portal"
            package_root.mkdir()
            primary = package_root / "SKILL.md"
            companion = package_root / "workflow.md"
            primary.write_text("# Extend Portal\n", encoding="utf-8")
            companion.write_text("# Workflow\n", encoding="utf-8")
            indexed = {
                "resource_id": resource_id,
                "resource_type": "skill",
                "source": {"path": str(primary)},
                "attributes": {
                    "content_root": str(package_root),
                    "single_file": False,
                },
            }
            resource_service = mock.Mock()
            resource_service.query.side_effect = [
                {"resources": [indexed], "total": 1},
                {"resources": [], "total": 0},
            ]
            expected = {"kb_id": resource_id, "title": "Extend Portal"}

            with (
                mock.patch.object(
                    knowledge_backend,
                    "get_resource_service",
                    return_value=resource_service,
                ),
                mock.patch.object(
                    knowledge_backend,
                    "knowledge_resource_id_for_path",
                    side_effect=KeyError("knowledge item not found"),
                ),
                mock.patch.object(
                    knowledge_backend,
                    "resolve_item_record",
                    return_value=expected,
                ) as resolve_item,
            ):
                result = knowledge_backend.item_metadata_for_path(str(companion))

            self.assertEqual(result, expected)
            resolve_item.assert_called_once_with(resource_id, active_only=False)

    def test_kb_id_is_the_only_public_root_address(self):
        """kb_id 作为唯一公共根地址并隐藏内部路径"""
        item = {
            "id": "thinking_ffffffffffff",
            "kb_id": "thinking_0123456789ab",
            "type": "thinking",
            "title": "Canonical knowledge",
            "file_id": "1234567890abcdef",
            "path": "/private/root/entry.md",
            "root_path": "/private/root",
            "files": [
                {
                    "file_id": "1234567890abcdef",
                    "name": "entry.md",
                    "relative_path": "entry.md",
                    "kind": "markdown",
                    "primary": True,
                },
                {
                    "file_id": "fedcba0987654321",
                    "name": "图 1.png",
                    "relative_path": "图 1.png",
                    "kind": "binary",
                    "primary": False,
                },
            ],
        }

        public = knowledge_backend.public_item_record(item)

        self.assertEqual(public["kb_id"], item["kb_id"])
        self.assertNotIn("id", public)
        self.assertNotIn("file_id", public)
        self.assertNotIn("path", public)
        self.assertEqual(public["files"][0]["address"], item["kb_id"])
        self.assertEqual(
            public["files"][1]["address"],
            f"{item['kb_id']}/file/%E5%9B%BE%201.png",
        )

    def test_path_hash_cannot_resolve_a_knowledge_item(self):
        """路径哈希不能用于解析知识条目"""
        with self.assertRaisesRegex(ValueError, "canonical kb_id required"):
            knowledge_backend.resolve_item_record("1234567890abcdef")

    def test_indexed_skill_pdf_is_resolvable_but_not_text(self):
        """已索引 skill PDF 可解析且识别为 PDF 文件"""
        with tempfile.TemporaryDirectory() as tmp:
            attachment = Path(tmp) / "reference.pdf"
            attachment.write_bytes(b"%PDF-test")

            with mock.patch.object(knowledge_backend.skill_backend, "_path_is_allowed", return_value=True):
                self.assertEqual(knowledge_backend._valid_item_path(str(attachment)), attachment.resolve())
            self.assertEqual(knowledge_backend._file_kind(attachment), "pdf")

    def test_directory_is_one_entry_and_nested_reports_are_companions(self):
        """目录作为单个知识条目且嵌套报告作为附属文件"""
        with tempfile.TemporaryDirectory() as tmp:
            tag_root = Path(tmp)
            entry = tag_root / "camera_probe__experiment_abcdef123456"
            report = entry / "explore" / "nodes" / "root" / "report.md"
            report.parent.mkdir(parents=True)
            (entry / "experiment.md").write_text("# Camera probe\n", encoding="utf-8")
            report.write_text("# Internal report\n", encoding="utf-8")

            specs = knowledge_backend._entry_specs(tag_root, "experiment")

            self.assertEqual(len(specs), 1)
            root, primary, files = specs[0]
            self.assertEqual(root, entry)
            self.assertEqual(primary, entry / "experiment.md")
            self.assertEqual(files, [entry / "experiment.md", report])

    def test_resource_index_entry_files_are_bounded_and_ignore_build_trees(self):
        """资源索引限制条目文件数量且忽略构建目录"""
        with tempfile.TemporaryDirectory() as tmp:
            tag_root = Path(tmp) / "material"
            entry = tag_root / "large-material"
            entry.mkdir(parents=True)
            primary = entry / "README.md"
            primary.write_text("# Large material\n", encoding="utf-8")
            for index in range(130):
                (entry / f"file-{index:03d}.txt").write_text(
                    str(index),
                    encoding="utf-8",
                )
            ignored = entry / "node_modules" / "package"
            ignored.mkdir(parents=True)
            (ignored / "ignored.js").write_text("ignored", encoding="utf-8")

            specs = knowledge_backend._index_entry_specs(tag_root)

            self.assertEqual(len(specs), 1)
            _root, selected, files, truncated = specs[0]
            self.assertEqual(selected, primary)
            self.assertEqual(
                len(files),
                knowledge_backend._INDEX_ENTRY_FILE_LIMIT,
            )
            self.assertTrue(truncated)
            self.assertIn(primary, files)
            self.assertFalse(
                any("node_modules" in path.parts for path in files)
            )

    def test_item_graph_resolves_public_index_records_by_knowledge_id(self):
        """条目图谱按知识标识解析公共索引记录"""
        root_id = "thinking_0123456789ab"
        related_id = "idea_abcdef123456"
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp) / "root.md"
            related_path = Path(tmp) / "related.md"
            root_path.write_text(
                "---\nlinks:\n  related:\n  - idea_abcdef123456\n---\n\nRoot\n",
                encoding="utf-8",
            )
            related_path.write_text("---\nlinks:\n  related: []\n---\n\nRelated\n", encoding="utf-8")
            resolved = {
                root_id: {
                    "kb_id": root_id,
                    "type": "thinking",
                    "title": "Root",
                    "project": "AlignMe",
                    "path": str(root_path),
                },
                related_id: {
                    "kb_id": related_id,
                    "type": "idea",
                    "title": "Related",
                    "project": "AlignMe",
                    "path": str(related_path),
                },
            }
            with knowledge_backend._LOCK:
                knowledge_backend._ITEM_GRAPH_CACHE.clear()
            with (
                mock.patch.object(
                    knowledge_backend,
                    "resolve_item_record",
                    side_effect=lambda kb_id, active_only=False, actor="": dict(resolved[kb_id]),
                ),
                mock.patch.object(
                    knowledge_backend,
                    "_item_graph_records",
                    return_value=resolved,
                ),
            ):
                graph = knowledge_backend.build_item_graph(root_id)

            self.assertEqual({node["kb_id"] for node in graph["nodes"]}, {root_id, related_id})
            self.assertEqual(graph["edges"], [{
                "source": root_id,
                "target": related_id,
                "relation": "related",
                "depth": 1,
            }])

    def test_global_knowledge_graph_uses_only_declared_frontmatter_links(self):
        """全局知识图谱仅采用元数据显式声明的链接"""
        root_id = "thinking_0123456789ab"
        related_id = "idea_abcdef123456"
        isolated_id = "decision_111111111111"
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp) / "root.md"
            related_path = Path(tmp) / "related.md"
            isolated_path = Path(tmp) / "isolated.md"
            root_path.write_text(
                "---\nlinks:\n"
                f"  related:\n  - {related_id}\n"
                "  source:\n  - https://example.test/reference\n"
                "---\n\nRoot\n",
                encoding="utf-8",
            )
            related_path.write_text(
                "---\nlinks:\n"
                f"  derived_from:\n  - {root_id}\n"
                "---\n\nRelated\n",
                encoding="utf-8",
            )
            isolated_path.write_text("---\nlinks: {}\n---\n\nIsolated\n", encoding="utf-8")
            paths = {
                root_id: root_path,
                related_id: related_path,
                isolated_id: isolated_path,
            }
            public_index = {
                "items": [
                    {
                        "kb_id": root_id,
                        "type": "thinking",
                        "title": "Root",
                        "project": "AlignMe",
                        "importance": 5,
                    },
                    {
                        "kb_id": related_id,
                        "type": "idea",
                        "title": "Related",
                        "project": "AlignMe",
                        "importance": 3,
                    },
                    {
                        "kb_id": isolated_id,
                        "type": "decision",
                        "title": "Isolated",
                        "project": "RobotWorker",
                        "importance": 1,
                    },
                ]
            }
            resource_service = mock.Mock()
            resource_service.query.return_value = {
                "resources": [
                    {
                        "resource_id": kb_id,
                        "source": {"path": str(path)},
                    }
                    for kb_id, path in paths.items()
                ],
            }
            with knowledge_backend._LOCK:
                knowledge_backend._KNOWLEDGE_GRAPH_CACHE.clear()
            with (
                mock.patch.object(
                    knowledge_backend,
                    "resource_index_snapshot",
                    return_value=public_index,
                ) as snapshot,
                mock.patch.object(
                    knowledge_backend,
                    "get_resource_service",
                    return_value=resource_service,
                ),
            ):
                graph = knowledge_backend.build_knowledge_graph(actor="reader")

            self.assertEqual(snapshot.call_args.kwargs["actor"], "reader")
            self.assertEqual(resource_service.query.call_args.kwargs["actor"], "reader")

            self.assertEqual(graph["schema"], "tfi.knowledge_graph.v1")
            self.assertEqual(
                graph["edges"],
                [
                    {
                        "source": related_id,
                        "target": root_id,
                        "relation": "derived_from",
                    },
                    {
                        "source": root_id,
                        "target": related_id,
                        "relation": "related",
                    },
                ],
            )
            degrees = {node["kb_id"]: node["degree"] for node in graph["nodes"]}
            self.assertEqual(degrees[root_id], 2)
            self.assertEqual(degrees[related_id], 2)
            self.assertEqual(degrees[isolated_id], 0)
            self.assertEqual(graph["relation_counts"]["related"], 1)
            self.assertEqual(graph["relation_counts"]["derived_from"], 1)
            self.assertEqual(graph["unresolved_links"], 1)

    def test_frontmatter_writer_replaces_id_with_kb_id(self):
        """写入元数据时用 kb_id 替换 id 字段"""
        migrated = knowledge_backend._set_frontmatter_kb_id(
            "---\nid: thinking_0123456789ab\ntype: thinking\n---\n\nBody\n",
            "thinking_0123456789ab",
        )

        self.assertIn("\nkb_id: thinking_0123456789ab\n", migrated)
        self.assertNotRegex(migrated, r"(?m)^id\s*:")

    def test_knowledge_reader_does_not_accept_id_as_kb_id(self):
        """知识读取器拒绝用 id 字段替代 kb_id"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "entry.md"
            path.write_text(
                "---\nid: thinking_0123456789ab\ntype: thinking\n---\n\nBody\n",
                encoding="utf-8",
            )

            item = knowledge_backend._read_kb_file(
                str(path),
                "Test",
                {"thinking"},
                {},
                entry_root=root,
                entry_files=[path],
            )

            self.assertEqual(item, {})


if __name__ == "__main__":
    unittest.main()
