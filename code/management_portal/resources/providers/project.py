from __future__ import annotations
from management_common.portal_paths import path as portal_path

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any


from management_common.principal import actor_principal
from management_common.instance_locations import current_instance
from management_portal.portal_access_control import get_portal_access_store
from management_portal import project_registry

from .common import canonical_record

if TYPE_CHECKING:
    from ..service import ResourceService


PROJECTS_ROOT = portal_path('projects')
PROJECT_TYPE_ALIASES = {
    "company": "company",
    "company_project": "company",
    "公司": "company",
    "公司项目": "company",
    "academic": "academic",
    "academic_project": "academic",
    "research": "academic",
    "paper": "academic",
    "学术": "academic",
    "学术项目": "academic",
    "personal": "personal",
    "personal_project": "personal",
    "个人": "personal",
    "个人项目": "personal",
    "other": "other",
    "其它": "other",
    "其他": "other",
    "其他项目": "other",
    "non_academic": "company",
}
UPDATABLE_FIELDS = frozenset({"status", "ddl", "project_type", "description", "tags"})


def _load_registry() -> dict[str, Any]:
    return project_registry.load()


def load_projects_payload() -> dict[str, Any]:
    return _load_registry()


def _registry_value_for_path(path: Path) -> str:
    return current_instance().reference(path.resolve(strict=False))


def _project_root_for_id(project_id: str) -> Path:
    return (PROJECTS_ROOT / project_id).resolve(strict=False)


def _path_payload_matches_project_root(
    raw_path: object,
    project_root: Path,
) -> bool:
    clean_path = str(raw_path or "").strip()
    if not clean_path:
        return True
    return project_registry.runtime_path(clean_path) == project_root


def _clean_project_id(value: object) -> str:
    project_id = str(value or "").strip()
    if not project_registry.PROJECT_ID.fullmatch(project_id):
        raise ValueError(
            "project id must use 1-80 letters (Chinese included), digits, _ . @ + -"
        )
    return project_id


def _clean_project_tags(value: object) -> list[str]:
    raw_items = (
        value
        if isinstance(value, list)
        else re.split(r"[,，\s]+", str(value or ""))
    )
    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        tag = re.sub(r"\s+", "", str(raw or "").strip())
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def register_project(
    payload: dict[str, Any],
    *,
    actor: str = "",
    automatic: bool = False,
    access_store: Any | None = None,
) -> dict[str, Any]:
    del automatic
    clean_actor = str(actor or "").strip()
    if clean_actor and actor_principal(clean_actor).kind == "user":
        access = access_store or get_portal_access_store()
        creator = access.user(clean_actor)
        if not creator.get("configured") or creator.get("status") != "active":
            raise PermissionError("Project creation requires an active Portal user")
    return create_project(payload, actor=clean_actor)


def _tag_folders_from_doc(doc: dict[str, Any]) -> list[str]:
    tag_vocab = doc.get("tag_vocab")
    folders: list[str] = []
    seen: set[str] = set()
    for item in tag_vocab if isinstance(tag_vocab, list) else []:
        if not isinstance(item, dict):
            continue
        folder = str(item.get("folder") or "").strip().strip("/")
        if not folder or folder in seen:
            continue
        folders.append(folder)
        seen.add(folder)
    return folders


def normalize_project_type(value: object) -> str:
    raw = str(value or "company").strip()
    normalized = PROJECT_TYPE_ALIASES.get(raw.lower()) or PROJECT_TYPE_ALIASES.get(
        raw
    )
    if not normalized:
        raise ValueError(
            "project type must be one of: company, academic, personal, other"
        )
    return normalized


def _write_project_seed_files(
    project_root: Path,
    project_id: str,
    description: str,
    project_type: str,
    tag_folders: list[str],
    language: str = "zh-CN",
) -> None:
    from management_portal.project_seeds import project_seed_documents

    academic = normalize_project_type(project_type) == "academic"
    directories = [
        project_root / "agent_port" / "skills",
        project_root / "agent_port" / "tools",
        project_root / "agents",
        project_root / "knowledge_base",
        project_root / "src",
        project_root / "to_archive",
    ]
    if academic:
        directories.extend(
            [project_root / "article", project_root / "references"]
        )
    directories.extend(
        project_root / "knowledge_base" / folder for folder in tag_folders
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    for relative, content in project_seed_documents(project_id, description, language).items():
        (project_root / relative).write_text(content, encoding="utf-8")


def _project_record(project_id: str) -> dict[str, Any]:
    target = str(project_id or "").strip()
    if not target:
        raise ValueError("Missing project id.")
    for item in load_projects_payload().get("projects") or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == target:
            return item
    raise KeyError("Project not found.")


def _resolved_project_root(project: dict[str, Any]) -> Path:
    resolved_root = project_registry.runtime_path(project.get("path"))
    if resolved_root is None:
        raise ValueError(f"project path missing: {project.get('id')}")
    projects_root = PROJECTS_ROOT.resolve(strict=False)
    if not resolved_root.is_relative_to(projects_root):
        raise PermissionError("Project path is outside the projects root.")
    return resolved_root


def create_project(
    payload: dict[str, Any],
    *,
    actor: str = "",
    operation_id: str = "",
) -> dict[str, Any]:
    from management_portal import permission_system

    project_id = _clean_project_id(payload.get("id"))
    language = payload.get("language", "zh-CN")
    if language not in {"en", "zh-CN"}:
        raise ValueError("Project template language must be en or zh-CN")
    description = str(payload.get("description") or "").strip()
    project_type = normalize_project_type(payload.get("project_type"))
    ddl = str(payload.get("ddl") or "").strip()
    tags = _clean_project_tags(payload.get("tags"))
    status = str(payload.get("status") or "active").strip() or "active"
    project_root = _project_root_for_id(project_id)
    if not _path_payload_matches_project_root(payload.get("path"), project_root):
        raise ValueError(f"Project path is fixed: {PROJECTS_ROOT / project_id}")
    if not project_root.is_relative_to(PROJECTS_ROOT.resolve(strict=False)):
        raise PermissionError(f"Project path must be under {PROJECTS_ROOT}.")

    from ..creation import Creation
    from ..directory_registrations import directory_writer
    creation = Creation(project_id, actor=actor, payload=payload, operation_id=operation_id)

    def place(document: dict[str, Any], connection: Any) -> None:
        # The Project and its place in the permission trees commit together (decision_b820faf42218 §5.7).
        saved = next(item for item in document["projects"] if item.get("id") == project_id)
        with creation.registration(connection):
            permission_system.sync_resource({**saved, "resource_id": project_id}, resource_type="project", connection=connection)

    with creation.prepare([(PROJECTS_ROOT / project_id, 'directory')]):
        if creation.completed:
            record = _project_record(project_id)
            return canonical_record(project_id, {'project': record, 'project_root': str(current_instance().resolve(record['path']))})
        document = _load_registry()
        if any(item.get('id') == project_id for item in document['projects']):
            raise FileExistsError('Project identity is already registered: ' + project_id)
        status_vocab = document['status_vocab']
        if status_vocab and status not in {str(item) for item in status_vocab}:
            raise ValueError(f"Unknown project status: {status}")
        creation.publish(lambda index, stage: _write_project_seed_files(
            stage, project_id, description, project_type, _tag_folders_from_doc(document), language))
        project_root, = creation.paths
        with directory_writer(project_registry.COLLECTION), project_registry.mutate(before_commit=place) as doc:
            projects = doc["projects"]
            if any(
                isinstance(item, dict)
                and str(item.get("id") or "").strip() == project_id
                for item in projects
            ):
                raise FileExistsError(f"Project id already exists: {project_id}")
            registry_value = _registry_value_for_path(project_root)
            if any(
                isinstance(item, dict)
                and str(item.get("path") or "").strip() == registry_value
                for item in projects
            ):
                raise FileExistsError(
                    f"Project path already registered: {registry_value}"
                )
            status_vocab = doc["status_vocab"]
            if status_vocab and status not in {str(item) for item in status_vocab}:
                raise ValueError(f"Unknown project status: {status}")
            record = {
                "id": project_id,
                "directory": {"owner_id": project_id},
                "status": status,
                "project_type": project_type,
                "description": description,
                "path": registry_value,
                "ddl": ddl,
                "tags": tags,
                **permission_system.creation_fields(actor),
            }
            projects.append(record)

    public_record = dict(record)
    return canonical_record(
        project_id,
        {
            "project": public_record,
            "project_root": str(project_root),
        },
    )


def update_project(
    project_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    field = str(payload.get("field") or "").strip()
    if field not in UPDATABLE_FIELDS:
        raise ValueError(f"Unsupported Project field: {field}")
    value: str | list[str] = str(payload.get("value") or "")
    if field == "project_type":
        value = normalize_project_type(value)
    elif field == "description":
        value = value.strip()
    elif field == "tags":
        value = _clean_project_tags(payload.get("value"))

    with project_registry.mutate() as data:
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise KeyError("Project not found.")
        if field == "status":
            status_vocab = (
                data.get("status_vocab")
                if isinstance(data.get("status_vocab"), list)
                else []
            )
            if status_vocab and value not in {
                str(item) for item in status_vocab
            }:
                raise ValueError(f"Unknown project status: {value}")
        project = next(
            (
                item
                for item in projects
                if isinstance(item, dict)
                and str(item.get("id") or "").strip() == project_id
            ),
            None,
        )
        if project is None:
            raise KeyError("Project not found.")
        project[field] = value
        public_record = dict(project)
    return canonical_record(project_id, public_record)


def project_summary(
    project_id: str,
) -> dict[str, Any]:
    project = _project_record(project_id)
    display_id = str(project.get("id") or "").strip()
    projects_root = PROJECTS_ROOT.resolve(strict=False)
    resolved_root = _resolved_project_root(project)

    summary_path = (resolved_root / "summary.md").resolve(strict=False)
    if not summary_path.is_relative_to(projects_root):
        raise PermissionError(
            "Project summary path is outside the projects root."
        )
    exists = summary_path.is_file()
    text = (
        summary_path.read_text(encoding="utf-8", errors="replace")
        if exists
        else ""
    )
    return canonical_record(
        display_id,
        {
            "path": str(resolved_root),
            "summary_path": str(summary_path),
            "project_type": str(project.get("project_type") or ""),
            "exists": exists,
            "text": text,
        },
    )


def save_project_summary(
    project_id: str,
    text: str,
) -> dict[str, Any]:
    payload = project_summary(project_id)
    summary_path = Path(str(payload.get("summary_path") or "")).resolve(
        strict=False
    )
    projects_root = PROJECTS_ROOT.resolve(strict=False)
    if not summary_path.is_relative_to(projects_root):
        raise PermissionError(
            "Project summary path is outside the projects root."
        )
    if not summary_path.parent.is_dir():
        raise FileNotFoundError("Project directory not found.")
    summary_path.write_text(str(text or ""), encoding="utf-8")
    return project_summary(project_id)


def install_mutation_provider(service: "ResourceService") -> None:
    def register(
        payload: dict[str, Any],
        *,
        actor: str = "",
        automatic: bool = False,
    ) -> dict[str, Any]:
        return register_project(
            payload,
            actor=actor,
            automatic=automatic,
        )

    def update(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        return update_project(
            resource_id,
            payload,
        )

    def unregister(resource_id: str, *, actor: str = "") -> dict[str, Any]:
        # DeleteProject removes the registry entry, Lineages and folder first;
        # this only deletes the record and the authorization resource after it.
        del actor
        try:
            _project_record(resource_id)
        except KeyError:
            return {"resource_id": str(resource_id), "status": "deleted"}
        raise ValueError("Project 只能用 DeleteProject 删除")

    service.register_mutation_provider(
        "project",
        register=register,
        update=update,
        unregister=unregister,
    )






def install_action_provider(service: "ResourceService") -> None:

    def move_knowledge(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        from management_portal import permission_system
        from management_portal.resources.knowledge_move import move_knowledge as move

        if resource_id not in permission_system.permitted_ids(
            actor, "RunResourceAction", "resource_id", [resource_id],
        ):
            raise PermissionError("Knowledge relocation requires target Project contributor permission")
        return move(resource_id, payload, actor=actor, project_batch=service._index().project_relocated)

    def read_summary(
        resource_id: str,
        _payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        return project_summary(resource_id)

    def save_summary(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        return save_project_summary(
            resource_id,
            str(payload.get("text") or ""),
        )

    service.register_action_provider(
        "project",
        {
            "summary.read": read_summary,
            "summary.save": save_summary,
            "knowledge.move": move_knowledge,
        },
    )
