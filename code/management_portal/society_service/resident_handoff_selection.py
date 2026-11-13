"""Admission of a recommendation choice, bound to its persisted source and one delivery slot."""
from management_common.resident_handoff_cards import SELECTION, cards, selected_card, selection_id, selection_part


def prepare(record, message, repository, *, relay_source=None):
    selection = selected_card(message)
    if selection is None:
        return
    from . import family_facade
    resident_id = record["resident_id"]
    source_id = selection.get("source_message_id")
    index = selection.get("card_index")
    parts = message.get("content_parts") or []
    if (sum(part.get("type") == SELECTION for part in parts) != 1
            or any(part.get("type") not in {"text", SELECTION} for part in parts)
            or type(index) is not int or index < 0 or not isinstance(source_id, str)):
        raise ValueError("推荐选择必须关联原卡片")
    source = repository.message(resident_id, source_id)
    if source is None and family_facade.is_facade(record):
        source = family_facade.projected_reply(record, source_id)
    if source is None and relay_source:
        # Follow only the admitted relay carrying this exact selection, never a client-supplied author.
        for holder, message_id in [relay_source, *family_facade.relay_sources_of(*relay_source)]:
            original = repository.message(holder, message_id) or {}
            if original.get("message_id") != message.get("message_id") or original.get("content_parts") != parts:
                break
            source = repository.message(holder, source_id)
            if source:
                break
    options = cards(source)
    if not relay_source and (message.get("quote") or {}).get("message_id") != source_id:
        raise ValueError("推荐选择必须回复原卡片")
    if index >= len(options) or not options[index]["ask_message"]:
        raise ValueError("原推荐卡片已不可用，请重新加载")
    if selection != selection_part(source_id, index, options[index]):
        raise ValueError("推荐选择与原卡片不一致，请重新加载")
    if message.get("message_id") != selection_id(source_id, index):
        raise ValueError("推荐选择的提交标识不匹配")
    if [part.get("text") for part in parts if part.get("type") == "text"] != ["替我问一下"]:
        raise ValueError("推荐选择只提交所选动作")
    if family_facade.is_facade(record) and not family_facade.quoted_member(record, message):
        raise ValueError("原卡片的发出成员已不可用，未重新路由")
