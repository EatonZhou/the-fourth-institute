"""Network Git (fetch, pull, push): SSH keys, credential prompts and a user's saved Git credentials."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from management_common import file_permissions
from management_common.product_environment import mirror_product_env
from management_common.text_values import stripped_text
from management_portal import repo_credentials_backend, resource_identity_backend
from management_portal.repo_workspace_checkout import RepoWorkspaceConflict, _checkout
from management_portal.repo_workspace_git import (
    GitAuthenticationRequired, GitOperationError, _git, _redact_text,
)
from management_portal.repo_workspace_state import _remote_auth_context, _remote_records


_GIT_CREDENTIAL_HELPER = Path(__file__).with_name("git_credential_portal.py")
_GIT_SSH_ASKPASS = Path(__file__).with_name("git_ssh_askpass_portal.py")
_SSH_KEY_ROOT = resource_identity_backend.DEFAULT_SSH_KEY_ROOT


def _credential_challenge(
    *,
    repo_id: str,
    remote: str,
    branch: str,
    operation: str,
    context: dict[str, str],
    reason: str,
    rejected: bool,
) -> dict[str, Any]:
    protocol = context.get("protocol") or ""
    provider = context.get("provider") or "generic"
    if protocol == "ssh":
        secret_label = "SSH 私钥口令或服务器密码"
        help_text = (
            "Portal 会先使用当前用户可用的 SSH key；加密私钥或密码认证需要在这里补充凭据。"
        )
    elif provider == "github":
        secret_label = "Personal access token"
        help_text = "GitHub 的 HTTPS Git 操作需要访问令牌，不能使用 GitHub 登录密码。"
    else:
        secret_label = "访问令牌或密码"
        help_text = "优先使用 Git 服务签发的访问令牌；仅在服务明确支持时使用账号密码。"
    return {
        "kind": "git_auth_required",
        "repo_id": repo_id,
        "remote": remote,
        "branch": branch,
        "operation": operation,
        "protocol": protocol,
        "host": context.get("host") or "",
        "path": context.get("path") or "",
        "username": context.get("username") or "",
        "auth_type": context.get("auth_type") or "",
        "provider": provider,
        "secret_label": secret_label,
        "help_text": help_text,
        "reason": reason,
        "rejected": bool(rejected),
        "remember_default": True,
    }


def _git_failure_category(message: object, *, protocol: str) -> str:
    value = str(message or "").casefold()
    permission_markers = (
        "protected branch",
        "pre-receive hook declined",
        "remote rejected",
        "permission to ",
        "write access to repository not granted",
        "you are not allowed to push",
        "requested url returned error: 403",
    )
    if any(marker in value for marker in permission_markers):
        return "permission_denied"
    auth_markers = (
        "authentication failed",
        "invalid username or password",
        "invalid username or token",
        "could not read username",
        "could not read password",
        "terminal prompts disabled",
        "requested url returned error: 401",
        "http basic: access denied",
        "permission denied (publickey",
        "permission denied (password",
        "no supported authentication methods available",
    )
    if any(marker in value for marker in auth_markers):
        return "authentication"
    if protocol == "ssh" and "permission denied" in value:
        return "authentication"
    return "other"


def _git_write_access_denied(message: object) -> bool:
    """Identify an explicit repository-level write permission rejection."""
    value = str(message or "").casefold()
    return any(
        marker in value
        for marker in (
            "permission to ",
            "write access to repository not granted",
            "you are not allowed to push",
            "requested url returned error: 403",
        )
    )


def _available_ssh_keys() -> list[str]:
    try:
        candidates = list(_SSH_KEY_ROOT.iterdir())
    except OSError:
        return []
    return sorted(
        path.name
        for path in candidates
        if path.is_file()
        and path.name.startswith("id_")
        and not path.name.endswith(".pub")
        and (_SSH_KEY_ROOT / f"{path.name}.pub").is_file()
    )


def _repo_ssh_identity(record: dict[str, Any]) -> Path | None:
    key_name = stripped_text(record.get("ssh_key"))
    if not key_name:
        return None
    if key_name not in _available_ssh_keys():
        raise RepoWorkspaceConflict(
            f"Repo 设置的 SSH 密钥不存在：{key_name}"
        )
    return _SSH_KEY_ROOT / key_name


def _ssh_executable(*, needs_askpass: bool) -> str:
    if sys.platform != "win32" or not needs_askpass:
        return "ssh"
    # Native Win32 OpenSSH uses CreateProcess for askpass, which cannot run a
    # Python/shebang script. Use the paired POSIX tools shipped with Git for
    # Windows so the private shell launcher has defined execution semantics.
    git = shutil.which("git")
    if git:
        for root in Path(git).resolve().parents:
            ssh = root / "usr" / "bin" / "ssh.exe"
            shell = root / "usr" / "bin" / "sh.exe"
            if ssh.is_file() and shell.is_file():
                return ssh.as_posix()
    raise GitOperationError("Windows SSH password authentication requires Git for Windows with bundled ssh and sh.")


@contextmanager
def _git_askpass_launcher():
    descriptor, raw_path = tempfile.mkstemp(prefix="portal-git-askpass-", suffix=".sh")
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            file_permissions.set_created_file_permissions(path, 0o700)
            command = shlex.join([Path(sys.executable).as_posix(), _GIT_SSH_ASKPASS.as_posix()])
            # No credential or untrusted prompt is embedded or forwarded.
            stream.write(("#!/bin/sh\nexec " + command + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)


def _ssh_command(
    *,
    identity_file: Path | None,
    batch_mode: bool,
) -> str:
    command = [
        _ssh_executable(needs_askpass=not batch_mode),
        "-o",
        f"BatchMode={'yes' if batch_mode else 'no'}",
        "-o",
        f"NumberOfPasswordPrompts={0 if batch_mode else 1}",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if identity_file is not None:
        command.extend(
            [
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(identity_file),
            ]
        )
    return shlex.join(command)


@contextmanager
def _git_credential_material(
    context: dict[str, str],
    *,
    username: str,
    secret: str,
    valid_for_seconds: int = 120,
):
    descriptor, raw_path = tempfile.mkstemp(
        prefix="tfi-git-credential-",
        suffix=".json",
    )
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            file_permissions.set_created_file_permissions(path, 0o600)
            json.dump(
                {
                    "protocol": context.get("protocol") or "",
                    "host": context.get("host") or "",
                    "path": context.get("path") or "",
                    "expires_at": time.time() + max(1, valid_for_seconds),
                    "username": username,
                    "secret": secret,
                },
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)


def _supplied_git_credential(
    credential: dict[str, Any] | None,
    context: dict[str, str],
    protocol: str,
) -> tuple[str, str]:
    """Check a credential typed for this operation; return its username and secret."""
    supplied = dict(credential or {})
    supplied_secret = str(supplied.get("secret") or "")
    supplied_username = str(
        supplied.get("username") or context.get("username") or ""
    ).strip()
    if supplied_secret and protocol == "http":
        raise GitOperationError(
            "当前 remote 使用明文 HTTP。请先改成 HTTPS 或 SSH，再保存和使用 Git 凭据。"
        )
    if supplied_secret:
        if len(supplied_secret) > 16_384 or any(
            char in supplied_secret for char in "\r\n\0"
        ):
            raise ValueError("Git 凭据包含不支持的字符。")
        if protocol == "https" and not supplied_username:
            raise ValueError("HTTPS Git 凭据需要用户名。")
        if protocol == "ssh" and not supplied_username:
            supplied_username = "git"
    return supplied_username, supplied_secret


def _git_without_credential(
    checkout: Path,
    args: tuple[str, ...],
    *,
    protocol: str,
    context: dict[str, str],
    challenge_target: dict[str, str],
    git_config: list[str],
    timeout: int,
    ssh_identity_file: Path | None,
) -> str:
    """Run without a Portal credential (SSH may use the account's keys); an authentication failure asks for one."""
    environment: dict[str, str] = {}
    if protocol == "ssh":
        environment["GIT_SSH_COMMAND"] = _ssh_command(
            identity_file=ssh_identity_file,
            batch_mode=True,
        )
    try:
        return _git(
            checkout,
            *args,
            timeout=timeout,
            git_config=git_config,
            env_overrides=environment,
        )
    except GitOperationError as exc:
        safe_message = _redact_text(str(exc))[:800]
        if (
            _git_failure_category(safe_message, protocol=protocol)
            == "authentication"
            and protocol in {"http", "https", "ssh"}
        ):
            if protocol == "http":
                raise GitOperationError(
                    "当前 remote 使用明文 HTTP，Portal 不会提示或保存凭据。请改成 HTTPS 或 SSH。",
                    returncode=exc.returncode,
                ) from exc
            challenge = _credential_challenge(
                **challenge_target,
                context=context,
                reason=safe_message,
                rejected=False,
            )
            raise GitAuthenticationRequired(
                "需要 Git 认证后才能继续。",
                challenge=challenge,
                returncode=exc.returncode,
            ) from exc
        raise


def _git_with_credential(
    checkout: Path,
    args: tuple[str, ...],
    *,
    protocol: str,
    context: dict[str, str],
    challenge_target: dict[str, str],
    username: str,
    secret: str,
    saved: dict[str, Any] | None,
    actor: str,
    git_config: list[str],
    timeout: int,
    ssh_identity_file: Path | None,
    credential_root: str | Path | None,
    credential_key_path: str | Path | None,
) -> str:
    """Run with a short-lived credential file; a rejection marks a saved credential invalid."""
    environment: dict[str, str] = {}
    # Context-managed material is removed in every success and failure path.
    with _git_credential_material(
        context,
        username=username,
        secret=secret,
        valid_for_seconds=timeout,
    ) as material_path, ExitStack() as launchers:
        mirror_product_env(environment, "PORTAL_GIT_CREDENTIAL_FILE", str(material_path))
        if protocol == "https":
            git_config.append(
                "credential.helper=!" + shlex.join([Path(sys.executable).as_posix(), _GIT_CREDENTIAL_HELPER.as_posix()])
            )
        elif protocol == "ssh":
            environment.update(
                {
                    "DISPLAY": os.environ.get("DISPLAY")
                    or "tfi-portal",
                    "SSH_ASKPASS_REQUIRE": "force",
                    "SSH_ASKPASS": launchers.enter_context(_git_askpass_launcher()).as_posix(),
                    "GIT_SSH_COMMAND": _ssh_command(
                        identity_file=ssh_identity_file,
                        batch_mode=False,
                    ),
                }
            )
        try:
            return _git(
                checkout,
                *args,
                timeout=timeout,
                git_config=git_config,
                env_overrides=environment,
            )
        except GitOperationError as exc:
            safe_message = _redact_text(str(exc)).replace(
                secret,
                "[credential]",
            )[:800]
            if (
                _git_failure_category(safe_message, protocol=protocol)
                == "authentication"
            ):
                if saved and actor:
                    repo_credentials_backend.mark_credential_invalid(
                        actor,
                        str(saved.get("credential_id") or ""),
                        root=credential_root,
                        key_path=credential_key_path,
                    )
                challenge = _credential_challenge(
                    **challenge_target,
                    context={**context, "username": username},
                    reason=safe_message,
                    rejected=True,
                )
                raise GitAuthenticationRequired(
                    "Git 凭据已失效或未被远程接受，请重新输入。",
                    challenge=challenge,
                    returncode=exc.returncode,
                ) from exc
            raise GitOperationError(
                safe_message,
                returncode=exc.returncode,
            ) from exc


def _settle_git_credential(
    saved: dict[str, Any] | None,
    *,
    remember: bool,
    actor: str,
    context: dict[str, str],
    username: str,
    secret: str,
    credential_root: str | Path | None,
    credential_key_path: str | Path | None,
) -> dict[str, Any]:
    """After success, save the typed credential when asked to, or mark the saved one used."""
    credential_result = {
        "used_saved": bool(saved),
        "saved": False,
        "credential_id": str((saved or {}).get("credential_id") or ""),
    }
    if remember:
        metadata = repo_credentials_backend.save_credential(
            actor,
            context,
            username=username,
            secret=secret,
            root=credential_root,
            key_path=credential_key_path,
        )
        credential_result.update(
            {
                "saved": True,
                "credential_id": metadata["credential_id"],
            }
        )
    elif saved and actor:
        repo_credentials_backend.mark_credential_used(
            actor,
            str(saved.get("credential_id") or ""),
            root=credential_root,
            key_path=credential_key_path,
        )
    return credential_result


def _network_git(
    checkout: Path,
    *args: str,
    repo_id: str,
    operation: str,
    remote: str,
    branch: str,
    context: dict[str, str],
    actor: str = "",
    credential: dict[str, Any] | None = None,
    remember: bool = False,
    timeout: int = 360,
    credential_root: str | Path | None = None,
    credential_key_path: str | Path | None = None,
    ssh_identity_file: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run one fetch, pull or push with the typed, saved or no credential; return output and credential use."""
    protocol = context.get("protocol") or ""
    supplied_username, supplied_secret = _supplied_git_credential(
        credential,
        context,
        protocol,
    )
    saved: dict[str, Any] | None = None
    if not supplied_secret and actor and protocol in {"https", "ssh"}:
        saved = repo_credentials_backend.find_credential(
            actor,
            context,
            root=credential_root,
            key_path=credential_key_path,
        )
    active_secret = supplied_secret or str((saved or {}).get("secret") or "")
    active_username = supplied_username or str((saved or {}).get("username") or "")
    if protocol == "ssh" and not active_username:
        active_username = "git"

    # The operation a credential challenge names if the remote asks for one.
    challenge_target = {
        "repo_id": repo_id,
        "remote": remote,
        "branch": branch,
        "operation": operation,
    }
    git_config = ["credential.helper=", "credential.useHttpPath=true"]
    if active_secret:
        output = _git_with_credential(
            checkout,
            args,
            protocol=protocol,
            context=context,
            challenge_target=challenge_target,
            username=active_username,
            secret=active_secret,
            saved=saved,
            actor=actor,
            git_config=git_config,
            timeout=timeout,
            ssh_identity_file=ssh_identity_file,
            credential_root=credential_root,
            credential_key_path=credential_key_path,
        )
    else:
        output = _git_without_credential(
            checkout,
            args,
            protocol=protocol,
            context=context,
            challenge_target=challenge_target,
            git_config=git_config,
            timeout=timeout,
            ssh_identity_file=ssh_identity_file,
        )
    return output, _settle_git_credential(
        saved,
        remember=bool(supplied_secret and remember and actor),
        actor=actor,
        context=context,
        username=active_username,
        secret=active_secret,
        credential_root=credential_root,
        credential_key_path=credential_key_path,
    )


def list_repo_credentials(
    repo_id: str,
    *,
    actor: str,
    credential_root: str | Path | None = None,
    credential_key_path: str | Path | None = None,
) -> dict[str, Any]:
    _record, checkout = _checkout(repo_id)
    remote_contexts: list[dict[str, Any]] = []
    for remote_record in _remote_records(checkout):
        name = str(remote_record.get("name") or "")
        context = _remote_auth_context(checkout, name, push=True)
        remote_contexts.append(
            {
                "remote": name,
                "protocol": context.get("protocol") or "",
                "host": context.get("host") or "",
                "path": context.get("path") or "",
                "username": context.get("username") or "",
                "auth_type": context.get("auth_type") or "",
                "provider": context.get("provider") or "",
                "secure": context.get("protocol") in {"https", "ssh", "local"},
            }
        )
    credentials = repo_credentials_backend.list_credentials(
        actor,
        root=credential_root,
        key_path=credential_key_path,
    )
    return {
        "schema": "tfi.repo-git-credential-settings.v1",
        "repo_id": repo_id,
        "remotes": remote_contexts,
        "credentials": credentials["credentials"],
    }


def delete_repo_credential(
    repo_id: str,
    credential_id: str,
    *,
    actor: str,
    credential_root: str | Path | None = None,
) -> dict[str, Any]:
    _checkout(repo_id)
    return repo_credentials_backend.delete_credential(
        actor,
        stripped_text(credential_id),
        root=credential_root,
    )
