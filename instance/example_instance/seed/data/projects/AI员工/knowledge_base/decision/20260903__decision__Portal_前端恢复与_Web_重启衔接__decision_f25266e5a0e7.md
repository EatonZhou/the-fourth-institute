---
kb_id: decision_f25266e5a0e7
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 16
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-27T13:08:41Z'
  updated_by:
    user: ''
    agent: resident_73cb83291316
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
- **Web 重启衔接**：Portal Web 换成新进程后，每个打开的页面在事件流连上新进程时自动重新载入当前地址，换用新进程的页面资源。
- **页面启动恢复**：检查关键脚本、样式和主界面的初始化结果；失败时有限次重新载入，仍失败则显示错误和手动重试入口。
- **HTTP 缓存与浏览器历史恢复**：哪些响应可以被浏览器缓存、用户按“后退”回到页面时怎样取得最新数据。

服务端怎样重启、Web 停止时怎样结束事件流，见 decision_37f98f8fcb4b；发布流程见 decision_08bb2d9141f9。

## 先认识几个概念

| 概念 | 含义 |
| --- | --- |
| 恢复层 | 局部恢复时盖在一个页面区域上的半透明层，显示服务状态和等待提示；页面其他区域照常可用 |
| 宿主区域 | 恢复层覆盖的页面区域，例如 Repo 页面、资源目录页面或 Society 工作台 |
| 服务状态接口 | 每个服务提供给浏览器的状态读取地址，例如 `/resource/api/service-status`、`/lineage/api/service-status`；Portal 路由器另有一个独立的状态地址 `/__portal_router/status` |
| 等待状态 | 服务正在重启（`restarting`）、在线但需要等待（`busy`）或暂时不可用（`unavailable`），可以自动重试 |
| 终止状态 | 自动重试已经停止，恢复层显示“重新检查”按钮。服务报告 `blocked`、`failed`、`incompatible`、`terminal`，或明确“不能重试且未就绪”，以及重试次数用完，都进入终止状态 |
| 进程启动时间 | 每个 Web 进程启动时记录的时间戳（`/api/health` 的 `started_at`，事件流就绪事件 `portal-ready` 的 `server_started_at`）。数值更大，说明已经换成了新的 Web 进程 |
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

- **重启不预告，页面跟着事件流走。** Web 停止时立即结束事件流（decision_37f98f8fcb4b），浏览器按事件流的 `retry: 1500` 自动重连；重启期间用户入口路由器替页面等待新进程上线。页面不显示倒计时，也不轮询健康接口。
- **事件流连上更新的 Web 进程时，页面重新载入当前地址一次。** 每条事件流的第一个事件 `portal-ready` 带着该进程的 `server_started_at`。页面记下第一次收到的值；之后收到的值更大，就说明换成了新进程，页面随即重新载入。重连到同一个进程、网络抖动或换订阅范围都不触发重新载入。重新载入保留路径、查询参数和锚点；登录页不自动重新载入。
- **所有标签页和内嵌页面各自判断。** 持有事件流的标签页把就绪事件连同 `server_started_at` 转发给其他标签页和内嵌页面，每个页面用自己记下的值比较。
- **从 BFCache 返回时同样判断。** 页面在 `pagehide` 关闭事件流、`pageshow` 后重新连接，记下的值随页面保留；页面在缓存期间 Web 已经重启时，重新连接后的就绪事件让页面重新载入。

### 页面启动校验与有限恢复

Web 健康接口只证明进程已经上线，不证明浏览器加载了完整资源或成功构建了界面。每次 Portal 页面初始加载都经过独立的启动检查：服务器把检查器直接内嵌在 HTML 的第一个脚本位置，不依赖可能加载失败的外部 UI 库。

服务器从当前文档提取首屏 `/static/` 脚本、样式清单，保留实际的文件内容摘要参数。检查器在文档上监听资源加载成功或失败，在窗口上监听首屏脚本执行错误；文档解析结束后，还必须收到导航和已声明主工作台的就绪信号，才判定启动成功。Society、Paper、Article 声明主工作台就绪；延迟打开的面板不作为首屏前置条件。工作台完成初始布局后报告就绪，后台领域数据暂不可用仍交给原来的局部恢复层，不把正常的加载状态当作整页初始化失败。

资源明确失败或启动脚本抛错时进入恢复；资源始终未完成、初始化静默中断或文档解析卡住时，30 秒启动期限结束后进入恢复。首次自动重载等待 1 秒，第二次等待 2 秒，最多自动重载两次。重试次数保存在当前标签页的 sessionStorage 中，并按完整页面地址隔离；嵌入页面成功不能清除父页面的预算。当前页面全部检查通过才清除自身记录。重新加载保留初始路径、查询参数和锚点，使用真正的文档重载，不能把同地址的锚点导航当作恢复。

预算用完、浏览器不能保存重试次数，或用户已经在当前文档操作时，停止自动重载并显示错误。提示列出失败资源或未完成的初始化检查，提供“重新加载”按钮；用户应先保留未保存内容，再主动开始新一轮有限恢复。错误提示只依赖原生 DOM 和内联样式，即使脚本卡在 `<head>`、`<body>` 尚未解析出来也可显示。不重放发送消息、写入资源等业务操作。

页面离开时取消检查和重载计时器。从 BFCache 返回时，尚未结束的启动检查继续；已经停止或准备重载的恢复不自动重复。启动成功后的业务异常、图片加载失败和延迟资源错误不触发整页重载。

### 重新载入后的数据

- **重新载入后的页面从规范地址、Portal Page State 和服务端数据恢复。** 选中对象、布局等界面状态由各页面在重新载入前保存的 Portal Page State 和规范页面地址恢复；显示的数据一律从领域服务重新读取。
- **未保存的内容由原来的组件负责保留。** 对话草稿、未保存的表单和有未保存修改的文档，由它们各自的持久化或离开前确认负责；重启衔接不接管业务状态。
- **恢复按影响范围选用。** 单个服务故障用局部恢复重建一个区域；Web 进程替换由事件流就绪事件触发整页重新载入；新文档资源或界面初始化失败用启动检查器有限恢复。服务状态读取不改变用户对资源和操作的权限。

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
  | Dev 的静态资源；Release 中未带内容摘要或使用旧构建标签的静态资源 | `public, max-age=0, must-revalidate` | 每次使用前按内容 ETag 验证，进程启动标识不承担静态文件身份 |
  | Release 中 `portal_v=sha256-<摘要>` 与响应文件原始字节匹配的静态资源 | `public, max-age=31536000, immutable` | 文件内容摘要绑定 URL 与字节；gzip 与原始响应共用内容身份，ETag 区分编码 |

- **静态资源的 200 和 304 响应使用相同的缓存策略。** 公共 HTTP 静态文件 owner 在页面发出时读取每个文件的内容摘要，替换该 URL 的 `portal_v`，保留其他查询参数及片段。摘要和压缩载荷按文件元数据有界复用，文件变更立即失效；无需重启发布同样得到新 URL。健康接口的进程 `build_id` 不参与静态缓存身份。延迟加载器使用需重新验证的无版本地址，不复制另一个脚本的摘要。

- **摘要不匹配的请求返回 409 与 `no-store`。** 服务器不把当前字节填入旧摘要 URL，即使请求携带旧 ETag 也不返回 304。页面在载入中跨过发布时，由已有启动检查器在原有预算内重新加载最新 HTML；已在浏览器中缓存的旧摘要文件仍可按其原内容复用。发布兼容单元继续保证源码一起交付。

- **旧长期缓存通过新文档地址恢复。** 已打开的旧页面重新加载后获得逐文件摘要 URL，从而避开历史 `portal_v=development` 或进程构建标签下的缓存，无需用户清空缓存。服务端无法撤回浏览器已经缓存并执行的脚本；未重新导航且没有触发既有启动恢复的页面保持原生命周期。部署只更新静态文件时不强制重载正在使用的页面。

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

### 新进程检测

新进程检测由 `portal_theme.js` 负责，这个脚本在每个 Portal 页面加载：

```text
portal_events.js 收到 portal-ready
  → 派发 portal:events-ready，detail 带 server_started_at（并转发给其他标签页和内嵌页面）
portal_theme.js
  → 第一次收到：记下 server_started_at
  → 之后收到更大的值（超过 1 毫秒）：不在 /login 时 window.location.reload()
```

`server_started_at` 由 Web 进程启动时记录，`/api/health` 的 `started_at` 是同一个值。

### 页面启动检查器

HTML 响应在语言、身份引导和静态资源版本参数处理后，由 `page_bootstrap.py` 注入 `portal_page_boot.js` 的独立检查函数与当前文档清单。登录页和普通预览 HTML 不注入；其他 Portal 外壳检查首屏资源及导航。主工作台通过宿主的 `data-portal-boot-required` 声明额外检查。导航和 Workbench 各自在完成自己的初始化后调用 `PortalPageBoot.ready(root)`，检查器不查询它们的私有 DOM，不从服务健康状态推断就绪。

仓库中的 `test_portal_page_bootstrap.py` 检查注入位置、清单、转义和页面声明。断连、样式失败、执行错误、静默初始化中断、解析超时、重试预算、跨页面隔离，以及 Society 后台恢复保持局部的浏览器行为，须分别引用实际取得的验证记录；不能把注入层测试视为这些行为已验证。

### 缓存响应头

页面文档、静态资源和大多数接口由 Portal Web 的 HTTP 处理层写入缓存响应头：静态资源按实例（Dev 或 Release）、请求中的 `portal_v` 与实际响应字节摘要选择策略，页面文档与接口按类型选择。指令执行、上传、资源下载和事件流由 ASGI 层直接处理；这些地址的响应在没有自选缓存策略时一律补上 `no-store`，上传内容、资源下载和事件流使用上表中各自的策略。
