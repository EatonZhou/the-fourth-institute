"""Published snapshots and atomic synchronization of personally owned copies."""

from contextlib import contextmanager
from copy import deepcopy
import secrets

from management_common.database_time import decode_times

from ..effect_script_candidate import require_publishable
from . import kinds, store
from .persistence import STORE_FIELDS as FIELDS, STORE_TIMES as TIMES, store_items as items, write_store_items as _write
from .registry import TemplateError, now

INSTALL = ("installed_from_store_id", "installed_store_version", "installed_store_hash")


def group_for(template):
    if template['kind'] == 'structure' and any(r.get('from') in {'new','fork'} for r in template['body'].get('roles', [])):
        return 'organization'
    return {"structure": "duty", "family_duty": "duty", "familize": "organization", "role": "role"}.get(template["kind"], "task")


def instructions_used(template):
    from ..isa_definition_normalization import normalize_template
    from ..lineage_effect_script import parse

    kind, body = template["kind"], normalize_template(template)["body"]
    if kind == "prompt":
        return ["DeliverMessage"]
    if kind == "explore":
        return ["ForkResident", "ApplyLineageChanges", "DeliverMessage"]
    if kind == "familize":
        return ["Familize"]
    if kind == "family_duty":
        from .family_graph import project

        body = project(template)["body"]
        return list(dict.fromkeys(
            call.instruction
            for component in body["components"]
            if (script := component["config"].get("script"))
            for call in parse(script).calls
        ))
    if kind == 'role':
        return ['Functionalize', 'ApplyLineageChanges']
    if kind == "script":
        return list(dict.fromkeys(call.instruction for call in parse(body["script"]).calls))
    names = []
    for role in body.get("roles", []):
        if role["from"] in ("new", "fork"):
            names.append("CreateResident" if role["from"] == "new" else "ForkResident")
    if body.get("components") or body.get("wires") or any(
        found.get("op") in {"delete", "update"} for found in body.get("match", [])
    ):
        names.append("ApplyLineageChanges")
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


def describe(template, *, editor_graph=True):
    kind = kinds.get(template["kind"])
    editor = kind.editor()
    if editor_graph and hasattr(kind, 'editor_graph'):
        editor = {**editor, 'graph': kind.editor_graph(template)}
    return {**template, "behavior": kind.describe(template), "editor": editor,
            "application_parameters": kinds.application_parameters(template)}


def get_template(template_id):
    return describe(annotate([store.get(template_id)])[0])


def is_admin(actor):
    from management_portal import permission_system

    return permission_system.manage("CheckPermission", {"principal_id": actor, "a": "@admin", "resource_id": "@public"}, actor=actor)["allowed"]


def _store_inventory(connection, actor, records):
    templates = sorted(
        store.read_rows(connection, "templates"),
        key=lambda row: (row["category"], row["title"], row["template_id"]),
    )
    annotated = annotate(templates, records)
    own = {row["template_id"]: row for row in annotated if row["created_by"] == actor}
    installed = {row["installed_from_store_id"]: row for row in reversed(annotated)
                 if row.get("installed_from_store_id") and row["created_by"] == actor}
    return own, installed


def _describe_item(item, actor, admin, own, installed):
    from ..resident_record_domain import find_resident
    publisher = find_resident(item['publisher']) if item['publisher'].startswith('resident_') else None
    local = installed.get(item["store_id"])
    status = local["store_status"] if local else {}
    return {**describe(item), "group": group_for(item),
            'publisher_title': (publisher or {}).get('title') or ('Resident' if item['publisher'].startswith('resident_') else item['publisher']),
            "instructions_used": instructions_used(item), "is_publisher": item["publisher"] == actor,
            "can_unpublish": item["publisher"] == actor or admin,
            "source_available": item["source_template_id"] in own,
            "installed_template_id": local["template_id"] if local else None,
            "installed_version": status.get("installed_version"),
            "has_update": status.get("has_update", False), "locally_modified": status.get("locally_modified", False)}


def listing(actor, *, search="", group=None):
    admin = is_admin(actor)
    with store.database().read() as connection:
        records = items(connection)
        own, installed = _store_inventory(connection, actor, records)
    result = []
    for item in records:
        if not item["active"]:
            continue
        if group and group_for(item) != group:
            continue
        if search and search.casefold() not in " ".join(str(item[key]) for key in ("title", "description", "publisher")).casefold():
            continue
        result.append(_describe_item(item, actor, admin, own, installed))
    return sorted(result, key=lambda item: (item["title"], item["store_id"]))


def get_item(store_id, actor):
    with store.database().read() as connection:
        row = connection.execute(
            "SELECT * FROM template_store_items WHERE store_id=%s AND active", (store_id,)
        ).fetchone()
        if row is None:
            raise KeyError(store_id)
        item = decode_times(row, TIMES)
        own, installed = _store_inventory(connection, actor, [item])
    return _describe_item(item, actor, is_admin(actor), own, installed)


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
        records = list(rows.values())
        own, installed = _store_inventory(connection, actor, records)
    return {"item": _describe_item(item, actor, admin, own, installed),
            "template": describe(annotate([template], records)[0])}


@contextmanager
def _installation(store_id):
    with store.database().transaction("templates") as connection:
        records = items(connection)
        item = next((row for row in records if row["store_id"] == store_id and row["active"]), None)
        if item is None:
            raise TemplateError("商店模板不存在或已下架")
        rows = {row["template_id"]: row for row in store.read_rows(connection, "templates")}
        yield connection, item, rows, records


def _owned_installation(item, rows, target_template_id, actor):
    previous = rows.get(target_template_id)
    if previous is None or previous["created_by"] != actor or previous["installed_from_store_id"] != item["store_id"]:
        raise TemplateError("只能更新自己从该商店条目安装的模板")
    return previous


def _installed_snapshot(item, actor, *, previous=None):
    from ..isa_definition_normalization import normalize_template

    stamp = now()
    value = normalize_template({key: deepcopy(item[key]) for key in store.CONTENT})
    value.update(
        template_id=previous["template_id"] if previous else "template_" + secrets.token_hex(6),
        builtin_key=None, builtin_source=None,
        created_by=actor, created_at=previous["created_at"] if previous else stamp,
        updated_by=actor, updated_at=stamp, version=previous["version"] + 1 if previous else 1,
        installed_from_store_id=item["store_id"], installed_store_version=item["version"],
        installed_store_hash=store.content_hash(value),
    )
    return value


def install(store_id, actor):
    with _installation(store_id) as (connection, item, rows, records):
        value = next((row for row in rows.values()
                      if row["created_by"] == actor and row["installed_from_store_id"] == store_id), None)
        if value is None:
            value = _installed_snapshot(item, actor)
            rows[value["template_id"]] = value
            store._write(connection, rows.values())
    return {"template": describe(annotate([value], records)[0])}


def update_installed(store_id, actor, *, target_template_id):
    with _installation(store_id) as (connection, item, rows, records):
        previous = _owned_installation(item, rows, target_template_id, actor)
        value = _installed_snapshot(item, actor, previous=previous)
        rows[target_template_id] = value
        store._write(connection, rows.values())
    return {"template": describe(annotate([value], records)[0])}


def install_as_new(store_id, actor, *, target_template_id):
    with _installation(store_id) as (connection, item, rows, records):
        previous = _owned_installation(item, rows, target_template_id, actor)
        previous.update({key: None for key in INSTALL})
        value = _installed_snapshot(item, actor)
        rows[value["template_id"]] = value
        store._write(connection, rows.values())
    return {"template": describe(annotate([value], records)[0]), "detached_template_id": target_template_id}


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
