"""Resolve selected IDs against one source snapshot, without link recursion."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .content import registered_content_path
from .features import SYSTEM
from .sanitize import RESOURCE_ID, ANONYMIZATION
from .store import digest

BASE_TYPES = {"user", "project", "lineage", "resident", "family", "workspace", "workbench"}
TEXT_SCOPE_REASON = "Resident must resolve this natural-language scope"
# The published workbench tables a frozen projection exports, by app id.
PROJECTION_TABLES = {'apps', 'versions', 'page_packages', 'application_packages', 'projects', 'bindings',
                     'binding_targets', 'native_entrypoints'}
# A row that names an excluded Portal System member in one of these goes with it.
SYSTEM_MEMBER_FIELDS = ("resident_id", "node_id", "source_node_id", "target_node_id", "element_id", "object_id",
                        "function_block_id", "family_id")
METADATA_TABLES = {"lineage_meta", "agent_store_meta", "registry_meta"}


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
    return _ExportScope(snapshot, source, resolution or {}).resolve()


class _ExportScope:
    """The export scope of one configuration snapshot in one source snapshot.

    ``resolve`` collects it step by step: each step adds selected IDs, rows,
    files, generated files and path mappings, and records what it cannot
    resolve in ``unresolved`` (the order of that list is part of the result).
    """

    def __init__(self, snapshot, source, resolution):
        self.snapshot = snapshot
        self.source = source
        self.resolution = resolution
        self.unresolved = []
        self.reasons = {}
        self.files = []
        self.generated = {}
        self.mappings = []
        self.workspaces = {}

    def resolve(self):
        if self.resolution and self.resolution.get("snapshot_sha256") != digest(self.snapshot):
            raise ValueError("Resident resolution does not match the immutable configuration snapshot")
        self._require_resolved_text(text_scopes(
            {key: value for key, value in self.snapshot.items() if key != 'resource_lineage_options'}))
        self._resolve_dependencies()
        self._collect_selected_ids()
        self._collect_lineages()
        self._collect_projections()
        self._collect_projection_resources()
        self._collect_record_projects()
        self._read_definition_rows()
        self._omit_unexported_references()
        self._check_projection_targets()
        self._collect_lineage_projects()
        self._keep_selected_system_flows()
        self._read_history()
        self._collect_application_targets()
        self._collect_member_ids()
        self._collect_workspaces()
        self._relocate_projection_workspaces()
        self._collect_registry()
        self._collect_resource_settings()
        self._collect_resource_files()
        self._collect_templates_and_permissions()
        self._collect_application_artifacts()
        self._keep_schema_metadata()
        return self._result()

    def _require_resolved_text(self, scopes):
        for location, text in scopes:
            if not self.resolution.get("text_resolutions", {}).get(location):
                self.unresolved.append({"path": location, "text": text, "reason": TEXT_SCOPE_REASON})

    def _projection_rows(self, table):
        return self.projection_tables.get(("workbenches", table), [])

    def _resolve_dependencies(self):
        from .resource_dependencies import frozen_or_resolve
        self.dependencies = frozen_or_resolve(self.snapshot, self.source,
                                              additional_ids=self.resolution.get('additional_ids', []))
        self.unresolved.extend({'path': 'resource_dependencies', **gap} for gap in self.dependencies['gaps'])
        from .features import resolve_features, selection_snapshot, structural_requirements
        required = set(self.resolution.get('additional_features', [])) | (
            set(self.dependencies['features']) - set(structural_requirements(self.snapshot)))
        self.feature_resolution = resolve_features(self.snapshot, source=self.source, dependencies=self.dependencies,
                                                   required=required)
        self.unresolved.extend({'path': 'feature_states', **conflict} for conflict in self.feature_resolution['conflicts'])
        self.selection = selection_snapshot(self.snapshot, self.feature_resolution)
        self.index = self.source.read_index()

    def _collect_selected_ids(self):
        selection = self.selection
        self.projects = {item["id"] for item in selection.get("projects", [])} | set(selection.get('manual_projects', []))
        ids = set(self.resolution.get("additional_ids", []))
        self.types = BASE_TYPES | set(self.dependencies['types'])
        for group in selection.get("resource_types", []):
            if group["type"] != "device":
                ids.update(group.get("instances", []))
        ids.update(self._document_ids())
        self.ids = {identity for identity in ids if not identity.startswith("device_")}
        from .skill_scope import excluded_skill_ids
        self.excluded_skills = excluded_skill_ids(self.index, self.ids)
        self.ids.difference_update(self.excluded_skills)

    def _document_ids(self):
        document_ids = set()
        for kind, config in self.selection.get("document_indexes", {}).items():
            if config.get("enabled"):
                for address in config.get("entries", []):
                    if RESOURCE_ID.fullmatch(address):
                        document_ids.add(address)
                        self.types.add(address.rsplit("_", 1)[0])
                    else:
                        self.unresolved.append({"path": kind, "address": address, "reason": "Resolve document file address to exact copy scope"})
        return document_ids

    def _collect_lineages(self):
        from .lineage_options import effective_lineages
        self.lineages = effective_lineages(self.selection, self.dependencies)
        explicit_lineages = {item['id'] for item in self.selection.get('lineages', [])}
        for dependency in self.dependencies['lineages']:
            lid = dependency['id']
            if lid not in explicit_lineages:
                self._require_resolved_text(text_scopes(self.lineages[lid], 'resource_lineage_options.' + lid))
            self.reasons.setdefault(lid, []).extend(dependency['required_by'])
        for dependency in self.dependencies['projects']:
            self.projects.add(dependency['id'])
            self.reasons.setdefault(dependency['id'], []).extend(dependency['required_by'])
        self.system = self.selection.get("system_lineage") or {}
        if self.system.get("selected") is None:
            self.unresolved.append({"path": "system_lineage.selected", "reason": "Portal System flow selection is required"})
        self.lineages.setdefault(SYSTEM, {"id": SYSTEM, "history": False, "workspace": {"own_outputs": False, "extra_bindings": "exclude"}})

    def _collect_projections(self):
        projections = self.dependencies['projections']
        revisions = {item['id']: item['revision'] for item in projections}
        projection_ids = set(revisions)
        self.projection_tables = {}
        for key, table in self.source.tables.items():
            if key[0] == "workbenches" and key[1] in PROJECTION_TABLES:
                values = self.source.rows(*key, predicates={"id": projection_ids, "app_id": projection_ids})
                if key[1] == 'apps':
                    values = [{**row, 'revision': revisions[row['id']], 'published': revisions[row['id']]} for row in values]
                elif 'revision' in table.columns:
                    values = [row for row in values if row['revision'] == revisions.get(row.get('app_id'))]
                self.projection_tables[key] = values
        installed_versions = {(row['app_id'], row['revision']) for row in self._projection_rows('versions')}
        for identity, revision in revisions.items():
            if (identity, revision) not in installed_versions:
                self.unresolved.append({'id': identity, 'revision': revision, 'reason': 'Frozen projection version is missing'})
        for projection in projections:
            actual = sorted([{'alias': row['alias'], 'lineage_id': row['lineage_id']}
                             for row in self._projection_rows('bindings') if row['app_id'] == projection['id']], key=lambda row: row['alias'])
            if actual != projection['bindings']:
                self.unresolved.append({'id': projection['id'], 'reason': 'Published projection bindings differ from frozen dependencies'})
        for row in self._projection_rows('bindings'):
            lid = row["lineage_id"]
            if lid not in self.lineages:
                self.unresolved.append({'id': lid, 'reason': 'Projection binding points outside the frozen dependency scope'})
        self.ids.update(projection_ids)
        self.ids.update(self.lineages)

    def _collect_projection_resources(self):
        from .projection_dependencies import declarations as projection_declarations, resolve_files as projection_files
        self.projection_scope = {'files': [], 'path_mappings': [], 'workspaces': {}, 'repositories': {}}
        if 'projection_resources' not in self.dependencies:
            return
        try:
            expected_resources = self.dependencies['projection_resources']
            if projection_declarations(self.source, self.dependencies['projections']) != expected_resources:
                raise ValueError('Projection resource declaration changed after dependency snapshot')
            self.projection_scope = projection_files(self.source, expected_resources)
            self.ids.update(self.projection_scope['repositories'])
            self.ids.update(self.projection_scope['workspaces'])
            if self.projection_scope['repositories']:
                self.types.add('repo')
        except (ValueError, OSError) as error:
            self.unresolved.append({'reason': str(error), 'path': 'projection_resources'})

    def _collect_record_projects(self):
        for identity in sorted(self.ids):
            record = self.index.get(identity)
            if not record:
                self.unresolved.append({"id": identity, "reason": "Selected object missing from source resource index"})
                continue
            for project in record.get("attributes", {}).get("project_ids", []):
                self.projects.add(project)
                self.reasons.setdefault(project, []).append("required by " + identity)

    def _read_definition_rows(self):
        self.rows = self.source.definition_rows(self.lineages)
        if 'project_principals' in self.dependencies:
            expected_entries = {item['project_id']: item['lineage_id'] for item in self.dependencies['project_principals']}
            current_entries = {item['project_id']: item['lineage_id'] for item in
                               self.source.rows('lineage', 'project_lineage_entries', predicates={'project_id': self.projects})}
            if current_entries != expected_entries:
                self.unresolved.append({'reason': 'Project principal bindings changed after dependency snapshot; save a new configuration/run'})

    def _omit_unexported_references(self):
        # Entry graphs can reference leaders in unselected sibling Lineages. Keep
        # only references whose owning Resident is actually exported, without
        # selecting those siblings or transferring their ownership.
        rows = self.rows
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

    def _check_projection_targets(self):
        owned_nodes = {(row['lineage_id'], row['node_id']) for row in self.rows.get(('lineage', 'lineage_nodes'), [])}
        bound_lineages = {(row['app_id'], row['alias']): row['lineage_id'] for row in self._projection_rows('bindings')}
        for target in self._projection_rows('binding_targets'):
            lid = bound_lineages.get((target['app_id'], target['alias']))
            if (lid, target['target_id']) not in owned_nodes:
                self.unresolved.append({'id': target['target_id'], 'projection': target['app_id'],
                                        'reason': 'Published projection target is missing from its selected Lineage structure'})

    def _collect_lineage_projects(self):
        for row in self.rows.get(("lineage", "lineages"), []):
            if row.get("project_id"):
                self.projects.add(row["project_id"])

    def _keep_selected_system_flows(self):
        excluded = self._excluded_system_members()
        rows = self.rows
        for key, values in rows.items():
            rows[key] = [row for row in values if not any(row.get(field) in excluded for field in SYSTEM_MEMBER_FIELDS)]
        for row in rows.get(('lineage', 'lineages'), []):
            if row.get('leader_resident_id') in excluded:
                row['leader_resident_id'] = ''

    def _excluded_system_members(self):
        # Portal System flows are verified from authoritative node/block membership.
        rows = self.rows
        system_nodes = {row["node_id"] for row in rows.get(("lineage", "lineage_nodes"), []) if row["lineage_id"] == SYSTEM}
        keep_system = set()
        for flow in self.system.get("selected") or []:
            identity = flow["id"] if isinstance(flow, dict) else flow
            if identity in system_nodes:
                keep_system.add(identity)
            elements = [row["element_id"] for row in rows.get(("lineage", "function_block_elements"), []) if row["function_block_id"] == identity]
            keep_system.update(elements)
            if not elements and identity not in system_nodes:
                self.unresolved.append({"id": identity, "reason": "Selected Portal System flow is missing"})
        excluded = system_nodes - keep_system
        selected_blocks = {item['id'] if isinstance(item, dict) else item for item in self.system.get('selected') or []}
        excluded.update(row['function_block_id'] for row in rows.get(('lineage', 'function_blocks'), [])
                        if row.get('lineage_id') == SYSTEM and row['function_block_id'] not in selected_blocks)
        excluded.update(row['family_id'] for row in rows.get(('lineage', 'families'), [])
                        if row.get('lineage_id') == SYSTEM and not keep_system)
        return excluded

    def _read_history(self):
        self.residents = {row["resident_id"]: row for row in self.rows.get(("lineage", "residents"), [])}
        self.resident_lineages = {row["node_id"]: row["lineage_id"] for row in self.rows.get(("lineage", "lineage_nodes"), []) if row.get("node_type") == "resident"}
        self.history_lineages = {identity for identity, options in self.lineages.items() if options.get("history")}
        history_residents = {rid for rid, lid in self.resident_lineages.items() if lid in self.history_lineages}
        self.rows.update(self.source.historical_rows(history_residents, self.history_lineages))

    def _collect_application_targets(self):
        applications = {row['application']: row for row in self.rows.get(('lineage', 'application_targets'), [])}
        # Explicitly selected Residents retain their native application assignment.
        # This exports routing, not new members, authorization, or queued work.
        for row in self.source.rows('lineage', 'application_targets', predicates={'resident_id': self.residents}):
            if row.get('resident_id') in self.residents:
                applications[row['application']] = row
        expected_applications = {item['application']: item for item in self.dependencies['application_bindings']}
        seen_applications = set()
        for row in self.source.rows('lineage', 'application_targets', predicates={'application': self.dependencies['application_targets']}):
            seen_applications.add(row['application'])
            expected = expected_applications[row['application']]
            if row.get('lineage_id') != expected['lineage_id'] or row.get('revision') != expected['revision']:
                self.unresolved.append({'id': row['application'], 'reason': 'Application binding changed after dependency snapshot; save a new configuration/run'})
            else:
                applications[row['application']] = row
        for missing in sorted(set(expected_applications) - seen_applications):
            self.unresolved.append({'id': missing, 'reason': 'Frozen application binding is missing'})
        self.rows['lineage', 'application_targets'] = list(applications.values())

    def _collect_member_ids(self):
        self.ids.update(self.residents)
        for key, values in self.rows.items():
            if key[1] in {"families", "function_blocks"}:
                self.ids.update(row["family_id" if key[1] == "families" else "function_block_id"] for row in values)
        self.ids.update(self.projects)

    def _collect_workspaces(self):
        own = {row["owned_workspace_id"]: rid for rid, row in self.residents.items() if row.get("owned_workspace_id")}
        kept_bindings = self._kept_workspace_bindings()
        self.rows[("lineage", "resident_workspace_bindings")] = kept_bindings
        workspace_ids = set(own) | {row["workspace_id"] for row in kept_bindings}
        for row in self.source.rows("workspace", "workspaces", predicates={"workspace_id": workspace_ids}):
            identity = row["workspace_id"]
            if identity in own:
                relative, include = self._owned_workspace(identity, own[identity], kept_bindings)
            else:
                relative, include = f"Workspaces/{identity}", True
            self.mappings.append((row["path"], "${share_root}/" + relative))
            if include:
                self.files.append({"source": row["path"], "target": "data/share_root/" + relative})
            self.workspaces[identity] = row
        if set(self.workspaces) != workspace_ids:
            self.unresolved.append({"ids": sorted(workspace_ids - set(self.workspaces)), "reason": "Workspace identity or path missing"})

    def _kept_workspace_bindings(self):
        """The Resident Workspace bindings each Lineage's ``extra_bindings`` keeps."""
        for lid, options in self.lineages.items():
            if options.get('workspace', {}).get('extra_bindings') == 'unset':
                self.unresolved.append({'id': lid, 'reason': 'Workspace extra binding scope is unset'})
        keeps = {
            "all": lambda binding: True,
            "partial": lambda binding: binding["workspace_id"] in self.resolution.get("workspace_ids", []),
            "exclude": lambda binding: False,
            "unset": lambda binding: False,
        }
        kept_bindings = []
        for binding in self.rows.get(("lineage", "resident_workspace_bindings"), []):
            lid = self.resident_lineages.get(binding["resident_id"])
            keep = keeps.get(self.lineages.get(lid, {}).get("workspace", {}).get("extra_bindings"))
            if keep is None:
                self.unresolved.append({"id": lid, "reason": "Workspace extra binding scope is unset"})
            elif keep(binding):
                kept_bindings.append(binding)
        return kept_bindings

    def _owned_workspace(self, identity, rid, kept_bindings):
        """The share-root folder of a Resident's own Workspace, and whether its outputs are copied."""
        lid = self.resident_lineages[rid]
        # Native reads and default-cwd validation derive the Resident
        # folder from share_root/Agents; the Workspace row must agree.
        relative = f"Agents/{lid}/residents/{rid}/workspace"
        folder = f"data/share_root/Agents/{lid}/residents/{rid}"
        self.generated[folder + "/resident.md"] = f"# {self.residents[rid]['title']}\n\nResident: {rid}\nLineage: {lid}\n"
        self.generated[folder + "/diary.md"] = "# Continuity\n\n" + ("Selected history is in the distribution database.\n" if lid in self.history_lineages else "Initial configuration; no source history included.\n")
        options = self.lineages[lid].get("workspace", {})
        include = options.get("own_outputs") is True
        if options.get("own_outputs") is None:
            self.unresolved.append({"id": lid, "reason": "Own Workspace output selection is unset"})
        self.generated[folder + "/workspaces.md"] = f"# Workspaces\n\nOwned: {identity} → ${{share_root}}/{relative}\n" + "".join(f"\n{binding['workspace_id']} → ${{share_root}}/Workspaces/{binding['workspace_id']}\n" for binding in kept_bindings if binding["resident_id"] == rid)
        self.generated[f"data/share_root/{relative}/.tfi-workspace"] = identity + "\n"
        return relative, include

    def _relocate_projection_workspaces(self):
        # Required evidence reuses an existing canonical Workspace binding.
        scope = self.projection_scope
        for identity, row in scope['workspaces'].items():
            actual = self.workspaces.get(identity, row)
            expected = row['path']
            actual_target = dict(self.mappings).get(actual['path'], actual['path'])
            prefix = expected.replace('${share_root}', 'data/share_root') + '/'
            for item in scope['files']:
                if item['target'].startswith(prefix):
                    item['target'] = actual_target.replace('${share_root}', 'data/share_root') + '/' + item['target'][len(prefix):]
            scope['generated_files'] = {
                (actual_target.replace('${share_root}', 'data/share_root') + '/' + key[len(prefix):]
                 if key.startswith(prefix) else key): text
                for key, text in scope.get('generated_files', {}).items()}
            scope['path_mappings'] = [(old, actual_target if new == expected else new)
                                      for old, new in scope['path_mappings']]
            self.workspaces[identity] = actual
        self.files.extend(scope['files'])
        self.generated.update(scope.get('generated_files', {}))
        self.mappings.extend(scope['path_mappings'])
        self.rows[("workspace", "workspaces")] = list(self.workspaces.values())
        self.ids.update(self.workspaces)

    def _collect_registry(self):
        self.registry = []
        for row in self.source.rows("resource_registry", "registry_documents", predicates={"document_id": self.ids | {"projects"}}):
            collection, identity, document = row["collection"], row["document_id"], row["document_json"]
            if isinstance(document, str):
                document = json.loads(document)
            if collection == "project_registry":
                self.registry.append(self._project_registry_row(row, document))
            elif identity in self.ids and not identity.startswith("device_"):
                self.registry.append(self._repo_registry_row(row, identity, document) if collection == 'repo' else row)
        self.rows[("resource_registry", "registry_documents")] = self.registry

    def _project_registry_row(self, row, document):
        document = {**document, "projects": [p for p in document.get("projects", []) if p["id"] in self.projects]}
        for project in document["projects"]:
            project["path"] = "/Projects/" + project["id"]
            self.generated[f"data/share_root/Projects/{project['id']}/.tfi-project"] = project["id"] + "\n"
        return {**row, "document_json": document}

    def _repo_registry_row(self, row, identity, document):
        # Release copies contain source bytes without Git storage. The
        # native Repo contract must survive automatic reconciliation
        # and must not advertise the source machine's Git capabilities.
        from management_portal.resource_identity_backend import repo_source_kind
        repo_source_kind(document)
        if identity in self.projection_scope['repositories']:
            root = '${code_root}'
        else:
            scope = resource_file_scope(identity, self.index[identity])
            root = '${share_root}/' + scope['target'].removeprefix('data/share_root/')
        document = {**document, 'source_kind': 'exported_tree',
                    'canonical_checkout': root, 'checkouts': [{'path': root}],
                    'remotes': [], 'push_enabled': False, 'ssh_key': ''}
        return {**row, 'document_json': document}

    def _collect_resource_settings(self):
        # Compiler and entry-file choices are resource configuration. Export only
        # selected articles; build reports/logs remain source runtime history.
        article_ids = {identity for identity in self.ids if self.index.get(identity, {}).get('resource_type') == 'article'}
        if ('article', 'build_settings') in self.source.tables:
            self.rows['article', 'build_settings'] = self.source.rows('article', 'build_settings', predicates={'article_id': article_ids})
        collections = {row["collection"] for row in self.registry}
        self.rows[("resource_registry", "registry_collections")] = self.source.rows("resource_registry", "registry_collections", predicates={"collection": collections})
        self.roles = self.source.rows("execution_accounts", "backend_roles")
        self.rows[("execution_accounts", "backend_roles")] = [{**row, "account_id": None} for row in self.roles]

    def _collect_resource_files(self):
        # External files and knowledge are copied once; links never add instances.
        from .core_skills import canonical_resources
        core_resources = canonical_resources()
        for identity in sorted(self.ids - set(self.residents) - set(self.workspaces) - self.projects):
            if identity in self.projection_scope['repositories']:
                continue
            record = self.index.get(identity, {})
            core = core_resources.get(identity) if record.get('resource_type') == 'skill' else None
            if core:
                self._relocate_core_skill(record, core)
                continue
            try:
                item = resource_file_scope(identity, record)
            except ValueError as error:
                self.unresolved.append({"id": identity, "reason": str(error)})
                continue
            if item:
                self.mappings.append((item['source'], '${share_root}/' + item['target'].removeprefix('data/share_root/')))
                self.files.append(item)
        # Folder truth and source paths in selected registry definitions relocate too.
        for lid in self.lineages:
            path = self.index.get(lid, {}).get("source", {}).get("path")
            if path:
                self.mappings.append((path, "${share_root}/Agents/" + lid))

    def _relocate_core_skill(self, record, core):
        # Core files are supplied once by export_core_skills. Relocate the
        # indexed primary file AND its folder, including historical names.
        destination = '${share_root}/Skills/' + core.name
        old_file = record.get('source', {}).get('path')
        old_root = record.get('attributes', {}).get('content_root')
        if old_file:
            self.mappings.append((old_file, destination + '/SKILL.md'))
        if old_root:
            self.mappings.append((old_root, destination))

    def _collect_templates_and_permissions(self):
        template_ids = {item["id"] for item in self.selection.get("society_templates", {}).get("selected", [])}
        self.rows[("templates", "templates")] = self.source.rows("templates", "templates", predicates={"template_id": template_ids})
        # Keep the installation ledger so unselected builtin definitions cannot return.
        self.rows[("templates", "installed_builtins")] = self.source.rows("templates", "installed_builtins")
        self.ids.update(template_ids)
        schemes = self.selection.get("permission_selection", {}).get("schemes", [])
        from .permissions import selected_permissions
        permissions = selected_permissions(self.source, schemes, self.ids)
        self.rows.update({("permission", key): value for key, value in permissions.items() if ("permission", key) in self.source.tables})
        self.rows.update(self.projection_tables)

    def _collect_application_artifacts(self):
        # Published application packages refer to immutable artifacts outside the
        # repository. Copy those exact bytes; their existing build hashes remain
        # the authority and must not be invalidated by textual transformations.
        for row in self._projection_rows('application_packages'):
            build = json.loads(row['build']) if isinstance(row['build'], str) else row['build']
            if build.get('status') != 'ready':
                self.unresolved.append({'id': row['app_id'], 'reason': 'Selected workbench has no ready application artifact'})
                continue
            from management_portal.workbench_projections.application_build import _artifact_root, read_artifact
            for asset in build.get('files', {}):
                read_artifact(build, asset)
            artifact = _artifact_root() / build['artifact_hash']
            self.files.append({'source': str(artifact), 'target': 'data/staging_root/shared/projection_applications/' + build['artifact_hash'], 'immutable_artifact': True})

    def _keep_schema_metadata(self):
        for key in self.source.tables:
            if key[1] in METADATA_TABLES:
                # Import needs the storage schema version, not a source process's
                # timer lease or its completed runtime-normalization marker. The
                # new instance initializes and acquires its own runtime state.
                self.rows[key] = self.source.rows(*key, predicates={"key": {"schema"}})
        self.rows = {key: values for key, values in self.rows.items() if key in self.source.tables}

    def _generate_document_indexes(self):
        for kind, config in self.selection.get("document_indexes", {}).items():
            if config.get("enabled"):
                entries = [identity for identity in config.get("entries", []) if identity in self.ids]
                self.generated["code/" + kind] = "# " + kind + "\n\n" + "\n".join(f"- [{self.index.get(identity, {}).get('title', identity)}](/r/{identity})" for identity in entries) + "\n"

    def _result(self):
        features = set(self.feature_resolution['included_features'])
        for identity, state in self.feature_resolution['states'].items():
            for parent in state['code_required_by']:
                self.reasons.setdefault(identity, []).append('required by ' + parent)
        self._generate_document_indexes()
        from .file_scope import external_path_mappings
        self.mappings.extend(external_path_mappings(self.files, self.resolution.get('file_scope_policy')))
        external = [{"kind": "backend_role", "role": row["role"], "model": row.get("model", ""), "status": "needs_configuration"} for row in self.roles]
        from .privacy import source_users
        source, resolution = self.source, self.resolution
        return {"source_usernames": sorted(set(source_users(self.rows)) | set(source.source_identity_names() if hasattr(source, "source_identity_names") else [])), "selected_ids": sorted(self.ids), "resource_types": sorted(self.types), "dependency_reasons": self.reasons,
                "unresolved": self.unresolved, "features": sorted(features), "files": self.files,
                "excluded_resources": [{'id': identity, 'reason': 'site_only_skill'} for identity in sorted(self.excluded_skills)],
                "generated_files": self.generated, "path_mappings": self.mappings, "tables": self.rows,
                "feature_resolution": {k: v for k, v in self.feature_resolution.items() if k != 'catalog'},
                "resource_dependencies": self.dependencies,
                "projection_code_repositories": self.projection_scope['repositories'],
                "lineage_options": deepcopy(self.lineages),
                "anonymization": deepcopy(ANONYMIZATION),
                "file_scope_policy": {**deepcopy(resolution.get('file_scope_policy', {})),
                                      'reset_roots': self.projection_scope.get('reset_roots', [])},
                "code_extras": resolution.get("code_extras", []), "external_requirements": external,
                **({'upgrade': deepcopy(resolution['upgrade'])} if 'upgrade' in resolution else {})}
