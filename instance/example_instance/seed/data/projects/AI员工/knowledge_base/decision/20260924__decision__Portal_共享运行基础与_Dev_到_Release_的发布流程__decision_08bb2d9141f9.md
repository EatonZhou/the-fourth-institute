---
kb_id: decision_08bb2d9141f9
type: decision
project: AI员工
created_at: '2026-09-24'
version:
  number: 43
  created_at: "2026-09-24"
  created_by:
    user: ""
    agent: "resident_403556d33f74"
  updated_at: "2026-11-04T05:34:37Z"
  updated_by:
    user: ""
    agent: "resident_dbcd3920b7cc"
importance: 8
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/scripts/tfi_dev.py
  - /opt/tfi/the-fourth-institute-dev/code/scripts/portal_release_units.py
  - /opt/tfi/the-fourth-institute-dev/code/scripts/tfi_development_runtime.py
  - /opt/tfi/the-fourth-institute-dev/code/scripts/tfi_release_install.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/agent_prompt_context.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/agent_skill_context.py
  - /opt/tfi/the-fourth-institute-dev/code/core_skills/tfi-agent-rule/portal-development.md
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

Portal 面向多个 Resident 同时开发、频繁小批量上线的工作方式。每完成一项功能或修复并通过相关必要验证，直接发布到 Release；静态前端等无需重启服务的修改也自动发布，发布时不重启服务；以持续修复缩短问题暴露到恢复的时间，不以工作区干净、所有任务结束或全量测试为常规发布门槛。

## 两棵同时存在的工作树

Dev 与 Release 是同一 Git 仓库登记的两个 worktree，即两个同时存在的源码目录。它们分别运行 Web 入口，浏览器切换选择访问哪一个入口，不在同一个目录里切换分支，也不复制业务数据库。

本机路径从所选实例的 `instance.json` 读取：`deployments.dev.code_root` 和 `deployments.release.code_root` 分别指向共享开发与 Release 工作树的 `code/` 目录。两棵树各绑定一个分支，当前实现分别是 `dev` 和 `main`；main 是 Release 的远端发布分支。分支名称不代表额外代码目录。所有 Resident 共用 Dev，不再为功能、修复、审计或发布创建任务分支和工作树。

Release 不承担并行开发，普通修改始终写在 Dev；只有 Release 工作树向远端推送 main，不推 Dev 分支。Release 接收 Dev 已提交历史并快进，不 squash、不建立另一套开发历史，也不通过强制推送覆盖远端。

## 共享运行与独立 Web

Dev 与 Release 两个部署属于同一个已安装实例：数据库、账号、文件资产、Conversation、Session、任务队列和持久执行状态只有一份。切换 Web 不创建新账号、复制 Session 或重放输入。

共享源码的唯一运行来源是 Dev 中声明的共享包：`management_common`、`management_portal/execution`、`management_portal/resources`、`management_portal/society_service`、`management_portal/hatg`。两个 Web 都从该来源加载这些共享协议。ResourceService、ExecutionService、SocietyService 和 ExecutionWorker 使用本站已有的一组后台服务；恢复 Dev 部署时不再另设第二组队列消费者。共享代码修改后按责任组件更新，因而两个部署同时受到影响，不存在共享层的 Dev/Release 故障隔离。

非共享 Web、页面与静态文件分别从各自 worktree 运行。Dev 显示当前开发内容；Release 显示最后发布的提交。共享包在 Release Git 历史中也有对应文件，供发行、恢复和追溯，但运行时只读 Dev 的共享来源，不把两个文件副本当作两个运行权威。

HATG 目录属于共享层，Web 指令实现属于对应 Web。Release 暂缺新目录的 Web 实现时返回 owner unavailable；已移除或暂缺的 Web handler 按既有共享目录兼容规则处理，不因目录更新而使整个 Release 启动失败。

## 一次日常发布

1. 在共享 Dev worktree 修改实现、必要依赖、测试与规范。
2. 仅运行能够发现本次改动回归的必要检查。已有有效结果可复用；不因准备发布重复全量回归或 TAU。用户明确要求的全量测试仍保留真实结果，不能把局部通过写成全量通过。
3. 在 Dev 仓库根执行 `python3 code/scripts/tfi_dev.py publish --message "<说明>" --path code/<路径>`。`--path` 可重复；显式 `--all` 提交全部修改；已提交的修改可不带路径发布当前 HEAD。`--check` 可附带本次相关检查，已完成验证时不必重跑。选中的路径属于浏览器兼容单元时，工具把整个单元一起提交，结果的 `release_unit_paths` 列出随行的文件。
4. 工具正常提交选定路径，通过 fast-forward 让 Release 工作树到达该提交，从 Release 推送远端。需要进程重新加载时激活受影响的应用组件并核对健康；完整发布范围无需重启时加 `--no-activate`，并通过 Release HTTP 响应核对新资源已生效。
5. 分别报告提交、推送、激活和实际验证结果。失败时继续修复并发布；Git 成功不等于服务已经加载。

Portal 开发任务的授权默认包含本次修复的提交、发布和推送；静态前端等不需要重启服务的修改也自动发布，仍需完成相关必要验证。发布与重启分别判断：核对本次完整发布范围，包括兼容单元随行文件、已有暂存内容和未发布祖先提交，确认全部无需重启即可生效时，使用 `publish --no-activate` 完成提交、Release 快进和推送，再核对 Release HTTP 资源；若包含需要进程重新加载的修改，则按责任组件正常激活。是否需要重启按修改本身的生效方式判断，不以发布器当前自动触发重启的行为作为依据。用户已授权开发且未要求只改 Dev 或暂缓发布时，开发者不再请求发布确认，也不等其他任务完成。WALL 不是发布的必经入口。

## 静态文件身份与无需重启发布

静态文件的缓存身份由公共 HTTP 静态 owner 从文件原始字节计算 SHA-256，页面引用 `portal_v=sha256-<摘要>`。Release 只对与返回字节匹配的摘要 URL 允许 immutable；旧构建标签和无版本地址每次重新验证。进程 `build_id`、Git 提交与单个静态文件摘要各有用途，不互相替代。`--no-activate` 发布后，即使 Web PID 和启动标识不变，新页面也会引用新的文件摘要。

验收检查实际 HTML 中的带版本 URL、响应字节和缓存头；同时记录提交与激活回执。修改公共 Python 静态 owner 仍需加载 Web 新代码，后续单纯静态修改无需为缓存换版而重启。已打开的旧页面在重新加载时获得新地址，加载过程中遇到摘要不匹配则按启动检查预算恢复；具体 HTTP 和恢复语义见 decision_f25266e5a0e7。

## 发布前的依赖环境检查

Release 快进、推送和激活排队之前，发布器检查两棵树下所需的依赖环境。Release 的指纹从待发布提交的实际依赖输入计算，包含随行的暂存内容与尚未发布的祖先提交；共享服务的 Dev 环境按实际工作树计算，不把未选中的 Dev 编辑当作 Release 内容。两者与启动器使用同一个指纹计算契约，包括 bootstrap、依赖清单及各树的离线 wheel 锁。

所需环境的解释器缺失时，本次发布拒绝继续：已生成的 Dev 提交保留，Release 不前进，也不推送或排队激活。准备好错误中列出的对应环境后重新发布。`--no-activate` 同样检查，避免把缺失环境留到下一次重启才发现。此检查不要求工作树干净，不改变选中文件和随行提交的发布边界。

## 示例内容的发布库存闭合

示例seed的文件库存由发布提交边界根据真实Git快照生成，打包器仍严格校验实际完整内容，不通过放宽校验容忍失配。发布选定文件后，先从索引读取本次seed内容变化及manifest行变化，对这些路径计算摘要与大小，处理新增和删除，再把修正后的完整manifest写入同一Git索引。基准为当前Release，因此尚未发布的祖先提交也在闭合范围内；手工准备的旧manifest行不能覆盖当前内容的真实摘要。

生成器不读取未选择的Dev内容，也不覆盖工作树manifest中的后续编辑。自动修正只属于本次暂存快照，工作树因此继续显示旧库存差异是正常的；后续发布在当次索引上重新计算，不以旧工作树库存作为内容真值。既有暂存字段与工程元数据保留，其他文件仍按共享发布的完整文件规则随行。

直接发布已提交HEAD时，对当前Release到目标提交之间的相同seed闭合范围作验证；库存不匹配则在Release快进与推送前拒绝，保留Dev提交，提示将相关seed路径及manifest一起发布以生成正确库存。此验证只证明本次变更的库存闭合，不能替代完整安装包的seed全量校验。原先已经发布的历史失配仍由严格打包诊断定位并显式修复。

## 浏览器兼容单元

一个页面由 HTML 外壳和若干脚本组成，脚本之间按名称调用彼此的工厂函数并传递依赖。只发布其中一个文件，Release 就会用新脚本调用旧脚本里还不存在的函数，或让页面加载 Release 里还没有的模块。`code/scripts/portal_release_units.py` 登记这些必须来自同一提交的文件组，发布工具在提交前把选中路径扩展到它们所属的整组：

- **Lineage 工作台**：`lineage.html`、`hatg-panel.html` 以及工作台、HATG 画布和 Lineage 程序的脚本与样式，按固定清单登记。
- **Chat 工作区**：入口脚本 `portal_chat_workspace.js`、全部 `chat_workspace_*.js` 模块和脚本组清单 `management_portal/script_groups.json`，模块取自清单中 `chat-workspace` 组列出的脚本。页面只写这一组的占位元素，服务端发出页面前把它展开为组内脚本，服务端公开静态文件列表也从这一组取得；清单与模块一起发布，Release 因而不会出现入口脚本已要求新模块、页面还没加载它的情况。页面不随行。

- **通用工作台脚本**：`portal-workbench` 组中的 `portal_workbench.js` 与它创建的部件工厂脚本组成一个兼容单元，成员按 `script_groups.json` 的加载顺序取得。
- **Workspace Surface 脚本**：`portal-workspace-surface` 组中的 `portal_workspace_surface.js` 与 Preview 面板、文件浏览器、Document Session、文档标签页和选区控制器的部件脚本组成另一个兼容单元；部件在根脚本之前加载。

这两个组分别将根脚本和部件一起发布。脚本组清单本身仍由 Chat 工作区兼容单元携带，不纳入这两个组的单元，避免仅修改工作台或 Workspace Surface 部件时把整套 Chat 一同提交；修改清单时按它所属的 Chat 单元扩展。此划分对应 `portal_release_units.py` 的 `PART_SCRIPT_GROUPS` 与 `script_group_unit`，共享清单说明加载顺序，各组的运行状态仍由自己的工厂实例持有。

单元之间可以共享文件，扩展会继续进行，直到没有新文件加入。单元里已从源码删除的成员不阻止其他文件发布；没有修改的成员不增加提交内容。`--all` 不做扩展。

## 并行修改的明确取舍

发布提交的是选定文件的当前完整内容，不是某个 Agent 独占的 diff。因此，选中文件中其他 Agent 尚未完成的修改、已有暂存内容，以及 Dev 的已提交祖先内容，可以一起进入 Release；兼容单元随行的文件同样如此。这是共同工作树和短发布周期的明确选择。

发布者不联系或等待其他作者，不为排除这些内容而拆分、重建提交、临时还原文件或延迟发布。不相关且未暂存的文件继续留在 Dev。暂存后其他人继续写入的新内容保留在工作区，下一轮再提交；发布结束时 Dev 再次变脏不构成失败。

并行发布仅在 Git 暂存、提交与 Release 更新这些有顺序要求的阶段串行，避免争用索引；不锁住全体开发编辑。若 Release 出现真实检出冲突或远端分叉，保留文件与历史并报告具体冲突，不用 reset、清空工作区或 force push 掩盖问题。

这种方式接受 Release 因伴随修改而短暂不可用，后续在 Dev 修正或 revert 后再次发布；不承诺由 Git 自动保证恢复时长。

## 激活与边界

实例的 `config/development.json` 登记两棵源码树、两个内部 Web 端口与公共路由端口；部署登记列出真实可用的 Dev/Release 入口。路由沿用同一套登录凭据和用户偏好，切换后同一 Resident、会话和资源继续可用。界面不能仅显示两个按钮却把它们指到同一个 Web。

共享服务按代码职责选择更新；Worker 使用已有平滑换代机制，保留执行 ID 与在途输入。普通发布不重启 PostgreSQL、NATS 或 Provider 账号服务，不创建或恢复旧数据库。独立单元测试使用隔离夹具；这种隔离不改变本站两个 Web 共用业务状态的设计。

发布器、安装器、核心提示词和 AGENTS 必须指向本规范；迁移到新代码根只改变本地路径和服务配置，不得把共享工作树流程改成“干净工作区、整棵 squash、独立 Dev 数据库”而仍称为本设计。没有 Git 的独立发行安装不被强制变成开发站。

## 开发规则如何进入 Resident 上下文

节点身份文件是 Resident Folder 内的小写 `resident.md`，由 Portal 显式注入；仓库 `AGENTS.md` 是开发入口说明，公开 `/guide/agent.md` 是以 User 身份接入的外部客户端指南。Portal 托管的 Codex 关闭自动项目文档加载（`project_doc_max_bytes = 0`），不能只修改 AGENTS.md 来传递开发规则。

共用提示的源码为 `code/core_skills/tfi-agent-rule/`。核心 Skill 属于代码层（《Portal 实例协议》），后台服务读取自己所运行代码目录中的主文件和所选角色文件，实例中不放副本。Resident 角色收到 SKILL.md 与 portal-resident.md；两者明确要求涉及 Portal 开发时再读取 portal-development.md、SPEC 和本规范，并不自动包含 development 角色全文。

修改共用规则时同步源码、示例实例的发布内容与本站共用目录，通过 `PreviewPromptInjection` 核对正文和发送时机。已有会话已送达的内容不会因文件变化而被改写；后续注入遵守 `prompt_injection` 设置与 Provider 保留策略，不能以预览的新正文代替实际送达证据。

两个 Web 入口、用户路由、实例目录与服务控制命令见 `decision_850e6248d680`；本规范定义两棵工作树、共享运行和发布流程。
