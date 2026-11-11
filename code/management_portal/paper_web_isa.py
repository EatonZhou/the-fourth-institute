"""Paper Web instruction adapter, sharing the HTTP export owner."""
from management_portal.paper_bibtex import export_request
TRANSLATION_EXECUTION_ENABLED = False


def export_paper_bibtex(inv):
    return export_request(inv.call.arguments, actor=inv.caller)


def translate_paper_text(inv):
    if not TRANSLATION_EXECUTION_ENABLED:
        raise ValueError('Paper translation execution is closed pending isolated cache acceptance')
    # Kept closed while management-only bindings are activated.
    from management_portal.paper_translation_workflow import KimiTextTranslation
    args = dict(inv.call.arguments)
    service = KimiTextTranslation(inv.caller)
    if 'task_id' in args:
        if set(args) != {'task_id'}:
            raise ValueError('task_id cannot be combined with a new request')
        return service.poll(args['task_id'])
    operation_id = args.pop('operation_id', None)
    refresh = args.pop('refresh', False)
    return service.request(operation_id=operation_id, request=args, refresh=refresh)


def get_translation_binding(inv):
    from management_portal.paper_translation_binding import inspect_binding
    return inspect_binding(inv.caller)


def ensure_translation_binding(inv):
    from management_portal.paper_translation_binding import ensure_binding
    return ensure_binding(inv.caller)


HANDLERS = {"ExportPaperBibtex": export_paper_bibtex, 'TranslatePaperText': translate_paper_text,
            'GetPaperTranslationBinding': get_translation_binding,
            'EnsurePaperTranslationBinding': ensure_translation_binding}


def resolve_message_context_references(inv):
    from .paper_chat_research import resolve_reference
    args = inv.call.arguments
    return {'snapshot': resolve_reference(actor=inv.caller,
        target_resident_id=args['target_resident_id'], reference=args['reference'])}

HANDLERS['ResolveMessageContextReferences'] = resolve_message_context_references
