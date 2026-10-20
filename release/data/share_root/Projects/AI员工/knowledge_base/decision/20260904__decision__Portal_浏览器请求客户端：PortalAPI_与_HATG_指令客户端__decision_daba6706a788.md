---
kb_id: decision_daba6706a788
type: decision
project: AI员工
created_at: '2026-09-04'
version:
  number: 8
  created_at: '2026-09-04'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_api.js
  - ${code_root}/management_portal/static/portal_instruction_client.js
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/tests/cases/test_portal_response_delivery.py
  related:
  - decision_ad3e21094f14
  - decision_898b8911a2ff
  - decision_0820081083f5
  - decision_741b04821171
  - decision_a144bf700017
  - decision_61e50306cc79
  - decision_6e230393221c
  supersedes: []
  derived_from: []
---
# Portal 浏览器请求客户端：PortalAPI 与 HATG 指令客户端

Portal 的每个页面都要通过 HTTP 向同一个 Portal 服务读取和提交数据。浏览器中有两个共享的请求客户端负责这件事：

- **`PortalAPI`**：普通 HTTP 请求的客户端。页面读取 JSON、提交表单、下载文本、图片和文件都经过它。
- **`PortalInstructionClient`**：执行 HATG ISA 指令的客户端。HATG ISA 是 Portal 中人和 Agent 共同使用的具名指令集（decision_898b8911a2ff），页面上的修改操作大多以指令的形式提交到唯一的指令执行地址 `/api/instructions/execute`。

两个客户端把正文编码、超时、取消、登录失效和错误形状统一起来：用户在不同页面遇到会话失效或请求失败时看到相同的行为，页面开发者也不必各自处理这些细节。本文只说明网络传输。搜索词、选中对象、加载状态、接口权限、业务重试和成功后的界面刷新，由调用它们的页面和领域服务负责；刷新去重、轮询和事件恢复由 Refresh Coordinator 负责（decision_6e230393221c）。

## 先认识一次请求

| 概念 | 含义 |
|---|---|
| 请求方法 | 表示动作类型的 GET、POST、HEAD，或调用方提供的其他方法 |
| JSON | Portal 接口常用的结构化文本格式；JavaScript 普通对象编码成 JSON 正文发送 |
| 表单数据 | 浏览器的 `FormData` 对象，用来提交字段或文件；浏览器自动生成 multipart 格式需要的分隔标记 |
| 响应 | 浏览器返回的 `Response`，包含 HTTP 状态、响应头和只能读取一次的正文 |
| 成功状态 | 2xx 状态（`Response.ok` 为真）。其他状态按所用方法转成错误，或交给调用方判断 |
| 取消信号 | `AbortSignal`，页面卸载、查询变化或用户取消时用它终止请求 |
| 超时 | 请求超过指定时长后由客户端主动终止 |
| 原始响应 | 没有解析正文的 `Response`，供图片、文本、文件和预览自行读取 |
| 幂等键 | 调用指令时附带的一个编号；用同一个编号重复提交同一条指令，操作只执行一次（decision_898b8911a2ff） |

## 设计点

### 请求正文与缓存

- **没有正文时默认发送 GET，提供正文时默认发送 POST。** 调用方显式给出 `method` 时以它为准。
- **普通对象正文自动编码成 JSON。** 字符串正文原样发送；两者都默认带 `Content-Type: application/json`，调用方显式给出的 Content-Type 优先。没有正文的请求不发送空 JSON。
- **表单正文交给浏览器生成格式。** 客户端删除任何大小写形式的 Content-Type，由浏览器填写 multipart 类型和分隔标记。
- **请求头按浏览器的标准 Header 规则合并。** `headers` 可以是普通对象、`Headers` 或键值对列表；名称不区分大小写。每次请求复制一份请求头，调用方传入的对象保持不变。
- **动态请求默认不使用浏览器缓存。** 默认 `cache: "no-store"`，调用方可以明确指定其他缓存方式。

### 取消与超时

- **每次请求有自己的取消信号。** 调用方的信号已经取消或随后取消时，只终止这一次请求，并把取消原因传下去。
- **默认超时是 120 秒。** 调用方可以为上传或长任务设置其他时长；0、负数和非有限值表示不设超时。
- **取消和超时可以区分。** 主动取消以 `AbortError` 结束，超时以 `TimeoutError` 结束。
- **请求结束时释放计时器和监听。** 成功、HTTP 失败、解析失败和取消都经过同一个清理步骤。

### JSON 响应

- **正文只读取一次。** 客户端保存原始文本、解析结果、是否为空和是否无效，调用方不需要再读 `Response` 正文。
- **空白正文当作空对象。** 204 或成功但没有正文的接口得到 `payload: {}`、`empty: true`。
- **成功状态却返回无效 JSON 时报错。** 普通数据方法抛出“请求没有返回有效数据，请刷新页面后重试。”，HTML 或损坏的文本不会交给业务代码。
- **需要自己处理状态的调用方可以拿到解析细节。** 它能同时读取 `Response`、解析结果、`empty`、`invalid` 和原始文本。

### 错误与登录失效

- **普通方法只返回成功状态的数据。** 非 2xx 状态抛出错误，错误消息依次取服务端返回的 `error`、`message`，都没有时为“HTTP <状态码>”。
- **错误保留机器可读的内容。** 错误对象带字符串 `code`、对象 `data`（服务端返回的正文）和数字 `status`，页面据此识别冲突、确认要求和权限错误。
- **会话失效时，两个客户端都把页面送到登录页。** 服务返回 401，或普通请求被重定向到 `/login` 时，客户端跳转到 `/login`，这次请求以“登录状态已失效，请重新登录后再试。”结束。只是发生了重定向、最终地址不是登录页时，按最终响应继续处理。
- **登录页和健康检查可以关闭自动跳转。** 它们用 `detectLoginRedirect: false` 自己处理 401。

### 执行指令

- **页面用 `PortalInstructionClient.execute` 执行非流式指令，成功时得到指令结果对象。** 它按 `tfi.hatg-instruction.v2` 格式提交指令名、参数、可选的幂等键和可选的 `expected_version`；服务端只接受该精确格式；服务返回 `status: "committed"` 时返回其中的 `result`。
- **失败时抛出带指令错误码的错误。** 错误带 `code`（例如 `instruction.forbidden`）、`details`、HTTP `status`、`instructionId`（本次执行的编号）和完整响应 `body`；消息取服务端的错误说明，没有时为“指令执行失败（HTTP <状态码>）”。
- **带幂等键的调用遇到同步未完成、同一请求仍在执行或责任服务暂不可用时，客户端用同一个幂等键自动重试。** 对应错误码为 `instruction.projection_pending`、`instruction.in_progress` 和 `instruction.owner_unavailable`。重试最多 7 次，间隔依次为 0.5、1、2、3、5、8、10 秒；页面取消请求时立即结束正在等待的重试间隔，并停止后续重试。没有幂等键的调用不能安全重复，直接把错误交给调用方。
- **页面卸载时仍要完成的小请求可以设置 `keepalive`。** 浏览器在页面关闭后继续把它发送完。

### 订阅流式指令

- **页面用 `PortalInstructionClient.watch` 订阅流式指令，每收到一批新结果就回调一次。** 流式指令是持续返回结果的只读指令，例如 `WatchConversationEvents`、`WatchTriggerPath` 和 `WatchLineage`，可用指令以正式 ISA 为准。服务以 Server-Sent Events（SSE，服务端持续推送的 HTTP 事件流）返回结果。
- **连接断开后，客户端从最后收到的位置继续订阅。** 每条结果带有游标 `cursor`；重新连接时客户端把最后一个游标作为 `after_cursor` 交回，结果不会重复或遗漏。重连间隔从 0.5 秒开始，每次翻倍，最长 8 秒；成功连接后恢复 0.5 秒。
- **只有暂时性故障才重连。** 网络中断、没有错误码的 5xx 响应，以及错误码为 `instruction.owner_unavailable`（服务暂时不可用）或 `instruction.failed`（一般执行失败）的失败会重连，包括流中途送来的错误事件；其他错误码（例如权限和参数错误）和 4xx 状态结束订阅，错误通过返回的 `done` 交给调用方。
- **订阅在服务发送结束事件或页面调用 `close()` 时结束。** `close()` 同时中止当前连接。

### 上传文件

- **页面用 `PortalInstructionClient.upload` 上传文件，分两步完成。** 客户端先执行资源指令 `CreateUpload`（decision_ad3e21094f14）登记这次上传，再把文件字节 PUT 到返回的上传地址。完成后返回上传 ID 和可引用的地址 `/api/uploads/<上传 ID>`。
- **上传显示进度，失败时说明原因。** 字节传输期间客户端按 0–99% 回报进度；网络错误、取消和超时（默认 120 秒）分别报告“上传失败，请检查网络后重试。”“上传已取消。”“上传超时，请重试。”；401 同样跳转登录页。

## 实现：请求怎样流过客户端

`PortalAPI` 处理一次请求的顺序：

```text
页面调用 PortalAPI 的某个方法
  → 合并客户端默认选项与本次选项
  → 确定方法、编码正文、补上默认缓存方式
  → 组合调用方的取消信号和超时
  → 浏览器 fetch
  → 检查 401 与登录页重定向
  → 按需一次性读取并解析 JSON
  → 返回数据、状态或原始 Response
  → 无论成败，清除计时器和取消监听
```

`PortalInstructionClient.execute` 通过 `PortalAPI.requestResult` 向 `/api/instructions/execute` 提交请求，复用同源凭据、语言头、超时、取消和登录检查。指令客户端解析指令状态与错误码，调用方负责把展示用的定义翻译为当前语言。`watch` 用 fetch 读取持续的事件流；上传字节用 `XMLHttpRequest` 报告进度。

共享客户端只负责跨页面稳定的传输规则。业务请求的先后顺序留在页面中：搜索页面仍要用请求序号或目标标记丢弃迟到的旧响应。

## 接口

### PortalAPI 的内部选项与解析结果

- `requestResult(path, options)` 使用 `options.method`；没有时按 `options.body` 是否为 `undefined` 选择 GET 或 POST。
- `detectLoginRedirect`、`parseJson`、`timeoutMs` 只供客户端使用，发出请求前移除。`DEFAULT_TIMEOUT_MS` 为 120000。
- 返回对象包含 `response`、`payload`、`empty`、`invalid`、`text`：

  | 情况 | `payload` | `empty` | `invalid` | `text` |
  | --- | --- | --- | --- | --- |
  | 有效的非空 JSON | 解析结果 | `false` | `false` | 原文 |
  | 空白正文 | `{}` | `true` | `false` | 原文 |
  | 无效的非空 JSON | `null` | `false` | `true` | 原文 |
  | `parseJson: false` | `null` | `false` | `false` | 空字符串 |

- 登录判断：`response.status === 401`，或跟随重定向后最终地址的路径为 `/login`，就调用 `window.location.assign("/login")` 并抛出会话失效错误。

### PortalAPI 的方法

| 方法 | 作用 |
| --- | --- |
| `request(path, options)` | 完整 JSON 请求。成功且 JSON 有效时返回 `payload`；成功但 JSON 无效时抛出可读错误；非成功状态抛出带 `code`、`data`、`status` 的错误。`parseJson: false` 时成功结果为 `null` |
| `get(path, options)` | 以 GET 调用 `request` |
| `post(path, body, options)` | 以 POST 调用 `request` |
| `postForm(path, formData, options)` | 以 POST 发送 `FormData` |
| `requestResult(path, options)` | 返回响应和解析详情，不因 HTTP 状态抛错，供需要自己处理状态的中间层使用 |
| `requestStatus(path, options)` | 返回 `{status, ok, data, response, text}`；JSON 无效时 `data` 为 `{}` |
| `postStatus(path, body, options)` | 以 POST 调用 `requestStatus` |
| `raw(path, options)` | 不解析正文，返回 `Response`；登录检查在收到响应头时执行，超时和取消覆盖正文读取，读完、取消或出错后清理计时器与监听 |
| `requireJson(result)` | 把 `requestResult` 的结果收窄为必需的 JSON：有效时返回 `payload`，无效时用保存的原文执行标准 `JSON.parse`，抛出标准解析错误 |
| `createClient(defaultOptions)` | 创建带默认选项的客户端。每次调用以本次选项覆盖默认选项，请求头逐项合并；返回的客户端提供 `requireJson`、`request`、`requestResult`、`requestStatus`、`get`、`post`、`postForm`、`postStatus` 和 `raw` |

`PortalAPI` 还公开 `DEFAULT_TIMEOUT_MS`。

### PortalInstructionClient 的方法

| 方法 | 作用 |
| --- | --- |
| `execute(instruction, args, options)` | 执行非流式指令，返回 `result`。`options` 可以包含 `idempotencyKey`、`expectedVersion`、`signal`、`timeoutMs` 和 `keepalive` |
| `watch(instruction, args, onBatch, options)` | 订阅流式指令，返回 `{done, close}`。SSE 事件 `batch` 的 `items` 交给 `onBatch`；`end` 结束订阅；`error` 带指令错误对象；`heartbeat` 每 10 秒一次，只用于保持连接 |
| `upload(file, options)` | 上传文件，返回 `{upload_id, url, file_name, media_type, size}`。`options` 可以包含 `purpose`、`target`、`fileName`、`mediaType`、`timeoutMs` 和 `onProgress` |

`PortalInstructionClient` 还公开 `SCHEMA`（调用格式名）和 `ENDPOINT`（指令执行地址），对象本身不可修改。

### 单例安装

两个脚本都只在 `window.PortalAPI` 或 `window.PortalInstructionClient` 尚不存在时安装。重复加载保留先前的实例，因此较早的调用方、测试替身和后加载的模块看到的是同一个对象。
