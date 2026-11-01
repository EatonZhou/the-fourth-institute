"""One-time relocation of instance-owned Provider programs into code dependencies.

This migration handles program files only. Account directories and credentials
are outside its source tree and are never copied, rewritten or enumerated here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shlex

from management_common.code_environment import native_binary, provider_directory
from management_common.instance_manifest import InstanceManifest

_PROGRAM = re.compile(r'(?:antigravity|claude-agent-sdk|claude-app-server|codex-cli|dsh-runtime|kimi-code|opencode)-[0-9][A-Za-z0-9._-]*')
_CURRENT = {'antigravity-current', 'claude-agent-sdk-current', 'claude-app-server-current',
            'dsh-runtime-current', 'kimi-code-current', 'opencode-current'}


@dataclass
class ProgramConversion:
    source: Path
    destination: Path
    package: Path
    node: Path
    codex: Path
    links: dict[Path, str]
    moved: bool = False
    package_moved: bool = False
    replaced: list[tuple[Path, bytes]] = field(default_factory=list)
    linked: list[tuple[Path, str | None]] = field(default_factory=list)

    def _link(self, path: Path, target: str) -> None:
        previous = os.readlink(path) if path.is_symlink() else None
        if path.exists() and previous is None:
            raise ValueError('Program link destination is occupied')
        self.linked.append((path, previous))
        path.unlink(missing_ok=True)
        path.symlink_to(target)

    def apply(self) -> None:
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.source.rename(self.destination)
        self.moved = True
        try:
            for relative, target in self.links.items():
                self._link(self.destination / relative, target)
            # Venv scripts and npm launchers may embed their installation prefix.
            # Only executable text in program bin directories is rewritten.
            candidates = set(self.destination.glob('*/bin/*'))
            candidates.update(self.destination.glob('*/pyvenv.cfg'))
            candidates.update(self.destination.glob('*/portal-codex'))
            for path in sorted(candidates):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                    continue
                original = path.read_bytes()
                if b'\0' in original or str(self.source).encode() not in original:
                    continue
                if not original.startswith(b'#!') and path.name != 'pyvenv.cfg':
                    continue
                self.replaced.append((path, original))
                path.write_bytes(original.replace(str(self.source).encode(), str(self.destination).encode()))
            launcher = self.destination / self.codex
            original = launcher.read_bytes()
            entry = launcher.parent / 'lib/node_modules/@openai/codex/bin/codex.js'
            self.replaced.append((launcher, original))
            launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(str(self.node))} {shlex.quote(str(entry))} "$@"\n')
            self._link(self.destination / 'codex', self.codex.as_posix())
            self.package.rename(self.destination / self.package.name)
            self.package_moved = True
            self._link(self.destination / 'claude-app-server-current', self.package.name)
        except BaseException:
            self.rollback()
            raise

    def rollback(self) -> None:
        if not self.moved:
            return
        for path, previous in reversed(self.linked):
            path.unlink(missing_ok=True)
            if previous is not None:
                path.symlink_to(previous)
        self.linked.clear()
        if self.package_moved:
            (self.destination / self.package.name).rename(self.package)
            self.package_moved = False
        for path, original in reversed(self.replaced):
            path.write_bytes(original)
        self.replaced.clear()
        self.destination.rename(self.source)
        self.moved = False


def plan(instance: InstanceManifest, old: dict, package: Path | None) -> ProgramConversion | None:
    settings = old.get('providers', {})
    if not settings.get('runtime_root'):
        if package is not None:
            raise ValueError('A prepared Provider package requires an old program root')
        return None
    source = Path(settings['runtime_root'])
    destination = provider_directory(instance)
    if not source.is_absolute() or source != source.resolve() or not source.is_dir() or not source.is_relative_to(instance.root):
        raise ValueError('Automatic program conversion requires an instance-owned canonical directory')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Code Provider directory already exists; resolve ownership before conversion')
    if package is None or not package.is_dir() or package.is_symlink() or package.parent != destination.parent:
        raise ValueError('Prepare a new Claude package beside the code dependencies before conversion')
    expected = (instance.code_root() / 'management_portal/claude_app_server_version.txt').read_text().strip()
    if (package / 'VERSION').read_text().strip() != expected or package.name != 'claude-app-server-' + expected:
        raise ValueError('Prepared Claude package does not match current code')
    if (source / package.name).exists():
        raise ValueError('Bump the Claude package version before conversion')
    for path in source.iterdir():
        if path.name in _CURRENT:
            if not path.is_symlink():
                raise ValueError('Expected Provider current link')
        elif not _PROGRAM.fullmatch(path.name) or not path.is_dir() or path.is_symlink():
            raise ValueError('Unknown file in Provider program root; owner review required')
    links = {}
    for path in source.rglob('*'):
        if not path.is_symlink():
            continue
        target = Path(os.readlink(path))
        if target.is_absolute() and target.is_relative_to(source):
            links[path.relative_to(source)] = os.path.relpath(destination / target.relative_to(source),
                                                           destination / path.relative_to(source).parent)
        elif path.parent == source:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source):
                raise ValueError('Provider current link points outside its program root')
    binary = Path(settings['codex_binary']).resolve(strict=True)
    if not binary.is_relative_to(source) or binary.name != 'portal-codex':
        raise ValueError('Codex migration requires its owned program launcher')
    if not (binary.parent / 'lib/node_modules/@openai/codex/bin/codex.js').is_file():
        raise ValueError('Codex package entry is unavailable')
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError('Program relocation crosses filesystems; prepare dependencies separately')
    return ProgramConversion(source, destination, package, native_binary(instance.code_root(), 'node'),
                             binary.relative_to(source), links)
