---
kb_id: decision_08bb2d9141f9
type: decision
project: AI员工
created_at: '2026-09-24'
version:
  number: 29
  created_at: "2026-09-24"
  created_by:
    user: ""
    agent: "resident_403556d33f74"
  updated_at: "2026-10-19T08:53:23Z"
  updated_by:
    user: ""
    agent: "resident_20bd82ebf4c6"
importance: 8
ai_generated: false
links:
  source:
  - /home/dirty-base/dirtybase-portal/scripts/publish_portal_release.py
  - /home/dirty-base/dirtybase-portal/scripts/portal_dev_commit.py
  - /home/dirty-base/dirtybase-portal/scripts/portal_release.py
  - /home/dirty-base/dirtybase-portal/scripts/portal_source_layout.py
  - /home/dirty-base/dirtybase-portal/scripts/restart_portal_release.py
  - /home/dirty-base/dirtybase-portal/scripts/prepare_portal_release_services
  - /home/dirty-base/dirtybase-portal/scripts/run_portal_shared_component.py
  - /home/dirty-base/dirtybase-portal/management_portal/__init__.py
  - /home/dirty-base/dirtybase-portal/management_portal/hatg/kernel.py
  - /home/dirty-base/dirtybase-portal/management_portal/web_isa.py
  - /home/dirty-base/dirtybase-portal/management_portal/wall_backend.py
  - /home/dirty-base/dirtybase-portal/systemd/management-portal-release.service
  - /home/dirty-base/dirtybase-portal/tests/cases/test_publish_portal_release.py
  - /home/dirty-base/dirtybase-portal/tests/cases/test_portal_release.py
  - /home/dirty-base/dirtybase-portal/tests/cases/test_restart_portal_release.py
  related:
  - decision_ab9880dc4c9d
  - decision_850e6248d680
  - decision_06defc47e960
  - decision_37f98f8fcb4b
  - decision_6febe7bb57f9
  supersedes: []
  derived_from: []
---
# Portal 共享运行基础与 Dev 到 Release 的快速发布流程

Portal 面向多个 Resident 同时开发、频繁小批量上线的工作方式。完成一项功能或修复，做直接相关且必要的验证，就发布；以持续修复缩短问题暴露到恢复的时间，不以工作区干净、所有任务结束或全量测试为常规发布门槛。

## 两棵同时存在的工作树

Dev 与 Release 是同一 Git 仓库登记的两个 worktree，即两个同时存在的源码目录。它们分别运行 Web 入口，浏览器切换选择访问哪一个入口，不在同一个目录里切换分支，也不复制业务数据库。

本机路径从 Git common directory 中的 `tfi-development.json` 读取：`dev` 指向共享开发工作树，`main` 指向 Release 工作树。两棵树各绑定一个分支，当前实现分别是 `dev` 和 `main`；main 是 Release 的远端发布分支。分支名称不代表额外代码目录。所有 Resident 共用 Dev，不再为功能、修复、审计或发布创建任务分支和工作树。

Release 不承担并行开发，普通修改始终写在 Dev；只有 Release 工作树向远端推送 main，不推 Dev 分支。Release 接收 Dev 已提交历史并快进，不 squash、不建立另一套开发历史，也不通过强制推送覆盖远端。

## 共享运行与独立 Web

两个入口属于同一个已安装实例：数据库、账号、文件资产、Conversation、Session、任务队列和持久执行状态只有一份。切换 Web 不创建新账号、复制 Session 或重放输入。

共享源码的唯一运行来源是 Dev 中声明的共享包：`management_common`、`management_portal/execution`、`management_portal/resources`、`management_portal/society_service`、`management_portal/hatg`。两个 Web 都从该来源加载这些共享协议。ResourceService、ExecutionService、SocietyService 和 ExecutionWorker 使用本站已有的一组后台服务；恢复 Dev 不再部署第二组队列消费者。共享代码修改后按责任组件更新，因而两个入口同时受到影响，不存在共享层的 Dev/Release 故障隔离。

非共享 Web、页面与静态文件分别从各自 worktree 运行。Dev 显示当前开发内容；Release 显示最后发布的提交。共享包在 Release Git 历史中也有对应文件，供发行、恢复和追溯，但运行时只读 Dev 的共享来源，不把两个文件副本当作两个运行权威。

HATG 目录属于共享层，Web 指令实现属于对应 Web。Release 暂缺新目录的 Web 实现时返回 owner unavailable；已移除或暂缺的 Web handler 按既有共享目录兼容规则处理，不因目录更新而使整个 Release 启动失败。

## 一次日常发布

1. 在共享 Dev worktree 修改实现、必要依赖、测试与规范。
2. 仅运行能够发现本次改动回归的必要检查。已有有效结果可复用；不因准备发布重复全量回归或 TAU。用户明确要求的全量测试仍保留真实结果，不能把局部通过写成全量通过。
3. 在 Dev 仓库根执行 `python3 code/scripts/tfi_dev.py publish --message "<说明>" --path code/<路径>`。`--path` 可重复；显式 `--all` 提交全部修改；已提交的修改可不带路径发布当前 HEAD。`--check` 可附带本次相关检查，已完成验证时不必重跑。
4. 工具正常提交选定路径，通过 fast-forward 让 Release 工作树到达该提交，从 Release 推送远端，激活受影响的应用组件并核对健康。
5. 分别报告提交、推送、激活和实际验证结果。失败时继续修复并发布；Git 成功不等于服务已经加载。

Portal 开发任务的授权默认包含本次修复的提交、发布和推送。除用户明确要求只改 Dev 或暂缓发布外，开发者不再请求确认，也不等其他任务完成。WALL 不是发布的必经入口。

## 并行修改的明确取舍

发布提交的是选定文件的当前完整内容，不是某个 Agent 独占的 diff。因此，选中文件中其他 Agent 尚未完成的修改、已有暂存内容，以及 Dev 的已提交祖先内容，可以一起进入 Release。这是共同工作树和短发布周期的明确选择。

发布者不联系或等待其他作者，不为排除这些内容而拆分、重建提交、临时还原文件或延迟发布。不相关且未暂存的文件继续留在 Dev。暂存后其他人继续写入的新内容保留在工作区，下一轮再提交；发布结束时 Dev 再次变脏不构成失败。

并行发布仅在 Git 暂存、提交与 Release 更新这些有顺序要求的阶段串行，避免争用索引；不锁住全体开发编辑。若 Release 出现真实检出冲突或远端分叉，保留文件与历史并报告具体冲突，不用 reset、清空工作区或 force push 掩盖问题。

这种方式接受 Release 因伴随修改而短暂不可用，后续在 Dev 修正或 revert 后再次发布；不承诺由 Git 自动保证恢复时长。

## 激活与边界

实例的 `config/development.json` 登记两棵源码树、两个内部 Web 端口与公共路由端口；部署登记列出真实可用的 Dev/Release 入口。路由沿用同一套登录凭据和用户偏好，切换后同一 Resident、会话和资源继续可用。界面不能仅显示两个按钮却把它们指到同一个 Web。

共享服务按代码职责选择更新；Worker 使用已有平滑换代机制，保留执行 ID 与在途输入。普通发布不重启 PostgreSQL、NATS 或 Provider 账号服务，不创建或恢复旧数据库。独立单元测试使用隔离夹具；这种隔离不改变本站两个 Web 共用业务状态的设计。

发布器、安装器、核心提示词和 AGENTS 必须指向本规范；迁移到新代码根只改变本地路径和服务配置，不得把共享工作树流程改成“干净工作区、整棵 squash、独立 Dev 数据库”而仍称为本设计。没有 Git 的独立发行安装不被强制变成开发站。
