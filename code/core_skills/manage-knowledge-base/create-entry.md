# 创建与归档条目

## 类型路由

用户明确指定类型时直接使用对应规则。未指定时按内容语义判断：

- 稳定的架构、协议、数据模型、职责边界或交互机制 Design：`decision`（兼容性的类型名）
- 一个值得独立审查的显著改进及其完整方案：`plan`
- 需要执行或跟踪的事项：先按 `./archive-todo-item.md` 区分 Mailbox 待办与知识资源，不使用旧知识 TODO 写入流程
- 尚未解决且值得追踪的问题：`question`
- 有解释力、会影响后续判断的发现：`insight`
- 可尝试的设想或路径：`idea`
- 项目提出、命名或重定义的术语：`concept`
- 围绕一个议题的推理和权衡过程：`thinking`

按类型读取：

- `./archive-concept-item.md`
- `./archive-idea-item.md`
- `./archive-insight-item.md`
- `./archive-question-item.md`
- `./archive-decision-item.md`
- `./archive-plan-item.md`
- `./archive-thinking-item.md`
- `./archive-todo-item.md`

资料（`information`）、风险（`risk`）、审查（`review`）等已注册类型没有独立分支时，仍按 `./knowledge-base-rules.md` 创建，并直接表达该类型的核心内容。

## 工作流

1. 确认项目 ID，并读取项目 `summary.md`。
2. 按 `./search.md` 在目标资源类型和相邻资源类型中查重。
3. 长文本可以拆成多个有独立价值的条目；无价值片段不归档。
4. 通过当前类型的正式登记入口提交项目、标题和正文，由服务分配稳定 ID、生成 YAML 与文件名、写入实际版本审计；选填字段以提供者契约为准。
5. 正文按对应类型语义组织。登记接口分别接收 title 与 body，自动生成一级标题；body 不含 YAML，自带一级标题时与 title 一致。不依赖旧的加粗标题抽取规则。
6. 创建后核对目录、文件名、身份字段、日期、链接和正文。

`importance` 只能是 1 至 10 的整数。无法判断时使用 5；除非对项目推进极其关键，通常不超过 8。

`metaness` 可省略；需要表达抽象层级时按 `./knowledge-base-rules.md` 阅读正文后评分，不能从重要度或计划发生在未来推导分数。Plan 的颗粒度和价值门槛遵循 `./archive-plan-item.md`，不按 Design 数量或领域配额生成条目。

实验、研究路线图、研究计划和资源类型变更使用主入口列出的专用技能。
