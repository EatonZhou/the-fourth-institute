"""ISA adapters for Lineage Function Block boundaries."""
from .. import function_block_domain as blocks


def functionalize(inv):
    block = blocks.functionalize(inv.argument("lineage_id"), inv.argument("element_ids"), inv.caller,
                                 **{k: inv.argument(k) for k in ("title", "x", "y") if k in inv.call.arguments})
    return {"function_block_id": block["id"]}


def unfunctionalize(inv):
    return {"element_ids": blocks.unfunctionalize(inv.argument("lineage_id"), inv.argument("function_block_id"), inv.caller)}


def update(inv):
    result = blocks.update(inv.argument("lineage_id"), inv.argument("function_block_id"), inv.caller,
                           **{k: inv.argument(k) for k in ("title", "x", "y", "add_element_ids", "remove_element_ids") if k in inv.call.arguments})
    return {"function_block": result} if result is not None else {}


def delete(inv):
    return blocks.delete(inv.argument("lineage_id"), inv.argument("function_block_id"), inv.caller)


HANDLERS = {"Functionalize": functionalize, "Unfunctionalize": unfunctionalize, "UpdateFunctionBlock": update,
            "DeleteFunctionBlock": delete}
