from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from management_portal import resource_identity_backend as backend


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _make_repo(path: Path, origin: str, *, upstream: str = "") -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.name", "TFI Portal Test")
    _git(path, "config", "user.email", "tfi-test@example.invalid")
    (path / "README.md").write_text(f"# {path.name}\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    _git(path, "remote", "add", "origin", origin)
    if upstream:
        _git(path, "remote", "add", "upstream", upstream)
    return _git(path, "rev-parse", "HEAD")


class ResourceIdentityBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scan_groups_clones_and_is_idempotent(self) -> None:
        """仓库扫描合并同源克隆且重复同步不产生变更"""
        shared_origin = "https://example.invalid/team/shared.git"
        first = self.root / "first"
        second = self.root / "second"
        _make_repo(first, shared_origin)
        _make_repo(second, shared_origin)
        invalid = self.root / "invalid" / ".git"
        invalid.mkdir(parents=True)

        report = backend.scan_and_sync_repo_registry(
            [self.root], apply=True
        )
        self.assertEqual(report["checkout_count"], 2)
        self.assertEqual(report["logical_repo_count"], 1)
        self.assertEqual(report["created_count"], 1)
        self.assertEqual(report["invalid_marker_count"], 1)
        record = backend.load_repo_records()[0]
        self.assertRegex(record["id"], r"^repo_[0-9a-f]{12}$")
        self.assertEqual(len(record["checkouts"]), 2)

        repeated = backend.scan_and_sync_repo_registry(
            [self.root], apply=True
        )
        self.assertEqual(repeated["created_count"], 0)
        self.assertEqual(repeated["updated_count"], 0)
        self.assertEqual(repeated["unchanged_count"], 1)

    def test_forks_with_distinct_origins_are_not_merged_by_upstream(self) -> None:
        """origin 不同的分叉仓库不因共用 upstream 合并"""
        upstream = "https://example.invalid/upstream/project.git"
        _make_repo(self.root / "fork-a", "https://example.invalid/a/project.git", upstream=upstream)
        _make_repo(self.root / "fork-b", "https://example.invalid/b/project.git", upstream=upstream)
        report = backend.scan_and_sync_repo_registry(
            [self.root], apply=False
        )
        self.assertEqual(report["logical_repo_count"], 2)
        self.assertEqual(backend.load_repo_records(), [])

    def test_register_and_resolve_repo_file_and_commit_file(self) -> None:
        """仓库登记后可解析当前与历史文件且拒绝路径越界"""
        checkout = self.root / "repo"
        commit = _make_repo(checkout, "https://example.invalid/team/repo.git")
        record = backend.register_repo_checkout(checkout)
        repo_id = record["id"]

        root = backend.resolve_repo_address(repo_id)
        self.assertEqual(root["target_kind"], "repo")
        self.assertEqual(root["checkout_path"], str(checkout))

        file_result = backend.resolve_repo_address(
            f"{repo_id}/file/README.md"
        )
        self.assertTrue(file_result["exists"])
        data, content_type, _ = backend.read_resolved_resource_content(
            file_result, address=f"{repo_id}/file/README.md"
        )
        self.assertIn(b"# repo", data)
        self.assertEqual(content_type, "text/markdown")

        historical_address = f"{repo_id}/commit/{commit}/file/README.md"
        historical = backend.resolve_repo_address(
            historical_address
        )
        self.assertTrue(historical["exists"])
        historical_data, _, _ = backend.read_resolved_resource_content(
            historical, address=historical_address
        )
        self.assertEqual(data, historical_data)

        with self.assertRaises(ValueError):
            backend.resolve_repo_address(
                f"{repo_id}/file/../outside.txt"
            )

    def test_alternate_remote_mirror_can_attach_to_existing_repo_id(self) -> None:
        """不同远端镜像可附加到已有仓库标识"""
        primary = self.root / "primary"
        mirror = self.root / "mirror"
        _make_repo(primary, "https://example.invalid/team/project.git")
        _make_repo(mirror, "ssh://mirror.example.invalid/team/project.git")
        record = backend.register_repo_checkout(primary)

        attached = backend.register_repo_checkout(
            mirror,
            repo_id=record["id"],
        )
        self.assertEqual(attached["id"], record["id"])
        self.assertEqual(len(attached["checkouts"]), 2)
        self.assertEqual(len(backend.load_repo_records()), 1)

    def test_checkout_missing_from_a_later_scan_keeps_its_repo_entry(self) -> None:
        """扫描不再找到的 checkout 仍留在原 Repo

        两个同源 clone 登记为一个 Repo 后删除其中一个目录再扫描：Repo ID 不变，被删的 checkout 留在登记中且 present 为 false。
        """
        origin = "https://example.invalid/team/moved.git"
        kept = self.root / "kept"
        moved = self.root / "moved"
        _make_repo(kept, origin)
        _make_repo(moved, origin)
        first = backend.scan_and_sync_repo_registry([self.root], apply=True)["created"][0]

        shutil.rmtree(moved)
        report = backend.scan_and_sync_repo_registry([self.root], apply=True)

        self.assertEqual(report["updated_count"], 1)
        record = backend.get_repo_record(first["id"])
        presence = {Path(item["path"]).name: item["present"] for item in record["checkouts"]}
        self.assertEqual(presence, {"kept": True, "moved": False})

    def test_checkout_matching_two_repos_is_rejected(self) -> None:
        """同时对上两个 Repo 的 checkout 被拒绝登记

        checkout 的路径属于一个 Repo、身份远端属于另一个 Repo 时，登记抛出 matches multiple repo ids，登记表保持两条记录。
        """
        first = self.root / "first"
        second = self.root / "second"
        _make_repo(first, "https://example.invalid/team/first.git")
        _make_repo(second, "https://example.invalid/team/second.git")
        backend.register_repo_checkout(first)
        backend.register_repo_checkout(second)
        # The second checkout now carries the first one's identity remote.
        _git(second, "remote", "set-url", "origin", "https://example.invalid/team/first.git")

        with self.assertRaisesRegex(ValueError, "checkout group matches multiple repo ids"):
            backend.register_repo_checkout(second)
        self.assertEqual(len(backend.load_repo_records()), 2)

    def test_fresh_unborn_repo_is_registered_before_first_commit(self) -> None:
        """新仓库可在首次提交前登记并解析"""
        checkout = self.root / "new-repo"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)

        record = backend.register_repo_checkout(
            checkout,
            project_ids=["NewProject"],
        )
        self.assertRegex(record["id"], r"^repo_[0-9a-f]{12}$")
        self.assertEqual(record["checkouts"][0]["head"], "")
        self.assertEqual(record["project_ids"], ["NewProject"])
        self.assertTrue(
            backend.resolve_repo_address(
                record["id"]
            )["exists"]
        )

    def test_non_git_directory_requires_explicit_initialization(self) -> None:
        """普通目录必须显式初始化 Git 才能登记"""
        checkout = self.root / "plain-directory"
        checkout.mkdir()

        with self.assertRaises(backend.GitCheckoutRequired):
            backend.register_repo_checkout(checkout)

        backend.initialize_git_checkout(checkout)
        record = backend.register_repo_checkout(
            checkout
        )
        self.assertTrue((checkout / ".git").is_dir())
        self.assertEqual(record["canonical_checkout"], str(checkout))

    def test_discovery_excludes_article_git_roots(self) -> None:
        """仓库发现与登记扫描跳过论文 Git 根目录"""
        code = self.root / "code"
        article = self.root / "article"
        _make_repo(code, "https://example.invalid/team/code.git")
        _make_repo(article, "https://git.overleaf.com/opaque.git")

        checkouts, invalid = backend.discover_git_checkouts(
            [self.root],
            excluded_roots=[article],
        )

        self.assertEqual([item["path"] for item in checkouts], [str(code)])
        self.assertEqual(invalid, [])

        report = backend.scan_and_sync_repo_registry(
            [self.root],
            excluded_roots=[article],
            apply=True,
        )
        self.assertEqual(report["checkout_count"], 1)
        self.assertEqual(
            backend.load_repo_records()[0]["canonical_checkout"],
            str(code),
        )

    def test_update_remote_sets_fetch_and_push_urls(self) -> None:
        """更新仓库远端同时设置拉取与推送地址"""
        checkout = self.root / "repo"
        _make_repo(checkout, "https://example.invalid/old.git")
        record = backend.register_repo_checkout(
            checkout
        )

        updated = backend.update_repo_remote(
            record["id"],
            name="origin",
            url="https://example.invalid/new.git",
            push_url="git@example.invalid:team/new.git",
        )

        self.assertEqual(
            _git(checkout, "remote", "get-url", "origin"),
            "https://example.invalid/new.git",
        )
        self.assertEqual(
            _git(checkout, "remote", "get-url", "--push", "origin"),
            "git@example.invalid:team/new.git",
        )
        self.assertEqual(len(updated["remotes"]), 1)
        self.assertEqual(updated["remotes"][0]["push_url"], "git@example.invalid:team/new.git")

    def test_generated_description_is_persisted_across_registry_sync(self) -> None:
        """生成的仓库描述与来源信息在登记同步后保留"""
        checkout = self.root / "described-repo"
        _make_repo(checkout, "https://example.invalid/team/described-repo.git")
        record = backend.register_repo_checkout(
            checkout,
        )

        updated = backend.update_repo_description(
            record["id"],
            "为团队提供可复用的仓库描述生成流程。",
            source_signature="a" * 64,
            provider="kimi_primary",
            model="kimi-k2.6",
        )

        self.assertEqual(
            updated["description"],
            "为团队提供可复用的仓库描述生成流程。",
        )
        self.assertEqual(
            updated["description_generation"]["provider"],
            "kimi_primary",
        )
        repeated = backend.scan_and_sync_repo_registry(
            [self.root],
            apply=True,
        )
        self.assertEqual(repeated["updated_count"], 0)
        persisted = backend.get_repo_record(record["id"])
        self.assertEqual(
            persisted["description"],
            "为团队提供可复用的仓库描述生成流程。",
        )
        self.assertEqual(
            persisted["description_generation"]["source_signature"],
            "a" * 64,
        )

    def test_unregister_removes_only_the_registry_record(self) -> None:
        """注销仓库仅删除登记记录并保留目录文件"""
        checkout = self.root / "registered-repo"
        _make_repo(checkout, "https://example.invalid/team/registered-repo.git")
        record = backend.register_repo_checkout(checkout)

        result = backend.unregister_repo(record["id"])

        self.assertTrue(result["deleted"])
        self.assertEqual(result["checkout_paths"], [str(checkout)])
        self.assertTrue(checkout.is_dir())
        self.assertTrue((checkout / "README.md").is_file())
        self.assertEqual(backend.load_repo_records(), [])


if __name__ == "__main__":
    unittest.main()
