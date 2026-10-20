"""Release business orchestration over the existing Family and script tooling."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from .store import ReleaseStore, canonical

FACADE = "resident_5f7d890fc033"


def start_run(arguments, actor):
    store = ReleaseStore()
    run = store.create_run(actor=actor, **arguments)
    if run['state'] in {'resolving', 'awaiting_supervisor'}:
        dispatch_run(run)
    return store.get_run(run["run_id"])


def dispatch_run(run):
    """An immutable input scope authorizes exactly this business request.

There is no admin impersonation and no publish permission check. Ordinary
Resident inputs and other business operations retain their authorization.
    """
    from management_portal.society_service import resident_input_scope, resident_message_contract, resident_protocol
    prompt = (
        "执行TFI完整发行运行 " + run["run_id"] + "。本次配置快照已固定在distribution运行记录，后续配置变更不影响它。\n"
        "读取命令：python scripts/generate_tfi_distribution.py show --run-id " + run["run_id"] + "\n"
        "先解析自然语言和未知依赖，再用同一脚本generate命令生成工程；以--resolution传入绑定snapshot_sha256的明确解析。"
        "不能把任务受理或文字回复当成发布完成。只在隔离工程副本处理，不公开推送、不改源数据。"
        "沿本Family分工由发行成员生成，安装成员监督安装/检查，667总验收；原整项门面不改派。\n"
        "mode=" + run["mode"] + "; supervisor_resident_id=" + run["supervisor_resident_id"]
        + "; tau_validation=" + str(run["tau_validation"]) + "\n配置快照：\n" + canonical(run["snapshot"])
    )
    message_id = resident_message_contract.derived_message_identity("tfi-release", FACADE, run["run_id"])
    resident_input_scope.register(resident_id=FACADE, message_id=message_id, actor=run["actor"],
                                  profile={"input_sha256": hashlib.sha256(prompt.encode()).hexdigest()})
    # Conversation owns its durable input queue and message identity deduplication.
    return resident_protocol.deliver_resident_input({"resident_id": FACADE, "message": {
        "message_id": message_id, "role": "user", "content_parts": [{"type": "text", "text": prompt}]}}, run["actor"])


def source_root(value):
    root = Path(__file__).resolve().parents[2]
    if str(value).lower() == "dev":
        return root
    if str(value).lower() == "release":
        from scripts.portal_release import RELEASE_ROOT
        # The Release directory is maintained by the Portal release owner.
        candidate = RELEASE_ROOT
        if candidate.is_dir():
            return candidate.resolve()
        raise ValueError("Release source directory is unavailable")
    path = Path(str(value)).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Source must be Dev, Release, or an existing absolute checkout")
    return path.resolve()


def generate_run(run_id, *, resolution=None, attempt=1):
    from .database import read_snapshot
    from .engineering import generate, inventory, checksum
    from .store import digest
    from .selection import resolve
    store = ReleaseStore()
    # Serialize the entire deterministic generation. Different runs remain independent.
    with store_database_run_lock(run_id):
        run = store.get_run(run_id)
        if type(attempt) is not int or attempt < 1:
            raise ValueError('Generation attempt must be a positive integer')
        if run['state'] == 'succeeded' or (attempt == 1 and run["state"] in {"generated", "installing", "checking"}):
            return run
        root = source_root(run["snapshot"].get("source"))
        destination = Path(run["snapshot"].get("destination") or "")
        if not destination.is_absolute():
            raise ValueError("Release destination must be an absolute directory")
        destination = destination / (run_id if attempt == 1 else f'{run_id}.attempt-{attempt}')
        def finish(manifest):
            return store.report(run_id, state='generated', stage='generation', expected_revision=run['revision'],
                evidence={'attempt': attempt, 'manifest': str(destination / 'manifest.json'),
                          'manifest_sha256': checksum(destination / 'manifest.json'),
                          'inventory_count': len(manifest['inventory']),
                          'supervisor_resident_id': run['supervisor_resident_id'], 'checks_passed': False})
        if (destination / 'manifest.json').is_file():
            manifest = json.loads((destination / 'manifest.json').read_text())
            if manifest.get('run_id') != run_id or manifest.get('selection_sha256') != digest(run['snapshot']) or manifest.get('inventory') != inventory(destination):
                raise ValueError('Existing attempt does not match the immutable run or inventory')
            if run['state'] == 'failed':
                raise ValueError('This artifact attempt failed; generate a new attempt without replacing its failure')
            if run['result'].get('manifest_sha256') == checksum(destination / 'manifest.json') and run['state'] in {'generated', 'installing', 'checking'}:
                return run
            return finish(manifest)
        with read_snapshot() as source:
            resolved = resolve(run["snapshot"], source, resolution=resolution)
            from management_common.distribution_targets import resolve_targets
            targets = resolve_targets(run['snapshot'], resolved.get('features', []))
            resolved['targets'] = targets
            if not targets['installable']:
                return store.report(run_id, state='needs_configuration', stage='target_preflight', expected_revision=run['revision'], evidence=targets)
            if resolved["unresolved"]:
                return store.report(run_id, state="resolving", stage="selection", expected_revision=run["revision"],
                                    evidence={"unresolved": resolved["unresolved"], 'feature_resolution': resolved.get('feature_resolution', {})})
            run = store.report(run_id, state="generating", stage="generation", expected_revision=run["revision"], evidence={'attempt': attempt, 'previous_result': run['result']})
            try:
                from .file_scope import preflight
                resolved['file_scope_preflight'] = preflight(resolved.get('files', []), resolved.get('file_scope_policy'))
                from .history import freeze
                tables, archive, frozen = freeze(resolved.pop("tables"))
                frozen.update(source.prepare_references(tables, archive))
                frozen['archived_control_rows'] = len(archive)
                sql = source.dump(tables, mappings=resolved["path_mappings"], frozen_history=archive)
                resolved["history_freeze"] = frozen
                commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
                dirty = bool(subprocess.run(['git', '-C', str(root), 'status', '--porcelain'], capture_output=True, text=True, check=True).stdout)
                manifest = generate(source_root=root, destination=destination, snapshot=run["snapshot"], resolved=resolved,
                                    database_sql=sql, source_identity={"selection": run["snapshot"]["source"], "commit": commit, 'working_tree_modified': dirty},
                                    run_id=run_id, supervisor_resident_id=run["supervisor_resident_id"])
            except Exception as error:
                store.report(run_id, state="failed", stage="generation", expected_revision=run["revision"],
                             evidence={"attempt": attempt, "error_type": type(error).__name__, "error": "Generation failed; inspect isolated output", "directory": str(destination),
                                       'missing_references': getattr(error, 'missing_references', [])})
                raise
        return finish(manifest)


_UPGRADE_UNSET = object()


def rebuild_code_run(run_id, *, data_manifest, expected_manifest_sha256, attempt,
                     upgrade_declaration=_UPGRADE_UNSET):
    """Repair code using an explicitly identified immutable export of this run.

    A changing live source must not silently change the already reviewed data
    selection during an installation repair. Both source identities are kept.
    """
    from .engineering import generate, inventory, checksum, ROOTS, validate_repair_upgrade
    from .store import digest
    store = ReleaseStore()
    with store_database_run_lock(run_id):
        run = store.get_run(run_id)
        if type(attempt) is not int or attempt < 2 or run['state'] == 'succeeded':
            raise ValueError('Code repair requires a new attempt of an unfinished run')
        previous_path = Path(data_manifest).resolve(strict=True)
        previous_root = previous_path.parent
        if checksum(previous_path) != expected_manifest_sha256:
            raise ValueError('Prior engineering manifest digest changed')
        previous = json.loads(previous_path.read_text())
        if previous['run_id'] != run_id or previous['selection_sha256'] != digest(run['snapshot']):
            raise ValueError('Prior engineering does not belong to this immutable run snapshot')
        if previous['inventory'] != inventory(previous_root):
            raise ValueError('Prior engineering inventory changed')
        root = source_root(run['snapshot']['source'])
        explicit_upgrade = upgrade_declaration is not _UPGRADE_UNSET
        if explicit_upgrade:
            upgrade_declaration = validate_repair_upgrade(upgrade_declaration, root, expected_manifest_sha256)
        destination = Path(run['snapshot']['destination']) / f'{run_id}.attempt-{attempt}'
        if destination.exists():
            raise FileExistsError('Code repair never overwrites an artifact attempt')
        files = []
        for name in ROOTS:
            data_root = previous_root / previous['files'][name]
            for child in sorted(data_root.iterdir()):
                if name not in ROOTS[:3] or not child.is_dir() or child.is_symlink():
                    raise ValueError('Reusable data must be credential-free exported directories')
                files.append({'source': str(child), 'target': child.relative_to(previous_root).as_posix(),
                              'immutable_artifact': True})
        resolved = {key: previous[key] for key in ('features', 'dependency_reasons', 'external_requirements', 'history_freeze', 'feature_resolution')}
        resolved['resource_dependencies'] = previous.get('resource_dependencies', {})
        resolved['lineage_options'] = previous.get('lineage_options', {})
        resolved['anonymization'] = previous.get('anonymization', {})
        resolved['core_skills'] = previous['core_skills']
        resolved.update(selected_ids=previous['resolved_objects'], files=files, unresolved=[], path_mappings=[],
                        resource_types=[p.stem for p in (previous_root / 'code/config/resource-types').glob('*.json') if not p.name.startswith('_')])
        target_fields = ('selected_targets', 'target_resolution', 'support_matrix', 'capability_gaps', 'installable', 'architecture_constraints', 'dependency_requirements')
        resolved['targets'] = {key: previous[key] for key in target_fields}
        if explicit_upgrade:
            resolved['upgrade'] = upgrade_declaration
        elif 'upgrade' in previous:
            resolved['upgrade'] = previous['upgrade']
        source = {'selection': run['snapshot']['source'],
                  'commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
                  'working_tree_modified': bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True)),
                  'data_origin': {'manifest': str(previous_path), 'manifest_sha256': expected_manifest_sha256,
                                  'database_sha256': previous['database']['sha256'], 'source': previous['source']}}
        run = store.report(run_id, state='generating', stage='code_repair', expected_revision=run['revision'],
                           evidence={'attempt': attempt, 'data_origin': source['data_origin'], 'previous_result': run['result']})
        try:
            manifest = generate(source_root=root, destination=destination, snapshot=run['snapshot'], resolved=resolved,
                                database_sql=(previous_root / previous['database']['path']).read_text(),
                                source_identity=source, run_id=run_id, supervisor_resident_id=run['supervisor_resident_id'])
            expected_data = [row for row in previous['inventory'] if row['path'].startswith('data/')]
            actual_data = [row for row in manifest['inventory'] if row['path'].startswith('data/')]
            if expected_data != actual_data or previous['database']['sha256'] != manifest['database']['sha256']:
                raise ValueError('Code repair changed previously reviewed data bytes')
        except Exception as error:
            store.report(run_id, state='failed', stage='code_repair', expected_revision=run['revision'],
                         evidence={'attempt': attempt, 'error_type': type(error).__name__, 'directory': str(destination)})
            raise
        return store.report(run_id, state='generated', stage='code_repair', expected_revision=run['revision'],
                            evidence={'attempt': attempt, 'manifest': str(destination / 'manifest.json'),
                                      'manifest_sha256': checksum(destination / 'manifest.json'),
                                      'data_origin': source['data_origin'], 'inventory_count': len(manifest['inventory']),
                                      'checks_passed': False, 'full_test': 'pending_supervisor'})


def store_database_run_lock(run_id):
    from .store import database
    # Session lock is independent of stage transactions: a failed generation
    # must not roll back the durable failure report or hide live progress.
    key = int.from_bytes(hashlib.sha256(('distribution:generation:' + run_id).encode()).digest()[:8], 'big', signed=True)
    return database().database.advisory_lock(key)
