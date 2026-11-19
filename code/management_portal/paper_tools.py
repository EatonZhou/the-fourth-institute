"""Web handlers for Paper application tools."""

from .paper_tool_declarations import DECLARATIONS as DECLARATIONS


def export_paper_bibtex(inv):
    from management_portal.paper_bibtex import export_request
    return export_request(inv.call.arguments, actor=inv.caller)


def get_translation_binding(inv):
    from management_portal.paper_translation_binding import inspect_binding
    return inspect_binding(inv.caller)


HANDLERS = {"ExportPaperBibtex": export_paper_bibtex, "GetPaperTranslationBinding": get_translation_binding}
