"""Disposable instances for tests: a manifest and site settings, nothing installed.

Unit tests share one schema-isolated test database. A test that needs files
asks for an instance root made here and selects it with ``PORTAL_INSTANCE_ROOT``;
every directory then comes from the manifest, never from per-directory
environment variables. A test that wants a directory somewhere particular gives
the area to ``write_instance`` instead of overriding a path.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from unittest import mock

from management_common.instance_manifest import AREAS, InstanceManifest, SCHEMA, load, validate

CODE_ROOT = Path(__file__).resolve().parents[2]


def test_postgres() -> dict[str, Path]:
    """The PostgreSQL programs the session's disposable database server runs."""
    from tests.cases.postgres_test_database import _binary, _postgres_environment
    binary = _binary("pg_dump").parent.resolve()
    return {"bin": binary,
            "lib": Path(_postgres_environment()["LD_LIBRARY_PATH"].split(os.pathsep)[0]).resolve(),
            "share": (binary.parent / "share").resolve()}


def write_instance(root: Path, *, site: dict | None = None, code: Path | None = None,
                   areas: dict[str, Path | str] | None = None, dev: bool = False, dev_port: int = 28087, dev_code: Path | None = None,
                   bind: str = '127.0.0.1', postgres: dict[str, Path | str] | None = None) -> Path:
    """Write ``instance.json`` and ``site.json`` under ``root`` and return the root.

    ``areas`` maps area names to directories outside the instance root; the
    others stay at their defaults under ``<root>/data``. ``dev`` adds the paired
    Dev deployment (on ``dev_port``, running ``dev_code`` when given) and its gateway; ``bind`` is the address
    the instance listens on. ``site`` replaces the site settings; an
    existing ``site.json`` is kept when it is not given.
    """
    root = Path(root).resolve()
    code = (code or CODE_ROOT).resolve()
    unknown = set(areas or {}) - set(AREAS)
    if unknown:
        raise ValueError(f"Unknown areas: {sorted(unknown)}")
    deployments = {'release': {'kind': 'release', 'code_root': str(code), 'web_port': 28086}}
    components = ['postgres', 'nats', 'resource', 'execution', 'society', 'worker', 'web']
    entry_port = 28086
    if dev:
        deployments['dev'] = {'kind': 'dev', 'code_root': str((dev_code or code).resolve()), 'web_port': dev_port}
        components += ['dev-web', 'gateway']
        entry_port = 28085
    document = {
        'schema': SCHEMA, 'instance_id': 'fixture-' + root.name, 'name': 'fixture',
        'created_at': '2026-11-01T00:00:00Z', 'seed_version': 'fixture',
        'deployments': deployments,
        'network': {'bind': bind, 'entry_port': entry_port, 'postgres_port': 25432, 'nats_port': 24222},
        'services': {'prefix': 'tfi-fixture', 'components': components},
        'dependencies': {'postgres': {key: str(path) for key, path in (
            postgres or test_postgres()).items()}},
    }
    if areas:
        document['areas'] = {name: str(Path(path).resolve()) for name, path in areas.items()}
    validate(root, document)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'instance.json').write_text(json.dumps(document), encoding='utf-8')
    if site is not None or not (root / 'site.json').exists():
        (root / 'site.json').write_text(json.dumps(site or {}), encoding='utf-8')
    return root


def select_instance(monkeypatch, root: Path, **options) -> InstanceManifest:
    """Make ``root`` the instance the code under test works in, for one test."""
    write_instance(root, **options)
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(Path(root).resolve()))
    return load()


@contextmanager
def selected_instance(root: Path, **options):
    """``select_instance`` for unittest-style cases and module-level setup."""
    write_instance(root, **options)
    with mock.patch.dict(os.environ, {'PORTAL_INSTANCE_ROOT': str(Path(root).resolve())}):
        yield load()


def instance_beside(path: Path) -> Path:
    """An instance root next to ``path``, outside every area placed at ``path``."""
    path = Path(path)
    return path.with_name(path.name + '-instance')


def lineage_folders(monkeypatch, root: Path, *, projects: Path | None = None) -> Path:
    """Place the ``lineages`` area at ``root`` for one test and return it.

    Modules that took the area at import time keep their own copy, so the
    ones that guard Lineage folders are pointed at ``root`` as well.
    """
    from management_portal.society_service import resident_domain_common
    areas = {'lineages': root}
    if projects is not None:
        areas['projects'] = projects
        monkeypatch.setattr(resident_domain_common, 'PROJECTS_ROOT', Path(projects))
    select_instance(monkeypatch, instance_beside(root), areas=areas)
    monkeypatch.setattr(resident_domain_common, 'AGENTS_ROOT', Path(root))
    return Path(root)
