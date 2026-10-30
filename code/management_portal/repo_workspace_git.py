"""Constrained Git commands for Repo and Article checkouts, their errors, and redaction."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from management_common.text_values import stripped_text


class GitOperationError(RuntimeError):
    """A constrained Git command failed."""

    def __init__(self, message: str, *, returncode: int = 1) -> None:
        self.returncode = int(returncode)
        super().__init__(message)


class GitAuthenticationRequired(GitOperationError):
    """A network Git operation needs a Portal-scoped credential."""

    def __init__(
        self,
        message: str,
        *,
        challenge: dict[str, Any],
        returncode: int = 1,
    ) -> None:
        self.challenge = dict(challenge)
        super().__init__(message, returncode=returncode)


def _git_bytes(
    checkout: Path,
    *args: str,
    timeout: int = 90,
    allowed_returncodes: Iterable[int] = (0,),
    git_config: Iterable[str] = (),
    env_overrides: dict[str, str] | None = None,
) -> bytes:
    config_args = [
        value
        for setting in git_config
        for value in ("-c", str(setting))
    ]
    try:
        proc = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "-c",
                "safe.directory=*",
                "-c",
                "core.quotePath=false",
                *config_args,
                "-C",
                str(checkout),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                **(env_overrides or {}),
            },
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitOperationError(f"Git command timed out after {timeout}s") from exc
    allowed = set(int(value) for value in allowed_returncodes)
    if proc.returncode not in allowed:
        message = (
            proc.stderr.decode("utf-8", errors="replace").strip()
            or proc.stdout.decode("utf-8", errors="replace").strip()
            or f"git exited {proc.returncode}"
        )
        raise GitOperationError(_redact_text(message), returncode=proc.returncode)
    return proc.stdout


def _git(
    checkout: Path,
    *args: str,
    timeout: int = 90,
    allowed_returncodes: Iterable[int] = (0,),
    git_config: Iterable[str] = (),
    env_overrides: dict[str, str] | None = None,
) -> str:
    return _git_bytes(
        checkout,
        *args,
        timeout=timeout,
        allowed_returncodes=allowed_returncodes,
        git_config=git_config,
        env_overrides=env_overrides,
    ).decode("utf-8", errors="replace").strip()


def _redact_url(value: object) -> str:
    raw = stripped_text(value)
    if not raw or "://" not in raw:
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.username is None and parsed.password is None:
        return raw
    hostname = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        hostname = f"{hostname}:{port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, parsed.fragment))


def _redact_text(value: object) -> str:
    text = str(value or "")
    return re.sub(
        r"([A-Za-z][A-Za-z0-9+.-]*://)([^/\s@]+)@",
        r"\1",
        text,
    )
