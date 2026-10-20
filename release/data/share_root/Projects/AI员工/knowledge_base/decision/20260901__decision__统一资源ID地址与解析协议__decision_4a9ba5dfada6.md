---
kb_id: decision_4a9ba5dfada6
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 18
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T18:47:37Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/http_resources.py
  - ${code_root}/management_portal/resources/isa.py
  - ${share_root}/Skills/tfi-unified-id/SKILL.md
  - ${code_root}/config/resource-types/_registry.json
  - ${code_root}/management_common/resource_identity.py
  - ${code_root}/management_portal/resources/address.py
  - ${code_root}/management_portal/resources/contracts.py
  - ${code_root}/management_portal/resources/service.py
  - ${code_root}/management_portal/resources/portal.py
  - ${code_root}/tests/cases/test_resource_portal_contract.py
  - ${code_root}/tests/cases/test_resource_identity_backend.py
  - ${code_root}/tests/cases/test_resource_resolver_portal.py
  - ${code_root}/tests/cases/test_resource_service.py
  related:
  - decision_ad3e21094f14
  - decision_a4e5f03c8b9b
  - decision_b820faf42218
  - decision_cf27d39e39cd
  - decision_b6e74d78f7b6
  - decision_8dc6b3d03ca7
  - decision_abaedeefca6f
  - decision_34158fdd4078
  - decision_8273271b67b1
  - decision_355ed742bca5
  supersedes: []
  derived_from: []
---
# 统一资源 ID、地址与解析协议

Portal 管理很多种业务对象：知识条目、论文、代码仓库、文章、设备、保存的搜索、测试用例、Resident 和 Lineage 等。它们由不同的业务模块保存，却需要在页面、Agent 消息和文件中被稳定地提及。本 Design 说明这些对象怎样获得统一身份（统一资源 ID），怎样用地址指向对象内部的文件或章节，以及一个地址怎样被转换成当前访问者有权看到的标准描述。

本篇负责 ID 语法、地址语法和解析结果的格式。资源怎样被搜索见 `decision_8dc6b3d03ca7`；谁有权读取见 decision_b820faf42218；页面怎样从正文中识别地址见 `decision_a4e5f03c8b9b`；执行解析的 ResourceService 的整体结构见 `decision_cf27d39e39cd`。

## 基本概念

- **资源**：Portal 可以统一发现、引用或操作的业务对象。资源的正文和业务状态仍由创建它的领域保存；统一资源体系只提供身份、地址、查找和权限检查。
- **根资源**：拥有独立生命周期、独立权限和统一 ID 的资源，例如一篇 Design 或一个 Repo。
- **统一资源 ID**：根资源在整个生命周期中不变的名字，例如 `article_63b2f19ad704`。标题、目录和页面地址改变时，ID 保持不变。
- **资源地址**：统一 ID，或统一 ID 后面接上内部路径，例如 `repo_a38c7d91e204/file/src/main.py`。
- **嵌套目标**：地址中内部路径指向的对象，例如 Repo 中的一个文件、Article 中的一个章节或一次提交。它共享根资源的身份和权限，只在需要独立移动、授权或版本管理时才拥有自己的统一 ID。
- **资源类型注册表**：从仓库 `config/resource-types/` 读取全部合法资源类型和 ID 语法；每个类型一个声明文件，`_registry.json` 保存语法与历史例外。代码中由 `ResourceTypeRegistry` 读取并校验。声明、提供者绑定、启用与停用及管理页见 decision_355ed742bca5。
- **地址解析器**：只检查地址字符串的形状，拆出根 ID、资源类型和内部路径，不查询对象是否存在。代码中为 `AddressParser`。
- **领域解析器**：理解某一类资源的地址含义的组件，代码中称为 Resolver。例如只有 Repo 的领域解析器知道 `file/src/main.py` 指仓库中的哪个文件。领域解析器还可以附带正文读取函数。
- **解析结果**：一个地址所指目标的标准描述，格式标识为 `tfi.resource_resolution.v2`。
- **访问者（actor）**：本次请求代表的身份，由认证入口提供，使用已认证的 Portal 用户或 Resident；匿名访问不能作为资源访问身份。

## 设计点

### 一个对象只有一个根身份

- **普通统一 ID 的格式是 `<类型>_<12 位小写十六进制码>`。** 类型以小写字母开头，可以包含小写字母、数字和用下划线分隔的分组；随机码只接受 12 位小写十六进制字符。
- **新类型必须先写入资源类型注册表。** 类型声明目录或其 JSON 文件集合、修改时间、大小变化后，`ResourceTypeRegistry` 自动重新读取。它至多每秒检查一次目录签名：服务在一次请求中会多次解析 ID，重复检查文件会在繁忙进程中造成明显等待；新登记的类型因此最多一秒后生效。
- **校验只去除输入首尾的空白。** 内部字符、类型和随机码的大小写都不会被改写。
- **Project 和用户使用各自领域已有的外部 ID。** Project ID 不能为空，不能包含 `/` 或空字符；用户 ID 还不能包含任何空白字符。校验这两种 ID 时必须显式指明类型，它们不能冒充普通的 `<类型>_<码>` 身份。
- **标题、目录、远程地址、页面路由和显示名称都只是属性。** 它们改变时，资源继续使用原来的统一 ID。调用方不能从标题或路径推导 ID，也不能另设 `id`、`repo_id` 等字段重复表示同一个根身份。
- **`new_id` 只生成尚未占用的候选 ID。** 它避开调用方给出的已占用集合；Project、用户和未登记的类型不能用它生成 ID。

### 地址可以指向根资源或内部目标

- **根地址就是统一 ID**，例如 `test_0372b018fa50`。
- **嵌套地址在根 ID 后用 `/` 连接非空路径段**，路径段的顺序保持不变，标题不能代替路径。
- **地址规范化的步骤：** 先做一次百分号解码，再去除首尾空白和首尾的 `/`。
- **以下地址被判为语法错误：** 空地址；包含空字符、换行或回车的地址；包含空路径段的地址；根部不是合法统一 ID 的地址。
- **语法正确只说明地址形状成立。** 对象是否存在、内部路径是否有意义、访问者能否读取，都在领域解析阶段判断。

### 解析始终使用当前访问者

- **`resolve(address, actor)` 使用认证入口已经确认的访问者。** 地址、当前工作目录、Workspace 或 Lineage 参数都不能产生或替换访问者。
- **领域解析器校验身份、对象生命周期和地址约束。** 是否允许调用资源 ISA、要求哪个等级，由权限系统按当前模板判定；解析器不能替换访问者，也不能用 owner 或可见性字段追加隐式授权条件。
- **权限按实际目标检查。** 地址解析出的根资源就是权限请求中的资源；Project 归属决定资源在 R 树上的位置，owner 是业务事实，不自行产生额外的读写权限门槛。User 与 Resident 均使用认证入口确认的身份。
- **结果随当前状态变化。** 权限改变、对象删除或领域服务故障时，同一地址会得到相应的错误；调用方不能改用标题、路径或 ID 相近的其他资源代替。
- **精确地址以领域当前存在性为准。** 协作资源直接读取、旧索引兼容与已删除摘要的区别以 decision_b6e74d78f7b6 的 `get` 契约为准。Workspace 必须有当前协作描述记录才能取得目录路径；缺失时根资源与文件地址均不可从旧索引恢复路径。解析不触发全量索引刷新，也不以 SocietyService 正文代替资源领域解析。

### 引用和正文读取各有分工

- **`reference(address, actor)` 生成适合保存和展示的简短引用。** 它先完成解析和读取授权，再返回规范地址、根 ID、类型、标题、摘要、状态、版本、分类、Portal 打开地址和可用操作提示。地址指向嵌套目标时，优先显示子目标的标题、摘要或文件名。
- **引用中的 `edit`、`save` 和 `download` 只提示入口是否存在。** 之后真正打开、读取或写入时，仍重新检查权限和对象状态。
- **`read_content(address, actor, max_bytes)` 读取正文。** 它复用同一次解析与授权，再由领域解析器附带的正文读取函数返回字节、媒体类型和解析结果；默认最多 4 MiB。正文从不来自资源索引的摘要。
- **`read_content_range(address, start, end, actor)` 按字节区间分段读取。** 每段读取都重新解析和授权。领域解析器提供区间读取函数时才可用；它支撑大文件的分段下载。

### 资源在浏览器中的打开地址由一份规则决定

- **统一 ID 和嵌套地址是身份，浏览器打开地址指向实际的领域页面。** 通用入口 `/r/<百分号编码后的完整地址>` 用当前访问者解析地址，再跳转到对应的领域页面。
- **打开地址的规则集中在 `management_portal/resources/portal.py`。** 它列出每种资源类型的页面路径、身份参数和允许附带的导航参数。ResourceService、领域解析器和索引都使用这份规则，不各自拼接地址；规则只决定地址格式，读取授权和存在性仍由 ResourceService 判断。
- **各类型打开的页面：**

  | 资源类型 | 打开地址 |
  | --- | --- |
  | 知识条目（`decision`、`thinking`、`idea` 等 12 种） | `/resource/knowledge?item=<ID>` |
  | Skill、Repo、Article、Experiment、Device | `/resource/<类型>?item=<ID>` |
  | Review | `/resource/knowledge?item=<ID>`；属于某篇论文的评阅改为打开该论文 |
  | Paper | `/paper?paper=<ID>` |
  | Test | `/wall?case=<ID>` |
  | Resident | `/lineage?resident=<ID>`，可附带所在 Lineage |
  | Lineage | `/lineage?lineage=<ID>` |
  | Chatroom | `/lineage?chatroom=<ID>` |
  | TODO | `/resource/knowledge?item=<ID>` |
  | Workbench | `/workbenches/view?id=<ID>` |
  | Project | `/projects/?project=<Project ID>` |
  | 保存的语义搜索 | 发起搜索的页面，附带 `semantic=<ID>`；WALL 的搜索还附带工程 |

  嵌套目标优先使用领域解析器给出的精确子目标地址。规则中没有的类型不生成打开地址，引用中保留通用的 `/r/` 地址；显式给出的类型与 ID 前缀不一致时也不生成。
- **索引返回结果时按当前规则计算打开地址。** 旧缓存中的 URL 不作为规则依据，调整页面地址不需要迁移索引数据。
- **浏览器使用同一份规则。** Portal Web 由这份规则生成脚本 `/static/portal_resource_routes.js`，页面在资源标签组件 `portal_resource_chips.js` 之前加载它，用来识别资源链接、资源类型和可以文本预览的类型。这份规则还包含测试运行记录 `testrun_<16 位十六进制码>` 的打开方式（`/wall?run=…`），它不是资源，只用于导航。
- **浏览器和服务端都拒绝可疑的跳转。** 浏览器只识别同源链接，拒绝身份参数重复、类型不符或存在冲突身份参数的链接；服务端校验跳转目标必须是本站路径，拒绝站外地址、再次指向 `/r/` 的地址和包含控制字符的地址。

### 精确地址优先于模糊搜索

- **资源搜索先把完整的查询文字当作资源地址，用当前访问者解析。**
- **解析成功时，这个资源以最高分进入后续的类型、分类、Project、状态和属性筛选。** 筛选仍然可以排除它。
- **索引暂时落后时，领域解析器仍能返回这个精确地址。** 对象不存在时精确结果为空，不会退回到相似文字的结果。

## 一次解析怎样完成

| 组件 | 职责 |
| --- | --- |
| 资源类型注册表（`ResourceTypeRegistry`） | 读取合法类型，生成和校验根 ID |
| 地址解析器（`AddressParser`） | 把输入规范化为根 ID、资源类型和内部路径 |
| 解析器目录（`ResolverRegistry`） | 按资源类型找到领域解析器，管理并发注册 |
| 领域实现 | 连接资源自己的登记表、清单文件、文件树或业务服务，判断对象和内部目标是否存在 |
| ResourceService | 规范化解析结果，执行统一读取授权，生成引用或读取正文 |
| 认证、权限与领域边界 | 入口按权限系统检查资源操作；门面校验 User/Resident 身份，领域检查存在性、私有性与地址约束（decision_b820faf42218） |

```text
调用方提交资源地址和已认证的访问者
                    ↓
地址解析器拆出根 ID、类型和内部路径
                    ↓
解析器目录找到这一类型的领域解析器
                    ↓
领域实现检查对象和内部目标
                    ↓
ResourceService 规范化解析结果
                    ↓
已授权入口的身份与领域读取约束检查
                    ↓
返回解析结果、引用或正文
```

地址语法对所有类型统一，Repo 文件、Article 章节和 Knowledge 正文的差异留在各自的领域实现中。增加一种嵌套地址时，只需要扩展根类型的领域解析器，调用方继续提交同一种地址。

## 领域解析器的装配与恢复

领域解析器及可选正文、区间读取函数的绑定、重复绑定、按需加载、并发和失败回滚，统一见 decision_cf27d39e39cd「按类型加载领域提供器」。本篇仅定义解析器接收的地址与返回结果；类型已登记但缺少解析器或正文能力时明确报告不支持，不猜测别的资源。

## 解析结果的格式

- 领域解析器必须返回一个对象，其中包含 `record` 对象。
- `record.resource_id` 必须等于请求地址的根 ID。`record` 中用 `id` 或其他 `*_id` 字段重复同一身份会被拒绝。
- 领域解析器可以返回 `root_id`、`resource_type` 和 `address`；返回时必须分别与解析出的根 ID、类型和规范地址一致。
- ResourceService 统一写入格式标识 `tfi.resource_resolution.v2`、规范地址、根 ID、资源类型、`target_kind`（目标种类）、`exists`（是否存在）和 `metadata`（附加信息）。领域解析器没有给出后三项时，默认分别为根资源类型、`true` 和空对象。
- 嵌套目标的解析结果可以增加子目标标题、文件名、内容大小等信息，但结果中唯一的 `resource_id` 仍是根 ID。

## 调用入口

| 入口 | 谁使用 | 说明 |
| --- | --- | --- |
| `parse_address(address)` | Portal 进程内代码 | 只校验并拆分地址语法 |
| `resolve(address, actor)` | 同上 | 解析对象并执行读取授权，返回解析结果 |
| `reference(address, actor)` | 同上 | 生成简短引用 |
| `read_content(...)`、`read_content_range(...)` | 同上 | 读取正文或正文的字节区间 |
| `new_id(resource_type)`、`validate_id(...)` | 同上 | 生成候选 ID；校验 ID 及其类型 |
| `GET /api/resources/resolve`、`/api/resources/reference`、`/api/resources/content` | 浏览器页面 | 以登录用户身份解析、生成引用和读取正文；地址指向有大小与修改时间信息的文件时，正文按区间读取 |
| `ResolveResource`、`ReadResource` 等资源类指令 | Resident 与页面 | HATG ISA 的资源指令（`decision_ad3e21094f14`），在 ResourceService 中执行并调用上面的方法；`ReadResource` 遇到超过 `max_bytes` 的正文时返回只限本人、有时效的下载地址 |

Portal 的其他进程通过 ResourceService 的 Unix socket 调用同名方法（`decision_b6e74d78f7b6`）。

## 创建与回收的生命周期

有明确结束边界的操作可以关联一个持久资源生命周期，记录本次新分配的根资源 ID。普通读取、更新、刷新及重试既有资源不改变资源归属。关联随当前请求、ISA 上下文及已认证的 ResourceService RPC 传递，不参与授权判断；每次请求仍以真实调用者校验权限。

结束时等待已进入的创建请求及嵌套调用结束，取得独占锁后封闭生命周期，再逐项通过 `UnregisterResource` 回收。请求持有的共享锁不占用资源记录的连接池；清理采用非阻塞独占锁尝试，等待期间允许原请求所需的嵌套调用完成，超时则保留待回收记录供重试。该统一入口调用资源类型登记的领域删除适配器，复用页面的产品删除流程；删除搜索、论文、Resident、Lineage 等资源时，停止工作、回收专属投影与保存审计历史仍由对应领域负责，不能只从索引移除资源。内部组件随所属图或业务投影清理，不冒充独立根资源注销。

创建登记在请求失败时仍保留；删除成功逐项记录，删除失败保留待回收项以便重试。仅全部清理完成后关闭生命周期并解除执行 Resident 的关联；其后普通调用不再属于已结束操作，显式携带旧生命周期标识的请求仍拒绝执行。已删除或未实际创建成功的 ID 允许幂等清理；权限拒绝和删除未完成不得解释为成功。没有关联的正式资源不纳入该次清理。

## 状态归属

统一资源查询和按 ID 读取都按当前认证主体计算 `discover/read/manage` 能力，包括 Resident、Lineage、Workspace 和 Workbench。能力是每次请求生成的返回值，不写入资源描述符或业务事实；业务归属直接读取所属领域的 canonical 记录。Usage Attribution 使用这份能力投影确定可显示名称的 Resident 和 Lineage；身份查询失败时返回错误，不能把所有已归属用量伪装成 Other Resident。

| 状态 | 归属 |
| --- | --- |
| 类型和 ID 语法 | 共享资源类型注册表 |
| 对象是否存在、正文 | 对应领域的登记表、清单文件或业务服务 |
| 内部地址的含义 | 根类型的领域解析器 |
| 跨领域的操作授权 | 权限系统当前方案与实例；领域保留存在性及业务约束 |
| 可搜索的摘要 | 可重建的资源索引 |

增加一种根资源类型的顺序是：先在资源类型注册表中登记类型，再安装领域解析器；需要读取正文时再提供正文读取函数。
