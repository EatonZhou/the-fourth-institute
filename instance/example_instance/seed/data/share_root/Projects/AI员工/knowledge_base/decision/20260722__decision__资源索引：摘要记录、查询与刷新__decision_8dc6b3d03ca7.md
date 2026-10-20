---
kb_id: decision_8dc6b3d03ca7
type: decision
project: AI员工
created_at: '2026-07-22'
version:
  number: 37
  created_at: '2026-07-22'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:48:27Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 9
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/timing.py
  - ${code_root}/management_portal/resources/access.py
  - ${code_root}/management_portal/resources/service.py
  - ${share_root}/Skills/tfi-unified-id/SKILL.md
  - ${code_root}/management_portal/resource_index_backend.py
  - ${code_root}/management_portal/paper_resource_index.py
  - ${code_root}/management_portal/article_resource_index.py
  - ${code_root}/management_portal/portal_features.py
  - ${code_root}/tests/cases/test_resource_index_optional_features.py
  - ${code_root}/management_portal/resource_source_changes.py
  - ${code_root}/management_portal/resources/schema.py
  - ${code_root}/management_portal/resources/contracts.py
  - ${code_root}/management_portal/resources/index.py
  - ${code_root}/management_portal/resources/index_runtime.py
  - ${code_root}/management_portal/resources/query.py
  - ${code_root}/management_portal/search_identity_backend.py
  - ${code_root}/management_portal/resources/providers/search.py
  - ${code_root}/management_portal/resources/providers/test.py
  - ${code_root}/management_portal/test_framework/catalog.py
  - ${code_root}/management_portal/test_framework/resource_index.py
  - ${code_root}/tests/cases/test_resource_index_backend.py
  - ${code_root}/tests/cases/test_resource_source_changes.py
  - ${code_root}/tests/cases/test_resource_service_status.py
  - ${code_root}/tests/cases/test_search_identity_backend.py
  - ${code_root}/tests/cases/test_board_semantic_search.py
  related:
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_4a9ba5dfada6
  - decision_b6e74d78f7b6
  - decision_8273271b67b1
  supersedes: []
  derived_from: []
---
# 资源索引：摘要记录、查询与串行刷新

Portal 的资源列表、资源选择器、全局搜索框和语义搜索，需要把知识条目、Paper、Repo、Article、测试用例、Resident、工作区等不同来源的资源放进同一组结果，让用户按文字、类型、Project 和状态找到它们。资源索引（Resource Index）是为此维护的一张跨类型“资源摘要表”：每个资源一行，只记录用于查找和展示的少量信息。

资源索引运行在 ResourceService 进程中（`decision_cf27d39e39cd`）。本 Design 说明摘要记录包含什么、查询怎样筛选和授权、各领域的变化怎样进入索引，以及索引落后或损坏后怎样恢复。资源正文和业务状态由各领域保存；地址解析规则见 `decision_4a9ba5dfada6`，调用方法的参数与错误见 `decision_b6e74d78f7b6`。

## 索引的三条边界

1. **只保存摘要。** 标题、摘要、所属 Project、状态等用于查找和展示的字段进入索引；正文仍从领域读取。
2. **可以从领域重新生成。** 索引是领域事实的副本，丢失或损坏时从各领域重新扫描即可恢复，业务修改从不以索引为准。
3. **不授予权限。** 一条记录在索引中不产生访问能力。查询指令由权限系统按当前模板授权（decision_b820faf42218），候选还须通过当前领域事实、个人 AMA 私有性与业务可见性过滤。索引快照只是权限事实的一种来源；用户、Project、协作和功能记录由权限事实适配器另外收集。事实来源和实例维护以 decision_b820faf42218 为准，R 树放置规则以 decision_600ae27b0538 为准。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 领域来源 | 实际保存某类资源的文件、登记表或数据库，例如 Project 知识库目录、Paper 清单、Test Catalog |
| 适配器（Adapter） | 为资源索引读取一个领域来源、生成摘要记录的组件。每个适配器有名称，负责固定的资源类型和来源路径 |
| 摘要记录（ResourceRecord） | 索引中的一行，描述一个资源 |
| 索引存储（Index Store） | 保存全部摘要记录和各适配器状态的组件。记录保存在进程内存中，并持久化到 PostgreSQL 的资源 schema |
| 完整快照 | 一个适配器当前负责的全部摘要记录 |
| 局部变化（ChangeSet） | 一个适配器一次提交的若干新增、覆盖和删除 |
| 来源指纹 | 适配器对自己来源的状态计算出的摘要（例如文件路径与修改时间），用来判断来源自上次扫描后是否变化 |
| 来源变化检查 | 声明了来源范围的适配器在普通刷新时，只找出上次检查以来变化的目录和文件，再按路径读取它们，不再对全部来源计算指纹 |
| 刷新 | 让一个适配器重新读取来源并更新它的记录。强制的完整刷新总是重新读取；普通刷新在来源没有变化时跳过读取，有来源变化检查的适配器只读取变化的来源 |

## 摘要记录

每条记录使用 `tfi.portal.resource_index.v2` 格式，以规范的 `resource_id` 作为唯一身份。

| 分组 | 字段 |
| --- | --- |
| 身份 | `resource_id`、`resource_type`。Project、User 使用各自领域 ID；合法例外由类型注册表声明 |
| 展示 | `title`、`summary`、可选的 `url` |
| 来源 | `source.adapter`（负责它的适配器）、`source.path`（可定位的来源路径）、`source.modified_at`（来源修改时间） |
| 分类 | `facets`：去重后的分类列表，总是包含资源类型 |
| 公共属性 | `project_ids`、`read_scope`、`relations`、`status`、`created_at`、`updated_at` |
| 扩展属性 | `attributes`：领域增加的 JSON 值 |
| 检索词 | `search_terms`：只用于匹配，不返回给调用方 |

记录的字段规则：

- **只用 `resource_id` 表达根身份。** 记录中不能再用 `id` 或其他 `*_id` 字段重复它。
- **多值字段使用去重数组。** `project_ids` 和 `relations` 必须是数组；单数形式的 `project`、`project_id`、`owner`、`owner_id` 会被拒绝。每个关系只含小写下划线格式的 `kind` 和非空的 `target_id`，完全相同的关系只保留一次。
- **读取范围有固定取值。** `read_scope` 可选，只接受 `project` 或 `global`，省略时按 `project` 处理。
- **记录不携带可见性。** 谁能读取资源由权限系统决定；记录中出现 `visibility` 属性时，写入和加载都会拒绝这条记录。
- **时间与状态是标量。** `status` 统一为字符串；`created_at` 和 `updated_at` 必须是字符串或数值，布尔值不合法。
- **资源 ID 和标题是基本检索词。** `summary` 只用于展示，不进入 `search_terms`。
- **记录不携带所有者，Project 与读取范围不产生权限表项。** 资源没有所有者列表，谁能管理由权限系统判定；Project 与读取范围用于筛选和展示，其中所属 Project 等归属字段也是权限系统确定资源在 R 树上位置的事实（一条记录关联多个 Project 时取第一个）。入口权限由权限系统判定，领域解析器继续执行对象存在性及业务约束（decision_b820faf42218）。

共享和第三方 Skill 的 `project_ids` 为空；Project Skill 只登记真实 Project ID。`Shared Skills`、`Third-party Skills` 是页面分组标签，不是 Project ID。页面根据 `attributes.source` 的 shared/third_party 与 `read_scope: global` 分组，不把这些标签放入 Project 筛选或权限归属。

## 设计点

### 所有类型返回相同的结果结构

- **不同类型的资源返回相同的摘要结构。** 页面可以用同一种卡片或列表展示它们。
- **每项结果携带稳定的资源 ID。** 标题、文件路径和外部编号只用于展示和筛选。
- **默认不返回已删除的资源。** 查询没有指定状态时隐藏 `status` 为 `deleted` 的记录；显式查询 `deleted` 状态时可以取得。
- **默认不返回扩展属性。** `include_attributes` 为 `false` 时去掉完整的 `attributes`，列表响应不携带不需要的类型细节。

### 普通查询先粗筛、再授权、最后分页

- **第一步用索引中稳定的字段粗筛。** 只使用资源 ID、关键词 `q`、资源类型和全部 `facets`。索引在内存中维护按类型分组的资源 ID；指定类型时先取得对应分组，再筛选关键词和 facet，不遍历其他类型的记录。
- **第二步按需补齐描述并投影当前业务事实。** 粗筛命中的协作资源候选先补齐所需协作描述，再由索引统一应用领域的规范业务事实；同一候选批次只应用一次。没有协作候选时跳过这些领域读取。描述补齐与当前事实投影是两项职责，自定义描述投影不能省略规范事实。生命周期、Project、Family 关系及只读父级事实按各自领域的当前值参与后续筛选，索引旧值不能覆盖它们（见下一节）。
- **第三步完整筛选。** 应用 Project 交集、精确状态、隐藏已删除记录和属性交集规则。
- **第四步身份与业务可见性过滤。** 查询入口已由权限系统完成指令授权；索引先投影当前领域事实、应用查询条件与私有记录可见性（个人 AMA 只对所有者和代表所有者的 Resident 可见），再排序、计数和分页。ResourceAccessPolicy 只确认 User/Resident 身份；领域提供者继续负责自己的业务约束。
- **索引不缓存授权结果。** 每次指令授权使用当前权限实例与当前调用者。索引读取的一批当前领域事实可供本次查询过滤复用，但不是权限快照；详情或正文读取须重新经过其指令及领域边界。
- **第五步评分、排序、分页。** 关键词对标准文本字段和适配器提供的可检索属性评分；排序先比较匹配分数，再用确定的记录字段区分并列，同一索引状态下翻页结果稳定。需要的结果少于授权后的候选时，只排序前 `offset + limit` 项；`total` 仍精确计算。`limit` 默认 50，范围 1–5000；`offset` 从 0 开始。结果返回 `total`、`offset`、实际 `limit` 和索引更新时间。
- **只复制最终返回的记录。** 粗筛、协作状态替换、筛选和授权使用的候选是索引记录的浅层视图：查询只替换其中的顶层字段（例如 `url`、`attributes`、能力），不修改索引中的记录。分页之后，只有返回的记录被完整复制，复制前去掉 `search_terms` 和未请求的 `attributes`；无需当前业务事实的资源，其页面地址也只为结果页生成。各领域投影仅为自己负责的类型建立浅层视图，并为要替换的属性建立新字典；其它类型只读透传，不能在进入领域前递归复制整批候选。最终返回结果的嵌套内容与索引隔离，调用方修改结果不能污染下一次查询。
- **完整的资源地址直接解析。** `q` 是完整地址时，ResourceService 先按当前访问者解析它；解析成功后跳过关键词评分，但仍经过其他筛选和授权。

### 协作资源按当前生命周期显示

Resident、Lineage 和工作区的删除与恢复由协作领域（SocietyService）记录，索引可能稍后才更新。

- **普通查询使用协作领域中当前的 `status` 与 `deleted_at`。** 协作领域已经标记删除时，默认查询立即隐藏这个资源，即使索引中仍是旧的活动摘要；恢复为活动状态后，索引中旧的删除记录也不会阻止它显示。
- **精确读取与发现索引分开。** `get` 的描述优先、Resident/Lineage 旧索引兼容及 Workspace 描述缺失时拒绝读取路径，统一见 decision_b6e74d78f7b6。`resolve` 与 `reference` 按领域当前存在性处理；这些直接读取不修改索引或推进适配器进度。
- **查询中的生命周期替换发生在完整筛选、授权、计数和分页之前。** 删除排在前面的对象，不会出现“当前页少了一项、总数不变”或漏掉后面仍然活动的对象。
- **显式查询 `deleted` 状态时照常授权。** 仍要遵守当前指令授权、业务可见性与查询条件；查询中的 Project 是筛选条件，不授予权限。`get` 返回已删除的摘要时，这个对象已经不能作为活动对象解析或操作；`resolve`、`reference` 和先按地址解析的查询把它视为不存在。
- **当前事实读取不改写索引。** 查询不修改索引存储、不推进适配器进度，也不等待索引追上。没有协作描述记录的旧索引记录、非协作类型和未知类型不因缺少描述记录而被推断为已删除；各类型只投影其领域实际掌握的事实，未知字段不补造。
- **归属事实按需批量读取。** 需要 Resident 当前父 Lineage 或 Family 关系等只读事实时，只读取这些身份和归属字段；不为列表组装完整 Resident 工作区、扩展、运行设置或完整 Lineage 图。父级不存在时不补造父级事实。未知或已删除身份不作为活动事实返回；领域读取失败时报告失败，不回退到描述中的旧值。这种读取提供领域事实，不创建或替代权限系统的判定。

### 全局搜索建议

- **Ctrl/⌘ K 的建议只匹配资源 ID 和标题。** 不区分大小写，不使用 `summary` 和其他检索词。
- **可以限定一个类型。** 给出 `resource_type` 时先按类型缩小候选，再评分；只等待这个类型的适配器就绪，其他类型的故障不影响结果。
- **不可见的候选不占名额。** 返回前对候选逐批补齐描述、统一投影一次当前规范业务事实，再应用动态条件和当前访问者的权限；自定义描述投影不能跳过这一边界。结果最多为调用方指定的数量，每项包含摘要和匹配分数。建议结果没有总数和分页；返回的嵌套对象同样与索引隔离。

### 查询前等待相关适配器就绪

- **查询只等待本次涉及的资源类型所属的适配器。**
- **适配器从未成功生成过记录时，查询触发一次刷新并等待。** 仍未完成时返回 `ResourceServiceUnavailable`，不用空列表掩盖索引尚未建立。
- **成功过的适配器后来刷新失败时，继续使用上一次成功的记录。** 失败原因在健康状态中报告。

### 一个资源出错，只影响它自己

- **完整快照中某个资源读取或校验失败时，其他资源照常更新。** 适配器记下带资源 ID 或来源路径的错误，索引保留这个资源上一次成功的摘要。
- **失败的资源如果从未成功过，不生成占位记录。** 健康状态中记录 `resource_errors` 与 `resource_error_count`。

## 当前的适配器

| 适配器 | 负责的类型 | 来源 |
| --- | --- | --- |
| `project_knowledge` | 知识库条目：meeting、question、idea、insight、decision、plan、experiment、thinking、risk、concept、information、patent、material、review、todo | 各 Project 的知识库目录与 Project 登记表 |
| `skill` | skill | Skill 目录 |
| `paper` | paper | Paper 清单 |
| `paper_review` | review（Paper 评审） | Paper 评审记录 |
| `project` | project | Project 登记表 |
| `collaboration` | resident、lineage、workspace | 协作登记记录 |
| `resource_registry` | repo、article | Repo 与 Article 的资源登记表（`decision_36f46b395d8e`） |
| `device` | device | 设备登记表 |
| `saved_search` | search | 语义搜索记录（`decision_eb066fbea4ed`） |
| `test` | test | Test Catalog 与实例中的修改（`decision_8273271b67b1`） |
| `workbench` | workbench | 工作台登记 |

一种类型要能通过统一查询找到，需要同时安装它的领域提供器和索引适配器。索引能保存任何类型的记录，每个 Portal 页面仍各自决定展示哪些类型、提供哪些筛选控件和卡片样式。

### 可选功能的索引参与

核心索引只通过 PortalFeature 声明加载可选功能。 `resource.index_adapters` 接入点提供适配器类集合，Paper 的清单与评审适配器由 Paper 功能拥有；`resource.registry_projection` 提供一个领域投影源，声明资源类型、登记集合以及单条记录的校验和摘要生成方法。共享 `resource_registry` 适配器负责路由登记变化、全量和增量扫描及逐资源错误处理；Article 的存储表示和摘要规则属于 Article 投影源。

功能模块未安装时不创建其适配器，也不将其类型或登记集合列入共享适配器。Article 缺失不妨碍 Repo 的登记索引，Paper 缺失不妨碍其余资源的索引初始化与读取。功能模块存在但内部依赖缺失时，加载保留原异常，不把损坏的安装当作未安装。安装状态在进程加载时确定，改变安装集合后重载 ResourceService。

功能已安装时，适配器名称、登记同步投影、全量快照、增量变化键、摘要字段和逐资源失败语义保持相同；动态接入不创建另一份登记真值。

## 记录怎样进入索引

### 登记同步投影与三种刷新触发

```text
正式登记成功 ─→ 所属适配器生成单条摘要 ─→ 索引存储持久化 ─→ 登记返回并可立即查询

领域修改成功后的变化通知 ─┐
文件变化观察              ─┼─→ 刷新队列 ─→ 适配器读取来源 ─→ 索引存储提交 ─→ 查询与等待者看到新记录
启动与周期核对            ─┘
```

- **登记同步投影。** ResourceService 在领域提交并同步授权后，让所属适配器从已提交的单个对象生成摘要，直接写入索引存储；持久化完成后才返回登记成功。Knowledge 与 Experiment 按新文件路径读取，Repo、Article、Device、Project 和 Workbench 使用各自适配器的单对象投影。登记不等待串行刷新队列，也不要求扫描该类型的全部资源；索引投影失败时，返回带 `domain_committed: true` 和 `stage: index_projection` 的提交后错误，不把成功伪装为 `unconfirmed`。
- **变化通知。** 修改、撤销登记和领域外的资源变化，按资源 ID、类型或路径找到负责的适配器并请求刷新；登记表通知携带单条记录的变化键，由适配器读取这一条（`decision_b6e74d78f7b6`）。按路径寻找适配器时，变化路径只解析一次，各适配器根目录的解析结果复用 30 秒，大量并发通知不会因反复的文件系统调用而互相拖慢。
- **文件变化观察。** 索引默认观察各适配器声明的目录（环境变量 `PORTAL_RESOURCE_INDEX_WATCH` 设为 `0`、`false` 或 `no` 时关闭）；文件变化后，按路径找到负责的适配器并刷新这个路径。观察事件溢出、可能漏掉变化时，对全部适配器请求强制刷新。每次完整扫描后，观察的目录随来源更新。
- **启动与周期核对。** 服务进程启动时，对全部适配器请求一次普通刷新，停机期间的修改因此立即补上。之后按时钟每 300 秒核对一次（可用 `PORTAL_RESOURCE_INDEX_RECONCILE_SECONDS` 调大，小于 300 时按 300），对当时既未排队也未运行的适配器请求普通刷新；已经排队或运行的适配器完成自己的刷新。核对不要求队列先空闲：知识库持续编辑时队列可能一直有请求，文件变化观察漏掉的修改（例如新 Fork 的 Resident）仍在一个周期内补上。

### 刷新队列

- **停止服务会取消尚未提交的扫描并唤醒等待者。** 索引不再接收或领取刷新任务；目录遍历和逐项读取在边界检查停止信号，来源变化检查的子进程被终止并回收。取消保持上次完整快照、代次与健康状态，不把部分扫描当作空快照提交，也不把取消记为来源故障。已开始的完整索引提交先完成；后台线程退出后再做最后的持久化。单次文件系统或数据库调用仍须返回，超过停止时限则报告失败，不与存活的刷新线程并发执行最终持久化。
- **每个 ResourceService 进程由一个后台工作线程串行处理刷新队列。** 当前适配器完成扫描、提交和持久化后，才开始下一个适配器；线程数量不随适配器数量增长。扫描期间到达的变化继续排队，同类型的待处理请求合并。慢扫描会延迟后续适配器的更新，已有成功快照仍用于查询；排队耗时与扫描耗时分别报告。停止服务取消当前扫描，等待工作线程退出后完成最终持久化。
- **同一个适配器排队中的请求合并。** 首个请求进入后保留 100 毫秒合并窗口，后到的请求不延长窗口；扫描期间的变化进入下一批。 合并后只要有一个请求是强制的就强制刷新，只要有一个请求要求完整刷新就完整刷新，否则合并需要刷新的路径。
- **慢刷新和失败写入日志。** 各适配器的等待和执行耗时分别记录。一次刷新排队或执行达到 5 秒时，向 stderr 写一行以 `slow resource index refresh:` 开头的 JSON，记录适配器、排队时间、执行时间、按路径刷新的路径数（完整刷新为 0）和队列中剩余的请求数。刷新抛出异常时，错误记入适配器状态，同时写入带堆栈的日志。
- **Paper 的扫描、变化检测与路径路由使用同一个配置目录。** 来源取当前 PaperStore 的 items 目录，独立部署与测试可以配置自己的论文存储。
- **适配器名称必须存在。** 请求未知适配器，或路径不属于任何适配器时，返回输入错误。

等待刷新时捕获调用进入时各适配器对应的批次，只等待这些批次完成持久化并报告结果。后来到达的变化不会无限延长本次等待；本批失败返回失败，后续成功不能冒充本批成功。带文件路径的显式刷新沿用该路径做增量读取，不先扩大为整个适配器的刷新。

### 一次刷新怎样执行

投影代码指纹覆盖核心索引、已参与的功能适配器和登记投影源，以及直接使用的 Knowledge、Skill 文档解析器。功能投影源的参与集合也进入指纹。解析器改变标题或摘要规则后，原文件即使没有修改也需要重新投影；下一次普通检查会重新读取完整来源，不能继续保留旧解析规则生成的摘要。

1. **有路径、且适配器支持按路径读取时，只读取这些路径。** 适配器返回一个局部变化；索引只提交与现有记录确实不同的新增和覆盖，以及确实存在的删除。某个路径读取失败时，按路径逐个重试。
2. **有来源变化检查的适配器只读取变化的来源。** `project_knowledge`、`paper` 和 `paper_review` 声明自己的来源范围：从哪些目录向下列几层、只列哪些目录名、匹配哪些文件名，以及哪些单独的文件。Paper 评审列表保存在数据库表 `paper.reviews` 中，没有可供检查的文件；Paper 后端修改评审列表后，按资源类型 `review` 发出变化通知，`paper_review` 适配器收到后做一次强制的完整刷新。普通刷新时，索引启动一个子进程（`resource_source_changes.py`）列出这个范围，报告 inode 变更时间（ctime）不早于上次检查开始前 2 秒的目录和文件，并检查已有记录的来源文件是否还在。写入、新建、改名和权限修改都会更新文件的 ctime，目录中增删条目会更新目录的 ctime。子进程单独运行，所以列出数万个文件时不占用服务进程的解释器锁，也不拖慢同时到达的查询。没有变化时只记录检查时间；有变化时，把变化的目录和文件、消失的来源和等待期间加入的路径交给适配器按路径读取，结果与完整扫描相同。以下情况改为读取完整快照：适配器还没有按这种方式检查过；索引代码（`resource_index_backend.py`）或适配器读取的登记表版本在上次检查后变了；适配器无法单独应用这些路径（例如同一评审 ID 出现在另一个文件中）；收到强制的完整刷新（观察事件溢出、刷新接口）。带路径的强制刷新仍按第 1 步处理。
3. **其他适配器比较来源指纹。** 普通刷新时，如果适配器已经有成功的快照，且来源指纹与上次相同，就只记录检查时间，不重新读取。
4. **需要时读取完整快照。** 适配器枚举它负责的全部资源；索引在一次提交中写入新增和更新，并删除上一版存在、这一版缺失的记录。

### 提交与进度

- **一次提交要么全部可见，要么全部不可见。** 查询者只能看到提交前或提交后的完整状态。提交校验失败时，不推进进度，也不暴露一半的记录。
- **每个记录只属于一个适配器。** 适配器不能删除或覆盖别的适配器的记录。
- **每次成功提交推进这个适配器的版本代次（generation），并记录成功时间。** 代次是确认来源进度的依据；`projection_state(adapter)` 返回它。
- **记录先在内存中批量更新，持久化后再发布变化。** 后台刷新完成后，索引把记录写入 PostgreSQL，再通知等待者和订阅者。
- **早于同步登记开始的扫描不能覆盖登记结果。** 索引记录单条同步投影的代次；后台完整快照或局部变化提交时，跳过扫描开始之后已被同步投影的资源 ID，防止旧扫描结果把刚登记的记录删除或改回旧值。
- **查询不等待数据库写入和刷新中的复制。** 索引存储的内存锁只用于交换记录引用：持久化在锁内取出待写的变化，数据库写入及其与另一 Portal 实例共享的 schema 级 advisory lock 都在锁外完成；刷新在锁内取得现有记录的引用，复制、比较和来源路径解析在锁外进行。存储中的记录只整体替换、从不原地修改，所以这些引用在锁外仍然一致。写入期间产生的变化以更新的代次等待下一次持久化；写入失败时，没有写入的变化放回待写集合，已被同一记录或适配器的更新变化取代的除外。
- **记录变化后广播一条通知。** ResourceService 进程通过 Portal 的通知总线发布 `projection_updated` 事件，内容包括适配器名称、新增与删除的数量，以及变化的资源 ID。变化超过 128 个时不列出 ID，改为 `snapshot_required: true`，订阅者需要重新读取完整结果。事件只用来提示变化，查询仍以索引为准。

## 持久化、健康与恢复

- **新建索引存储写入 `tfi.resource-index-store.v1` 标记**，保存在资源 schema 中。已有标记必须精确等于 `tfi.resource-index-store.v1`；初始化遇到既有行不更新 value，不因启动或重试改写历史 marker。读取校验不执行数据迁移、不改变数据库 DDL 或版本号。
- **健康状态按适配器报告**：记录数、版本代次、最近检查、最近成功、来源级错误和 `resource_error_count`，以及正在运行和排队中的适配器。
- **慢查询与锁等待写入服务日志。** 查询超过 0.3 秒时报告各步骤耗时，索引内存锁的等待或持有达到 0.2 秒时报告等待者与持有者，变化路由分别计时身份检查、适配器匹配和索引查找（格式见 `decision_cf27d39e39cd`）。
- **进程重启后先使用上次持久化的快照。** 查询立即可用，随后每个适配器做一次普通刷新：有来源变化检查的适配器只读取停机期间变化的来源，其他适配器按来源指纹决定是否重新读取。索引代码变化后的第一次刷新会完整扫描一次。

故障按范围处理：

| 故障 | 处理 |
| --- | --- |
| 单个资源读取或校验失败 | 记录资源级错误，保留它上一次成功的摘要，其他资源照常更新 |
| 适配器无法读取来源或无法确定来源范围 | 保留上一次成功的快照，记录错误，下次刷新重试 |
| 提交校验失败 | 不推进代次，不暴露部分记录 |
| 变化通知丢失 | 周期核对按来源变化检查或来源指纹发现变化并刷新 |
| 索引存储损坏或丢失 | 新建存储，所有适配器完整扫描重建；不会从索引反向修改领域 |

## 状态归属

| 状态 | 保存者 |
| --- | --- |
| 正文、业务状态、是否删除 | 各领域来源 |
| 摘要记录、适配器代次与刷新健康 | 资源索引 |
| 资源 ID 与地址语法 | 共享资源类型注册表（`decision_4a9ba5dfada6`） |
| 能否发现、读取、管理 | 权限系统与领域授权 |
| 页面展示哪些类型、卡片与筛选控件 | 各 Portal 页面 |

新增一种资源类型时，实现它的领域提供器和索引适配器即可；已有页面不需要知道它的存储方式。

TODO 与 Review 知识文件均由 `project_knowledge` 适配器收录，并带 `knowledge` facet。TODO 的稳定身份取 front matter 的 `id`，其他知识类型取 `kb_id`；缺失身份的文件不从文件名推断 ID。TODO 的 `assignees`、`task_status`、`finished_at`、`archived_at` 直接取自 front matter，其中 `task_status` 投影为资源的 `status`。索引完整扫描与按路径增量刷新使用同一份文件解析，删除文件时移除记录。文件格式见 `${share_root}/Skills/manage-knowledge-base/archive-todo-item.md`。

按索引解析的资源若带 `knowledge` facet，地址解析委托给 Knowledge 的文件与正文解析器，使 TODO、Review 与其他知识条目提供一致的详情、下载和嵌套文件地址。Paper 评审保留自己的 facet 与论文页面入口。
