"""Core material-reference instruction contributed by the Paper feature."""
from management_portal.hatg.declarations import InstructionDeclaration as D, P, R, On

DECLARATIONS = (
    D(name='ResolveMessageContextReferences', category='conversation', owner='web',
      description='受理前以实际调用主体解析结构化材料引用，不创建会话或授予资源权限。',
      mutates=False, decides=(On('target_resident_id', 'resident'),),
      parameters=(P('target_resident_id', description='接收材料的目标 Resident ID', required=True),
                  P('reference', 'object', '材料的结构化引用；来源、范围和访问权限由 owner 核验', required=True)),
      result=(R('snapshot', 'object', '已鉴权的不可变材料快照与来源信息'),)),
)
