---
kb_id: decision_6b9e42f1c8a7
type: decision
project: AI员工
created_at: '2026-07-17'
version:
  number: 10
  created_at: '2026-07-17'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-11T18:30:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_3913409c89ae
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/management_portal/execution/native_options.py
  - ${code_root}/systemd/portal-kimi-code.env
  - ${code_root}/systemd/portal-kimi-code-config.toml
  - ${code_root}/management_portal/kimi_code_client.py
  - ${code_root}/management_portal/kimi_code_server.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/tests/cases/test_kimi_code_client.py
  - ${code_root}/tests/cases/test_kimi_code_server.py
  related:
  - decision_d40f59f5508f
  - decision_6c2f91a7d84e
  - decision_3dd52c0692c1
  - decision_e587fe1c472e
  - decision_ef1d6c925955
  - decision_3e8c2a7f91d4
  - decision_c47a13e8b962
  - decision_6ad0384e020d
  - decision_b1ab45c4b3ec
  supersedes: []
  derived_from: []
---

# Kimi Code Provider 的 Session 接入

Kimi Code Provider 通过 Kimi Code 的 HTTP 会话接口与 WebSocket 实时流执行 Resident 请求。ExecutionService 拥有请求、输入投递与恢复状态；Kimi 服务拥有原生会话和进行中的 prompt；AgentData 保存 Portal 已观察到的规范消息、工具事件、用量与可移植上下文。

## 路线、配置与部署

Provider ID 为 `kimi`。Kimi 服务使用 `PORTAL_LLM_API_REGISTRY` 指定的 LLM API Registry 和 `PORTAL_KIMI_CODE_PROVIDER_PROFILE` 指定的路线，默认路线为 `kimi_primary`。网络探测使用这条路线的 `base_url` 加 `/models`，与实际服务地址保持一致。

`systemd/portal-kimi-code-config.toml` 是模型目录的唯一来源，第一项为默认模型；`PORTAL_KIMI_CODE_DEFAULT_MODEL` 可以选择模板中已有的模型。执行适配器与服务启动都校验模型，未登记模型直接拒绝。运行配置按模板写入 Kimi 工作目录，密钥从登记的凭据引用读取，不进入消息或日志。运行时程序通过 `kimi-code-current` 选择，安装与切换见 decision_e587fe1c472e。

Kimi 服务由 `portal-kimi-code.service` 管理。当前 HTTP 接口在本机回环地址提供服务，保持现有无需鉴权的运行方式；更改这项隔离规则需要单独决定。Kimi 状态和凭据目录按 Provider 声明纳入受限脚本的遮蔽范围。

## 公共执行流程

`KimiBackend` 继承 `NativeSocketBackend`。公共流程负责上下文种子、操作分派、事件交付、输入绑定、取消、交接和错误续接，以及结束后的用量与压缩记录。Kimi 客户端负责把这些动作映射到原生 HTTP 与 WebSocket 协议，工具网关只在本次执行期间绑定。

只有 `create` 或 `turn` 可以建立缺失的原生会话。`fork` 和 `compact` 缺少历史时明确拒绝，`release` 不会创建空会话。跨 Provider Fork 与 RestartSession 使用 AgentData 中的可移植上下文作为新会话的种子，来源系统指令不作为用户历史复制。

一轮输入提交后，Kimi 返回原生 prompt ID。适配器把该 ID 写入输入投递与恢复记录，后续事件和控制使用同一身份。运行中追加输入由 Kimi steer 接口提交；客户端记录追加 prompt 与目标轮次的对应关系，处理其终态时不会提前结束父轮次。

## 实时事件与回答

客户端连接 `/ws` 接收实时事件，转换为统一的消息、推理、工具和子 agent 事件。消息使用 Kimi 的稳定块 ID；只有完整块进入规范历史，文本增量用于过程展示。子 agent 事件驱动独立步骤序列与进度卡片。客户端已经发送的 `turn/completed` 直接交给 Harness，Backend 不再补发第二次完成事件。

连接断开后尝试重新连接；同一断开期间只报告一次 `portal.stream_gap`。无法连续恢复的流会使尚未完成的文本失效，并发出 `provider/traceGap`。状态快照用于判断 prompt 的运行状态。Worker 脱离后重新附着，在该 prompt 结束时按原 prompt ID 读取持久消息中的完整回答，以新的完整消息追加到 AgentData；已显示的实时内容保持不变。恢复条目使用稳定身份，重复恢复不会覆盖先前消息，也不会把其他 prompt 的回答或历史推理带入本轮。

## 交接、错误与取消

Worker 交接请求使客户端脱离并抛出 `KimiCodeDetached`，原生 prompt 继续运行。新 Worker 按检查点中的 prompt ID 调用恢复路径。恢复路径先补回同一 prompt 的完整回答；完整实时回答已经送达时不再追加同一回答。原生 prompt 结束后仍没有可交付回答，才报告恢复事件并以 “continue” 续接；原输入不重发。临时网络失败进入公共 Provider 恢复循环，受恢复预算约束。

取消调用 Kimi 的会话 abort。压缩等待同样检查取消和交接：取消终止操作，交接释放 Worker；服务不可用时不会无限占住执行槽。

## Portal 工具

`kimi_portal_tools` 和 `kimi_tool_gateway` 通过 MCP 与会话 hook 把模型工具调用对应到本轮执行。Worker 依据本轮授权目录执行工具并完成 Harness 后处理。缺少可归属的会话时工具拒绝执行。工具在调用时实时交给 Harness，不等轮次结束才报告。

Resident 的 `allow_subagents` 决定是否提供 Agent 与 AgentSwarm。工具开关、系统指令和本轮身份随请求传入；工具网关在轮次结束时解除绑定，后续轮次重新登记。

## 压缩与用量

自动压缩和手动压缩都只记录 wire 日志中实际存在的压缩历史。`compact` 在已有会话上执行，比较操作前后的原生压缩引用；没有新压缩时返回 `nothing_to_compact`，不写入无法用于 Fork 的不透明检查点。压缩记录包含可移植的替换历史，重复引用不会重复登记。

每轮结束或失败后的清理阶段，适配器从原生记录读取模型请求用量，逐条写入公共账本。某条用量事件发出失败时记日志并继续处理其余请求。公共基类负责读取失败的处理与日志，Provider 只负责原生格式解析；不以会话累计值推算本轮消费。

实现入口是 `execution/backends.py`、`kimi_code_client.py`、`kimi_live_events.py`、`kimi_code_server.py`、`kimi_portal_tools.py`、`kimi_tool_gateway.py` 和 `execution/provider_turn_usage.py`。用量、压缩和上下文继承的公共规则见 decision_b1ab45c4b3ec；运行时安装见 decision_e587fe1c472e。
