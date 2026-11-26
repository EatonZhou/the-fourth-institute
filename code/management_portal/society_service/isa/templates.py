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


def create(inv, *, operation):
    selection = inv.argument("from_selection")
    if selection is not None and (
        not selection.get("lineage_id")
        or not isinstance(selection.get("element_ids"), list)
    ):
        raise invalid("from_selection 需要 lineage_id 与 element_ids")
    return {
        "template": _call(
            operation,
            inv.argument("template") or {},
            inv.caller,
            from_selection=selection,
        )
    }


def update(inv, *, operation):
    changes = {
        **(inv.argument("template") or {}),
        **{key: inv.arguments[key] for key in store.CONTENT if key in inv.arguments},
    }
    return {
        "template": _call(
            operation,
            inv.argument("template_id"),
            changes,
            inv.caller,
        )
    }


def apply(inv, *, operation):
    return _call(
        operation,
        inv.argument("lineage_id"),
        inv.argument("template_id"),
        inv.argument("target"),
        inv.caller,
        parameters=inv.argument("parameters"),
        title=inv.argument("title") or "",
        resident_settings=inv.argument("resident_settings"),
        match_choices=inv.argument("match_choices"),
    )


HANDLERS = {
    "ConfirmTemplateScript": lambda inv: effect_script_authoring.review(inv, decision="confirm", template=True),
    "CancelTemplateScriptDraft": lambda inv: effect_script_authoring.review(inv, decision="cancel", template=True),
    "ListTemplateStore": lambda inv: {"items": _call(marketplace.listing, inv.caller, search=inv.argument("search") or "", group=inv.argument("group"))},
    "GetTemplateStoreItem": lambda inv: {"item": _call(marketplace.get_item, inv.argument("store_id"), inv.caller)},
    "PublishTemplate": lambda inv: _call(marketplace.publish, inv.argument("template_id"), inv.caller, inv.argument("release_note") or ""),
    "InstallStoreTemplate": lambda inv: _call(marketplace.install, inv.argument("store_id"), inv.caller),
    "UpdateInstalledTemplate": lambda inv: _call(marketplace.update_installed, inv.argument("store_id"), inv.caller, target_template_id=inv.argument("target_template_id")),
    "InstallStoreTemplateAsNew": lambda inv: _call(marketplace.install_as_new, inv.argument("store_id"), inv.caller, target_template_id=inv.argument("target_template_id")),
    "UnpublishTemplate": lambda inv: _call(marketplace.unpublish, inv.argument("store_id"), inv.caller),
    "ListTemplates": lambda inv: {
        "templates": _call(templates.list_templates, inv.argument("target_kind")),
        "kinds": kinds.available(),
    },
    "GetTemplate": lambda inv: {
        "template": _call(templates.get, inv.argument("template_id"))
    },
    "CreateTemplate": lambda inv: create(inv, operation=templates.create),
    "GetTemplateCreatePreview": lambda inv: create(inv, operation=templates.create_preview),
    "UpdateTemplate": lambda inv: update(inv, operation=templates.update),
    "GetTemplateUpdatePreview": lambda inv: update(inv, operation=templates.update_preview),
    "DeleteTemplate": lambda inv: _call(templates.delete, inv.argument("template_id")),
    "RestoreTemplate": lambda inv: {
        "template": _call(templates.restore, inv.argument("template_id"), inv.caller)
    },
    "ApplyTemplate": lambda inv: apply(inv, operation=templates.apply),
    "GetTemplateApplyPreview": lambda inv: apply(inv, operation=templates.apply_preview),
}
