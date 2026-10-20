---
kb_id: decision_cf27d39e39cd
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 18
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T20:29:27Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/timing.py
  - ${code_root}/management_portal/resources/providers/chatroom.py
  - ${code_root}/management_portal/resource_auto_registration.py
  - ${code_root}/management_portal/resources/runtime.py
  - ${code_root}/management_portal/resources/service.py
  - ${code_root}/management_portal/resources/server.py
  - ${code_root}/management_portal/resources/transport.py
  - ${code_root}/management_portal/resources/providers
  - ${code_root}/management_portal/resources/providers/test.py
  - ${code_root}/management_portal/resource_index_backend.py
  related:
  - decision_4637ca4a69e2
  - decision_b6e74d78f7b6
  - decision_4a9ba5dfada6
  - decision_8dc6b3d03ca7
  - decision_b820faf42218
  - decision_36f46b395d8e
  - decision_96a887d83e8a
  - decision_8273271b67b1
  supersedes: []
  derived_from: []
---
# ResourceService 模块与运行架构

Portal 中的持久内容分属许多领域：知识条目、Paper、Repo、Article、设备、语义搜索、测试用例、工作台、工作区、Project，以及 Resident 和 Lineage 的身份。每个领域用自己的方式保存数据，例如 Markdown 文件、清单文件、Git 仓库或数据库表。页面、Resident 和后台任务都需要用同一套规则查找、读取和修改这些内容：同一个地址指向同一个对象，同一个人在任何入口看到相同的权限结果。

ResourceService（资源统一服务）是承担这件事的 Portal 服务。它按统一的资源 ID 和地址（`decision_4a9ba5dfada6`）接收请求，检查权限，把请求交给拥有该资源的领域实现，再把结果整理成统一格式。每个 Portal 实例（Dev 与 Release）各运行一个独立的 ResourceService 进程。

本 Design 说明 ResourceService 由哪些部分组成、一次读取或修改怎样完成、各项状态归谁保存，以及故障后怎样恢复。每个方法的参数、返回结果和错误由 `decision_b6e74d78f7b6` 逐项说明；资源索引的内部机制由 `decision_8dc6b3d03ca7` 说明。

## 基本概念

- **资源**：在 ResourceService 中登记、拥有统一 ID 的对象，例如 `decision_*`、`paper_*`、`repo_*`、`workspace_*`。资源的正文和业务字段保存在它所属的领域中。
- **访问者（actor）**：一次请求代表的身份，可以是 Portal 用户或 Resident。访问者由认证入口确认后随请求传入，ResourceService 按它判断权限。
- **领域提供器（Provider）**：把一种资源类型接入 ResourceService 的领域实现。提供器按能力分为三种，一个领域只提供自己实际支持的能力：
  - **解析器（Resolver）**：把地址解析成领域对象，可以附带读取正文和按字节区间读取正文的函数；
  - **变更提供器（Mutation Provider）**：实现登记、修改和撤销登记；
  - **动作提供器（Action Provider）**：实现该类型独有的操作，例如 Paper 提取参考文献、Project 保存摘要。动作名写作“对象.动词”。
- **资源索引（Resource Index）**：把各领域的资源摘要汇总成一张可以跨类型查询的表，用于搜索、列表和建议。它可以随时从各领域重新生成。
- **索引适配器（Adapter）**：为资源索引从某一个领域来源生成摘要记录的组件，每个来源一个。
- **变化提示（change hint）**：领域修改成功后，ResourceService 发给资源索引的一条通知，说明哪个路径、ID 或类型发生了变化，让对应的适配器及时刷新。
- **权限实例**：权限系统在 PostgreSQL schema `permission` 中保存当前模板生成的 P、A、R 三棵树、权限表、创建者与管理员等事实和临时凭证，由 ResourceService 进程内的权限端口维护和判定。所有者等业务事实由各领域提供，资源索引不另存一套权限或 visibility。规则见 decision_b820faf42218。
- **登记账本（Registration Store）**：为“由服务分配 ID”的登记保留 ID 的数据库表，保证重试同一个登记请求得到同一个 ID。

## 设计点

### 所有调用方使用同一个 ResourceService

- **Portal 页面、Resident 和 Portal 的其他服务都通过 ResourceService 访问资源。** 它们不自己扫描文件、解释 ID 或判断资源权限。
- **页面读取通过 HTTP 接口，修改通过 HATG ISA 指令。** 浏览器用 `GET /api/resources/resolve`、`/api/resources/reference`、`/api/resources/content`、`/api/resource-index/query` 等接口解析、读取和查询；登记、修改、上传和资源动作都作为 ISA 指令（`decision_ad3e21094f14`）提交到 `/api/instructions/execute`。
- **Resident 通过 ISA 资源指令使用 ResourceService。** `SearchResources`、`ReadResource`、`RegisterResource`、`RunResourceAction` 等指令由 ResourceService 自己执行（`decision_898b8911a2ff`），Resident 与页面得到相同的规则和结果。
- **其他 Portal 进程使用同名方法。** SocietyService、ExecutionService 和 Portal Web 通过 Unix socket 客户端调用 `query`、`resolve`、`register`、`action` 等方法，参数、返回结构和错误含义与进程内调用相同。

### 业务状态留在领域，资源索引只是可重建的查询副本

- **正文、业务字段和状态变化规则由各领域保存。** 例如知识条目的 Markdown 文件、Paper 的清单、Repo 的 Git 工作区、Test Catalog 和 Lineage 成员关系。
- **资源索引只保存用于查询的摘要。** 读取正文时，ResourceService 始终回到领域的正文读取函数，不从索引摘要读取。
- **描述补齐与当前业务事实各有归属。** ResourceService 为查询和建议补齐所需协作描述，索引统一在动态筛选前投影领域的规范业务事实，同一候选批次只应用一次；调用方的描述投影不能替代这一步。owner 与父级归属等列表所需事实按需批量读取，不为授权组装完整工作区、扩展或 Lineage 图。事实读取与权限裁决分开，仍由现有授权系统判断每次请求的权限，返回对象与索引存储隔离。
- **登记与索引摘要在一次请求中完成。** 领域提交后，ResourceService 让所属适配器生成单条摘要并持久化，再返回登记成功；登记不等待后台刷新队列。修改、撤销登记和领域外变化仍发送变化提示，提示失败或等待超时不会撤销领域事实，周期核对补上遗漏的更新。比登记更早开始的扫描不能覆盖这次单条投影。

### 每项能力按资源类型分别提供

- **可解析、可读取正文、可登记、可修改、可撤销登记和可执行动作是彼此独立的能力。** 类型没有提供某项能力时，请求返回明确的“不支持”。
- **能力由类型声明与已安装的领域提供器共同决定。** 声明文件、部署状态和有效能力的唯一契约见 decision_355ed742bca5；调用方使用 GetResourceType，不维护静态插件能力表。
- **专用创建与通用删除适配器可以并存。** Chatroom、Paper、Search、Resident 和 Lineage 的专用创建入口保持各自领域规则；已安装的 `UnregisterResource` 适配器复用领域删除流程，不能只删索引。Resident/Lineage 的普通创建和修改仍走专用指令，回收语义见 decision_4a9ba5dfada6「创建与回收的生命周期」。Chatroom 的解析与正文是摘要 JSON，资源索引不收录它（decision_a6bebf4ac283）。
- **Workspace 元数据更新和 Test 生命周期保持领域合同。** Workspace 更新标题必须带版本令牌（decision_8838269736d3）；Test 的用例生成、退役与恢复见 decision_8273271b67b1。

### 权限在每次操作时由授权系统判断

- **每个 ISA 入口由权限系统判断调用者能否执行。** 资源门面校验已认证的 User 或 Resident 身份，并应用当前领域的读取限制；列表能力提示不构成独立的授权真值。
- **资源授权由权限系统决定。** 当前模板的指令映射写明每条指令对哪个参数要求哪个等级，PAR 引擎按调用者自身的表项判定；未分类的权限要求按当前方案 A/R 树的默认节点判定，不因缺少映射而免检；显式免检分类仍按方案判定（decision_b820faf42218）。
- **所需等级由当前模板声明。** 资源门面不为普通修改硬编码另一套 Project contributor/admin 门槛；管理员专属指令的目标与等级以其正式权限声明和当前方案为准。
- **领域可以增加自己的限制，不能放宽 ResourceService 已经拒绝的权限。**
- **能力提示只说明入口是否存在。** 解析结果和引用中的 `edit`、`save`、`download` 提示是否安装了相应能力；真正执行时重新检查权限和对象状态。

### 由服务分配 ID 的登记在重试时保持同一个 ID

- **知识条目、Experiment 和工作台的登记由 ResourceService 分配 ID。** 这些提供器声明“服务托管登记”。
- **同一个登记请求重试得到同一个 ID。** ResourceService 用访问者、资源类型和请求内容（按键排序后的 JSON）计算指纹，第一次请求时在登记账本中为它保留一个 ID；之后键的顺序不同、内容相同的重试，以及进程重启后的重试，都使用这个 ID。提供器必须以这个 ID 完成登记。
- **撤销登记成功后才释放保留的 ID。** 撤销失败时继续保留，下一次重试仍指向同一个对象。
- **调用方不能提交或读取内部登记键。** 请求中包含 `registration_key`，或内容无法转换为 JSON 时，在分配 ID 之前拒绝。

## ResourceService 由哪些部分组成

```text
 Portal Web（HTTP 读取接口、指令执行地址）   Resident 的 Harness 工具   SocietyService、ExecutionService
                     │                               │                              │
                     └──────────── Unix socket（资源方法、instruction.execute、权限方法）────┘
                                                     │
                                          ResourceService 门面
             ┌──────────────┬──────────────┬─────────┴────────┬──────────────┬──────────────┐
          权限端口与实例      领域提供器目录     资源索引与适配器      登记账本       变化提示分发
       （树、权限表、临时凭证）（解析/变更/动作）  （PostgreSQL 快照）   （PostgreSQL）
                                    │
                    各领域实现（知识库文件、Paper、Repo、Article、设备、测试、Project……）
```

| 组件 | 职责 | 源码位置 |
| --- | --- | --- |
| 门面 `ResourceService` | 校验地址和请求内容，检查权限，选择提供器，统一结果格式，发送变化提示 | `resources/service.py` |
| 运行组合 | 按运行模式创建唯一的门面实例，并注入其他组件 | `resources/runtime.py` |
| 领域插件目录 | 声明每个插件负责的类型和能力，按类型加载 | `resources/providers/__init__.py` |
| ISA 指令实现 | 执行资源类、权限类指令和 Project 指令，以及上传与工作区文件写入 | `resources/isa.py` |
| 权限端口 | 按当前模板维护权限实例，回答“能否执行”，处理权限指令 | `permission_system/` |
| 资源索引 | 汇总各领域摘要，提供查询、建议和状态 | `resource_index_backend.py`、`resources/index.py` |
| 登记账本 | 为服务托管登记保留 ID | `resources/registration.py` |
| 服务端与客户端 | Unix socket 服务端、带签名的客户端 | `resources/server.py`、`resources/transport.py`、`resources/rpc_auth.py` |

## 运行方式

### 三种运行模式

ResourceService 按环境变量 `PORTAL_RESOURCE_SERVICE_MODE` 选择运行模式，默认 `client`：

| 模式 | 用在哪里 | 结果 |
| --- | --- | --- |
| `server` | ResourceService 服务进程 | 创建进程内的门面，并通过 Unix socket 对外提供方法 |
| `client` | Portal Web、SocietyService、ExecutionService 等业务进程 | 创建 Unix socket 客户端，远程调用服务进程 |
| `local` | 测试和维护脚本 | 在当前进程中直接创建门面 |

未知模式直接报错。客户端找不到 socket 或连接失败时报告服务不可用，不在本进程中另建一个 ResourceService。socket 位于实例目录下的 `resource-service.sock`，也可以用 `PORTAL_RESOURCE_SERVICE_SOCKET` 指定。

### 服务进程

- **每个连接由一个线程处理。** 请求是一行 JSON；普通结果以一行 JSON 返回。读取正文时先返回一行描述（长度、内容类型和解析结果），再发送该长度的字节。
- **同一个 socket 提供三类方法：** 资源方法（`resource.query`、`resource.resolve`、`resource.register` 等，见 `decision_b6e74d78f7b6`）；HATG 指令方法 `instruction.execute`（`decision_96a887d83e8a`）；授权系统的方法。
- **内部请求由共享签名组件认证。** 包括无业务访问者的健康与控制请求在内，部署服务按 decision_96a887d83e8a 的认证、密钥与防重放合同处理；ResourceService 带访问者的方法从认证块取得 actor，拒绝参数重报身份。资源专用帧与错误见 decision_b6e74d78f7b6。
- **启动后在后台预热。** 服务进程先读取资源索引状态；状态读取完成前返回 `warming`，完成后标记就绪，再调用 `ResourceService.warm()` 预热查询路径。索引还没有可用快照时每 2 秒重试一次，最多 120 秒；查询预热异常写日志，不撤销已建立的服务就绪状态。索引预热后，权限端口按 decision_b820faf42218 合并内置规则的发布变化并补齐现有对象，保留实例中的有效编辑；不能把启动理解为无条件重建自定义方案。事实来自用户、Project、协作记录、功能事实接口和索引。服务进程同时运行 Repo 与 Article 的自动登记任务（`decision_36f46b395d8e`）。

### 按类型加载领域提供器

- **第一次用到某个类型时，只加载负责这个类型的插件。** 解析、变更和动作三种能力分别加载，互不影响。
- **同一类型的并发首次请求只加载一次。** 加载在每种能力各自的锁内进行。
- **加载失败时整体撤回。** 一次加载中任一绑定失败，本次新增的全部绑定都撤回，这个类型保持未加载，下一次请求重新尝试。
- **同一类型、同一能力只能有一个提供器。** 重复绑定默认报错，显式声明替换时才覆盖。提供器声明的资源类型必须已在共享类型注册表中登记。
- **没有专门解析器的类型由通用解析器处理。** 只要资源索引收录了这个类型，就由基于索引的通用解析器解析。

## 三种主要工作流

### 读取一个地址

```text
调用方提交地址和访问者
        ↓
ResourceService 校验地址语法，加载该类型的解析器
        ↓
领域解析器找到对象或内部目标
        ↓
ResourceService 统一结果格式，检查读取权限
        ↓
返回解析结果或简短引用；读取正文时调用领域的正文读取函数
```

领域解析器返回的根 ID、类型和地址必须与请求一致，不一致的结果按错误处理（`decision_4a9ba5dfada6`）。

### 查询一组资源

ResourceService 负责精确地址分流和协作描述补齐，索引负责候选筛选、当前领域事实投影、业务可见性、排序与分页。查询和建议的完整阶段及结果隔离规则统一见 decision_8dc6b3d03ca7；方法输入与输出见 decision_b6e74d78f7b6。领域事实不等于权限快照，授权结果不跨请求复用。

### 修改资源或执行领域动作

```text
调用方提交资源 ID（登记时为类型）、请求内容和访问者
        ↓
ResourceService 检查权限和请求内容
        ↓
选择变更提供器或动作处理函数，由领域执行
        ↓
登记：核对返回记录的 ID 和类型，登记权限关系，并同步投影单条索引摘要
修改、撤销登记：核对返回记录的 ID 和类型，同步授权记录，发送变化提示
动作：核对返回值是对象后直接返回
```

- **变更必须返回规范的资源记录，ID 和类型与预期一致。** 登记成功后，ResourceService 为新资源建立授权记录。
- **提交状态由调用协议统一定义。** 领域提交后错误、`domain_committed`、`stage` 和 `index_projection` 的字段与调用方重读规则，统一见 decision_b6e74d78f7b6「等待索引确认」及「一次变更怎样完成」。登记同步投影与后台刷新的并发保护由 decision_8dc6b3d03ca7 定义。
- **动作由领域拥有。** 通用接口核对返回对象，会改变状态的动作由领域通知索引；输入、幂等与结果见各领域动作契约。

## 故障与恢复

- **健康状态按来源分别报告。** `status` 返回服务版本、构建 ID、预热状态，以及每个索引适配器的最近检查、最近成功、错误和记录数；`projection_state(adapter)` 返回单个适配器的刷新进度。
- **故障只影响对应的范围。** 某个适配器扫描出错，只影响该来源，索引继续提供它上一次成功的结果；某个资源的解析出错，只影响这个资源；授权存储不可用时，依赖它的权限判断明确失败。空结果不会被用来掩盖这些错误。
- **索引从未成功生成时，读取会触发一次生成。** 仍然失败时返回 `ResourceServiceUnavailable`。
- **进程重启后从持久状态继续。** 资源索引从 PostgreSQL 中上一次成功的快照继续提供查询，登记账本继续为重试保留同一个 ID，领域提供器在需要时重新加载。
- **遗漏的更新由周期核对补上。** 领域修改已经提交、变化提示却丢失时，适配器的周期核对会重新生成对应记录；索引数据丢失时可以从各领域完整重建。
- **慢请求自己报告耗时分布。** 下列情况各向 stderr 写一行以 `slow <标签>:` 开头的 JSON，请求步骤计时以 `ResourceService slow` 开头并包含总耗时和各步骤耗时，用于定位并发下的等待来源：
  - 查询超过 0.3 秒：快照、粗筛、协作描述投影、完整筛选、访问能力计算、筛选排序、输出复制和规范打开地址生成，以及候选数和返回数。排序与复制保留合计耗时，并分别记录 `select_and_rank`、`copy_results`；链接生成独立记录 `canonical_urls`，避免把这一阶段误归入复制；
  - 协作资源同步超过 0.5 秒：准备、描述记录、权限关系同步与通知，以及已记录的锁等待、读取、写入、校验和提交等子步骤；
  - 资源索引中启用计时的锁，等待或持有达到 0.2 秒时，写出等待者与持有者的代码位置。

  报告只在超过阈值时产生，不改变请求结果。

## 状态归属

| 状态 | 保存者 |
| --- | --- |
| 资源 ID 与地址语法 | 共享资源类型注册表（`decision_4a9ba5dfada6`） |
| 资源正文、业务字段和状态变化 | 对应领域 |
| 跨类型查询摘要 | 资源索引（可重建） |
| 所有者与领域归属 | 对应领域的规范业务记录 |
| 权限树、权限表、创建者与管理员事实、临时凭证 | 权限系统，PostgreSQL schema `permission` |
| 服务托管登记保留的 ID | 登记账本 |
| 已退役、禁止重新使用的资源 ID | `resource_registry.retired_identities`，只保存 ID |
| 提供器目录与运行模式 | ResourceService 运行组合 |

资源规范写入边界通过 `resources/retired_identity_boundary.py` 检查退役 ID，拒绝重建对应的有效身份或从该身份继承。删除状态仍可正常投影。写入者在同一事务中持有该身份的共享维护锁，使退役不能插入检查与写入之间；维护者使用非阻塞独占锁，并再次核对前像。

无法还原原始输入、因而不能验证的旧删除重放记录，由资源领域的内部维护入口处理，不能伪造历史证明，也不能降低正常读取校验。处理须限定一个明确的 Resident ID，核对同库权威删除事实和公共删除描述一致、没有已验证证据，固定原始字节、revision 与 xmin。先把原记录写入独占私有归档并持久化，再在同一事务建立退役身份约束、写入明确标记未恢复证明的维护回执、删除该无效重放记录。公共删除描述、权威删除记录和原业务历史保持；任一前像变化或并发写入均拒绝。提交后沿原资源集合推进 revision 和通知；重试只认相同前像摘要对应的维护回执，不把维护回执当作原始删除授权。

一次性的 owner 迁移观测只保留在离线备份中；领域运行记录保存真实运行结果。

新增一种资源类型时，只需增加对应的领域插件，必要时增加一个索引适配器；页面、Resident 和其他服务继续使用同一个 ResourceService。
