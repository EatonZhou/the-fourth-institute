"""Template ISA adapters; kinds own validation and behavior."""

from management_portal.hatg.contracts import invalid, not_found

from .. import templates
from ..templates import registry, store


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
    "ListTemplates": lambda inv: {
        "templates": _call(templates.list_templates, inv.argument("target_kind")),
        "kinds": registry.kinds(),
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
