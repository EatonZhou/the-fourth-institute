from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from management_portal import paper_backend, semantic_search_jobs
from management_portal.resources.providers import paper as paper_provider

PAPER_ONE = "paper_000000000001"
PAPER_TWO = "paper_000000000002"


class PaperStoreIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "papers"
        self.store = paper_backend.PaperStore(self.root)
        self.store.ensure()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_record(self, paper_id: str, title: str) -> dict:
        item_root = self.store.items_root / "2026" / paper_id
        item_root.mkdir(parents=True, exist_ok=True)
        (item_root / "manifest.json").write_text(json.dumps({
            "paper_id": paper_id,
            "paper_key": paper_id,
            "title": title,
            "source": {"created_at": 1, "updated_at": 1},
            "files": {},
            "relations": {},
        }), encoding="utf-8")
        return {
            "paper_id": paper_id,
            "paper_key": paper_id,
            "title": title,
            "root": str(item_root),
            "source": {"created_at": 1, "updated_at": 1},
            "files": {},
            "relations": {},
        }

    def test_store_uses_manifests_as_facts_and_builds_no_parallel_paper_list(self) -> None:
        """论文存储以清单为事实且不生成并行论文列表"""
        self.write_record(PAPER_ONE, "First")
        self.write_record(PAPER_TWO, "Second")

        self.assertEqual([paper["id"] for paper in self.store.all_papers()], [PAPER_ONE, PAPER_TWO])
        self.store.rebuild_fingerprint_index()
        self.assertFalse(self.store.index_path("papers.json").exists())
        self.assertTrue(self.store.index_path("fingerprints.json").is_file())

    def test_manifest_lookup_uses_persistent_manifest_index_before_filesystem_scan(self) -> None:
        """论文清单优先通过持久索引查找且无需扫描文件系统"""
        self.write_record(PAPER_ONE, "First")
        self.store.rebuild_fingerprint_index()
        with mock.patch.object(
            self.store,
            "manifest_paths",
            side_effect=AssertionError("unexpected full manifest scan"),
        ):
            result = self.store.manifest_for_paper_id(PAPER_ONE)

        self.assertIsNotNone(result)
        self.assertEqual(result[1]["paper_id"], PAPER_ONE)

    def move_items_to_external_disk(self) -> Path:
        external_items = Path(self.temp_dir.name) / "external" / "items"
        external_items.parent.mkdir()
        self.store.items_root.rename(external_items)
        self.store.items_root.symlink_to(external_items, target_is_directory=True)
        return external_items

    def test_manifest_index_lookup_survives_symlinked_items(self) -> None:
        """条目目录改为符号链接后清单索引仍可定位论文"""
        self.write_record(PAPER_ONE, "First")
        self.store.rebuild_fingerprint_index()
        self.move_items_to_external_disk()

        with mock.patch.object(
            self.store,
            "manifest_paths",
            side_effect=AssertionError("unexpected full manifest scan"),
        ):
            result = self.store.manifest_for_paper_id(PAPER_ONE)

        self.assertIsNotNone(result)
        self.assertEqual(result[1]["paper_id"], PAPER_ONE)

    def test_rebuild_manifest_index_keeps_logical_paths_with_symlinked_items(self) -> None:
        """符号链接条目重建清单索引后保留逻辑相对路径"""
        self.write_record(PAPER_ONE, "First")
        self.move_items_to_external_disk()

        self.store.rebuild_fingerprint_index()

        self.assertEqual(
            self.store.manifest_map()[PAPER_ONE],
            f"items/2026/{PAPER_ONE}/manifest.json",
        )

    def test_manifest_index_rejects_symlink_escape_outside_items(self) -> None:
        """论文清单索引拒绝经符号链接逃逸到条目目录外"""
        record = self.write_record(PAPER_ONE, "First")
        external_items = self.move_items_to_external_disk()
        neighbor = external_items.parent / "manifest.json"
        neighbor.write_text(json.dumps(record), encoding="utf-8")
        escaped = self.store.items_root / "escaped.json"
        escaped.symlink_to(neighbor)
        self.store.index_path("manifests.json").write_text(
            json.dumps({PAPER_ONE: "items/escaped.json"}), encoding="utf-8",
        )

        with mock.patch.object(self.store, "manifest_paths", return_value=[]):
            self.assertIsNone(self.store.manifest_for_paper_id(PAPER_ONE))

    def test_symlinked_items_keep_pdf_and_analysis_available(self) -> None:
        """符号链接条目保留 PDF 与分析文件可用性"""
        record = self.write_record(PAPER_ONE, "First")
        item_root = Path(record["root"])
        pdf_path = item_root / "paper.pdf"
        analysis_path = item_root / "analysis.md"
        pdf_path.write_bytes(b"%PDF-1.7\nfixture")
        analysis_path.write_text("# Analysis", encoding="utf-8")
        record["files"] = {"pdf": "paper.pdf", "analysis": "analysis.md"}
        (item_root / "manifest.json").write_text(json.dumps(record), encoding="utf-8")
        self.move_items_to_external_disk()

        with (
            mock.patch.object(paper_backend, "PAPER_STORE", self.store),
            mock.patch.object(paper_backend, "ALLOWED_FILE_ROOTS", [self.root]),
        ):
            paper = self.store.manifest_to_paper(record, item_root / "manifest.json")
            public = paper_backend._paper_public(paper)
            self.assertTrue(public["has_pdf"])
            self.assertTrue(public["has_analysis"])
            for path in (pdf_path, analysis_path):
                self.assertEqual(paper_backend._safe_file_path(str(path)), path.resolve())
                self.assertEqual(paper_backend._safe_file_path(str(path.resolve())), path.resolve())
            asset, content_type = paper_backend.resolve_asset(PAPER_ONE, "pdf")

        self.assertEqual(asset.read_bytes(), b"%PDF-1.7\nfixture")
        self.assertEqual(content_type, "application/pdf")

    def test_symlinked_items_do_not_allow_neighbor_or_escaped_files(self) -> None:
        """符号链接条目仍拒绝相邻目录与逃逸文件"""
        self.write_record(PAPER_ONE, "First")
        external_items = self.move_items_to_external_disk()
        neighbor = external_items.parent / "private.pdf"
        neighbor.write_bytes(b"%PDF-1.7\nprivate")
        escaped = self.store.items_root / "2026" / PAPER_ONE / "escaped.pdf"
        escaped.symlink_to(neighbor)

        with (
            mock.patch.object(paper_backend, "PAPER_STORE", self.store),
            mock.patch.object(paper_backend, "ALLOWED_FILE_ROOTS", [self.root]),
        ):
            self.assertIsNone(paper_backend._safe_file_path(str(neighbor)))
            self.assertIsNone(paper_backend._safe_file_path(str(escaped)))

    def test_paper_id_is_canonical(self) -> None:
        """论文标识校验接受规范格式且拒绝外部私有标识"""
        self.assertEqual(paper_backend._validated_paper_id(PAPER_ONE), PAPER_ONE)
        with self.assertRaisesRegex(ValueError, "invalid Paper ID"):
            paper_backend._validated_paper_id("provider-specific-id")

    def test_paper_mutations_reject_generic_id_fields(self) -> None:
        """论文修改接口拒绝以通用 id 字段替代专用标识"""
        with self.assertRaisesRegex(ValueError, "paper_id required"):
            paper_backend.delete_paper({"id": PAPER_ONE}, "sample_admin")
        with self.assertRaisesRegex(ValueError, "paper_id required"):
            paper_backend.upload_paper_pdf(
                {"id": PAPER_ONE, "data_bytes": b"%PDF-1.7"},
                "sample_admin",
            )
        with self.assertRaisesRegex(ValueError, "paper collection job id required"):
            paper_backend.delete_paper_collection_job({"id": "not-a-collection-job"}, "sample_admin")
        with self.assertRaisesRegex(ValueError, "semantic search id required"):
            semantic_search_jobs.delete_search(
                "paper",
                {"id": "search_0123456789ab"},
                "sample_admin",
            )

    def test_scan_preserves_canonical_paper_id_and_resolves_it(self) -> None:
        """论文标识扫描保留规范标识并可解析到页面"""
        record = self.write_record(PAPER_ONE, "First")
        manifest_path = Path(record["root"]) / "manifest.json"

        preview = paper_backend.scan_and_clean_paper_ids(
            paper_root=self.root,
            apply=False,
        )
        self.assertEqual(preview["changed_count"], 0)
        self.assertEqual(preview["invalid_count"], 0)
        applied = paper_backend.scan_and_clean_paper_ids(
            paper_root=self.root,
            apply=True,
        )
        self.assertEqual(applied["changed_count"], 0)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["paper_id"], PAPER_ONE)
        resolved = paper_backend.resolve_paper_address(
            PAPER_ONE,
            paper_root=self.root,
        )
        self.assertEqual(resolved["paper_id"], PAPER_ONE)
        self.assertEqual(resolved["portal_url"], f"/paper?paper={PAPER_ONE}")

    def test_root_paper_content_reads_the_primary_pdf(self) -> None:
        """论文根资源内容读取主 PDF 并返回正确媒体类型"""
        record = self.write_record(PAPER_ONE, "First")
        item_root = Path(record["root"])
        pdf = b"%PDF-1.7\nprimary paper bytes"
        (item_root / "paper.pdf").write_bytes(pdf)
        manifest_path = item_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = {"pdf": "paper.pdf"}
        manifest["root"] = str(item_root)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        root = paper_backend.resolve_paper_address(
            PAPER_ONE,
            paper_root=self.root,
        )
        with mock.patch.object(
            paper_backend,
            "PAPER_STORE",
            self.store,
        ):
            content, content_type, resolved = paper_provider._read_content(
                root,
                address=PAPER_ONE,
                max_bytes=1024,
            )

        self.assertEqual(content, pdf)
        self.assertEqual(content_type, "application/pdf")
        self.assertEqual(resolved["relative_path"], "paper.pdf")

    def test_new_paper_uses_unified_id(self) -> None:
        """新论文分配统一格式标识且不附加 resource_id"""
        paper = paper_backend._clean_paper({"title": "New Paper"}, actor="sample_admin")
        self.assertRegex(paper["id"], r"^paper_[0-9a-f]{12}$")
        self.assertNotIn("resource_id", paper)

    def test_ref_context_requires_source_paper_id(self) -> None:
        """参考文献上下文要求提供来源 paper_id"""
        with self.assertRaisesRegex(ValueError, "paper_id required"):
            paper_backend._ref_context("ref-1")

if __name__ == "__main__":
    unittest.main()
