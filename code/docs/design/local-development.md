# Dev / Release 双工作树与快速发布

权威设计为 SPEC 索引中的 [Portal 共享运行基础与 Dev 到 Release 的发布流程](../../../instance/example_instance/seed/data/share_root/Projects/AI员工/knowledge_base/decision/20260924__decision__Portal_共享运行基础与_Dev_到_Release_的发布流程__decision_08bb2d9141f9.md)。

`git-common-dir/tfi-development.json` 登记同时存在的 Dev 和 Release worktree。
所有开发在 Dev 完成；Release（main）只接收发布并向远端推送。

```bash
python3 code/scripts/tfi_dev.py publish --message "修复说明" --path code/相关路径
```

指定路径当前内容、已有暂存内容与祖先提交进入 Release；Dev 可以有并行修改。
需要完整同步时使用 `--all`。`--check` 是可选的相关验证命令，不重复已有有效检查。

已有实例可配置第二个 Web 入口，共用其数据库、账号、队列和共享服务：

```bash
python3 code/scripts/tfi_development_runtime.py configure --root /实际实例目录
python3 code/scripts/tfi_development_runtime.py activate --root /实际实例目录
```

配置后的发布会排队激活受影响组件（读取返回的 `activation.receipt` 确认结果），Worker 使用平滑换代；无需重建实例，也不
重启数据库和 Provider 账号服务。`--no-activate` 仅用于明确要求延迟激活的准备阶段。
