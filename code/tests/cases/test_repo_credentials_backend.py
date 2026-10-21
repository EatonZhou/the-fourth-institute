from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from management_portal import repo_credentials_backend


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "credentials", tmp_path / "credential.master-key"


def test_repo_credentials_are_encrypted_and_isolated_by_portal_user(
    tmp_path: Path,
) -> None:
    """仓库凭证加密保存并按 Portal 用户隔离，可标为失效、替换和删除"""
    root, key_path = _paths(tmp_path)
    context = {
        "protocol": "https",
        "host": "github.com",
        "username": "",
        "auth_type": "token",
    }

    saved = repo_credentials_backend.save_credential(
        "sample_admin",
        context,
        username="octocat",
        secret="github_pat_fixture_value",
        root=root,
        key_path=key_path,
    )

    assert saved["status"] == "active"
    assert saved["username"] == "octocat"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    encrypted_files = list(root.rglob("*.credential"))
    assert len(encrypted_files) == 1
    assert stat.S_IMODE(encrypted_files[0].stat().st_mode) == 0o600
    assert b"github_pat_fixture_value" not in encrypted_files[0].read_bytes()

    listed = repo_credentials_backend.list_credentials(
        "sample_admin",
        root=root,
        key_path=key_path,
    )
    assert listed["credentials"] == [saved]
    assert "secret" not in listed["credentials"][0]
    assert repo_credentials_backend.list_credentials(
        "another-user",
        root=root,
        key_path=key_path,
    )["credentials"] == []

    found = repo_credentials_backend.find_credential(
        "sample_admin",
        context,
        root=root,
        key_path=key_path,
    )
    assert found is not None
    assert found["secret"] == "github_pat_fixture_value"

    invalid = repo_credentials_backend.mark_credential_invalid(
        "sample_admin",
        saved["credential_id"],
        root=root,
        key_path=key_path,
    )
    assert invalid is not None
    assert invalid["status"] == "invalid"
    assert repo_credentials_backend.find_credential(
        "sample_admin",
        context,
        root=root,
        key_path=key_path,
    ) is None

    replaced = repo_credentials_backend.save_credential(
        "sample_admin",
        context,
        username="octocat",
        secret="github_pat_replacement",
        root=root,
        key_path=key_path,
    )
    assert replaced["credential_id"] == saved["credential_id"]
    assert replaced["status"] == "active"

    deleted = repo_credentials_backend.delete_credential(
        "sample_admin",
        saved["credential_id"],
        root=root,
    )
    assert deleted["deleted"] is True
    assert repo_credentials_backend.list_credentials(
        "sample_admin",
        root=root,
        key_path=key_path,
    )["credentials"] == []


def test_git_credential_helper_reads_only_private_matching_material(
    tmp_path: Path,
) -> None:
    """git 凭证助手只对主机匹配且权限为 600 的凭证文件给出账号密码"""
    material = tmp_path / "material.json"
    material.write_text(
        json.dumps(
            {
                "protocol": "https",
                "host": "github.com",
                "username": "octocat",
                "secret": "fixture-token",
            }
        ),
        encoding="utf-8",
    )
    material.chmod(0o600)
    helper = (
        Path(__file__).resolve().parents[2]
        / "management_portal"
        / "git_credential_portal.py"
    )
    environment = {
        **os.environ,
        "PORTAL_GIT_CREDENTIAL_FILE": str(material),
    }

    matched = subprocess.run(
        [str(helper), "get"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )
    assert "username=octocat" in matched.stdout
    assert "password=fixture-token" in matched.stdout

    mismatched = subprocess.run(
        [str(helper), "get"],
        input="protocol=https\nhost=gitee.com\n\n",
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )
    assert mismatched.stdout.strip() == "quit=true"

    material.chmod(0o644)
    insecure = subprocess.run(
        [str(helper), "get"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )
    assert insecure.stdout.strip() == "quit=true"


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
