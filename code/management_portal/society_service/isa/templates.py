"""Template ISA adapters; kinds own validation and behavior."""

from management_portal.hatg.contracts import invalid, not_found

from .. import templates, effect_script_authoring
from ..templates import kinds, store, marketplace


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except templates.TemplateError as exc:
        raise invalid(str(exc)) from exc
    except KeyError as exc:
        raise not_found(f"模板或目标不存在：{exc.args[0]}") from exc


def create(inv):
    selection = inv.argument("from_selection")
    if selection is not None and (
        not selection.get("lineage_id")
        or not isinstance(selection.get("element_ids"), list)
    ):
        raise invalid("from_selection 需要 lineage_id 与 element_ids")
    return {
        "template": _call(
            templates.create,
            inv.argument("template") or {},
            inv.caller,
            from_selection=selection,
            dry_run=bool(inv.argument("dry_run")),
        )
    }


def update(inv):
    changes = {
        **(inv.argument("template") or {}),
        **{key: inv.arguments[key] for key in store.CONTENT if key in inv.arguments},
    }
    return {
        "template": _call(
            templates.update,
            inv.argument("template_id"),
            changes,
            inv.caller,
            dry_run=bool(inv.argument("dry_run")),
        )
    }


def apply(inv):
    return _call(
        templates.apply,
        inv.argument("lineage_id"),
        inv.argument("template_id"),
        inv.argument("target"),
        inv.caller,
        parameters=inv.argument("parameters"),
        title=inv.argument("title") or "",
        resident_settings=inv.argument("resident_settings"),
        match_choices=inv.argument("match_choices"),
        dry_run=bool(inv.argument("dry_run")),
    )


HANDLERS = {
    "ReviewTemplateScript": lambda inv: effect_script_authoring.review(inv, template=True),
    "ListTemplateStore": lambda inv: {"items": _call(marketplace.listing, inv.caller, search=inv.argument("search") or "", group=inv.argument("group"), store_id=inv.argument("store_id"))},
    "PublishTemplate": lambda inv: _call(marketplace.publish, inv.argument("template_id"), inv.caller, inv.argument("release_note") or ""),
    "InstallStoreTemplate": lambda inv: _call(marketplace.install, inv.argument("store_id"), inv.caller, target_template_id=inv.argument("target_template_id"), mode=inv.argument("mode") or "install"),
    "UnpublishTemplate": lambda inv: _call(marketplace.unpublish, inv.argument("store_id"), inv.caller),
    "ListTemplates": lambda inv: {
        "templates": _call(templates.list_templates, inv.argument("target_kind")),
        "kinds": kinds.available(),
    },
    "GetTemplate": lambda inv: {
        "template": _call(templates.get, inv.argument("template_id"))
    },
    "CreateTemplate": create,
    "UpdateTemplate": update,
    "DeleteTemplate": lambda inv: _call(templates.delete, inv.argument("template_id")),
    "RestoreTemplate": lambda inv: {
        "template": _call(templates.restore, inv.argument("template_id"), inv.caller)
    },
    "ApplyTemplate": apply,
}
