"""Resource index adapters for Project knowledge items and Skill packages."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from pathlib import Path
from typing import Any, Callable, Iterable

import knowledge_board_lan.knowledge_source as knowledge_source
import skill_board_lan.skill_backend as skill_backend

from management_common.text_values import stripped_text
from management_portal import project_registry
from management_portal.resources.schema import resource_relation

from management_portal.resource_index_adapters import (
    _resolved_root, _resolved_path, _known_path, _tree_paths, _watch_tree, _PathsAdapter,
)
from management_portal.resource_index_records import _dedupe, _timestamp_iso, resource_record, ChangeSet


def _project_statuses(projects: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {
        stripped_text(item.get("id")): stripped_text(item.get("status"))
        for item in projects
        if isinstance(item, dict) and stripped_text(item.get("id"))
    }


class ProjectKnowledgeAdapter(_PathsAdapter):
    name = "project_knowledge"
    resource_types = frozenset(
        {
            "meeting",
            "question",
            "idea",
            "insight",
            "decision",
            "plan",
            "experiment",
            "thinking",
            "risk",
            "concept",
            "information",
            "patent",
            "material",
            "review",
            "todo",
        }
    )
    roots = (
        Path(str(portal_path("share_root") / "Projects")),
    )
    registry_collections = frozenset({project_registry.COLLECTION})
    protect_existing_sources = True

    def project_record(self, resource_type: str, item: dict[str, Any]) -> dict[str, Any]:
        """Read only the newly registered entry when publishing its summary."""
        if resource_type not in self.resource_types:
            raise ValueError(f"unsupported Knowledge resource type: {resource_type}")
        source_path = Path(stripped_text(item.get("path"))).resolve(strict=False)
        project_id = stripped_text(item.get("project") or next(iter(item.get("project_ids") or []), ""))
        if not source_path.is_file() or not project_id:
            raise ValueError("Knowledge registration requires its source path and Project")
        content_root = Path(stripped_text(item.get("content_root")) or str(source_path.parent))
        raw = self._raw_item_for_existing_record({
            "resource_id": item.get("resource_id"),
            "source": {"path": str(source_path)},
            "attributes": {"content_root": str(content_root), "project_ids": [project_id]},
        })
        if not raw:
            raise RuntimeError(f"could not parse registered Knowledge resource: {item.get('resource_id')}")
        statuses = _project_statuses(knowledge_source._parse_projects_yaml())
        result = self._record_for_item(raw, statuses=statuses, order=0)
        if result is None:
            raise RuntimeError("registered Knowledge resource has no index summary")
        return result

    def source_scope(self) -> dict[str, Any]:
        """The sources the change finder lists: each knowledge base's
        directories two levels down and the item files in them."""
        extensions = sorted(knowledge_source._VALID_ITEM_FILE_EXTENSIONS)
        trees: list[dict[str, Any]] = [
            {
                "root": str(_resolved_root(root)),
                "max_depth": 2,
                "files": ["*" + extension for extension in extensions],
            }
            for root in sorted(Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base"))
        ]
        return {"trees": trees, "files": []}

    @staticmethod
    def source_resolution_paths(records: Iterable[dict[str, Any]]) -> list[str]:
        paths: set[str] = set()
        for record in records:
            source = record.get("source") if isinstance(record.get("source"), dict) else {}
            attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            for value in (source.get("path"), attrs.get("content_root"), attrs.get("canonical_root")):
                if stripped_text(value):
                    paths.add(stripped_text(value))
        return sorted(paths)

    @staticmethod
    def _scope_index(
        records: list[dict[str, Any]],
    ) -> Callable[[Path], list[dict[str, Any]]]:
        """Find the records whose path scope holds a path, as
        knowledge_source._indexed_knowledge_record_owns_path does, resolving
        each record's paths once instead of once per changed path."""
        by_source: dict[Path, list[int]] = {}
        by_root: dict[Path, list[int]] = {}
        for index, record in enumerate(records):
            source = record.get("source") if isinstance(record.get("source"), dict) else {}
            attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            source_text = stripped_text(source.get("path"))
            if source_text:
                by_source.setdefault(_known_path(source_text), []).append(index)
            if stripped_text(record.get("resource_type")) == "article":
                root_text = stripped_text(attrs.get("canonical_root"))
                if root_text:
                    by_root.setdefault(_known_path(root_text), []).append(index)
                continue
            root_text = stripped_text(attrs.get("content_root"))
            if attrs.get("single_file") or not root_text:
                continue
            root = _known_path(root_text)
            if root.parent.name == "knowledge_base":
                resource_id = stripped_text(record.get("resource_id"))
                if not resource_id:
                    continue
                root = root / resource_id
            by_root.setdefault(root, []).append(index)

        def matching(path: Path) -> list[dict[str, Any]]:
            found = set(by_source.get(path, ()))
            for candidate in (path, *path.parents):
                found.update(by_root.get(candidate, ()))
            return [records[index] for index in sorted(found)]

        return matching

    def watch_directories(self) -> list[Path]:
        directories: set[Path] = set()
        for root in Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base"):
            directories.update(_watch_tree(root, max_depth=2))
        return sorted(directories, key=str)

    def owns_path(self, path: Path) -> bool:
        resolved = _resolved_path(path)
        if resolved == _resolved_root(self.roots[0]):
            return True
        parts = resolved.parts
        if "knowledge_base" not in parts:
            return False
        index = parts.index("knowledge_base")
        if index + 1 >= len(parts):
            return True
        _allowed, folder_map, _reverse, _hash = knowledge_source._get_tag_config()
        knowledge_folders = set(folder_map.values()) - {"skill"}
        return parts[index + 1] in knowledge_folders

    def protects_missing_source(
        self,
        record: dict[str, Any],
        source_path: Path,
    ) -> bool:
        parts = source_path.parts
        if "knowledge_base" not in parts:
            return False
        index = parts.index("knowledge_base")
        if index + 1 >= len(parts):
            return False
        tag_root = Path(*parts[: index + 2])
        _allowed, _folder_map, folder_to_type, _hash = knowledge_source._get_tag_config()
        item_type = folder_to_type.get(
            parts[index + 1],
            stripped_text(record.get("resource_type")),
        )
        return any(
            source_path == primary_path.resolve(strict=False)
            for _entry_root, primary_path, _entry_files in knowledge_source._entry_specs(
                tag_root,
                item_type,
            )
        )

    def _record_for_item(
        self,
        raw: dict[str, Any],
        *,
        statuses: dict[str, str],
        order: int,
    ) -> dict[str, Any] | None:
        item = knowledge_source.public_item_record(raw)
        resource_id = stripped_text(item.get("kb_id"))
        resource_type = stripped_text(item.get("type"))
        if resource_type == "skill":
            return None
        project = stripped_text(item.get("project"))
        root_path = Path(stripped_text(raw.get("root_path")))
        relative_path = stripped_text(raw.get("relative_path") or raw.get("filename"))
        source_path = root_path / relative_path if relative_path else root_path
        facets = ["knowledge"]
        if resource_type == "experiment":
            facets.append("explore")
        attributes = {
            key: value
            for key, value in item.items()
            if key
            in {
                "type",
                "created_at",
                "version",
                "status",
                "importance",
                "metaness",
                "ai_generated",
                "assignees",
                "task_kind",
                "finished_at",
                "archived_at",
                "filename",
                "mtime",
                "file_count",
                "file_count_truncated",
                "source",
                "source_label",
                "label",
                "kind",
            }
        }
        attributes.update(
            {
                "project_ids": [project] if project else [],
                "relations": [resource_relation("related", target) for target in item.get("related") or []],
                "project_status": statuses.get(project, ""),
                "preview": stripped_text(item.get("preview")),
                "content_root": str(root_path),
                "sort_order": order,
            }
        )
        return resource_record(
            resource_id=resource_id,
            resource_type=resource_type,
            title=item.get("title") or item.get("filename") or resource_id,
            summary=item.get("preview"),
            adapter=self.name,
            path=source_path,
            modified_at=_timestamp_iso(item.get("mtime")),
            facets=facets,
            attributes=attributes,
            search_terms=[
                item.get("filename"),
                project,
                resource_type,
                item.get("source"),
                item.get("source_label"),
                item.get("label"),
            ],
        )


    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = knowledge_source.scan_index_source(active_only=False)
        projects = [dict(item) for item in payload.get("projects") or [] if isinstance(item, dict)]
        statuses = _project_statuses(projects)
        records: list[dict[str, Any]] = []
        for order, raw in enumerate(payload.get("items") or []):
            if not isinstance(raw, dict):
                continue
            record = self._record_for_item(
                raw,
                statuses=statuses,
                order=order,
            )
            if record:
                records.append(record)

        return records, {
            "allowed_types": [
                value
                for value in payload.get("allowed_types") or []
                if value != "skill"
            ],
            "projects": projects,
            "resource_errors": list(payload.get("resource_errors") or []),
        }

    @staticmethod
    def _raw_item_for_existing_record(record: dict[str, Any]) -> dict[str, Any]:
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
        source_path = Path(stripped_text(source.get("path"))).resolve(strict=False)
        content_root = Path(stripped_text(attrs.get("content_root"))).resolve(strict=False)
        if not source_path.is_file():
            return {}
        if content_root.parent.name == "knowledge_base":
            files_root = content_root / stripped_text(record.get("resource_id"))
        else:
            files_root = content_root
        entry_files, truncated = knowledge_source._bounded_entry_files(source_path, files_root)
        allowed, _folder_map, folder_to_type, config_hash = knowledge_source._get_tag_config()
        return ProjectKnowledgeAdapter._indexed_item(
            knowledge_source._get_md_item_cached(
                source_path,
                content_root,
                entry_files,
                stripped_text(next(iter(attrs.get("project_ids") or []), "")),
                config_hash,
                set(allowed),
                folder_to_type,
            ),
            entry_files,
            truncated,
        )

    @staticmethod
    def _indexed_item(item: dict[str, Any], entry_files: list[Path], truncated: bool) -> dict[str, Any]:
        # The same fields knowledge_source.scan_index_source gives a full scan's items.
        if not item:
            return {}
        item.pop("files", None)
        item.pop("path", None)
        item["file_count"] = len(entry_files)
        item["file_count_truncated"] = truncated
        return item

    @staticmethod
    def _raw_item_for_new_path(path: Path, projects: Iterable[dict[str, Any]]) -> dict[str, Any]:
        allowed, _folder_map, folder_to_type, config_hash = knowledge_source._get_tag_config()
        allowed_types = set(allowed)
        for project in projects:
            project_id = stripped_text(project.get("id"))
            project_path = stripped_text(project.get("path"))
            if not project_id or not project_path:
                continue
            knowledge_root = (Path(project_path).expanduser() / "knowledge_base").resolve(strict=False)
            try:
                relative = path.relative_to(knowledge_root)
            except ValueError:
                continue
            if len(relative.parts) < 2:
                return {}
            folder = relative.parts[0]
            item_type = folder_to_type.get(folder, folder)
            if item_type not in allowed_types:
                return {}
            tag_root = knowledge_root / folder
            for entry_root, primary_path, entry_files, truncated in knowledge_source._index_entry_specs(tag_root):
                if entry_root == tag_root:
                    text = knowledge_source._read_file_text(str(primary_path))
                    meta, _body = knowledge_source._parse_front_matter(text)
                    resource_id = knowledge_source.knowledge_metadata_id(meta)
                    companion_root = tag_root / resource_id if resource_id else None
                    in_scope = path == primary_path or bool(
                        companion_root
                        and (path == companion_root or companion_root in path.parents)
                    )
                else:
                    in_scope = path == entry_root or entry_root in path.parents
                if not in_scope:
                    continue
                return ProjectKnowledgeAdapter._indexed_item(
                    knowledge_source._get_md_item_cached(
                        primary_path,
                        entry_root,
                        entry_files,
                        project_id,
                        config_hash,
                        allowed_types,
                        folder_to_type,
                    ),
                    entry_files,
                    truncated,
                )
        return {}

    def scan_changes(
        self,
        paths: Iterable[str],
        previous: Iterable[dict[str, Any]],
        source_state: dict[str, Any],
    ) -> ChangeSet | None:
        paths = tuple(paths)
        if project_registry.CHANGE_PATH in paths:
            return None
        changed_paths = tuple(
            Path(path).expanduser().resolve(strict=False) for path in paths
        )
        projects = [dict(item) for item in knowledge_source._parse_projects_yaml()]
        statuses = _project_statuses(projects)
        previous_records = list(previous)
        records_in_scope = self._scope_index(previous_records)
        affected: dict[str, dict[str, Any]] = {}
        new_paths: list[Path] = []
        for path in changed_paths:
            matching = records_in_scope(path)
            if matching:
                for record in matching:
                    affected[stripped_text(record.get("resource_id"))] = record
            else:
                new_paths.append(path)

        upserts: list[dict[str, Any]] = []
        deletes: list[str] = []
        for resource_id, previous_record in affected.items():
            source = previous_record.get("source") if isinstance(previous_record.get("source"), dict) else {}
            if not Path(stripped_text(source.get("path"))).is_file():
                deletes.append(resource_id)
                continue
            raw = self._raw_item_for_existing_record(previous_record)
            if not raw:
                raise RuntimeError(f"could not parse changed Knowledge resource: {resource_id}")
            order = int((previous_record.get("attributes") or {}).get("sort_order") or 0)
            record = self._record_for_item(
                raw,
                statuses=statuses,
                order=order,
            )
            if record:
                upserts.append(record)
                if record["resource_id"] != resource_id:
                    deletes.append(resource_id)

        next_order = 1 + max(
            [
                int((record.get("attributes") or {}).get("sort_order") or 0)
                for record in previous_records
            ]
            or [0]
        )
        seen_new_ids = {record["resource_id"] for record in upserts}
        for path in new_paths:
            raw = self._raw_item_for_new_path(path, projects)
            if not raw:
                continue
            record = self._record_for_item(
                raw,
                statuses=statuses,
                order=next_order,
            )
            if record and record["resource_id"] not in seen_new_ids:
                upserts.append(record)
                seen_new_ids.add(record["resource_id"])
                next_order += 1
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts),
            deletes=tuple(_dedupe(deletes)),
            source_state={
                **source_state,
                "last_incremental_path_count": len(changed_paths),
            },
        )


class SkillPackageAdapter(_PathsAdapter):
    name = "skill"
    resource_types = frozenset({"skill"})
    roots = (
        Path(str(portal_path("share_root") / "Skills")),
        Path(str(portal_path("share_root") / "Projects")),
    )
    registry_collections = frozenset({project_registry.COLLECTION})
    recursive_fingerprint = True
    protect_existing_sources = True

    def source_paths(self) -> list[Path]:
        project_roots = sorted(Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base/skill"))
        roots = [
            Path(str(portal_path("share_root") / "Skills")),
            *project_roots,
        ]
        return _tree_paths(roots)

    def fingerprint(self) -> str:
        return self._fingerprint_paths(self.source_paths())

    def watch_directories(self) -> list[Path]:
        directories = set(_watch_tree(Path(str(portal_path("share_root") / "Skills")), max_depth=1))
        for root in Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base/skill"):
            directories.update(_watch_tree(root, max_depth=1))
        return sorted(directories, key=str)

    def owns_path(self, path: Path) -> bool:
        resolved = _resolved_path(path)
        shared_root = _resolved_root(str(portal_path("share_root") / "Skills"))
        if resolved == shared_root or shared_root in resolved.parents:
            return True
        parts = resolved.parts
        return "knowledge_base" in parts and "skill" in parts[parts.index("knowledge_base") + 1 :]

    def protects_missing_source(
        self,
        _record: dict[str, Any],
        source_path: Path,
    ) -> bool:
        return self.owns_path(source_path)

    def _record_for_item(
        self,
        raw: dict[str, Any],
        *,
        statuses: dict[str, str],
        order: int,
    ) -> dict[str, Any]:
        skill_id = stripped_text(raw.get("skill_id"))
        raw_root_path = Path(stripped_text(raw.get("root_path")))
        relative_path = stripped_text(raw.get("relative_path") or raw.get("filename") or "SKILL.md")
        source_path = raw_root_path if raw_root_path.is_file() else raw_root_path / relative_path
        root_path = raw_root_path.parent if raw_root_path.is_file() else raw_root_path
        files: list[dict[str, Any]] = []
        for file in raw.get("files") or []:
            if not isinstance(file, dict):
                continue
            relative = stripped_text(file.get("relative_path") or file.get("name"))
            primary = bool(file.get("primary")) or relative == relative_path
            files.append(
                {
                    "address": skill_id if primary else knowledge_source._nested_file_address(skill_id, relative),
                    "name": stripped_text(file.get("name") or relative),
                    "relative_path": relative,
                    "kind": stripped_text(file.get("kind") or "binary"),
                    "primary": primary,
                }
            )
        source_kind = stripped_text(raw.get("source"))
        project = "" if source_kind in {"shared", "third_party"} else stripped_text(raw.get("project"))
        project_status = statuses.get(project, "")
        attributes = {
            "type": "skill",
            "project_ids": [project] if project else [],
            "read_scope": (
                "global"
                if source_kind in {"shared", "third_party"}
                else "project"
            ),
            "project_status": project_status,
            "status": stripped_text(raw.get("status")),
            "created_at": stripped_text(raw.get("created_at")),
            "importance": int(raw.get("importance") or 0),
            "mtime": int(raw.get("mtime") or 0),
            "filename": stripped_text(raw.get("filename") or "SKILL.md"),
            "relative_path": relative_path,
            "content_root": str(root_path),
            "single_file": raw_root_path.is_file(),
            "file_count": int(raw.get("file_count") or len(files)),
            "name": stripped_text(raw.get("name")),
            "source": source_kind,
            "source_label": stripped_text(raw.get("source_label")),
            "label": stripped_text(raw.get("label")),
            "preview": stripped_text(raw.get("preview") or raw.get("description")),
            "ai_generated": False,
            "sort_order": order,
        }
        search_terms: list[object] = [
            raw.get("name"),
            raw.get("description"),
            raw.get("filename"),
            relative_path,
            project,
            raw.get("source"),
            raw.get("source_label"),
            raw.get("label"),
        ]
        for file in files:
            search_terms.extend([file.get("name"), file.get("relative_path")])
        return resource_record(
            resource_id=skill_id,
            resource_type="skill",
            title=raw.get("title") or raw.get("name") or skill_id,
            summary=raw.get("description") or raw.get("preview"),
            adapter=self.name,
            path=source_path,
            modified_at=_timestamp_iso(raw.get("mtime")),
            facets=["skill", "knowledge"],
            attributes=attributes,
            search_terms=search_terms,
        )

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = skill_backend.scan_index_source(active_only=False)
        statuses = _project_statuses(payload.get("projects") or [])
        records = [
            self._record_for_item(raw, statuses=statuses, order=order)
            for order, raw in enumerate(payload.get("items") or [])
            if isinstance(raw, dict)
        ]
        return records, {
            "projects": list(payload.get("projects") or []),
            "sources": list(payload.get("sources") or []),
            "resource_errors": list(payload.get("resource_errors") or []),
        }

    @staticmethod
    def _record_scope_contains(record: dict[str, Any], path: Path) -> bool:
        return knowledge_source._indexed_knowledge_record_owns_path(record, path)

    @staticmethod
    def _raw_item(
        root: Path,
        *,
        project: str,
        source: str,
        source_label: str,
        preferred_primary: Path | None = None,
    ) -> dict[str, Any]:
        markdown_files = skill_backend._candidate_markdown_files(root)
        primary = (
            preferred_primary
            if preferred_primary and preferred_primary in markdown_files
            else skill_backend._choose_primary_file(root, markdown_files)
        )
        if not primary:
            return {}
        files = skill_backend._candidate_skill_files(root) or markdown_files
        return skill_backend._get_item_cached(
            root=root,
            primary_path=primary,
            files=files,
            project=project,
            source=source,
            source_label=source_label,
        )

    @staticmethod
    def _new_item_context(
        path: Path,
        projects: Iterable[dict[str, Any]],
    ) -> tuple[Path, str, str, str] | None:
        shared_root = skill_backend.SHARED_SKILLS_ROOT.resolve(strict=False)
        third_party_root = skill_backend.THIRD_PARTY_SKILLS_ROOT.resolve(strict=False)
        try:
            relative = path.relative_to(third_party_root)
        except ValueError:
            relative = None
        if relative and relative.parts and not relative.parts[0].startswith("."):
            root = third_party_root / relative.parts[0]
            return root, "", "third_party", "Third-party"
        try:
            relative = path.relative_to(shared_root)
        except ValueError:
            relative = None
        if (
            relative
            and relative.parts
            and not relative.parts[0].startswith(".")
            and relative.parts[0] != third_party_root.name
        ):
            root = shared_root / relative.parts[0]
            return root, "", "shared", "Shared"
        for project in projects:
            project_id = stripped_text(project.get("id"))
            project_path = stripped_text(project.get("path"))
            if not project_id or not project_path:
                continue
            skill_root = (Path(project_path).expanduser() / "knowledge_base" / "skill").resolve(strict=False)
            try:
                relative = path.relative_to(skill_root)
            except ValueError:
                continue
            if not relative.parts or relative.parts[0].startswith("."):
                return None
            return skill_root / relative.parts[0], project_id, "project", "Project"
        return None

    def scan_changes(
        self,
        paths: Iterable[str],
        previous: Iterable[dict[str, Any]],
        _source_state: dict[str, Any],
    ) -> ChangeSet | None:
        paths = tuple(paths)
        if project_registry.CHANGE_PATH in paths:
            return None
        changed_paths = tuple(
            Path(path).expanduser().resolve(strict=False) for path in paths
        )
        projects = [dict(item) for item in skill_backend._parse_projects(active_only=False)]
        statuses = _project_statuses(projects)
        previous_records = list(previous)
        affected: dict[str, dict[str, Any]] = {}
        new_paths: list[Path] = []
        for path in changed_paths:
            matches = [
                record
                for record in previous_records
                if self._record_scope_contains(record, path)
            ]
            if matches:
                for record in matches:
                    affected[stripped_text(record.get("resource_id"))] = record
            else:
                new_paths.append(path)

        upserts: list[dict[str, Any]] = []
        deletes: list[str] = []
        for resource_id, previous_record in affected.items():
            source = previous_record.get("source") if isinstance(previous_record.get("source"), dict) else {}
            attrs = previous_record.get("attributes") if isinstance(previous_record.get("attributes"), dict) else {}
            source_path = Path(stripped_text(source.get("path"))).resolve(strict=False)
            root = source_path if attrs.get("single_file") else Path(stripped_text(attrs.get("content_root"))).resolve(strict=False)
            raw = self._raw_item(
                root,
                project=stripped_text(next(iter(attrs.get("project_ids") or []), "")),
                source=stripped_text(attrs.get("source")),
                source_label=stripped_text(attrs.get("source_label")),
                preferred_primary=source_path,
            )
            if not raw:
                if source_path.is_file():
                    raise RuntimeError(f"could not parse changed Skill resource: {resource_id}")
                deletes.append(resource_id)
                continue
            order = int(attrs.get("sort_order") or 0)
            record = self._record_for_item(raw, statuses=statuses, order=order)
            upserts.append(record)
            if record["resource_id"] != resource_id:
                deletes.append(resource_id)

        next_order = 1 + max(
            [
                int((record.get("attributes") or {}).get("sort_order") or 0)
                for record in previous_records
            ]
            or [0]
        )
        seen_new_ids = {record["resource_id"] for record in upserts}
        for path in new_paths:
            context = self._new_item_context(path, projects)
            if not context:
                continue
            root, project, source, source_label = context
            raw = self._raw_item(
                root,
                project=project,
                source=source,
                source_label=source_label,
            )
            if not raw:
                continue
            record = self._record_for_item(raw, statuses=statuses, order=next_order)
            if record["resource_id"] in seen_new_ids:
                continue
            upserts.append(record)
            seen_new_ids.add(record["resource_id"])
            next_order += 1
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts),
            deletes=tuple(_dedupe(deletes)),
            source_state={"last_incremental_path_count": len(changed_paths)},
        )
