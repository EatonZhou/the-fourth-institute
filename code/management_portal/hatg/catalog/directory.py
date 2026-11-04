"""Directory ownership and the independently reported filesystem effects."""
from ..declarations import FIXED, On, InstructionDeclaration as D, P, R


DECLARATIONS = tuple(D(
    name=name, category='resource', owner='resource', mutates=True,
    decides=(*FIXED, On('owner_id', 'resident', 'lineage', 'project', optional=True)),
    description=description,
    parameters=(R('path', description='区域内或区域外的绝对目录路径', max_length=4096),
                R('operation_id', description='本次操作的稳定 ID；同一 ID 重试沿用原 Workspace', max_length=200),
                P('owner_id', description='目录所属 Resident、Lineage 或 Project；省略表示属于 Workspace 自己；不授予权限'),
                P('title', description='Workspace 名称', max_length=200)),
    result=(R('workspace_id', description='稳定 Workspace ID'), R('resource', 'object', 'Workspace 记录')),
) for name, description in (
    ('CreateWorkspace', '在空位置创建由 Portal 管理的 Workspace，随所属对象删除。'),
    ('RegisterWorkspace', '将现成目录登记为新的 Workspace，按归属管理；注销时清理其拥有的目录和文件，目录内的嵌套登记一并注销。'),
)) + (D(
    name='GetDirectoryCleanup', category='resource', owner='resource', decides=FIXED,
    description='查看目录文件清理进度；注销请求的原调用者或对对象有读取权限的调用者可以查看。',
    parameters=(R('resource_id', description='被删除或产生清理任务的对象 ID'),),
    result=(R('items', 'array', '文件清理状态、尝试次数、失败原因和完成时间；与登记是否已注销分别报告'),),
),)


DECLARATIONS += tuple(D(
    name=name, category='resource', owner='lineage', mutates=mutates,
    target='lineage_id', decides=(On('lineage_id', 'lineage'),),
    description=description,
    parameters=(R('lineage_id', description='冻结创建请求所属的父 Lineage'),
                R('resource_id', description='尚未完成创建的 Resident ID')) + ((
                    P('originating_instruction_id', description='原始创建指令引用；owner 核验正常结束和同步初始化契约，不接受调用者证明'),
                ) if mutates else ()),
    result=(R('creation', 'object', '创建状态；取消和文件清理分别报告，可重试 pending cleanup'),),
) for name, mutates, description in (
    ('GetResourceCreation', False, '读取父 Lineage 中的资源创建状态与 writer 证据状态。'),
    ('CancelResourceCreation', True, '持久撤销未登记的资源创建并阻止继续或重试；文件清理须证明 writer 已停止，证据不足保持 pending。'),
))
