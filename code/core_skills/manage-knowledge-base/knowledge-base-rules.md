# 知识库写入规范

## 位置与权限

- 项目知识库位于 `GetProject` 返回的实际项目目录下的 `knowledge_base/`。
- 项目 ID 和目录必须与 HATG ISA `GetProject` 返回的注册信息一致，不根据显示名称猜测。
- 用户只要求检查、回答或预览时不得写入。用户明确要求创建、修改、整理、合并或写回时，可在目标范围内执行。
- 修改前读取完整目标条目及相关定义、决策和替代条目。

## 身份与文件名

- 新的普通知识资源通过 `RegisterResource` 创建，由提供者分配 ID、规范文件名和版本审计；已有身份保持不变。Skill 等不能统一登记的类型遵循各自提供者流程。
- 普通知识条目使用 `kb_id: <type>_<12位小写十六进制码>`。
- 历史知识 TODO 使用 `id`；Skill 包入口使用 `metadata.id`，单篇项目知识 Skill 使用 `kb_id`。这些是不同存储形态，不相互改写。历史格式存在不代表当前接口允许创建，见 `./archive-todo-item.md`。
- 普通知识条目文件名为 `YYYYMMDD__<type>__<Title>__<kb_id>.md`。
- 文件名前缀日期与 `created_at` 一致，文件名末尾 ID 与 YAML 头部身份字段一致。
- `type` 与 ID 前缀一致；存储目录按当前知识目录映射确定，不要求 folder 与类型键同名。

## 正式读写入口

先读取 `GetResourceType` 和相关指令 schema，确认类型与有效能力。知识条目创建用 `RegisterResource`，fields 中给 `project_id`、`title`、`body`；当前选填项为 `importance`、`metaness`、`ai_generated`、`links`、`status`。不提交手造 kb_id、path、version 或带 YAML 头部的 body。服务补一级标题；自带一级标题时须与 title 一致，不再依赖加粗冒号标记抽取标题。

更新前通过 `ReadResource` 取得完整文本和版本，用 `UpdateResource` 的 changes 提交 `text` 与 `expected_version`；版本冲突后重读并合并。保留 ID、类型和创建审计；提供者负责递增修改版本，调用者不预先加一或代写执行者。新增了什么与最终落盘结果以回读为准。

下面是理解和检查存储格式的说明，不是绕过服务手工造记录的步骤。TODO 等专用格式还需读取对应分支；正文类型转换不是修改 ID 前缀，应按当前提供者能力建立新资源及关系。

## 普通条目 YAML 头部

```yaml
---
kb_id: <type>_<code>
type: <type>
project: <ProjectId>
created_at: <YYYY-MM-DD>
version:
  number: 1
  created_at: <ISO-8601 UTC>
  created_by:
    user: <发起创建的 User ID 或 "">
    agent: <执行写入的 Resident/Agent ID 或 "">
  updated_at: <ISO-8601 UTC>
  updated_by:
    user: <发起本次修改的 User ID 或 "">
    agent: <执行本次修改的 Resident/Agent ID 或 "">
importance: <1-10>
metaness: <可选，1-10 的整数>
ai_generated: false
links:
  source: []
  related: []
  supersedes: []
  derived_from: []
---
```

- `importance` 缺失或无法判断时使用 5。
- 只有正文完全由 AI 生成时，`ai_generated` 才为 `true`；包含人类输入、确认或口述时为 `false`。
- `version` 是条目的版本审计真相。创建时 `number` 为 1，两个时间相同，`created_by` 与 `updated_by` 相同；User 记录请求或确认创建的人，Agent 记录实际执行写入的稳定 Resident/Agent ID。某一方确实不存在或无法从执行上下文确定时使用空字符串，不得猜测，也不得用 session/thread ID 代替 Agent ID。
- 正式修改会保留 `version.created_at` 和 `version.created_by`，由服务递增 `number` 并记录实际修改时间与操作者。调用者回读核对这些结果，不手动伪造审计。时间使用 ISO-8601 UTC。
- 不在通用 YAML 顶层添加 `updated_at`、`created_by`、`updated_by`、`aliases`、`tags`、`title`、`authors`、`year`、`venue`、`url` 等自定义字段；版本审计字段只放在 `version` 中。
- `status` 的可用值按目标提供者判断；历史记录中的状态枚举不能当成所有类型的通用状态机。普通知识可用 `obsolete` 标记已过时；不要仅因 Plan 实施完成就提交接口不支持的状态。
- 待办的完整结构见 `./archive-todo-item.md`。

## 抽象层级 metaness

`metaness` 是普通 YAML 知识类型共用的可选顶层字段，描述正文讨论对象的抽象层级。它适用于采用普通头部的各类知识，包含 Design、Plan、概念、思考、审查及使用 `id` 的待办；不为专用格式强行添加普通头部。

- 显式值必须是 1 至 10 的严格整数。布尔值、浮点数、字符串、空值、集合和越界整数均非法；例如 `true`、`3.0`、`"3"`、`null`、`0`、`11` 都应报错。
- 缺失合法，不要求补齐。WALL 对缺失值采用显示默认值 1，不回写源文件；显式非法值必须给出可定位诊断，不能冒充缺失值或自动截断、转型、猜测修正。
- 阅读完整正文后按主要讨论对象评分，不能按标题、目录、类型或关键词机械批量赋分。一篇文档提到规范或包含多个模块，并不自动提高分数。
- `importance` 继续表达重要程度；`metaness` 只表达抽象层级。未来时间性也与层级独立：具体的新功能仍可为 1 或 2，已经稳定的规范治理也可为 6。

评分锚点用于统一判断，不作为配额：

| 分值 | 主要讨论对象 |
|---|---|
| 1 | 具体功能、操作、事实或局部行为 |
| 2 | 一个子系统的模型、流程或直接工程方案 |
| 3 | 多个子系统之间的共同机制、边界或整体工程设计 |
| 4–5 | 跨领域可复用的设计原则、工作方式或演化方向；正文确实解释这些抽象 |
| 6–7 | 规范的规范、知识组织或设计方法的治理与评价规则 |
| 8–10 | 跨多套方法与治理体系的元模型，以及这些元模型的构造和评价原则 |

普通工程叙述通常在 1–3。高分无优先实施、质量更好或价值更大的含义。只在用户授权评分或内容修改确实需要调整时写入主观评分，并遵守版本审计；后台校验只报告格式问题，保留缺省与有效评分。

## 链接与附件

- `source` 记录外部来源、原始材料或非知识库文件。
- `related` 记录平级相关条目或对象。写上研究地图的方向 ID（`direction_*`）时，条目成为该方向的产出，显示在地图的方向时间线上。
- `supersedes` 记录当前条目取代的旧条目。
- `derived_from` 记录整理或推导来源。
- 知识库条目只写稳定 ID；网页写网址；普通文件写绝对路径；Agent 写 Agent ID。
- 网址或普通文件可追加 `#L12` 或 `#L12-L20`。知识库条目在 `links` 中不追加行号。
- 条目附件放在与 Markdown 同级、名称与条目 ID 完全相同的目录中，正文用相对路径引用。

## 修改、替代与删除

- 修改正文时保留条目 ID、创建日期、`version.created_at`、`version.created_by` 和已有有效 YAML 头部；通过正式更新入口由服务递增版本并记录修改审计，不把编辑日期伪装成创建日期。
- 当前条目取代旧条目时使用 `supersedes`，并把旧条目标记为 `obsolete`；不要让两个互斥版本同时表现为当前事实。
- 合并前保留各条目的独有信息和必要链接。
- 不根据相似文件名直接删除条目或附件。用户未明确授权删除时，先报告候选和依据。
- 修改后核对 YAML 头部、文件名、目录、引用、附件和相关条目的口径；有项目级构建或检查工具时运行针对性验证。

实验（`experiment`）、专利（`patent`）和项目技能使用各自专用规则，不套用普通条目结构。
