from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


from management_portal import article_identity_backend as backend
from management_portal import article_texlab_backend
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from tests.cases.registry_support import registry_document, write_projects
from management_portal.resources.providers import article
from management_portal.resources.registration import ResourceRegistrationStore


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _make_article_repo(path: Path, title: str, origin: str) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.name", "TFI Portal Test")
    _git(path, "config", "user.email", "tfi-test@example.invalid")
    (path / "main.tex").write_text(
        "\\documentclass{article}\n"
        f"\\title{{{title}}}\n"
        "\\begin{document}\n"
        "% a comment whose removal must not shift section offsets\n"
        "\\section{Introduction}\nIntro body.\n"
        "\\section{Evaluation}\\label{sec:evaluation}\nEvaluation body.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    _git(path, "add", "main.tex")
    _git(path, "commit", "-q", "-m", "initial")
    _git(path, "remote", "add", "origin", origin)


class ArticleIdentityBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "ExampleProject"
        self.project.mkdir()
        write_projects(
            {
                "projects": [
                    {
                        "id": "ExampleProject",
                        "path": str(self.project),
                        "status": "active",
                    }
                ]
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_title_macros_keep_latex_replacements_literal(self) -> None:
        """Article 标题提取会展开自定义宏，宏内容中的 LaTeX 反斜杠按字面处理"""
        article_root = self.project / "article"
        article_root.mkdir()
        manuscript = article_root / "main.tex"
        manuscript.write_text(
            "\\documentclass{article}\n"
            "\\newcommand{\\name}{\\textsc{ArchExplorer}}\n"
            "\\newcommand\\hpcaauthors{First Author$\\dagger$}\n"
            "\\title{\\name{}: Automated Architecture Discovery}\n"
            "\\begin{document}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )

        self.assertEqual(
            backend.extract_article_title(manuscript),
            "ArchExplorer: Automated Architecture Discovery",
        )

    def test_standalone_tex_root_outranks_titled_support_documents(self) -> None:
        """可独立编译的 tex 文件优先作为主文档，标题取自其引入的设置文件，编译器取自 TeX program 注释"""
        article_root = self.project / "article"
        article_root.mkdir()
        (article_root / "THUTHESIS_CHANGELOG.md").write_text(
            "# Changelog\n",
            encoding="utf-8",
        )
        (article_root / "whitepaper_setup.tex").write_text(
            "\\thusetup{\n"
            "  title = {Physical AI Infrastructure},\n"
            "}\n",
            encoding="utf-8",
        )
        (article_root / "physical_ai_infra_whitepaper.tex").write_text(
            "% !TeX program = xelatex\n"
            "\\documentclass{article}\n"
            "\\input{whitepaper_setup}\n"
            "\\begin{document}\n"
            "Body\n"
            "\\end{document}\n",
            encoding="utf-8",
        )

        inspected = backend.inspect_article_root(
            article_root,
        )
        self.assertEqual(
            inspected["primary_file"],
            "physical_ai_infra_whitepaper.tex",
        )
        self.assertEqual(inspected["title"], "Physical AI Infrastructure")

        record = backend.register_article(
            article_root,
        )
        settings = backend.load_article_settings(
            record["id"],
            build_root=self.root / "article-build",
        )
        self.assertEqual(settings["primary_file"], "physical_ai_infra_whitepaper.tex")
        self.assertEqual(settings["compiler"], "xelatex")

    def test_scan_merges_same_article_variants_and_is_idempotent(self) -> None:
        """扫描时同一标题的多个 Article 目录合并为一条记录，重复扫描不产生变更"""
        canonical = self.project / "article"
        arxiv = self.project / "article_arxiv"
        empty = self.project / "articles"
        _make_article_repo(canonical, "One Logical Article", "https://example.invalid/main.git")
        _make_article_repo(arxiv, "One Logical Article", "https://example.invalid/arxiv.git")
        empty.mkdir()

        report = backend.scan_and_sync_article_registry(
            [self.project],
            apply=True,
        )
        self.assertEqual(report["article_root_count"], 2)
        self.assertEqual(report["logical_article_count"], 1)
        self.assertEqual(report["created_count"], 1)
        self.assertEqual(report["skipped_count"], 1)
        record = backend.load_article_records()[0]
        self.assertRegex(record["id"], r"^article_[0-9a-f]{12}$")
        self.assertEqual(record["canonical_root"], str(canonical))
        self.assertEqual(record["project_ids"], ["ExampleProject"])
        self.assertNotIn("source_repo_id", record)
        self.assertNotIn("source_repo_ids", record)
        self.assertTrue(
            all("source_repo_id" not in root for root in record["roots"])
        )
        self.assertEqual(len(record["roots"]), 2)

        repeated = backend.scan_and_sync_article_registry(
            [self.project],
            apply=True,
        )
        self.assertEqual(repeated["created_count"], 0)
        self.assertEqual(repeated["updated_count"], 0)
        self.assertEqual(repeated["unchanged_count"], 1)

    def test_resolve_article_file_and_section(self) -> None:
        """ResourceService 按 Article 地址解析主文件、指定文件和章节，拒绝越出目录的路径"""
        access = PortalAccessStore()
        access.upsert_user(
            "article-test", actor="fixture"
        )
        service = ResourceService(
            portal_access=access,

            collaboration_store=CollaborationDescriptorStore(),
            registration_store=ResourceRegistrationStore(),
            resolver_loader=article.install_resolver,
        )
        article_root = self.project / "article"
        _make_article_repo(article_root, "Resolvable Article", "https://example.invalid/article.git")
        record = backend.register_article(
            article_root,
        )
        article_id = record["id"]

        root = service.resolve(
            article_id, actor="article-test"
        )
        self.assertEqual(root["target_kind"], "article")
        self.assertEqual(root["record"]["title"], "Resolvable Article")
        self.assertEqual(root["primary_file"], "main.tex")
        self.assertEqual(root["resolved_path"], str(article_root / "main.tex"))

        root_data, root_content_type, resolved_root = (
            service.read_content(
                article_id, actor="article-test"
            )
        )
        self.assertEqual(resolved_root["target_kind"], "article")
        self.assertIn(b"Resolvable Article", root_data)
        self.assertIn("tex", root_content_type)

        data, content_type, resolved_file = service.read_content(
            f"{article_id}/file/main.tex", actor="article-test"
        )
        self.assertEqual(resolved_file["target_kind"], "file")
        self.assertIn(b"Resolvable Article", data)
        self.assertIn("tex", content_type)

        section_data, _, section = service.read_content(
            f"{article_id}/section/evaluation", actor="article-test"
        )
        self.assertEqual(section["section_title"], "Evaluation")
        self.assertIn(b"Evaluation body", section_data)
        self.assertNotIn(b"Intro body", section_data)

        with self.assertRaises(ValueError):
            service.resolve(
                f"{article_id}/file/../outside.tex", actor="article-test"
            )

        # Open mode lets every valid User and Resident through.
        access.upsert_user("article-test", actor="fixture")
        data, _, _ = service.read_content(article_id, actor="article-test")
        self.assertIn(b"Resolvable Article", data)

    def test_article_git_management_is_scoped_to_registered_directory(self) -> None:
        """Article 的 Git 状态、差异、暂存和提交限定在登记目录内，暂存区含目录外文件时拒绝提交"""
        article_root = self.project / "article"
        article_root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.project)], check=True)
        _git(self.project, "config", "user.name", "TFI Portal Test")
        _git(self.project, "config", "user.email", "tfi-test@example.invalid")
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nFirst draft.\n\\end{document}\n",
            encoding="utf-8",
        )
        (self.project / "outside.txt").write_text("outside\n", encoding="utf-8")
        _git(self.project, "add", "article/main.tex", "outside.txt")
        _git(self.project, "commit", "-q", "-m", "initial")
        record = backend.register_article(
            article_root,
        )
        article_id = record["id"]

        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nModel revision.\n\\end{document}\n",
            encoding="utf-8",
        )
        status = backend.article_git_status(article_id)
        self.assertTrue(status["available"])
        self.assertEqual(status["branch"], "main")
        self.assertEqual([item["path"] for item in status["changes"]], ["main.tex"])
        diff = backend.article_git_diff(
            article_id, "main.tex"
        )
        self.assertIn("-First draft.", diff["diff"])
        self.assertIn("+Model revision.", diff["diff"])

        staged = backend.stage_article_git_paths(
            article_id,
            ["main.tex"],
            staged=True,
        )
        self.assertEqual(staged["staged_count"], 1)
        unstaged = backend.stage_article_git_paths(
            article_id,
            ["main.tex"],
            staged=False,
        )
        self.assertEqual(unstaged["staged_count"], 0)
        backend.stage_article_git_paths(
            article_id,
            ["main.tex"],
            staged=True,
        )
        committed = backend.commit_article_git_changes(
            article_id,
            "Revise manuscript",
            actor="model-agent",
        )
        self.assertEqual(committed["commit"]["subject"], "Revise manuscript")
        self.assertFalse(committed["status"]["dirty"])
        history = backend.article_git_history(
            article_id
        )
        revised_commit = next(
            item for item in history["commits"] if item["subject"] == "Revise manuscript"
        )
        revised_diff = backend.article_git_commit_diff(
            article_id, revised_commit["sha"]
        )
        self.assertIn("+Model revision.", revised_diff["diff"])
        initial_commit = next(
            item for item in history["commits"] if item["subject"] == "initial"
        )
        initial_diff = backend.article_git_commit_diff(
            article_id, initial_commit["sha"]
        )
        self.assertIn("article/main.tex", initial_diff["diff"])
        self.assertNotIn("outside.txt", initial_diff["diff"])

        (article_root / "main.tex").write_text(
            (article_root / "main.tex").read_text(encoding="utf-8") + "% next\n",
            encoding="utf-8",
        )
        (self.project / "outside.txt").write_text("outside staged\n", encoding="utf-8")
        _git(self.project, "add", "outside.txt")
        backend.stage_article_git_paths(
            article_id,
            ["main.tex"],
            staged=True,
        )
        with self.assertRaisesRegex(ValueError, "Article 目录之外"):
            backend.commit_article_git_changes(
                article_id,
                "Must stay scoped",
            )
        self.assertEqual(
            _git(self.project, "diff", "--cached", "--name-only").splitlines(),
            ["article/main.tex", "outside.txt"],
        )

    def test_article_git_can_be_initialized_for_unversioned_article(self) -> None:
        """未纳入版本管理的 Article 可初始化 Git 仓库，初始化后文件显示为未跟踪"""
        article_root = self.project / "article"
        article_root.mkdir()
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nDraft.\n\\end{document}\n",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        article_id = record["id"]
        unavailable = backend.article_git_status(
            article_id
        )
        self.assertFalse(unavailable["available"])

        initialized = backend.initialize_article_git(
            article_id
        )
        self.assertTrue(initialized["available"])
        self.assertEqual(initialized["branch"], "main")
        self.assertEqual(initialized["untracked_count"], 1)
        self.assertEqual(initialized["changes"][0]["path"], "main.tex")

    def test_explicit_id_attaches_changed_title_variant(self) -> None:
        """指定 Article ID 登记时，标题已改的目录并入同一 Article"""
        first = self.project / "article"
        second = self.project / "article_submission"
        _make_article_repo(first, "Working Title", "https://example.invalid/working.git")
        _make_article_repo(second, "Final Title", "https://example.invalid/final.git")
        record = backend.register_article(
            first,
        )
        attached = backend.register_article(
            second,
            article_id=record["id"],
        )
        self.assertEqual(attached["id"], record["id"])
        self.assertEqual(len(attached["roots"]), 2)
        self.assertEqual(len(backend.load_article_records()), 1)

    def test_unregister_removes_only_the_registry_record(self) -> None:
        """注销 Article 只删除登记记录，不删除目录和文件"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Registered Article", "https://example.invalid/article.git")
        record = backend.register_article(
            article_root,
        )

        result = backend.unregister_article(record["id"])

        self.assertTrue(result["deleted"])
        self.assertTrue(article_root.is_dir())
        self.assertTrue((article_root / "main.tex").is_file())
        self.assertEqual(backend.load_article_records(), [])

    def test_compile_writes_pdf_to_build_cache_not_article_directory(self) -> None:
        """编译 Article 时 PDF 写入构建缓存，不写入 Article 目录"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Compilable Article", "https://example.invalid/article.git")
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "article-build"

        result = backend.compile_article_pdf(
            record["id"],
            build_root=build_root,
            from_scratch=True,
        )

        pdf_path = Path(result["pdf_path"])
        self.assertEqual(pdf_path, build_root / f"{record['id']}.pdf")
        self.assertTrue(pdf_path.is_file())
        self.assertEqual(pdf_path.read_bytes()[:4], b"%PDF")
        self.assertFalse(result["warnings"])
        self.assertTrue(result["from_scratch"])
        self.assertFalse((article_root / "main.pdf").exists())
        report = backend.load_article_build_report(
            record["id"], build_root=build_root
        )
        self.assertEqual(report["status"], "success")
        self.assertTrue(report["pdf_available"])
        self.assertEqual(report["primary_file"], "main.tex")

    def test_fast_draft_replaces_graphics_and_scratch_clears_auxiliary_cache(self) -> None:
        """草稿模式编译用占位框代替图片，增量编译保留缓存，从头编译清空辅助缓存"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Draft Article", "https://example.invalid/article.git")
        (article_root / "pixel.png").write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\usepackage{graphicx}\n"
            "\\begin{document}\n"
            "Before\\includegraphics[width=4cm]{pixel.png}After\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "article-build"
        backend.update_article_settings(
            record["id"],
            {"compile_mode": "draft"},
            build_root=build_root,
        )

        draft = backend.compile_article_pdf(
            record["id"],
            build_root=build_root,
            from_scratch=True,
        )

        self.assertEqual(draft["compile_mode"], "draft")
        cache_root = backend.article_build_cache_path(record["id"], build_root=build_root)
        wrapper = cache_root / "draft" / "portal-draft.tex"
        self.assertIn("PassOptionsToPackage{draft}{graphicx}", wrapper.read_text())
        draft_text = subprocess.run(
            ["pdftotext", draft["pdf_path"], "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        self.assertIn("pixel.png", draft_text)
        sentinel = cache_root / "keep-between-incremental-builds"
        sentinel.write_text("cached", encoding="utf-8")

        backend.update_article_settings(
            record["id"],
            {"compile_mode": "normal"},
            build_root=build_root,
        )
        normal = backend.compile_article_pdf(
            record["id"],
            build_root=build_root,
        )
        self.assertEqual(normal["compile_mode"], "normal")
        self.assertTrue(sentinel.is_file())
        normal_text = subprocess.run(
            ["pdftotext", normal["pdf_path"], "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        self.assertNotIn("pixel.png", normal_text)

        backend.compile_article_pdf(
            record["id"],
            build_root=build_root,
            from_scratch=True,
        )
        self.assertFalse(sentinel.exists())

    def test_compile_error_handling_matches_continue_and_stop_modes(self) -> None:
        """LaTeX 出错时 continue 模式仍生成 PDF 并给出警告，stop 模式编译失败且没有 PDF"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Error Modes", "https://example.invalid/article.git")
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "Before \\undefinedportalcommand after.\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "article-build"

        backend.update_article_settings(
            record["id"],
            {"error_handling": "continue"},
            build_root=build_root,
        )
        continued = backend.compile_article_pdf(
            record["id"],
            build_root=build_root,
            from_scratch=True,
        )
        self.assertTrue(continued["pdf_available"])
        self.assertEqual(continued["status"], "warning")
        self.assertGreater(continued["counts"]["error"], 0)

        backend.update_article_settings(
            record["id"],
            {"error_handling": "stop"},
            build_root=build_root,
        )
        with self.assertRaisesRegex(RuntimeError, "LaTeX compilation failed"):
            backend.compile_article_pdf(
                record["id"],
                build_root=build_root,
                from_scratch=True,
            )
        stopped = backend.load_article_build_report(record["id"], build_root=build_root)
        self.assertEqual(stopped["status"], "error")
        self.assertFalse(stopped["pdf_available"])
        self.assertGreater(stopped["counts"]["error"], 0)

    def test_article_workspace_file_operations_and_search(self) -> None:
        """Article 工作区支持新建目录和文件、读写、上传与全文搜索，拒绝越出目录的路径"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Editable Article", "https://example.invalid/article.git")
        record = backend.register_article(
            article_root,
        )
        article_id = record["id"]

        folder = backend.create_article_entry(
            article_id,
            "sections",
            "directory",
        )
        self.assertEqual(folder["type"], "directory")
        created = backend.create_article_entry(
            article_id,
            "sections/method.tex",
            "file",
        )
        self.assertEqual(created["path"], "sections/method.tex")

        saved = backend.write_article_file(
            article_id,
            "sections/method.tex",
            "\\section{Method}\nRuntime rewiring is searchable.\n",
        )
        self.assertGreater(saved["size"], 0)
        content = backend.read_article_file(
            article_id,
            "sections/method.tex",
        )
        self.assertIn("Runtime rewiring", content["content"])

        uploaded = backend.upload_article_file(
            article_id,
            filename="notes.md",
            data=b"# Notes\nworkspace upload\n",
            directory="sections",
        )
        self.assertEqual(uploaded["path"], "sections/notes.md")
        image_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        (article_root / "sections" / "diagram.png").write_bytes(image_bytes)
        image_path = backend.resolve_article_raw_file(
            article_id,
            "sections/diagram.png",
        )
        self.assertEqual(image_path, article_root / "sections" / "diagram.png")
        descriptor = backend.describe_article_file(
            article_id, "sections/diagram.png"
        )
        self.assertEqual(descriptor["mediaType"], "image/png")
        self.assertTrue(descriptor["binary"])
        self.assertNotIn("content", descriptor)
        self.assertIn("/raw?path=sections%2Fdiagram.png", descriptor["rawUrl"])
        with self.assertRaises(ValueError):
            backend.resolve_article_raw_file(
                article_id,
                "../outside.png",
            )
        tree = backend.list_article_files(
            article_id,
            build_root=self.root / "article-build",
        )
        paths = {entry["path"] for entry in tree["entries"]}
        self.assertIn("main.tex", paths)
        self.assertIn("sections/method.tex", paths)
        self.assertIn("sections/notes.md", paths)

        results = backend.search_article_files(
            article_id,
            "runtime rewiring",
        )
        self.assertEqual(results["results"][0]["path"], "sections/method.tex")
        self.assertEqual(results["results"][0]["line"], 2)
        with self.assertRaises(ValueError):
            backend.read_article_file(
                article_id,
                "../outside.txt",
            )

    def test_article_document_descriptors_are_bounded_and_preserve_file_scope(self) -> None:
        """Article 文件描述不读入大文件全文，按内容判断二进制，拒绝越界路径和指向外部的符号链接"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Document Article", "https://example.invalid/article.git")
        article_id = backend.register_article(
            article_root,
        )["id"]
        pdf = article_root / "figure.pdf"
        with pdf.open("wb") as stream:
            stream.write(b"%PDF-1.7\n")
            stream.truncate(20 * 1024 * 1024)
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            descriptor = backend.describe_article_file(
                article_id, "figure.pdf"
            )
        self.assertEqual(descriptor["mediaType"], "application/pdf")
        self.assertEqual(descriptor["size"], 20 * 1024 * 1024)
        self.assertNotIn("content", descriptor)
        self.assertEqual(descriptor["version"], str(pdf.stat().st_mtime_ns))
        self.assertTrue(descriptor["downloadUrl"].endswith("&download=1"))
        source = article_root / "utf8.txt"
        source.write_text("a" * 4095 + "中", encoding="utf-8")
        self.assertFalse(backend.describe_article_file(
            article_id, source.name
        )["binary"])
        (article_root / "unknown.bin").write_bytes(b"\xff\xfe\xfd")
        self.assertTrue(backend.describe_article_file(
            article_id, "unknown.bin"
        )["binary"])
        outside = self.project / "private.pdf"
        outside.write_bytes(b"%PDF-private")
        (article_root / "escape.pdf").symlink_to(outside)
        for path in ("../private.pdf", "escape.pdf", str(outside)):
            for reader in (backend.describe_article_file, backend.resolve_article_raw_file):
                with self.subTest(path=path, reader=reader.__name__), self.assertRaises(ValueError):
                    reader(article_id, path)

    def test_project_semantic_index_covers_all_tex_and_bib_files(self) -> None:
        """Article 语义索引覆盖全部 tex 与 bib 文件，并报告未定义的引用、文献和缺失图片"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Semantic Article", "https://example.invalid/article.git")
        (article_root / "sections").mkdir()
        (article_root / "sections" / "method.tex").write_text(
            "\\section{Method}\\label{sec:method}\n"
            "See \\ref{sec:missing} and \\cite{known,missing-cite}.\n"
            "\\includegraphics{missing-figure}\n",
            encoding="utf-8",
        )
        (article_root / "references.bib").write_text(
            "@article{known,\n"
            "  title={A Known Reference},\n"
            "  author={Ada Example}\n"
            "}\n",
            encoding="utf-8",
        )
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\title{Semantic Article}\n"
            "\\begin{document}\n"
            "\\input{sections/method}\n"
            "\\bibliography{references}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )

        index = backend.article_semantic_index(
            record["id"],
            build_root=self.root / "article-build",
        )

        self.assertEqual(index["primary_file"], "main.tex")
        self.assertIn(
            "sections/method.tex",
            {item["path"] for item in index["documents"]},
        )
        self.assertEqual(index["headings"][0]["title"], "Method")
        self.assertEqual(index["labels"][0]["key"], "sec:method")
        self.assertEqual(index["bibliography"][0]["key"], "known")
        self.assertEqual(index["bibliography"][0]["title"], "A Known Reference")
        problem_kinds = {item["kind"] for item in index["problems"]}
        self.assertIn("undefined_reference", problem_kinds)
        self.assertIn("undefined_citation", problem_kinds)
        self.assertIn("missing_figure", problem_kinds)
        self.assertTrue(
            next(
                item
                for item in index["documents"]
                if item["path"] == "sections/method.tex"
            )["reachable_from_primary"]
        )

    def test_compile_generates_synctex_and_supports_both_directions(self) -> None:
        """编译生成 SyncTeX，支持从源码到 PDF 和从 PDF 到源码的双向定位"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Synchronized Article", "https://example.invalid/article.git")
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "article-build"
        backend.compile_article_pdf(
            record["id"],
            build_root=build_root,
            from_scratch=True,
        )

        forward = backend.article_source_to_pdf(
            record["id"],
            path="main.tex",
            line=6,
            column=2,
            build_root=build_root,
        )

        self.assertEqual(forward["direction"], "source-to-pdf")
        self.assertGreaterEqual(forward["match"]["page"], 1)
        reverse = backend.article_pdf_to_source(
            record["id"],
            page=forward["match"]["page"],
            x=forward["match"]["x"],
            y=forward["match"]["y"],
            build_root=build_root,
        )
        self.assertEqual(reverse["direction"], "pdf-to-source")
        self.assertEqual(reverse["match"]["path"], "main.tex")
        self.assertGreaterEqual(reverse["match"]["line"], 1)

    def test_texlab_session_returns_symbols_and_citation_completion(self) -> None:
        """TexLab 会话返回文档符号和文献引用补全"""
        try:
            article_texlab_backend._texlab_command()
        except RuntimeError:
            self.skipTest("TexLab is not installed")
        article_root = self.project / "article"
        _make_article_repo(article_root, "TexLab Article", "https://example.invalid/article.git")
        (article_root / "references.bib").write_text(
            "@article{known,\n  title={Known Reference}\n}\n",
            encoding="utf-8",
        )
        main_path = article_root / "main.tex"
        main_path.write_text(
            main_path.read_text(encoding="utf-8").replace(
                "Intro body.",
                "Intro body \\cite{known}.\n\\bibliography{references}",
            ),
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        content = main_path.read_text(encoding="utf-8")

        status = article_texlab_backend.article_texlab_request(
            record["id"],
            {"action": "status"},
        )
        symbols = article_texlab_backend.article_texlab_request(
            record["id"],
            {
                "action": "document_symbols",
                "path": "main.tex",
                "content": content,
                "version": 1,
            },
        )
        cursor_offset = content.index("\\cite{") + len("\\cite{")
        line = content.count("\n", 0, cursor_offset)
        line_start = content.rfind("\n", 0, cursor_offset) + 1
        completion = article_texlab_backend.article_texlab_request(
            record["id"],
            {
                "action": "completion",
                "path": "main.tex",
                "content": content,
                "version": 2,
                "line": line,
                "character": cursor_offset - line_start,
                "trigger_kind": 1,
            },
        )

        self.assertTrue(status["available"])
        self.assertTrue(symbols["available"])
        self.assertTrue(
            any("Introduction" in item["name"] for item in symbols["symbols"])
        )
        self.assertTrue(completion["available"])
        self.assertIn(
            "known",
            {
                str(item.get("label") or item.get("insertText") or "")
                for item in completion["items"]
            },
        )

    def test_article_compiler_settings_are_persistent_and_validated(self) -> None:
        """Article 编译设置持久保存且可部分更新，未知编译器被拒绝"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Configured Article", "https://example.invalid/article.git")
        (article_root / "alternate.tex").write_text(
            "\\documentclass{article}\\begin{document}Alternate\\end{document}",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "article-build"

        settings = backend.update_article_settings(
            record["id"],
            {"compiler": "xelatex", "primary_file": "alternate.tex"},
            build_root=build_root,
        )
        self.assertEqual(settings["compiler"], "xelatex")
        self.assertEqual(settings["primary_file"], "alternate.tex")
        self.assertFalse(settings["auto_compile"])
        self.assertEqual(settings["compile_mode"], "normal")
        self.assertTrue(settings["syntax_check"])
        self.assertEqual(settings["error_handling"], "continue")
        settings = backend.update_article_settings(
            record["id"],
            {
                "auto_compile": True,
                "compile_mode": "draft",
                "syntax_check": False,
                "error_handling": "stop",
            },
            build_root=build_root,
        )
        self.assertEqual(settings["compiler"], "xelatex")
        self.assertEqual(settings["primary_file"], "alternate.tex")
        self.assertTrue(settings["auto_compile"])
        self.assertEqual(settings["compile_mode"], "draft")
        self.assertFalse(settings["syntax_check"])
        self.assertEqual(settings["error_handling"], "stop")
        loaded = backend.load_article_settings(
            record["id"],
            build_root=build_root,
        )
        self.assertEqual(loaded["compiler_label"], "XeLaTeX")
        self.assertEqual(
            backend.article_build_store.settings(record["id"])["compiler"],
            loaded["compiler"],
        )

        with self.assertRaises(ValueError):
            backend.update_article_settings(
                record["id"],
                {"compiler": "unknown", "primary_file": "main.tex"},
                build_root=build_root,
            )

    def test_running_article_compile_can_be_stopped(self) -> None:
        """运行中的 Article 构建命令可被停止"""
        article_id = "article_0123456789ab"
        outcome: list[BaseException] = []
        backend._begin_article_build(article_id)

        def run() -> None:
            try:
                backend._run_article_build_command(
                    article_id,
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    cwd=self.root,
                    timeout_seconds=35,
                )
            except BaseException as exc:
                outcome.append(exc)
            finally:
                backend._finish_article_build(article_id)

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with backend._ACTIVE_ARTICLE_BUILDS_LOCK:
                if article_id in backend._ACTIVE_ARTICLE_BUILDS:
                    break
            time.sleep(0.01)

        stopped = backend.stop_article_compile(article_id)
        thread.join(timeout=5)

        self.assertTrue(stopped["stop_requested"])
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], backend.ArticleCompilationStopped)

    def test_running_latex_compile_can_be_stopped(self) -> None:
        """运行中的 LaTeX 编译可被停止，构建报告记为已停止且没有 PDF"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Stoppable Article", "https://example.invalid/article.git")
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\newcount\\portalcount\n"
            "\\loop\\advance\\portalcount by 1 "
            "\\ifnum\\portalcount<2000000000\\repeat\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        article_id = record["id"]
        build_root = self.root / "article-build"
        outcome: list[BaseException] = []

        def run() -> None:
            try:
                backend.compile_article_pdf(
                    article_id,
                    build_root=build_root,
                    timeout_seconds=35,
                    from_scratch=True,
                )
            except BaseException as exc:
                outcome.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with backend._ACTIVE_ARTICLE_BUILDS_LOCK:
                if article_id in backend._ACTIVE_ARTICLE_BUILDS:
                    break
            time.sleep(0.01)

        stopped = backend.stop_article_compile(article_id)
        thread.join(timeout=6)

        self.assertTrue(stopped["stop_requested"])
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], backend.ArticleCompilationStopped)
        report = backend.load_article_build_report(article_id, build_root=build_root)
        self.assertEqual(report["status"], "stopped")
        self.assertFalse(report["pdf_available"])

    def test_latex_diagnostics_extract_errors_and_locations(self) -> None:
        """LaTeX 日志解析出错误与警告及其文件、行号和类别，忽略 Info 信息"""
        diagnostics = backend.parse_latex_diagnostics(
            "! LaTeX Error: File `missing.sty' not found.\n"
            "./main.tex:42: Misplaced alignment tab character &.\n"
            "Package natbib Warning: Citation `demo' undefined.\n"
            "Package pdftexcmds Info: \\pdfdraftmode not found.\n"
        )
        self.assertEqual(diagnostics[0]["severity"], "error")
        self.assertEqual(diagnostics[1]["file"], "main.tex")
        self.assertEqual(diagnostics[1]["line"], 42)
        self.assertEqual(diagnostics[1]["severity"], "error")
        self.assertEqual(diagnostics[2]["severity"], "warning")
        self.assertEqual(diagnostics[0]["category"], "dependency")
        self.assertEqual(diagnostics[2]["category"], "reference")
        self.assertEqual(len(diagnostics), 3)

    def test_build_preflight_exposes_ambiguous_main_documents(self) -> None:
        """构建预检发现多个候选主文档时要求选择，指定主文档后不再提示"""
        article_root = self.project / "article"
        article_root.mkdir()
        for name in ("paper.tex", "supplement.tex"):
            (article_root / name).write_text(
                "\\documentclass{article}\n\\begin{document}x\\end{document}\n",
                encoding="utf-8",
            )

        preflight = backend.inspect_article_build_profile(
            article_root,
        )

        self.assertEqual(len(preflight["candidates"]), 2)
        self.assertTrue(preflight["requires_selection"])
        self.assertEqual(preflight["status"], "warning")
        self.assertIn("请确认主文档", preflight["issues"][0]["message"])

        selected = backend.inspect_article_build_profile(
            article_root,
            primary_file="supplement.tex",
            compiler="pdflatex",
        )
        self.assertFalse(selected["requires_selection"])
        self.assertEqual(selected["primary_file"], "supplement.tex")

    def test_registration_preflight_allows_missing_tex_tools(self) -> None:
        article_root = self.project / "article"
        article_root.mkdir()
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}x\\end{document}\n",
            encoding="utf-8",
        )
        with mock.patch.object(backend, "_tex_which", return_value=""):
            registration = backend.inspect_article_build_profile(
                article_root, require_toolchain=False
            )
            build = backend.inspect_article_build_profile(article_root)
        self.assertNotEqual(registration["status"], "blocked")
        self.assertEqual(build["status"], "blocked")
        self.assertTrue(any("latexmk" in item["message"] for item in build["issues"]))

    def test_build_preflight_reports_missing_latex_dependency(self) -> None:
        """构建预检报告缺失的 LaTeX 宏包和字体并阻止构建"""
        article_root = self.project / "article"
        article_root.mkdir()
        (article_root / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\usepackage{tfi_package_that_does_not_exist}\n"
            "\\setmainfont{TFI Portal Typeface That Does Not Exist}\n"
            "\\begin{document}x\\end{document}\n",
            encoding="utf-8",
        )

        preflight = backend.inspect_article_build_profile(
            article_root,
            primary_file="main.tex",
        )

        self.assertEqual(preflight["status"], "blocked")
        missing = [item for item in preflight["issues"] if item["category"] == "dependency"]
        self.assertEqual(len(missing), 1)
        self.assertIn("tfi_package_that_does_not_exist.sty", missing[0]["message"])
        missing_fonts = [item for item in preflight["issues"] if item["category"] == "font"]
        self.assertEqual(len(missing_fonts), 1)
        self.assertIn("TFI Portal Typeface That Does Not Exist", missing_fonts[0]["message"])

    def test_build_settings_reject_input_fragments_as_main_documents(self) -> None:
        """构建设置拒绝把没有 documentclass 声明的片段文件设为主文档"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Standalone Main", "https://example.invalid/main.git")
        (article_root / "body.tex").write_text(
            "\\section{Body fragment}\n",
            encoding="utf-8",
        )
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "build"

        settings = backend.load_article_settings(
            record["id"],
            build_root=build_root,
        )
        self.assertEqual(settings["primary_candidates"], ["main.tex"])
        with self.assertRaisesRegex(ValueError, r"must contain \\documentclass"):
            backend.update_article_settings(
                record["id"],
                {"primary_file": "body.tex"},
                build_root=build_root,
            )

    def test_build_profile_controls_project_rc_shell_escape_and_final_log(self) -> None:
        """构建配置控制是否使用项目 latexmkrc 和 shell-escape，诊断只取最终日志"""
        article_root = self.project / "article"
        _make_article_repo(article_root, "Build Profile", "https://example.invalid/build.git")
        (article_root / ".latexmkrc").write_text("$max_repeat = 7;\n", encoding="utf-8")
        record = backend.register_article(
            article_root,
        )
        build_root = self.root / "build"
        settings = backend.update_article_settings(
            record["id"],
            {"use_project_latexmkrc": True, "shell_escape": True},
            build_root=build_root,
        )
        self.assertEqual(settings["schema"], backend.ARTICLE_BUILD_PROFILE_SCHEMA)
        self.assertTrue(settings["use_project_latexmkrc"])
        self.assertTrue(settings["shell_escape"])

        captured: list[str] = []

        def fake_build(_article_id, command, *, cwd, timeout_seconds, env=None):
            del cwd, timeout_seconds
            captured.extend(command)
            self.assertEqual(env["TZ"], "UTC")
            outdir = Path(next(value.split("=", 1)[1] for value in command if value.startswith("-outdir=")))
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "main.pdf").write_bytes(b"%PDF-1.4\n")
            (outdir / "main.log").write_text("Output written on main.pdf.\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "LaTeX Warning: Citation `old' undefined.\n", None)

        with mock.patch.object(backend, "_run_article_build_command", side_effect=fake_build):
            report = backend.compile_article_pdf(
                record["id"],
                build_root=build_root,
            )

        self.assertIn("-r", captured)
        self.assertIn(str(article_root / ".latexmkrc"), captured)
        self.assertTrue(any("-shell-escape" in value for value in captured))
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["environment_profile"], settings["environment_profile"])


if __name__ == "__main__":
    unittest.main()
