---
kb_id: decision_ed57c4ad1001
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 13
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/hatg/catalog/environment.py
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/device_identity_backend.py
  - ${code_root}/management_portal/index.html
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/tests/cases/test_agent_status_active_agents.py
  related:
  - decision_4527448ad07f
  - decision_00546e28e1e0
  supersedes: []
  derived_from: []
---
# Home GPU 与磁盘资源状态

Home 首页的 Agent Status 区域里有一个 **GPU / Disk Resources** 子面板，让用户在一处看到实验室 GPU 服务器是否空闲、各台存储的磁盘是否快满，以及磁盘快满时可以怎样处置。面板只负责展示和给出建议；启动任务、结束进程、清理文件和迁移数据都由用户在别处自行完成。

本 Design 说明面板中的可见对象、两类资源的数据从哪里来、怎样采集和缓存。面板在 Home 中的位置、折叠和关闭由共享 Workbench 负责，见 decision_00546e28e1e0。

## 页面上的对象与层级

GPU 与磁盘在同一个子面板中共享紧凑摘要行，并在其后直接呈现两类资源组；它们的数据来源和内部层级不同。

```text
GPU / Disk Resources 子面板
├─ 资源摘要行
├─ GPU 资源组
│     └─ GPU 服务器卡片
│        └─ 单张 GPU 行
│           └─ 占用显存的进程行
└─ 磁盘资源组
      └─ 监控目标卡片（NAS 或 Thor）
         ├─ 文件系统行
         ├─ 已挂载外接目标
         ├─ 轻量诊断
         └─ 惰性迁移建议
```

### GPU 区域

**GPU 摘要**显示整个 GPU 资源组的概况。`GPU: not checked` 表示尚无观测，`loading` 表示正在采集，`unavailable` 表示没有可用观测；成功时显示“可用卡数/总卡数 available”。这里的“可用”只包含 `empty` 和 `idle` 两种状态；`busy` 和遥测不足的 `unknown` 都不计入。

**GPU 服务器卡片**对应 Device Identity 设备清单中的一台 GPU 服务器。卡片标题显示设备名称，并在登记信息提供时显示 IP；右侧显示可用卡数、总卡数和采集时间。服务器是父设备，卡片内每张物理 GPU 是拥有独立 `device_*` ID 的子设备。

**单张 GPU 行**显示卡号、型号、`empty`、`idle`、`busy` 或 `unknown` 状态、计算利用率、显存使用、温度和功耗。`unknown` 表示判断可用性所需的利用率或显存占用遥测缺失；它单独计数且不计入可用卡。设备不支持独立显存总量时，页面显示 unified memory 百分比；明确支持显存遥测但本次值缺失时显示“memory telemetry unknown”，设备本身不提供相关字段时显示“memory total unsupported”。

**GPU 进程行**只列出显存占用大于零的计算或图形进程。每行显示进程 ID、显存占用、可用的运行时长和推断出的 owner。owner 优先取命令中的 run ID，再回退到 Linux 用户，仍无法识别时显示 unknown。

### 磁盘区域

**Disk 摘要**汇总当前所有文件系统。它依次表达 loading、unavailable、critical 数量、warn 数量、partial、not checked 或 ok。critical 和 warn 后面的数字是文件系统数量，不是服务器数量。

**磁盘监控目标卡片**对应 Agent Status 后端配置的一台主机或一个存储位置。当前目标为本机 NAS 和通过 SSH 读取的 Thor。每个目标有内部监控键、显示名称、连接方式和一组关注路径；监控键只在本模块内用来选择目标，与 TFI Portal 的 `resource_*` 统一资源 ID 是两套编号。

**文件系统行**对应一次容量观测中的一个实际卷。它显示文件系统或卷名称、挂载点、文件系统类型、可用容量、已用百分比、状态文字和进度条。多个关注路径落在同一卷时合并为一行，同时保留全部路径和挂载点。

**外接目标**列出已经挂载且适合作为迁移目标的设备，并显示型号、标签、挂载点和可用容量。**轻量诊断**列出已删除但仍被进程占用的文件、systemd journal、容器存储和大文件检查的发现。**惰性迁移建议**根据高占用文件系统与外接目标给出人工处置顺序；“惰性”表示优先迁移历史产物、日志和缓存，保留正在运行的工作目录。

两类详情始终直接显示在子面板内，没有单独的展开或收起控件。面板上的所有内容都是只读的。

## 设计点

- **资源面板首次显示时才读取详情，反复开合不重复初始化。**
- **资源面板可见时，Agent Status 的主 Refresh 按钮同时强制刷新 GPU 和磁盘。** 两个请求并行发出，GPU 的远程探测不拖慢磁盘请求。磁盘请求立即返回当前快照与采集进度，已有容量行继续显示；GPU 采集期间显示 loading。刷新期间另一类资源和 Agent Status 的其他面板照常可用。资源面板隐藏时，主 Refresh 只刷新其他面板。
- **Home 只按整组刷新资源。** 页面上没有单台服务器或单个磁盘目标的刷新按钮；读取单个目标的能力只在指令接口上提供（见“接口”一节）。
- **GPU 摘要把 empty 与 idle 都计为可用。** `empty` 表示利用率和显存比例都低于空闲阈值；`busy` 表示任一指标高于繁忙阈值；两者之间为 `idle`。默认空闲阈值均为 5%，默认繁忙阈值均为 50%。
- **GPU 关键遥测缺失归入 unknown。** 状态分类同时需要计算利用率和可比较的显存占用比例；任一输入缺失时不以零补值，不归入 empty、idle 或 busy。unknown 单独汇总并从 available 数量中排除，防止采集失败被展示成空闲资源。
- **GPU 详情按“服务器 → 单卡 → 进程”分层呈现。** 服务器错误留在自己的卡片；其他服务器和单卡仍然显示。跨服务器结果混合成功与失败时，组状态为 partial。
- **GPU 采集结果必须与设备登记一致。** 采集器逐项核对单卡序号（index）、UUID 和服务器登记的卡数，核对失败的服务器显示错误。按 device ID 读取时，ID 必须精确匹配登记记录；找不到时返回错误和全部已知 ID。
- **缺失的 GPU 指标显示为缺失。** 显存总量、利用率、温度、功耗和进程各自独立判断；某项缺失时页面显示缺失说明，数值位置留空，零只表示设备确实报告了零。
- **磁盘摘要按最高风险决定。** 每个文件系统按已用比例计算 ok、warn 或 critical；监控目标取其文件系统最高级别，整个资源组再汇总所有目标。当前默认 warn 为 85%，critical 为 92%。
- **同一文件系统只显示一份容量。** 多个关注路径指向同一文件系统时共用一个容量进度条，保留这些路径和挂载点，避免把同一容量重复计算为多个磁盘。
<!-- wall: {"key":"disk-capacity-first","importance":8} -->
- **磁盘容量先发布，诊断按段补充。** 每个目标先发布 df 容量和容量风险，再发布外接目标、已删除仍占用文件、系统日志、容器存储、大文件扫描和迁移建议。各段拥有独立的等待、执行、成功、部分成功、失败或未执行状态与时间；诊断失败保留已成功取得的容量及其他诊断发现。页面持续读取快照呈现进度，不等全部诊断结束才显示容量，也不把尚未执行的检查描述为“未发现问题”。
- **主 Refresh 为 Disk 请求按需诊断。** 新的强制刷新周期在容量和挂载信息之后依次运行 deleted-open、journal、容器存储和本地大文件检查。远程目标的大文件阶段标记为 skipped，并说明远程扫描未启用。已有采集周期运行时，同目标请求复用该周期，不并发启动重复检查。
- **磁盘诊断保持有界。** 每个已配置目标同一时刻只运行一个采集周期，各目标独立推进。每段检查沿用受控命令超时并独立显示错误；每类 finding 最多显示 5 条明细，并最多为 3 个高占用文件系统生成迁移建议。标题中的 finding 数量仍保留完整计数。
- **外接目标决定建议内容。** 外接目标采集完成后，已发现已挂载外接盘时建议优先把历史内容惰性迁移到该目标；成功采集但未发现目标时建议先挂载设备。外接目标观测失败或不完整时，迁移建议标记为 skipped，页面不把采集错误解释为“没有外接盘”。建议只供用户判断，不自动执行。
- **一个目标出错时，同组其他目标照常采集和显示。** 连接失败、命令不可用、返回格式异常或阈值计算失败时，错误只显示在对应的服务器或磁盘目标上。
- **强制刷新失败时继续显示上一次的结果，并标明刷新失败。** 有旧结果时，页面保留旧详情并带上 `stale`（结果已过时）和 `refresh_error`（最近一次失败原因）两个标记；没有旧结果时，该目标显示为 error。
- **资源内容没有独立开合状态。** GPU / Disk Resources 面板的位置、分组、折叠和关闭状态由 Home Workbench 保存；GPU 与 Disk 详情本身始终随面板内容一起渲染。

## 首次读取与重复开合

Home 运行器根据当前区域及父区域的 Workbench 可见性，决定何时连接资源组件。隐藏、折叠或位于未选中页签时，不启动 GPU、磁盘详情请求或磁盘刷新协调器；首次展开后分别普通读取两类快照，均不带 `refresh` 参数。

已有账号摘要中的资源观测仍可作为展示缓存，缓存缺失或过期时怎样处理由采集后端决定。再次开合保留已连接实例及内容，不重复首次读取。统一的首次连接规则见 decision_00546e28e1e0。

## 两套资源身份从哪里来

**Device Identity** 是 Portal 的设备身份目录。GPU 路径从这里读取服务器父设备和 GPU 子设备的稳定 `device_*` ID、位置、连接方式、预期卡数、卡号和 UUID。采集器依据这些登记信息选择受控脚本与 SSH 目标。

**磁盘监控池**是 Agent Status 后端当前配置的容量检查目标。当前包含：

| 内部监控键 | 页面名称 | 连接方式 | 关注路径 |
|---|---|---|---|
| `nas_storage` | NAS | Portal 主机本地读取 | 站点 `portal_paths` 的 `share_root`、`staging_root`、`instances_root` |
| `thor_storage` | Thor | 通过登记的 SSH 配置远程读取 | `/home`、`/srv`、`/tmp` |

两套来源分工明确：GPU 设备身份和每台服务器最近一次观测保存在 Device Identity；磁盘监控目标、关注路径和容量缓存保存在 Agent Status 后端。页面直接使用各自来源给出的身份，磁盘监控键只用于本模块，设备身份只来自 Device Identity 登记。

## 采集、缓存与恢复

### 组件职责

| 组件 | 责任 |
|---|---|
| Device Identity 后端 | 保存 GPU 父子设备身份及最近服务器观测 |
| GPU 采集器 | 调用受控状态脚本，校验服务器与单卡身份，归一化遥测和进程 |
| 磁盘采集器 | 对监控路径执行本地或远程容量查询，识别挂载目标并按需诊断 |
| Agent Status 缓存 | 按资源和采集成本保存最近结果、时间与错误 |
| Home 资源控制器 | 在独立子面板中直接绘制摘要与详情，响应主 Refresh，并在失败时保留已有列表 |

### GPU 工作流

```text
Device Identity 的 GPU 服务器与单卡登记
              ↓
普通读取 → 最近服务器观测，缺失时返回 not_checked
主 Refresh → 受控脚本与 SSH 探测
              ↓
校验服务器、卡数、index 与 UUID
              ↓
归一化 empty / idle / busy、遥测与进程
              ↓
保存服务器观测 → 汇总资源组 → Home
```

GPU 结果默认缓存 60 秒。普通 Agent Status 读取和资源面板首次可见时的普通 GPU 读取使用 Device Identity 中的最近观测；资源面板可见时，Home 主 Refresh 会强制探测并绕过普通缓存读取。采集失败的影响限制在目标服务器，成功观测继续成为该服务器下一次普通读取的来源。

### 磁盘工作流

```text
磁盘监控目标与关注路径
          ↓
按目标合并刷新请求 → 立即返回旧容量或等待状态
          ↓
本地或远程 df → 按实际卷合并 → 发布容量快照
          ↓
lsblk → 发布外接目标结果
          ↓
按需逐项诊断 → 每项结束后发布发现与阶段状态
          ↓
迁移建议 → 标记本轮完成

每次发布 → 同一目标缓存 → 资源组快照 → Home
```

磁盘结果默认缓存 300 秒。普通读取遇到有效缓存时立即返回；缓存缺失或过期时安排只含容量、外接目标和建议的后台刷新，按需诊断阶段标记为 skipped。带 `refresh: true` 的请求发起包含按需诊断的后台周期，并立即返回当前快照；同目标已有周期运行时合并请求，轮询只读取快照。进程恢复后，如果缓存仍标记采集中而没有对应运行任务，下一次读取会重新安排轻量采集，避免阶段永久停留在等待状态。

每个目标的 `updated_at` 表示容量观测时间，诊断完成不把旧容量时间更新为新时间。容量采集失败时，有旧容量则保留它与原时间并标记 `stale`、`refresh_error`；无旧容量则为 error。依赖容量的后续阶段标记为 skipped。容量已成功时，后续阶段错误只保存在自己的阶段状态，不清空容量、不改变其风险等级，也不阻止其他独立诊断。

Home 的磁盘控制器在资源面板首次可见连接后独立读取磁盘接口：采集中每秒读取一次，完成后回到 300 秒间隔，浏览器页面隐藏时暂停，组件销毁时停止。面板再次折叠保留已经建立的控制器，折叠本身不等同于浏览器页面隐藏。后续 Agent Status 摘要不会覆盖磁盘控制器已接收的分段结果。磁盘控制器轮询时只读取快照，不带 `refresh` 参数；初始旧容量与更新中状态可以同时显示。缓存时间、GPU 状态阈值和磁盘告警阈值均可由 Portal 环境配置调整。

## 接口

Home 通过 Portal 的 HATG 指令接口读取两类资源。HATG 指令是 Portal 统一的“指令名 + 参数”调用方式，页面和 Agent 使用同一组指令（见 decision_4527448ad07f）。两类资源都由 `GetDeviceStatus` 读取，`kind` 选择资源类型：

| `kind` | 其他参数 | 用途 |
|---|---|---|
| `gpu` | `refresh`（可选，true 表示强制采集）、`device_id`（可选，只读一台服务器或一张卡） | 读取 GPU 资源组或单个 GPU 设备 |
| `disk` | `refresh`（可选）、`device_id`（可选，填磁盘监控键，如 `nas_storage`） | 读取磁盘资源组或单个监控目标 |

资源面板首次可见时，Home 分别以 `kind: "gpu"` 和 `kind: "disk"` 调用一次，不带其他参数；面板可见时按下主 Refresh，Home 带 `refresh: true` 对两类资源再各调用一次。按 `device_id` 读取单个目标的能力 Home 没有使用。

两类结果都把设备列表放在 `devices` 中，另带该类资源的汇总字段。`kind: "gpu"` 读取整组时返回 `status`、`gpu_count`、`available_gpu_count`、`busy_gpu_count`、`unknown_gpu_count`、`empty_gpu_count`、`idle_gpu_count`、`servers`（每台服务器及其 `gpus`）、扁平的 `gpus`、`updated_at` 和 `ttl_seconds`（缓存秒数）。`devices` 是供 Agent 使用的统一列表：整组读取时等于 `servers`，按 `device_id` 读取时只含所选设备，ID 未知时为空。

`kind: "disk"` 读取整组时返回 `status`、`resources`（每个监控目标）、扁平的 `filesystems`、`transfer_targets`、`suggestions`、`triage`、`updated_at`、`ttl_seconds` 和 `refreshing`；组级 `refreshing` 表示仍有目标正在采集。`devices` 与 `resources` 内容相同，供 Agent 使用。每个目标保存自己的文件系统、外接目标、建议、`triage`、`refreshing` 和 `stages`。

`stages` 记录一个磁盘目标本轮采集中各段的进度。段名为 `capacity`（容量）、`transfer_targets`（外接目标）、`deleted_open_files`（已删除仍占用的文件）、`journal_usage`（systemd 日志）、`docker_usage`（容器存储）、`large_files`（大文件）和 `suggestions`（迁移建议）。每段状态取 `pending`（等待）、`running`（执行中）、`ok`、`partial`（部分成功）、`error` 或 `skipped`（未执行），并带适用的开始时间、完成时间和错误或跳过原因。组级 `partial` 表示组内同时有成功结果和错误。
