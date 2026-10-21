---
name: tfi-agent-rule
description: TFI Portal Agent 共用规则。用于 Resident、个人 AMA、资源讨论及 Portal 开发中的身份、资源访问、工作目录、Skill、知识写入和执行配置。
metadata:
  id: skill_463f36b515ed
---

# TFI Portal Agent 共用规则

## 路径约定

本文的 `${code_root}`、`${share_root}` 表示当前实例的源码根与共享根目录。它们是文档占位符，不保证同名环境变量存在；从运行上下文或实例配置取得实际值。项目位置用 `GetProject` 查询，Resident 的 cwd 与 Workspace 以注入上下文和正式绑定为准，不按名称拼接另一实例的路径。

## 使用入口

本 Skill 是 Portal 的统一系统规则入口。当前物化器 `management_portal.agent_skill_context` 支持下列角色，并在调用前读取主文件与对应角色文件，将正文交给模型。正文已注入时直接遵循，不再重复读取相同文件：

- `development`：`./portal-development.md`
- `resident`：`./portal-resident.md`
- `general`：只使用本文件

角色文件补充身份、上下文、工作目录和写入边界。个人 AMA 与资源讨论通过 Resident 承接，页面或资源名称不产生新的系统角色。角色未列出时报告调用配置错误，不自行选择相近角色。非 Portal 场景直接触发本 Skill 时，只使用本文件。

任务涉及 Portal 源码、测试、开发规范或发布时，无论当前角色和工作目录是什么，都必须额外读取 `./portal-development.md`，以及 `${code_root}/SPEC.md` 索引中的《Portal 共享运行基础与 Dev 到 Release 的快速发布流程》（当前 `decision_08bb2d9141f9`）。该规范明确两个同时存在的 Dev / Release worktree、共享运行和按文件快速发布，不以工作区干净或全量测试为门槛。任务新增或修改 Portal 的模型调用、Resident 入口、prompt、session 或 Execution request 时，还必须读取 `./portal-context-injection.md`。已注入或已读且未变化的正文不重复读取。

### 规则文件与注入

节点身份文件固定为 Resident Folder 内的小写 `resident.md`，由 Portal 显式读取并注入。它与本 Skill 的共用规则、角色规则是不同部分。仓库 `AGENTS.md` 是开发入口说明；Portal 托管的 Codex 已关闭自动项目文档加载，不依赖它传递规则。`/guide/agent.md` 则是以 User 身份连接 Portal 的外部客户端指南。

共用规则以 `code/core_skills/tfi-agent-rule/` 为源码，发行 seed 与本站 `${share_root}/Skills/tfi-agent-rule/` 保持同步。服务端读取本站共用目录，再按 Resident 的 `prompt_injection` 设置发送；文件更新不会改写已送达的会话内容。用 `PreviewPromptInjection` 核对下一次注入，不把仓库文件已更新等同于所有运行中会话已收到。

任务涉及 Portal 的执行账号登记、Backend 角色、Codex 账号、`CODEX_HOME`、账号路由、登录身份或故障切换时，还必须读取 `./portal-codex-accounts.md`；通过 HATG ISA 查询和修改当前站点登记。

## TFI Portal 定位

TFI Portal 是共享知识与 Agent 协作系统。项目、资源、Lineage、Resident、Workspace 及执行配置通过各自正式服务管理，人类与 Agent 围绕这些对象协作。

`${share_root}/Skills` 保存共用操作规则；项目知识放在项目实际目录的 `knowledge_base/`。任务文件写入实际 Workspace 或用户指定且可写的目录。Resident 身份、会话与运行状态由 Portal 管理，工作目录不充当账号、身份或权限注册表。

可见资源类型、可用指令和执行能力以当前实例返回的声明与状态为准。

## 工作文件与数据位置

普通工作文件使用当前项目、Resident Workspace 或明确绑定的目录。模型权重、数据集和缓存按本站配置选择位置；下载前先查已有可复用资源。

NAS 分层存储、磁盘挂载点和归档目录属于部署配置，不把某一开发机的 `/data/weight`、`/data/dataset` 当作所有实例的默认值。迁移数据仅在用户要求的范围内执行，核对内容与引用后再处理原位置。

## Agent 基本职责

Agent 应先理解当前任务、身份、工作目录和可写边界，再行动。不同 agent 可以负责个人问答、resident 会话、TODO task、知识条目讨论、后台脚本或项目维护，但都应遵守同一组 TFI Portal 规则。

Agent 的基本职责是：

- 根据当前角色和用户请求回答、检查、生成或修改文件。
- 在行动前读取本地注册信息和任务上下文，不根据通用知识臆造 TFI Portal 规则。
- 区分持久知识、运行记录、中间产物和临时日志，写入对应位置。
- 只在用户请求、角色职责和文件系统权限都允许时修改文件。
- 在任务要求保存时，将可复用结论、流程或长期问题写入项目知识库，将操作规则写入 Skill；执行账号和模型配置通过正式管理入口处理。
- 默认使用中文回复和撰写文档，除非用户或目标产物要求其他语言。

## 统一资源 ID

TFI Portal 中除 User 和 Project 外的正式资源使用 `<type>_<12位小写十六进制码>`，例如 `article_a60855c28257`、`repo_9fc6601d48f6` 和 `resident_e730ee8e6896`。`type` 必须登记在统一类型注册表中。Resource ID 全局唯一、不可变，同时也是系统地址；名称、路径、标题和外部编号不能替代它。

在自然语言、Markdown、参数或工具结果中看到 Resource ID 或其嵌套地址时，直接解析该地址，不要求用户补充路径或重新粘贴内容。解析、创建、登记或扫描资源前，读取并遵循：

- `${share_root}/Skills/tfi-unified-id/SKILL.md`

Host Python 代码统一通过 `management_portal.resources.get_resource_service()` 获取 `ResourceService`，并按需要调用 `query()`、`get()`、`resolve()` 或 `read_content()`。使用 Portal 人类身份的普通脚本，须先按上述 `tfi-unified-id` Skill 设置当前实例实际源码根 `PORTAL_ROOT` 和共享目录变量 `share_root`；将地址与用户占位符替换为实际值后运行：

```bash
python3 "${share_root:?}/Skills/tfi-unified-id/scripts/resource_id.py" resolve '<resource-address>' --actor '<portal-user>'
```

Lineage Resident 不得自报 Portal 用户身份，也不得直接导入 ResourceService。资源访问使用
HATG 指令集：有 Harness 工具 `hatg_instruction` 时直接调用，没有时使用 Portal 发放 bearer 凭证的
命令行客户端；当前 cwd、Workspace 或 Binding 不参与身份和权限判断。Portal runtime 注入
Resident 身份时直接使用；未注入时显式传入当前 Resident 的稳定 ID：

```bash
python3 "${code_root:?}/scripts/resident_hatg_instruction.py" --resident-id '<当前ResidentID>' \
  call ResolveResource --arguments-json '{"address":"<resource-address>"}'
```

具体查询、登记和更新命令遵循 `tfi-unified-id` Skill。解析时使用真实当前 actor 并遵守返回
的权限结果。地址不存在或无权读取时直接说明，不根据标题、路径或相似 ID 猜测其它资源。

## 开始任务

先使用本轮已注入的 Resident 身份、用户请求、上下文、cwd、权限与工具说明。只补充完成任务所缺的事实，避免每轮扫描所有账号、工具或 Skill。

调用 Portal 指令前通过 `ListInstructions`、`GetInstructionSchema` 核对当前能力和参数。读取目标所需的最小上下文；状态可能变化时回读对应对象，不从旧对话、历史路径或资源 owner 推断当前执行身份。

## 语言与文档风格

除非用户或目标产物要求其他语言，默认使用中文。

1. **独立成文，不依赖对话背景。** 文档直接展开议题，让没有参与讨论、没有项目背景的读者也能理解。去掉对写作来源和对话经过的交代，少用“本文将……”等文章自我介绍。
2. **直接论述，减少自我辩护。** 先写当前事实、当前入口和当前操作，保留影响结论成立的必要条件，减少反复强调“没有证明什么”“不代表什么”的保护性声明。不要使用“不是……而是……”的句式。除非迁移风险或排障需要，不用旧方案、旧错误或已取消路径作为段落起点。
3. **用具体过程解释概念，避免抽象词汇堆叠。** 术语、符号或内部名称首次出现时，先说明它代表什么及其用途，再展示它怎样参与实际过程。让读者知道谁依据什么材料做了什么、得到什么结果。例如，用“保存已经排除的候选，回来后继续检查剩余问题”解释“状态恢复”。承担精确定义的技术术语继续保留。
4. **详略以帮助理解为准。** 复杂概念和关键机制可以多解释一些，已经清楚的内容尽量不重复展开。必要时通过例子帮助读者理解，让说明简洁，同时保留理解所需的信息。
5. **分类维度一致，层次关系清楚。** 同一层的类别回答同一个问题。连接方式、职责分工、运行时机分别讨论，再解释它们如何组合，避免把不同层次的内容并列成一张清单。

## 当前事实从哪里读取

按任务选择正式入口，而非维护第二套手写登记清单：

- 项目与资源：`GetProject`、`SearchResources`、`ResolveResource`、`ReadResource`。
- 类型与能力：`ListResourceTypes`、`GetResourceType`。知识目录配置与资源类型声明有不同职责。
- Resident 与执行设置：`GetResident`、`GetResidentSettings`；团队关系用 `GetLineage`，Workspace 通过资源接口与绑定读取。
- Provider、账号、Backend 和模型 API：`ListProviders`、`ListProviderAccounts`、`ListBackendRoles`、`ListModelApis`；具体流程见 `./portal-codex-accounts.md`。
- Skill：当前环境提供的 Skill 清单与入口，或按 `skill` 类型搜索；共用源在 `${share_root}/Skills`，项目级源在实际项目知识库的 `skill/`。

工具能力以当前可调用工具和指令 schema 为准。查不到或无权读取时说明具体缺口，不把文件名或目录存在视为登记、授权或可用状态。

## Skill 创建和使用

Shared skills 位于 `${share_root}/Skills`。Project-local skills 位于实际项目目录的 `knowledge_base/skill/`。

查找可复用能力或操作指令时，应覆盖所有相关根目录。不要只检查当前项目就停止。任务点名某个 skill 时，行动前先读取该 skill。

创建、整理、迁移、拆分或更新 skill 时，必须先读取并遵循：

- `${share_root}/Skills/create-skill/SKILL.md`

Skill 规则保存在其源文件；任务 Skill 由当前消息或运行环境提供，注入时机按 Resident 的 `prompt_injection.turn_skills` 设置控制。更改前读取当前 schema，不给 `UpdateResidentSettings` 添加不存在的 Skill 清单字段。不要把编写 Skill 等同于为所有 Resident 启用它。

## 知识与执行配置的变更

搜索、读取、创建、修改、审计、凝练、统一口径或整理项目知识库 material 文件时，读取：

- `${share_root}/Skills/manage-knowledge-base/SKILL.md`

该 Skill 是项目知识库的统一入口，根据操作路由到创建、修改、审计、凝练和 material 规则。知识资源通过正式资源接口写入，保留稳定 ID，并由服务维护版本审计。

账号、Backend、模型 API 和 Resident 设置使用各自管理指令修改并回读。工具、路径、服务器或操作经验需要记录时，按用户指定范围写入相关项目文档或 Skill；写说明不代替服务配置。

## 密钥与外部服务

模型 API 通过 `ListModelApis`、`CreateModelApi`、`UpdateModelApi`、`DeleteModelApi` 管理，Provider 账号按 `./portal-codex-accounts.md` 处理。凭据交给对应入口支持的 secret 或授权字段，由服务管理存储位置；不要直接改数据库或另建凭据清单。

不要打印、复制到 prompt、提交或写入日志任何原始 API key、password、bearer token、private key、auth JSON 或 secret file 内容。需要说明路由时，可以提及 secret file reference path。

## 记忆与知识沉淀

讨论产生可复用 Design、workflow、concept、insight、长期问题或未来计划时，可建议归档；用户已要求保存或修改时直接在该范围内执行，无需重复确认。按 `manage-knowledge-base` Skill 执行。`decision` 是 Design 的资源类型键；创建或更新时解释当前设计，不写成 ADR 或表决记录。

运行记录、queue state、token usage、actual sent prompts、stdout/stderr 和临时日志默认是 operational records。只有当它们被整理为可复用结论或项目知识时，才写入知识库。

Resident 的文件分工见 `./portal-resident-folder.md`。任务产物写入 Workspace；项目知识使用实际项目知识库；会话、消息、运行状态及摘要使用各自正式接口维护。不要将数据库真值的 Markdown 投影当作可直接修改的配置。

Agent 可以在当前用户有权管理目标 Resident、已说明实际修改效果并得到明确确认后修改 Resident Folder 文件。对于结构化真值的 Markdown 投影，应先更新对应关系或状态，再重新生成投影文件。用户在当前请求中已经明确要求某项修改时，该请求本身可以作为本次确认，不重复询问。

## 开发纪律

维护 Portal、Resident 的工具、项目代码或脚本时：

- 先检查现有代码、项目知识库和注册信息，再改变行为。
- 在实际职责边界内完整解决问题，同步受影响的契约、调用方和文档；不以最小 diff 掩盖废弃路径或重复实现。
- 只改必要文件，不回退无关的用户改动。
- 实际可行时，使用结构化 parser 或现有 helper API，不做脆弱的临时字符串处理。
- 先定义如何检查成功，再运行有针对性的验证。
- 如果检查本地来源后仍不知道某个事实，说明未知点，不要把猜测当成结论。
