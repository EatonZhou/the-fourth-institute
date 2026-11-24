"""Published snapshots and atomic synchronization of personally owned copies."""

from copy import deepcopy
import secrets

from management_common.database_time import decode_times, encode_times
from management_common.relational_rows import sync_rows

from ..effect_script_candidate import require_publishable
from . import kinds, store
from .registry import TemplateError, now

TIMES = ("published_at", "updated_at", "unpublished_at")
INSTALL = ("installed_from_store_id", "installed_store_version", "installed_store_hash")
FIELDS = ("source_template_id", "publisher", "version", "release_note", *store.CONTENT,
          "content_hash", "versions", "active", *TIMES)


def items(connection):
    return [decode_times(row, TIMES) for row in connection.execute("SELECT * FROM template_store_items")]


def _write(connection, rows):
    sync_rows(connection, "template_store_items", ("store_id",), FIELDS,
              [encode_times(row, TIMES) for row in rows],
              json_columns=("targets", "parameters", "body", "versions"))


def group_for(template):
    return {"structure": "duty", "family_duty": "duty", "familize": "organization", "role": "role"}.get(template["kind"], "task")


def instructions_used(template):
    from ..instruction_name_migration import normalize_template
    from ..lineage_effect_script import parse

    kind, body = template["kind"], normalize_template(template)["body"]
    if kind == "prompt":
        return ["DeliverMessage"]
    if kind == "explore":
        return ["ForkResident", "CreateComponent", "DeliverMessage"]
    if kind == "familize":
        return ["Familize"]
    if kind == "family_duty":
        return ["MaintainFamilyReview"]
    if kind == "script":
        return list(dict.fromkeys(call.instruction for call in parse(body["script"]).calls))
    names = []
    for role in body.get("roles", []):
        if role["from"] in ("new", "fork"):
            names.append("CreateResident" if role["from"] == "new" else "ForkResident")
    for found in body.get("match", []):
        op = found.get("op")
        if op in ("delete", "update"):
            from .rewrite import is_wire
            noun = "Resident" if found.get("kind") == "resident" else "Wire" if is_wire(found) else "Component"
            names.append(("Delete" if op == "delete" else "Update") + noun)
    if body.get("components"):
        names.append("CreateComponent")
    if body.get("wires"):
        names.append("CreateWire")
    if body.get("function_block"):
        names.append("Functionalize")
    for component in body.get("components", []):
        script = component.get("config", {}).get("script")
        if script:
            names.extend(call.instruction for call in parse(script).calls)
    return list(dict.fromkeys(names))


def annotate(templates, records=None):
    if records is None:
        with store.database().read() as connection:
            records = items(connection)
    by_id = {item["store_id"]: item for item in records}
    by_source = {item["source_template_id"]: item for item in records}
    result = []
    for template in templates:
        source = by_id.get(template.get("installed_from_store_id"))
        published = by_source.get(template.get("template_id"))
        status = None
        if template.get("installed_from_store_id"):
            status = {"store_id": template["installed_from_store_id"],
                      "publisher": source["publisher"] if source else "",
                      "installed_version": template["installed_store_version"],
                      "latest_version": source["version"] if source else template["installed_store_version"],
                      "latest_release_note": source["release_note"] if source else "",
                      "store_active": bool(source and source["active"]),
                      "has_update": bool(source and source["active"] and source["version"] > template["installed_store_version"]),
                      "locally_modified": store.content_hash(template) != template["installed_store_hash"]}
        result.append({**template, "store_status": status,
                       "published_store": {key: published[key] for key in ("store_id", "version", "active", "published_at")} if published else None})
    return result


def describe(template):
    kind = kinds.get(template["kind"])
    return {**template, "behavior": kind.describe(template), "editor": kind.editor(),
            "application_parameters": kinds.application_parameters(template)}


def get_template(template_id):
    return describe(annotate([store.get(template_id)])[0])


def is_admin(actor):
    from management_portal import permission_system

    return permission_system.manage("CheckPermission", {"principal_id": actor, "a": "@admin", "resource_id": "@public"}, actor=actor)["allowed"]


def listing(actor, *, search="", group=None, store_id=None):
    templates = store.all_templates()
    with store.database().read() as connection:
        records = items(connection)
    annotated = annotate(templates, records)
    own = {row["template_id"]: row for row in annotated if row["created_by"] == actor}
    installed = {row["installed_from_store_id"]: row for row in reversed(annotated)
                 if row.get("installed_from_store_id") and row["created_by"] == actor}
    admin = is_admin(actor)
    result = []
    for item in records:
        if not item["active"] or (store_id and item["store_id"] != store_id):
            continue
        if group and group_for(item) != group:
            continue
        if search and search.casefold() not in " ".join(str(item[key]) for key in ("title", "description", "publisher")).casefold():
            continue
        local = installed.get(item["store_id"])
        status = local["store_status"] if local else {}
        result.append({**describe(item), "group": group_for(item),
                       "instructions_used": instructions_used(item), "is_publisher": item["publisher"] == actor,
                       "can_unpublish": item["publisher"] == actor or admin,
                       "source_available": item["source_template_id"] in own,
                       "installed_template_id": local["template_id"] if local else None,
                       "installed_version": status.get("installed_version"),
                       "has_update": status.get("has_update", False), "locally_modified": status.get("locally_modified", False)})
    return sorted(result, key=lambda item: (item["title"], item["store_id"]))


def publish(template_id, actor, release_note=""):
    store.ensure_builtins()
    admin = is_admin(actor)
    with store.database().transaction("templates") as connection:
        template = next((row for row in store.read_rows(connection, "templates") if row["template_id"] == template_id), None)
        if template is None:
            raise KeyError(template_id)
        if template.get("builtin_key"):
            raise TemplateError("内置模板不能发布到商店")
        require_publishable(template)
        creator = str(template.get("created_by") or "")
        resident_source = creator.startswith("resident_") and not template.get("installed_from_store_id")
        if not (creator == actor or (resident_source and admin)):
            raise TemplateError("只能发布自己创建的模板")
        rows = {row["source_template_id"]: row for row in items(connection)}
        previous = rows.get(template_id)
        version = previous["version"] + 1 if previous else 1
        stamp, digest = now(), store.content_hash(template)
        item = {key: deepcopy(template[key]) for key in store.CONTENT}
        item.update(store_id=previous["store_id"] if previous else "store-" + secrets.token_hex(6),
                    source_template_id=template_id, publisher=previous["publisher"] if previous else creator,
                    version=version, release_note=release_note,
                    content_hash=digest, active=True, published_at=stamp, updated_at=stamp, unpublished_at=None,
                    versions=[*(previous["versions"] if previous else []),
                              {"version": version, "release_note": release_note, "content_hash": digest, "published_at": stamp}])
        rows[template_id] = item
        _write(connection, rows.values())
    return {"item": next(row for row in listing(actor, store_id=item["store_id"])), "template": get_template(template_id)}


def install(store_id, actor, *, target_template_id=None, mode="install"):
    from ..instruction_name_migration import normalize_template

    if mode not in ("install", "overwrite", "copy"):
        raise TemplateError("未知安装方式")
    store.ensure_builtins()
    with store.database().transaction("templates") as connection:
        records = items(connection)
        item = next((row for row in records if row["store_id"] == store_id and row["active"]), None)
        if item is None:
            raise TemplateError("商店模板不存在或已下架")
        rows = {row["template_id"]: row for row in store.read_rows(connection, "templates")}
        previous = rows.get(target_template_id) if target_template_id else None
        if target_template_id and (not previous or previous["created_by"] != actor or previous.get("installed_from_store_id") != store_id):
            raise TemplateError("只能更新自己从该商店条目安装的模板")
        if mode == "overwrite" and not previous:
            raise TemplateError("覆盖更新需要 target_template_id")
        if mode == "install":
            previous = previous or next((row for row in rows.values() if row["created_by"] == actor and row.get("installed_from_store_id") == store_id), None)
            if previous:
                if previous["installed_store_version"] == item["version"]:
                    return {"template": describe(annotate([previous], records)[0])}
                if store.content_hash(previous) != previous["installed_store_hash"]:
                    raise TemplateError("该模板有本地修改，请选择覆盖或另存新版副本")
                mode = "overwrite"
        detached = None
        if mode == "copy" and previous:
            detached = previous["template_id"]
            previous.update({key: None for key in INSTALL})
        identity = previous["template_id"] if mode == "overwrite" else "template_" + secrets.token_hex(6)
        stamp = now()
        value = normalize_template({key: deepcopy(item[key]) for key in store.CONTENT})
        value.update(template_id=identity, builtin_key=None, builtin_source=None,
                     created_by=actor, created_at=previous["created_at"] if mode == "overwrite" else stamp,
                     updated_by=actor, updated_at=stamp, version=previous["version"] + 1 if mode == "overwrite" else 1,
                     installed_from_store_id=store_id, installed_store_version=item["version"], installed_store_hash=store.content_hash(value))
        rows[identity] = value
        store._write(connection, rows.values())
    return {"template": describe(annotate([value], records)[0]), **({"detached_template_id": detached} if detached else {})}


def unpublish(store_id, actor):
    admin = is_admin(actor)
    with store.database().transaction("templates") as connection:
        rows = items(connection)
        item = next((row for row in rows if row["store_id"] == store_id), None)
        if item is None:
            raise KeyError(store_id)
        if item["publisher"] != actor and not admin:
            raise TemplateError("只有发布者或公共内容管理员可以下架模板")
        item.update(active=False, unpublished_at=now(), updated_at=now())
        _write(connection, rows)
    return {"store_id": store_id, "active": False}
