---
kb_id: decision_c604274e21dd
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 10
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_wall08_round4_relay_independent.py
  - ${code_root}/tests/cases/test_wall08_round4_auxiliary_gaps.py
  - ${code_root}/management_portal/ai_socket_relay.py
  - ${code_root}/management_portal/resident_handoff.py
  - ${code_root}/management_portal/ai_provider_registry.py
  - ${code_root}/management_portal/model_api_store.py
  - ${code_root}/systemd/portal-ai-socket-relay.service
  - ${code_root}/scripts/portal_service_units.py
  related:
  - decision_725dedbe89b2
  - decision_6c2f91a7d84e
  - decision_6ad0384e020d
  - decision_85222f2d316b
  - decision_0f57656a2acd
  supersedes: []
  derived_from: []
---

# Portal LAN AI Socket Relay 的请求与安全协议

AI Socket Relay 为受信任局域网设备提供模型调用入口。设备发送已登记调用项及本次消息，Relay 通过 ExecutionService 提交独立的无状态 Chat Completion，再把上游完整 JSON 返回。设备不保存模型密钥；不同请求的消息、模型和生成参数独立，不继承其他设备或前一次请求的内容。

## 调用项与执行边界

调用项（resource）是 Relay 自己的路由标识，登记在 PostgreSQL 表 `model_apis.relay_resources` 中：`resource_id` 为主键，`resource_json` 固定服务商（provider）和模型（model），`enabled` 为 `false` 时停用。每次请求重新读取登记，修改后不需要重启 Relay。这张表不设 ISA 指令或页面，由维护 Portal 的 agent 用 Portal 数据库账号直接写入，同时填写 `updated_at` 与 `updated_by`。设备不能修改接口地址、密钥、请求超时或重试预算。

Relay 在 LLM API Registry 中选择已启用的 openai_compatible_chat 条目，优先使用 ID 为 <服务商>_primary 的条目，否则取该服务商的第一项。找不到条目时转发失败。模型接口登记与生成协议见 decision_6c2f91a7d84e。

每次调用使用 actor=ai-socket-relay，以本次固定模型、messages、生成参数创建一个新的 ExecutionRequest。ExecutionService 负责排队、账号容量、Worker 执行和结果保存（decision_6ad0384e020d）。请求采用 stateless Session，不创建 Resident、Lineage 或 Conversation；不注入 Resident 核心规则。messages 中设备提供的 system 消息按原样传输。

messages 取 payload.messages 中的对象项；没有 user 消息时补入 Run AI relay chat completion.。生成参数取 payload 中除 model、messages、stream 外的字段，仅作为上游请求参数，不覆盖 Registry 连接控制。超时取登记的 timeout_s（缺省 120 秒），重试取 max_retries（缺省 0）。Relay 等待执行的上限为各次请求超时预算之和再加 30 秒排队余量；等待超时返回错误，Execution 的生命周期仍由执行服务管理。连接断开或等待失败不会自动重发请求，设备自行决定是否重试。

## 连接与帧

来源地址白名单由 IP 或 CIDR 网段组成，连接建立后、读取正文前检查；空白名单拒绝全部连接。监听地址默认是 127.0.0.1:18484，实际监听与白名单由部署配置决定。无法解析的白名单项使服务启动失败。

一帧由 4 字节大端无符号长度和相应长度的 UTF-8 JSON 对象组成。单连接串行处理请求；写完响应后读取下一帧。每帧默认上限 4 MiB，可由 PORTAL_AI_RELAY_MAX_BYTES 配置，最小 1024 字节。每帧从读取长度到完成正文共享 30 秒截止时间，持续少量发送也不能延长截止时间；读超时或连接断开会关闭连接并释放名额。发送响应也有超时。

连接名额由 PORTAL_AI_RELAY_MAX_CONCURRENT 指定，默认 16、最小 1，从连接接受到关闭期间占用。超过上限立即返回 429 并关闭；对端提前关闭不会使错误响应路径再次抛出异常。

## 版本 1 消息

请求示例：

```json
{"version":1,"request_id":"device-1","resource":"registered-model","payload":{"messages":[{"role":"user","content":"你好"}]}}
```

- version 必须为整数 1。
- resource 去除首尾空白并转小写后查登记。
- payload 必须为对象。model 省略时填入登记模型；指定不同模型时拒绝。
- payload.stream 为真时拒绝；仅支持完整的非流式响应。
- request_id 可省略，响应原样返回；它只供设备关联请求，不提供幂等保证。

成功响应：

```json
{"type":"response","request_id":"device-1","resource":"registered-model","provider":"registered-provider","status":200,"content_type":"application/json","body_json":{}}
```

body_json 是该次 Execution 成功结果中的上游完整响应。执行失败不能伪装成空的 200 响应。

错误帧包含 type=error、整数 status 和简短 error。解析后发生的错误带 request_id；调用项相关的 403 另带 resource。读取请求前拒绝连接时没有 request_id。

| 状态 | 原因 | 连接处理 |
| --- | --- | --- |
| 400 | 空帧、超长帧、无效 UTF-8/JSON、非对象或嵌套过深 | 关闭 |
| 400 | 版本不符、缺少调用项、payload 非对象、要求流式输出 | 可继续 |
| 403 | 来源 IP 不被允许 | 关闭 |
| 403 | 未登记调用项或模型不匹配 | 可继续 |
| 429 | 连接名额已满 | 关闭 |
| 502 | 登记读取、模型连接或 Execution 失败 | 可继续 |

读帧超时直接关闭连接。错误响应只暴露固定说明，不包含上游异常正文、密钥或数据库连接参数。

## 日志与部署

日志保存来源 IP、请求 ID、调用项、模型、状态及耗时；错误仅附异常类型。请求 ID、调用项和模型使用转义并限长的日志表示，避免控制字符和换行污染日志。消息正文与凭据不写日志。

Relay 是独立的 systemd 用户服务，使用 Portal runtime-venv 中的 Python 和主机 PostgreSQL 环境文件，经 Execution RPC 提交请求。ExecutionService 重启不会连带停止 Relay；Relay 异常退出由 systemd 在 2 秒后重启。服务模板由共同单元安装器管理。

版本 1 的信任边界是局域网、防火墙及来源白名单，不包含应用层认证或 TLS。增加认证、加密或流式输出需要明确的新协议。
