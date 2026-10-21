# 共享代码与实例安装

Windows 11 x64 用户双击 `TheFourthInstitute-Setup-x64.exe` 即可安装；
完成后自动准备默认示例、启动服务并打开 Portal 网页。
详见 [Windows 预览安装说明](code/scripts/windows/README.md)，包括联网依赖、
长路径授权、账号配置、日志位置与当前验证范围。

仓库根目录执行 `python3 prepare_install.py`，生成经过校验的
`.build/example_instance/engineering/manifest.json`。
应用源码位于 `code/`；示例初始资料位于 `instance/example_instance/seed/`。

新实例的 target.json 设置：

- `install_root`：仓库下 `instance/<实例名>` 的绝对路径。
- `shared_code_root`：仓库下 `code` 的绝对路径。
- `data_roots`：分别指向该实例的 `data/share`、`data/staging`、
  `data/instances`、`data/accounts`、`data/secrets`。
- `bind: "0.0.0.0"` 允许 LAN 访问；端口与服务名前缀必须未被其他实例占用。
- `run_id`、`supervisor_resident_id` 从生成的 manifest 读取，其余依赖按下文配置。

运行：

```bash
python3 code/scripts/tfi_release_install.py install --target target.json --manifest .build/example_instance/engineering/manifest.json
```

基础程序与 Python 环境会在仓库 `dependencies/` 中准备并复用；也可以指定已有程序。
每个实例只需初始化一次。日常切换无需重新安装或导入示例数据：

```bash
python3 code/scripts/tfi_release_install.py stop --root instance/实例一
python3 code/scripts/tfi_release_install.py start --root instance/实例二
python3 code/scripts/tfi_release_install.py status --root instance/实例二
```
新实例数据库位于 `data/database/`，服务直接使用共享代码，不复制代码到实例内。
首次安装示例实例允许既有 `seed/`，不会将它当作实际运行数据。

老实例迁移必须保留原有数据与身份，不能用示例 SQL 或 first-admin 覆盖。
共享代码更新需要核对所有实例的兼容性及数据库迁移，不使用下面旧独立工程的
整目录替换升级命令。详情见 [Design](code/docs/design/shared-code-instances.md)。

下面保留独立发行工程的参数与生命周期说明；独立模式不设置 `shared_code_root`。

# 独立发行工程安装与升级

固定入口 `python3 code/scripts/install_tfi_release` 消费生成模块的
`tfi.release-engineering.v1` 工程。安装器校验 inventory、代码树和 SQL 摘要，
按 directory_mappings 替换 `${share_root}` 等已声明路径，不重新筛选发布内容。
完整发布测试由运行记录中的真实 Resident 监督；安装健康只表示 `installed`，
功能验收继续显示 `pending_resident`。

若部署者明确接受保留的 Workspace 文件尚未全面核查，可在安装及续跑时指定
`--defer-data-verification`：延后五数据根的 inventory 集合和摘要核对，仍核对代码、SQL、
实例身份及空凭据目录，复制过程中仍拒绝符号链接和特殊文件。安装记录将标记
`data_verification=deferred_by_operator`；此选项不代表数据完整性通过，也不会回源补齐文件。

部署者明确要求直接进行本机功能验证时，可改用 `--skip-file-verification`，跳过安装期
inventory 遍历及代码、数据、SQL、依赖文件摘要校验。仍读取 manifest 配置并核对运行身份、
目标路径、端口及必要依赖；manifest 摘要仅用于实例所有权标识。记录为
`file_verification=skipped_by_operator`，不得据此宣称工程完整性通过。

有监督 Resident 的工程先执行 `install --prepare-only`，完成复制库只读冻结
核查后，再以 `install --freeze-evidence <核查JSON>` 续跑。核查JSON须含本次 `run_id`、
`supervisor_resident_id`、`manifest_sha256`、`passed:true`，`violations` 的值均为0。
安装器在全部应用服务停止时调用本工程的初始化 helper，显式使用目标DB、五根和
`config/site.json`，结果写入 `runtime/feature-initialization.json`，随后才启动服务。
普通用户工程未指定监督 Resident 时，安装器在导入后自动只读核验旧任务、
事件源和会话控制状态已冻结；核验失败保持应用停止，不伪造通过记录。
没有声明该字段的旧工程不推导新默认值，也不据此声称v53默认状态验收通过。
新装初始化中断或失败必须先归档诊断，再按原所有权清理重建；不能直接重放半完成初始化。
升级在迁移、代码切换后应用新工程默认值，传旧工程已包含功能ID，保留本站已有设置；
初始化失败进入已有整站代码/DB/配置配对恢复，不覆盖本地选择或重新导入发行数据库。
定时器只消费 helper 的 `lineage_timer_required:true`：初始化成功且绑定当前工程摘要后，
服务环境才把定时器归属设为本实例；准备阶段、失败或摘要不匹配时保持禁用。

`native_component_switch` 初始化动作只消费发行方已解析的 `binding` 和最小 `site_config`。
初始化在状态写入前用目标库定义核验绑定、组件类型和归属；Pulse 的事件源集合写入
`event_source_progress`，architecture_audit 的 Effect 写入 `operator_work`，均复用原生
暂停/恢复 checkpoint 变换并回读实际状态。只合并绑定键，保留 site 配置其他键。
升级保留本站映射、暂停值及其他 checkpoint；绑定残缺返回 `needs_configuration`，
类型/归属冲突拒绝初始化，不能静默重绑。暂停不取消已有工作；升级排空检查区分纯暂停
checkpoint 与 pending deliveries。原生多组件开关采用逐项提交及失败补偿，不是组原子事务；
恢复未确认时须回读实际组件状态。安装初始化则由独立目标数据库事务管理，文件与DB仍依赖
既有安装中断拒绝重放/升级配对恢复边界，不宣称跨文件与数据库原子提交。

安装器提供 Linux systemd、Windows 用户态组件/显式 SCM 和 macOS launchd 适配。
Windows 图形安装器使用当前用户进程，不要求系统服务账号。适配代码存在不代表
原生验收通过；Windows/macOS 的实际依赖、IPC、用户登录和 Resident 对话仍需实机验证。
安装锁复用 management_common.file_lock 的跨进程互斥，帮助入口不导入平台专属锁或服务实现。
目标平台与能力状态统一消费生成方的目标目录，不在安装器维护第二套支持矩阵。
旧运行快照保持不变，其Linux范围只记为派生的legacy-effective-linux事实。
入口调用唯一目标目录的preflight_manifest，实际OS/架构与所选目标、能力先通过检查，
再懒加载对应平台实现。Python/PG/NATS可执行探测在创建安装目录与锁之前运行；
平台拒绝结果包含结构化主机信息与缺口。
安装状态保存platform_preflight（实际主机、工程摘要、检查时间、依赖结果），不回写运行快照。

使用主机已有 Python 3.12（含 venv）、PostgreSQL 二进制与 nats-server。
新工程的 `runtime_dependencies` 按所选功能声明 bootstrap profiles，其中 `core` 是
启动必需，其余是功能可选。安装器只检查和准备核心 Python 包；共享布局的环境放在
`dependencies/<核心依赖摘要>/venv`，独立工程放在 `runtime/dependencies/<工程摘要>/venv`，
不修改传入的 Python 环境。启用的服务和初始化均使用核心核验通过的解释器。
核心依赖准备失败不会启动应用服务；查看 `runtime/dependency-install.log` 后可重试。
历史工程未声明 profiles 时继续使用原有核心依赖预检，不改写其 manifest。

Article、浏览器自动化、测试及模型工具不在首次安装时自动下载，也不参与启动必需核验。
`installation.json` 分别保存已验证的 `profiles`、完整 `declared_profiles` 和
按需检查的 `optional_profiles`；可选项没有经过验证时不会记录成已就绪。
安装器和通用 bootstrap 不自动安装 TeX。用户按需要自行安装 TeX 编译器、`latexmk`、
文档所用宏包以及可选的 `texlab`，使 Portal 进程的 PATH 能找到工具后重启进程。
Article 的资料预检不要求工具齐全；实际编译时检测缺失并提示，工具可用后重新检测。
其他扩展可在需要时显式调用 bootstrap 的相应 profile；Test/TAU/Provider 的
显式准备仍要求 Node.js >=20 与 npm。安装器不修改系统包管理器。
账号、TAU 测试会话等本站配置由功能初始化检查，工具安装不伪造配置。
升级在原有完整备份后准备核心依赖；核心依赖失败沿原恢复入口恢复成对代码、数据和配置。
发布器同时保留所选文章的编译器与主文件设置，不导入编译日志/报告。
文章自行引用的额外宏包、系统字体或外部工具仍须通过该文章的编译预检和实际编译确认。

目标配置为 JSON，包含以下字段：

```json
{
  "install_root": "/opt/tfi-instances/run-example",
  "run_id": "与manifest一致的运行ID",
  "supervisor_resident_id": "与manifest一致的真实ResidentID",
  "service_prefix": "tfi-run-example",
  "database_name": "tfi_run_example",
  "data_roots": {
    "share_root": "/opt/tfi-instances/run-example/data/share",
    "staging_root": "/opt/tfi-instances/run-example/data/staging",
    "instances_root": "/opt/tfi-instances/run-example/data/instances",
    "accounts_root": "/opt/tfi-instances/run-example/data/accounts",
    "secrets_dir": "/opt/tfi-instances/run-example/data/secrets"
  },
  "ports": {"web": 28086, "postgres": 56432, "nats": 14223},
  "bind": "127.0.0.1",
  "python": "/opt/tfi-runtime/bin/python",
  "pg_bin": "/usr/lib/postgresql/16/bin",
  "pg_share": "/usr/share/postgresql/16",
  "nats_server": "/usr/sbin/nats-server"
}
```

需要额外 PostgreSQL 动态库时提供 `pg_lib`。路径不能含引号、反斜杠、控制字符
或 systemd 展开字符。安装根须与当前源码和五数据根完全分离，五个目标数据根
均位于安装根内且彼此不重叠。Unix socket 路径长度受系统限制，安装根宜短。
用户直接安装普通发布工程时，`supervisor_resident_id` 可为空，须与 manifest 一致；
完整测试工程必须保留生成阶段指派的真实监督 Resident，安装参数不能改派或清空。

安装并启动服务后，在浏览器中访问：

- 本机：`http://127.0.0.1:<Web端口>/`，例如 `http://127.0.0.1:28086/`。
- 同一局域网的其他设备：`http://<安装主机IP>:<Web端口>/`，例如 `http://192.168.1.100:28086/`。

Web 端口取自 `target.json` 中的 `ports.web`；`28086` 是上方配置的示例值。
默认 `"bind": "127.0.0.1"` 仅允许本机访问。需要局域网访问时，在安装前将
`"bind"` 设为 `"0.0.0.0"`，并在主机防火墙中允许访问该 Web 端口。
在 Linux 安装主机上运行 `hostname -I` 可查看地址，选择其中的局域网 IP。
`0.0.0.0` 是监听地址，浏览器中应填写安装主机的实际 IP。

```bash
python3 code/scripts/install_tfi_release plan --target target.json
python3 code/scripts/install_tfi_release install --target target.json --manifest manifest.json
python3 code/scripts/install_tfi_release status --root /opt/tfi-instances/run-example
python3 code/scripts/install_tfi_release first-admin --root /opt/tfi-instances/run-example --username yourname
python3 code/scripts/install_tfi_release stop --root /opt/tfi-instances/run-example
python3 code/scripts/install_tfi_release cleanup --root /opt/tfi-instances/run-example
```

`plan` 只读检查路径、端口、依赖可执行文件和服务名占用。`install` 建立独立
PostgreSQL 集群（仅本实例 Unix socket）、数据库和 NATS，以及 Resource、Execution、
Society、Worker、Web 服务。所有服务显式使用安装根下的配置文件和独立环境，
不会继承源数据库地址、Provider 凭据、HOME 内配置或 Resident bearer。
服务名为 `<service_prefix>-<component>.service`，保存在当前 OS 用户的 systemd 目录。
需在启动前审查导入历史时，先运行 `install --prepare-only`：仅启动独立 PostgreSQL，
应用服务保持停止，结果为 `prepared`。监督 Resident 核对旧队列、执行租约及恢复状态
不可消费后，用同一工程和目标再次运行不带该开关的 `install`，继续启动和健康检查。

运行结果写入安装根的 `installation.json` 和 `installation-result.json`，记录
run_id、supervisor_resident_id、阶段及错误、服务状态、URL 和停止清理 argv。
服务日志通过 `journalctl --user -u <service-name>` 检查；失败的确定性命令输出保留于
`runtime/command-error.log`。失败后由监督 Resident 诊断，不能将脚本退出码视为完整测试结论。
相同工程和目标的重复安装保留已导入数据库。若导入被中断且数据库已存在，安装器拒绝
覆盖，需先诊断并选择清理该独立实例后重新安装。

`stop` 仅停止记录中的七个服务，保留实例数据。`cleanup` 在核对服务文件归属并确认
停止后删除该实例单元及整个安装根（包括独立数据库）。外部复用的 Python 和 PostgreSQL
依赖保留。安装工程或参数变更需要新实例，不能覆盖既有实例或源 Portal。

`first-admin` 复用现有 `scripts/portal_setup.py` 生成仅此新实例的一次性初始化邀请，
将链接保存到实例 secrets_dir 下的 `first-admin-link.txt`（0600），只输出文件路径。
使用者在本机查看该文件并打开邀请页面设置密码；通过其他机器访问时，把链接的
127.0.0.1 换成安装主机地址，保留端口和 fragment。已有账号时原初始化入口会拒绝。
安装目标默认 `enable_lineage_timers: false`，通过既有 `PORTAL_LINEAGE_TIMER_INSTANCE`
限制定时源，保留 Lineage 定义与历史但不在首次上线时自动启动定时工作。
需要定时工作的新部署可在目标配置中显式设为 true，并先准备自己的 Backend。
首次打开新站注册自己的管理员并配置自己的 Backend。`external_requirements` 原样
随结果提供。TAU 验证由监督 Resident 对上线系统另行组织，验证开关不改变工程内容。

## 已有独立站点升级与恢复

安装与升级共用固定入口、平台前检、目标所有权、实例锁和服务适配。升级只接受本安装器
管理且健康的独立站点；起点为installation.json中确切的manifest_sha256。当前范围是
同安装布局、同PostgreSQL主版本、依赖仍可复用的版本升级；不升级源Dev/Release站点。
工程须显式声明以下合同，缺字段或起点不匹配时在修改前拒绝：

```json
{"upgrade":{"from_manifest_sha256":["支持的原工程完整摘要"],
 "migrations":[{"id":"稳定唯一迁移ID","path":"migrations/001.sql","sha256":"文件完整摘要"}]}}
```

migrations必须明确列出，空列表表示该起点不需数据库迁移；SQL必须在工程inventory中。
迁移不导入发行distribution.sql覆盖本站库。迁移在同一事务内执行，ID与摘要记入
tfi_installer.migrations；相同已完成迁移不会再运行，同ID不同摘要拒绝。
迁移不得自行提交事务；违反时按备份恢复路径处理。

```bash
python3 code/scripts/install_tfi_release upgrade --root /opt/tfi-instances/run-example --manifest /path/to/new/manifest.json
python3 code/scripts/install_tfi_release recover --root /opt/tfi-instances/run-example
```

升级前须使本站活动及排队工作排空。安装器检查活动工作、停止入口与调度后再次检查，
再停止全部本次服务并确认PID为0。maintenance目录中保存私有、离线一致的完整备份及摘要，
包含代码、独立PG集群、本站用户/账号凭据、五根和本地配置；权限为当前安装用户私有。
发行包中的初始化数据和五根不会复制进已有站点，不重新执行首管理员初始化。

备份完成后执行迁移、切换代码并验证服务健康，阶段写入maintenance/upgrade.json。
迁移或切换失败时尝试成对恢复代码与数据库；恢复前保留失败版本的完整副本。
健康检查失败时停止本站服务，保留匹配的新代码与数据库，显式recover会先保存失败版本
再恢复旧版本，避免静默丢弃新版本启动后产生的本地写入。恢复后保持停止状态，检查后
使用原工程的install命令续跑。失败版本中的新增业务数据应按具体迁移规则核对、恢复，
不会自动合并到旧数据库。不能仅复制旧代码后继续使用不兼容的新数据库。

中断的升级必须先recover，不能通过install绕过阶段记录或重复导入。恢复中断仍保留
有效所有权记录，可再次recover；备份损坏或仍有存活PID时拒绝恢复。备份前中断未修改
代码或数据库，recover只确认停止并解除该阶段。完整备份仅在本独立安装内保存，不向源站
或公开位置复制凭据。cleanup会删除本次安装根及这些备份，验收及恢复需要它们时应保留。

当前没有已成功安装且声明兼容的旧版本，完整升级验证为not_run；组件检查覆盖备份完整性、
凭据/Workspace保留、代码与数据库配对恢复及失败快照保留，不替代真实旧站升级验收。

## 依赖来源与验证记录

可使用既有bootstrap_portal联网安装依赖，不要求离线包；不修改共享虚拟环境。
发行工程记录约束与来源，安装结果的platform_preflight记录实际OS/架构、二进制版本、
可执行文件路径和SHA256、core doctor解析的包版本及约束文件摘要。复用本地依赖标为
existing-local-installation；没有获取新依赖时不臆造下载来源，不记录带认证信息的URL。
依赖前检失败发生在安装落盘或升级停服务前，修复依赖后可沿同一目标重试。

维护规范归SPEC.md索引中的decision_15aab18c0ab3；本文件仅保留必要安装操作说明。
