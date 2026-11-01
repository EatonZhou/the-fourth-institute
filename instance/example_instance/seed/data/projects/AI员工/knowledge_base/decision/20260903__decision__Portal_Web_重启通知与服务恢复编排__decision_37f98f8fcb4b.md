---
kb_id: decision_37f98f8fcb4b
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 17
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-31T18:14:27Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/web_server.py
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/notifications/realtime_lifecycle.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/static/portal_events.js
  - ${code_root}/management_portal/static/portal_theme.js
  - ${code_root}/management_portal/portal_deployment_router.py
  - ${code_root}/scripts/portal_services.py
  - ${code_root}/scripts/tfi_development_runtime.py
  - ${code_root}/scripts/tfi_release_install.py
  - ${code_root}/management_common/service_shutdown.py
  - ${code_root}/management_portal/society_service/lineage_program_polling.py
  - ${code_root}/management_portal/society_service/lineage_program_terminal_reconciliation.py
  - ${code_root}/management_portal/society_service/coordinator.py
  - ${code_root}/tests/cases/test_service_shutdown.py
  - ${code_root}/tests/cases/test_portal_asgi.py
  - ${code_root}/tests/cases/test_web_server.py
  - ${code_root}/tests/cases/test_portal_services.py
  - ${code_root}/tests/cases/test_development_runtime.py
  related:
  - decision_ab9880dc4c9d
  - decision_6e230393221c
  - decision_9f6135cbbb22
  - decision_99bb5dc0f96b
  - decision_f25266e5a0e7
  - decision_075f66430606
  - decision_08bb2d9141f9
  supersedes: []
  derived_from: []
---
# Portal Web 重启衔接与服务恢复编排

Portal 在发布新代码、Dev 加载源码修改或运维修复时，需要重启网页进程和后台服务。正在使用页面的人应当在重启后自动回到可用的页面。本 Design 说明一次由服务控制面发起的重启怎样替换网页进程、按组件重启后台服务，浏览器怎样发现新进程并重新加载，后台组件停止时怎样把未完成的工作交给新进程。

浏览器怎样探测新进程并重新加载页面见 `decision_f25266e5a0e7`；服务健康、构建标识和激活记录见 `decision_99bb5dc0f96b`；重启与发布命令见 `decision_ab9880dc4c9d`，发布流程见 `decision_08bb2d9141f9`；ExecutionWorker 换代时怎样交接运行中的任务见 `decision_075f66430606`。

## 基本概念

- **Web**：向浏览器提供页面、HTTP API 和实时连接的网页进程。Dev 的 Web 是 `<前缀>-dev-web.service`，Release 的 Web 是 `<前缀>-web.service`，用户入口 `<前缀>-router.service` 按用户的选择把请求转给其中之一。
- **受控重启**：由配对运行时 `scripts/tfi_development_runtime.py` 执行的重启。`./scripts/portal_services restart`、`./scripts/portal_services request-restart` 和 ISA `RestartPortal` 使用它的按组件重启，`tfi_dev.py publish` 使用它的发布激活。控制面知道哪些单元被重启，并在重启后检查 Web 健康。进程崩溃、断电或网络中断不经过控制面，浏览器靠同样的恢复探测发现新进程。
- **实时连接**：浏览器与 Web 之间保持打开的事件流（`/api/events`），Web 通过它把页面数据变化和通知推送给浏览器。
- **实时连接开关**（`RealtimeConnectionLifecycle`）：Web 进程内的一个对象，记录当前是否接受新的实时连接，并持有一个结束信号。Web 停止时开关关闭、信号发出。
- **Web 启动时间**（`server_started_at`）：当前 Web 进程启动的时间。每条实时连接的第一个事件 `portal-ready` 都带着它，浏览器用它识别新进程。

## 设计点

- **受控重启直接替换进程，不向浏览器预告。** 配对运行时对选中的单元执行 `systemctl --user restart`；页面在事件流重连到新进程时重新加载。
- **Web 收到停止信号就结束实时连接，其他请求最多再用 15 秒。** 实时连接不会自己结束，等它们只会让停止中的进程空等；普通请求到 15 秒仍未结束时被取消。
- **页面只在连上更新的 Web 进程时重新加载。** 依据是 `portal-ready` 中的 `server_started_at` 大于页面第一次连上时记下的值；同一进程的重连、网络抖动不触发重新加载。
- **组件按调用方给出的顺序逐个重启，组件之间不等待健康。** 按组件重启的缺省顺序为 Web、ResourceService、ExecutionService、SocietyService，全部处理后检查该实例 Web 的健康。发布激活按固定顺序处理受影响的组件，最后检查 Dev、Release 和用户入口三处健康。
- **ExecutionWorker 通过信号换代。** 按组件重启 ExecutionWorker 时发 USR1，即使构建标识相同也替换当前 Worker；发布激活和 `--generation-only` 发 HUP，构建标识未变时保留当前 Worker。
- **重启任务在独立的临时单元中执行。** `request-restart` 和 `RestartPortal` 在 `tfi-pair-restart-<uuid>` 单元中运行，发布激活在 `tfi-pair-publish-<uuid>` 单元中运行。发起请求的 Web 被重启后，这些单元继续完成剩余组件和健康检查。
- **重启失败时，控制面报告错误并保持当前代码。** 普通重启不自动换回旧代码；需要撤销一次 Release 更新时，在 Dev 中修正或撤销改动后再发布（见 `decision_08bb2d9141f9`）。

## 一次受控重启的过程

按组件重启由配对运行时的 `restart` 完成。`./scripts/portal_services restart <dev|release>` 在命令进程中执行，并持有该实例的 `portal-services.lock`；`./scripts/portal_services request-restart dev` 和 ISA `RestartPortal` 先校验组件名，再用 `systemd-run --user` 启动 `tfi-pair-restart-<uuid>` 单元执行同一过程，立即返回请求 ID。独立 TFI 实例没有配对运行时，这些入口返回错误，整站服务由 `install_tfi_release` 管理。

```text
portal_services restart <实例> [--component ...]
  │
  ├─ 按组件顺序逐个处理（缺省 web → resource → execution-service → lineage）
  │    web：systemctl --user restart 该实例的 Web 单元
  │         旧 Web 立即结束实时连接，其余请求最多再用 15 秒
  │         浏览器重连新 Web，portal-ready 中的 server_started_at 变大，页面重新加载
  │    resource / execution-service / lineage：systemctl --user restart 共享单元
  │    execution-worker：发 USR1，等待新 Worker（--generation-only 时发 HUP）
  │
  ├─ 检查该实例 Web 的 /api/health（每秒一次，最多 90 次）
  │
  └─ 返回 ok 与 Web 健康结果；任何一步失败则返回错误，后续组件不再处理
```

### Web 怎样停止

Dev Web 和 Release Web 都由 Portal 的入口 `python -m management_portal.web_server` 启动，它用 Uvicorn 运行 ASGI 应用，优雅关闭时限 15 秒。收到停止信号时：

1. 入口立即结束实时连接：实时连接开关关闭，之后到达的 `/api/events` 返回 503；结束信号和推送通道的关闭让每条打开的事件流马上结束。
2. Uvicorn 停止接受新连接，等待其余进行中的请求结束；到 15 秒仍未结束的任务被取消，日志记录“Cancel N running task(s)”。
3. Web 的 lifespan 收尾，停止后台功能，关闭线程池和 NATS 连接（见 decision_ecf0f2b2de28）。

实时连接不会自己结束，第 1 步让 Web 不必为它们等满 15 秒。本站 2026-10-27 实测，有页面连着时 Release Web 停止用时 0.8 秒，Dev Web 1.5 秒。

浏览器的实时连接断开后自动重连。每条新连接的第一个事件 `portal-ready` 带着该 Web 进程的 `server_started_at`；`portal_theme.js` 记下页面第一次连上时的值，之后某次连接带来的值更大时，页面重新加载当前地址（登录页除外），换用新进程的页面资源。Web 重启期间，用户入口路由器连不上后端时最多等待 90 秒再转发，仍不可用时返回一个每 5 秒自动刷新的提示页。

### 后台组件的重启顺序

`restart` 把组件名映射为单元：`web` 对应该实例的 Web 单元，`resource`、`execution-service`、`lineage`、`execution-worker` 依次对应 `<前缀>-resource`、`<前缀>-execution`、`<前缀>-society`、`<前缀>-worker`。后台组件由 Dev 和 Release 共用，从 Dev 工作树运行，所以对任一实例重启后台组件，重启的都是同一组单元。缺省组件为 `web`、`resource`、`execution-service`、`lineage`，不含 ExecutionWorker。

组件按调用方给出的顺序逐个处理，中间不等待健康检查：

- Web、ResourceService、ExecutionService 和 SocietyService 执行 `systemctl --user restart`。
- ExecutionWorker 的管理进程收到 USR1 后用新 Worker 替换当前 Worker，构建标识相同也替换；控制面等待 `runtime/worker-health.json` 出现新的 `worker_id`，最多 270 秒。`portal_services restart --generation-only` 改发 HUP，构建标识未变时保留当前 Worker。交接过程见 `decision_075f66430606`。

全部组件处理完后，控制面检查该实例 Web 的 `/api/health`。

发布激活按固定顺序处理受影响的组件：ResourceService、ExecutionService、SocietyService、ExecutionWorker、Release Web、Dev Web、用户入口路由器；ExecutionWorker 只发 HUP。全部处理后检查 Dev、Release 和用户入口三处健康，结果写入 receipt（见 `decision_99bb5dc0f96b`）。

### 后台组件怎样停止

SocietyService 和 ResourceService 收到 SIGTERM 后，先关闭监听 socket，唤醒空闲的读取和 Watch 流，给已接受的操作最多 5 秒送出结果，再断开剩余连接；随后同时通知各个后台工作停止，共用 24 秒等待它们退出。整个停止过程最多 30 秒：到时进程直接退出，打印所有线程的调用栈，未提交的事务随进程回滚，已提交的任务、回执和租约由新进程按原有恢复协议接手。

新进程每次启动都会做一轮全量对账，逐个检查每个 Lineage 的事件来源与运行状态，并修复各 Lineage 中 Resident 完成与 Resident 调用的记录。停止开始后，这一轮在两个 Lineage 之间、单个 Lineage 内两个节点或两次 Resident 调用之间结束，跳过收尾清理，剩下的交给下一轮。修复的每一步都按 Message ID 观察和提交，下一轮从停下的地方接着做。协调器停止时，排队但尚未开始的工作直接取消，不计入等待；正在等待 Resident 回合结束的任务运行在后台线程中，也不会拖住进程退出。

停止超过 3 秒时，进程先打印一次所有线程的调用栈（“Service shutdown still waiting”），到 30 秒上限时再打印一次（“Service shutdown deadline reached”）。排查慢停止时，从 `journalctl --user -u <前缀>-society.service`（ResourceService 为 `<前缀>-resource.service`）中这两段输出找到停止在等待的线程。

## 故障与恢复

| 发生的问题 | 调用方和用户看到什么 | 恢复方式 |
| --- | --- | --- |
| 组件名未知 | 命令在重启前报错，没有组件被重启 | 修正参数后重新执行 |
| Web 停止时有普通请求 15 秒内没有结束 | Web 日志记录“Cancel N running task(s)” | 无需处理；剩余任务被取消，已提交的领域操作仍然有效，浏览器重连新进程 |
| systemd 重启失败或 Web 健康检查超时 | 控制面返回错误，后续组件不再处理；发布激活的 receipt 为 `ok: false` | 运维查看单元日志后重新执行重启；需要撤销 Release 更新时修正后再发布 |
| 进程突然崩溃 | 事件流断开，没有任何预告 | 浏览器自动重连，连上新进程后同样重新加载；运维重新启动并验收 |

页面恢复后能看到哪些内容，取决于各页面自己保存的状态；`decision_9f6135cbbb22` 说明重新加载后怎样恢复已保存的页面偏好，未保存的表单和草稿由各页面负责。

## 重复的重启请求

命令行 `portal_services restart` 持有该实例的 `portal-services.lock`，同一实例的两次命令行重启依次执行。`request-restart` 和 `RestartPortal` 每次调用都启动一个新的 `tfi-pair-restart-<uuid>` 单元，不合并请求，也不取这把锁。发布激活在 `runtime/development-activation.lock` 下逐个执行，每个请求写自己的 receipt；激活按变更路径选择组件，没有受影响的组件时不重启任何服务（见 `decision_08bb2d9141f9`）。
