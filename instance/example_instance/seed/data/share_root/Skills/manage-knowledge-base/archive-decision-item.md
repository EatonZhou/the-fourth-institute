# Design（`type: decision`）条目归档规则

## 类型语义

`decision` 是为兼容现有资源 ID、目录和检索接口而保留的类型名。该类型的正文语义是系统 Design：解释一个稳定设计如何工作、由谁负责，以及它与相邻设计如何协作。它不是 ADR，也不以记录某次表决、候选方案或“为什么选了 X”作为主要目的。

## 适用场景

- 架构、协议、数据模型、组件职责、交互流程或运行机制已经稳定，能够约束后续实现。
- 用户要求“记录规范”“沉淀设计”或更新某项现行设计。
- 现有 Design 的责任边界、核心模型或关键流程发生变化。

以下内容不单独归为 `decision`：

- 尚未收敛的探索、比较和推理过程：使用 `thinking`、`idea` 或 `question`。
- 面向未来的实施步骤、迁移阶段和交付安排：使用 `plan`。
- 操作手册、排障步骤和日常运行流程：使用相应 workflow、skill 或项目正式运维文档；Portal 运维入口从当前规范索引解析，不硬编码源站条目。
- 测试记录、变更日志、临时配置和当前运行状态：使用事实或运行记录类型。

## Design 内容

正文围绕当前设计本身组织，并按实际复杂度说明：

- 要解决的问题与责任边界。
- 核心对象、状态和它们之间的关系。
- 数据、控制或消息的关键流向。
- 职责归属、依赖方向以及与相邻组件的接口。
- 必须保持的不变量、失败语义和恢复方式。
- 允许变化的部分与需要保持稳定的演进边界。

只写理解和实现该设计所需的内容。不要为了套模板补齐无信息量的章节，也不要混入实施计划、测试清单、日期日志或容易过期的运行数据。

## 标准工作流

1. 确认项目、设计对象和适用范围。
2. 搜索同一责任边界下已有的 Design 及其替代关系。
3. 同一设计发生演进时优先更新现有条目；只有形成独立责任边界时才创建新条目。
4. 用设计对象作为标题，直接解释当前模型，不使用“决定采用”“本决策”或 ADR 式结论句。
5. 若新设计替代旧设计，将旧条目标记为 `obsolete`，并维护 `supersedes`、`related` 等链接。
6. 写入后检查标题、YAML、链接、状态以及与相邻 Design 的口径一致性。

## 输出格式

正文使用 Markdown 一级标题：

```markdown
# <Design 标题>

<直接解释该设计的责任边界、模型和运行方式。>
```

以下头部仅说明服务落盘格式；创建时提交 title/body，由服务生成身份与审计，更新时保留原身份，不手工盖版本：

```yaml
---
kb_id: decision_<code>
type: decision
project: <ProjectId>
created_at: <YYYY-MM-DD>
version:
  number: 1
  created_at: <ISO-8601 UTC>
  created_by:
    user: <User ID 或 "">
    agent: <Resident/Agent ID 或 "">
  updated_at: <ISO-8601 UTC>
  updated_by:
    user: <User ID 或 "">
    agent: <Resident/Agent ID 或 "">
importance: <1-10>
ai_generated: false
links:
  source: []
  related: []
  supersedes: []
  derived_from: []
---
```

参考文件名仍为 `YYYYMMDD__decision__<Title>__<kb_id>.md`。`decision` 仅是兼容字段；展示名称和正文都使用 Design 语义。
