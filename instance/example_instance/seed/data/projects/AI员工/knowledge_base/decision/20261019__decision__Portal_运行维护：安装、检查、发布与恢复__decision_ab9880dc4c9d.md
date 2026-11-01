---
kb_id: decision_ab9880dc4c9d
type: decision
project: AI员工
created_at: '2026-10-19'
version:
  number: 21
  created_at: '2026-10-19'
  created_by:
    user: ''
    agent: resident_20bd82ebf4c6
  updated_at: '2026-11-01T03:39:34Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
importance: 7
ai_generated: true
links:
  source:
  - scripts/portal_services.py
  - scripts/tfi_dev.py
  - scripts/tfi_development_runtime.py
  - scripts/tfi_release_install.py
  - scripts/tfi_application_upgrade.py
  - scripts/tfi_application_freeze.py
  - scripts/tfi_application_clients.py
  - scripts/tfi_application_admission.py
  - scripts/tfi_application_work.py
  - scripts/tfi_application_snapshot.py
  related:
  - decision_08bb2d9141f9
  - decision_6febe7bb57f9
  - decision_b06ae321a668
  - decision_e587fe1c472e
  supersedes: []
  derived_from: []
---

# Portal 运行维护：安装、检查、发布与恢复

Portal 的运行维护依次包含依赖准备、定向检查、发布验收和故障恢复。下列 `./scripts/…` 与 `python3 scripts/…` 命令在 Portal 代码目录 `code/` 执行，发布命令 `python3 code/scripts/tfi_dev.py` 在它的上一级仓库根执行；以实际服务状态和激活结果判断是否完成。Dev 和 Release 有各自的 Web 入口，但共享数据库、Session 和同一组后台服务；操作共享服务前要同时考虑两个部署。日常管理入口见 `docs/guide/admin.md`。

## 安装与诊断

Portal 使用 Python 3.12。按需要选择安装档；`tau` 独立安装 Playwright 和 Chromium，普通 `test` 档不包含浏览器。安装入口不初始化数据库，也不启动服务。

```bash
python3 scripts/bootstrap_portal install core
python3 scripts/bootstrap_portal install core test tau provider article embeddings
python3 scripts/bootstrap_portal doctor core test tau provider article embeddings
```

可用 `--venv <目录>` 指向已有环境，`install --dry-run` 预览，`doctor --json` 获取机器可读结果。`doctor` 检查所选档的依赖，不代表数据库、外部 Provider 或所有服务已就绪。新主机的服务由安装器 `scripts/install_tfi_release`（实现在 `scripts/tfi_release_install.py`）在安装阶段按平台注册：Linux 为 systemd 用户服务，macOS 为 launchd，Windows 为 SCM；组件为 postgres、nats、resource、execution、society、worker、web，服务名为 `<service_prefix>-<组件>.service`（Windows 不带 `.service` 后缀）。需要 Dev 部署的实例再运行 `scripts/tfi_development_runtime.py configure --root <实例安装根目录>`（目前只支持 Linux 用户服务），注册 `<service_prefix>-dev-web.service` 和 `<service_prefix>-router.service`，并用同一脚本的 `activate --root <实例安装根目录>` 完成首次激活。Linux 单元中处理请求的服务（postgres、nats、resource、execution、society、web，以及 dev-web、router）设 `CPUWeight=1000`；worker 运行 Provider 适配器，保持默认的 100；测试运行用 20（`management_portal/test_framework/executor.py`）。测试占满主机时，请求路径上的服务仍先得到 CPU。旧安装器写的、只缺这一行的单元仍算本实例所有：安装器 `start` 注册服务时把它们改写为当前定义，dev-web 与 router 由 `configure` 重新生成；systemd 在 daemon-reload 时把新权重应用到运行中的服务，不需要重启。数据库连接来自站点环境中的 `PORTAL_DATABASE_URL`。测试用 `PORTAL_DATABASE_NAMESPACE` 隔离 schema，不对生产 schema 做实验。

数据目录在 Portal 管理 → 数据目录中查看和修改。页面显示实例根目录和各区域的位置、大小与所在磁盘的剩余空间。管理员给某个区域填写新的绝对路径后，控制作业执行「移动区域」（《Portal 实例协议》）：停止使用该区域的服务、复制、检查复制结果、更新清单、启动服务，服务在新位置正常运行后删除原目录。提交后从页面跟踪作业，失败时先看作业记录，不要手动删除源目录。现在的页面仍按旧的四个数据根显示和迁移，按 `plan_4d1990717dc0` 第六步改为区域。

## 规范环境变量与旧运行数据迁移

进程环境只接受 `PORTAL_*` 规范名，旧环境变量及别名映射不再参与读取；规范变量的显式空值保持为空。运行格式、身份头、浏览器标识与摘要校验均仅接受当前规范名称，不再保留历史兼容，也不能通过旧映射配置重新开启。实例配置继续提供既有数据库、服务和路径，取消历史兼容不意味着重建数据库或替换运行账号；明确配置优先于默认值，不以文件存在与否切换历史来源。

旧 Seed 标记、调用计划摘要和相关状态目录的处理先核对已有备份、操作回执及实际状态。只对未完成的旧项制定精确清单：能迁移或补当前标记的迁移；不能迁移的旧项在排除当前引用后清理。记录每项理由、迁移前后状态、备份和残留引用，不重新运行已经完成的迁移，不将清理扩大到业务正文、用户消息、资源 ID、整个区域或账号凭据。状态目录的有效锁、在途任务和文件引用必须保留；存在占用时记录剩余项，不通过停服或强杀解除。

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
# 在 code/ 目录：让 Dev 加载修改
./scripts/portal_services request-restart dev
# 在仓库根（code/ 的上一级）：发布
python3 code/scripts/tfi_dev.py publish --message '本次改动' \
  --path code/<本次文件或目录> --path code/<必要依赖或删除项>
# 确需同步整个工程时：
python3 code/scripts/tfi_dev.py publish --all --message '全量同步'
```

`request-restart dev` 不带 `--component` 时重启 Dev Web 和 resource、execution、society 三个共享服务；只需加载 Dev 页面代码时加 `--component web`。

发布工具在 Dev 工作树提交指定路径（选中文件所属的浏览器兼容单元整组随行），把 Release 工作树快进到该提交，从 Release 推送 `main`，再经 `systemd-run --user` 排队一个激活任务后立即返回。检查输出中的 `ok`、`commit`、`changed_paths`、`release_unit_paths`、`push` 和 `activation.receipt`。激活任务按改动文件重启受影响的 `<service_prefix>-<组件>.service`，Worker 经 HUP 平滑换代，PostgreSQL、NATS 和 Provider 账号服务不重启；完成后把结果写入 receipt（`<实例安装根目录>/runtime/tfi-pair-publish-<id>-result.json`），检查其中的 `ok`、`components` 和 `health`（`dev`、`release`、`public`）。最近一次成功激活另存于 `runtime/development-activation.json`，激活过程的输出在 `journalctl --user -u tfi-pair-publish-<id>`。推送在排队激活之前执行：推送失败时命令报错退出，Release 工作树已经快进，但激活没有排队，服务仍运行原来的代码。以输出的 commit 核对 Release，勿用并行开发后可能已经前进的 Dev HEAD 代替。ISA `PublishRelease` 调用同一流程，其 `paths` 相对 `code/` 目录。

WALL 的成果发布与工程级全量发布也调用同一发布器。Portal 管理 → 执行设置中的 WALL 定时任务总开关只阻止新定时任务；既有工程计划保留，已运行任务和手动操作继续。它独立于 system Lineage 的 Event 开关。

## 故障恢复

1. 查看 `portal_services status --all`、发布命令输出、receipt 和 `runtime/development-activation.json`，确定当前 Release commit、失败阶段及受影响的部署。激活在独立的 `tfi-pair-publish-<id>` 单元中运行，调用方断开不影响它；receipt 尚未写出时先查该单元日志，避免盲目重复发布。
2. 修正 Dev 代码；要撤销已发布的代码，用 `git revert <commit>` 生成新提交，再按上述流程发布。没有就地回退命令。代码撤销不会回滚共享数据库、Session 或共同服务的运行状态；数据恢复须使用对应备份和数据库恢复流程。
3. `portal_services` 只提供 `status`、`health`、`restart` 和 `request-restart`。整站停止与启动使用安装器：`scripts/install_tfi_release stop --root <实例安装根目录>`、`scripts/install_tfi_release start --root <实例安装根目录>`；停止影响 Dev 与 Release 共用的全部服务，执行前确认维护意图。
4. 数据库故障先确认 PostgreSQL 服务、`PORTAL_DATABASE_URL` 和连接权限；恢复数据从经验证的备份进行，先在隔离环境核对。账号或密钥故障不要在日志中输出认证文件内容。

## 专项维护入口

- Resident 令牌由 Portal 自动维护。停用或轮换时使用 `python -m management_portal.manage_agent_tokens` 的 `list`、`disable <id>`、`set <id>` 子命令；新令牌只显示一次，按当前身份验证权限。
- Provider 安装和更新由各自运行时负责。DSH 初始准备可用 `python3 scripts/prepare_agent_runtimes.py`，验收用 `python3 scripts/verify_dsh_backend.py --cwd <授权工作区> --resident-id <当前 Resident ID> --lineage-id <当前 Lineage ID>`。Claude Code 使用 `scripts/install_claude_code_backend`；激活新版 daemon 前确认 `health.active=0`，并核对实际运行版本、登录及用量 RPC。安装成功不等于旧进程已切换。
- 本机 Resident ISA 调用若被代理截获，检查 CLI 的 `NO_PROXY`/`no_proxy` 是否包含 Portal 的本机或私网地址。`401/403` 先查凭据与当前身份，连接拒绝查服务状态和路由，不要借用他人 Resident 身份。
- 旧执行账号迁移已完成，勿重跑已删除的一次性脚本。遗留的旧目录反链和 socket 别名只有在对应账号无运行任务、旧 daemon 已退出且 `/proc` 与 socket 检查确认无引用后才能清理；实体账号目录与认证文件不在清理范围内。历史依据见 `decision_7956d7cb4bde` 和 Git 提交 `4de8486a282bb2c77af6b44f20f9e5ccd477d718`。


## 实例生命周期与代码更新

使用 `scripts/tfi_release_install.py` 的 `create/start/stop/status/retire/first-admin/configure-ports/update-code/list`。通过 `--instance <名称>` 或 `PORTAL_INSTANCE_ROOT` 选择实例；清单是唯一的实例配置入口。代码更新替换代码并重启，数据库结构由所属服务启动时更新，需要退路时先由备份软件备份。Windows/macOS 沿用同一实例生命周期。部署路径、格式名与消息来源遵循实例协议。旧实例转换命令只用于尚未转换的安装，一次性迁移脚本在目标安装完成转换后删除。
