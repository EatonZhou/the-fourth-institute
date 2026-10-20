---
kb_id: decision_b06ae321a668
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 19
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/http_resources.py
  - ${code_root}/management_portal/resource_backup_backend.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/resource_backup.html
  - ${code_root}/management_portal/static/resource_backup.js
  - ${code_root}/tests/cases/test_resource_backup_backend.py
  - ${code_root}/tests/cases/test_resource_portal_routes.py
  - ${share_root}/Services/resource-backup/README.md
  - ${code_root}/management_portal/backup_lineage.py
  - ${code_root}/scripts/resource_backup_lineage.py
  related:
  - decision_4637ca4a69e2
  - decision_3fe3cca8fd95
  - decision_68215b3a825b
  - decision_5ff0cb51ed84
  - decision_481dc4e6292c
  - decision_ed57c4ad1001
  - decision_4a9ba5dfada6
  - decision_b6e74d78f7b6
  supersedes: []
  derived_from: []
---
# Portal Resource Backup 监控、触发与恢复边界

Resource Backup 负责把 TFI Portal 的关键数据定期写入加密的备份仓库，出事时可以从中恢复。实际的备份、校验和清理由系统层的备份服务完成。Portal System（lineage_ed619225d417）中的“资源备份”Function Block 通过 Event → Effect → RunScript 决定何时执行；备份服务保留数据范围、加密、保留策略与互斥锁，不再拥有独立自动调度。备份不调用模型。Portal 的 Resource Backup 页面让用户查看最近一次可恢复的时间、自动计划和仓库状态，并通过同一组手动 Event 发起固定操作。本 Design 说明这些对象、页面、权限、状态接口和动作接口，以及 Portal 在恢复操作中的边界。

## 先认识备份系统

- **备份服务**：由 `PORTAL_RESOURCE_BACKUP_ROOT` 指定的备份程序；未指定时采用 `portal_paths.path("share_root") / "Services" / "resource-backup"`（仅使用规范环境变量，路径配置规则见 decision_15aab18c0ab3）。入口是 `src/main.py`，用 restic 读写备份仓库。它把整体状态写在 `state/status.json`，把每次运行的记录写在 `state/runs/` 下。备份凭据只由它读取。
- **备份仓库（repository）**：存放快照的加密目标。页面只显示仓库类型、位置摘要、容量和加密状态，不显示原始路径和凭据。
- **快照与恢复点**：一次成功备份在仓库中留下一个快照。最近一次成功快照的时间称为**最近恢复点**，表示“现在出事最多会丢多久的数据”。
- **备份档位（profile）**：一次备份覆盖哪些内容。`critical`（关键状态）覆盖控制平面、会话和凭据元数据，适合频繁执行；`content`（完整内容）覆盖完整的 Resource 内容和可恢复工作区。
- **维护操作**：`check`（仓库校验）检查仓库元数据能否读取；`deep-check`（深度校验）额外读取约 10% 的数据块；`retention`（保留策略）按规则删除过期快照，加 `--prune` 时同时回收仓库空间。
- **运行记录（run record）**：一次备份或维护操作的记录，包括种类、状态、开始和结束时间、摘要和面向操作者的错误说明。
- **流程节点**：六种操作分别有一个手动 Event 和一个执行 Effect；除 `critical` 外，还各有一个定时 Event。定时与手动 Event 连到同一个 Effect。Effect 通过 RunScript 调用固定脚本，脚本只接受预先列出的操作，再调用备份服务入口。
- **应用绑定**：组件使用已有顶层 `client_key=system:backup:<operation>:<role>`，其中 role 为 `manual`、`timer` 或 `effect`。后端直接从 GetLineage 定义解析这些稳定标记，不另存一份绑定或开关状态。组件改名不影响调用；缺失组件或断开连线后，页面报告流程不可用。

各部分负责的事实如下：

| 参与者 | 负责的内容 |
| --- | --- |
| 备份服务 | 备份配置、来源与排除项、仓库、凭据引用、保留策略、快照、校验结果、`status.json` 与运行记录 |
| Portal System Lineage | 定时与手动入口、Event/Effect 开关、计划、触发路径和 ScriptRun 进程生命周期 |
| Portal 后端（`resource_backup_backend.py`） | 判断当前用户的权限，从备份服务的文件中挑出允许返回的字段，组装页面概览，把页面动作映射到固定的执行任务 |
| Resource Backup 页面 | 把概览显示为卡片、列表和详情；只在浏览器中保存搜索词 |
| 用户 | 查看状态；已登录 User 和 Resident 可以手动发起备份或校验 |

## 自动计划

“资源备份”Function Block 有 5 个定时 Event，计划明确使用 Asia/Hong_Kong 时区：

| 定时 Event | 计划 | 备份服务执行的固定操作 |
| --- | --- | --- |
| 完整内容备份 | 每天 03:00 | `main.py run-once --profile content --apply` |
| 仓库校验 | 每天 03:05，随机推迟至多 15 分钟 | `main.py maintain --operation check --apply` |
| 备份保留策略 | 每天 02:15，随机推迟至多 15 分钟 | `main.py maintain --operation retention --apply` |
| 备份空间回收 | 每周日 04:15，随机推迟至多 20 分钟 | `main.py maintain --operation retention --apply --prune` |
| 深度校验 | 每周日 05:30，随机推迟至多 20 分钟 | `main.py maintain --operation deep-check --apply` |

每日完整备份使用 `misfire_policy=skip`，错过夜间窗口时等下一晚，避免白天占用 IO；维护任务使用 `coalesce_latest`，运行时故障造成的多次错过合并为一次。所有定时 Event 使用 `overlap_policy=drop`。显式关闭 Event 遵循 Lineage 的暂停语义：不触发，重新打开后从下一次计划继续，不补跑关闭期间的次数。

RunScript 共用 `exclusive_key=resource-backup`，备份服务继续使用同一排他锁保护仓库。`critical` 仅手动发起；`content`、`check`、`deep-check`、`retention` 和 `prune` 均有独立手动入口。关闭定时 Event 只停止自动启动，手动 Event 仍可使用；关闭 Effect 同时禁止两种入口执行。已启动的备份不会因关闭 Event 而中断，需要停止时通过对应 ScriptRun 的取消能力处理。

迁移后旧的五个 systemd timer 必须停止并禁用，Portal 不再调用或读取它们；正式自动调度只有 Lineage 一处。

## 页面上能看到什么

Resource Backup 页面位于 Resource 领域导航下，由工具栏、健康摘要、主体两栏和运行详情弹窗组成。

- **工具栏**：搜索框“搜索备份状态”；“校验”按钮发起仓库校验（`check`）；“关键状态”按钮发起关键状态备份（`critical`）；刷新按钮重新读取状态；主按钮“立即完整备份”发起完整内容备份（`content`）。工具栏右侧显示本页最后更新时间。深度校验没有页面按钮。
- **健康摘要**：说明总体健康状态，并用三张只读指标卡显示最近恢复点、下次自动同步时间（没有计划时显示“等待调度”）和加密仓库的已用容量与剩余空间。
- **活动运行卡**：有备份或校验正在执行时出现在主体之前，显示当前操作、开始时间和进度说明。页面一次只显示一个活动运行。
- **左栏**：“备份计划”卡片分为“自动策略”（每个档位的计划、上次成功和下次运行）和“覆盖范围”（每个档位的来源、必需来源和排除项数量）；下方“最近运行”列表显示最近的备份和校验，每行可以打开详情。
- **右栏**：“仓库状态”卡片显示目标摘要、加密、保留策略和最近校验；“需要关注”卡片列出当前提示；“恢复指南”卡片链接到固定的知识条目。
- **运行详情弹窗**：标题为“运行详情”，显示所选运行记录中允许返回的字段。关闭弹窗不影响运行。

搜索只过滤已经加载的覆盖范围和最近运行，不向服务端发请求。刷新失败时，页面保留上一次成功读取的内容并显示错误和更新时间，提醒用户这些内容可能已经过时。

## 设计点

- **所有已登录用户都能查看备份状态。** 概览中的 `can_view` 对任何已登录用户为 `true`，返回内容已经去掉仓库原始地址和凭据。
- **有效登录的 User 和 Resident 可以请求手动备份或校验，是否允许由权限系统判定。** 备份入口不另设一份管理员名单；`can_run`、`can_verify` 要求已配置备份 Lineage，且当前 actor 对该目标的 `TriggerEvent` 被当前方案允许（decision_b820faf42218、decision_600ae27b0538）。页面按概览返回的 `can_run`、`can_verify` 启用按钮，服务端在执行前再检查一次。
- **页面只能发起四种固定操作。** 备份只接受 `critical` 或 `content`，校验只接受 `check` 或 `deep-check`；每种操作映射到绑定的手动 Event，最终脚本按固定参数调用执行器，浏览器提交的值不会拼进命令。
- **已有操作在执行时拒绝新操作。** 后端发现 `status.json` 或运行记录中有未结束的运行时返回 `409`。检查和启动之间没有跨进程事务，同时到达的两个请求最终由备份服务的排他锁保证只运行一个。
- **发起成功只表示已经排队。** 接口返回 `202`，内容为 `accepted: true`、操作种类、档位或模式，以及 `status: "queued"`。备份是否成功，要看之后的概览或运行记录。
- **恢复在 Portal 之外进行。** 页面没有恢复、解锁、删除快照、修改仓库或执行任意命令的入口。“恢复指南”链接到知识条目 `information_8276cb0ac295`（Resource 系统恢复顺序），实际恢复由操作者在备份服务所在环境中选择快照、目标和影响范围后执行。

## 状态概览接口

`GET /api/resource-backup/overview` 返回 `tfi.resource-backup-overview.v1`。后端读取备份服务的 `config/config.yaml`（其中可选的 `target`、`coverage`、`policy` 段只用于显示）、`state/status.json`、`state/runs/` 下的运行记录，以及 GetLineage 中的 Event/Effect 运行投影，只输出页面需要的字段：

| 字段 | 含义 |
| --- | --- |
| `schema` | 固定为 `tfi.resource-backup-overview.v1` |
| `generated_at` | 后端生成本次概览的 UTC 时间 |
| `health` | 总体健康状态（`healthy`、`degraded`、`failed`、`running`、`setup_required`、`unavailable` 或 `unknown`）、说明、最近恢复点、最近校验时间、连续失败次数 |
| `policy` | 默认档位、每个档位的计划、上次成功和下次运行时间，以及保留与校验策略 |
| `target` | 仓库类型、位置摘要、加密、容量和可用性 |
| `coverage` | 每个档位的来源、必需来源和排除项数量与状态 |
| `active_run` | 当前正在执行的运行；没有时为空 |
| `recent_runs` | 去重并按时间排序的最近运行，最多 20 条 |
| `permissions` | `can_view`、`can_run`、`can_verify`、`can_cancel` |
| `schedule` | 六个手动操作与五个定时 Event 的绑定、可用性、暂停状态、日历、上次和下次触发时间；无法读取 Lineage 或绑定时 `readable` 为 `false`。关闭定时 Event 或 Effect 后，不显示下次自动运行时间 |
| `warnings` | “需要关注”区域的提示，例如状态文件缺失、仓库不可用、流程未配置或已关闭 |
| `restore_guide` | 恢复指南知识条目的 ID、标题和地址 |

`can_cancel` 单独试判定同一备份 Lineage 目标上的 `CancelScriptRun`，不复用 `can_run`；页面和接口目前没有取消操作。

总体健康依据 `status.json` 中的健康状态、最近成功时间和最近错误时间计算：有活动运行时为 `running`；状态文件缺失为 `setup_required`；状态文件无法读取为 `unavailable`；最近错误晚于最近成功时为 `failed`。页面直接显示后端给出的健康状态。

概览不返回仓库原始地址、密码、密钥内容、环境变量、命令行、凭据文件内容和未过滤的日志；错误只给出面向操作者的简短说明。

## 手动操作接口

| 接口 | 请求体 | 触发的手动 Event |
| --- | --- | --- |
| `POST /api/resource-backup/runs` | `{"profile":"critical"}` | 立即关键状态备份 |
| `POST /api/resource-backup/runs` | `{"profile":"content"}` | 立即完整内容备份 |
| `POST /api/resource-backup/verifications` | `{"mode":"check"}` | 立即仓库校验 |
| `POST /api/resource-backup/verifications` | `{"mode":"deep-check"}` | 立即深度校验 |

后端使用当前登录 actor 经 HATG `GetLineage` 解析绑定与状态，再调用 `TriggerEvent`。只有相应 Effect 接受执行后才返回 `202 queued`，回执包含 `lineage_id`、`event_source_id`、`event_id` 与 `trigger_path_id`。Event/Effect 关闭、连线缺失、绑定缺失或执行器未接受操作时返回 `503`。概览的计划、暂停状态与下次触发时间直接来自同一 Lineage，备份状态文件不覆盖这些计划字段。原始执行器错误不返回给浏览器。

所有接口都经过 Portal 登录会话和同源请求保护。错误含义：档位或模式无效 `400`；当前操作未获授权 `403`；已有活动运行 `409`；流程未配置、关闭或不可用 `503`；其他错误 `500`。概览接口在备份服务的文件缺失或不可读时仍返回 `200`，并在 `health` 与 `warnings` 中说明。

## 从点击到运行记录

```text
用户点击固定按钮
  -> 页面按概览中的权限和活动运行决定按钮是否可用
  -> Portal 后端检查权限和档位/模式取值
  -> 后端确认没有未结束的运行
  -> TriggerEvent 触发对应的手动 Event
  -> Effect 调用 RunScript，接受后返回 202 queued
  -> RunScript 管理脚本进程，脚本调用备份服务的固定操作
  -> 备份服务执行，更新 status.json 和运行记录
  -> 页面刷新概览，显示活动运行或最终结果
```

页面上的检查只用于及时反馈，服务端检查决定是否执行。Portal 后端不运行 restic，不读取凭据，也不计算快照内容，这些都只在备份服务中发生；备份方式改变时只需修改备份服务，Portal 只在显示字段或操作种类变化时修改。

## 刷新机制

页面通过共享读取器获取概览：普通读取在 900 毫秒内直接使用缓存，并把 1200 毫秒内的重复请求合并为一次；强制读取只合并 100 毫秒内的重复请求。首次进入、点击刷新和发起操作之后使用强制读取。有活动运行时每 3 秒刷新一次；页面隐藏时改为每 60 秒一次。

读取失败时保留上一次成功的内容。发起操作返回 `202` 后如果后续读取失败，页面只能确认操作已经排队，最终结果仍以备份服务的运行记录为准。

页面在首次读取当前登录身份之后才开始读取概览。登录账号或权限发生变化时，页面停止旧身份的刷新，清空旧的概览和详情，暂时禁用操作按钮，再用新身份重新读取；刷新时的中止信号会传给实际的概览请求，旧身份迟到的响应不会覆盖新状态。只有身份没有变化、普通刷新失败时，才保留上一次成功的内容。

## 恢复的边界

1. 恢复指南只是固定的知识入口，不说明某个快照一定可以恢复。
2. 概览中的校验结果说明最近一次检查的情况，不能代替恢复演练。
3. 实际恢复需要备份服务读取仓库和凭据，由操作者选择快照、目标和影响范围；Portal 不持有这些权限。

Home 的设备状态（decision_ed57c4ad1001）可以显示备份的一句摘要并链接到本页；备份流程在 Portal System 的“资源备份”Function Block 中查看与控制，快照、恢复点与校验事实仍以备份服务投影为准。统一资源 ID 与 ResourceService 的一般规则见 decision_4a9ba5dfada6 与 decision_b6e74d78f7b6。
