# Dev / Release 双工作树与快速发布

权威设计为 SPEC 索引中的 [Portal 共享运行基础与 Dev 到 Release 的快速发布流程](../../../instance/example_instance/seed/data/projects/AI员工/knowledge_base/decision/20260924__decision__Portal_共享运行基础与_Dev_到_Release_的发布流程__decision_08bb2d9141f9.md)。旧双实例部署规范 `decision_850e6248d680` 仅作历史记录，不用于配置当前开发站。

实例清单 `instance.json` 的 `deployments.dev.code_root` 与 `deployments.release.code_root` 登记同时存在的 Dev 和 Release worktree（各自是 worktree 的 `code/` 目录），Git 公共目录不保存配置。
所有开发在 Dev 完成；Release（main）只接收发布并向远端推送。

```bash
python3 code/scripts/tfi_dev.py publish --message "修复说明" --path code/相关路径
```

指定路径当前内容、已有暂存内容与祖先提交进入 Release；Dev 可以有并行修改。
需要完整同步时使用 `--all`。`--check` 是可选的相关验证命令，不重复已有有效检查。

已有实例可增加 Dev 部署（第二个 Web 入口），共用其数据库、账号、队列和共享服务。先用 `tfi_dev.py init` 在主检出旁建立 Dev worktree，再把两个 worktree 登记为实例的部署：

```bash
python3 code/scripts/tfi_dev.py init
python3 code/scripts/tfi_development_runtime.py configure --root /实际实例目录 [--dev <Dev worktree>]
python3 code/scripts/tfi_development_runtime.py activate --root /实际实例目录
```

`--dev` 省略时是主检出旁的 `<目录名>-dev`。命令行工具用 `--instance <名称>` 或 `PORTAL_INSTANCE_ROOT` 选择实例。

配置后的发布会排队激活受影响组件（读取返回的 `activation.receipt` 确认结果），Worker 使用平滑换代；无需重建实例，也不
重启数据库和 Provider 账号服务。`--no-activate` 仅用于明确要求延迟激活的准备阶段。

## Resident 从哪里读取规则

仓库根目录 `AGENTS.md` 是开发入口；Resident Folder 的小写 `resident.md` 保存节点身份，
由 Portal 显式注入。公开 `/guide/agent.md` 面向外部 User 客户端。
Portal 托管的 Codex 关闭自动项目文档加载，因此不能只修改 `AGENTS.md` 来向 Resident 传递流程。

共用提示的源码是 `code/core_skills/tfi-agent-rule/`，后台服务运行时直接读取自身代码目录中
`core_skills/tfi-agent-rule/` 的主文件及对应角色文件，实例的 skills 区域不保存副本。`resident`
角色通过其中的开发任务规则读取 `portal-development.md` 和 SPEC；不会自动注入 development 角色全文。
修改随代码发布生效，并通过 `PreviewPromptInjection` 检查正文和发送时机。
共用规则要求必读的 `create-skill`、`manage-knowledge-base` 同样以 `code/core_skills/` 为源码，
按同一方式生效。
已送达的会话内容不会因文件变化而被改写；首轮、后续轮次、压缩后发送各按注入设置执行。
