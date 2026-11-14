"""Paper material-reference resolution for Resident input."""


def resolve_message_context_references(inv):
    from .paper_chat_research import resolve_reference
    args = inv.call.arguments
    if args['reference'].get('type') == 'business.history':
        from .paper_business_history import resolve
        return {'snapshot': resolve(actor=inv.caller, target_resident_id=args['target_resident_id'], reference=args['reference'])}
    if args['reference'].get('type') == 'paper.task-material':
        from .paper_agent_materials import resolve_task_reference
        return {'snapshot': resolve_task_reference(actor=inv.caller, target_resident_id=args['target_resident_id'], reference=args['reference'])}
    return {'snapshot': resolve_reference(actor=inv.caller,
        target_resident_id=args['target_resident_id'], reference=args['reference'])}

HANDLERS = {"ResolveMessageContextReferences": resolve_message_context_references}
