"""Paper feature read-only export declaration; per-resource access is filtered by its owner."""
from management_portal.hatg.declarations import InstructionDeclaration as D, P, R, On

CATEGORIES = {"paper": "Paper"}
DECLARATIONS = (
    D(name='ResolveMessageContextReferences', category='conversation', owner='web',
      description='受理前以实际调用主体解析结构化材料引用，不创建会话或授予资源权限。',
      mutates=False, decides=(On('target_resident_id', 'resident'),),
      parameters=(P('target_resident_id', required=True), P('reference', 'object', required=True)),
      result=(R('snapshot', 'object'),)),
    D(name='GetPaperTranslationBinding', category='paper', owner='web', description='读取已发布Paper主Lineage与翻译绑定，不创建对象。',
      mutates=False, decides=(), parameters=(), result=(R('workbench_id'), R('lineage_id'), R('resident_id'))),
    D(name='EnsurePaperTranslationBinding', category='paper', owner='web', description='旧Kimi订阅翻译实验已永久取消；兼容入口不再创建或配置角色。',
      mutates=True, decides=(), parameters=(), result=(R('workbench_id'), R('lineage_id'), R('resident_id'))),
    D(name='TranslatePaperText', category='paper', owner='web', description='旧Kimi订阅翻译实验已永久取消；兼容入口拒绝执行，请使用Reader翻译API。',
      mutates=True, decides=(), parameters=(P('operation_id'), P('text'), P('source_language'), P('target_language'),
        P('terminology'), P('refresh', 'boolean'), P('task_id')),
      result=(R('task_id'), R('status'), R('text'), R('error'))),
    D(name="ExportPaperBibtex", category="paper", owner="web",
      description="按指定 Paper ID 或当前认证主体的收藏导出 BibTeX；不可读或缺失条目返回 warnings。",
      mutates=False, decides=(), parameters=(
          P("paper_ids", "array", "Paper ID 数组，与 favorites=true 二选一", items={"type": "string"}),
          P("favorites", "boolean", "导出当前认证主体的收藏，与 paper_ids 二选一"),
      ), result=(R("bibtex", description="UTF-8 BibTeX 正文"),
                 R("paper_ids", "array", "实际导出的 ID"), R("warnings", "array", "省略项和缺失字段"))),
)
