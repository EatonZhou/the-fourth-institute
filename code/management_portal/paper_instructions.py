"""Paper feature read-only export declaration; per-resource access is filtered by its owner."""
from management_portal.hatg.declarations import InstructionDeclaration as D, P, R, On, FIXED

CATEGORIES = {"paper": "Paper"}
DECLARATIONS = (
    D(name='ResolveMessageContextReferences', category='conversation', owner='web',
      description='受理前以实际调用主体解析结构化材料引用，不创建会话或授予资源权限。',
      mutates=False, decides=(On('target_resident_id', 'resident'),),
      parameters=(P('target_resident_id', description='接收材料的目标 Resident ID', required=True),
                  P('reference', 'object', '材料的结构化引用；来源、范围和访问权限由 owner 核验', required=True)),
      result=(R('snapshot', 'object', '已鉴权的不可变材料快照与来源信息'),)),
    D(name='GetPaperTranslationBinding', category='paper', owner='web', discovery_group='application', description='读取已发布Paper主Lineage与翻译绑定，不创建对象。',
      mutates=False, decides=FIXED, parameters=(), result=(R('workbench_id', description='Paper 工作台 ID'),
      R('lineage_id', description='已发布主 Lineage ID；未绑定时为空'),
      R('resident_id', description='历史翻译角色 Resident ID；未绑定时为空'))),
    D(name="ExportPaperBibtex", category="paper", owner="web", discovery_group="application",
      description="按指定 Paper ID 或当前认证主体的收藏导出 BibTeX；不可读或缺失条目返回 warnings。",
      mutates=False, decides=FIXED, parameters=(
          P("paper_ids", "array", "Paper ID 数组，与 favorites=true 二选一", items={"type": "string"}),
          P("favorites", "boolean", "导出当前认证主体的收藏，与 paper_ids 二选一"),
      ), result=(R("bibtex", description="UTF-8 BibTeX 正文"),
                 R("paper_ids", "array", "实际导出的 ID"), R("warnings", "array", "省略项和缺失字段"))),
)
