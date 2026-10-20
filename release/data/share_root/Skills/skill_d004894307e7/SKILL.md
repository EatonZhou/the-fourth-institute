---
name: create-skill
description: 用中文创建或修改 TFI Portal skill。用于用户要求新增、整理、迁移、拆分或更新 shared skill、project-local skill 或 Portal role skill，并需要判断存放位置、职责边界、SKILL.md 结构、多文件组织或与 tfi-agent-rule 保持一致时。
metadata:
  id: skill_d004894307e7
---

路径从当前实例运行上下文与配置解析：`${code_root}` 是源码根，`${share_root}` 是共享目录；项目目录使用 `GetProject` 返回值。这些名称是文档占位符，执行前取得实际值。

# 创建与维护 Skill

默认用中文创建或修改 TFI Portal skill。先掌握本文件的必读规则，再按目标类型读取对应细则。

## 多文件原则

多文件描述 skill 的目标，是避免所有使用 skill 的 agent 和人类一次性看到所有内容，增加认知负担。

SKILL.md 只存放使用这个 skill 必须阅读的内容：核心目标、启动流程、文件组织规则、必须遵守的禁令，以及按需读取哪些额外文件。

额外文件只承载不一定每次都需要阅读的内容，例如具体使用分支、额外规范、工具、脚本说明、异常处理和子能力。创建某一类 skill 时，只读取正在创建的那一类细则。

供 agent 或人类按需阅读的文档类额外文件必须直接放在 skill 根目录，与 `SKILL.md` 并列。不要创建 `reference/`、`references/` 或 `docs/` 文档目录。`scripts/` 只放可执行工具，`assets/` 只放生成产物会使用的素材；不要用这两个目录规避文档平铺规则。

## 结构规范

每个 skill 占用一个独立文件夹，目录名与 `SKILL.md` frontmatter 的 `name` 一致。

最小结构：

```text
skill-name/
└── SKILL.md
```

多文件结构：

```text
skill-name/
├── SKILL.md
├── branch-or-role-file.md
├── extra-rules-file.md
├── scripts/
└── assets/
```

`SKILL.md` 应尽量简短，只写使用方法和读取路线。具体分支、角色差异、意外情况、失败处理、子流程和工具使用方式，放入与 `SKILL.md` 并列的额外文件或 `scripts/`。

## 快速流程

1. 明确 skill 用途、触发场景、目标使用者和存放位置。
2. 按目标类型读取对应细则：
   - `./shared-skill.md`
   - `./portal-role-skill.md`
   - `./project-local-skill.md`
   - `./third-party-skill.md`
3. 写 `SKILL.md` 和必要资源。`SKILL.md` 只介绍使用方法，保持简短。
4. 按本文件的写作规范检查内容。
5. 按本文件的验收规范完成检查。

## 写作规范

`SKILL.md` frontmatter 至少包含：

```yaml
---
name: skill-name
description: 说明 skill 做什么，以及什么时候使用。
metadata:
  id: skill_<新生成的12位小写十六进制码>
---
```

先读取 `${share_root}/Skills/tfi-unified-id/SKILL.md`，按其中的源码根、脚本路径和调用身份设置执行 `new skill` 生成 ID。新 Skill 必须写入唯一且永久不变的 `metadata.id`；移动或改名 Skill 时保留原 ID。

`name` 使用小写字母、数字和连字符，并与目录名一致。

`description` 是触发入口，应包含稳定触发词和使用场景。不要把触发条件只写在正文里。

正文规则：

- 默认用中文。
- 使用祈使或不定式风格写规则。
- 只写会改变 agent 行为的规则、流程、引用和资源。
- 保持 `SKILL.md` 简短，把规范、分支、异常、子能力和工具说明拆到与 `SKILL.md` 并列的文件。
- 文档类额外文件直接放在 skill 根目录；不要创建 `reference/`、`references/` 或 `docs/`。
- 不要使用 Markdown 超链接写法，即方括号文本后接圆括号路径的形式。
- 称呼同一 skill 目录内的内部文件时，统一写成行内代码形式的相对路径，例如 `./shared-skill.md`。
- 跨 Skill 引用使用 `${share_root}/Skills/...`，执行前解析当前实例根目录；同包文件使用 `./...`，不要写死源站路径。
- 不要复制密钥、token、认证文件或 secret 内容到 skill。
- 不要让 `AGENTS.md`、Portal prompt 适配文本或 Codex 私有 skill 成为 TFI Portal 规则的唯一来源。
- 不要生成或保留 `agents/openai.yaml`，即使系统 Codex `skill-creator` 把它标为 recommended。

可复用操作写入 Skill，项目事实与经验按知识库流程保存。Provider 账号、模型 API 和 Resident 设置通过相应 HATG 管理指令修改并回读；编写 Skill 不会自动修改执行配置或为所有 Resident 启用它。

## 验收规范

完成后检查：

- `SKILL.md` frontmatter 能被解析。
- `metadata.id` 符合 `skill_<12位小写十六进制码>`，且未被其它 Skill 使用。
- `name` 与目录名一致。
- 正文为中文，除非用户明确要求保留英文。
- 每个 skill 占用一个独立文件夹。
- `SKILL.md` 只保留使用方法和读取路线，细节已拆到额外文件。
- 所有供 agent 或人类按需阅读的文档与 `SKILL.md` 位于同一目录，skill 内不存在 `reference/`、`references/` 或 `docs/` 文档目录。
- 类型、位置和职责不与 `${share_root}/Skills/tfi-agent-rule/SKILL.md` 冲突。
- 操作步骤有当前代码、指令 schema 或正式规范依据；文件存在、链接有效不等于机制仍然生效。角色列表与实际物化器一致，声明的读取和写入入口在当前实例可用。
- Portal shared skill 位于 `${share_root}/Skills/<skill-name>/SKILL.md`。
- Project-local skill 位于对应项目的 `knowledge_base/skill/<skill-name>/SKILL.md`。
- skill 目录内不存在 `agents/` 或 `agents/openai.yaml`。
- 没有无关 README、安装说明、变更日志或临时记录。

运行：

```bash
python3 ${share_root}/Skills/create-skill/scripts/quick_validate.py <skill-folder>
```

## 默认判断

用户说“Portal 的 shared skill”“TFI Portal shared skill”，或希望 Portal Skills 页面和任务技能选择器可见时，默认创建在 `${share_root}/Skills/<skill-name>/SKILL.md`。

维护已有 Skill 时保留 ID，核对入口、所有附文和脚本中的实际操作。逐项检查指令参数、数据真值、路径来源、触发方式、权限和部署范围；不能只替换术语或检查文件存在。删除失效分支及其独占附件，并同步有效引用。核心规则以 `${code_root}/core_skills/` 为源码，通过该工程的发布与共享同步流程生效；不要只修共享副本。

不要创建 `agents/` 或 `agents/openai.yaml`。

不要创建无关 README、安装说明、变更日志、历史记录或普通说明文档。
