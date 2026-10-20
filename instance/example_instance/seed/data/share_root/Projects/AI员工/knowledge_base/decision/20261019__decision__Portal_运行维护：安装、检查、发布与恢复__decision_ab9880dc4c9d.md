---
kb_id: decision_ab9880dc4c9d
type: decision
project: AI员工
created_at: '2026-10-19'
version:
  number: 8
  created_at: '2026-10-19'
  created_by:
    user: ''
    agent: resident_20bd82ebf4c6
  updated_at: '2026-10-19T20:23:55Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 7
ai_generated: true
links:
  source:
  - scripts/portal_services.py
  - scripts/publish_portal_release.py
  related:
  - decision_08bb2d9141f9
  - decision_6febe7bb57f9
  - decision_b06ae321a668
  - decision_e587fe1c472e
  supersedes: []
  derived_from: []
---

# Portal 运行维护：安装、检查、发布与恢复

Portal 的运行维护依次包含依赖准备、定向检查、发布验收和故障恢复。维护者在仓库根目录执行下列命令，以实际服务状态和发布结果判断是否完成。Dev 和 Release 有各自的 Web 入口，但共享数据库、Session 和部分后台服务；操作共享服务前要同时考虑两个实例。日常管理入口见 `docs/guide/admin.md`。

## 安装与诊断

Portal 使用 Python 3.12。按需要选择安装档；`tau` 独立安装 Playwright 和 Chromium，普通 `test` 档不包含浏览器。安装入口不初始化数据库，也不启动服务。

```bash
python3 scripts/bootstrap_portal install core
python3 scripts/bootstrap_portal install core test tau provider article embeddings
python3 scripts/bootstrap_portal doctor core test tau provider article embeddings
```

可用 `--venv <目录>` 指向已有环境，`install --dry-run` 预览，`doctor --json` 获取机器可读结果。`doctor` 检查所选档的依赖，不代表数据库、外部 Provider 或所有服务已就绪。新主机另运行 `scripts/prepare_portal_dev_services` 和 `scripts/prepare_lineage_postgres`；数据库连接来自站点环境中的 `PORTAL_DATABASE_URL`。测试用 `PORTAL_DATABASE_NAMESPACE` 隔离 schema，不对生产 schema 做实验。

数据目录在 Portal 管理 → 数据目录中查看和迁移。迁移前备份并确认目标为空；提交后从页面跟踪后台任务，等待复制校验和服务重启完成。失败时先看任务状态与保留的备份，不要手动删除源目录、链接或备份。挂载点不能直接作为迁移源。

## 规范环境变量与旧运行数据迁移

进程环境只接受 `PORTAL_*` 规范名，旧环境变量及别名映射不再参与读取；规范变量的显式空值保持为空。运行格式、身份头、浏览器标识与摘要校验均仅接受当前规范名称，不再保留历史兼容，也不能通过旧映射配置重新开启。本站部署配置继续提供既有数据库、服务和路径，取消历史兼容不意味着重建数据库或替换运行账号；明确配置优先于默认值，不以文件存在与否切换历史来源。

旧 Seed 标记、调用计划摘要和相关状态目录的处理先核对已有备份、操作回执及实际状态。只对未完成的旧项制定精确清单：能迁移或补当前标记的迁移；不能迁移的旧项在排除当前引用后清理。记录每项理由、迁移前后状态、备份和残留引用，不重新运行已经完成的迁移，不将清理扩大到业务正文、用户消息、资源 ID、整个数据根或账号凭据。状态目录的有效锁、在途任务和文件引用必须保留；存在占用时记录剩余项，不通过停服或强杀解除。

迁移工具由运行与调度领域维护，旧名称只能来自显式本站配置或私有操作材料，不能留在公共源码中。迁移后运行时仅接受当前 Seed 标记和计划摘要。操作完成以数据回读为准，源码测试或旧命令退出成功不能代替实际结果；重启、部署或额外停服须在对应操作范围内单独执行，迁移本身不隐式触发。

## 修改与定向检查

按变更范围运行相关测试，再检查受影响服务和实际功能；不要把全量测试当作每次发布的固定门槛。常用入口：

```bash
./scripts/portal_services status --all
python3 scripts/bootstrap_portal doctor core
python3 scripts/check_portal_architecture.py \
  --resident-id <当前 Resident ID> --output-dir <授权的报告目录>
```

架构核对可加 `--commit <完整 SHA>` 检查指定发布提交。退出码 0 只表示核对完成，还须看报告的 `has_deviations`；退出码 2 表示核对不完整或失败。发布触发的架构核对也可通过 `GetScriptRun`、`ReadScriptRunOutput` 查看产物。

网页或代理改动另检查实际页面、静态资源响应和 `/api/events` 流；修改 nginx 配置时先 `sudo nginx -t`，再按站点方式 reload。TAU 用测试页面或 TAU 运行器验证，先用 `doctor tau` 检查浏览器环境。

## 发布与验收

已授权的 Portal 开发任务在定向验证通过后通常包含 Release 发布及必要的 Git commit；用户明确要求仅改 Dev 或暂缓发布时遵从该要求。共享组件的 Dev 重载也会影响 Release 使用的共同服务；页面改动仍需发布才进入 Release Web。

```bash
./scripts/portal_services request-restart dev
./scripts/publish_portal_release --message '本次改动' \
  --path <本次文件或目录> --path <必要依赖或删除项>
# 确需同步整个工程时：
./scripts/publish_portal_release --all --message '全量同步'
```

发布工具在 Dev 提交指定路径，将 Release 快进至该 commit，重启 Release Web 并验收；发布单元独立运行，调用方断开时仍可继续。检查输出中的 `ok`、`changed_paths`、`dev_commit`、`active_commit`、健康结果和远端 `push`。远端推送失败不会撤销已生效的本地发布。诊断日志位于 `release/publication-logs/`；以该次结果的 commit 核对 Release，勿用并行开发后可能已经前进的 Dev HEAD 代替。

WALL 的成果发布与工程级全量发布也调用同一发布器。Portal 管理 → 执行设置中的 WALL 定时任务总开关只阻止新定时任务；既有工程计划保留，已运行任务和手动操作继续。它独立于 system Lineage 的 Event 开关。

## 故障恢复

1. 查看 `portal_services status --all`、发布结果和 `release/publication-logs/`，确定当前 Release commit、失败阶段及受影响实例。若是发布进程被调用方中断，先查独立单元结果，避免盲目重复发布。
2. 修正 Dev 代码；要撤销已发布的代码，用 `git revert <commit>` 生成新提交，再按上述流程发布。没有就地回退命令。代码撤销不会回滚共享数据库、Session 或共同服务的运行状态；数据恢复须使用对应备份和数据库恢复流程。
3. 维护栅栏存在时先确认维护意图。`portal_services stop <实例>` 会写入栅栏，`portal_services start <实例>` 在健康检查通过后清除；不要绕过栅栏强行自动部署。
4. 数据库故障先确认 PostgreSQL 服务、`PORTAL_DATABASE_URL` 和连接权限；恢复数据从经验证的备份进行，先在隔离环境核对。账号或密钥故障不要在日志中输出认证文件内容。

## 专项维护入口

- Resident 令牌由 Portal 自动维护。停用或轮换时使用 `python -m management_portal.manage_agent_tokens` 的 `list`、`disable <id>`、`set <id>` 子命令；新令牌只显示一次，按当前身份验证权限。
- Provider 安装和更新由各自运行时负责。DSH 初始准备可用 `python3 scripts/prepare_agent_runtimes.py`，验收用 `python3 scripts/verify_dsh_backend.py --cwd <授权工作区> --resident-id <当前 Resident ID> --lineage-id <当前 Lineage ID>`。Claude Code 使用 `scripts/install_claude_code_backend`；激活新版 daemon 前确认 `health.active=0`，并核对实际运行版本、登录及用量 RPC。安装成功不等于旧进程已切换。
- 本机 Resident ISA 调用若被代理截获，检查 CLI 的 `NO_PROXY`/`no_proxy` 是否包含 Portal 的本机或私网地址。`401/403` 先查凭据与当前身份，连接拒绝查服务状态和路由，不要借用他人 Resident 身份。
- 旧执行账号迁移已完成，勿重跑已删除的一次性脚本。遗留的旧目录反链和 socket 别名只有在对应账号无运行任务、旧 daemon 已退出且 `/proc` 与 socket 检查确认无引用后才能清理；实体账号目录与认证文件不在清理范围内。历史依据见 `decision_7956d7cb4bde` 和 Git 提交 `4de8486a282bb2c77af6b44f20f9e5ccd477d718`。


## 同版运行元数据的定向修复

运行时继续严格检查当前格式。历史格式名称变更只使用一次性、显式计划迁移，不在读取器中保留别名。操作前确认实际服务的数据库或文件绑定及物理结构，备份原值并记录计划摘要；并发变化拒绝覆盖。发布、服务重启与业务重放不由迁移工具隐式触发。

部署注册表由 `scripts/migrate_tfi_runtime_names.py --deployment-registry` 单独处理。私有 names-file 提供实际 source_schema 和文件 sha256；只允许同一 v1 格式，正式读取器先验证实例/路由。原字节备份后仅替换顶层标记，其余字节和凭据引用保持不变；此模式不连接数据库。

消息来源由 `scripts/migrate_message_origins.py` 定向处理。私有计划指定实际 database、db_user、agent_schema、lineage_schema 及同版来源标记映射。检查范围为 Agent segments 的 message.origin、message_artifact 的 origin、notice 的 metadata.message.origin，以及 Mailbox message_json 的 origin。只更新明确路径的 schema 字符串，不修改正文、身份、ID、顺序、载荷或其摘要。先用当前 message_attribution 验证作者与发起人，再形成覆盖绑定、主键和前后字节的 plan_sha256。默认检查不更新业务表；apply 须固定计划摘要、写入独占私有备份并 fsync，再在一个事务内以原字节和 xmin 作 CAS。任一失败整体回滚，备份保留；完成后使用真实 GetConversation 回读。运行时不接受原标记，也不通过此迁移改写权限或消息作者。


## 部署路径的离线切换

源码、Provider 状态、密钥和部署配置需要更名时，使用 `scripts/migrate_deployment_paths.py` 的显式私有清单；清单列明每项目录移动、文件原字节摘要与候选摘要、既有链接目标及相关服务。工具仅支持 Linux 同文件系统移动，不更名数据库、账号、资源 ID，也不生成旧路径兼容链接。密钥、凭据、业务数据、所有者和访问模式保持不变。

切换必须由不依赖被移动目录的外部维护进程执行。操作员先协调所有共享消费者和触发器完成停写，记录原服务状态并验证独立完整备份。所有相关用户与系统服务以及 timer/socket/path/automount 激活来源须确实停止，用户服务检查必须使用源运行用户的 systemd bus，系统服务使用系统 manager；`/proc` 可见性不完整、活动引用、路径重叠、符号链接祖先、目标已存在或非普通数据项均拒绝执行。工具不停止服务，不提供权限提升，也不以停掉当前 Resident 来完成自身迁移。

`seal` 固定源目录内容及 inode、配置和候选文件摘要，同时封存相关服务输入的原声明、展开路径、文件元数据和内容摘要；`apply` 必须提供该计划的独立确认摘要并持有同一私有操作锁。每个步骤先持久化 intent，使用不覆盖目标的原子 rename 或原子文件替换，fsync 并回读后才记为完成。源码与所有 worktree 的未提交变化必须保留；配置候选过期须重新合并和核验，不按旧快照覆盖。


计划采用 `tfi.deployment-cutover.v3`，明确列出 moves、edits、links、remove_links、references 和带 user/system scope 的 services；v1、v2 计划拒绝执行，须按当前前像重新生成并封存 v3，不能直接改版本或自动补字段。服务发现检查 FragmentPath、drop-in、EnvironmentFiles 的实际内容以及别名和激活关系；必需文件缺失、不可读或无法准确解析均阻断。挂载检查覆盖可见命名空间中的根内挂载和同文件系统 bind 别名；进程检查覆盖命令、环境、cwd、exe、fd、maps。可见性不完整即拒绝，不能只用父目录的 st_dev 相同判断可以迁移。

`inspect` 仅诊断，不封存、不加操作锁、不改文件或服务。seal 与 apply 持续检查完整消费者和前像。切换中的服务缓存引用按封存原始声明及 journal 中已完成且未回退的移动、编辑和链接步骤，计算当前允许的位置与摘要，并在每一步及最终检查时重新读取实际文件。原路径已经移动不等于输入缺失，原路径重新出现也不能恢复别名读取；新增服务、声明变更、glob 成员变化、候选被改动或不可读输入均拒绝。回退检查以已标记 reverted 的步骤重新计算允许状态，不把封存字节当作当前内容。每一步重新发现服务、挂载和进程，不能跳过检查或用旧链接维持读取。文件与新链接全部回读后，才以精确原目标检查删除退役链接；每一步保留 intent/done。rollback 先恢复退役链接，再按 journal 逆序恢复；它不包括数据库、DNS、网络配置、虚拟环境重建和恢复写入后的数据，不能宣称等于整站恢复。

Python 虚拟环境应在新位置用相同解释器、精确版本和已核验工件重建，并保留原 system-site-packages 设置；直接移动或只替换 shebang 不算完成。域名、证书、外部服务及目标发行实例等跨边界引用应先明确并准备齐全。启动由维护者按原依赖关系恢复，逐项验证真实加载路径、原有会话、当前账号、凭据与服务健康，不能以孤立测试或 unit active 代替验收。

工具只备份被编辑文件及 journal，不能替代完整数据和凭据备份。自动 `rollback` 仅用于已完整执行且所有目标仍与记录的 after 状态一致的操作；中断步骤、重建环境后或恢复写入后，先保全现状并逐项核对，不盲目重放、整体回滚或覆盖新增数据。恢复操作不引入运行时历史兼容。
