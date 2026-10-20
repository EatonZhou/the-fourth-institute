---
kb_id: decision_f25266e5a0e7
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 14
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:32:33Z'
  updated_by:
    user: ''
    agent: resident_139519ddd6fa
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/page_bootstrap.py
  - ${code_root}/management_portal/static/portal_page_boot.js
  - ${code_root}/tests/cases/test_portal_page_bootstrap.py
  - ${code_root}/management_portal/static/portal_component_recovery.js
  - ${code_root}/management_portal/static/portal_theme.js
  - ${code_root}/management_portal/static/portal_identity.js
  - ${code_root}/management_portal/static/portal_events.js
  - ${code_root}/management_portal/static/portal_refresh_coordinator.js
  - ${code_root}/management_portal/static/repo_workspace.js
  - ${code_root}/management_portal/static/resource_catalog.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/http_media_range.py
  - ${code_root}/management_portal/portal_deployment_router.py
  - ${code_root}/tests/cases/test_portal_asgi.py
  related:
  - decision_08bb2d9141f9
  - decision_daba6706a788
  - decision_741b04821171
  - decision_6e230393221c
  - decision_9f6135cbbb22
  - decision_850e6248d680
  supersedes: []
  derived_from: []
---
# Portal 前端恢复、HTTP 缓存与 Web 重启衔接

Portal 的页面依赖多个后台服务：资源由 ResourceService 提供，Resident 与 Lineage 由 SocietyService 提供，页面本身和大部分接口由 Portal Web 提供。这些服务会因发布或故障短暂不可用。本文说明浏览器在这些时刻怎样让用户继续工作：

- **局部恢复**：某个页面区域依赖的服务暂时不可用时，只遮住这个区域，有限次自动重试，服务恢复后重新读取这个区域的数据。
- **Web 重启衔接**：Portal Web 计划重启时，所有打开的页面显示倒计时，确认新的 Web 进程上线后，自动重新载入当前地址。
- **页面启动恢复**：检查关键脚本、样式和主界面的初始化结果；失败时有限次重新载入，仍失败则显示错误和手动重试入口。
- **HTTP 缓存与浏览器历史恢复**：哪些响应可以被浏览器缓存、用户按“后退”回到页面时怎样取得最新数据。

服务端怎样重启、部署怎样推进、重启提示由谁发出，见 decision_850e6248d680 与 decision_08bb2d9141f9。

## 先认识几个概念

| 概念 | 含义 |
| --- | --- |
| 恢复层 | 局部恢复时盖在一个页面区域上的半透明层，显示服务状态和等待提示；页面其他区域照常可用 |
| 宿主区域 | 恢复层覆盖的页面区域，例如 Repo 页面、资源目录页面或 Society 工作台 |
| 服务状态接口 | 每个服务提供给浏览器的状态读取地址，例如 `/resource/api/service-status`、`/lineage/api/service-status`；Portal 路由器另有一个独立的状态地址 `/__portal_router/status` |
| 等待状态 | 服务正在重启（`restarting`）、在线但需要等待（`busy`）或暂时不可用（`unavailable`），可以自动重试 |
| 终止状态 | 自动重试已经停止，恢复层显示“重新检查”按钮。服务报告 `blocked`、`failed`、`incompatible`、`terminal`，或明确“不能重试且未就绪”，以及重试次数用完，都进入终止状态 |
| 重启提示 | Web 计划重启时覆盖整个页面的提示框，显示倒计时 |
| 进程启动时间 | 每个 Web 进程启动时记录的时间戳（`/api/health` 的 `started_at`，重启提示的 `server_started_at`）。数值更大，说明已经换成了新的 Web 进程 |
| BFCache | 浏览器的前进后退缓存：用户后退时，浏览器直接恢复先前保存的整个页面，而不是重新请求 |

## 设计点

### 局部恢复

- **页面区域确认依赖的服务不可用后，才显示恢复层。** 判断依据是配置白名单中的服务不可用错误码（例如 `resource_service_unavailable`），或状态查询明确返回未就绪。`isUnavailable` 不依据自然语言消息猜测；业务请求失败但不在白名单中时，`run` 与恢复循环重新确认服务状态，就绪则交回业务错误，未就绪才进入恢复。普通 4xx 或首次加载中的等待本身不足以判定服务不可用。
- **恢复层只禁用它覆盖的区域中原本可用的控件，恢复后只重新启用这些控件。** 宿主页面通过控件列表声明要禁用哪些控件；页面其他区域不受影响。
- **恢复层按状态宣读提示。** 等待时恢复层使用 `aria-live="polite"`，宿主区域设为 `aria-busy="true"`；进入终止状态时改用 `aria-live="assertive"`。
- **自动重试有次数上限。** 默认最多检查 24 次（可配置为 1–60 次）。每次间隔取“服务建议的等待时间 `retry_after_ms`”和“指数退避时间”中较大的一个：退避从 750 毫秒开始，每次翻倍，最多翻倍 5 次；间隔不少于 250 毫秒，不超过 15 秒。
- **服务就绪后，先重新读取区域的数据，成功后才移除恢复层。** 重新读取时又遇到“不可用”错误，继续重试；遇到其他错误，恢复停止，错误交给页面自己的错误处理。
- **用完重试次数后进入终止状态。** 恢复层显示“<服务名> 连接失败”和“在有限自动重试窗口内仍不可用。自动重连已停止，可手动重新检查。”，用户点击“重新检查”后清除终止状态，从头再走一遍流程。
- **同一个区域的恢复不会重复进行。** 同一时间多次触发恢复、检查或重跑时，共用正在进行的那一次。
- **状态读取失败时先问路由器。** 服务状态接口读取失败或报告未就绪时，页面再读一次路由器的独立状态接口；它报告终止状态时直接进入终止状态。页面首次执行受保护的读取时，状态读取失败就先直接尝试读取数据，失败了再进入恢复流程。

目前接入局部恢复的页面区域：Repo 页面和资源目录页面（依赖 ResourceService），Society 工作台（依赖 SocietyService）。

### Web 重启衔接

- **Web 计划重启时，每个打开的页面都显示重启提示和倒计时。** 提示框标题为“系统即将重启”，倒计时每秒更新，按服务端时间校正本机时钟。提示框不能用 Esc 或点击背景关闭，但不锁住页面，用户仍可保存或结束当前操作。
- **页面从两个来源得知重启提示。** 一是 Portal 事件流推送的重启提示；二是定期读取 `/api/restart-notice`。事件流已连接或页面在后台时每 5 分钟读取一次，事件流未连接且页面可见时每 2 秒读取一次；收到事件流就绪（`portal:events-ready`）或页面重新可见时立即补读一次。读取失败不影响页面其他部分。
- **倒计时结束后，页面等待新进程上线。** 提示变为“请等待后台重启”，页面每 500 毫秒读取一次 `/api/health`；重启期间连接失败是正常的，页面继续等待。
- **确认新进程上线后，页面重新载入当前地址一次。** 只有 `/api/health` 或重启提示中的进程启动时间大于页面先前看到的值，才算新进程上线。重新载入保留路径、查询参数和锚点；登录页不自动重新载入；同一个新进程只触发一次，旧进程仍在运行时继续等待，不反复刷新。
- **页面离开时暂停，返回时继续。** 页面进入 `pagehide` 时停止倒计时、提示读取和健康检查；从 BFCache 恢复（`pageshow` 且 `persisted` 为真）时重新计算倒计时并继续读取。

### 页面启动校验与有限恢复

Web 健康接口只证明进程已经上线，不证明浏览器加载了完整资源或成功构建了界面。每次 Portal 页面初始加载都经过独立的启动检查：服务器把检查器直接内嵌在 HTML 的第一个脚本位置，不依赖可能加载失败的外部 UI 库。

服务器从当前文档提取首屏 `/static/` 脚本、样式清单，保留实际的构建版本参数。检查器在文档上监听资源加载成功或失败，在窗口上监听首屏脚本执行错误；文档解析结束后，还必须收到导航和已声明主工作台的就绪信号，才判定启动成功。Society、Paper、Article 声明主工作台就绪；延迟打开的面板不作为首屏前置条件。工作台完成初始布局后报告就绪，后台领域数据暂不可用仍交给原来的局部恢复层，不把正常的加载状态当作整页初始化失败。

资源明确失败或启动脚本抛错时进入恢复；资源始终未完成、初始化静默中断或文档解析卡住时，30 秒启动期限结束后进入恢复。首次自动重载等待 1 秒，第二次等待 2 秒，最多自动重载两次。重试次数保存在当前标签页的 sessionStorage 中，并按完整页面地址隔离；嵌入页面成功不能清除父页面的预算。当前页面全部检查通过才清除自身记录。重新加载保留初始路径、查询参数和锚点，使用真正的文档重载，不能把同地址的锚点导航当作恢复。

预算用完、浏览器不能保存重试次数，或用户已经在当前文档操作时，停止自动重载并显示错误。提示列出失败资源或未完成的初始化检查，提供“重新加载”按钮；用户应先保留未保存内容，再主动开始新一轮有限恢复。错误提示只依赖原生 DOM 和内联样式，即使脚本卡在 `<head>`、`<body>` 尚未解析出来也可显示。不重放发送消息、写入资源等业务操作。

页面离开时取消检查和重载计时器。从 BFCache 返回时，尚未结束的启动检查继续；已经停止或准备重载的恢复不自动重复。启动成功后的业务异常、图片加载失败和延迟资源错误不触发整页重载。

### 重新载入后的数据

- **重新载入后的页面从规范地址、Portal Page State 和服务端数据恢复。** 选中对象、布局等界面状态由各页面在重新载入前保存的 Portal Page State 和规范页面地址恢复；显示的数据一律从领域服务重新读取。
- **未保存的内容由原来的组件负责保留。** 对话草稿、未保存的表单和有未保存修改的文档，由它们各自的持久化或离开前确认负责；重启提示不接管业务状态。
- **恢复按影响范围选用。** 单个服务故障用局部恢复重建一个区域；Web 进程替换用重启提示重新载入整页；新文档资源或界面初始化失败用启动检查器有限恢复。服务状态读取不改变用户对资源和操作的权限。

### HTTP 缓存

- **Portal 应用自己决定每个响应的 `Cache-Control`，反向代理不改动。** Portal 路由器转发应用响应时保留该响应头；路由器自己生成的状态与后端不可用响应使用 `no-store`。
- **缓存策略按响应的用途确定：**

  | 响应 | `Cache-Control` | 原因 |
  | --- | --- | --- |
  | 已登录用户的页面文档 | `private, no-cache` | 浏览器可以保存当前用户的页面外壳，但每次使用前都向 Portal 重新验证；页面中的数据仍由接口重新读取 |
  | 登录、二次验证和密码重置页面 | `no-store` | 认证过程的页面不保存 |
  | 动态接口、状态读取、指令执行结果和错误响应 | `no-store` | 当前事实不由 HTTP 缓存提供；浏览器客户端也默认 `cache: no-store`（decision_daba6706a788） |
  | Server-Sent Events 事件流 | `no-cache, no-store` | 实时推送不缓存 |
  | 敏感下载（资源下载链接） | `private, no-store` | 下载内容不进入可复用缓存 |
  | 已上传文件的内容（`/api/uploads/<ID>`） | `private, max-age=300` | 同一个上传 ID 的内容不会改变，当前用户的浏览器可以复用 5 分钟 |
  | 受权限控制的媒体预览 | 请求带当前文件版本时 `private, max-age=31536000, immutable`，否则 `private, no-cache` | 文件版本改变时地址随之改变 |
  | Dev 的静态资源；Release 中没有带当前构建版本的静态资源 | `public, max-age=0, must-revalidate` | 可以保存，但每次使用前按 ETag 重新验证。Dev 的工作树可能在 Web 进程不重启的情况下修改，同一个带版本的地址不代表内容不变 |
  | Release 中带当前构建版本 `portal_v` 的静态资源 | `public, max-age=31536000, immutable` | Release 的构建版本对应不变的内容，可以长期复用 |

- **静态资源的 200 和 304 响应使用相同的缓存策略。** Portal 在页面 HTML 中给 `/static/...` 地址加上 `portal_v=<构建版本>`。检查前端交付时，要检查页面实际引用的带版本地址。需要让浏览器丢弃旧的长期缓存时，通过 Web 发布更新构建版本，让页面引用新地址。

### 浏览器历史恢复

- **Portal 保留已登录页面进入 BFCache 的条件，但不保证每次后退都命中 BFCache。** 浏览器版本、打开的功能、内存压力和页面运行状态都可能让浏览器改为完整导航。
- **页面从 BFCache 恢复时，重新确认身份并重新连接实时通道。** 共享身份模块跳过内存中的结果，重新读取 `/api/me`，会话失效时按统一规则跳转登录页。页面进入 `pagehide` 时，共享事件模块关闭事件流（EventSource），刷新协调模块关闭跨标签页广播通道（BroadcastChannel），并放弃事件流的主导权，避免后台消息或锁竞争让浏览器丢弃缓存的页面；恢复后重新打开通道、重新竞争主导权并连接事件流。之后收到的 `portal:events-ready` 和页面自己的恢复逻辑补读易变的数据。页面在 `pagehide` 释放的订阅、计时器和组件，在 `pageshow` 恢复后不能出现重复监听。
- **没有命中 BFCache 时，后退是一次普通的页面请求。** Portal 先验证会话，再从规范地址、Portal Page State 和服务端数据重建页面。BFCache 恢复与完整重新载入应得到相同的用户状态和最新的权限结果。测试分别记录 `pageshow.persisted` 和导航类型，不能把 `back_forward` 导航类型当作命中了 BFCache。

## 实现

### 局部恢复控制器

页面用 `PortalComponentRecovery.create(options)` 为一个区域创建恢复控制器。选项包括：`endpoint`（服务状态接口）、`serviceName`（服务名，用于显示提示）、`unavailableCodes`（不可用错误码数组，默认空；调用方显式提供，如 Resource 页的 `resource_service_unavailable` 或 Society 页保留的 `lineage.unavailable`、`lineage.busy` 机器码）、`host`（宿主区域的选择器）、`surfaceName`（提示中称呼这个区域的名字，例如“Repo Workspace”）、`controls`（要禁用的控件）、`maxAttempts`、`retryBaseMs`、`retryMaxMs`，以及回调 `onReady`（服务就绪后重新读取数据）、`onRecovered`、`onFatal`、`onStatus`。

| 方法 | 作用 |
| --- | --- |
| `readStatus()` | 读取服务状态，失败时参考路由器的独立状态 |
| `isUnavailable(error)` | 从 `error.code` 或 `error.data.code` 读取机器码，与 `unavailableCodes` 精确匹配 |
| `show(status)` / `hide()` | 显示或移除恢复层 |
| `recover(status, onReady)` | 从给定状态开始有限次的恢复循环 |
| `check(onReady)` | 读取状态，未就绪时进入恢复 |
| `run(onReady)` | 先以 optimistic 模式读取服务状态，就绪后执行 `onReady`；失败时按错误白名单或重读状态决定进入恢复还是交回业务错误 |
| `guard(request)` | 执行一次服务请求；请求因服务不可用失败时先用 `onReady` 选项开始检查与恢复，再把错误交给调用方 |
| `retry()` | 在终止状态下重新检查 |

Repo 与 Resource 页面的读取、页面专用写入和资源指令都经过同一个控制器的 `guard`，不再各自包装请求。控制器没有销毁方法，页面在自己的生命周期内复用同一个实例。控制器只使用服务状态中的 `ready`、`terminal`、`retryable`、`state` 和 `retry_after_ms`。

### 重启提示

重启提示由 `portal_theme.js` 中的重启控制逻辑负责，这个脚本在每个 Portal 页面加载：

```text
重启提示（事件流推送 / 定期读取 /api/restart-notice）
  → 显示提示框，每秒按服务端时间更新倒计时
  → 倒计时结束：提示“请等待后台重启”
  → 每 500 毫秒读取 /api/health?restart_probe=<时间戳>
  → started_at 大于先前看到的进程启动时间
  → window.location.reload()，只执行一次
```

`/api/restart-notice` 返回 `active`、`notice_id`、`message`、`restart_at`（计划重启时间）、`now`（服务端当前时间）和 `server_started_at`；没有计划重启时只返回 `active: false`、`now` 与 `server_started_at`。页面第一次读到的 `server_started_at` 作为比较基准。

### 页面启动检查器

HTML 响应在语言、身份引导和静态资源版本参数处理后，由 `page_bootstrap.py` 注入 `portal_page_boot.js` 的独立检查函数与当前文档清单。登录页和普通预览 HTML 不注入；其他 Portal 外壳检查首屏资源及导航。主工作台通过宿主的 `data-portal-boot-required` 声明额外检查。导航和 Workbench 各自在完成自己的初始化后调用 `PortalPageBoot.ready(root)`，检查器不查询它们的私有 DOM，不从服务健康状态推断就绪。

仓库中的 `test_portal_page_bootstrap.py` 检查注入位置、清单、转义和页面声明。断连、样式失败、执行错误、静默初始化中断、解析超时、重试预算、跨页面隔离，以及 Society 后台恢复保持局部的浏览器行为，须分别引用实际取得的验证记录；不能把注入层测试视为这些行为已验证。

### 缓存响应头

页面文档、静态资源和大多数接口由 Portal Web 的 HTTP 处理层写入缓存响应头：静态资源按实例（Dev 或 Release）和请求中的 `portal_v` 选择策略，页面文档与接口按类型选择。指令执行、上传、资源下载和事件流由 ASGI 层直接处理；这些地址的响应在没有自选缓存策略时一律补上 `no-store`，上传内容、资源下载和事件流使用上表中各自的策略。
