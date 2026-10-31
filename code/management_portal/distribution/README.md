# TFI 发行后端

`/api/workbenches/release` 是登录后的发布业务接口。POST 参数为
`{id, panel:"packaging", operation, arguments}`。HTTP 层保留会话和 CSRF
保护；此领域不要求发布权限，也不向普通资源或工作台操作授予权限。

支持 `configs.list/get/save`、`runs.list/get/start` 和 `candidates`。
配置保存接受名称、完整 `snapshot`、可选配置 ID；已有 ID 直接覆盖当前内容，
不生成配置修订、不要求版本号，也不因旧页面的保存计数拒绝保存。
`request_key` 固定同次保存的回执，更新内容必须换键。
运行接受 `request_key`、`mode:publish|full_test`、`snapshot` 或 `config_id`、
独立的 `tau_validation` 和 `provenance`。服务生成 ID，运行快照不可变。
同次请求重试返回同一 ID，主动再次运行换键。`snapshot` 是选择范围的权威，
不依赖另传的一份自然语言任务文本。

核心执行规则默认来自仓库 `core_skills/tfi-agent-rule` 与
`core_skills/tfi-unified-id`。显式 `source_root` 只改变读取根，目录名仍必须为
这两个 canonical 名称；不读取旧 Skill 名称、域值或格式前缀别名。
必需规则及统一 ID CLI 缺失时生成失败，不回退到本站共享目录。
导出保持 canonical 目录，安装时按目标五根展开路径。

`candidates/release_features` 的 `resolution.resource_dependencies` 是类型到页面、
发布投影、普通 Lineage 和所属 Project 的唯一依赖解析结果（schema
`tfi.release-resource-dependencies.v1`）。它从资源 provider 所有权、原生
`resource_chat` target 和功能声明的 Workbench 已发布绑定读取关系，不创建绑定。
`type_reasons` 与 `edges` 保留直接选型、SPEC/IMPROVE 条目及共享依赖的原因；
`projections` 固定发布 revision 和 alias→Lineage，`lineages/projects` 按 ID 去重。
缺失类型、目标或发布绑定记入 `gaps`，阻止生成可安装工程，但允许保存待配置清单。

新配置保存/另存会重算并保存 `snapshot.resource_dependencies`，按配置 ID 发起的新
运行沿用当前保存内容中的关系；直接提交快照时由服务端重算。生成时验证固定版本及
绑定仍存在并保持一致；绑定变化要求重新保存/创建请求，不静默替换已有运行。
manifest 同时记录这份解析结果。自动 Lineage 只带成员结构和基础 Workspace，历史、
输出目录、额外绑定和默认启用不自动选择；显式手选的选项保持原值。移除某类型后，
仍被其它类型、文档或手选范围要求的关系保留；不因依赖页面而复制全部业务实例。
历史快照缺字段只在读取/导出时派生，新代码修复复用旧工程时保留其原解析记录。

`targets` 操作返回唯一目标目录及给定快照的组合解析。新配置/运行保存
`target_platforms`；旧运行保持原文，在 manifest/阶段中派生
`legacy-effective-linux`。`management_common.distribution_targets` 统一 OS/架构
识别、能力矩阵和无副作用前检。实现状态与实机验证分列；有能力缺口的选择
可保存，生成时进入 `needs_configuration`。安装器消费工程声明的目标、架构和
依赖约束，再自行探测实际二进制与依赖。Windows/macOS 当前为 incomplete/not_run。
`candidates` 下的 `release_targets` 是同一目录的兼容入口；每个 target 包含
`gaps`。未知但格式合法的目标 ID 保留在配置中，解析为 unsupported，阻止生成。
候选结果中的数据库时间统一递归转换为 UTC ISO 字符串，包括模板和权限方案。

发布面板的 `native` / `detail` 请求增加 `panel=packaging`，返回当前已发布
development 工作台的发布面板及 `release_scope=packaging`。同面板的 `state`
使用独立、按登录 actor 隔离的偏好，限制 480KB。资产走
`/api/workbenches/release-assets/<id>/<revision>/<hash>/<file>`；只接受当前
已发布修订的 packaging HTML 和非 HTML 依赖。普通工作台和未发布版本仍走
原有授权。发布面板不装载普通 Resident 对话，运行提交由单一发布服务负责。
首次读取新偏好容器时，仅从同 actor、当前已发布开发工作台的旧 preferences
恢复 `panels.packaging`，保留草稿和旧 submission 原文。已有新容器（包括空对象）
优先，迁移不改旧数据、不读取其它面板。

运行持久化位于 `distribution` 领域。请求通过既有 Resident input scope
和确定性消息 ID 投递给 Family 门面。内部生成、安装及监督调用 `ReleaseStore.report`
写入 CAS 阶段记录；浏览器没有伪造阶段或成功状态的接口。

生成入口：

```sh
.venv/bin/python scripts/generate_tfi_distribution.py show --run-id RUN_ID --environment SOURCE_ENV_FILE
.venv/bin/python scripts/generate_tfi_distribution.py generate --run-id RUN_ID --environment SOURCE_ENV_FILE
```

非空自然语言范围要求 Resident 提交绑定 `snapshot_sha256` 的 `--resolution`
文件。确定性解析不会根据知识链接自动扩大实例选择。生成目录为配置目的根下的
run ID；已有成功工程不会被重写，也不修改源库、源文件或公开推送。
失败重试用 `--attempt 2` 等生成兄弟目录，保留旧工程及失败证据。阶段记录包含
新 manifest SHA256；同一已生成 attempt 的回执重试不重写产物，也不覆盖后续安装失败。

manifest 使用 `tfi.release-engineering.v1`。代码、PostgreSQL SQL、四数据根
和安装入口均为工程相对路径；`inventory` 只排除工程根 manifest 本身，
包含嵌套同名文件。结构化路径用 `${share_root}` 等四根及 `${code_root}`
映射。记录 Git 基线和代码树摘要，具体文件内容由 inventory 固定。
密钥目录为空，订阅账号在目标主机登记，源认证 schema 不导出；Backend 角色保留并等待新站映射。
Repo 从注册的 canonical_checkout 复制代码，Paper 从注册 manifest 所在目录
复制 PDF 与附件；找不到这些实际内容时进入未解析状态，不静默省略。
发行 Repo 保留原 ID/schema，以 `source_kind=exported_tree` 声明不带 `.git` 的源码树。
投影代码 Repo 绑定 `${code_root}`，其他复制的 Repo 绑定 `${share_root}/Codes/<id>`；
checkouts 只保留该根，清空远端和 SSH 路径并关闭 push。运行时按显式根判断存活，
允许原生文件读取，拒绝 Git 历史及写操作；不从源站 checkout 或远端回退。

升级兼容性以工程摘要确定，不根据展示版本推断。绑定快照的 resolution 可显式提供
`upgrade={from_manifest_sha256:[精确起点摘要],migrations:[{id,path,sha256}]}`。
迁移按声明顺序执行，ID 唯一；相对 SQL 路径与摘要必须匹配 inventory，不能使用
新装的 `database/distribution.sql`。无 upgrade 声明仍可新装，升级须无副作用拒绝。
本次首轮没有成功旧实例或已验证迁移，真实升级为 not_run。

历史导出保留聊天、执行、序列及载荷。消费队列、旧 session 绑定、恢复工作
在副本清空，活动输入和触发路径取消，事件源暂停。原控制行匿名化归档到
`distribution_history.records`；`history_freeze` 记录处理范围。安装监督先用
`prepare-only` 导入，只读核查无活动输入/队列/触发路径，再启动服务。新实例
默认不运行 Lineage 定时源，须单独配置。
生成前按实际 PostgreSQL 约束扫描全部外键。只有领域明确允许且实际可空的
源 Provider Session 控制引用被分离；执行和消息历史保留，原引用归档。
匿名化导致最近活动投影同键时按其 owner 语义保留最新时间，不去重消息载荷。

完整测试运行固定监督 Resident，成功报告必须包含 manifest、检查通过、
实例 URL 和行为证据；请求 TAU 验证时还须 `tau_status=passed`。
工程生成、安装健康和模型行为验收分别记录，任何一个阶段都不代替完整验收。

## 功能带出与安装默认状态（v53）

新配置的 `feature_states={id:{included:boolean|null,default_enabled:boolean|null}}`
是唯一功能状态来源；保存、另存与新 run 都保留同一结构。旧运行不回写，
缺字段从旧选择派生，默认值分别注明已核实的原生初始化来源；不存在可核实规则
时要求补全，不将缺值当作全部开启。新 map 优先于旧 categories/optional_features。
缺 map 时 `projects_page`→`projects`、`test_view`→`test_view` 保留旧布尔选择，
默认启用意图仍为 null 并注明原生初始化来源。已有 map 后这两个旧字段只由
canonical included 投影，不能以旧 true 反向带回。模板选择和既有明确资源类型
依赖统一由 `structural_requirements` 解析，候选与导出相同；依赖原因分别记录
`society_templates.selected`、`resource_types.<type>`，不从展示名称猜能力。

`candidates` 的 `release_features` 接受 `{snapshot}`，返回
`{schema,features:[{id,title,initial_default,default_source,adapter,code_requires,
runtime_requires,system_flow_ids,...}],resolution}`。System 候选来自同一只读源快照
的真实 Function Block 与独立事件源；流程 ID 为 `flow:<真实ID>`。计时/Harness
工作流按原生 client_key 合并到设置的 feature ID，不能出现第二份启停状态。

manifest 与副本 `config/distribution.json` 保留 `feature_resolution`，其中
states 分别记录手动带出、代码依赖带出、默认意图、解析来源、实际有效状态和
本站缺少条件；conflicts 阻止生成。代码依赖自动带出；运行依赖被显式关闭时
报冲突。有代码但不存在原生启停映射的能力，其“默认关闭”明确阻止生成，
不能只写 manifest 却声称已生效。默认开启且缺模型、账号、目标或服务单元时
保留意图，标 needs_configuration。普通事件流程默认值未设置时须明确选择。

安装器在冻结只读核查之后、服务启动之前调用
`distribution.initialization.initialize(manifest, target_connection, roots,
mode='fresh'|'upgrade', existing_feature_ids=(), site_config_path=target_site_json)`。
connection 必须是安装器已验证的独立 psycopg 连接，roots 为五根实际绝对路径；
有 System 设置绑定时必须传目标 site_config_path。helper 使用原生执行设置、
WALL 设置、语义搜索文件、应用接单开关和事件源暂停状态，不启动服务或业务工作。
组件恢复调用原生 resume 基线逻辑，不恢复旧队列、租约、session 或历史 checkpoint。
初始化 ledger 保证重试和升级保留本站已有选择；升级还应传入旧工程功能 ID。
文件与数据库的一致性回滚由安装器的私有全根备份/恢复承担。

初始化结果的 `lineage_timer_required` 缺省 false；仅有效开启、依赖就绪、
初始化成功且目标 checkpoint 未暂停的原生 `time.reached` 事件源可使其为 true。
`lineage_timer_sources` 给出 feature_id/lineage_id/source_id 证据；重试重新读取
目标暂停状态，不把缺失 progress 当作已开启。安装器落盘结果后才为本站
service_prefix 开启 timer owner，保存/打包/helper 都不调度工作。升级分支改以
目标库现有原生 time.reached 定义和未暂停 checkpoint 为准，覆盖新 actions
未列出的本站流程，不受新包默认 false/未就绪意图影响；证据标记
origin=existing_target_state，能匹配当前动作时才带 feature_id。旧包无功能声明
的升级也走同一目标状态读取；不改写旧进度或恢复任何旧队列。

计时/Harness 与核心执行代码共用模块，生成器在副本中单独关闭未带出功能的
原生初始值及读写入口，不能由核心默认 TRUE 或残留设置行重新启用。已带出功能
继续使用原生设置和安装初始化，保留默认关闭后的本站启用入口；源模块不改写。

发行依赖约束文件、摘要、来源选择规则及非完整锁定限制记录在
`dependency_materials`；实际安装版本/二进制摘要由安装器另记。根 INSTALL.md
采用固定安装器的完整说明。旧 attempt 不因这些新增字段重新写入。

资源类型裁剪后，生成器按副本原生 vocabulary 投影全部内置权限方案：each
只留已安装类型，无类型的规则及其授权移除，不降成分类节点、不重新挂靠。
保留规则丢失父级/回退依赖时拒绝生成；保留的管理员授权不扩权。
独立进程加载副本注册表和指令目录，由原生 Schemes 编译全部内置方案，结果
写入 manifest.permission_validation。此检查不代替真实首管理员注册监督。

已验数据需要固定而源码需修复时，内部生成入口可显式传 `--data-manifest`
和 `--data-manifest-sha256`、新的 `--attempt`。先验证原包全清单及本 run 的
不可变 snapshot，再复用原 SQL/实际资源；新包再核数据逐字节不变，来源分别
记录新代码 commit 与 data_origin 原包摘要。此模式不重新读取活动源数据库，
不覆盖旧包，也不继承之前的安装/功能成功结论。
