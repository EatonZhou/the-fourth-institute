---
kb_id: decision_37f98f8fcb4b
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 14
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:35:24Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/notifications/realtime_lifecycle.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/http_portal_session.py
  - ${code_root}/management_portal/management-portal.service
  - ${code_root}/systemd/management-portal-routed.conf
  - ${code_root}/systemd/management-portal-release.service
  - ${code_root}/scripts/portal_services.py
  - ${code_root}/scripts/restart_portal_with_notice.py
  - ${code_root}/scripts/restart_portal_release.py
  - ${code_root}/scripts/portal_restart_notice_client.py
  - ${code_root}/management_common/service_shutdown.py
  - ${code_root}/management_portal/society_service/lineage_program_polling.py
  - ${code_root}/management_portal/society_service/lineage_program_terminal_reconciliation.py
  - ${code_root}/management_portal/society_service/coordinator.py
  - ${code_root}/tests/cases/test_service_shutdown.py
  - ${code_root}/tests/cases/test_portal_asgi.py
  - ${code_root}/tests/cases/test_portal_services.py
  related:
  - decision_850e6248d680
  - decision_6e230393221c
  - decision_9f6135cbbb22
  - decision_99bb5dc0f96b
  - decision_f25266e5a0e7
  - decision_075f66430606
  - decision_08bb2d9141f9
  supersedes: []
  derived_from: []
---
# Portal Web 重启通知与服务恢复编排

Portal 在发布新代码、Dev 加载源码修改或运维修复时，需要重启网页进程和后台服务。正在使用页面的人应当提前知道即将发生的重启，页面在重启后应当自动恢复。本 Design 说明一次由控制面发起的重启怎样先向浏览器发出倒计时通知，再断开实时连接、替换网页进程，并按依赖顺序重启后台组件；也说明管理员怎样从页面安排重启通知。

浏览器怎样显示倒计时、探测新进程并重新加载页面见 `decision_f25266e5a0e7`；组件生命周期公告的文件格式见 `decision_99bb5dc0f96b`；重启命令、组件名和验收条件见 `decision_850e6248d680`；ExecutionWorker 换代时怎样交接运行中的任务见 `decision_075f66430606`。

## 基本概念

- **Web**：向浏览器提供页面、HTTP API 和实时连接的网页进程。Dev 的 Web 是 `management-portal.service`（端口 18086），Release 的 Web 是 `management-portal-release.service`（端口 18085）。
- **受控重启**：由服务控制面（`./scripts/portal_services restart`，以及调用它的发布工具和 Dev 源码重载器）发起的重启。控制面事先知道 Web 即将停止，因此可以先通知用户。进程崩溃、断电或网络中断没有这个提前窗口，浏览器只能靠恢复探测发现。
- **重启通知**：一条短期记录，说明重启计划在什么时间发生、显示什么提示。Web 把它保存在文件中，并立即推送给已经打开页面的浏览器；浏览器也可以通过 `GET /api/restart-notice` 读取。
- **实时连接**：浏览器与 Web 之间保持打开的长连接，Web 通过它把页面数据变化和通知推送给浏览器。
- **实时连接生命周期**（`RealtimeConnectionLifecycle`）：Web 进程内的一个对象，记录当前是否接受新的实时连接以及有哪些连接处于打开状态。
- **排空**：Web 停止接受新的实时连接，关闭已有连接，并等待打开的连接数降到零。浏览器看到连接被关闭后进入恢复流程；这不表示用户的业务操作失败。
- **Web 启动时间**（`server_started_at`）：当前 Web 进程启动的时间，用于区分通知属于重启前还是重启后的进程。
- **组件生命周期公告**：控制面为 ResourceService 和 SocietyService 写下的最近状态（`restarting`、`ready`、`unavailable`），页面据此区分计划内重启和意外断线。

## 设计点

- **受控重启先向浏览器发出 5 秒倒计时通知。** 通知发出后，旧 Web 在倒计时内照常处理请求，用户可以保存或结束当前操作。Dev 和 Release 的倒计时都固定为 5 秒。
- **倒计时结束后，Web 先排空实时连接，再由 systemd 替换进程。** 排空结束时所有浏览器的实时连接都已关闭，浏览器随即开始等待新 Web 上线。
- **通知或排空失败时，重启照常进行。** 需要重启的 Web 可能已经无法响应，重启本身正是修复手段；这时控制面记录失败原因，跳过倒计时，直接重启。浏览器由恢复探测发现进程变化。
- **新 Web 不会重播重启前的通知。** 通知只在计划重启时间晚于当前 Web 启动时间时有效，因此新进程读到同一个通知文件时，会把它视为已经完成的重启。
- **Portal 管理员可以从页面安排重启通知。** 管理员可以设置 3 到 300 秒的倒计时和提示文字，并选择是否在倒计时结束时由 Web 自己重启所在的服务。
- **后台组件在 Web 切换之后按依赖顺序重启。** ResourceService 和 SocietyService 同时重启时，先停止 SocietyService，等 ResourceService 重启并恢复健康后，再启动 SocietyService；只重启 SocietyService 时，也先确认 ResourceService 健康。ExecutionWorker 通过重新加载完成换代。
- **ResourceService 和 SocietyService 的每次重启都有公告。** 重启开始时公告 `restarting`，验收通过后公告 `ready`，任何一步失败时公告 `unavailable`。页面的服务状态接口据此向用户显示“正在重启”或“暂时不可用”。
- **重启失败时，控制面报告错误并保持当前代码。** 普通重启不自动换回旧代码；需要撤销一次 Release 更新时，在 Dev 中修正或撤销改动后再发布（见 `decision_08bb2d9141f9`）。

## 重启通知

### 通知的保存与有效条件

Web 把当前通知保存在一个 JSON 文件中：Dev 为 Dev 工作树中的 `management_portal/restart_notice.json`，Release 为 `<实例根目录>/release/restart_notice.json`（由环境变量 `PORTAL_RESTART_NOTICE_PATH` 指定）。每次写入都整体替换文件，最后一次写入的通知生效。文件中的字段为 `notice_id`（16 位十六进制随机数）、`message`、`start_at`（发出时间）、`restart_at`（计划重启时间）、`expires_at`（`restart_at` 之后 20 秒）、`restart_requested`、`scheduled_by` 和 `created_at`。

通知同时满足以下条件时有效：有 `notice_id`；`restart_at` 晚于当前 Web 启动时间；当前时间早于 `restart_at`；当前时间不晚于 `expires_at`。

### 浏览器读取当前通知

`GET /api/restart-notice` 无需登录，登录页和正在恢复会话的页面也能读取，始终返回 `200`。没有有效通知时，响应为 `active: false`、`now`（Web 当前时间）和 `server_started_at`。有效时还包含：

| 字段 | 含义 |
| --- | --- |
| `notice_id` | 本次通知的标识 |
| `message` | 显示给用户的提示；为空时使用“系统将在倒计时结束后重启。” |
| `start_at`、`restart_at`、`expires_at` | 发出时间、计划重启时间和最晚有效时间（Unix 秒） |
| `restart_requested` | 本次通知是否要求 Web 在倒计时结束时自己重启 |
| `scheduled_by` | 安排通知的用户名；控制面发出的通知为 `local-restart` |
| `now`、`server_started_at` | 浏览器用来校正倒计时和识别新进程 |

通知写入后，Web 还会把同样的内容作为一条页面数据变化推送给已经订阅的浏览器。

### 管理员安排通知

`POST /api/restart-notice` 需要有效的 Portal 会话，并且用户具有 Portal 管理权限。请求体字段：

- `seconds`：倒计时秒数，转换为整数后限制在 3 到 300 之间，缺省为 5；无法转换为整数时返回 `400`。
- `message`：提示文字，去掉首尾空白后最多保留 240 个字符。
- `restart`：为真时，Web 启动一个后台线程，在倒计时结束后执行 `systemctl --user restart` 重启自己所在的服务（服务名来自环境变量 `PORTAL_SERVICE_NAME`，Dev 缺省为 `management-portal.service`，Release 为 `management-portal-release.service`）。

成功时返回 `200` 和当前通知内容；没有管理权限返回 `403`，其他失败返回 `500`。接口不提供版本号或幂等键，多个管理员同时安排通知时，最后一次写入生效。

### 控制面使用的本机接口

控制面通过 Web 提供的两个本机接口发通知和排空。两个接口都只接受直接来自 `127.0.0.1` 或 `::1`、且不带 `X-Forwarded-For` 头的 `POST` 请求，其他来源返回 `403`。

**`/api/internal/restart-notice`** 发布通知。它要求实时连接生命周期仍在接受连接、推送通道可用，否则返回 `503`。请求体必须是 JSON 对象（`seconds`、`message`，客户端另附 `services`），否则返回 `400`。通知以 `scheduled_by=local-restart`、`restart_requested=false` 写入。成功响应为 `ok: true`、`projection_published: true`、`subscriber_count`（当前订阅推送的连接数，仅供观察）和 `notice`。

**`/api/internal/drain-realtime`** 排空实时连接。Web 停止接受新连接，关闭推送通道，然后最多等待 2 秒让打开的连接全部释放。全部释放时返回 `200`：`ok: true`、`drained: true`、`started_realtime_connections`（开始时的连接数）、`closed_realtime_connections`、`remaining_realtime_connections`（为 0）和 `elapsed_seconds`；超时返回 `503`，附同样的计数和 `drained: false`。

控制面的客户端 `scripts/portal_restart_notice_client.py` 负责校验两个响应。发通知最多等 3 秒，只有 `ok`、`projection_published` 为真且返回的通知有效才算成功；排空最多等 4 秒，只有 `ok`、`drained` 为真且剩余连接数为 0 才算成功。

## 一次受控重启的过程

Release 的 ISA 重启请求由独立的 `portal-restart-<id>.service` 执行；Web 退出后该单元继续完成后台组件编排和健康验收。服务控制面在执行前检查维护栅栏，维护中的实例保持停止状态（decision_850e6248d680）。

```text
portal_services restart <实例> [--component ...]
  │
  ├─ 选中 web：
  │    旧 Web 发布通知 ──> 浏览器显示 5 秒倒计时
  │    等待 5 秒
  │    旧 Web 排空实时连接 ──> 浏览器进入恢复等待
  │    systemd 停止旧 Web、启动新 Web
  │
  ├─ 后台组件按依赖重启，并公告 restarting
  │    （ResourceService → 等待健康 → SocietyService；ExecutionService；ExecutionWorker 重新加载）
  │
  ├─ 等待实例验收（最多 90 秒）
  │
  └─ 公告 ready；任何一步失败则公告 unavailable 并返回错误
```

### Dev 与 Release 的 Web 怎样执行通知和排空

两个实例在同一时间点做同样的事，接入方式不同：

- **Dev**：控制面运行 `scripts/restart_portal_with_notice.py`，它把提示文字写入 `management_portal/restart_notice_request.json` 后执行 `systemctl --user restart management-portal.service`。systemd 停止 Web 时运行 unit 的 `ExecStop`，即同一脚本的 `--no-restart --sleep` 模式：读取并删除请求文件（超过 120 秒的请求被忽略，改用默认提示），通过本机接口发通知；通知成功才等待 5 秒；随后排空。任一步失败都记录“continuing shutdown”并以成功退出，systemd 继续停止进程。因为 `ExecStop` 在每次停止 Dev Web 时都会运行，任何方式停止 Dev Web 都会先给浏览器倒计时。
- **Release**：控制面运行 `scripts/restart_portal_release.py`，它先通过本机接口发通知、等待 5 秒、排空，再执行 `systemctl --user restart management-portal-release.service`。通知或排空失败时，脚本记录原因，跳过剩余的等待，直接重启。携带 `--commit` 的发布沿用此通知和排空入口；停服后的分支快进、manifest 一致性及切换失败处理以 `decision_08bb2d9141f9` 为准。

两个 Web 都由 Uvicorn 运行，停止时最多用 15 秒完成正在处理的请求（`--timeout-graceful-shutdown 15`）。

### 后台组件的重启顺序

Web 切换完成后（未选中 Web 时直接从这里开始），控制面按以下顺序处理选中的组件：

1. 如果同时选中 `resource` 和 `lineage`：公告 SocietyService `restarting`，停止 SocietyService。
2. 依次处理 `resource`、`execution-service`、`lineage` 中被选中的组件：
   - ResourceService 和 SocietyService 先公告 `restarting`（已在第 1 步公告的不重复）。
   - 只选中 `lineage` 时，先等待 ResourceService 健康（最多 30 秒）。
   - 重启组件；第 1 步中停止的 SocietyService 此时直接启动。
   - ResourceService 重启后，等待它健康（最多 30 秒）再继续。
3. 选中 `execution-worker` 时，Worker 管理进程正在运行就让它重新加载（由它完成新旧 Worker 的交接），没有运行就启动它。
4. 等待实例验收（条件见 `decision_850e6248d680`）。
5. 验收通过，为选中的 ResourceService 和 SocietyService 公告 `ready`；Web 切换或第 1 到 4 步中任何一步出错，为它们公告 `unavailable`，并把错误返回调用方。
6. 在 Dev 上重启的共享组件（`resource`、`execution-service`、`lineage`、`execution-worker`），接着在 Release 上按同样的步骤重启。

同一次重启中，每个组件的所有公告使用同一个操作 ID（形如 `lineage-restart-<uuid>`），页面据此把 `restarting` 和随后的 `ready` 对应起来。

### 后台组件怎样停止

SocietyService 和 ResourceService 收到 SIGTERM 后，先关闭监听 socket 并结束正在等待的 Watch 流，再同时通知各个后台工作停止，共用 8 秒等待它们退出。整个停止过程最多 10 秒：到时进程直接退出，打印所有线程的调用栈，未提交的事务随进程回滚，已提交的任务、回执和租约由新进程按原有恢复协议接手。

新进程每次启动都会做一轮全量对账，逐个检查每个 Lineage 的事件来源与运行状态，并修复各 Lineage 中 Resident 完成与 Resident 调用的记录。停止开始后，这一轮在两个 Lineage 之间、单个 Lineage 内两个节点或两次 Resident 调用之间结束，跳过收尾清理，剩下的交给下一轮。修复的每一步都按 Message ID 观察和提交，下一轮从停下的地方接着做。协调器停止时，排队但尚未开始的工作直接取消，不计入等待；正在等待 Resident 回合结束的任务运行在后台线程中，也不会拖住进程退出。

停止超过 3 秒时，进程先打印一次所有线程的调用栈（“Service shutdown still waiting”），到 10 秒上限时再打印一次。排查慢停止时，从 `journalctl --user -u portal-<dev|release>-society-service` 中这两段输出找到停止在等待的线程。

## 故障与恢复

| 发生的问题 | 调用方和用户看到什么 | 恢复方式 |
| --- | --- | --- |
| 管理员请求没有权限或倒计时不是整数 | `403` 或 `400`，没有安排通知 | 修正后重新提交 |
| 旧 Web 无法发布通知（进程异常或实时推送不可用） | 控制面日志记录失败；浏览器没有倒计时 | 重启继续；浏览器通过恢复探测发现新进程 |
| 排空超时 | 控制面日志记录剩余连接数 | 重启继续；Uvicorn 在 15 秒内结束剩余请求，残留连接随进程退出断开 |
| systemd 启动失败或组件健康检查超时 | 控制面返回错误，相关组件公告 `unavailable` | 运维诊断后重新执行重启；需要撤销 Release 更新时修正后再发布 |
| 进程突然崩溃 | 没有通知 | 浏览器直接进入恢复探测；运维重新启动并验收 |

重启通知、组件生命周期公告和实时健康检查回答三个不同的问题：通知说明“即将重启”，公告说明“控制面最近一次宣布的组件状态”，健康检查说明“组件此刻能否响应”。页面恢复后能看到哪些内容，取决于各页面自己保存的状态；`decision_9f6135cbbb22` 说明重新加载后怎样恢复已保存的页面偏好，未保存的表单和草稿由各页面负责。


## 密集重启请求的合并

`portal_services request-restart dev` 的待处理请求合并、修订号确认和失败重试由 `decision_850e6248d680` 的“源码重载器”规则定义。请求合并窗口与本篇的浏览器停止倒计时分别计时；进入实际重启后，按本篇执行通知、排空和组件恢复。

Release 发布的批次选择、已覆盖且健康时跳过重复切换的条件、健康检查与返回值见 `decision_08bb2d9141f9`；需要实际重启时，执行本篇的通知、排空和验收流程。