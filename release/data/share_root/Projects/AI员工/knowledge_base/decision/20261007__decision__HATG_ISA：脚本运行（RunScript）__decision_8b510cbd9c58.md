---
kb_id: decision_8b510cbd9c58
type: decision
project: AI员工
created_at: '2026-10-07'
version:
  number: 27
  created_at: '2026-10-07T03:07:05Z'
  created_by:
    user: tfi-exported-user
    agent: resident_dfadf5a85ef9
  updated_at: '2026-10-19T20:58:52Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/script_runs/service.py
  - ${code_root}/management_portal/society_service/script_runs/runtime.py
  - ${code_root}/management_portal/society_service/script_runs/runner.py
  - ${code_root}/management_portal/society_service/script_runs/worker.py
  - ${code_root}/management_portal/hatg/catalog/script_run.py
  - ${code_root}/tests/cases/test_script_runs.py
  related:
  - decision_4adcc1ff2f2c
  - decision_5ff0cb51ed84
  - decision_481dc4e6292c
  - decision_898b8911a2ff
  - decision_b820faf42218
  - decision_3fe3cca8fd95
  - decision_fd760e898f9a
  supersedes: []
  derived_from: []
---
# HATG ISA：脚本运行（RunScript）

Lineage 中有一类工作只需要按明确规则执行，不需要模型判断：整理文件、清理日志、抓取外部数据、生成统计报告。`RunScript` 指令以 dirty-base 用户在主机上异步运行一份 Python、JavaScript 或 Bash 脚本文件，脚本结束时产生 `script.finished` Event。Effect 脚本仍然只调用 HATG ISA 指令（decision_4adcc1ff2f2c），运行程序就是调用 `RunScript`；Resident、页面和命令行也可以直接调用它。

整项不需要模型判断的定时维护用 Lineage 表达：`time.reached` 事件源 → Effect（调用 `RunScript`）→ 需要时用 `script.finished` 接下一步。Portal System 的后台功能配置、备份计划与已有自动流程归 decision_5ff0cb51ed84；本篇定义脚本执行契约，不另维护部署计划。

需要判断的工作仍由 Resident 的模型完成，不用预写脚本替代 Resident 的判断。运行器不向脚本注入模型账号凭证；这不构成凭证隔离，脚本的主机文件与服务访问能力见“运行环境”。

所属服务：SocietyService。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `RunScript` | 是 | 在主机上启动一次脚本运行，立即返回运行 ID | 对 `lineage_id` 有 @admin |
| `GetScriptRun` | 否 | 读取一次脚本运行的状态、输出字段和产物清单 | 对 `lineage_id` 有 @viewer |
| `ListScriptRuns` | 否 | 按 Lineage、Effect、状态或脚本文件列出脚本运行 | 对 `lineage_id` 有 @viewer |
| `ReadScriptRunOutput` | 否 | 按偏移读取一次运行的 stdout、stderr 或某个产物 | 对 `lineage_id` 有 @viewer |
| `CancelScriptRun` | 是 | 停止一次正在进行的脚本运行 | 对 `lineage_id` 有 @admin |

## `RunScript`

**参数：**

| 参数 | 说明 |
| --- | --- |
| `path` | 必填。脚本文件的绝对路径；调用者必须能读取它 |
| `lineage_id` | 所属 Lineage，也是权限判定的对象；Effect 调用使用当前激活的 Lineage，Resident 直接调用默认使用自身所在 Lineage，User 直接调用必须提供。显式值必须与激活所属一致 |
| `runtime` | `python`、`javascript` 或 `bash`。省略时按扩展名判断：`.py` 为 python，`.js`、`.mjs`、`.cjs` 为 javascript，`.sh`、`.bash` 为 bash；其他扩展名必须填写 |
| `argv` | 传给脚本的命令行参数，字符串列表，最多 64 项，每项最长 4,000 字符 |
| `arguments` | 传给脚本的 JSON 对象，编码后最大 64 KB |
| `cwd` | 工作目录，绝对路径；省略时为脚本所在目录 |
| `timeout_seconds` | 可选正整数，单位秒；无默认值与最大值，省略时一直运行到结束或被取消 |
| `exclusive_key` | 互斥键，最长 120 字符。同一 Lineage 的同一互斥键已有运行中的脚本时不启动新运行 |

**结果：** `script_run_id`；`status` 为 `running`，或因互斥键返回 `already_running`（此时 `script_run_id` 是正在进行的那次运行）；`started_at`。

- **异步执行。** 指令启动运行后立即返回，Effect 不等待脚本结束。脚本的结果通过 `script.finished` Event 或 `GetScriptRun` 读取。
- **幂等。** 同一幂等键返回同一次运行。Effect 中的每次调用自带幂等键（decision_4adcc1ff2f2c），Effect 重试不会重复启动脚本。
- **ISA 调用与运行记录保留发起者身份。** Effect 中调用时是本次 Run 的发起者，时间事件启动的 Run 是 Lineage 的运行主体；主机进程及文件访问使用 Portal 服务所属的系统用户。
- **启动时记录脚本 SHA-256。** 脚本从原路径执行，工作目录按 cwd 参数；不保存或挂载只读快照。哈希记录启动时读取的内容，源文件及其依赖仍可被后续编辑。

**运行环境：**

| runtime | 解释器 |
| --- | --- |
| `python` | Portal Release 运行环境的 Python，可以导入 Release 代码树中的 `management_portal` 等模块 |
| `javascript` | 系统 Node.js（22） |
| `bash` | `/bin/bash` |

## 脚本的输入与输出

运行器通过环境变量告诉脚本以下位置：

| 环境变量 | 内容 |
| --- | --- |
| `PORTAL_SCRIPT_INPUT` | 一个 JSON 文件：`script_run_id`、`arguments`、`caller_id`、`lineage_id`、`run_id`、`effect_id` |
| `PORTAL_SCRIPT_OUTPUT` | 脚本写入一个 JSON 对象（最大 64 KB），作为本次运行的输出字段；不写时输出为空对象 |
| `PORTAL_SCRIPT_ARTIFACTS` | 产物目录。写在这里的文件随运行保存，最多 200 个、合计 50 MB |
| `PORTAL_ISA_SOCKET` | 调用 HATG ISA 指令的本地套接字 |

- `argv` 作为脚本的命令行参数传入。
- stdout 和 stderr 是运行日志，各保存最多 10 MB，超出部分截断并注明。
- 退出码 0 为成功，非 0 为失败。输出文件不是 JSON 对象或超过上限时，运行失败，错误码 `script.invalid_output`。
- **脚本调用指令不接触令牌。** 三种运行环境各有一个客户端：Python 模块 `portal_script`（`isa(name, **arguments)`、`inputs()`、`set_output(fields)`），JavaScript 模块 `portal-script`（`await isa(name, arguments)`、`inputs()`、`setOutput(fields)`），Bash 命令 `portal-isa <指令> 名称=值 …`（输出结果 JSON）与 `portal-output 名称=值 …`。它们都经 `PORTAL_ISA_SOCKET` 发出请求，运行器以调用者身份转交 Portal，权限系统逐次检查。客户端成功时返回指令的 result 对象，失败时抛出错误或返回非零退出码。JavaScript 同时支持 `require` 与 ESM `import`。运行器持有本次运行独立的令牌，只能用于指令入口，不向脚本环境注入该令牌。有运行时限时令牌有效期覆盖该时限及收尾时间；无时限时没有到期时间。运行器结束即删除有效租约，协调器进一步禁用令牌记录。
- **Portal 重启期间自动重试。** Portal Web 拒绝连接时，运行器在 120 秒内重试（请求没有送达，不会重复执行）；只读指令收到 `instruction.owner_unavailable`（所属服务正在重启）时也在 120 秒内重试。改变状态的指令不自动重试，结果交还脚本自行处理。

## 运行环境

脚本以 dirty-base 用户直接在主机上运行。它能访问该用户可访问的全部文件、凭证目录、本地服务套接字和网络，使用真实 HOME、PATH 与主机 /tmp，不隔离 PID、IPC 或 UTS。能调用 RunScript 的主体因此拥有 dirty-base 的主机权限；TFI portal 接受这一能力边界。

默认方案要求对所属 Lineage 有 `@admin`（TFI portal 2026-10-19）。Lineage 在 R 树中挂在所属 Project 下，所以 Project 的 admin（默认方案中包括该 Project 的 Resident）能在本 Project 的 Lineage 中运行脚本，对其他 Project 的 Lineage 只有 viewer，不能运行；不属于 Project 的 Lineage 按它在 R 树中的位置判定。判定用的 Lineage 与运行所属的 Lineage 由同一段代码确定：Effect 内取激活的 Lineage，Resident 省略 `lineage_id` 时取其所在 Lineage，User 省略时以缺少 `lineage_id` 拒绝；省略参数不会落到其他节点或免检。TFI portal 2026-10-19 接受“项目 admin 通过 RunScript 得到无沙箱的主机脚本能力”：例如 Resident 可以用 RunScript 编译文章，不需要为编译另设指令。以 Portal 服务身份运行的 Effect 不经过权限判定。脚本调用 ISA 时仍逐次以发起者为执行者、按发起者自身的权限检查相应指令。

- **每次运行交由 Portal 进程外的原生执行单元。** Linux 使用用户级 systemd 临时单元，保留 KillMode=control-group 与 5 秒停止宽限；macOS 启动独立会话的 runner，持久记录 PID，取消时核对进程身份后向其进程组发送 SIGTERM。Portal 重启或发布不会中断已启动的脚本。Windows 的逐运行本地 ISA relay 尚未实现，RunScript 在准入前明确拒绝。
- **Linux 环境来自用户级 systemd 管理器，同时显式传入发起服务配置的规范站点与路径变量；macOS runner 仅继承 HOME、PATH、语言与相同路径白名单。** 路径白名单为 PORTAL_SITE_CONFIG、PORTAL_PATHS_CONFIG、PORTAL_RELEASE_ROOT 与 portal_paths.ENV_KEYS 中的路径变量；只传入发起服务环境实际存在的项，Linux 每项使用独立的 --setenv 参数。不得通配转发 PORTAL_* 或补读旧环境变量。runner 再向脚本加入 PORTAL_SCRIPT_*、PORTAL_ISA_SOCKET、PATH、PYTHONPATH、NODE_PATH。Portal 不将服务进程的数据库连接或密钥环境变量加入该白名单；这不限制脚本直接访问主机。
- **运行不设置内存、CPU 或进程数量上限。** 仅在显式提供 timeout_seconds 时设置运行时限，超过后终止并记录 timed_out；省略时持续运行到结束或取消。
- **测试隔离由 Test Framework 自己负责。** 它的本地隔离组仍使用 bubblewrap 隔离凭证、网络和真实数据，辅助实现位于 management_portal/test_framework/isolation.py，与 RunScript 的主机执行无关。

## 运行状态与记录

| 状态 | 含义 |
| --- | --- |
| `running` | 正在运行 |
| `succeeded` | 退出码为 0，输出有效 |
| `failed` | 退出码非 0，或输出无效 |
| `timed_out` | 超过运行时限被终止 |
| `cancelled` | 被 `CancelScriptRun` 停止 |
| `lost` | 运行单元已经消失却没有留下结果，由运行协调器判定 |

运行记录包括：`script_run_id`、`path`、`runtime`、`sha256`、`argv`、`arguments`、`caller_id`、`lineage_id`、`run_id`、`effect_id`、`exclusive_key`、`status`、`exit_code`、`error_code`、`outputs`、`stderr_tail`（末尾 8 KB）、`artifacts`（名称、大小、媒体类型）、`started_at`、`finished_at`、`duration_seconds`。

运行记录、日志和产物保存 60 天，到期一并删除。运行记录由 PostgreSQL `portal_state` 中的 `script_runs` namespace 保存，与 LineageData 的 schema 版本独立。

Dev 与 Release 共用同一份运行记录；判定 `lost`、产生 `script.finished`、清理到期记录由持有租约的一个实例完成，完成事件使用稳定派发游标，通知使用稳定通知 ID；协调器在写入完成标记前崩溃时可安全重试。运行器先完成子进程与 ISA 转发服务的收尾，删除本次运行的凭证租约，再原子发布结果文件；完成结果可见时，该租约已经失效，成功、失败与取消均遵守此顺序。协调器在发现结果后持久化终态。单元消失且没有结果时，协调器根据运行时限判定 timed_out 或 lost。

## `script.finished` Event Source

- **观察一个或多个 Effect**（Watch 连线指向 Effect）。被观察的 Effect 中每次 `RunScript` 启动的运行结束时产生一次 Event。
- **`config.outcome`**：`any`（默认）、`succeeded`，或 `failed`（包括 `failed`、`timed_out`、`cancelled`、`lost`）。
- **payload**：`script_run_id`、`status`、`exit_code`、`outputs`、`path`、`duration_seconds`。下游用 `event.payload.outputs.<字段>` 读取脚本输出。
- **Event 属于启动脚本的那次 Effect 激活所在的 Run**，发起者相同。

**自动触发的脚本失败时通知该 Lineage 的人类管理员。** Run 由 `time.reached` 或 `file.changed` 启动、其中的脚本以 `failed`、`timed_out` 或 `lost` 结束时，系统在通知时通过权限端口找出本人名下有表项覆盖该 Lineage 的 admin 的活动 User：表项可以写在该 Lineage 或它在 R 树上的祖先节点上，因此包含管理员；Lineage 创建者按创建者规则对它有 admin（decision_b820faf42218）。通知接收人独立于运行身份，完成事件继续保留原 Run 和原始身份。没有活动人类接收者时仍保留失败记录和 `script.finished` 事件。通知标题为“自动触发的脚本失败：<脚本文件名>”；同一个 Effect 按 UTC 日期只通知第一次。自动触发的 Effect 在脚本启动前失败时也通知同一组人类管理员，Resident 和 Effect 的失败通知见 decision_5ff0cb51ed84。

## 读取与停止

- **`GetScriptRun`**：必填参数 `lineage_id`、`script_run_id`，返回上节的运行记录。
- **`ListScriptRuns`**：必填 `lineage_id`；可选参数 `effect_id`、`status`、`path`、`limit`（默认 50，最大 200），返回 `{runs, total}`，按开始时间倒序；total 只包含调用者能读取的匹配记录。
- **`ReadScriptRunOutput`**：必填 `lineage_id`、`script_run_id`、`stream`（`stdout`、`stderr` 或 `artifact`）、`name`（`stream=artifact` 时的产物相对路径）、`offset`、`limit`（最大 65,536 字节）。返回 `text`、`base64`、`offset`、`next_offset`、`size`、`eof`、`mime_type`、`truncated`；偏移与长度按字节计。文本采用 UTF-8 替换解码，二进制使用 base64。产物清单每项包含 `name`、`path`（同一相对路径）、`size`、`mime_type`，只读取已登记产物，不跟随越界符号链接。
- **`CancelScriptRun`**：必填参数 `lineage_id`、`script_run_id`。运行已结束时不改变它；停止进程期间若已写出自然结束的结果，保留该结果，不覆盖为 cancelled。
- 启动阶段收到的取消请求也必须生效：命令准备完成时已取消就不再启动脚本；创建进程期间收到取消，runner 取得进程句柄后立即终止整个进程组并回收，写出 cancelled 结果并删除凭证租约。取消不等待脚本的运行超时；Linux systemd 单元保留 5 秒停止宽限。macOS 在 runner PID 尚未持久化时保存取消请求，启动完成后再执行。ISA 套接字存在只表示转发入口已建立，不表示脚本已经启动。
- 默认模板对读取与列表要求所属 Lineage 的 viewer，对取消要求 admin。运行领域核对 script_run_id 的真实归属与 lineage_id 相同，不匹配时返回不存在；列表只返回该 Lineage 的记录。发起这次运行不授予取消权限。产物路由与页面调用同样携带 lineage_id。

## Portal 页面

- **Workbench 的 `script.finished` 检查器使用服务端事件目录。** Watch Effect 可多选；运行结果可选择全部、成功或失败（含超时、取消和丢失），保存时更新 Watch 连线与 `outcome` 配置。
- **Workbench 的运行记录逐项展示 RunScript 启动的脚本。** `GetTriggerPath` 的执行记录提供 `script_run_id` 或 `business_ids.script_run_id`，页面以 GetScriptRun 读取状态、用时、输出字段与产物；调用记录可翻页。运行中每 5 秒跟进状态，页面隐藏时暂停读取，运行结束或视图关闭后停止刷新。
- **stdout 与 stderr 按字节分页读取。** 展开时分别读取第一段，用户可继续读取；到达日志末尾后随运行刷新。页面保留跨段 UTF-8 字符，输出字段和日志按纯文本显示，达到服务端日志保存上限时提示截断。
- **HTML 产物在不共享 Portal 来源的框架中查看。** 框架使用 `sandbox="allow-scripts"`，没有 `allow-same-origin`。产物响应同时设置 `Content-Security-Policy: sandbox allow-scripts`，因此直接打开内容地址仍受隔离。脚本可在产物内部交互，不能读取 Portal Cookie、localStorage、父页面或调用 API；CSP 禁止网络连接、子框架、表单提交和外部资源，自包含的内联样式与 data/blob 图片可显示。
- **产物内容沿用运行记录的读取权限。** Web 路由先以当前登录者调用 GetScriptRun 确认产物清单，再以同一身份用 ReadScriptRunOutput 分段取字节，不读取客户端指定的文件系统路径，不向 HTML 注入 Portal 的身份或资产脚本。响应不缓存、禁止媒体类型嗅探与 Referer 传递。文本以纯文本预览，常见位图可预览，其他类型下载；所有产物均提供下载入口。
- **Home“定时与自动触发”面板包含脚本 Effect。** 由 `time.reached` 或 `file.changed` 激活且指令脚本包含 RunScript 的 Effect 按 Lineage 分组，显示脚本路径、触发计划和最近脚本运行状态与时间。暂停与恢复作用于事件源（decision_481dc4e6292c）。

## 定时维护放在哪里

Effect 脚本中的常量按当前指令声明使用 JSON 类型赋值，例如 `RunScript` 的 `timeout_seconds:=3600`；`=` 表达文本，布尔参数同样按其声明使用 `:=true` 或 `:=false`。保存时检查常量类型和范围，在源行给出 `effect.invalid_argument`；语法和编译规则见 decision_4adcc1ff2f2c。

Lineage 的维护脚本放在 `<Lineage Folder>/scripts/<工作名>/`，附 `README.md`；运行状态写在同目录的 `state/`。Portal System 与系统演化的功能归属、开关、备份和失败处理统一见 decision_5ff0cb51ed84。具体触发时间、所属 Lineage 和 Effect 参数从当前图定义读取；本篇不复制会随配置变化的计划表，也不另列已退役的内部指令入口。

## 协调与存储开销

脚本协调器每两秒按索引读取运行中或终态尚未通知的记录，每批最多 100 条，按运行 ID 游标轮转；已经通知的历史记录退出轮询。租约在独立表中获取和续租，处理批次期间每 10 秒续租，租期 30 秒；失去租约或本地租期届满后停止后续处理。慢通知不会让续租排在整批运行记录之后。完成事件继续沿用稳定派发游标，通知成功后才提交 notified；通知失败保留待办供后续尝试。

超过 60 天且已经完成通知的记录每分钟按批清理，每批最多 100 条；运行中或尚未通知的结果不提前删除。单次运行的读取、结果更新和取消只访问对应记录。列表在数据库中按 Lineage、Effect、状态或路径过滤并限制返回数量，不把其他 Lineage 的历史结果加载到进程中。幂等键与互斥键的竞争在短数据库事务中完成，原生执行单元的启动、停止及通知投递在该事务之外执行。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `RunScript` | `path`、`lineage_id`、`runtime`、`argv`、`arguments`、`cwd`、`timeout_seconds`、`exclusive_key` |
| `GetScriptRun` | `lineage_id`、`script_run_id` |
| `ListScriptRuns` | `lineage_id`、`effect_id`、`status`、`path`、`limit` |
| `ReadScriptRunOutput` | `lineage_id`、`script_run_id`、`stream`、`name`、`offset`、`limit` |
| `CancelScriptRun` | `lineage_id`、`script_run_id` |
