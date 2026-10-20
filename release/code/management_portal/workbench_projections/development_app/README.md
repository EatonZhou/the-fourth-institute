# Portal 开发投影应用工程

发行依赖由本目录的 `release-dependencies.json` 声明（`tfi.projection-release-dependencies.v1`），与 application 协议分开。`code_repository` 对应应用的登记仓库。架构与协议使用作者交付的当前所需文件清单，逐项固定精确 Workspace 地址、SHA256 和字节数，包含当前入口及其必要资料；架构不通过通用 JSON 递归展开。生产者覆盖更新当前资料，不保存历史架构、提交快照或旧模型；重建及运行副本镜像完成后，按实际新字节更新发行清单。目录附件沿根描述符读取实际地址与摘要，不能从 opaque 文件名推导摘要。来源按需读取当前代码与 Design，不携带旧提交源码。阶段进展在发行副本中初始化为空，不再通过 `json_roots` 展开源站报告索引。只提供合法的空索引、空补充表及必要格式契约；源站报告、详情和历史证据不进入发行包，选中汇报 Workspace 全部成果也不能绕过此规则。

阶段进展直接读取汇报成员的 `workspace_f9ff5e8a76df/file/reports/stage-index.json`，源站 Dev 与 Release 共用该数据源；独立发行安装将同一资源身份映射到本站工作区，从零开始积累。新报告直接写正式 TFI 格式，报告与来源就绪后原子更新索引；没有转换、同步或二次发布步骤。源站既有报告地址、成果 ID、行锚和反馈身份保持不变，打包不删除源站记录。前端直接校验实际报告与补充表的引用、哈希及精确格式；始终观察固定索引，每次加载后替换新增报告的订阅集合，失败保留 SDK 原订阅，隐藏暂停、显示补读。空初始化属于新发行数据，升级保留本站已经积累的进展。Portal 正常打包发行模块和安装器只在执行打包、安装时使用，与本站汇报更新分离。

发行声明的 `stage_progress` 指定同一 Workspace 下的 `index`、`content`、`schema` 精确地址及 `new_report_directory`。打包只冻结 schema 的实际摘要和大小，生成空索引、空补充表及支持空态的发行格式契约；空态不伪造生成时间、覆盖起点或最新报告引用，产生首份报告后按原契约填写。发行契约的空态扩展可重复应用，发行版再次开发和打包时不会叠加格式包装。该 Workspace 的源输出整树在复制阶段排除，生成文件复用其实际安装绑定；当前架构的 15 项文件声明保持独立。

三个区域的架构地图均只读 `repo_5d287fed9c7e/file/architecture/modules.yaml`。演化绑定使用当前 `ref/schema/model_version`，以实际存在的模块 ID 联动；正文与结构须来自同一字节版本，但旧 revision 不作为模块身份门控。失配、删除模块和 `needs_review` 均禁止错误联动，旧 `commit/snapshot_ref` 不触发读取。阶段进展的可选模块归属仅接受当前地图及其精确摘要/锚点，旧架构归属不读取、不订阅，保留原成果身份与普通 Git 证据。

Plan 依赖的结构化入口为当前 `architecture/evolution.yaml`：架构关联读取 `plans[].id`，未来演化正文读取 `directions[status=proposed].plans[]` 与 `plans[].id` 的交集。发行需要保留这些明确声明的知识对象；Plan 正文、方向叙事与 Markdown 中的任意文本不作为递归依赖入口。现有15项架构文件闭包、阶段 `json_roots` 与这些知识资源分别由各自的显式声明负责。

宿主为生成的应用文档先注入固定的 `__PORTAL_SITE__.compatibility`（空 `browser_identifiers` 与空 `format_namespaces`），再同步执行 `portal_state.js`，之后启动 SDK 和应用。文档不继承父页的配置；读取器仅接受完整且精确匹配的 TFI 格式名称与版本。

本工程是投影协议 v2 的多入口应用包，包含架构与协议、阶段进展、未来演化、打包发布区域。`example` 是独立的平台验收入口，用 D3 与 Worker 绘制标明为模拟的本地节点。

架构区展示面向当前工作区的说明，允许研究内容滞后；提交、摘要和历史绑定不作为显示条件。首屏只读取索引、模型入口和模型文档，指令目录及教学内容在打开时读取，源码点击后读取当前工作区文件。附加来源缺失不影响架构浏览；资源入口保留服务端实际错误，区分文件缺失、服务失败与明确的权限拒绝。只展示当前架构、协议与来源，不提供历史提交选择、提交比较或历史架构恢复；刷新替换当前资料与视图状态，读取入口丢弃旧历史列表。

## 打包发布

资源关联区域默认折叠，点击标题展开；候选刷新保留当前展开状态。该区域消费 `release_features.resolution.resource_dependencies`（`tfi.release-resource-dependencies.v1/v2`），展示服务端核实的类型支持、页面、发布投影修订/绑定、Lineage、Project、关联依据及缺口。前端不维护类型到团队的映射，不将自动对象写入手动选择，撤销来源后按新结果更新。自动 Lineage 默认不带经历、自有成果和额外绑定；已显式选择的 Lineage 展示其独立范围，修改沿 Society 原有入口完成。候选刷新中、失败、缺字段均明确显示待核实，不继续展示旧关联为当前事实。缺口允许保存配置，但阻止页面发起发布；命名保存/新 run 的关联冻结及修订校验由发行服务处理，旧 run 不改写。草稿比较忽略只读 `resource_dependencies`，其余选择仍严格比较；新主动运行仅以 config_id 和配置修订消费服务端冻结快照，保留完整快照用于本地回执。旧未决请求保持原 snapshot 传输，不改变幂等重试正文。这是页面接线，不代表实际生成或安装验证已完成。


清单编辑时自动保存到当前用户的分栏状态，显示保存中、已保存或失败。写入串行处理，防止较早的输入覆盖较新的草稿或请求回执；手动保存和提交会等保存完成。未完成或失败的状态写入由宿主在刷新前提示，失败时保留页面内容供重试。

Portal 基础代码随发行完整保留，页面不展示固定模块枚举。LICENSE、NOTICE 及实际发行所需第三方声明必带，既无可选项也无单独固定展示；旧勾选记录存入 `legacy_mandatory_content`，不再控制范围。旧基础组自定义内容保留在补充说明。“Projects 页面”单独控制页面和导航；Project 后端、服务及所选数据继续保留。

带出主题使用 `sdk.context().themes` 的真实候选；宿主从 `PortalTheme.options()` 提供当前主题菜单的 ID 和翻译名称，应用不复制名单，也不修改操作用户的外观。清单 `themes` 为 null 时表示未设置，空数组表示明确不附带额外主题，其他数组保存所选 ID。旧草稿未设置时保持 null；当前不可用的已选 ID 继续显示并保留，提交前要求重新核对。列表加载失败可重试，不清空选择。新任务包括所选主题所需样式、素材和切换支持；自动模式的基础样式随基础代码提供。既有 submission 正文、key 和 message_id 不因清单升级改变。

资源类型来自 ListResourceTypes；选择类型表示包含必要声明、提供者与服务。展开后使用一个名称／ID 选择框，点击即显示当前类型的可读候选，输入动态搜索，候选同时显示名称和 ID；支持方向键、Enter 选择、Escape 关闭、加载更多及加载／空结果／失败提示。已选项不能重复加入，旧查询结果不能覆盖新输入。精确 ID 经 ResolveResource 核实类型和读取权限，任意文字不会直接成为实例。没有实例且没有文字范围时只带类型能力，不解释为全部实例。Project、Lineage、Resident、Family 由 Society 区处理，Workbench 由关联投影区处理。

实例的真实所属 Project 经资源详情与必要的规范化查询摘要核实，并逐个检查 Project 可读性，保存在 resource_types[].ownership。所有所属项目自动加入并显示资源来源；不自动选其他资源或 Lineage。撤选实例仅移除不再被依赖且未手动选择的项目，手动取消不能漏掉仍被资源或投影依赖的项目。无所属项目的资源保持无归属；归属未知或不可读时显示待核实并阻止新请求，保留最后已知归属。旧草稿恢复与提交前重新核实；变化后重新核对。自然语言原样保存，由执行团队解析后遵守相同规则，既有 submission 原文和幂等身份保持不变。对应规范 decision_15aab18c0ab3 v36（SPEC.md 已索引，名称与分类未变）。

关联投影来自真实 Workbench 的资源正文与 bindings。直接选投影和 Lineage 下的关联投影共用一份选择；所有绑定的 Lineage 及归属 Project 都会加入并显示原因，不自动带经历。手动选择与自动依赖分别记录：撤选投影仅移除不再需要的自动依赖；手动取消项目不能排除仍被投影需要的结构。绑定缺失或权限不足会显示待核实并阻止新提交，已知依赖和经历保留供处理。投影目录支持搜索和分页，Lineage 下展示当前已加载且关联可核实的投影。

资源实例范围、Society、投影和总体补充说明都支持自然语言；测试、文档、工具归入附带内容。文字原样保存并进入任务，执行团队负责查询理解，不将文字当成已解析的 ID。

可选功能区域包含 Resident 向量／语义搜索、WALL 定时任务、模型请求时间记录和 Harness 记录；这些候选已对照 `portal_management.js` 的实际执行设置核实，定义于 `optional-features.js`。TAU 与 Telepus 的原有控件也放在此区域，仍分别使用 `categories.tests` 和 `categories.tools` 的原字段，不生成重复选择。资源类型能力与 Projects 页面仍在原有专用区域选择；基础代码和权限支持不作为可取消项。

清单 `optional_features` 保存新增能力的稳定标识与布尔值；null/缺项表示未设置，true 表示手动带出，false 表示未手动带出（被依赖时仍自动包含），旧草稿不会被解释成全部带出或全部排除。未知旧标识可见保留待核对。选择自动保存，Dev/Release 共享，完全不调用源站点运行设置写接口。新任务要求发行副本的页面、导航、菜单、设置、按钮、安装提示及请求与前后端/测试/文档使用同一发行范围；缺失语义搜索时不显示开关、模型状态、安装入口，关键词搜索保留。已带出但运行时关闭可保留启用入口。这里仅实现清单与任务表达，没有执行实际代码裁剪或 UI 导出；历史 submission 保持原文和身份。

同一可选功能区域中的 Portal System 列表通过 `packaging_system` 绑定调用 `GetLineage`，使用返回的 Function Block、未分组 Resident 和组件；显示真实成员、内部连线与跨范围连线，不按名称虚构流程。其模型请求计时 Function Block 经真实 ID 核实，与上方计时设置共用选择。清单 `system_lineage` 保存已核实 Project、定义修订、所选流程结构和原文说明；null 选择表示未设置，空列表表示明确不带其他可选流程。`lineage_ed619225d417` 及实际所属 Project 默认加入依赖，撤销手动选择不能漏掉它，经历和 Workspace 范围仍独立编辑。源定义读取失败或已选对象消失时保留选择并阻止新提交；定义版本或结构变化要求重新核对。执行任务按所选功能整理必要共享结构及跨范围连线，不能把默认 Lineage 等同全带内部流程；其他 Lineage 继续保留全部 Resident。


功能范围保存手动意图，并在 `effective_features` 计算已核实的传递依赖与 `required_by` 原因。当前代码明确核实 TAU、原生 Test 页面都依赖测试框架；选中后框架自动勾选且不能取消，撤销后保留原手动勾选和其他依赖。Society 模板的处理支持随基础代码提供。依赖闭包处理共享依赖和环，不扩张实例范围。任意模板脚本、流程连线及自然语言无法仅靠选择界面完整判断，任务要求执行团队读取实际所选源码，自动补足其必要功能依赖，不额外要求用户选 B 或批准 B。UI、测试和文档采用补齐后的实际范围。

Device 仅提供类型支持，不提供实例选择框和实例范围输入。归一化将旧实例、归属和范围原文转为 `excluded_device_selections`，页面可展开查看已排除说明；它们不是带出范围，不再产生 Project 依赖。直接 ID 解析也拒绝 Device 实例。任务明确这项限制同样适用于自然语言和依赖路径；其他已选外部资源必须交付实际内容，Repo 连代码带出，不只复制登记或源链接。本页没有实际导出能力。

Society 区的模板目录来自 `ListTemplates`，可逐项选择、全选可读项、不带模板，并填写原文范围；不硬编码内置名单。`society_templates` 保存 configured、真实 template_id、kind、version、从实际组件结构读取的支持要求及原文；未设置不同于明确空列表。提交前 `GetTemplate` 再核实当前权限和版本，读取失败保留已有选择并显示错误，绝不调用施加模板。发行任务要求携带定义/参数/正文/素材和必要功能支持，不带生成实例或历史；未选模板在发行初始化时不能补回，已带 Lineage 不受其来源模板未选影响。权限模板仍使用独立区域。

“权限模板与实例”为独立区域，通过 `ListPermissionSchemes`、`GetPermissionScheme` 和按方案读取 `ListPermissionEntries(r=@portal,r_scope=subtree)` 核实当前用户可读内容。每方案一份实例，不虚构独立实例 ID；实例选择自动包含模板。清单 `permission_selection` 保存 scheme ID、template/instance 标记、configured 与范围原文。未设置、明确不附带和具体选择区分保存；权限受限可见且不清空旧选择，提交前重新读取。新任务要求发行副本移除指向未带出对象的权限节点、表项和关联，保留有效权限语义且不扩大授权，首次管理员沿用原初始化，源站点启用状态与权限不变。当前页面不执行权限裁剪。

工具区去掉没有具体定义的“运维脚本”预设勾选，旧草稿的该文字保留在可见自定义说明并随任务待核对，不映射到全部 scripts。真实安装运行所需脚本随对应功能处理；额外文件继续用原文说明。

测试附带内容包括“测试框架”（目录、执行、结果记录）、“测试用例”；“TAU 功能”（Agent 真实使用验证）在可选功能区选择。用例范围文字保留；TAU 选择自动勾选必要测试框架并显示原因，不自动复制源测试账号、执行槽位、历史运行或全部测试团队。

测试界面只在关联投影区选择真实 `builtin=test` Workbench，功能目录提供 `projection_builtin`，资源依赖提供 `selection_feature`、实际 ID、修订和绑定。页面不按标题/路径猜测、不额外显示 Test 开关；旧 `test_view=true` 由后端解析实际投影，新增手选与 canonical 状态联动，撤选清理手选 ID 和 canonical included，避免旧值反灌。必要框架依赖仍保留，隐藏界面不等于停止 TAU/执行 API。旧“Test Workbench”文字保留待核对，历史 submission 不变。

源码来源通过下拉框选择 Dev、Release 或其他来源；选择其他来源后填写目录或仓库地址。清单仍保存原有 source 字符串，旧草稿中的自定义地址恢复到其他来源，已投递请求保持原文。

SPEC.md、IMPROVE.md 分别独立选择，替代附带文档中的“规范索引”。通过既有资源 read/resolve_many 接口按当前用户权限读取实际索引与条目，不新增 ISA。首次读取默认使用当前工作台 application.repo_id 的索引地址，页面显示实际地址并允许改为所选源码的索引资源地址；该地址不是对 Dev/Release 来源一致性的证明，执行团队仍须核实。源码选择改变时显示冲突提示，不清空已选正文。

所选正文经 ResolveResource 和既有实例归属查询核实真实承载类型与 Project；`document_indexes[].requirements` 按类型/项目保存正文地址组。正文类型支持自动合入 `resource_types`，`manual=false` 表示仅因文档带入，`document_entries` 显示来源；其 `instances` 不代表全类型数据，正文范围仍由索引选择确定。资源区和文档区显示原因，所属 Project 随正文保留；撤选只移除不再被依赖且未手动选择的类型/项目。旧草稿加载与提交前重新核实，失败保留最后已知依赖并提示；真实类型不按 ID 前缀猜测，规范描述的业务功能不自动启用。已投递请求不改写或重发。

新任务明确不沿知识条目链接递归扩大选择；打包时在发行副本调整元数据 `links` 与正文资源链接，已选目标适配发行地址，未选目标去链接或转普通文字。源文档、索引与链接不改。所属 Project 和必要结构规则照常适用；当前页面只表达这项处理要求，没有实现或宣称已经执行链接重写、文件导出或数据库处理。

使用 Markdown 结构解析表格和主要文件列表引用，保留源文件的大领域/子标题；SPEC 中逐规范选择，IMPROVE 按原分组显示 Plan，不把规则说明中的引用当作候选。展开模块可逐项选择或模块内全选，整个索引也可全选；不可读项不能新选，已选后不可读或移出索引的条目保留可见并在新提交前提示处理。文档状态 `document_indexes` 按文件名保存 enabled/address/source/entries/description/pending_legacy，文字原样保留，关闭区域保留编辑草稿但不带出。旧“规范索引”转为 SPEC 待核对，不自动启用或全选；原自定义说明仍可见，既有 submission 不变。

所有选择使用 applicable_only：全选只表达本次功能适用范围内的选择。源码索引未提供完整功能关联，页面明确标注关联待核实，不声称已自动筛选。任务要求未带功能的对应测试用例、规范和 Plan 默认排除，共享/跨模块内容由执行团队核实，不按关键词删除或静默带回功能。发行副本改写 SPEC/IMPROVE 索引、正文及必要引用，源文档不变；WALL 使用发行索引正文，不能延用已排除条目的源缓存。选择 Plan 只带文档，不执行计划。本页未实现实际过滤导出。

发布位置保留手动输入，旁边的“浏览”打开主机文件夹选择窗口。从已填写的绝对路径打开（未填写或是仓库地址时从 `/` 打开），沿真实文件系统进入子目录、返回上一级或根目录，也可在路径栏输入绝对路径并回车跳转。目录无需登记为 Workspace。选定当前文件夹后回填并自动保存；取消或读取失败保持原输入，读取失败时仍可改路径重试。

选择器使用共享桥接 `sdk.directories({path, after?})`，由 `/api/workbenches/directories` 校验登录身份和 Workbench 可读权限。后端按实际目录路径查询既有 `ReadResource` 权限：已登记 Workspace 或个人 AMA 目录沿用其对象及所属人；未登记目录按该绝对路径判定。符号链接先解析真实目标并重新鉴权；逐项过滤不可读子目录。返回当前路径、父路径、仅含目录的分页列表，每页最多 200 项；每次导航、翻页和最终选择重新读取，目录列表不授予写权限。这里浏览 Portal 所在主机，不上传客户端文件，不读取文件正文，不创建目录或执行发布。

`packaging` 入口实现 decision_15aab18c0ab3 的清单与团队接入。在开发工作区左侧栏点击“打包发布”打开独立页面（`/development?view=packaging`），与开发投影、WALL、Test 同级。页面按用户保存到现有投影 state，Dev 与 Release 共用。先选择可读 Project，再按项目选择 Lineage，搜索和翻页携带 project_id；选项目不自动选其他数据。Residents、Family 等必要结构随所选 Lineage。`packaging` 绑定指向既有开源版本团队门面，页面原生 Chat 带上已保存清单。

清单 selection_version=3 保存资源类型与实例、手动 Project/Lineage 选择、投影及全部依赖、经历和各区原文。旧草稿逐项核实 Lineage 归属；旧 Projects 选项转为 projects_page。按用户当前要求，移除“旧清单待核对内容”区域：legacy_selections 及废弃的业务／投影／团队数据分组选项不再保留到当前清单和新请求，也不按显示名猜测为新选择。当前自然语言与补充说明继续保存。提交前重新核实类型、实例、归属与投影绑定；绑定变化须重新核对。已投递的 saved.submission 原文、key、message_id 不变。页面不实现或扩大数据库导出工具。

Workspace 基础支持固定包含，通用类型区不提供独立选择。每个已选 Lineage（包括投影依赖带入的 Lineage）展开“工作区与文件”，统一作用于全部 Resident。`lineages[].workspace` 保存 `own_outputs`（null/true/false：未设置/带出/不带自有成果）、`extra_bindings`（unset/exclude/all/partial：未设置/不带额外绑定/全部内容/部分内容）、`extra_scope` 部分范围原文和 `exclusions` 排除说明原文。初始未设置，不推断全带；切换范围选项保留文字，部分范围仅在 partial 时应用。`history` 独立控制上下文与经历。投影重新核实关联时保留已选 Lineage 的文件设置。

基础 Resident Folder 文件（resident.md、diary.md、workspaces.md 等）与自有 workspace/ 目录、必要登记和绑定必带，排除范围不能移除它们。本地缓存和源凭据不带。任务要求在发行数据库移除未带出的额外绑定，安装路径映射后生成一致的 workspaces.md，源数据不变，共享 Workspace 去重。旧 resource_types 中的 workspace 类型、实例和说明原样转为 `pending_workspace_selections`，在 Society 的“Workspace 范围待核对”中可见，保存与任务原文保留，用户可明确移除；不会转成独立导出项或自动扩大项目及文件选择。页面只记录范围和任务语义，文件导出、数据库处理与安装器由后续执行能力承担。

用户可命名保存多个配置、加载修改及另存。配置使用稳定 config_id 和修订；每次主动“发布”或“完整发布测试”创建独立 run_id 和不可变配置快照，版本名称不作为去重键。同次网络重试沿用持久化的原 run_id/payload，不因再次点击刷新创建新运行。配置/运行由唯一发布后端持久化，投影 state 保留当前编辑草稿、选中身份及待核实请求。页面通过宿主发布服务桥读写，不直接联网。完整测试的 TAU 验证开关与发行是否带出 TAU 功能独立；监督 Resident、阶段、产物、实例 URL 与失败/缺配置状态取自实际运行记录，受理、脚本 exit0 和 installed 都不表示完整测试成功。

升级不会改写或重发 saved.submission。已投递的原文可展开查看；缺少回执时使用“用原内容重试投递”，沿用原 key 和 text，与当前草稿分别保存。旧配置内容可另存为命名配置后主动发起新运行，原 submission 不重写、不自动重发。

本入口不实现源码裁剪、发行数据库导出与匿名化、安装器或远端发布脚本。它向团队提交真实业务请求并展示实际结果；这些能力尚未完成时由团队如实说明。不要用此页面的投递验证代替发行物可用性验证。

## 产品体验设计的公共能力

当前产品设计见 `SPEC.md` 索引的 `decision_13e1a748cf04` 当前版本（作者立场）。此处只说明平台接口与实际限制；三区的布局、内容和交互以各自获批设计为准。平台提供运行架构图、协议步骤动画、成果叙事、截图、本地概念演示，以及同源 Portal 页面的宿主导航；媒体能力以表中实际边界为准。

| 设计所需能力 | 当前可用方式与边界 |
| --- | --- |
| 分层图、局部展开、语义缩放、搜索 | 区域内 SVG/Canvas/WebGL、D3、dagre、Cytoscape；区域管理布局与选择，几何库只提供通用计算。搜索覆盖面来自已读取并过滤权限的数据。 |
| 协议回放、未来概念演示 | CSS 动画、Web Animations、requestAnimationFrame 与本地状态机，不需新增宿主 API。区域提供暂停、逐步/回看、文字说明，并响应 prefers-reduced-motion；模拟不调用真实业务指令。 |
| 大图布局 | 现有独立 Worker 构建与 SDK 加载；主线程先过滤权限，Worker 只接收数据。用 requestId/区域代次丢弃过时结果。 |
| 截图与图示 | 模板 import 的 png/jpg/jpeg/gif/webp/svg 转为 data URL，应用 DOM 的 img 可显示 data:/blob:；外部 URL 和相对图片路径不会由宿主自动载入。实时资源图片须经原始 sdk.resource('read') 鉴权取得字节再生成本地 URL，不能用将字节解码为文本的 resources.read。 |
| Markdown 正文 | shared/markdown.js 只渲染经过净化的正文；图片、视频和样式会移除。截图由区域的专用图片视图承载，不能期望 Markdown 自动加载。 |
| 体验入口 | `await sdk.openPage(entry.url)` 打开服务端显式登记的同源 Portal 页面。当前工作台使用 `/workbenches/view?id=<当前ID>&region=<区域ID或facade>`，保留 iframe/草稿并聚焦现有区域或原生 Chat；其他登记页面在宿主当前标签导航。返回 `{opened, mode, url}`，布局拒绝时 opened 为 false。外源、API/下载/登出与未知路径拒绝，目标页继续自身鉴权。 |
| 视频、音频、自定义字体 | 尚不支持正式加载：CSP 的 media-src/font-src 继承 default-src 'none'，音视频还未登记产物 MIME。woff2 的构建 data URL loader 并不意味着运行时字体获准加载。当前使用系统字体、截图和可控的图形演示。 |

动画、计时器、观察器与本地 blob URL 由区域拥有。`hide()` 暂停可见性工作并保存播放位置，`show()` 按当前尺寸和内容恢复，`destroy()` 取消动画/RAF、终止 Worker、断开观察器并回收 URL；共享 `signal` 只在销毁时 abort，不在隐藏时 abort。异步创建的资源应检查 signal，销毁后晚到即释放。宿主不自动暂停应用的所有动画，后台任务不能依赖浏览器节流来清理。

CSS/SVG 使用公共主题变量和 currentColor。Canvas/WebGL 需要区域重新取色并重绘，可观察 iframe 内 `#portalProjectionTheme` 的 childList/characterData/subtree 变化；目前没有独立 onTheme API。尺寸使用本地 ResizeObserver。窗口监听、主题/尺寸观察器都登记 onDispose。键盘搜索和菜单在区域内实现，不接管 Portal 全局快捷键。

主体比例以真实应用 iframe 的可见视口计量，不把外层空白容器或 Portal 导航算进区域。应用可用局部布局 CSS 调整 body 默认 12px 留白、使 #app 填满视口；不覆盖主题变量。首屏和窄屏均须由区域/体验验收测量实际图、叙事或演示占比，平台没有自动保证 80% 的布局模板。静默刷新可沿用 watch 与内容版本检查，成功更新不必显示常驻技术横条；撤权时仍须清除旧数据。

反馈可传 `subject`（非空、最多240个UTF-16码元）作为当前对象的显示名称，宿主在同一个确认中显示真实收件团队和发送文字，技术情境默认折叠；subject 不替代真实 ref 或改变发送内容。

未来概念的演示状态保留 simulated 标记；反馈附着真实 current/proposed 对象，scene 说明模拟场景与来源版本。使用 sdk.md 的既有完整上下文序列化与取消合同，不另建发送渠道。演示状态与展示偏好由所属区域通过 sdk.state 保存；阶段进展不保存成果已读、未读或阅读水位。

`openPage` 接受以单个 `/` 开头的根相对地址或当前 Portal 同源 HTTP(S) 绝对地址，拒绝协议相对地址、用户凭据、空白/控制字符、反斜杠、编码或越级路径。当前工作台入口仅支持唯一 id、至多一个 region，无额外 query/hash；省略 region 与 region=facade 等价，聚焦已有原生 Chat，未知或未装配目标抛 `projection.page_target`。其他页面的查询和锚点由目标页面解释。地址/未登记路径抛 `projection.page_url`，导航目录不可用抛 `projection.page_unavailable`。mode 为 local 表示原地处理；page 表示已发起顶层导航，不表示目标已加载或鉴权成功，旧文档退出时 SDK Promise 不保证继续可观察。调用方必须处理 opened:false 和异常，不能标成体验成功。此接口不读取目标正文、不提交业务动作、不改变 iframe CSP，也没有绕过目标权限的能力。

若获批设计确需短片播放或新媒体/依赖，平台据具体来源与交互补齐共享能力及定向验证。当前媒体继续使用本地图形演示；旧平台验证不能替代新三区的体验验收。

## 区域合同

投影读取侧的格式校验由 `src/shared/format-tags.js` 统一提供。`schemaMatches(actual, expected)` 的 expected 必须为完整 `tfi.<tag>.vN`，actual 仅按完整字符串精确匹配；历史前缀即使通过额外参数传入也不接受。`configuration` 固定返回空 `format_namespaces`，共享生命周期不读取它。各读取器只接受自身明确支持的格式版本，已有 canonical v1/v2 的版本语义保持不变。各区域其余字段、原始 schema 绑定关系及摘要校验不变。新页面定义、Execution 本地视图状态和组装的指令教学内容输出 `tfi`；读取不迁移存量状态、持久 ID 或历史内容。不同文档之间要求相等的 schema 仍按原字符串比较，不归一化标签后重算历史摘要。目录、地图、绑定和 registry 的冻结原文保持各自身份。测试使用 `archive.` 验证拒绝；仓库里的脱敏 fixture 是重新生成摘要与引用的隔离副本，不充当生产批次。

`src/architecture`、`src/progress`、`src/evolution` 各自的 `index.js` 导出 `mount(root, context)`，可以返回 Promise。返回的运行对象可提供 `show()`、`hide()` 和 `destroy()`；unmount 通过 destroy 表达，不另导出卸载函数。首次挂载后按实际可见性调用 show/hide；隐藏保留实例；退出页面销毁。异步挂载完成时若页面已经销毁，立即销毁晚到对象。

context 提供：

- `region`、`sdk`（当前 iframe 的 Projection）、`signal`（销毁时 abort）。
- `resources.resolve(ref)`：按当前用户解析，保留原始 ref。资源读取时才去掉 `#L123` 或 `#L123-L130`。
- `resources.read(ref, 'text' | 'json' | 'yaml')`：返回 `{readable, ref, data, metadata}`；不可读 data 为 null。resolve 后撤权或删除导致 read 抛 `permission_denied`、`file_not_found`、`resource_not_found` 时，同样返回 `{readable:false, ref, data:null}`，区域须清除旧正文与观察。其他服务、参数或版本错误继续抛出，底层 SDK read 不改变。成功时 `metadata.content_sha256` 为原始 bytes 的 SHA-256，YAML 与行锚点文本只有哈希相同才可配对；不一致重新读取整对。知识正文版本仍取 `metadata.record.version.number`。
- `resources.readable(items, getRef)`：只返回已鉴权可读条目。统计与连线必须在过滤之后建立；缺失前置仍是未知，不能删除前置后判为可执行。
- `onDispose(fn)`、`onFocus(fn)`、`watch(refs, callback)`：将清理函数纳入生命周期；晚到的 watch 结果也会取消。每个区域当前只有一组 watch，新调用替换旧组，调用前汇总全部 refs。原始 SDK 自身的订阅须用 onDispose 登记清理。包装后的清理函数保留 Promise；在途重复调用共用结果，成功只执行一次，失败允许重试。共享层为不等待结果的 hide/destroy 报告 `Region cleanup failed`，显式 await 仍会取得原错误。SDK 只将被后续 watch/stop 取代的旧 stop 取消作为正常无操作；权限、网络和未被取代的取消仍是失败。

语义对象沿用 `{ref, kind, status, source, observed_at}`。ref 保留真实资源文件及行锚点；模块 kind 使用 `module:<id>`，方向使用 `direction:<id>`，逻辑身份为文件地址与局部 id 的组合，刷新时按该身份重算行锚点，不用显示名或旧行号充当稳定身份。`status` 区分 current/proposed/simulated/verified，无证据不标 verified。三个区域均 publish/subscribe focus；`sdk.context().focus` 提供当前快照。反馈只通过 SDK 交给宿主确认。

区域冻结、废弃来源版本基线或改变本地选择时调用 `await sdk.cancelFeedback()`，使旧确认及发送前检查失效；返回 `{cancelled: boolean}`，原 `sdk.feedback(...)` Promise 以 `projection.cancelled` 拒绝。宿主还会在隐藏/销毁、focus 更新、watch 来源变化时自动取消，并在确认后和原生 Chat 准备提交时重验。区域仍须保留发送前全源版本核对和 epoch 守卫；取消不自动重试，也不能撤回已经发出的消息。

架构源码通过 `repo_<id>/file/<路径>` 读取当前工作区内容，按当前用户鉴权；架构页面没有提交候选、历史架构与提交比较入口。完整约定见上级 sdk.md 的“区域工程接入”。

阶段成果直接从汇报 Workspace 的 `reports/stage-index.json` 发现，按其中的报告与 content_ref 读取。成果身份使用报告、模块、改善 ID；头条与改善只有正式 item_links/improvement_ref 且报告字节哈希吻合时才合并。content 中的 experience_targets 提供当前体验入口；展开层使用正式 read_ref 与哈希读取当期提交说明、固定 Design 正文和历史截图。日期筛选使用所属报告结束日（香港时区），支持包含两端的范围、单边范围与清除，可与模块和搜索条件组合；没有成果已读、未读、阅读记录或阅读水位。

阅读现场在面板仍可测量时记录，避免宿主先隐藏 iframe 后取得的零尺寸、零滚动覆盖状态。显示时重新鉴权读取正文，再恢复展开、滚动和草稿；来源撤权时清除旧内容。跨区保持真实对象身份；来源内容和权限核对完成后才应用晚到结果，不为领域内容在共享层另存一套事实。具体数据映射、历史浏览和区域布局由各区按当前 Design 维护。

正文统一使用 `import { renderMarkdown } from '../shared/markdown.js'`，调用 `renderMarkdown(root, text, {onLink})`。marked 解析后由 DOMPurify 按正文标签白名单清理，不保留脚本、图片、样式、表单或事件属性。默认链接为带地址提示的文字；提供 onLink 时，仅资源地址或 HTTP(S) 链接变成按钮，由调用方通过既有宿主/SDK流程处理。渲染器不导航、不加载外部内容。

## 主题与图形

应用使用宿主提供的 `--bg`、`--panel`、`--text`、`--accent`、`--border` 等主题变量，公共控件使用 `portalButton`、`portalField`、`portalToolbar`、`paneHead`、`paneBody`。局部布局可自由组织，禁止重写基础主题、品牌与全局导航。宿主同步主题切换。

`d3`、`@dagrejs/dagre`、`cytoscape` 在 package.json 中声明，安装时解析可用版本。区域使用常规 import，构建时打包进入口。依赖变更由平台维护。每个入口旁可放 `layout.worker.js`，构建器自动打包为 `<区域名>.worker.js`；架构区使用 `await sdk.createWorker('architecture.worker.js')`。示例保持 `layout.worker.js` 产物名。Worker 使用 postMessage 通信，退出时 terminate；Worker 无 SDK、会话凭据和网络能力，需要的 npm 库直接 import 以便打包。使用者无需改共享配置。

Lineage 共用画布底层通过 `await sdk.loadLibrary('portal-graph')` 加载，读取 `globalThis.PortalGraphGeometry`。它提供世界/屏幕坐标转换、可见边界、视图中心、贝塞尔连线与命中计算；位置以 100 单位对应 worldSize 的宽高。不要从应用目录跨路径导入 Portal 源码。`sdk.icon(name)` 返回公共图标 SVG 节点；主题继承 currentColor。模板支持的图片 import 转为 data URL，避免运行时请求；媒体与字体运行边界见上表。

架构区另有明确登记的 `src/architecture/graph-layout.worker.js → architecture-layout.worker.js`，存在时自动构建，通过 `sdk.createWorker('architecture-layout.worker.js')` 使用。区域输入协议为 `{requestId, nodes, edges}`，只传权限过滤后的图；平台不解释布局协议。该命名与通用 `architecture.worker.js`、示例 `layout.worker.js` 独立。

## 构建与保存

`npm install --package-lock=false --ignore-scripts` 准备本地依赖，`npm test` 运行共享合同验证，`npm run build` 生成 dist。入口清单在 entries.json，每个入口输出同名 HTML/JS；CSS import 由构建器生成独立样式，入口须引用它。正式保存由 Portal 在无网络隔离环境按当前依赖声明重新构建，开发目录中的 dist 不作为发布依据。

工作台修订提交 `application`：

```json
{
  "repo_id": "repo_5d287fed9c7e",
  "directory": "management_portal/workbench_projections/development_app",
  "commit": "完整的40位Git提交哈希",
  "entries": {"architecture": "architecture.html", "progress": "progress.html", "evolution": "evolution.html", "packaging": "packaging.html"}
}
```

页面清单 sidebar 的 source 分别使用 `package:architecture`、`package:progress`、`package:evolution`、`package:packaging`，panels 为 `{}`，html 为空。平台示例可单独使用 entries `{example: 'example.html'}` 和 source `package:example`。保存成功后修订包含宿主生成的 application_build；失败不产生可发布修订。发布独立提交该已保存版本，旧在线版本在草稿构建期间继续运行。

在 Portal 仓库运行 `python3 -m management_portal.workbench_projections.development_projection --commit <完整SHA>` 可生成开发投影定义；加 `--example` 只生成平台示例。命令只输出定义，不修改或发布工作台。负责人使用 HATG UpdateResource 保存生成定义，并单独发布修订。

首版每个入口及 Worker 必须自包含，不支持运行时分块 import。HTML 可引用独立 JS/CSS；CSS 中的 import 必须在构建时展开，url 仅允许 data: 和 #fragment。模板已将常用图片与 woff2 字体配置为 data URL，剩余本地或外部 CSS URL 会使保存失败。宿主读取固定修订资产，再交给无同源权限的 iframe 执行；所有业务数据经过 SDK，CSP 禁止联网。

正式构建在隔离环境中解析和安装当前依赖，禁用生命周期脚本；应用源码构建阶段无网络，仅能只读访问本次专用缓存，无法读取宿主 npm 配置或凭据。每功能区默认 8 MiB，应用全部产物最多 64 MiB。完整限制和 SDK 见上级 sdk.md；构建日志、依赖缓存、不可变产物由平台管理，不写入源码仓库。

页面加载错误向使用者称为“工作台页面文件加载失败”；这些前端文件用于显示工作台，与用户请求制作的 Portal 发行版是不同的内容。站点目录配置改名或搬迁时，应保留仍被运行中服务读取的旧入口，直到相关服务全部采用新配置，避免文件仍在而读取位置改变。

Portal System 读取使用已有投影绑定机制。安装时设置 `bindings.packaging_system.lineage_id` 为实际 Portal System；生成定义支持 `deployment.development_projection.system_lineage_id`。`GetLineage` 的目标由 host 从绑定解析，不能靠参数覆盖 main；缺失或不可读绑定显示错误，不回退到其他团队。该绑定没有 Resident 消息或事件角色，不增加任务投递入口。

### v50 配置与运行实现

`release-session.js` 只管理配置导航和不确定回执，`release-api.js` 对接 `management_portal.distribution.api`；导出/阶段推进由发行服务维护，固定安装及完整验证由运行记录中指定的监督 Resident 执行。配置和运行 ID 均由服务生成；保存和启动各有冻结的请求 key，同次网络重试保持原参数。更新配置按修订检查，加载另一配置重建所有选择编辑器，旧 submission 始终独立保留。JSONB 对象键次序不被当成用户改动。保存未完成配置不要求版本/路径完整，启动前才做完整验证。

发布候选经 `releaseCandidates` 进入封闭的发布读服务；不更改通用资源/ISA权限。旧任务重试和团队对话仍沿原通路。运行界面只呈现服务端 ID、状态、负责/监督 Resident、阶段证据与产物；外部实例地址可选中复制，沙箱不提供无法打开的弹窗链接。首轮已存在运行从服务读取，不由页面重新启动。规范沿 SPEC.md 已登记的 decision_15aab18c0ab3 v50，旧的清单说明不代表页面实现了导出器；实际发行能力以运行证据为准。

### 目标平台选择

目标目录来自发布服务 targets({snapshot,features})，页面不维护能力矩阵。target_platforms 为非空多选，未知或未实现目标仍可保存；实现状态与验证状态分别呈现。新草稿初选 Linux；旧配置缺字段时以 legacy-linux 标明编辑来源，不改旧 run 快照。命名保存/加载/另存以及新运行快照保留目标，manifest 的 selected_targets/support_matrix 与平台验证证据取服务端事实，勾选不表示可安装或通过验证。

运行平台证据保留完整原记录（OS/架构、工程摘要、时间与证据引用），platform_results 按记录的平台及 scenario/operation 分别显示新装与升级验证；未记录不推断通过，Linux 新装结果不代替升级或其他平台。

发布候选桥接显式解析宿主 Lineage 绑定，并从真实资源属性保留 Project 归属。投影目录分批加载，每批共享重复的归属读取；选择和保存前核实不复用目录缓存。

运行结果后台轮询只刷新运行区域，不锁定清单表单；候选资源列表在滚动条或窗口失焦时保持打开，点击外部、焦点转到外部控件、选择资源或 Escape 才关闭。

### 功能带出与安装默认值（v53）

`feature-states.js` 消费 `candidates/release_features` 的真实目录、canonical ID、默认来源与依赖解析；`feature_states` 是新清单的唯一功能意图。可选功能、Portal System、原生 Test 与 Projects 页面控件共用同一状态，带出和安装后默认开启分别保存。未带出时禁用默认控件但保留其值；旧缺字段由后端迁移并显示未设置/初始化来源，不读取源站点运行开关冒充默认。代码依赖自动包含并显示来源，默认开启意图、依赖冲突和实际就绪分开显示；配置可保存待处理冲突，开始运行前重新核实。

命名保存、加载、修改、另存与草稿刷新保留两个值；旧 run 和 submission 不回写。能力矩阵使用后端解析后的功能范围。规范沿根 SPEC.md 登记的 decision_15aab18c0ab3 v53；实际安装初始化由发行服务和安装器处理，页面不会启停源功能。

已选资源行左侧呈现名称、ID 与所属 Project，移除实例位于同行右侧，长名称和窄屏可换行。当前运行和旧任务的后台状态轮询均不锁定选择表单。

旧 Projects/Test 页面布尔意图按既有控件的已确认功能 ID 转成新字段后交服务重新解析，保留其余迁移意图和默认 null。若目录缺失或服务仍无法解析、或所选 Society 模板必要功能尚未被服务解析，页面显示待核实并保留原选择，阻止以不完整解析发起运行；不会把缺失迁移结果保存为用户主动取消。

发布进度与产物仅展示已发起的发布/完整测试记录。候选初始化、面板重新显示、手动及后台记录刷新均不禁用整页；慢请求或重启错误只在读取区域提示，独立配置字段仍可编辑和保存。配置加载重建编辑器时清理旧交互锁及 aria-busy；实际提交和保存操作仍防止重复执行。

纯代码支持消费服务端 `kind=code_support` / `default_applicable=false`，隐藏默认启停控件并显示依赖支持说明；旧 `default_enabled` 原意保留但不冒充执行。运行功能与流程仍使用原生默认控件。依赖原因优先使用 `code_required_by_details` 的名称与来源，不以空 effective_enabled 冒充开启。

Lineage 名称使用已有归属核实读取的真实元数据，与搜索分页独立；保存和重新加载时再次核实。名称仅用于显示，ID及经历/Workspace范围保持原样；不可读时显示ID与名称待核实。

权限模板与实例沿现有三项候选操作读取，目录加载中、成功空列表、读取失败及尚未选择分别显示。模板与实例分别核实，空表项不判定为模板不可读；失败保留旧选择，提示读取失败而不推断权限不足。

发布页文字复制：候选资源名称与 ID 可拖选复制，拖选不加入清单，单击或键盘确认仍可选择。异步写操作只禁用编辑控件，说明文字与发布结果仍可选择；复制运行文字或地址时延后结果重绘，结束复制后继续显示新结果。
