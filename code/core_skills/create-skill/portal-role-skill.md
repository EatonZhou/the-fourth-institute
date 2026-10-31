# Portal Role Skill 规范

## 位置

Portal role rule 是统一 `tfi-agent-rule` Skill 的按需分支，放在：

```text
${share_root}/Skills/tfi-agent-rule/portal-<role>.md
```

当前物化器 `management_portal.agent_skill_context.PORTAL_ROLE_FILES` 注册的角色文件为：

- `${share_root}/Skills/tfi-agent-rule/portal-development.md`
- `${share_root}/Skills/tfi-agent-rule/portal-resident.md`

`general` 只使用主文件。Portal 上游调用 `portal_system_instructions`，在服务端读取主文件与已注册角色文件，按 SHA 去重后注入正文；模型无需重复读取已注入文件。个人 AMA 与资源讨论沿用 Resident 入口。不要为页面名称另建系统角色、独立 Skill、独立 `metadata.id` 或兼容目录。

## 职责

Portal role skill 定义某类 Portal 运行入口的身份、上下文、权限和写入边界。

每个 role skill 应单独说明：

- 该入口代表谁行动。
- 任务上下文从哪里读取。
- 可以读写哪些目录。
- 哪些操作必须等待用户确认。
- 任务产物、知识和执行配置分别通过什么正式入口维护。
- 失败、信息不足、权限不足时如何回复。

## 多文件组织

角色规则直接写入一个 `portal-<role>.md`。只有单个角色文件本身过长且存在明确的按需读取分支时，才增加同目录平铺文件，并从该角色文件写明读取条件。

新增角色时同时更新：

- `${share_root}/Skills/tfi-agent-rule/SKILL.md` 的角色映射。
- Portal 统一 system prompt 后端允许的角色集合。
- 对应 agent 调用点的显式角色参数。
- 使用真实物化器核对角色正文及导出内容；仅检查文件存在不足以证明接入生效。

不得从工作区 `AGENTS.md`、独立角色 Skill 或调用点内联规则读取角色规则。
