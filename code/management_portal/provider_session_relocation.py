"""Provider sessions follow a moved working directory.

Providers keep some session state keyed by the directory a session works in:
Codex trust entries in each account's ``config.toml``, Claude Code's per-directory
project folder under each account's ``projects/``, and Kimi's workspace and
session records in the instance's Kimi home. When Portal moves directories (a
Project rename, a Lineage folder move, moving an area, relocating an instance),
``relocate_provider_sessions`` moves this state from the old path prefix to the
new one so Residents continue in their existing sessions.

Only state of accounts registered in this instance is touched; account
directories come from the registrations, never from a default location.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

LOGGER = logging.getLogger(__name__)
MAX_REWRITE_BYTES = 16 * 1024 * 1024
# Claude Code writes the session's working directory into its first records.
CLAUDE_CWD_SCAN_LINES = 64


class _Prefix:
    """Rewrites paths under ``old`` to the same place under ``new``."""

    def __init__(self, old: str, new: str) -> None:
        self.old, self.new = old, new
        # A path starts after a separator and ends where a path name could not continue.
        self._pattern = re.compile(r"(?<![\w.~-])" + re.escape(old) + r"(?![\w@+-]|\.\w)")

    def under(self, path: str) -> bool:
        return path == self.old or path.startswith(self.old + "/")

    def map(self, path: str) -> str:
        return self.new + path[len(self.old):]

    def text(self, text: str) -> str:
        return self._pattern.sub(lambda _match: self.new, text)


def _canonical(value: str | os.PathLike) -> str:
    text = os.fspath(value)
    if not os.path.isabs(text):
        raise ValueError("Session relocation needs absolute paths")
    return os.path.normpath(text)


def _rewrite_file(path: Path, prefix: _Prefix, apply: bool, result: dict) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            return
        if path.stat().st_size > MAX_REWRITE_BYTES:
            result["errors"].append({"path": str(path), "error": "file too large to rewrite"})
            return
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    except OSError as exc:
        result["errors"].append({"path": str(path), "error": str(exc) or type(exc).__name__})
        return
    after = prefix.text(text)
    if after == text:
        return
    if apply:
        try:
            # Writing in place keeps the file's owner, mode and links.
            with path.open("r+b") as stream:
                stream.write(after.encode("utf-8"))
                stream.truncate()
        except OSError as exc:
            result["errors"].append({"path": str(path), "error": str(exc) or type(exc).__name__})
            return
    result["changed"].append(str(path))


def claude_project_key(path: str) -> str:
    """The installed Claude SDK's NFC/sanitization/32-bit hash format."""
    path = unicodedata.normalize('NFC', os.path.realpath(path))
    sanitized = re.sub(r'[^a-zA-Z0-9]', '-', path)
    if len(sanitized) <= 200:
        return sanitized
    hashed = 0
    for character in path:
        hashed = (hashed * 31 + ord(character)) & 0xffffffff
    if hashed >= 0x80000000:
        hashed -= 0x100000000
    hashed = abs(hashed)
    suffix = ''
    while hashed:
        hashed, digit = divmod(hashed, 36)
        suffix = '0123456789abcdefghijklmnopqrstuvwxyz'[digit] + suffix
    return sanitized[:200] + '-' + (suffix or '0')


def _claude_folder_cwd(folder: Path) -> str | None:
    """The working directory recorded by the sessions in one Claude project folder."""
    for transcript in sorted(folder.glob("*.jsonl")):
        try:
            with transcript.open(encoding="utf-8") as stream:
                for _index, line in zip(range(CLAUDE_CWD_SCAN_LINES), stream):
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    cwd = record.get("cwd") if isinstance(record, dict) else None
                    if isinstance(cwd, str) and os.path.isabs(cwd):
                        return os.path.normpath(cwd)
        except (OSError, UnicodeDecodeError):
            continue
    return None


def _merge_folder(source: Path, target: Path, apply: bool, result: dict) -> None:
    """Move ``source`` to ``target``; into an existing target, move what does not collide."""
    if not target.exists():
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, target)
        result["changed"].append(str(target))
        return
    for child in sorted(source.iterdir()):
        destination = target / child.name
        if os.path.lexists(destination):
            if child.is_dir() and not child.is_symlink() and destination.is_dir() and not destination.is_symlink():
                _merge_folder(child, destination, apply, result)
            else:
                result["conflicts"].append({"source": str(child), "target": str(destination)})
            continue
        if apply:
            os.rename(child, destination)
        result["changed"].append(str(destination))
    if apply:
        try:
            source.rmdir()
        except OSError:
            pass


def _registered_homes(provider: str, kinds: Iterable[str] | None = None) -> list[Path]:
    from management_common.provider_account_paths import registered_home
    from management_portal.execution.account_registrations import registrations

    homes = set()
    for row in registrations(provider):
        if kinds is not None and row["kind"] not in kinds:
            continue
        try:
            homes.add(registered_home(row))
        except ValueError:
            continue
    return sorted(homes)


def kimi_home() -> Path:
    from management_common.portal_paths import path as portal_path

    return portal_path("provider-state") / "kimi-code"


def _claude_homes() -> list[Path]:
    from management_common.portal_paths import path as portal_path
    homes = set(_registered_homes('claude', kinds=('subscription_login',)))
    # API-backed Claude sessions have instance-owned configuration directories.
    homes.update(portal_path('provider-state').glob('agent-config/*/claude/config'))
    suffix = 'residents/*/runtime/backend/claude/config'
    homes.update(portal_path('projects').glob('*/agents/*/' + suffix))
    homes.update(portal_path('lineages').glob('*/' + suffix))
    return sorted(homes)


def dsh_project_key(cwd: str) -> str:
    """DSH persistence's projectKey, including JavaScript UTF-16 code units."""
    encoded = cwd.encode('utf-16-le', errors='surrogatepass')
    readable, separator = '', False
    for offset in range(0, len(encoded), 2):
        code = int.from_bytes(encoded[offset:offset + 2], 'little')
        character = chr(code)
        if character in '/\\:':
            if not separator:
                readable += '-'
            separator = True
        elif re.fullmatch(r'[A-Za-z0-9._-]', character):
            readable += character
            separator = False
        else:
            readable += f'~{code:04X}'
            separator = False
    return '--' + (readable.lstrip('-') or 'root')[:251] + '--'


def _relocate_dsh(prefix: _Prefix, apply: bool, result: dict) -> None:
    from management_common.portal_paths import path as portal_path
    state = portal_path('provider-state') / 'dsh'
    for sessions in sorted(state.glob('dsh-*/sessions')):
        for project in sorted(sessions.iterdir()):
            if not project.is_dir() or project.is_symlink():
                continue
            for folder in sorted(project.iterdir()):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                destination = None
                for log in sorted(folder.glob('*.jsonl')):
                    try:
                        with log.open('rb') as stream:
                            first = stream.readline()
                        header = json.loads(first)
                        cwd = header.get('cwd')
                        if header.get('type') != 'session' or not isinstance(cwd, str) or not prefix.under(cwd):
                            continue
                        new = prefix.map(cwd)
                        destination = sessions / dsh_project_key(new) / folder.name
                        if apply:
                            # Keep message records byte-for-byte. Only metadata changes.
                            temporary = log.with_name(log.name + '.relocating')
                            try:
                                import shutil
                                with log.open('rb') as source, temporary.open('xb') as target:
                                    source.readline()
                                    header['cwd'] = new
                                    target.write((json.dumps(header, ensure_ascii=False) + '\n').encode())
                                    shutil.copyfileobj(source, target)
                                shutil.copymode(log, temporary)
                                os.replace(temporary, log)
                            finally:
                                temporary.unlink(missing_ok=True)
                        result['changed'].append(str(log))
                    except (OSError, ValueError) as exc:
                        result['errors'].append({'path': str(log), 'error': type(exc).__name__})
                if destination is not None and destination != folder:
                    _merge_folder(folder, destination, apply, result)
            if apply:
                try:
                    project.rmdir()
                except OSError:
                    pass
        # Launch profiles are generated state, not transcripts.
        for config in sessions.parent.glob('profile/*.yaml'):
            _rewrite_file(config, prefix, apply, result)
        for config in sessions.parent.glob('profile/*.json'):
            _rewrite_file(config, prefix, apply, result)


def relocate_provider_sessions(old_prefix: str | os.PathLike, new_prefix: str | os.PathLike,
                               *, apply: bool = True) -> dict[str, Any]:
    """Move Provider session state of directories under ``old_prefix`` to ``new_prefix``.

    Call it after the directory itself has moved, while no Resident works in it.
    Errors and collisions are reported, not raised; ``apply=False`` only reports.
    """
    old, new = _canonical(old_prefix), _canonical(new_prefix)
    result: dict[str, Any] = {"old_prefix": old, "new_prefix": new, "changed": [], "conflicts": [], "errors": []}
    if old == new:
        return result
    prefix = _Prefix(old, new)
    # Codex trusts each working directory by its path in config.toml.
    for home in _registered_homes("codex"):
        _rewrite_file(home / "config.toml", prefix, apply, result)
    # Claude Code keeps each working directory's sessions and memory in a folder named after it.
    # The name is lossy, so a folder's directory is read from its transcripts; a folder
    # without transcripts (only memory) is recognised by the name of the prefix itself.
    old_key = claude_project_key(old)
    for home in _claude_homes():
        projects = home / "projects"
        if not projects.is_dir():
            continue
        for folder in sorted(projects.iterdir()):
            if folder.is_symlink() or not folder.is_dir():
                continue
            cwd = _claude_folder_cwd(folder)
            if cwd and (cwd == new or cwd.startswith(new + '/')):
                original = old + cwd[len(new):]
                if folder.name == claude_project_key(original):
                    cwd = original
            if cwd is None and folder.name == old_key:
                cwd = old
            if cwd is None or not prefix.under(cwd):
                continue
            target = projects / claude_project_key(prefix.map(cwd))
            if target == folder:
                continue
            try:
                _merge_folder(folder, target, apply, result)
            except OSError as exc:
                result["errors"].append({"path": str(folder), "error": str(exc) or type(exc).__name__})
    # Kimi sessions record the directory they started in and their own location.
    home = kimi_home()
    if home.is_dir():
        for path in sorted({home / "workspaces.json", home / "session_index.jsonl",
                            *home.glob("sessions/*/*/state.json")}):
            _rewrite_file(path, prefix, apply, result)
    _relocate_dsh(prefix, apply, result)
    return result
