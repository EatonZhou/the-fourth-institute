# TFI Portal Shared Skill 规范

## 位置

TFI Portal shared skill 放在：

```text
${share_root}/Skills/<skill-name>/SKILL.md
```

## 适用场景

用于多个项目、Portal 页面、Resident 或个人 AMA 共用的稳定流程、全局规则和共享工具使用方式。

## 创建要求

- 默认选择 `${share_root}/Skills/<skill-name>`。
- 使用中文写 `SKILL.md`，除非用户明确要求英文或目标 skill 是第三方原文迁移。
- `description` 写清楚 skill 做什么，以及什么时候触发。
- 与 `${share_root}/Skills/tfi-agent-rule/SKILL.md` 的路径语义保持一致。
- 供 agent 或人类按需阅读的文档直接放在 shared skill 根目录，与 `SKILL.md` 并列；不要创建 `reference/`、`references/` 或 `docs/` 文档目录。
- 正文中不要使用 Markdown 超链接写法；称呼同一 shared skill 目录内的内部文件时，统一写成行内代码形式的相对路径，例如 `./project-local-skill.md`。
- 不要把平台私有 prompt、AGENTS 适配文本或 Codex 私有规则当成 canonical shared skill。

## Portal 可见性

如果用户希望 Portal 资源目录的 Shared Skills 可见，按此目录创建。Skill 类型当前不支持 `RegisterResource`，由文件扫描与索引发现；不要伪造登记调用。已发现的 Skill 可用 `ReadResource` / `UpdateResource` 编辑正文；先解析服务返回的内容地址，多文件包内部文件使用 `skill_<code>/file/<相对路径>`。

页面可发现、Resident 本轮已注入以及发行包已选择是三种不同状态。创建或编辑源文件不会自动改变所有 Resident 的技能选择，也不会修改已冻结的发行包。

改名或搬目录时保留 `metadata.id`，同步实际调用方引用，不复制出两个同 ID 的 Skill。完成后从 Skill 资源目录核对新标题、内容和稳定 ID。
