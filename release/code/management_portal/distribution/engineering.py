"""Deterministic engineering writer, consuming a resolved selection snapshot."""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .sanitize import knowledge_links, private_path, scrub, SECRET_VALUE
from .store import canonical, digest
from management_common.release_dependencies import declaration as runtime_dependencies

SCHEMA = "tfi.release-engineering.v1"
ROOTS = ("share_root", "staging_root", "instances_root", "accounts_root", "secrets_dir")


def relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Engineering paths must remain relative to the package")
    return path


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    return [{"path": path.relative_to(root).as_posix(), "sha256": checksum(path), "size": path.stat().st_size}
            for path in sorted(root.rglob("*")) if path.is_file() and path != root / "manifest.json"]


def tree_digest(files):
    return hashlib.sha256(canonical(files).encode()).hexdigest()


def dependency_materials(code):
    """Record shipped constraints and their limits, never source pip credentials."""
    inputs = []
    for path in sorted(code.glob('requirements*.txt')):
        inputs.append({'path': 'code/' + path.name, 'sha256': checksum(path),
                       'constraints': [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]})
    return {'requirements': inputs, 'fully_locked': False,
            'lock_status': 'Shipped constraint files are not a transitive hash lock; installer records resolved versions and executable hashes',
            'package_source': 'Installer-selected package index; no source account or index credentials exported',
            'external_executables': ['installer-selected Python', 'installer-selected PostgreSQL', 'installer-selected NATS'],
            'network_policy': 'Online resolution may be required by bootstrap; available local dependencies may be reused'}


def validate_upgrade(declaration, files):
    """An upgrade is an explicit compatibility claim tied to immutable SQL bytes."""
    if not isinstance(declaration, dict):
        raise ValueError("Upgrade declaration must be an object")
    starts = declaration.get('from_manifest_sha256')
    migrations = declaration.get('migrations')
    if not isinstance(starts, list) or not starts or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in starts) or len(set(starts)) != len(starts):
        raise ValueError("Upgrade requires unique exact starting manifest hashes")
    if not isinstance(migrations, list):
        raise ValueError("Upgrade migrations must be an ordered list")
    indexed = {item['path']: item['sha256'] for item in files}
    seen = set()
    clean = []
    for migration in migrations:
        if not isinstance(migration, dict):
            raise ValueError("Invalid upgrade migration")
        identity, path, sha = (migration.get(key) for key in ('id', 'path', 'sha256'))
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', identity) or identity in seen:
            raise ValueError("Upgrade migration IDs must be unique stable identifiers")
        if not isinstance(path, str) or relative(path).as_posix() != path or not path.endswith('.sql') or path == 'database/distribution.sql':
            raise ValueError("Upgrade must use dedicated relative migration SQL, never the fresh-install database")
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or indexed.get(path) != sha:
            raise ValueError("Upgrade migration digest must match engineering inventory")
        seen.add(identity)
        clean.append({'id': identity, 'path': path, 'sha256': sha})
    return {'from_manifest_sha256': list(starts), 'migrations': clean}


def validate_repair_upgrade(declaration, source_root, origin_sha256):
    """Preflight an explicit repair claim; generation rechecks shipped bytes."""
    migrations = declaration.get('migrations') if isinstance(declaration, dict) else None
    # First validate structure without touching any declaration-selected path.
    declared_files = [item for item in migrations if isinstance(item, dict)
                      and 'path' in item and 'sha256' in item] if isinstance(migrations, list) else []
    # Invalid paths can be unhashable; do not use them as inventory keys.
    declared_files = [item for item in declared_files if isinstance(item['path'], str)]
    clean = validate_upgrade(declaration, declared_files)
    if clean['from_manifest_sha256'] != [origin_sha256]:
        raise ValueError('Code repair upgrade must declare only the exact reused manifest origin')
    root = Path(source_root).resolve(strict=True)
    actual_files = []
    for item in clean['migrations']:
        path = relative(item['path'])
        if path.parts[0] != 'code':
            raise ValueError('Code repair migrations must be dedicated code/ SQL files')
        source = root.joinpath(*path.parts[1:])
        if source.is_symlink() or not source.resolve(strict=True).is_relative_to(root) or not source.is_file():
            raise ValueError('Code repair migration must be a source file within the code root')
        actual_files.append({'path': item['path'], 'sha256': checksum(source)})
    return validate_upgrade(clean, actual_files)


def copy_content(source, target, *, selected, mappings=(), knowledge=False, anonymize=True, source_usernames=(), skill_scope=None):
    """Never dereference symlinks or include local auth/runtime caches."""
    source = Path(source)
    from .skill_scope import SkillScope
    skill_scope = skill_scope or SkillScope()
    if skill_scope.excluded_root(source) is not None:
        return
    if source.is_symlink():
        raise ValueError("Selected content contains a symlink; resolve its scope explicitly")
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for item in sorted(source.iterdir()):
            if private_path(item.name):
                continue
            copy_content(item, target / item.name, selected=selected, mappings=mappings, knowledge=knowledge, anonymize=anonymize, source_usernames=source_usernames, skill_scope=skill_scope)
        return
    if not source.is_file():
        raise ValueError("Selected content is missing or is not a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_bytes()
    compressed_json = source.name.endswith(".json.gz")
    if compressed_json:
        content = gzip.decompress(content)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # Binary artifacts are copied byte-for-byte, never interpreted as paths.
        target.write_bytes(content)
    else:
        from .privacy import portable_text, redact, release_code_text
        if compressed_json:
            # Frozen release fixtures are derived portable samples, not byte-identical source evidence.
            text = json.dumps(redact(json.loads(text), source_usernames), ensure_ascii=False, separators=(",", ":"))
        elif not (anonymize and source.suffix.lower() == ".json"):
            if not anonymize and source.suffix.lower() in {'.py', '.js', '.mjs', '.json', '.yaml', '.yml', '.css', '.html'}:
                text = release_code_text(text, source, source_usernames)
            else:
                text = portable_text(text, source_usernames, environment=knowledge or source.suffix.lower() in {".md", ".rst", ".txt"})
        if not anonymize:
            # Source declarations describe types/fields, not human instances.
            # Preserve their JSON shape (including password field schemas).
            text = scrub(text, mappings=mappings, anonymize=False)
        elif source.suffix.lower() == ".json":
            try:
                text = json.dumps(scrub(json.loads(text), mappings=mappings, anonymize=anonymize, source_usernames=source_usernames), ensure_ascii=False, indent=2) + "\n"
            except json.JSONDecodeError:
                text = scrub(text, mappings=mappings)
        else:
            text = scrub(text, mappings=mappings)
        if knowledge and source.suffix.lower() == ".md":
            text = knowledge_links(text, selected)
        if compressed_json:
            target.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
        else:
            target.write_text(text, encoding="utf-8")
    target.chmod(source.stat().st_mode & 0o777)


def source_files(root):
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Source must be an existing code checkout")
    for name in sorted(set(result.stdout.decode().split("\0")) - {""}):
        path = relative(name)
        if not private_path(path) and (root / path).is_file():
            yield path


def feature_slice(code, enabled):
    """Rewrite the copy's feature registry through its syntax tree."""
    path = code / "management_portal/portal_features.py"
    if not path.exists():
        return
    tree = ast.parse(path.read_text())
    for statement in tree.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id == "FEATURES":
            statement.value.elts = [item for item in statement.value.elts
                                   if isinstance(item, ast.Call) and item.args[0].value in enabled]
    path.write_text(ast.unparse(tree) + "\n", encoding="utf-8")


def execution_feature_slice(code, enabled):
    """Shared execution code must not enable omitted recording capabilities."""
    path = code / 'management_portal/execution/service_settings.py'
    if not path.exists():
        return
    omitted = {'model_request_timing', 'harness_recording'} - set(enabled)
    if not omitted:
        return
    tree = ast.parse(path.read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ExecutionSettings')
    methods = {node.name: node for node in owner.body if isinstance(node, ast.FunctionDef)}
    for feature in sorted(omitted):
        field = feature + '_enabled'
        # Change only this owner's defaults, never arbitrary TRUE literals.
        for node in ast.walk(methods['__init__']):
            if feature == 'model_request_timing' and isinstance(node, ast.Constant) and isinstance(node.value, str) and 'INSERT INTO service_settings(singleton, provider_timing_proxy_enabled)' in node.value:
                if 'VALUES (1, TRUE)' not in node.value:
                    raise ValueError('Execution timing default changed; review distribution adapter')
                node.value = node.value.replace('VALUES (1, TRUE)', 'VALUES (1, FALSE)')
            if feature == 'harness_recording' and isinstance(node, ast.Dict):
                for index, key in enumerate(node.keys):
                    if isinstance(key, ast.Constant) and key.value == field:
                        node.values[index] = ast.Constant('BOOLEAN NOT NULL DEFAULT FALSE')
        for node in ast.walk(methods['get']):
            if isinstance(node, ast.Dict):
                for index, key in enumerate(node.keys):
                    if isinstance(key, ast.Constant) and key.value == field:
                        node.values[index] = ast.Constant(False)
        if feature == 'model_request_timing':
            methods['model_request_timing_enabled'].body = ast.parse('return False').body
        else:
            methods['admit_harness_recording'].body = ast.parse('yield False').body
        client_key = {'model_request_timing': 'system:request-timing:enabled',
                      'harness_recording': 'system:harness-recording:enabled'}[feature]
        methods['_set'].body[0:0] = ast.parse(
            f'if enabled and client_key == {client_key!r}:\n'
            f'    raise ValueError({("Feature not included in this distribution: " + feature)!r})').body
    path.write_text(ast.unparse(ast.fix_missing_locations(tree)) + '\n', encoding='utf-8')


def initialize_default_adapters(code):
    """Adapt only the copy's native new-target default; preserve update semantics."""
    path = code / 'management_portal/society_service/application_target_configuration.py'
    if not path.exists():
        return
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if 'target_id' not in keys or 'admission_paused' not in keys:
            continue
        index = keys.index('admission_paused')
        if isinstance(node.values[index], ast.Constant) and node.values[index].value is False:
            node.values[index] = ast.parse("__import__('management_common.distribution_features', fromlist=['initial_admission_paused']).initial_admission_paused(application)", mode='eval').body
    path.write_text(ast.unparse(ast.fix_missing_locations(tree)) + '\n', encoding='utf-8')


def generate(*, source_root, destination, snapshot, resolved, database_sql, source_identity=None, run_id="", supervisor_resident_id=""):
    """Write a fresh isolated directory. Never overwrite source or an earlier run."""
    source_root = Path(source_root).resolve(strict=True)
    for root in resolved.get('projection_code_repositories', {}).values():
        if Path(root).resolve(strict=True) != source_root:
            raise ValueError('Projection code repository differs from the selected release source')
    destination = Path(destination).expanduser().resolve()
    if destination == source_root or destination in source_root.parents or destination.is_relative_to(source_root):
        raise ValueError("Release destination must be isolated from the source checkout")
    if resolved.get("unresolved"):
        raise ValueError("Release selection still has unresolved items")
    if destination.exists():
        raise FileExistsError("Release destination already exists; use a new run directory")
    selected = set(resolved.get("selected_ids", []))
    mappings = resolved.get("path_mappings", [])
    source_usernames = resolved.get("source_usernames", [])
    from .skill_scope import SkillScope
    skill_scope = SkillScope()
    destination.mkdir(parents=True)
    try:
        code = destination / "code"
        for path in source_files(source_root):
            if path.parts[0] in {"tests", ".wall"} and str(path) not in resolved.get("code_extras", []):
                continue
            if str(path) in {"SPEC.md", "IMPROVE.md", "config/site.json", "config/portal_instances.json", "release-manifest.json"}:
                continue
            copy_content(source_root / path, code / path, selected=selected, anonymize=False, source_usernames=source_usernames, skill_scope=skill_scope)
        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            if not (code / name).is_file():
                raise ValueError("Required legal document missing: " + name)
        feature_slice(code, set(resolved.get("features", [])))
        execution_feature_slice(code, resolved.get('features', []))
        initialize_default_adapters(code)
        (code / "config").mkdir(exist_ok=True)
        # Presentation is independent of the anonymous principal's valid machine
        # identity. No User account or authorization record is created.
        from .sanitize import ANONYMOUS_USER_ID, ANONYMOUS_LABEL
        (code / "config/principal-labels.json").write_text(json.dumps(
            {'user': {ANONYMOUS_USER_ID: ANONYMOUS_LABEL}}, ensure_ascii=False), encoding='utf-8')
        (code / "config/distribution.json").write_text(json.dumps({"schema": SCHEMA, "features": resolved.get("features", []),
            'feature_resolution': resolved.get('feature_resolution', {})}))
        resource_types = code / "config/resource-types"
        if "resource_types" in resolved:
            for declaration in resource_types.glob("*.json"):
                if not declaration.name.startswith("_") and declaration.stem not in resolved["resource_types"]:
                    declaration.unlink()
        if (resource_types / '_registry.json').is_file():
            from management_common.resource_identity import ResourceTypeRegistry
            ResourceTypeRegistry(resource_types).declarations
        from .permissions import adapt_builtin_schemes
        permission_validation = adapt_builtin_schemes(code)
        # Standalone distributions cannot invoke the source site's Dev/Release
        # service controller through either its CLI or an imported API.
        controller = code / "scripts/portal_services.py"
        if controller.exists():
            tree = ast.parse(controller.read_text())
            unsupported = '{"ok": False, "supported": False, "reason": "Standalone TFI instances use install_tfi_release status/cleanup; source Dev/Release service management is unavailable"}'
            for statement in tree.body:
                if isinstance(statement, ast.FunctionDef) and statement.name in {"status", "instance_status", "user_router_health", "restart_user_router"}:
                    statement.body = ast.parse("return " + unsupported).body
                elif isinstance(statement, ast.FunctionDef) and statement.name in {"start", "stop", "restart", "request_restart", "_run", "_controller", "_prepare_units"}:
                    statement.body.insert(0, ast.parse('raise RuntimeError("Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported")').body[0])
            controller.write_text(ast.unparse(tree) + "\n")
        # Reject source-router network changes before saving; return a concrete
        # unsupported result rather than an asynchronous success notification.
        admin = code / "management_portal/http_admin.py"
        if admin.exists():
            tree = ast.parse(admin.read_text())
            for statement in ast.walk(tree):
                if isinstance(statement, ast.FunctionDef) and statement.name == "network_post":
                    statement.body = ast.parse('return self.respond(request, 409, {"error": "Standalone TFI network settings are owned by install_tfi_release target configuration", "supported": False})').body
                if isinstance(statement, ast.FunctionDef) and statement.name in {"embeddings_get", "embeddings_post", "embeddings_install_post"} and "resident_semantic_search" not in resolved.get("features", []):
                    statement.body = ast.parse('return self.respond(request, 404, {"error": "This distribution does not include Resident semantic search"})').body
            admin.write_text(ast.unparse(tree) + "\n")
        for root in ROOTS:
            (destination / "data" / root).mkdir(parents=True)
        from .file_scope import FileScope
        file_scope = FileScope(resolved.get('files', []), resolved.get('file_scope_policy'))
        copied = set()
        copy_scopes = []
        for item in resolved.get("files", []):
            target = relative(item["target"])
            if len(target.parts) < 3 or target.parts[0] != "data" or target.parts[1] not in ROOTS[:3]:
                raise ValueError("Resolved resource files must target a declared data root")
            identity = (str(Path(item["source"]).resolve()), str(target))
            if identity in copied:
                continue
            if item.get('immutable_file'):
                descriptor = item['immutable_file']
                source = Path(item['source'])
                if skill_scope.excluded_root(source) is not None:
                    raise ValueError('Immutable projection evidence contains a site-only Skill')
                if source.is_symlink() or not source.is_file() or source.stat().st_size != descriptor['size'] or checksum(source) != descriptor['sha256']:
                    raise ValueError('Projection evidence changed before copy')
                output = destination / target
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, output)
                if checksum(output) != descriptor['sha256']:
                    raise ValueError('Projection evidence changed during copy')
            elif item.get('immutable_artifact'):
                artifact = Path(item['source'])
                if skill_scope.excluded_root(artifact) is not None or any(
                        p.is_symlink() or private_path(p.relative_to(artifact)) or skill_scope.excluded_root(p) is not None
                        for p in artifact.rglob('*')):
                    raise ValueError('Immutable application artifact contains private paths or links')
                shutil.copytree(artifact, destination / target)
            else:
                for relative_path, actual, directory in file_scope.walk(item['source']):
                    output = destination / target / relative_path
                    if directory:
                        output.mkdir(parents=True, exist_ok=True)
                    else:
                        copy_content(actual, output, selected=selected, mappings=mappings,
                                     knowledge=item.get('knowledge', False), anonymize=item.get('anonymize', True), source_usernames=source_usernames, skill_scope=skill_scope)
            copied.add(identity)
            copy_scopes.append({'source': str(Path(item['source']).absolute()), 'target': str(target)})
        for name, text in resolved.get("generated_files", {}).items():
            path = destination / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(scrub(text, mappings=mappings, source_usernames=source_usernames), encoding="utf-8")
        from .core_skills import export_core_skills
        if 'core_skills' in resolved:
            # A code repair already copied the immutable data, including its
            # core rules. Never overwrite those bytes with live source rules.
            core_skills = resolved['core_skills']
            for item in core_skills.get('files', []):
                path = relative(item['path'])
                if not path.is_relative_to('data/share_root/Skills') or checksum(destination / path) != item['sha256']:
                    raise ValueError('Reused core Skill does not match the prior engineering')
        else:
            core_skills = export_core_skills(destination, source_usernames=source_usernames)
        db = destination / "database/distribution.sql"
        db.parent.mkdir()
        db.write_text(database_sql, encoding="utf-8")
        entrypoint = "code/scripts/install_tfi_release"
        if not (destination / entrypoint).is_file():
            raise ValueError("Fixed release installer is not present in selected code")
        (destination / "INSTALL.md").write_text(
            "# TFI Portal\n\nRun `python3 code/scripts/tfi_release_install.py --help` for installation parameters.\n"
            "Use a new installation directory, independent PostgreSQL database, five data roots, service prefix and ports.\n"
            "Register the first administrator in the new instance. Configure your own backend accounts; source authentication is never supplied.\n",
            encoding="utf-8")
        if (source_root / 'INSTALL.md').is_file():
            copy_content(source_root / 'INSTALL.md', destination / 'INSTALL.md', selected=selected, anonymize=False, source_usernames=source_usernames)
        files = inventory(destination)
        code_files = [{**item, "path": item["path"][5:]} for item in files if item["path"].startswith("code/")]
        manifest = {
            "schema": SCHEMA, "run_id": run_id, "supervisor_resident_id": supervisor_resident_id, "source": source_identity or {},
            "selection_sha256": digest(snapshot),
            "selection_snapshot": scrub(snapshot, mappings=mappings, source_usernames=source_usernames),
            "resolved_objects": sorted(selected), "dependency_reasons": resolved.get("dependency_reasons", {}),
            "unresolved": [], "code": {"path": "code", "sha256": tree_digest(code_files)},
            "database": {"path": "database/distribution.sql", "format": "postgresql-sql", "sha256": checksum(db)},
            "files": {name: f"data/{name}" for name in ROOTS},
            "directory_mappings": [{"placeholder": "${" + name + "}", "root": name, "path": "."} for name in (*ROOTS, "code_root")],
            "installer": {"entrypoint": entrypoint}, "inventory": files,
            "external_requirements": resolved.get("external_requirements", ["PostgreSQL", "Python dependencies", "new backend account mapping"]),
            "features": resolved.get("features", []), "checks": {"no_git_history": True, "source_authentication_excluded": True},
            "history_freeze": resolved.get("history_freeze", {}),
            "feature_resolution": resolved.get('feature_resolution', {}),
            "dependency_materials": dependency_materials(code),
            "runtime_dependencies": runtime_dependencies(resolved.get('features', []),
                resolved.get('resource_dependencies', {}).get('types', [])),
            "core_skills": core_skills,
            "excluded_resources": resolved.get('excluded_resources', []),
            "permission_validation": permission_validation,
            "resource_dependencies": resolved.get('resource_dependencies', {}),
            "lineage_options": resolved.get('lineage_options', {}),
            "anonymization": resolved.get('anonymization', {}),
            "file_scope": scrub({**file_scope.report(), 'copy_scopes': copy_scopes,
                'deduplication': 'Exact resolved-source/destination pairs only; overlapping bindings retain separate directory copies'},
                mappings=mappings, anonymize=False),
        }
        from management_common.distribution_targets import resolve_targets
        manifest.update(resolved.get('targets') or resolve_targets(snapshot, resolved.get('features', [])))
        if 'upgrade' in resolved:
            manifest['upgrade'] = validate_upgrade(resolved['upgrade'], files)
        (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
    except Exception:
        # Preserve the isolated partial tree for diagnosis, never present it as installable.
        (destination / "GENERATION_FAILED").write_text("Generation incomplete; no installable manifest.\n")
        raise
