"""Paper feature read-only export declaration; per-resource access is filtered by its owner."""
from management_portal.hatg.declarations import InstructionDeclaration as D, P, R

CATEGORIES = {"paper": "Paper"}
DECLARATIONS = (
    D(name="ExportPaperBibtex", category="paper", owner="web",
      description="按指定 Paper ID 或当前认证主体的收藏导出 BibTeX；不可读或缺失条目返回 warnings。",
      mutates=False, decides=(), parameters=(
          P("paper_ids", "array", "Paper ID 数组，与 favorites=true 二选一", items={"type": "string"}),
          P("favorites", "boolean", "导出当前认证主体的收藏，与 paper_ids 二选一"),
      ), result=(R("bibtex", description="UTF-8 BibTeX 正文"),
                 R("paper_ids", "array", "实际导出的 ID"), R("warnings", "array", "省略项和缺失字段"))),
)
