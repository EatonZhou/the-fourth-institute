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
    ('RegisterWorkspace', '将现成目录登记为新的 Workspace；注销只删登记，保留现成目录。'),
)) + (D(
    name='GetDirectoryCleanup', category='resource', owner='resource', decides=FIXED,
    description='查看目录文件清理进度；注销请求的原调用者或对对象有读取权限的调用者可以查看。',
    parameters=(R('resource_id', description='被删除或产生清理任务的对象 ID'),),
    result=(R('items', 'array', '文件清理状态、尝试次数、失败原因和完成时间；与登记是否已注销分别报告'),),
),)
