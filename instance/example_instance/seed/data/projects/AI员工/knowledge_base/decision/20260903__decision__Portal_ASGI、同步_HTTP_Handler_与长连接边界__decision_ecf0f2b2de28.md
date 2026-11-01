---
kb_id: decision_ecf0f2b2de28
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 8
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-31T18:14:17Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/web_server.py
  - ${code_root}/management_portal/notifications/async_fanout.py
  - ${code_root}/scripts/tfi_release_install.py
  - ${code_root}/scripts/tfi_development_runtime.py
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/notifications/realtime_lifecycle.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_portal_asgi.py
  - ${code_root}/tests/cases/test_web_server.py
  related:
  - decision_f55bac7fc199
  - decision_96a887d83e8a
  - decision_898b8911a2ff
  - decision_0820081083f5
  - decision_34158fdd4078
  - decision_85222f2d316b
  - decision_abaedeefca6f
  - decision_6e230393221c
  - decision_37f98f8fcb4b
  supersedes: []
  derived_from: []
---
# Portal ASGI、同步 HTTP Handler 与长连接边界

Portal Web 是 Portal 的浏览器应用层。它的大部分页面和 JSON 接口由同步处理函数实现：读取完整请求，一次生成完整响应。它还要支持持续推送的事件流、分块下载，以及进程停止时及时结束这些长连接。Portal Web 用 ASGI 应用承载这两类请求：同步处理函数放到线程中执行，需要长时间保持的连接由异步路由直接处理。

本篇说明 Portal Web 进程内的运行方式：请求怎样到达同步处理函数、哪些路由走异步处理、线程池和队列的容量、进程启动和关闭的顺序，以及停止时怎样结束实时连接。页面路由表、登录检查和错误格式见 decision_0820081083f5；事件流中通知的内容和可见性见 decision_abaedeefca6f。

## 必要概念

- **ASGI**：Python 异步 Web 服务器与应用之间的接口规范。Portal Web 用 Starlette 框架实现 ASGI 应用，由 Uvicorn 服务器运行。
- **事件循环**：在一个线程里轮流调度异步任务的运行器。任务等待网络或计时器时让出执行权，所以一条长连接不会占住一个线程；反过来，任何阻塞操作放在事件循环里都会让所有连接停顿。
- **同步处理函数（Handler）**：沿用 Python 标准库 `BaseHTTPRequestHandler` 写法的请求处理类 `portal.Handler`，从类文件对象读请求、向类文件对象写响应。
- **桥接层**：把 ASGI 请求转换成 Handler 能读取的对象，在线程中运行 Handler，再把它写出的响应转换回 ASGI 响应的代码（`asgi.py` 中的 `portal_http`）。
- **临时缓冲（spool）**：先放在内存、超过阈值后自动转存到临时文件的缓冲区。
- **SSE（Server-Sent Events）**：服务器沿一条 HTTP 连接持续推送文本事件的机制。
- **lifespan**：ASGI 规定的进程启动和关闭钩子，Portal Web 在这里创建和释放全进程共用的组件。
- **实时连接开关**（`RealtimeConnectionLifecycle`）：应用状态中的一个对象，记录 Web 是否还接纳新的事件流，并持有一个“结束信号”。停止开始时开关关闭、信号发出。

## 部署方式

Dev 和 Release 两个实例各运行一个 Web 进程：`python -m management_portal.web_server --host <监听地址> --port <端口>`。入口 `web_server.py` 用 Uvicorn 运行 `management_portal.asgi:app`，固定单进程、关闭访问日志、优雅关闭时限 15 秒（`--timeout-graceful-shutdown` 可改）；它在 Uvicorn 的 `Server` 上加了一步：收到停止信号时立即结束实时连接（见下文“停止时结束实时连接”）。启动命令由安装器 `scripts/tfi_release_install.py` 生成，配对运行时 `scripts/tfi_development_runtime.py` 为两个实例分别设定端口和源码目录。Dev 由 `<前缀>-dev-web.service` 运行，Release 由 `<前缀>-web.service` 运行；两者的端口记在实例配置中。每个实例只有一个 worker 进程，所以下文的线程池、通知队列和实时连接开关在每个实例中各有一份。外部访问经过反向代理（decision_85222f2d316b）和用户入口路由器 `<前缀>-router.service`，路由器按用户的选择转发到其中一个实例。

## 路由

| 路由 | 方法 | 处理方式 | 作用 |
| --- | --- | --- | --- |
| `/api/events` | GET | 异步 | 浏览器变化事件流（SSE） |
| `/api/instructions/execute` | POST | 异步 | HATG ISA 指令的 HTTP 入口；普通指令返回一次 JSON，流式指令返回 SSE（decision_898b8911a2ff） |
| `/api/uploads/{upload_id}` | PUT、POST | 异步 | 向已经创建的上传写入内容 |
| `/api/uploads/{upload_id}` | GET | 异步 | 读取已完成的上传内容，用于附件预览 |
| `/api/resources/download/{token}` | GET | 异步 | 分块下载一次资源读取中过大的结果，支持单段 `Range` |
| 其他所有路径 | GET、HEAD、POST、OPTIONS | 同步 Handler | 页面、静态文件和其余 JSON 接口 |

除了最后一行，每个异步路由都包在同一个错误边界里：未处理的异常按 Portal 的 JSON 错误格式返回，5xx 错误对外只显示“Portal request failed”，不暴露内部细节。

## 设计点

- **同步 Handler 在线程池中运行。** 事件循环线程只负责收发数据；文件读取、模板渲染和内部 RPC 这类阻塞操作都在线程中执行。
- **请求正文收齐并检查大小后才调用 Handler。** 正文上限 520 MiB，超过时立即返回 413，Handler 不会执行。
- **请求和响应都先写进 2 MiB 的临时缓冲。** 超过 2 MiB 的部分转存到临时文件，内存占用有上限。各业务接口仍按自己的规则限制内容大小。
- **需要长时间保持或分块发送的连接由异步路由处理。** 这些连接不占用 Handler 线程；可能阻塞的计算（可见性查询、指令执行、读取下一块内容）交给线程执行。
- **已经生成的响应不排在业务请求后面发送。** 响应正文由 Starlette 自带的线程池分块读取，与 Handler 线程池分开，Handler 线程全部被慢速内部调用占住时，已完成的响应仍能发出。
- **静态资源有自己的线程池。** `/static/` 的 GET 和 HEAD 仍由同一个 Handler 按原有路由和权限规则处理，但在单独的线程池中运行；业务接口占满 Handler 线程时，页面仍能加载显示错误和恢复界面所需的脚本与样式。
- **停止信号一到就结束实时连接。** 实时连接会一直打开，Web 停止时不等它们自己结束；其他进行中的请求仍有 15 秒完成。

## 同步请求怎样处理

```mermaid
sequenceDiagram
    participant U as Uvicorn
    participant B as 桥接层
    participant H as Handler 线程
    U->>B: 请求头与正文分块
    B->>B: 正文写入临时缓冲，累计不超过 520 MiB
    B->>H: 在 Handler 线程池中运行
    H->>H: 把状态行、响应头和正文写入响应缓冲
    H-->>B: 完成
    B->>U: 状态与响应头
    B->>U: 正文按 256 KiB 分块发送
```

**Handler 看到的请求与原来一致。** 桥接层为 Handler 填好方法、路径和查询字符串、HTTP 版本（固定为 HTTP/1.1）、请求头、客户端地址、服务器地址、请求协议（http 或 https，用于同源检查）和正文。Handler 没有对应的 `do_<方法>` 时返回 405。

**响应由缓冲收集后再发送。** Handler 写出的第一部分被解析为状态行和响应头，头部超过 64 KiB、状态行无效或 Handler 没有写出任何响应时报错。`Connection` 和 `Transfer-Encoding` 由 Uvicorn 自己管理，桥接层丢弃 Handler 写出的这两个头。Handler 完成后才开始发送响应，所以同步路由不适合持续推送。

**Handler 抛出异常时。** JSON 接口路径按 Portal 的 JSON 错误格式返回对应状态码；页面路径交给 Starlette 返回 500。无论成功还是失败，临时缓冲都会关闭。

**客户端中途断开时。** 当前请求的发送结束，但线程中已经提交的领域操作仍然有效；调用方重试写操作前，应先用稳定 ID 或版本号查询当前状态。

## 线程池和容量

| 资源 | 容量 | 超出或用尽时 |
| --- | --- | --- |
| 同步 Handler 请求正文 | 520 MiB | 返回 413，不调用 Handler |
| 请求与响应的内存缓冲 | 各 2 MiB | 超出部分转存临时文件 |
| 响应头 | 64 KiB | 报错，返回失败 |
| 响应发送块 | 256 KiB | 按块读取和发送 |
| Handler 线程池 `portal-http` | `PORTAL_HTTP_HANDLER_WORKERS`，默认 32，最少 16 | 同步路由排队等待线程 |
| 静态资源线程池 `portal-static` | 4 个线程 | `/static/` 请求排队，不占用 Handler 线程池 |
| 非流式指令线程池 `portal-instruction` | 16 个线程 | 普通 ISA 调用排队，不占用 Handler 线程池 |
| 事件线程池 `portal-sse-payload` | 4 个线程 | 事件内容生成与流式指令共用；单批事件内容等待超过 24 秒时改为要求页面重新读取，结束信号可提前结束等待 |
| 事件流单连接队列 | 64 条通知 | 这个连接改为要求页面重新读取 |
| 流式指令的批次队列 | 16 批 | 生成批次的线程等待浏览器取走 |

变化事件流的 ready、心跳、重连及快照内容由 decision_abaedeefca6f 定义；本表集中定义执行容量和等待时限。

## 长连接路由

**变化事件流 `/api/events`。** 需要登录，未登录时重定向到 `/login`。打开前确保 NATS 监听器和知识归档的文件观察器（decision_f55bac7fc199）已经启动。实时连接开关已关闭、分发器或事件线程池不可用时返回 503。连接先发送 `portal-ready`，其中的 `server_started_at` 是当前 Web 进程的启动时间，页面据此判断自己是否连上了新进程（decision_37f98f8fcb4b）。每批通知在线程池中生成内容；结束信号可打断等待，分发器关闭时连接随即结束，退出时取消订阅。响应类型为 `text/event-stream`，带 `Cache-Control: no-cache, no-store` 和 `X-Accel-Buffering: no`，后者让反向代理不缓冲事件。事件内容、筛选、ready、心跳及重连契约见 decision_abaedeefca6f。

**流式指令 `/api/instructions/execute`。** 调用者用登录会话或 Bearer 凭证认证，页面请求还要通过同源检查。流式指令在事件线程池中运行，每产生一批结果放进最多 16 批的队列，浏览器依次收到 `batch` 事件（事件 ID 为最后一项的游标），结束时收到 `end`，出错时收到 `error`；连接先发送 `retry: 3000`，空闲 10 秒发送心跳。浏览器断开后设置停止标志并取消流式调用；远端流通过关闭绑定 socket 打断阻塞读取，生产线程不再继续向队列发布。线程取消不回滚已经提交的领域操作。认证、授权上下文和结果格式见 decision_898b8911a2ff。

**上传与下载。** 上传写入在读取正文前先检查 `Content-Length`，读取过程中累计超过上传上限立即返回 413。下载每一块都重新检查调用者对资源的读权限；`Range` 越界时返回 416。这两类请求的业务规则属于资源指令。

## 停止时结束实时连接

打开的页面一直保持一条 `/api/events` 连接。Uvicorn 的优雅关闭先停止接受新连接，再等待所有打开的响应结束，最后才执行 lifespan 关闭；如果只靠它，停止中的 Web 会把这些事件流一直等到 15 秒时限，期间新请求已经无法到达这个进程。

`web_server.py` 的 `PortalWebServer` 在 Uvicorn 处理停止信号的同时，把 `asgi.end_realtime_streams` 交给事件循环执行：

1. 实时连接开关关闭，之后到达的 `/api/events` 返回 503；结束信号发出，正在等待事件内容生成的连接放弃这一批并结束；
2. 分发器关闭，所有正在等待通知的连接立即醒来并结束。

事件流结束后，Uvicorn 只需等待其他进行中的普通请求，随后执行 lifespan 关闭。浏览器的事件流断开后自动重连，连到新进程。lifespan 关闭的第一步也调用同一个函数，所以不经过这个入口启动的应用（例如测试）停止时同样结束事件流。

本站 2026-10-27 的实测：有页面连着时，Release Web 从收到停止信号到进程退出用时 0.8 秒，Dev Web 1.5 秒；只靠 Uvicorn 优雅关闭时，每次都要等满 15 秒。

## 启动和关闭顺序

lifespan 启动时依次：

1. 调用已安装功能的 `web.lifespan` 接入点，保存各功能返回的停止回调；后台任务清单属于功能声明，不由 ASGI 重复维护。
2. 创建并启动分发器，启动 NATS 监听器。
3. 让侧栏缓存订阅通知中心：会话通知使会话索引缓存失效，执行通知以外的其他通知使 Resident 侧栏缓存失效。
4. 创建 Handler、静态资源、非流式指令和事件内容四个线程池，保存到应用状态；创建处于接纳状态的实时连接开关。
5. 登记本进程的 Web kernel；`PORTAL_ISA_ROUTING=socket` 时再打开指令 socket（decision_96a887d83e8a）。

关闭时依次：

1. 结束实时连接：关闭实时连接开关、发出结束信号并关闭分发器（与停止信号到达时执行的是同一个函数，已经执行过时不再有作用）。
2. 若已打开指令 socket，停止它。
3. 取消侧栏缓存订阅并清空缓存。
4. 按启动的反序执行功能停止回调。
5. 关闭四个线程池，不等待运行中的任务，并取消尚未开始的任务。
6. 关闭 NATS 监听器和发布器。

Uvicorn 收到停止信号后先停止接受新连接，最多等 15 秒让进行中的请求结束，到时取消剩余任务，然后才执行 lifespan 关闭；事件流在停止信号到达时已经结束，不占用这段等待。领域服务和 NATS 能独立重连，Portal Web 启动不需要等它们就绪。具体功能后台任务由功能自身负责启动和停止，接入约定见 decision_15aab18c0ab3。

## 故障与恢复

| 情况 | 结果 | 恢复方式 |
| --- | --- | --- |
| 同步 Handler 请求正文超过 520 MiB | 413 | 缩小请求，或改用上传接口 |
| Handler 抛出异常 | JSON 接口返回对应错误码，页面返回 500 | 先查询领域状态确认是否已经提交，再按幂等键或版本规则重试 |
| Handler 线程全部占用 | 新的同步请求排队 | 已经生成的响应照常发送；慢调用结束后恢复 |
| 实时连接开关已关闭（Web 正在停止） | `/api/events` 返回 503 | 新进程就绪后浏览器重连 |
| 事件内容生成过慢 | 该批改为要求重新读取 | 页面重新读取当前视图 |
| Web 停止时普通请求 15 秒内没有结束 | Uvicorn 取消剩余任务并记录“Cancel N running task(s)” | 无需处理；已提交的领域操作仍然有效，调用方按稳定 ID 或版本号确认后再重试 |
| 客户端中途断开 | 当前请求结束 | 领域状态以实际提交结果为准 |
