"""Host script instructions (decision_8b510cbd9c58)."""
from ..declarations import ON_LINEAGE, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters, result=(), *, mutates=False):
    return D(name=name, category='script_run', owner='lineage', description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target='lineage_id',
             decides=decides)


DECLARATIONS = (
    _d('RunScript', ON_LINEAGE, '以配置的服务账号在主机上异步运行 Python、JavaScript 或 Bash 脚本。', (
        R('path', description='脚本文件的绝对路径'),
        P('lineage_id', description='所属 Lineage，也是权限判定的对象；Effect 内由激活确定，Resident 直接调用时默认使用其所在 Lineage；User 直接调用须填写'),
        P('runtime', description='解释器；省略时按扩展名判断：.py、.js/.cjs/.mjs、.sh/.bash', enum=('python', 'javascript', 'bash')),
        P('argv', 'array', '命令行参数，最多 64 个，每个最多 4000 字符', max_length=64, items={'type': 'string', 'maxLength': 4000}),
        P('arguments', 'object', '交给脚本的 JSON 输入，最大 64 KB；脚本从 PORTAL_SCRIPT_INPUT 文件读取'),
        P('cwd', description='工作目录的绝对路径，默认脚本所在目录'), P('timeout_seconds', 'integer', minimum=1, description='可选正整数；省略时持续运行到结束或取消，无默认或最大时限'),
        P('exclusive_key', description='互斥键：同一 Lineage 内同键已有运行中的脚本时不再启动，返回 already_running', max_length=120),
    ), (R('script_run_id', description='脚本运行 ID；重放或互斥键冲突时为已有的那次运行'),
        R('status', description='新运行为 running；互斥键冲突为 already_running；重放为已有运行的当前状态'),
        R('started_at', description='运行开始时间')), mutates=True),
    _d('GetScriptRun', ON_LINEAGE, '读取脚本运行状态、输出字段与产物清单。', (
        R('lineage_id', description='脚本运行所属的 Lineage'), R('script_run_id', description='RunScript 返回的运行 ID'),
    )),
    _d('ListScriptRuns', ON_LINEAGE, '按 Lineage、Effect、状态或脚本文件查询脚本运行。', (
        R('lineage_id', description='要查询的 Lineage'), P('effect_id', description='只列出这个 Effect 启动的运行'),
        P('status', description='只列出这种状态：running、succeeded、failed、timed_out、cancelled 或 lost'),
        P('path', description='只列出这个脚本文件的运行；按解析后的绝对路径精确匹配'),
        P('limit', 'integer', '最多返回的条数，默认 50；按开始时间从新到旧', minimum=1, maximum=200),
    ), (R('runs', 'array', '运行记录，从新到旧：script_run_id、path、status、started_at、finished_at、exit_code、outputs、artifacts…'),
        R('total', 'integer', '符合条件的运行总数'))),
    _d('ReadScriptRunOutput', ON_LINEAGE, '按字节偏移读取脚本日志或产物；同时返回文本与 base64 字节。', (
        R('lineage_id', description='脚本运行所属的 Lineage'), R('script_run_id', description='RunScript 返回的运行 ID'),
        R('stream', description='读取标准输出、标准错误或产物', enum=('stdout', 'stderr', 'artifact')),
        P('name', description='stream 为 artifact 时要读取的产物名'),
        P('offset', 'integer', '起始字节偏移，默认 0', minimum=0),
        P('limit', 'integer', '最多读取的字节数，默认 65536', minimum=1, maximum=65536),
    ), (R('text', description='读到的字节按 UTF-8 解码的文本，无法解码处替换'), R('base64', description='读到的原始字节，base64 编码'),
        R('offset', 'integer', '本次读取的起始字节偏移'), R('next_offset', 'integer', '下一次读取的起始偏移'),
        R('size', 'integer', '日志或产物当前的总字节数'), R('eof', 'boolean', '是否已读到当前末尾'),
        R('mime_type', description='内容类型：日志为 text/plain，产物按文件名判断'),
        R('truncated', 'boolean', '日志超过保存上限、后面的输出未保存'))),
    _d('CancelScriptRun', ON_LINEAGE, '停止尚在运行的脚本；已结束的运行保持原状。', (
        R('lineage_id', description='脚本运行所属的 Lineage'), R('script_run_id', description='RunScript 返回的运行 ID'),
    ), mutates=True),
)
