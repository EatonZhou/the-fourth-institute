from __future__ import annotations

from . import tables, generation_storage

from management_common.principal import is_user_or_resident

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import cache

from management_common.portal_database import portal_database
from management_portal.resources import ResourceOperationError
from management_portal.portal_access_control import is_active_user_or_resident


IDENTITY = re.compile(r"^workbench_[0-9a-f]{12}$")
ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CORE_BUILTINS = {
    "development": {"title": "Portal 开发投影", "path": "/development", "description": "Portal 的架构、阶段进展与未来方向工作台"},
    "studio": {"title": "投影工坊", "path": "/workbenches?panel=studio", "description": "Workbenches 中由普通 Lineage 提供的 AI 页面生成模块"},
}


@cache
def builtins() -> dict[str, dict]:
    """The built-in workbenches by name: the core ones, then the installed features' (workbench.builtin)."""
    from management_portal import portal_features

    found = dict(CORE_BUILTINS)
    for _feature, builtin in portal_features.contributions("workbench.builtin"):
        found[builtin["name"]] = {key: builtin[key] for key in ("title", "path", "description")}
    return found
# Directory stars live with the other personal favorites (presentation_store).
FAVORITE_SURFACE = "workbench"
DEFINITION_FIELDS = frozenset({
    "title", "description", "html", "bindings", "navigation", "manifest", "panels", "application",
})


class ProjectionError(ResourceOperationError):
    pass


def _changeable(actor: str, app_ids) -> set[str]:
    """The workbenches UpdateResource would let the actor change."""
    from management_portal import permission_system

    return permission_system.permitted_ids(actor, "UpdateResource", "resource_id", list(app_ids))


def readable_projects(actor: str, project_ids) -> set[str]:
    """The Projects GetProject would let the actor read."""
    from management_portal import permission_system

    return permission_system.permitted_ids(actor, "GetProject", "project_id", sorted({str(item) for item in project_ids if item}))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def definition_changes(value: dict) -> dict:
    return {key: item for key, item in value.items() if key in DEFINITION_FIELDS}


def validate_manifest(value: dict) -> dict:
    result = copy.deepcopy(value)
    if not isinstance(result, dict):
        raise ProjectionError("projection.invalid", "工作台定义必须是对象")
    if not str(result.get("title") or "").strip() or len(str(result["title"])) > 120:
        raise ProjectionError("projection.title", "请输入 1–120 字的工作台名称")
    if result.get("navigation", "menu") not in {"menu", "top", "hidden"}:
        raise ProjectionError("projection.navigation", "导航位置无效")
    if result.get("builtin") and result["builtin"] not in builtins():
        raise ProjectionError("projection.builtin", "未知的内置工作台类型")
    source = result.get("html", "")
    if not isinstance(source, str) or len(source.encode()) > 400_000:
        raise ProjectionError("projection.source", "页面源码须小于 400 KB")
    if source:
        from .content import ContentTemplateError, validate_content
        try:
            validate_content(source)
        except ContentTemplateError as error:
            raise ProjectionError("projection.template", str(error)) from error
    bindings = result.get("bindings", {})
    if not isinstance(bindings, dict) or len(bindings) > 256:
        raise ProjectionError("projection.bindings", "最多关联 256 个 Lineage")
    for alias, binding in bindings.items():
        if not ALIAS.fullmatch(alias) or not isinstance(binding, dict):
            raise ProjectionError("projection.binding", "Lineage 别名格式无效")
        if not re.fullmatch(r"lineage_[0-9a-f]{12}", str(binding.get("lineage_id", ""))):
            raise ProjectionError("projection.binding", "请使用正式 Lineage ID")
        roles = binding.get("residents", {})
        if not isinstance(roles, dict) or len(roles) > 200:
            raise ProjectionError("projection.roles", "Resident 绑定格式无效")
        for role, resident in roles.items():
            if not isinstance(role, str) or not role or len(role) > 240 or not re.fullmatch(r"resident_[0-9a-f]{12}", str(resident)):
                raise ProjectionError("projection.resident", "Resident 绑定格式无效")
        events = binding.get("events", {})
        if not isinstance(events, dict) or len(events) > 200:
            raise ProjectionError("projection.events", "流程入口绑定格式无效")
        for name, event_id in events.items():
            if not ALIAS.fullmatch(name) or not isinstance(event_id, str) or not event_id.startswith("event_source_"):
                raise ProjectionError("projection.events", "请使用有效的流程别名和 Event Source ID")
    result["bindings"] = bindings
    if result.get("manifest") is not None:
        from .content import ContentTemplateError
        from .page import validate_page, validate_application
        try:
            if source:
                raise ContentTemplateError("v2 页面只使用清单和面板源码，html 必须为空")
            if result.get("application") is not None:
                validate_application(result["application"])
            validate_page(result["manifest"], result.get("panels", {}), bindings, result.get("application"))
            result.setdefault("panels", {})
        except ContentTemplateError as error:
            raise ProjectionError("projection.manifest", str(error)) from error
    elif result.get("panels"):
        raise ProjectionError("projection.manifest", "面板源码需要 v2 页面清单")
    elif result.get("application") is not None:
        raise ProjectionError("projection.manifest", "应用包需要 v2 页面清单")
    result["navigation"] = result.get("navigation", "menu")
    return result


class Store:
    def __init__(self):
        self.database = portal_database(schema="workbenches")
        with self.connect() as connection:
            connection.executescript(tables.DDL)
            connection.executescript(generation_storage.DDL)
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS preferences (
                    actor TEXT NOT NULL, app_id TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(actor, app_id)
                );
                CREATE TABLE IF NOT EXISTS native_entrypoints (
                    name TEXT PRIMARY KEY, app_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS role_requests (
                    workbench_id TEXT NOT NULL, lineage_id TEXT NOT NULL,
                    slot_id TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(workbench_id, lineage_id, slot_id)
                );
            """)

    @contextmanager
    def connect(self):
        with self.database.connect() as connection:
            yield connection

    def lock(self, scope: str):
        digest = hashlib.sha256(("workbench:" + scope).encode()).digest()
        return self.database.advisory_lock(
            int.from_bytes(digest[:8], "big", signed=True)
        )

    def role_request(
        self, workbench_id: str, lineage_id: str, slot_id: str
    ) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT actor,data FROM role_requests "
                "WHERE workbench_id=%s AND lineage_id=%s AND slot_id=%s",
                (workbench_id, lineage_id, slot_id),
            ).fetchone()
        return {"actor": row["actor"], **json.loads(row["data"])} if row else {}

    def forget_role_request(self, workbench_id: str, lineage_id: str, slot_id: str) -> None:
        """Forget a role creation whose Resident was deleted, so the role can be created again."""
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM role_requests WHERE workbench_id=%s AND lineage_id=%s AND slot_id=%s",
                (workbench_id, lineage_id, slot_id),
            )

    def save_role_request(
        self,
        workbench_id: str,
        lineage_id: str,
        slot_id: str,
        actor: str,
        state: dict,
    ) -> None:
        encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            result = connection.execute(
                "INSERT INTO role_requests(workbench_id,lineage_id,slot_id,actor,data) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT(workbench_id,lineage_id,slot_id) DO UPDATE "
                "SET data=excluded.data WHERE role_requests.actor=excluded.actor",
                (workbench_id, lineage_id, slot_id, actor, encoded),
            )
            if result.rowcount != 1:
                raise ProjectionError(
                    "projection.creation_actor_mismatch",
                    "角色创建请求主体不匹配；请核对原请求回执",
                    409,
                )

    @staticmethod
    def _require_live_manifest(connection, resource_id, data):
        from management_portal.resources.retired_identity_boundary import require_live_identity
        parents = [rid for binding in data.get('bindings', {}).values()
                   for rid in [binding['lineage_id'], *binding.get('residents', {}).values()]]
        require_live_identity(connection, resource_id, parents=parents)

    def create(self, resource_id: str, payload: dict, actor: str) -> dict:
        if not actor or not IDENTITY.fullmatch(resource_id):
            raise ProjectionError("projection.identity", "工作台身份无效")
        if "application_build" in payload:
            raise ProjectionError("projection.application_build", "构建回执只能由宿主生成")
        data = validate_manifest(payload)
        self.prepare_application(data, actor)
        builtin = str(data.get("builtin") or resource_id)
        data.update(workbench_id=resource_id, created_by=actor, created_at=now(), updated_at=now())
        with self.connect() as connection:
            self._require_live_manifest(connection, resource_id, data)
            connection.execute('INSERT INTO apps VALUES (%s,%s,%s,1,NULL,%s,%s,%s)',
                               (resource_id,actor,builtin,data['created_at'],data['updated_at'],data['navigation']))
            tables.save_version(connection, resource_id, 1, data)
        return self.get(resource_id, actor)

    def get(self, app_id: str, actor: str, *, published: bool = False) -> dict:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM apps WHERE id=%s', (app_id,)).fetchone()
            if row is None:
                raise ProjectionError("projection.not_found", "工作台不存在或已删除", 404)
            data = tables.manifest(connection, row)
            if not self.authorized(data, actor, "read"):
                raise PermissionError("无权访问此工作台")
            if published:
                if row["published"] is None:
                    raise ProjectionError("projection.unpublished", "工作台尚未发布，请先预览并发布", 409)
                data = tables.manifest(connection, row, row["published"])
            data.update(revision=row["revision"], published_revision=row["published"])
            return data

    def list(self, actor: str) -> list[dict]:
        """The directory: the built-in entries, workbenches of Projects the actor
        may read and those the actor may change. Drafts are listed only to
        those who may change them; nobody else can use an unpublished page."""
        if not is_user_or_resident(actor):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT apps.id, "
                "EXISTS (SELECT 1 FROM native_entrypoints WHERE native_entrypoints.app_id=apps.id), "
                "ARRAY(SELECT project_id FROM projects WHERE projects.app_id=apps.id AND projects.revision=apps.revision) "
                "FROM apps ORDER BY created_at DESC, id DESC",
            ).fetchall()
        changeable = _changeable(actor, [row[0] for row in rows])
        readable = readable_projects(actor, (project for row in rows for project in row[2]))
        result = []
        for app_id, native, projects in rows:
            if not (native or app_id in changeable or readable.intersection(projects)):
                continue
            try:
                app = self.get(app_id, actor)
            except PermissionError:
                continue
            if app["published_revision"] or app_id in changeable:
                result.append(app)
        return result

    def native_ids(self) -> set[str]:
        """The workbenches that currently serve a built-in entry."""
        with self.connect() as connection:
            return {row[0] for row in connection.execute('SELECT app_id FROM native_entrypoints')}

    def get_revision(self, app_id: str, actor: str, revision: int) -> dict:
        """Read an immutable revision under the current workbench permission."""
        self.get(app_id, actor)
        if type(revision) is not int or revision < 1:
            raise ProjectionError("projection.revision", "工作台修订无效", 404)
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM apps WHERE id=%s', (app_id,)).fetchone()
            if row is None or revision > row["revision"]:
                raise ProjectionError("projection.revision", "工作台修订不存在", 404)
            data = tables.manifest(connection, row, revision)
        return {**data, "revision": revision, "published_revision": row["published"]}

    @staticmethod
    def prepare_application(data: dict, actor: str, current: dict | None = None) -> None:
        application = data.get("application")
        if application is None:
            data.pop("application", None)
            data.pop("application_build", None)
            return
        if current and application == current.get("application") and current.get("application_build", {}).get("status") == "ready":
            data["application_build"] = copy.deepcopy(current["application_build"])
            return
        from .application_build import build_application
        data["application_build"] = build_application(application, actor)
        if data["application_build"].get("status") != "ready":
            raise ProjectionError("projection.application_build", "应用包构建未成功；修订未保存")

    def builtin(self, name: str, actor: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT app_id FROM native_entrypoints WHERE name=%s', (name,)).fetchone()
        return self.get(row[0], actor, published=True) if row else None

    def activate_builtin(self, app_id: str, actor: str, *, previous_app_id: str, name: str = "") -> dict:
        app = self.get(app_id, actor)
        self.require_write(app, actor)
        name = name or app.get("builtin")
        if name not in builtins() or app["published_revision"] != app["revision"]:
            raise ProjectionError("projection.activation", "只能启用已发布且与当前版本一致的业务工作台", 409)
        if app.get("builtin") and app["builtin"] != name:
            raise ProjectionError("projection.activation", "已登记的内置工作台不能改为其他入口", 409)
        if previous_app_id:
            self.require_write(self.get(previous_app_id, actor), actor)
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))")
            current = connection.execute('SELECT revision, published, builtin FROM apps WHERE id=%s FOR UPDATE', (app_id,)).fetchone()
            if current is None:
                raise ProjectionError("projection.not_found", "工作台不存在或已删除", 404)
            if current["revision"] != app["revision"] or current["published"] != app["revision"] or current["builtin"] != (app.get("builtin") or app_id):
                raise ProjectionError("projection.activation_conflict", "工作台版本已变更，请重新发布并核对入口", 409)
            row = connection.execute('SELECT app_id FROM native_entrypoints WHERE name=%s', (name,)).fetchone()
            actual = row[0] if row else ""
            if actual == app_id:
                return app
            if actual != previous_app_id:
                raise ProjectionError("projection.activation_conflict", "业务入口已变更，请重新核对原入口", 409)
            connection.execute('UPDATE apps SET builtin=%s WHERE id=%s', (name, app_id))
            connection.execute('INSERT INTO native_entrypoints(name,app_id) VALUES (%s,%s) ON CONFLICT(name) DO UPDATE SET app_id=excluded.app_id', (name, app_id))
        return self.get(app_id, actor)

    @staticmethod
    def updated_manifest(current: dict, payload: dict) -> dict:
        data = validate_manifest({**current, **definition_changes(payload)})
        data.pop("revision", None)
        data.pop("published_revision", None)
        data["updated_at"] = now()
        return data

    def update(self, app_id: str, payload: dict, actor: str, expected_revision: int, *, authority: str = "") -> dict:
        """``authority`` is whose right to change the page this write uses when
        the writer acts for someone else (a designer saving a requested page)."""
        current = self.get(app_id, actor)
        self.require_write(current, authority or actor)
        if current["revision"] != expected_revision:
            raise ProjectionError("projection.conflict", "工作台已被更新，请刷新后重试", 409)
        data = self.updated_manifest(current, payload)
        self.prepare_application(data, actor, current)
        revision = expected_revision + 1
        with self.connect() as connection:
            self._require_live_manifest(connection, app_id, data)
            result = connection.execute('UPDATE apps SET revision=%s,updated_at=%s,navigation=%s WHERE id=%s AND revision=%s',
                                        (revision,data['updated_at'],data['navigation'],app_id,expected_revision))
            if result.rowcount != 1:
                raise ProjectionError("projection.conflict", "工作台已被更新，请刷新后重试", 409)
            tables.save_version(connection, app_id, revision, data)
        return self.get(app_id, actor)

    def publish(self, app_id: str, actor: str, revision: int) -> dict:
        current = self.get(app_id, actor)
        self.require_write(current, actor)
        validate_manifest(current)
        if current.get("application"):
            from .application_build import read_artifact
            build = current.get("application_build") or {}
            if build.get("status") != "ready" or build.get("commit") != current["application"]["commit"]:
                raise ProjectionError("projection.application_build", "只能发布构建成功的应用包修订", 409)
            for path in build.get("files", {}):
                read_artifact(build, path)
        if not current.get("builtin") and not current.get("manifest") and not current.get("html", "").strip():
            raise ProjectionError("projection.empty", "请先生成或填写页面源码")
        with self.connect() as connection:
            result = connection.execute('UPDATE apps SET published=%s WHERE id=%s AND revision=%s', (revision, app_id, revision))
            if result.rowcount != 1:
                raise ProjectionError("projection.conflict", "发布版本已变化，请重新预览", 409)
        return self.get(app_id, actor)

    def delete(self, app_id: str, actor: str) -> dict:
        self.require_write(self.get(app_id, actor), actor)
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))")
            if connection.execute('SELECT 1 FROM native_entrypoints WHERE app_id=%s', (app_id,)).fetchone():
                raise ProjectionError("projection.active_entrypoint", "不能删除正在使用的权威业务入口；请先显式切换到已验证的工作台", 409)
            connection.execute('DELETE FROM apps WHERE id=%s', (app_id,))
            connection.execute('DELETE FROM versions WHERE app_id=%s', (app_id,))
            connection.execute('DELETE FROM preferences WHERE app_id=%s', (app_id,))
            connection.execute('DELETE FROM role_requests WHERE workbench_id=%s', (app_id,))
        from management_portal import presentation_store
        presentation_store.forget_favorite_target(FAVORITE_SURFACE, app_id)
        return {"deleted": app_id}

    def require_write(self, app: dict, actor: str) -> None:
        if not self.authorized(app, actor, "manage"):
            raise PermissionError("无权修改此工作台")

    @staticmethod
    def authorized(app: dict, actor: str, operation: str) -> bool:
        # Any User or Resident may open a workbench; the permission system
        # decides who may change, publish and delete it.
        if not is_user_or_resident(actor):
            return False
        if operation == "read":
            return True
        return app["workbench_id"] in _changeable(actor, [app["workbench_id"]])

    def job(self, job_id: str, actor: str, data: dict | None = None) -> dict:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM generations WHERE id=%s', (job_id,)).fetchone()
            if row:
                stored = generation_storage.decode(row)
                workbench_id = str(stored.get("workbench_id") or "")
                if not is_active_user_or_resident(actor):
                    raise PermissionError("无权访问生成任务")
                if data is not None and data.get("workbench_id", "") != workbench_id:
                    raise ProjectionError("projection.job_workbench", "生成任务所属工作台不能修改", 409)
            if data is not None:
                data = {**data, "created_by": row["actor"] if row else actor}
                generation_storage.save(connection, job_id, data['created_by'], data)
                return copy.deepcopy(data)
            if row is None:
                raise ProjectionError("projection.job_not_found", "生成任务不存在", 404)
            return generation_storage.decode(row)

    def resident_job(self, job_id: str, resident_id: str) -> dict:
        """Return a generation job only to the Resident assigned to execute it."""
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM generations WHERE id=%s', (job_id,),
            ).fetchone()
        if row is None:
            raise ProjectionError("projection.job_not_found", "生成任务不存在", 404)
        data = generation_storage.decode(row)
        if data.get("resident_id") != resident_id:
            raise PermissionError("只有本次生成任务绑定的设计师可以提交结果")
        return {**data, "created_by": row["actor"]}

    def state(self, app_id: str, actor: str, value: dict | None = None, *, panel: str = "") -> dict:
        app = self.get(app_id, actor)
        if panel and panel not in {item['id'] for item in app.get('manifest', {}).get('sidebar', [])}:
            raise ProjectionError("projection.panel", "功能区不存在")
        with self.connect() as connection:
            if panel:
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'projection-state:{actor}:{app_id}',))
                stored = connection.execute('SELECT data FROM preferences WHERE actor=%s AND app_id=%s', (actor, app_id)).fetchone()
                container = json.loads(stored[0]) if stored else {}
                if value is None:
                    return container.get('panels', {}).get(panel, {})
                if not isinstance(value, dict):
                    raise ProjectionError("projection.state", "页面偏好必须是 JSON 对象")
                panel_value = value
                value = {**container, 'panels': {**container.get('panels', {}), panel: value}}
            if value is not None:
                if not isinstance(value, dict):
                    raise ProjectionError("projection.state", "页面偏好必须是 JSON 对象")
                encoded = json.dumps(value, ensure_ascii=False)
                if len(encoded.encode()) > 64_000:
                    raise ProjectionError("projection.state_size", "页面偏好最多 64 KB")
                connection.execute('INSERT INTO preferences VALUES (%s, %s, %s) ON CONFLICT(actor,app_id) DO UPDATE SET data=excluded.data', (actor, app_id, encoded))
                return panel_value if panel else value
            row = connection.execute('SELECT data FROM preferences WHERE actor=%s AND app_id=%s', (actor, app_id)).fetchone()
            return json.loads(row[0]) if row else {}

    def latest_generation(self, app_id: str, actor: str) -> dict | None:
        self.get(app_id, actor)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE actor=%s AND workbench_id=%s ORDER BY created_at DESC, id DESC LIMIT 1",
                (actor, app_id),
            ).fetchone()
            return generation_storage.decode(row) if row else None
