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
    D(name='GetPaperTranslationBinding', category='paper', owner='web', description='读取已发布Paper主Lineage与翻译绑定，不创建对象。',
      mutates=False, decides=FIXED, parameters=(), result=(R('workbench_id', description='Paper 工作台 ID'),
      R('lineage_id', description='已发布主 Lineage ID；未绑定时为空'),
      R('resident_id', description='历史翻译角色 Resident ID；未绑定时为空'))),
    D(name='EnsurePaperTranslationBinding', category='paper', owner='web', description='旧Kimi订阅翻译实验已永久取消；兼容入口不再创建或配置角色。',
      mutates=True, decides=FIXED, parameters=(), result=(R('workbench_id', description='旧接口的工作台 ID；当前入口固定拒绝'),
      R('lineage_id', description='旧接口的 Lineage ID；当前入口固定拒绝'),
      R('resident_id', description='旧接口的翻译角色 ID；当前入口固定拒绝'))),
    D(name='TranslatePaperText', category='paper', owner='web', description='旧Kimi订阅翻译实验已永久取消；兼容入口拒绝执行，请使用Reader翻译API。',
      mutates=True, decides=FIXED, parameters=(P('operation_id', description='旧操作标识；当前入口固定拒绝'),
        P('text', description='旧接口的待翻译文本'), P('source_language', description='源语言'),
        P('target_language', description='目标语言'), P('terminology', description='旧接口的术语约定'),
        P('refresh', 'boolean', '旧接口的刷新请求'), P('task_id', description='旧翻译任务 ID')),
      result=(R('task_id', description='旧翻译任务 ID；当前入口固定拒绝'),
        R('status', description='旧任务状态；当前入口固定拒绝'),
        R('text', description='旧翻译结果；当前入口固定拒绝'),
        R('error', description='失败说明；当前取消错误由标准指令错误回执返回'))),
    D(name="ExportPaperBibtex", category="paper", owner="web",
      description="按指定 Paper ID 或当前认证主体的收藏导出 BibTeX；不可读或缺失条目返回 warnings。",
      mutates=False, decides=FIXED, parameters=(
          P("paper_ids", "array", "Paper ID 数组，与 favorites=true 二选一", items={"type": "string"}),
          P("favorites", "boolean", "导出当前认证主体的收藏，与 paper_ids 二选一"),
      ), result=(R("bibtex", description="UTF-8 BibTeX 正文"),
                 R("paper_ids", "array", "实际导出的 ID"), R("warnings", "array", "省略项和缺失字段"))),
)
