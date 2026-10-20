# Portal Agent 上下文注入规范

本规范约束 TFI Portal 中需要 TFI Portal agent 身份或工作规则的模型调用与
agent 入口。

## Portal 上游统一物化

Portal 调用模型前，统一使用
`management_portal.agent_prompt_context.portal_system_instructions` 把 role、
task-specific Skills、是否允许 sub-agent 和注入策略物化为完整 system instructions。

角色集合由 `management_portal.agent_skill_context.PORTAL_ROLE_FILES` 定义：当前为
`resident`、`development`，另有不附加角色文件的 `general`。主文件和对应角色正文由
服务端读取、按 SHA 去重并注入；任务 Skill 保留为明确引用，按需读取。个人 AMA 和资源讨论
使用 Resident 的执行入口，不以页面名称增加系统角色。

role 是 Portal 上下文层信息，只允许存在于 Portal 上游构造器及其调用点。
`management_portal.execution` 不得定义固定 role 列表、读取 role Skill、选择 role
分支或生成 TFI Portal system prompt。

所有 AI 调用最终提交
`management_portal.execution.contracts.ExecutionRequest`。请求只把已经物化的
`SystemInstructions.parts` 作为不透明数据传给执行层。每个部分包含正文、发送时机和位置。
调用点不得使用 `AGENTS.md`、独立通用 system-prompt 文件或 Resident 私有入口替代统一构造器。

## 附加 Skill 的路径

任务 Skill 使用 Resident 的路径规则：名称仅允许小写字母、数字和短横线，长度为
2–79，首字符为字母或数字。名称解析为 `${share_root}/Skills/<名称>/SKILL.md`；显式路径
与按当前调用者解析的 Skill 资源 ID 最终也必须指向该目录内存在的 `SKILL.md`。
检查真实路径，符号链接不能绕过目录范围；无效名称、越界和文件缺失在调用模型前拒绝。
最多处理前 12 项，按首次出现顺序去重。

## 会话生命周期

Portal 上游明确选择：

- 无状态模型请求每次都是新调用，因此每次附带 system instructions。
- Resident 默认使用 `standard` 预设；共用规则、角色规则、节点 `resident.md`、运行身份与授权是不同注入部分，各自按设置发送。不要把节点身份文档当作共用角色文件。
- 通过 Prompt 注入面板，或 `GetResidentSettings`（结果的 `prompt_injection`）、`UpdateResidentSettings`（`changes.prompt_injection`）、`PreviewPromptInjection` 读取、修改和预览各部分的时机。
- Provider 声明系统位置跨压缩保留时，压缩后不重复发送该位置的首轮规则；Codex 属于这种情况。prompt 位置仍按各自时机发送。
- Claude 与 DSH 每轮需要完整系统提示；Provider API 将 prompt 位置的部分写入最后一条用户消息。

Execution 根据会话首次注入、各部分时机、压缩位置和恢复检查点计算注入计划；它不得知道
instructions 属于哪个 Portal role。创建、Fork、显式压缩与释放操作本身不发送系统指令。

Fork 子 Resident 创建自己的 Provider session，在首轮注入自己的身份和规则。
RestartSession 的初始化轮次按目标 Provider 构造完整注入计划。
恢复和失败续跑沿用原轮次的计划，不重复附加已经送达的部分。

## Portal 与 Execution 职责

Portal 业务代码拥有完整流程：读取业务对象、执行非 AI 逻辑、组织 Prompt、保存
execution binding、解释结果并写回业务状态。只有真正的模型调用进入
ExecutionService。

不得把 Portal 业务流程包装为外层 process Execution，再由它提交内层 AI Execution。
不得为 Paper、Repo、Watchboard 或其他业务建立专用 Execution Worker。
执行层真正的 Worker 只有通用 `ExecutionWorker`。

AI Execution 持久运行。Portal 业务 job 只保存全局唯一的 `execution_id`；Portal 重启后
重新连接同一个 Execution 并恢复结果写回，不重复提交已绑定请求。

## 系统层服务与模型调用

需要判断与写作的定时工作由 Resident 亲自完成，确定性维护通过 Lineage 事件与 `RunScript`
组织，具体见 `${share_root}/Skills/scheduled-work/SKILL.md`。业务代码确需模型能力时，使用
当前 Portal 上游构造器准备上下文，再按 `ExecutionRequest` 合同提交 `ExecutionService`。
Resident 自动化使用公开 HATG 能力；不依赖旧共享 Agent 目录中的私有客户端。

系统服务可以保留业务说明和工具实现，但不得保存另一份 TFI Portal 共用 system
prompt，也不得直接调用 provider client。

## 验证

相关改动至少验证：

- Portal 上游物化的 system instructions 只包含一次统一 Skill 入口。
- 同一 session 的首轮、后续轮次与压缩后轮次均按各部分的预设时机附加。
- 选择每轮发送的部分在第二轮继续发送；跨压缩保留系统位置的 Provider 不因压缩重复发送首轮系统规则。
- 预览的系统位置与消息前后位置和实际发送一致。
- Execution package 不包含 Portal role 列表、role 字段或 Skill 渲染器。
- Portal 业务源码不把业务 command 包成外层 Execution，也不定义专用业务
  Execution Worker。
- 有 execution binding 的业务恢复不重新 submit，只等待原 execution 并完成写回。
