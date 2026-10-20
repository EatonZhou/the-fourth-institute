---
kb_id: decision_5ff0cb51ed84
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 30
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:43:36Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 9
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/capacity_alerts.py
  - ${code_root}/scripts/portal_capacity_alerts.py
  - ${code_root}/management_portal/scheduled_triggered_monitor.py
  - ${code_root}/management_portal/backup_lineage.py
  - ${code_root}/scripts/resource_backup_lineage.py
  - ${code_root}/scripts/portal_pulse_lineage.py
  - ${code_root}/management_portal/society_service/lineage_event_dispatch_rules.py
  - ${code_root}/management_portal/society_service/templates/builtins.py
  - ${code_root}/management_portal/society_service/lineage_program_runtime.py
  - ${code_root}/management_portal/society_service/lineage_recurring_schedule.py
  - ${code_root}/management_portal/society_service/config.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  related:
  - decision_b06ae321a668
  - decision_865ac8ebef25
  - decision_0f57656a2acd
  - decision_ad3e21094f14
  - decision_1b2bdf5c33d6
  - decision_481dc4e6292c
  - decision_898b8911a2ff
  - decision_d19b9909a674
  - decision_2a3ff353165c
  - decision_4637ca4a69e2
  - decision_68215b3a825b
  - decision_5b5653c3eaa5
  - decision_4adcc1ff2f2c
  - decision_ae422b92c6f3
  - decision_25eb93d803a4
  supersedes: []
  derived_from: []
---
# 定时与自动触发工作及 Portal System 运行维护

Portal 中有一类工作不需要人在对话里逐次发起：知识库巡检、论文收集、每日日程整理，它们按时间表或某个事实的出现自动开始。这类工作称为**定时与自动触发的工作**。需要模型判断的工作由普通 Resident 承担；整项无需模型判断的维护由 Lineage 的 `time.reached → Effect → RunScript` 执行（decision_8b510cbd9c58）。Resident 是 Portal 中可以跨多次模型会话持续存在的 Agent 身份（decision_0f57656a2acd）；系统只有一种 Resident，它没有“后台”或“系统”之类的类型字段，也没有专用的调度表或命令清单。一项这样的工作什么时候开始、每次做什么，由这个 Resident 所在 Lineage 中的事件源和连线决定。

这样，定时与自动触发的工作和其他协作使用同一套对象与指令：创建 Resident、配置触发、查看运行和修改模型设置都通过 HATG ISA 指令完成（decision_898b8911a2ff），人和其他 Agent 可以用同样的方式查看和调整它。

## 基本概念

- **Lineage**：一组 Resident 及其协作关系组成的对象。Lineage 中除了 Resident 节点，还有事件源、动作组件和连接它们的连线（decision_4adcc1ff2f2c）。承担这类工作的 Resident 可以是团队 Lineage 中的一名成员，也可以独自占一个 Lineage；与其他工作没有协作关系的独立工作适合单独成一个 Lineage，彼此互不影响。
- **事件源（Event Source）**：Lineage 中产生 Event 的组件。Event 是一条被持久保存的事实，例如“到了每天 9 点”或“某个文件变了”。这类工作最常用两种事件源：
  - `time.reached`：按时间触发，支持四种计划——指定时刻触发一次、从启用起倒计时触发一次、按固定间隔重复、按日历（每天、每周指定星期几、每月指定日期的某个时刻，使用指定时区）重复；
  - `file.changed`：目标 Resident 工作区中的指定文件发生变化时触发。
  其他事件源（另一个 Resident 完成工作、共享状态满足条件等）也可以用来启动工作，完整目录见 decision_4adcc1ff2f2c。
- **Invoke 连线**：从事件源连到 Resident 的 Invoke 端口的连线。连线上写明每次要做的工作（任务说明）；事件到来时，系统把任务说明作为一条消息投递给这个 Resident。任务说明也可以写在调用 `DeliverMessage` 的 Effect（动作组件）的固定提示词中。
- **业务记录与产物**：Resident 在自己的工作区保存每次工作的记录和产物，需要长期引用的产物登记为资源（decision_ad3e21094f14）。

## 设计点

- **User 是可选的运行配置。** 未配置 `run_as_principal` 时，自动事件正常建立计划，由 Portal 服务记录观察事实和事件来源；接收输入的 Resident 使用自身身份和当前权限执行。配置了有效 User 时，运行以该 User 为发起者，Effect 按该 User 自己的权限调用指令，接收工作的 Resident 仍只按自己的权限执行（decision_b820faf42218）；配置保存仍经 `CreateLineage` 或 `UpdateLineage` 的既有权限检查。不从创建者、Leader 或 owner 推断权限，也不把服务的内部投递身份授予 Resident。显式配置的账号失效或配置格式错误时保留图定义并阻止自动激活；修复配置或清除可选 User 后，由同一调度器重新建立计划。
- **打开开关不表示计划已建立。** 自动源未暂停且未耗尽、但身份校验阻止激活时，运行态返回 `state=blocked` 与 `error`。暂停、耗尽仍保留各自状态，手动源不继承这个身份阻塞。页面展示受阻原因；可运行但下一时间尚未生成时显示等待计划建立，不能把周期文本当作具体下一次时间。身份变更后的诊断和运行态按当前事实重新读取，不把旧失败当作永久状态。

- **定时启动由 `time.reached` 事件源和一条 Invoke 连线表达。** 用户或 Agent 创建事件源，把它连到目标 Resident 的 Invoke 端口并写明任务；到时间后，任务沿连线投递给该 Resident。这类 Resident 的计时全部由 Lineage 的事件机制完成，不使用操作系统定时器。
- **内置 Function Block“定时工作”提供现成组合。** 它包含一个按固定间隔重复的 `time.reached` 事件源（默认 3600 秒）和一个手动事件源“运行一次”，两者都连到同一个 Resident，并写上同一段任务说明；参数是任务说明、间隔秒数和随机偏移秒数（decision_5b5653c3eaa5）。
- **重复计划可以设置随机偏移，让同时到期的工作错开启动。** 随机偏移 `jitter_seconds` 取 0–86400 秒，按固定间隔重复时还必须小于间隔。系统为每一次触发在 0 到偏移上限之间随机抽取实际开始时间，并在抽取后保存，重启后不会重新抽取。
- **立即运行一次就是手动触发。** 调用者对连到同一 Resident 的手动事件源调用 `TriggerEvent`，或直接用 `DeliverMessage` 把任务投递给这个 Resident。手动触发到达时目标正在工作，这次触发排队等待，不会被丢弃（连线显式配置了其他不可用策略时按配置处理）。
- **定时与自动触发的工作默认在新的上下文中运行。** 由 `time.reached` 或 `file.changed` 启动的工作不进入 Resident 当前的模型会话：每次运行使用空白会话，不读取也不改变 Resident 自己的对话上下文。连线配置 `context_policy="session"` 时改为在 Resident 当前会话中运行。Lineage 在投递之前记录这次投递的执行方式，Resident 开始工作时按它执行。
- **暂停调度就是暂停事件源。** `PauseComponent`（`paused: true`）暂停事件源后，到期的触发直接跳过；以 `paused: false` 恢复后从下一次计划时间继续，暂停期间的触发不补发。修改计划或随机偏移使用 `UpdateComponent`；永久停止时删除事件源或它到 Resident 的连线。
- **停止正在进行的工作与暂停调度是两件事。** `CancelInput` 按消息 ID 停止 Resident 对应的工作；`OpenIntake`（`open: false`）关闭 Resident 的接单开关，之后到达的任务排队等待、不执行，直到以 `open: true` 重新打开（decision_2a3ff353165c）。
- **模型选择就是 Resident 的运行设置。** 更换模型或推理强度使用 `UpdateResidentSettings`，更换 Provider 或账号使用 `RestartSession`（decision_d19b9909a674）；新设置从下一轮工作开始生效。
- **运行状态分三处读取。** Resident 是否在工作、正在处理哪条输入、还有哪些任务在等待，由 Resident 状态与队列给出（decision_2a3ff353165c）；每次事件或独立 Effect 激活触发了谁、是否完成，由 Trigger Path 及其步骤、Execution 关联给出（decision_68215b3a825b）；事件源自己保存已触发次数、下一次计划时间和最近一次投递的摘要，由 `GetComponentRuntime` 读取。
- **Home 的“定时与自动触发”面板把这些 Resident 汇总在一处。** 凡是有 `time.reached` 或 `file.changed` 事件源连到 Invoke 端口的 Resident，都作为一行显示并按所在 Lineage 分组，内容全部来自上述读取指令；页面上的暂停、恢复、修改定时和运行一次都提交为对应的指令（decision_481dc4e6292c）。
- **每次投递都是 Resident 的一轮模型工作。** 事件源经 Invoke 连线投递的任务，一律由目标 Resident 的模型完成。任务说明写的是这次要完成的工作；Portal 不提供跳过模型、直接替 Resident 运行一条命令的执行方式，连线也没有选择执行方式的字段。
- **其他确定性步骤在 Resident 的工作中完成。** 一项 AI 工作中的抓取、整理、校验这类不需要模型判断的步骤，由 Resident 在这一轮里自己调用工具完成，受它的工具权限和运行沙箱约束；判断和产出由 Resident 自己负责，不交给一个预先写好的流程脚本。
- **工作内容写在 Resident 自己的职责说明里。** 一项定时或自动触发的工作的目标、步骤、质量标准、产物位置和收尾记录写在 Resident Workspace 的 `duties/<工作名>.md`；确定性的小步骤做成同一 Workspace `tools/` 下的脚本。连线任务只说明这次要做什么并指向职责说明，手动入口需要参数时从触发附带的说明中读取。每次运行在 Workspace 的 `runs.md` 追加一行记录。
- **整项无需模型判断的定时维护由 Lineage 调用 RunScript。** `time.reached` 事件源连接调用 `RunScript` 的 Effect，脚本以 dirty-base 用户在主机上执行，ISA 调用保留 Lineage 的运行主体身份，结束后产生 `script.finished`。日志保留、知识库确定性整理、Paper CCF 缓存刷新与 Resource Backup 使用这个流程。业务脚本可放在 `<Lineage Folder>/scripts/<工作名>/`，随 Portal 发布的适配入口放在 Release 仓库的 `scripts/`；执行器仍负责其业务锁、秘密引用和产物（decision_8b510cbd9c58）。
- **定时或文件触发的运行失败时通知持有该 Lineage admin 权限的活动 User。** 系统在通知时通过权限端口找出本人名下有表项覆盖该 Lineage 的 admin 的活动 User：表项可以写在该 Lineage 或它在 R 树上的祖先节点上，因此包含管理员；Lineage 创建者按创建者规则对它有 admin（decision_b820faf42218）。接收人查询只用于确定通知投递对象，运行的认证身份保持原值。
- **自动触发的 Resident、Effect 和 RunScript 失败都会告诉 Lineage 的人类管理员。** 系统从 Trigger Path 的起始事件或当前记录识别 `time.reached`、`file.changed`，同一路径中的后续步骤同样适用。Resident 产生的新事件另建 Trigger Path，通过起始原因关联来源 Execution。Lineage 中有监听这次失败、并把事件交出去的失败处理者时，由它检查原因后再告诉管理员；没有时，平台以 Lineage Leader 的名义把失败写成一条消息，出现在管理员 Mailbox 的“对话”栏，不启动 Leader 的工作；没有 Resident 的 Lineage 和看不到 Leader 对话的管理员收到通知（decision_15f7df737ed5）。Resident 输入失败按连线和 UTC 日期去重，标题为“自动触发的任务失败：<Resident ID>”；Effect 失败或超时按 Effect 和 UTC 日期去重，包含 RunScript 参数错误导致脚本尚未启动的失败；已经启动的脚本以 failed、timed_out 或 lost 结束时另按启动它的 Effect 和 UTC 日期去重。系统保存失败终态，并用稳定的消息或通知 ID 去重；没有活动人类接收者时保留失败记录。手动触发沿原有运行观察流程处理。
- **执行身份始终是这个 Resident。** 它调用模型、写入资源或投递消息时都以自己的 `resident_*` 身份署名；身份缺失或失效时这一步失败，系统不改用其他身份重试（decision_ae422b92c6f3）。

## Portal System 的后台功能配置

`lineage_ed619225d417` 承担 Portal 运行功能的配置面板，每项功能封装成一个具名 Function Block。定时 Event、手动 Event 与 Effect 都使用既有暂停状态；应用页面读取 `GetLineage.program_event_source_runtime` 和 `program_effect_runtime`，不另存一份功能开关。暂停定时 Event 停止后续自动运行，独立的手动 Event 仍可运行一次；暂停 Effect 则阻止所有入口调用它。已经接纳的外部程序可能仍在运行，需要取消时对对应 ScriptRun 发出取消指令。

Provider Runtime 更新、日志与测试证据保留都具有独立的定时和手动入口。Resident 发现摘要先判断待写队列与承担者是否空闲，有任务才交给 Resident。后台异常处理通过 Resident 工作失败、Effect 调用失败和脚本失败事件交给明确的处理者，异常处理自身不监听自己的失败以免循环。

Resource Backup 的六类操作（关键状态、完整内容、校验、深度校验、保留策略、空间回收）在同一 Function Block 中配置。五项自动计划由 Lineage 接管，旧 systemd timer 在切换时关闭，保持只有一套调度。备份执行程序继续负责数据范围、加密、仓库锁和保留规则。Portal Pulse 的日、周、月与手动入口迁入 System，保留原 Resident 身份、摘要历史及个人发布记录。

应用适配器使用组件 `client_key` 定位流程端点，正式对象身份仍是资源 ID。Create、ApplyLineageChanges 与 Update 使用同一校验：可以给未标记的组件或连线补上标记，已经存在的非空标记不能改为另一个值，同一图内标记不可重复。改名和移动布局不影响应用绑定；缺失、断线或关闭时返回不可用，不回退到独立的后台调度器。

Notice Board 自动分类与 Which Resident 推荐由应用入口触发 Event，经 Effect 投递到 ApplicationTarget 指定的 Resident。分类保留持久待处理队列，关闭期间等待，不消耗失败重试次数；已有 Event 尚未取得投递回执时继续观察原触发路径，避免重复创建。推荐沿用网页调用者和候选快照，分类沿用服务调用者，应用流程不借用图拥有者权限。

`lineage_048353291724` 是 Portal 系统演化面板，集中表达 Harness 记录与采集、每日评估与评审、产物保留三个 Function Block。产物保留同时具有手动入口和可选定时入口，后者初始关闭。模型请求计时留在 Portal System。记录和计时的 Event 开关投影到 Execution 本地设置，执行侧不反向调用 Lineage；关闭只停止后续记录，保留在途请求和已有历史。旧代理监听器仅用于已有请求排空，不作为新请求计时的控制对象。

Resident 恢复先在执行准入锁之外收集输入状态与回复证据，再逐个输入在原锁内比较接受记录、执行关联、状态和成员关系；记录删除、替换或已结算时跳过旧观察。观察派发和积压投递继续使用原准入锁，避免批量证据读取阻塞图编辑，同时保持投递幂等与契约校验。

Codex 额度快照与周报告没有生产消费者，System 中不保留该自动流程；既有报告文件仍保留。Portal 的用量页面继续读取 Execution 账本和账号适配器提供的原生用量。额度百分比不转换为 token，某个 Provider 未提供外部用量能力时保持缺失，不推断成完整统计。

## 运行容量预警

Portal System 中的“运行容量预警”Function Block 用每五分钟一次的定时 Event 和一个“立即检查运行容量”手动 Event 调用同一个 RunScript Effect。脚本通过正式只读指令 `GetRuntimeCapacity` 观察数据库、主机磁盘和执行队列，不调用模型。来源与字段口径见 decision_4527448ad07f。

- **监测按来源独立判断。** PostgreSQL 使用全部客户端连接数与普通连接槽的比例；磁盘覆盖本机 `/`、`${share_root}`、`/data` 的现存文件系统，同一设备只计算一次；执行队列使用 ExecutionService 的排队总量，该总量包括配额、派发间隔和会话绑定等待。队列通知明确称为持续积压，不据总量判定数据库或并发容量不足。
- **首次超限与恢复各通知一次，升为紧急级别时补发一次。** 默认数据库使用率 80% 预警、90% 紧急、降至 70% 及以下恢复；磁盘使用率 85% 预警、95% 紧急、降至 80% 及以下恢复。预警和恢复均需相邻有效观察持续至少五分钟；数据库与磁盘的紧急阈值即时通知。执行队列达到 20 项预警、50 项紧急，超限需持续至少十五分钟；降至 5 项及以下持续五分钟后恢复。
- **观察中断不能被当作健康恢复。** 来源读取失败时保留其既有容量告警并中断待确认计时，连续五分钟不可读取产生该来源的采集异常通知；恢复读取持续五分钟后解除采集异常。两次有效观察相隔超过十五分钟时重新计时，重复或倒序快照不累加时长。
- **告警状态与待发通知在 Lineage 共享状态中持久保存。** 单元 `runtime_capacity_alerts` 保存最新快照、阈值、各指标当前级别与候选状态、通知序号和待发列表。先以比较并交换写入状态与待发通知，再调用 `CreateNotification`，最后确认已发送。通知使用稳定 ID；请求结果丢失或进程重启后重发同一 ID，Mailbox 中仍只有一条。并行读写冲突重新读取，不覆盖较新的观察。
- **接收人和阈值由 Effect 的 RunScript arguments 明确配置。** 初始接收人为 TFI portal，阈值采用上述默认值；调整配置不创建第二套功能开关。定时 Event 控制自动检查，手动 Event 保留即时检查入口；Effect 关闭后两种入口均不再运行。RunScript 使用同一独占键避免同时启动，运行记录包含实际采样与通知结果。
- **失败保留可恢复的事实。** 某个容量源不可读不阻断其他来源；共享状态或通知调用失败使脚本失败，沿既有脚本失败处理流程通知管理员，下一轮从待发列表继续。既有告警不因临时缺失指标、通知失败或暂停流程而宣告恢复。

## 一次定时工作怎样完成

```text
time.reached 事件源（到达计划时间 + 随机偏移）
        │ 产生 Event
        ▼
Invoke 连线（附上本次任务说明）
        │
        ▼
Resident 接收任务 ──正在工作时按重叠策略处理──> 等待队列
        │ 开始执行
        ▼
在工作区中调用脚本、工具和模型
        │
        ▼
保存业务记录与产物，必要时登记资源
        │
        ▼
这条输入进入终态，Lineage 运行记录收尾
```

SocietyService（负责运行 Lineage 的后台服务，decision_1b2bdf5c33d6）持久保存每个事件源的计划和已经触发到的位置，因此进程重启后从上次停下的地方继续，不会重复投递已经投递过的任务。SocietyService 的协调器在 Lineage 的下一个时间或文件事件源到期时唤醒这个 Lineage；次数已用完或已经到期处理过的事件源不再安排唤醒。Dev 与 Release 共用 Lineage 数据，定时由共享数据中的租约决定由哪一个实例运行，同一时刻只有一个实例运行定时，它停止后另一个实例接管；环境变量 `PORTAL_LINEAGE_TIMER_INSTANCE` 可以把定时限定在某个实例。重复计划还有两项策略处理例外情况：

| 情况 | 配置项 | 取值与效果 |
| --- | --- | --- |
| Portal 停机期间错过了一次或多次计划时间 | 错过执行策略 `misfire_policy` | `coalesce_latest`（默认）：只补发一次，代表最近一次错过的计划时间，并在 Event 中记下错过的次数；`skip`：全部跳过，等下一次计划时间 |
| 到期时上一轮工作还没结束 | 重叠执行策略 `overlap_policy` | `drop`（默认）：丢弃本次触发；`queue_one`：最多排队一次，Resident 空闲后执行；`latest`：只保留最新一次，覆盖尚未执行的旧触发 |

事件源处于暂停状态时，到期的触发一律跳过，不参与上述两项策略。

## 相邻 Design

- Resident 的创建、设置与会话切换见 decision_d19b9909a674；接单、停止与队列控制见 decision_2a3ff353165c。
- 事件源、连线和组件的定义与修改见 decision_4637ca4a69e2，事件目录与运行机制见 decision_4adcc1ff2f2c，运行观察见 decision_68215b3a825b。
- Home “定时与自动触发”面板的页面与健康判断见 decision_481dc4e6292c。
- 模型调用的排队与执行见 decision_25eb93d803a4。

## 配置阻塞与重试

自动事件显式配置的 run_as_principal 主体类型不符合 User 要求，或配置的账号不存在、已停用时，调度返回配置受阻原因，不安排指数退避或定时重试。协调器状态保留按 Lineage 列出的阻塞原因；Lineage 配置变更通知或重新检查会读取最新事实，配置有效后恢复计划。临时数据库、服务或通信故障继续使用退避重试，不按错误文案猜测是否属于配置问题。

未配置 User 是合法状态，不属于配置错误。该分类不补填创建者或 owner，也不改变已经接受的 Event 的发起者。已有事件的恢复沿用原始有效身份；缺失或无效的历史事件身份仍需显式重新授权发出。身份规则本身与协调器的故障重试策略分别维护。
