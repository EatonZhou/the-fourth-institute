---
kb_id: decision_08bb2d9141f9
type: decision
project: AI员工
created_at: '2026-09-24'
version:
  number: 28
  created_at: '2026-09-24'
  created_by:
    user: ''
    agent: resident_403556d33f74
  updated_at: '2026-10-19T08:53:23Z'
  updated_by:
    user: ''
    agent: resident_20bd82ebf4c6
importance: 8
ai_generated: false
links:
  source:
  - ${code_root}/scripts/publish_portal_release.py
  - ${code_root}/scripts/portal_dev_commit.py
  - ${code_root}/scripts/portal_release.py
  - ${code_root}/scripts/portal_source_layout.py
  - ${code_root}/scripts/restart_portal_release.py
  - ${code_root}/scripts/prepare_portal_release_services
  - ${code_root}/scripts/run_portal_shared_component.py
  - ${code_root}/management_portal/__init__.py
  - ${code_root}/management_portal/hatg/kernel.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/wall_backend.py
  - ${code_root}/systemd/management-portal-release.service
  - ${code_root}/tests/cases/test_publish_portal_release.py
  - ${code_root}/tests/cases/test_portal_release.py
  - ${code_root}/tests/cases/test_restart_portal_release.py
  related:
  - decision_ab9880dc4c9d
  - decision_850e6248d680
  - decision_06defc47e960
  - decision_37f98f8fcb4b
  - decision_6febe7bb57f9
  supersedes: []
  derived_from: []
---
# Portal 共享运行基础与 Dev 到 Release 的发布流程

Portal 同时运行两个使用入口。Dev 直接运行开发工作树中的最新源码，开发者和 Agent 修改后很快就能使用；Release 运行已经发布的代码，更新节奏较慢，供日常使用。两个入口共用同一套业务数据和执行体系。

本 Design 说明两个入口共享哪些部分、Dev 中的一项修改怎样经过“发布”到达 Release，以及发布怎样更新 Release、返回结果和推送 commit。入口怎样选路、实例目录和服务控制命令见 `decision_850e6248d680`；重启时浏览器收到的倒计时通知见 `decision_37f98f8fcb4b`；发布前后的验证证据见 `decision_6febe7bb57f9`。

## 设计理念

Portal 的开发一直在进行：多个人和多个 Agent 在同一个 Dev 工作树里同时修改代码，经常改到同一个文件。等所有工作停下、工作区干净后再发布，几乎找不到时间窗口。因此 Portal 采用小批量发布：完成一个功能或修复，做与它相关的定向验证，就把它送到 Release；发现问题后继续修复、继续发布。这样从发现问题到修复上线的距离短，协调成本低。代价是某次发布可能让 Release 暂时出问题，由后续修复处理：在 Dev 中修正或撤销改动，再发布一次。

同一个 Session 需要在两个入口之间连续使用，运行中的任务也需要在代码更新时平滑交接。所以两个入口共享数据库和任务队列，后台服务都运行同一份代码，执行 Worker 不区分任务来自哪个入口，也不检查代码版本。相应地，两个入口之间没有故障隔离：共享部分出错时，两边同时受影响。

## 基本概念

- **Dev 工作树**：`${code_root}`，一个 Git 仓库，Dev 唯一的分支为 `main`。Dev 入口的全部服务从这里运行，所有修改也先写在这里。
- **共享源码**：两个入口共同使用的五个源码目录，在 `scripts/portal_source_layout.py` 中声明：`management_common`、`management_portal/execution`、`management_portal/resources`、`management_portal/society_service`、`management_portal/hatg`。它们包含共享数据的读写和服务之间的调用协议。
- **共享组件**：ResourceService（资源服务）、ExecutionService（执行服务）、SocietyService 和 ExecutionWorker（执行 Worker，实际运行 Agent 任务的进程）四类后台服务。Dev 和 Release 各运行一组这样的进程，两组都从 Dev 工作树启动、使用 Dev 的 Python 运行环境，读写同一个数据库。因此它们运行的都是 Dev 工作树中的共享源码。
- **Release Web**：Release 入口的网页进程（systemd 服务 `management-portal-release.service`，端口 18085）。它提供页面、静态文件和非共享的 Web 代码，是两个入口之间唯一按发布版本区分的部分。
- **Release 检出目录**：Release 唯一的代码目录 `${share_root}/Projects/AI员工/src/dirtybase-portal-release`。它是 Dev 仓库登记的另一个检出目录（Git worktree），检出 Release 唯一的分支 `release`。Release Web 从这里运行，Release 不保留其他版本的代码。
- **`release` 分支**：Release 唯一的分支，只接收 Dev 的提交。每次发布把它快进（fast-forward）到本次发布的 Dev commit，因此 `release` 的历史就是 `main` 历史中依次发布过的位置。
- **发布**：运行 `./scripts/publish_portal_release`，把 Dev 中选定的修改提交到 `main`，再把 `release` 分支快进到包含该提交的发布批次版本，验收 Release 健康。
- **发布结果**：发布命令在标准输出中返回的 JSON，包含请求、实际变化范围、健康验收和推送情况。独立 systemd 单元的日志保留输出，调用方中断后仍可查询。

## 设计点

### 共享与独立的范围

- **两个入口读写同一份业务数据。** 数据库、Conversation、Session、队列及其持久状态只有一份；从 Dev 或 Release 发起的操作作用于相同的记录。
- **两个入口的执行 Worker 从同一个任务队列认领任务。** Worker 认领任务时不区分任务来自哪个入口，也不检查代码版本；入口切换或 Worker 换代时，运行中的任务继续由 Worker 的换代机制交接，不重新创建。
- **共享源码的修改在重启共享组件后同时作用于两个入口。** Dev 重启受影响的共享组件时，控制面随即重启 Release 中的同名组件，两边一起加载新代码；这类修改不需要等待发布。
- **Release Web 只在发布时更换代码。** Dev 中的页面和非共享 Web 代码可以随时修改，Release Web 保持在 `release` 分支所在的 commit 上。
- **HATG 指令目录属于共享源码，Web 指令的处理函数随 Release Web 发布。** Release Web 启动时按 Dev 工作树（包括未提交的修改）中的指令目录登记自己的处理函数。目录已经删除的指令，Release 仍带着旧处理函数时只记录警告；目录新声明、Release 还没有处理函数的 Web 指令也只记录警告，调用它返回 `instruction.owner_unavailable`（503，可重试），直到处理函数随下一次发布送达。Release Web 不因这种版本差异停止启动；Dev 和共享组件运行自己的目录，缺少实现时仍拒绝启动。

### 日常修改流程

- **一项修改按“修改、定向验证、重启 Dev、发布”四步送达两个入口。** 开发者在 Dev 工作树中修改源码，针对本次变化运行相关检查，执行 `./scripts/portal_services request-restart dev` 让 Dev 组件加载新代码并确认实际行为，再执行 `./scripts/publish_portal_release --message "<说明>" --path <文件或目录>`（`--path` 可以重复）发布到 Release。
- **发布前只需要与本次变化相关的验证。** 整个工程零告警、工作区干净、其他功能完成、全量测试通过、WALL 审核和人工版本号都不是发布条件。验证的选择与证据见 `decision_6febe7bb57f9`。
- **Portal 开发任务的交付默认包括发布和推送。** 用户授权开发或修复 Portal 功能后，Agent 在定向验证通过后直接发布本次改动及必要依赖，发布产生的 commit 自动推送到全部远端；Agent 不另外请求“允许提交”或“允许发布”。用户明确要求暂缓发布或只改 Dev 时例外。只在 Dev 生效的修改还没有完成交付。
- **发布失败时，发布者继续定位和修复。** 无法完成时，发布者报告实际阻塞和尚未生效的范围。

### 发布范围与并行修改

- **发布提交选定路径的当前内容，Release 得到这个 commit 的全部文件。** 发布工具只把 `--path` 指定的文件或目录（包括其中的删除）加入 Git 暂存区并提交；Release 检出的是整个 commit。因此 Dev 分支上此前已经提交、尚未发布的改动会一起到达 Release。本次实际送达的范围以本次 commit 与上一个 Release commit 的差异为准，通过命令结果的 `changed_paths` 返回。
- **同一文件中多方的修改一起发布。** 所选文件里已有其他人或 Agent 的未提交修改时，这些修改随本次发布送达；暂存区中原有的内容也进入本次 commit。工具按文件工作，不拆分文件内容。
- **Society 工作台页面按整组发布。** 选中 Society 工作台或 HATG 面板的任一页面文件时，工具自动把同组仍然存在的 HTML、JavaScript、CSS 和 JSON 文件一起加入本次发布，使 Release 中的页面结构与脚本来自同一版本。
- **发布前不与其他修改方协调。** 工作区里有其他人或 Resident 尚未完成的改动是常态，发布直接按当前内容进行：不联系、询问或等待改动所属方确认完成状态，不为排除这些改动而缩小发布范围、拆分或重建提交、临时还原文件或推迟发布。未完成的改动随发布进入 Release 后如有问题，按上面的方式处理：所属方在 Dev 中继续修正，再发布一次。
- **发布保留所有并行工作。** 工具不重置暂存区，也不撤销或覆盖任何人的文件；暂存之后才写入的编辑留在 Dev 工作树，等待下一次提交。
- **提交和服务切换分别串行保护。** 提交准备锁保护 Git 暂存、提交和待发布目标登记；释放后等待最近请求起 5 秒的安静窗口，从首个请求起最多等待 30 秒。服务切换锁保护批次选择、推进 `release`、健康检查和推送。同一批选择最新的显式发布提交，必须包含当前请求的 commit；运行中的服务切换不阻止后续发布准备提交。
- **全量发布把 Dev 工作树的全部变化一次送到 Release。** `--all` 暂存当前全部新增、修改和删除后提交并发布，用于补齐日常小批量发布中漏掉的改动。
- **HATG 指令 `PublishRelease` 调用同一个发布工具。** 参数 `message`、`paths` 和 `all` 对应 `--message`、`--path` 和 `--all`，成功时返回 commit、实际变化路径、Release 健康状态和推送结果，失败时以 `instruction.failed` 返回命令结果中的错误；它只能在 Dev 实例执行，发起方式记为 `direct`（decision_4527448ad07f）。

### 发布结果与推送

- **Release 健康且实际版本包含请求 commit 时，发布成功。** `commit`、`dev_commit` 是本请求提交，`release_commit`、`active_commit` 是实际验收版本，合并发布时允许不同。健康检查未通过或实际版本不包含请求提交时返回失败；已创建的 Dev commit 保留，修复后可以再次发布。
- **发布改动了共享源码时，工具重启受影响的共享组件。** 工具根据本次变化的文件推算受影响的共享组件，先重启 Dev 中的这些组件，再重启 Release 中的同名组件。改动执行层代码（`management_portal/execution/`）会重启两个入口的 ExecutionWorker，正在运行的任务由 Worker 换代机制交接。
- **每批发布至多安排一次 Release Web 切换，浏览器提前 5 秒收到通知。** 已发布版本包含本请求且服务健康时跳过重复重启；当前版本不健康时仍尝试恢复。 倒计时结束后 Release Web 停止，工具这时把 `release` 分支快进到本次 commit，再启动 Release Web。正在运行的进程因此不会读到一半新、一半旧的代码；分支无法快进时，Release Web 仍以原来的代码启动。通知和实时连接排空见 `decision_37f98f8fcb4b`。
- **每个发布 commit 都推送到 Dev 仓库配置的全部远端。** 当前远端是内网 `origin` 与 GitHub 上的 `github`，推送到远端与 Dev 当前分支同名的分支，只做普通推送。某个远端拒绝推送（例如历史已经分叉）或不可达时，结果写入命令输出，Release 更新照常有效；发布者报告问题，处理后补推。强制推送和改写已推送的历史不在发布授权之内。
- **`release` 分支只接收 Dev 的提交。** 每次发布都是快进。`release` 上出现 Dev 没有的提交时，快进失败，发布报错，那些提交不会被丢弃。
- **发布不另打标签或写额外引用。** 发布过的版本由 `release` 分支的 Git 历史表达。
- **Release 只有一份代码，没有回退命令。** 要撤销一次发布，在 Dev 中修正，或用 `git revert` 撤销相应 commit，再发布一次。发布过的每个版本都在 Dev 分支的 Git 历史里。
- **发布结束后整理 Agent Status 状态缓存。** 一次发布会改变构建标识，工具随后整理 Agent Status 的用量快照缓存，每个环境、用户和缓存类别只保留最新版本。整理结果返回在 `status_cache`，失败不影响发布结果。

### 与 WALL 的配合

WALL（`decision_06defc47e960`）是按设计点组织 AI 软件开发的工作台。它在 Portal 工程上提供三个与发布相关的入口，全部调用本 Design 描述的发布工具，不另建发布状态。

- **成果卡片上的“发布”只移除当前任务对应的卡片。** WALL 让 Resident 查明实际文件范围，执行 `publish_portal_release --origin wall --wall-job <任务 ID> --block <卡片>`，成功后在 `WALL_RESULT` 返回完整 `publication_commit`。WALL 校验该 commit 为 `release` 分支 HEAD 或其祖先，再更新当前任务与卡片；其他成果保持不变。
- **“全量发布”执行 `publish_portal_release --all`，不改动任何成果卡片。** 它补齐漏发的改动，Resident 负责执行全量范围，完成后 WALL 校验返回的 commit 已在 Release 分支历史中。
- **“全面回归与修复”独立于发布。** 它由本工程固定的回归测试 Resident 执行，需要 Test Lab 操作权限；回归结果不作为发布门槛，回归中做出的修复按日常流程各自发布。全量发布和全面回归可以分别设置定时计划，默认不启用。
- **直接运行发布工具的发布同样有效。** 有相应权限的人或 Agent 可以不经过 WALL 直接发布；需要时再在 WALL 中补充成果与 commit 的关联。

## 实现说明

### 一次发布的步骤

```text
publish_portal_release --message M --path P ...
 1. 展开发布单元，在提交准备锁内暂存、提交并登记待发布目标
 2. 释放提交锁，等待 5 秒安静窗口（从首个请求起最多 30 秒）
 3. 取得服务切换锁，选取最新显式发布目标并检查它包含本请求提交
 4. 按实际 Release 与目标版本的差异准备组件；共享组件只重启尚未部署的变化
 5. Release 已包含请求且健康：跳过 Web 重启；否则通知、停止、快进、启动并验收
 6. 检查路由，只在路由源码变化或版本未知时重启
 7. 推送实际批次版本到全部远端；实际 Web 切换成功后发布一次 Release 变化事件
 8. 释放切换锁，整理状态缓存并返回本请求提交、实际版本和健康结果
```

`--message` 必填。步骤 1 由 `scripts/portal_dev_commit.py` 完成：Dev 的 HEAD 必须位于分支上；每个路径必须是仓库内的相对路径，不能包含 `..` 或指向 `.git`，并且至少匹配一个已跟踪或未被忽略的文件，目录匹配其中全部文件；既没有 `--path` 也没有 `--all` 时报错。`--all` 执行 `git add -A`。只有暂存区有变化时才创建 commit，否则沿用 HEAD。

准备或提交失败时，命令直接返回错误，Release 保持原状态。共享组件更新、Web 切换或路由验收抛出异常时，命令结果包含 `failed` 和具体原因；Release 分支无法快进时，Web 以原来的代码重新启动。快进成功而健康检查失败时，Release 停在本次 commit，发布者修复后再次发布。批次进入服务切换后，无论组件或健康验收是否成功，工具都尝试在步骤 7 推送实际批次版本。提交准备锁与服务切换锁分别保护提交登记和分支推进、验收，组件维护继续遵守服务控制面的维护栅栏。

### Release 检出目录怎样使用共享代码

发布工具在 Release 检出目录根目录写入 `release-manifest.json`（schema `tfi.portal-publication.v1`），包含：`source_commit`、`dev_commit` 和 `release_commit`（三者相同，都是本次 commit）、`built_at`、`message`、`paths`（本次 `changed_paths`）、`shared_source`（Dev 工作树路径）和 `shared_paths`（共享源码目录列表）。这个文件被 `.gitignore` 忽略，推进分支时保留；Release 分支成功快进后才写入它，随后启动 Web。快进失败时保留原清单，Web 启动读取的构建标识与代码保持一致。

Release Web 导入 `management_portal` 包时，包的初始化代码读取这份清单，在 Python 的导入查找链最前面装入 `SharedSourceFinder`：之后导入共享目录对应的包时，都从 `shared_source` 加载。Release 检出目录里这些目录的文件保持原样，只是不被使用。Release 的共享组件由 `scripts/run_portal_shared_component.py` 从 Dev 工作树启动，Release 的 `runtime-venv` 是指向 Dev 运行环境的符号链接。因此 Release Web 与所有共享组件使用同一份共享协议代码。

### 共享组件的重启范围

步骤 4 用 Dev 源码重载器的文件到组件对照表（`components_for_paths`，见 `decision_850e6248d680`）把 `changed_paths` 换算成组件集合，再与四个共享组件取交集。工具在 Dev 实例操作锁内核对组件已部署记录，从交集中去掉源码未变化且已经部署的组件；剩余集合非空时执行 `portal_services.restart('dev', components=...)`；Dev 重启共享组件后，控制面会接着在 Release 实例上重启同样的组件。本次重启的组件返回在 `components`，形如 `release:web`、`shared:execution-worker`。

### 用户路由刷新

发布在 Release Web 启动后检查共享的 `portal-user-router.service` 是否运行着旧代码，需要时重启它并等待健康接口通过。路由进程也会加载认证与数据库访问代码；共享 schema 和认证实现更新后，只有 Web 重启会让路由继续使用旧实现，导致同一登录态访问网页成功、切换入口却返回 `login required`。重启会切断路由器上的全部连接（两个实例的实时连接和进行中的请求），所以只在需要时进行：路由器启动时和之后加载新模块时，把自己加载的仓库源码以及 unit 模板 `systemd/portal-user-router.service` 的摘要连同进程号写入 `<实例根目录>/user-router-source.json`；发布比较这些文件的当前内容，有文件变化、记录缺失或记录不属于正在运行的路由进程时才重启。

为了让大多数发布不重启路由器，路由器只加载选路、会话校验和运行状态需要的模块：登记表、会话签名、`portal_access_control` 与 `state_database`、数据库连接和 `runtime_status`，以及 `management_common` 中的少量基础模块。会话校验只读取账号是否登记、是否启用和会话版本（`PortalAccessStore.is_configured`、`is_active`、`session_version`），不判断调用者是否是管理员（`portal_admin` 字段）；这项判断要经权限端口查询权限实例，会把权限系统及其依赖载入路由器。运行状态需要的 AgentData schema 标记放在 `management_common/agent_data_schema.py`，路由器不导入执行层。运行状态的字段和状态含义见 `decision_99bb5dc0f96b`。

路由刷新使用现有签名密钥和登录态，不要求用户重新登录。重启时命令结果包含 `shared:user-router`；`router_health` 总是给出，路由不健康时发布状态为失败。

### 发布输出与中断后的查询

发布命令在独立的 `portal-publish-<id>.service` 中执行。直接调用时自动派生独立单元并等待输出，已位于发布单元时直接执行；Web 或 Worker 的 cgroup 停止不终止发布。自动创建的单元将标准输出与诊断分别保存在 `release/publication-logs/`，调用方中断后可以通过单元状态和日志确认完成情况。自行创建独立单元的调用方负责指定日志去向。

发布工具只返回结果，不创建发布报告、索引或 preparing/activating 状态文件。进程被终止时，以单元状态和最后的日志定位中断步骤；再次运行命令会按当前源码、Release 分支和现有锁执行。WALL 任务 ID 相同也不会复用旧的磁盘结果。

标准输出 JSON 使用 `tfi.portal-publication-result.v1`，包含本次 `commit`、`dev_commit`、`release_commit`，原 Release 的 `parent_commit`，说明、请求路径、展开路径与实际 `changed_paths`，来源、WALL 任务与成果标识、是否全量发布、是否合并 `coalesced`，以及组件、健康、路由、开始与结束时间、缓存整理和推送结果。`status` 只有最终 `completed` 或 `failed`，`ok` 表示 Release 与路由验收是否成功，`error` 保留失败原因。来源与成果参数用于输出关联，WALL 更新卡片时使用自己任务记录中的成果地址。

WALL 通过 `scripts/portal_release.py` 校验 `publication_commit`：它必须是实际存在的完整 commit ID，且为 `refs/heads/release` 的 HEAD 或祖先。非法 ID、尚未发布的提交或不可读取的 Release 分支均拒绝完成任务，成果保留。后续发布快进不会使先前已发布的 commit 失效；全量发布只记录 commit 和任务结果，不移除卡片。

### 推送

推送目标是 `git remote` 列出的每个远端，命令为 `git push <远端> <commit>:refs/heads/<Dev 当前分支>`。推送禁止交互：`GIT_TERMINAL_PROMPT=0`，SSH 使用 `BatchMode=yes` 和 20 秒连接超时；每个远端最多等待 300 秒。命令输出包含每个远端的结果和错误输出的最后 600 个字符。没有远端时记为跳过；Dev 的 HEAD 不在分支上时记为失败并跳过。推送结果只写入 `push` 字段，不改变 `ok`。

### `release` 分支的推进

发布工具调用 `portal_services.restart('release', components=('web',), release_commit=<commit>)`，只有 Release Web 的重启可以带上 `release_commit`。控制面把它交给 `scripts/restart_portal_release.py --commit <commit>`，按以下顺序执行：

1. 向浏览器发布 5 秒重启通知，等待倒计时结束，排空实时连接。
2. `systemctl --user stop management-portal-release.service`。
3. 确认 Release 检出目录位于 `release` 分支，在其中执行 `git merge --ff-only --quiet <commit>`，并确认 HEAD 等于该 commit。提交中删除的文件同时从 Release 中消失；被忽略的 `release-manifest.json` 保留。不在 `release` 分支上或无法快进时，这一步报错。
4. 第 3 步成功后写入与当前检出 commit 对应的 manifest，再执行 `systemctl --user start management-portal-release.service`。第 3 步失败时保留原 manifest 并同样启动 Web，Release Web 以原来的代码启动，错误返回给发布工具。

Release Web 的 systemd unit 以 Release 检出目录为工作目录（`WorkingDirectory=${share_root}/Projects/AI员工/src/dirtybase-portal-release`），由 `prepare_portal_release_services` 从 Dev 工作树的 `systemd/` 安装。其他读取 Release 代码的程序（例如 Telepus 的 `TELEPUS_PORTAL_ROOT`）也指向这个固定路径。
