"""Paper application tools, attached only by explicit Resident registration."""
from management_portal.application_tools import ApplicationToolDeclaration as D
from management_portal.hatg.declarations import FIXED, P, R

DECLARATIONS = (
    D(ref="application:paper.translation_binding", name='GetPaperTranslationBinding', description='读取已发布Paper主Lineage与翻译绑定，不创建对象。',
      mutates=False, decides=FIXED, parameters=(), result=(R('workbench_id', description='Paper 工作台 ID'),
      R('lineage_id', description='已发布主 Lineage ID；未绑定时为空'),
      R('resident_id', description='历史翻译角色 Resident ID；未绑定时为空'))),
    D(ref="application:paper.export_bibtex", name="ExportPaperBibtex",
      description="按指定 Paper ID 或当前认证主体的收藏导出 BibTeX；不可读或缺失条目返回 warnings。",
      mutates=False, decides=FIXED, parameters=(
          P("paper_ids", "array", "Paper ID 数组，与 favorites=true 二选一", items={"type": "string"}),
          P("favorites", "boolean", "导出当前认证主体的收藏，与 paper_ids 二选一"),
      ), result=(R("bibtex", description="UTF-8 BibTeX 正文"),
                 R("paper_ids", "array", "实际导出的 ID"), R("warnings", "array", "省略项和缺失字段"))),
)


def export_paper_bibtex(inv):
    from management_portal.paper_bibtex import export_request
    return export_request(inv.call.arguments, actor=inv.caller)


def get_translation_binding(inv):
    from management_portal.paper_translation_binding import inspect_binding
    return inspect_binding(inv.caller)


HANDLERS = {"ExportPaperBibtex": export_paper_bibtex, "GetPaperTranslationBinding": get_translation_binding}
