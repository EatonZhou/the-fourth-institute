# Project-local Skill 规范

## 位置

Project-local skill 放在：

```text
<GetProject 返回的实际项目目录>/knowledge_base/skill/<skill-name>/SKILL.md
```

## 适用场景

只服务某个项目，用于项目内部工作流、项目术语、实验流程、论文写作或本项目工具链。

## 创建要求

- 先用 `GetProject` 确认目标项目目录；知识目录有自定义映射时通过 `ReadResource(address="knowledge_tags_7d4e9a2c6f10")` 读取本站知识标签配置资源，并使用相应标签的 `folder`，不按项目显示名称拼路径。
- 不把项目专用规则写入 skills 区域。
- `description` 中写清项目名、任务类型和触发场景。
- 项目工具、路径、实验分支和异常处理优先拆到与 `SKILL.md` 并列的规范文件。
- 如果规则将来会跨项目复用，建议改建 TFI Portal shared skill。
