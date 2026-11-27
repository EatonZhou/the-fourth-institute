"""Host script instructions (decision_8b510cbd9c58)."""
from ..declarations import ON_LINEAGE, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters, result=(), *, mutates=False):
    return D(name=name, category='script_run', owner='lineage', description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target='lineage_id',
             decides=decides)


READ_RUN = (
    R('lineage_id', description='脚本运行所属的 Lineage'),
    R('script_run_id', description='RunScript 返回的运行 ID'),
)
# The run record GetScriptRun returns (the stored fields minus service-private ones).
RUN_RECORD = (
    R('script_run_id', description='脚本运行 ID'),
    R('lineage_id', description='脚本运行所属的 Lineage'),
    R('status', description='running、succeeded、failed、timed_out、cancelled 或 lost'),
    R('path', description='脚本文件的绝对路径'),
    R('runtime', description='解释器：python、javascript 或 bash'),
    R('cwd', description='运行时的工作目录'),
    R('sha256', description='启动时脚本内容的 SHA-256'),
    R('argv', 'array', '命令行参数'),
    R('arguments', 'object', '交给脚本的 JSON 输入'),
    R('caller_id', description='启动运行的调用者'),
    R('effect_id', description='启动运行的 Effect；不是 Effect 启动时为空'),
    R('trigger_path_id', description='所属 Trigger Path；不在 Run 中启动时为空'),
    R('started_at', description='运行开始时间'),
    R('finished_at', description='运行结束时间；运行中为空'),
    R('duration_seconds', 'number', '运行用时（秒）；运行中为 0'),
    P('exit_code', 'integer', '脚本退出码；运行中或没有退出码时为空'),
    R('error_code', description='失败原因的错误码，没有错误时为空'),
    R('outputs', 'object', '脚本通过 set_output 写出的输出字段'),
    R('stderr_tail', description='标准错误末尾最多 8 KB'),
    R('artifacts', 'array', '已登记的产物，每项含 name、size、mime_type'),
    P('timeout_seconds', 'integer', '运行时限；没有设置时省略'),
    P('exclusive_key', description='启动时给出的互斥键'),
)
READ_RANGE = (
    P('offset', 'integer', '起始字节偏移，默认 0', minimum=0),
    P('limit', 'integer', '最多读取的字节数，默认 65536', minimum=1, maximum=65536),
)
READ_RESULT = (
    R('text', description='读到的字节按 UTF-8 解码的文本，无法解码处替换'),
    R('base64', description='读到的原始字节，base64 编码'),
    R('offset', 'integer', '本次读取的起始字节偏移'),
    R('next_offset', 'integer', '下一次读取的起始偏移'),
    R('size', 'integer', '日志或产物当前的总字节数'),
    R('eof', 'boolean', '是否已读到当前末尾'),
    R('mime_type', description='内容类型：日志为 text/plain，产物按文件名判断'),
    R('truncated', 'boolean', '日志超过保存上限、后面的输出未保存'),
)


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
    ), RUN_RECORD),
    _d('ListScriptRuns', ON_LINEAGE, '按 Lineage、Effect、状态或脚本文件查询脚本运行。', (
        R('lineage_id', description='要查询的 Lineage'), P('effect_id', description='只列出这个 Effect 启动的运行'),
        P('status', description='只列出这种状态：running、succeeded、failed、timed_out、cancelled 或 lost'),
        P('path', description='只列出这个脚本文件的运行；按解析后的绝对路径精确匹配'),
        P('limit', 'integer', '最多返回的条数，默认 50；按开始时间从新到旧', minimum=1, maximum=200),
    ), (R('runs', 'array', '运行记录，从新到旧：script_run_id、path、status、started_at、finished_at、exit_code、outputs、artifacts…'),
        R('total', 'integer', '符合条件的运行总数'))),
    _d('ReadScriptRunOutput', ON_LINEAGE, '按字节偏移读取脚本日志。', (
        *READ_RUN, R('stream', description='标准输出或标准错误', enum=('stdout', 'stderr')), *READ_RANGE,
    ), READ_RESULT),
    _d('ReadScriptRunArtifact', ON_LINEAGE, '按字节偏移读取脚本运行的已登记产物。', (
        *READ_RUN, R('name', description='GetScriptRun 返回的产物名'), *READ_RANGE,
    ), READ_RESULT),
    _d('CancelScriptRun', ON_LINEAGE, '停止尚在运行的脚本；已结束的运行保持原状。', (
        R('lineage_id', description='脚本运行所属的 Lineage'), R('script_run_id', description='RunScript 返回的运行 ID'),
    ), mutates=True),
)
