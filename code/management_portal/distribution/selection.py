"""Resolve selected IDs against one source snapshot, without link recursion."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .content import registered_content_path
from .sanitize import RESOURCE_ID, prune_permissions, ANONYMIZATION
from .store import digest

SYSTEM = "lineage_ed619225d417"
BASE_TYPES = {"user", "project", "lineage", "resident", "family", "workspace", "workbench"}
DEPENDENCIES = {"tau": {"test_framework"}, "test_view": {"test_framework"}, "wall_scheduling": {"wall"}}


def resource_file_scope(identity, record):
    """Use each resource owner's registered content root, without link traversal."""
    kind = record.get("resource_type")
    attributes = record.get("attributes", {})
    if kind in {"lineage", "family", "workbench"}:
        return None
    content_path = registered_content_path(record)
    path = str(content_path) if content_path is not None else None
    if kind == "repo":
        relative = f"Codes/{identity}"
    elif kind == "article":
        relative = f"Articles/{identity}"
    elif kind == "paper":
        if not path or Path(path).name != "manifest.json" or not Path(path).is_file():
            raise ValueError("Selected paper has no available registered manifest")
        path = str(Path(path).parent)
        relative = f"Papers/items/selected/{identity}"
    elif kind == "skill" and path and Path(path).is_dir():
        relative = f"Skills/{identity}"
    elif path:
        owners = attributes.get("project_ids", [])
        if not owners:
            raise ValueError("File resource needs explicit target location")
        relative = f"Projects/{owners[0]}/knowledge_base/{kind}/{Path(path).name}"
    else:
        return None
    return {"source": path, "target": "data/share_root/" + relative, 'anonymize': kind != 'repo',
            "knowledge": kind not in {"repo", "skill"}}


def text_scopes(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            location = path + "." + key if path else key
            if key in {"notes", "description", "society_description", "projection_description", "extra_scope", "exclusions"} and isinstance(item, str) and item.strip():
                yield location, item
            elif key not in {"ownership", "excluded_device_selections"}:
                yield from text_scopes(item, location)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from text_scopes(item, f"{path}[{index}]")


def resolve(snapshot, source, *, resolution=None):
    """Resident resolution may add exact IDs/files, with evidence tied to inputs."""
    selection = deepcopy(snapshot)
    resolution = resolution or {}
    unresolved = []
    reasons = {}
    if resolution and resolution.get("snapshot_sha256") != digest(snapshot):
        raise ValueError("Resident resolution does not match the immutable configuration snapshot")
    for location, text in text_scopes({key: value for key, value in snapshot.items() if key != 'resource_lineage_options'}):
        explanation = resolution.get("text_resolutions", {}).get(location)
        if not explanation:
            unresolved.append({"path": location, "text": text, "reason": "Resident must resolve this natural-language scope"})
    from .resource_dependencies import frozen_or_resolve
    resource_dependencies = frozen_or_resolve(snapshot, source, additional_ids=resolution.get('additional_ids', []))
    unresolved.extend({'path': 'resource_dependencies', **gap} for gap in resource_dependencies['gaps'])
    from .features import resolve_features, selection_snapshot, structural_requirements
    feature_resolution = resolve_features(snapshot, source=source, dependencies=resource_dependencies,
        required=set(resolution.get('additional_features', [])) | (set(resource_dependencies['features']) - set(structural_requirements(snapshot))))
    unresolved.extend({'path': 'feature_states', **conflict} for conflict in feature_resolution['conflicts'])
    selection = selection_snapshot(snapshot, feature_resolution)
    index = source.read_index()
    projects = {item["id"] for item in selection.get("projects", [])} | set(selection.get('manual_projects', []))
    ids = set(resolution.get("additional_ids", []))
    types = BASE_TYPES | set(resource_dependencies['types'])
    for group in selection.get("resource_types", []):
        if group["type"] != "device":
            ids.update(group.get("instances", []))
    document_ids = set()
    for kind, config in selection.get("document_indexes", {}).items():
        if config.get("enabled"):
            for address in config.get("entries", []):
                match = RESOURCE_ID.fullmatch(address)
                if match:
                    document_ids.add(address)
                    types.add(address.rsplit("_", 1)[0])
                else:
                    unresolved.append({"path": kind, "address": address, "reason": "Resolve document file address to exact copy scope"})
    ids.update(document_ids)
    ids = {identity for identity in ids if not identity.startswith("device_")}
    from .skill_scope import excluded_skill_ids
    excluded_skills = excluded_skill_ids(index, ids)
    ids.difference_update(excluded_skills)
    from .lineage_options import effective_lineages
    lineages = effective_lineages(selection, resource_dependencies)
    explicit_lineages = {item['id'] for item in selection.get('lineages', [])}
    for dependency in resource_dependencies['lineages']:
        lid = dependency['id']
        if lid not in explicit_lineages:
            for location, text in text_scopes(lineages[lid], 'resource_lineage_options.' + lid):
                if not resolution.get('text_resolutions', {}).get(location):
                    unresolved.append({'path': location, 'text': text, 'reason': 'Resident must resolve this natural-language scope'})
        reasons.setdefault(lid, []).extend(dependency['required_by'])
    for dependency in resource_dependencies['projects']:
        projects.add(dependency['id'])
        reasons.setdefault(dependency['id'], []).extend(dependency['required_by'])
    system = selection.get("system_lineage") or {}
    if system.get("selected") is None:
        unresolved.append({"path": "system_lineage.selected", "reason": "Portal System flow selection is required"})
    lineages.setdefault(SYSTEM, {"id": SYSTEM, "history": False, "workspace": {"own_outputs": False, "extra_bindings": "exclude"}})
    projection_revisions = {item['id']: item['revision'] for item in resource_dependencies['projections']}
    projection_ids = set(projection_revisions)
    projection_tables = {}
    for key, table in source.tables.items():
        if key[0] == "workbenches" and key[1] in {'apps', 'versions', 'page_packages', 'application_packages', 'projects', 'bindings', 'binding_targets', 'native_entrypoints'}:
            values = source.rows(*key, predicates={"id": projection_ids, "app_id": projection_ids})
            if key[1] == 'apps':
                values = [{**row, 'revision': projection_revisions[row['id']], 'published': projection_revisions[row['id']]} for row in values]
            elif 'revision' in table.columns:
                values = [row for row in values if row['revision'] == projection_revisions.get(row.get('app_id'))]
            projection_tables[key] = values
    installed_versions = {(row['app_id'], row['revision']) for row in projection_tables.get(('workbenches', 'versions'), [])}
    for identity, revision in projection_revisions.items():
        if (identity, revision) not in installed_versions:
            unresolved.append({'id': identity, 'revision': revision, 'reason': 'Frozen projection version is missing'})
    for projection in resource_dependencies['projections']:
        actual = sorted([{'alias': row['alias'], 'lineage_id': row['lineage_id']}
                         for row in projection_tables.get(('workbenches', 'bindings'), []) if row['app_id'] == projection['id']], key=lambda row: row['alias'])
        if actual != projection['bindings']:
            unresolved.append({'id': projection['id'], 'reason': 'Published projection bindings differ from frozen dependencies'})
    for row in projection_tables.get(("workbenches", "bindings"), []):
        lid = row["lineage_id"]
        if lid not in lineages:
            unresolved.append({'id': lid, 'reason': 'Projection binding points outside the frozen dependency scope'})
    ids.update(projection_ids)
    ids.update(lineages)
    from .projection_dependencies import declarations as projection_declarations, resolve_files as projection_files
    projection_scope = {'files': [], 'path_mappings': [], 'workspaces': {}, 'repositories': {}}
    if 'projection_resources' in resource_dependencies:
        try:
            expected_resources = resource_dependencies['projection_resources']
            if projection_declarations(source, resource_dependencies['projections']) != expected_resources:
                raise ValueError('Projection resource declaration changed after dependency snapshot')
            projection_scope = projection_files(source, expected_resources)
            ids.update(projection_scope['repositories'])
            ids.update(projection_scope['workspaces'])
            if projection_scope['repositories']:
                types.add('repo')
        except (ValueError, OSError) as error:
            unresolved.append({'reason': str(error), 'path': 'projection_resources'})
    for identity in sorted(ids):
        record = index.get(identity)
        if not record:
            unresolved.append({"id": identity, "reason": "Selected object missing from source resource index"})
            continue
        for project in record.get("attributes", {}).get("project_ids", []):
            projects.add(project)
            reasons.setdefault(project, []).append("required by " + identity)
    rows = source.definition_rows(lineages)
    if 'project_principals' in resource_dependencies:
        expected_entries = {item['project_id']: item['lineage_id'] for item in resource_dependencies['project_principals']}
        current_entries = {item['project_id']: item['lineage_id'] for item in
                           source.rows('lineage', 'project_lineage_entries', predicates={'project_id': projects})}
        if current_entries != expected_entries:
            unresolved.append({'reason': 'Project principal bindings changed after dependency snapshot; save a new configuration/run'})
    # Entry graphs can reference leaders in unselected sibling Lineages. Keep
    # only references whose owning Resident is actually exported, without
    # selecting those siblings or transferring their ownership.
    selected_residents = {row['resident_id'] for row in rows.get(('lineage', 'residents'), [])}
    reference_key = ('lineage', 'lineage_node_references')
    omitted_references = {(row['lineage_id'], row['node_id']) for row in rows.get(reference_key, [])
                          if row['node_id'] not in selected_residents}
    rows[reference_key] = [row for row in rows.get(reference_key, [])
                           if (row['lineage_id'], row['node_id']) not in omitted_references]
    for name in ('lineage_layout_positions', 'lineage_edges', 'lineage_observation_targets'):
        key = ('lineage', name)
        if key in rows:
            rows[key] = [row for row in rows[key] if not any(
                (row.get('lineage_id'), row.get(field)) in omitted_references
                for field in ('node_id', 'source_node_id', 'target_node_id'))]
    owned_nodes = {(row['lineage_id'], row['node_id']) for row in rows.get(('lineage', 'lineage_nodes'), [])}
    bound_lineages = {(row['app_id'], row['alias']): row['lineage_id']
                     for row in projection_tables.get(('workbenches', 'bindings'), [])}
    for target in projection_tables.get(('workbenches', 'binding_targets'), []):
        lid = bound_lineages.get((target['app_id'], target['alias']))
        if (lid, target['target_id']) not in owned_nodes:
            unresolved.append({'id': target['target_id'], 'projection': target['app_id'],
                               'reason': 'Published projection target is missing from its selected Lineage structure'})
    actual_lineages = rows.get(("lineage", "lineages"), [])
    for row in actual_lineages:
        if row.get("project_id"):
            projects.add(row["project_id"])
    # Portal System flows are verified from authoritative node/block membership.
    system_nodes = {row["node_id"] for row in rows.get(("lineage", "lineage_nodes"), []) if row["lineage_id"] == SYSTEM}
    keep_system = set()
    for flow in system.get("selected") or []:
        identity = flow["id"] if isinstance(flow, dict) else flow
        if identity in system_nodes:
            keep_system.add(identity)
        elements = [row["element_id"] for row in rows.get(("lineage", "function_block_elements"), []) if row["function_block_id"] == identity]
        keep_system.update(elements)
        if not elements and identity not in system_nodes:
            unresolved.append({"id": identity, "reason": "Selected Portal System flow is missing"})
    excluded = system_nodes - keep_system
    selected_blocks = {item['id'] if isinstance(item, dict) else item for item in system.get('selected') or []}
    excluded.update(row['function_block_id'] for row in rows.get(('lineage','function_blocks'), [])
                    if row.get('lineage_id') == SYSTEM and row['function_block_id'] not in selected_blocks)
    excluded.update(row['family_id'] for row in rows.get(('lineage', 'families'), [])
                    if row.get('lineage_id') == SYSTEM and not keep_system)
    for key, values in rows.items():
        rows[key] = [row for row in values if not any(row.get(field) in excluded for field in ("resident_id", "node_id", "source_node_id", "target_node_id", "element_id", "object_id", "function_block_id", "family_id"))]
    for row in rows.get(('lineage', 'lineages'), []):
        if row.get('leader_resident_id') in excluded:
            row['leader_resident_id'] = ''
    residents = {row["resident_id"]: row for row in rows.get(("lineage", "residents"), [])}
    resident_lineages = {row["node_id"]: row["lineage_id"] for row in rows.get(("lineage", "lineage_nodes"), []) if row.get("node_type") == "resident"}
    history_lineages = {identity for identity, options in lineages.items() if options.get("history")}
    history_residents = {rid for rid, lid in resident_lineages.items() if lid in history_lineages}
    rows.update(source.historical_rows(history_residents, history_lineages))
    applications = {row['application']: row for row in rows.get(('lineage', 'application_targets'), [])}
    # Explicitly selected Residents retain their native application assignment.
    # This exports routing, not new members, authorization, or queued work.
    for row in source.rows('lineage', 'application_targets', predicates={'resident_id': residents}):
        if row.get('resident_id') in residents:
            applications[row['application']] = row
    expected_applications = {item['application']: item for item in resource_dependencies['application_bindings']}
    seen_applications = set()
    for row in source.rows('lineage', 'application_targets', predicates={'application': resource_dependencies['application_targets']}):
        seen_applications.add(row['application'])
        expected = expected_applications[row['application']]
        if row.get('lineage_id') != expected['lineage_id'] or row.get('revision') != expected['revision']:
            unresolved.append({'id': row['application'], 'reason': 'Application binding changed after dependency snapshot; save a new configuration/run'})
        else:
            applications[row['application']] = row
    for missing in sorted(set(expected_applications) - seen_applications):
        unresolved.append({'id': missing, 'reason': 'Frozen application binding is missing'})
    rows['lineage', 'application_targets'] = list(applications.values())
    ids.update(residents)
    for key, values in rows.items():
        if key[1] in {"families", "function_blocks"}:
            ids.update(row["family_id" if key[1] == "families" else "function_block_id"] for row in values)
    ids.update(projects)
    files = []
    generated = {}
    mappings = []
    workspaces = {}
    own = {row["owned_workspace_id"]: rid for rid, row in residents.items() if row.get("owned_workspace_id")}
    bindings = rows.get(("lineage", "resident_workspace_bindings"), [])
    kept_bindings = []
    for lid, options in lineages.items():
        if options.get('workspace', {}).get('extra_bindings') == 'unset':
            unresolved.append({'id': lid, 'reason': 'Workspace extra binding scope is unset'})
    for binding in bindings:
        lid = resident_lineages.get(binding["resident_id"])
        options = lineages.get(lid, {}).get("workspace", {})
        if options.get("extra_bindings") == "all":
            kept_bindings.append(binding)
        elif options.get("extra_bindings") == "partial":
            if binding["workspace_id"] in resolution.get("workspace_ids", []):
                kept_bindings.append(binding)
        elif options.get("extra_bindings") not in {"exclude", "unset"}:
            unresolved.append({"id": lid, "reason": "Workspace extra binding scope is unset"})
    rows[("lineage", "resident_workspace_bindings")] = kept_bindings
    workspace_ids = set(own) | {row["workspace_id"] for row in kept_bindings}
    for row in source.rows("workspace", "workspaces", predicates={"workspace_id": workspace_ids}):
        identity = row["workspace_id"]
        if identity in own:
            rid = own[identity]
            lid = resident_lineages[rid]
            # Native reads and default-cwd validation derive the Resident
            # folder from share_root/Agents; the Workspace row must agree.
            relative = f"Agents/{lid}/residents/{rid}/workspace"
            folder = f"data/share_root/Agents/{lid}/residents/{rid}"
            generated[folder + "/resident.md"] = f"# {residents[rid]['title']}\n\nResident: {rid}\nLineage: {lid}\n"
            generated[folder + "/diary.md"] = "# Continuity\n\n" + ("Selected history is in the distribution database.\n" if lid in history_lineages else "Initial configuration; no source history included.\n")
            options = lineages[lid].get("workspace", {})
            include = options.get("own_outputs") is True
            if options.get("own_outputs") is None:
                unresolved.append({"id": lid, "reason": "Own Workspace output selection is unset"})
            generated[folder + "/workspaces.md"] = f"# Workspaces\n\nOwned: {identity} → ${{share_root}}/{relative}\n" + "".join(f"\n{binding['workspace_id']} → ${{share_root}}/Workspaces/{binding['workspace_id']}\n" for binding in kept_bindings if binding["resident_id"] == rid)
            generated[f"data/share_root/{relative}/.tfi-workspace"] = identity + "\n"
        else:
            relative = f"Workspaces/{identity}"
            include = True
        mappings.append((row["path"], "${share_root}/" + relative))
        if include:
            files.append({"source": row["path"], "target": "data/share_root/" + relative})
        workspaces[identity] = row
    if set(workspaces) != workspace_ids:
        unresolved.append({"ids": sorted(workspace_ids - set(workspaces)), "reason": "Workspace identity or path missing"})
    # Required evidence reuses an existing canonical Workspace binding.
    for identity, row in projection_scope['workspaces'].items():
        actual = workspaces.get(identity, row)
        expected = row['path']
        actual_target = dict(mappings).get(actual['path'], actual['path'])
        for item in projection_scope['files']:
            prefix = expected.replace('${share_root}', 'data/share_root') + '/'
            if item['target'].startswith(prefix):
                item['target'] = actual_target.replace('${share_root}', 'data/share_root') + '/' + item['target'][len(prefix):]
        prefix = expected.replace('${share_root}', 'data/share_root') + '/'
        projection_scope['generated_files'] = {
            (actual_target.replace('${share_root}', 'data/share_root') + '/' + key[len(prefix):]
             if key.startswith(prefix) else key): text
            for key, text in projection_scope.get('generated_files', {}).items()}
        projection_scope['path_mappings'] = [(old, actual_target if new == expected else new)
                                            for old, new in projection_scope['path_mappings']]
        workspaces[identity] = actual
    files.extend(projection_scope['files'])
    generated.update(projection_scope.get('generated_files', {}))
    mappings.extend(projection_scope['path_mappings'])
    rows[("workspace", "workspaces")] = list(workspaces.values())
    ids.update(workspaces)
    registry = []
    for row in source.rows("resource_registry", "registry_documents", predicates={"document_id": ids | {"projects"}}):
        collection, identity, document = row["collection"], row["document_id"], row["document_json"]
        if isinstance(document, str):
            import json
            document = json.loads(document)
        if collection == "project_registry":
            document = {**document, "projects": [p for p in document.get("projects", []) if p["id"] in projects]}
            for project in document["projects"]:
                project["path"] = "/Projects/" + project["id"]
                generated[f"data/share_root/Projects/{project['id']}/.tfi-project"] = project["id"] + "\n"
            registry.append({**row, "document_json": document})
        elif identity in ids and not identity.startswith("device_"):
            if collection == 'repo':
                # Release copies contain source bytes without Git storage. The
                # native Repo contract must survive automatic reconciliation
                # and must not advertise the source machine's Git capabilities.
                from management_portal.resource_identity_backend import repo_source_kind
                repo_source_kind(document)
                if identity in projection_scope['repositories']:
                    root = '${code_root}'
                else:
                    scope = resource_file_scope(identity, index[identity])
                    root = '${share_root}/' + scope['target'].removeprefix('data/share_root/')
                document = {**document, 'source_kind': 'exported_tree',
                            'canonical_checkout': root, 'checkouts': [{'path': root}],
                            'remotes': [], 'push_enabled': False, 'ssh_key': ''}
                registry.append({**row, 'document_json': document})
            else:
                registry.append(row)
    rows[("resource_registry", "registry_documents")] = registry
    # Compiler and entry-file choices are resource configuration. Export only
    # selected articles; build reports/logs remain source runtime history.
    article_ids = {identity for identity in ids if index.get(identity, {}).get('resource_type') == 'article'}
    if ('article', 'build_settings') in source.tables:
        rows['article', 'build_settings'] = source.rows('article', 'build_settings', predicates={'article_id': article_ids})
    collections = {row["collection"] for row in registry}
    rows[("resource_registry", "registry_collections")] = source.rows("resource_registry", "registry_collections", predicates={"collection": collections})
    roles = source.rows("execution_accounts", "backend_roles")
    rows[("execution_accounts", "backend_roles")] = [{**row, "account_id": None} for row in roles]
    # External files and knowledge are copied once; links never add instances.
    from .core_skills import canonical_resources
    core_resources = canonical_resources()
    for identity in sorted(ids - set(residents) - set(workspaces) - projects):
        if identity in projection_scope['repositories']:
            continue
        record = index.get(identity, {})
        core = core_resources.get(identity) if record.get('resource_type') == 'skill' else None
        if core:
            # Core files are supplied once by export_core_skills. Relocate the
            # indexed primary file AND its folder, including historical names.
            destination = '${share_root}/Skills/' + core.name
            old_file = record.get('source', {}).get('path')
            old_root = record.get('attributes', {}).get('content_root')
            if old_file:
                mappings.append((old_file, destination + '/SKILL.md'))
            if old_root:
                mappings.append((old_root, destination))
            continue
        try:
            item = resource_file_scope(identity, record)
        except ValueError as error:
            unresolved.append({"id": identity, "reason": str(error)})
            continue
        if item:
            mappings.append((item['source'], '${share_root}/' + item['target'].removeprefix('data/share_root/')))
            files.append(item)
    # Folder truth and source paths in selected registry definitions relocate too.
    for lid in lineages:
        path = index.get(lid, {}).get("source", {}).get("path")
        if path:
            mappings.append((path, "${share_root}/Agents/" + lid))
    template_ids = {item["id"] for item in selection.get("society_templates", {}).get("selected", [])}
    rows[("templates", "templates")] = source.rows("templates", "templates", predicates={"template_id": template_ids})
    # Keep the installation ledger so unselected builtin definitions cannot return.
    rows[("templates", "installed_builtins")] = source.rows("templates", "installed_builtins")
    ids.update(template_ids)
    schemes = selection.get("permission_selection", {}).get("schemes", [])
    from .permissions import selected_permissions
    permissions = selected_permissions(source, schemes, ids)
    rows.update({("permission", key): value for key, value in permissions.items() if ("permission", key) in source.tables})
    rows.update(projection_tables)
    # Published application packages refer to immutable artifacts outside the
    # repository. Copy those exact bytes; their existing build hashes remain
    # the authority and must not be invalidated by textual transformations.
    for row in projection_tables.get(('workbenches', 'application_packages'), []):
        import json
        build = json.loads(row['build']) if isinstance(row['build'], str) else row['build']
        if build.get('status') != 'ready':
            unresolved.append({'id': row['app_id'], 'reason': 'Selected workbench has no ready application artifact'})
            continue
        from management_portal.workbench_projections.application_build import _artifact_root, read_artifact
        for asset in build.get('files', {}):
            read_artifact(build, asset)
        artifact = _artifact_root() / build['artifact_hash']
        files.append({'source': str(artifact), 'target': 'data/staging_root/shared/projection_applications/' + build['artifact_hash'], 'immutable_artifact': True})
    for key in source.tables:
        if key[1] in {"lineage_meta", "agent_store_meta", "registry_meta"}:
            rows[key] = source.rows(*key)
    rows = {key: values for key, values in rows.items() if key in source.tables}
    features = set(feature_resolution['included_features'])
    for identity, state in feature_resolution['states'].items():
        for parent in state['code_required_by']:
            reasons.setdefault(identity, []).append('required by ' + parent)
    for kind, config in selection.get("document_indexes", {}).items():
        if config.get("enabled"):
            entries = [identity for identity in config.get("entries", []) if identity in ids]
            generated["code/" + kind] = "# " + kind + "\n\n" + "\n".join(f"- [{index.get(identity, {}).get('title', identity)}](/r/{identity})" for identity in entries) + "\n"
    from .file_scope import external_path_mappings
    mappings.extend(external_path_mappings(files, resolution.get('file_scope_policy')))
    external = [{"kind": "backend_role", "role": row["role"], "model": row.get("model", ""), "status": "needs_configuration"} for row in roles]
    from .privacy import source_users
    return {"source_usernames": sorted(set(source_users(rows)) | set(source.source_identity_names() if hasattr(source, "source_identity_names") else [])), "selected_ids": sorted(ids), "resource_types": sorted(types), "dependency_reasons": reasons,
            "unresolved": unresolved, "features": sorted(features), "files": files,
            "excluded_resources": [{'id': identity, 'reason': 'site_only_skill'} for identity in sorted(excluded_skills)],
            "generated_files": generated, "path_mappings": mappings, "tables": rows,
            "feature_resolution": {k:v for k,v in feature_resolution.items() if k != 'catalog'},
            "resource_dependencies": resource_dependencies,
            "projection_code_repositories": projection_scope['repositories'],
            "lineage_options": deepcopy(lineages),
            "anonymization": deepcopy(ANONYMIZATION),
            "file_scope_policy": {**deepcopy(resolution.get('file_scope_policy', {})),
                                  'reset_roots': projection_scope.get('reset_roots', [])},
            "code_extras": resolution.get("code_extras", []), "external_requirements": external,
            **({'upgrade': deepcopy(resolution['upgrade'])} if 'upgrade' in resolution else {})}
