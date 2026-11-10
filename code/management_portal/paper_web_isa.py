"""Paper Web instruction adapter, sharing the HTTP export owner."""
from management_portal.paper_bibtex import export_request


def export_paper_bibtex(inv):
    return export_request(inv.call.arguments, actor=inv.caller)


HANDLERS = {"ExportPaperBibtex": export_paper_bibtex}
