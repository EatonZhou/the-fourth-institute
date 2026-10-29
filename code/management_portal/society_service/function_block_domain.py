"""Function Block commands: each commits one Function Block edit to a Lineage.

The rules of the edit itself live in ``function_block_authoring``.
"""
from __future__ import annotations

from . import function_block_authoring, lineage_aggregate


def functionalize(lineage_id, element_ids, actor, *, title="", x=None, y=None,
                template_id=None, element_keys=None, edit=None, function_block_id=None):
    from . import lineage_domain

    def apply(body):
        if edit:
            edit(body)
        return function_block_authoring.functionalize_in_body(body, element_ids, actor, title=title, x=x, y=y,
                                   template_id=template_id, element_keys=element_keys,
                                   function_block_id=function_block_id)
    _, block = lineage_domain.commit_lineage_edit(lineage_id, actor, apply, reason="Functionalize", full_view=False)
    return block


def update(lineage_id, function_block_id, actor, **changes):
    from . import lineage_domain

    def apply(body):
        if "family" in function_block_authoring.find_block(body, function_block_id) and any(changes.get(k) for k in ("add_element_ids", "remove_element_ids")):
            raise ValueError("Family membership requires UpdateFamily")
        if "family" in function_block_authoring.find_block(body, function_block_id) and changes.get("title") is not None:
            raise ValueError("Family title requires UpdateFamily")
        return function_block_authoring.update_in_body(body, function_block_id, actor, **changes)
    _, block = lineage_domain.commit_lineage_edit(lineage_id, actor, apply, reason="UpdateFunctionBlock", full_view=False)
    return block


def unfunctionalize(lineage_id, function_block_id, actor):
    from . import lineage_domain
    _, ids = lineage_domain.commit_lineage_edit(
        lineage_id, actor, lambda body: function_block_authoring.unfunctionalize_in_body(body, function_block_id),
        reason="Unfunctionalize", full_view=False)
    return ids


def delete(lineage_id, function_block_id, actor):
    from . import lineage_domain
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        ids, residents = function_block_authoring.deletion_scope(previous, function_block_id)
        if any("family" in b for b in previous["function_blocks"] if b["id"] in ids):
            raise ValueError("Family requires Unfamilize or DeleteFamily")
        lineage_domain.commit_lineage_edit(lineage_id, actor, lambda body: function_block_authoring.remove_elements_in_body(body, ids),
            reason="DeleteFunctionBlock", resident_ids=residents, full_view=False)
    return {"element_ids": sorted(ids - {function_block_id}), "resident_ids": sorted(residents)}
