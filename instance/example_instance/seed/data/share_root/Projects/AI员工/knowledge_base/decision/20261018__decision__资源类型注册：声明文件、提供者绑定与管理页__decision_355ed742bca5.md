---
kb_id: decision_355ed742bca5
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 16
  created_at: '2026-10-18T14:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_667111522609
  updated_at: '2026-10-20T00:52:21Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 7
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_common/resource_identity.py
  - ${code_root}/config/resource-types/device.json
  - ${code_root}/config/resource-types/_registry.json
  - ${code_root}/management_portal/resources/resource_types.py
  - ${code_root}/management_portal/resources/service.py
  - ${code_root}/management_portal/resources/providers/__init__.py
  - ${code_root}/management_portal/static/portal_management_resource_types.js
  - ${code_root}/management_portal/static/portal_management.js
  - ${code_root}/management_portal/static/portal_nav.js
  - ${code_root}/management_portal/static/resource_tabs.js
  related:
  - decision_4a9ba5dfada6
  - decision_15aab18c0ab3
  - decision_2f67ed28db67
  - decision_8dc6b3d03ca7
  - decision_ad3e21094f14
  - decision_b820faf42218
  supersedes: []
  derived_from: []
---

# 资源类型注册：声明文件、提供者绑定与管理页

Portal 的每个资源都有 `<类型>_<码>` 形式的统一 ID（decision_4a9ba5dfada6）。类型由仓库中的声明文件定义，声明把类型元数据与 `management_portal/resources/providers/` 下的提供者绑定。管理员可以在 Portal 里查看类型、可用状态和实例数量，并决定启用哪些；公开版本未包含提供者功能时，相应类型显示为“未安装”。本 Design 说明类型声明、提供者绑定、启停状态与 Portal 管理的“资源类型”分栏。

ID 语法、地址解析和打开地址仍由 decision_4a9ba5dfada6 规定；资源目录页面见 decision_2f67ed28db67；资源索引见 decision_8dc6b3d03ca7；发布时选择初始功能和数据的规则见 decision_15aab18c0ab3。

## 基本概念

- **资源类型（type）**：统一 ID 中下划线之前的部分，例如 `repo`、`decision`。分类（category）为 `resource`（独立资源）或 `knowledge_entry`（知识库条目）。
- **类型声明（declaration）**：描述一个类型的 JSON 文件，位于仓库 `config/resource-types/<类型>.json`，一个类型一个文件。
- **提供者（provider）**：实现一个类型行为的模块，位于 `management_portal/resources/providers/`，负责登记机制、修改、撤销登记、地址解析、正文读取与专用动作。统一登记政策来自类型声明，`GetResourceType.registerable` 合成声明政策与提供者机制；`updatable`、`unregisterable` 与 `actions` 来自提供者。
- **类型注册表（`ResourceTypeRegistry`）**：进程内读取全部声明的组件，负责校验 ID、给出类型元数据。
- **类型状态**：`enabled`（启用）、`disabled`（停用）、`not_installed`（未安装：声明存在，提供者所属功能未包含在本次部署中）。
- **实例**：某个类型下已登记的一个资源；数量来自资源索引。

## 设计点

### 声明

- **每个资源类型由仓库中的一个声明文件定义。** 文件名就是类型键。字段：`type`、`category`、`title`（中英文标题）、`description`、`provider`（`模块:属性`，纯身份类型留空）、`catalog_section`（在 Resource 页面哪个类别下展示，不展示留空）、`catalog_order`、`registerable`（User 能否用 `RegisterResource` 登记实例）、`scope`（`project`、`shared` 或 `lineage`，决定它在权限方案资源树上的位置）、`unit`（现有实现中的功能归属标记，用于判断提供者是否已安装）、`enabled_by_default`。
- **类型注册表只从声明目录读取。** 声明目录取代统一 ID Skill 中原先的单文件类型声明；ID 语法、随机码规则和历史例外放在同目录的 `_registry.json`。调用方通过 `ListResourceTypes` 读取类型。
- **新增类型只需要一个声明文件和一个提供者模块。** 资源索引、Resource 页面的类别导航、权限方案的资源树和 `RegisterResource` 都从声明读取类型，代码中没有第二份类型列表。
- **声明与提供者不一致时按原因区分。** 声明的 `unit` 所标记功能已包含而提供者模块缺失或无法导入时，ResourceService 拒绝启动；对应功能未包含时，类型记为未安装，服务正常启动。
- **统一登记的政策与实现机制分别检查。** 声明的 `registerable` 决定 User 是否可通过 `RegisterResource` 登记；mutation 提供者的 `register` 是实现机制。List/Get 顶层有效 `registerable` 等于声明为 true 且按需装载后的提供者具有 `register`，未安装类型为 false，不能因提供者尚未装载而误报 false。声明 true 却没有机制属于声明完整性错误，由覆盖全部声明的定向测试拦截。类型通过已安装、已启用检查后，声明 false 即返回 `resource_type.not_registerable`，即使提供者有登记机制也不放行；chatroom、paper、search 等仍由各自专用指令创建。
- **声明原文与有效能力分别返回。** `ListResourceTypes.types[].declaration` 与 `GetResourceType.declaration` 是原始声明的独立副本，供完整只读 JSON 展示，不受顶层能力合成覆盖。`updatable`、`unregisterable` 与 `actions` 继续由提供者给出；页面能力展示使用顶层有效值，原文展示使用 `declaration`。

### 状态

- **启用的类型参与登记、目录、搜索和索引。**
- **停用的类型拒绝新登记，并退出目录和搜索；已有实例仍可按 ID 解析、读取和删除。** 所有已声明类型的 ID 永远能通过语法校验，链接不会因为停用变成错误地址。
- **未安装的类型不能启用。** 它的声明仍被读取，所以 ID 可以解析；目录隐藏它；保留的 Lineage 或其他数据引用它的实例时，操作以“类型未安装”失败。
- **状态保存在数据库，按部署生效。** 表 `resource_types.state` 记录每个类型的 `enabled`、版本和修改者；没有记录的类型按 `enabled_by_default` 处理。Dev 与 Release 读同一份。
- **功能安装状态变化时同步类型状态。** 包含提供者功能后，它的类型按 `enabled_by_default` 生效；去掉该功能后，这些类型变为未安装，已保存的启用记录保留，再次安装时恢复。

### 管理页

- **Portal 管理新增“资源类型”分栏。** 表格列出类型、分类、标题、状态、实例数、提供者与所属功能、目录位置、是否可登记；可按分类和状态筛选。点开一行显示声明全文和提供者的动作目录。
- **管理员在这里启用或停用类型。** 停用前显示该类型的实例数和它出现的页面，确认后生效；未安装的类型只能查看。
- **启用与停用需要“Portal 运维”节点的 admin，查看需要对“共享”的 viewer。** 权限系统关闭时任何已登录 User 可用。
- **只有查看权限的用户也能打开资源类型分栏。** 页面隐藏用户管理和类型启停入口，读取类型无需先请求用户管理数据；服务端拒绝一次启停请求后，当前分栏也隐藏写操作。
- **状态版本冲突时刷新当前类型并提示重试。** 用户再次操作时使用刷新后的版本；页面不自动覆盖其他人的修改。
- **类型状态改变后，当前页面的资源导航同步更新。** 已停用和未安装的类型不参与类别生成；页面附加在类别栏中的操作控件保持原有节点与交互状态。
- **实例的登记入口不变。** User 在 Resource 页面的表单或 `RegisterResource` 登记实例；这个分栏管理类型，不编辑实例。

## 实现说明

### 声明文件

```json
{
  "type": "device",
  "category": "resource",
  "title": {"zh": "设备", "en": "Device"},
  "description": "A managed device. Servers and independently schedulable GPUs...",
  "provider": "management_portal.resources.providers.device:DeviceProvider",
  "catalog_section": "device",
  "catalog_order": 60,
  "registerable": true,
  "scope": "shared",
  "unit": "core",
  "enabled_by_default": true
}
```

`_registry.json` 保存 `schema_version`、`code_pattern`、`type_pattern` 和 `id_exceptions`。

### 读取与绑定

`unit` 表示功能归属。发布时选定功能，资源类型随对应提供者的安装状态工作。

类型注册表读取 `config/resource-types/`，可通过 `PORTAL_RESOURCE_TYPES_DIR` 指定目录。提供者按声明中的 `provider` 装载，类型状态决定搜索和目录是否纳入该类型；实例数量从资源索引查询。

### 页面读取与错误处理

列表和详情使用 `ListResourceTypes`、`GetResourceType`，原始声明使用返回的 `declaration` 展示。启停操作携带当前版本；版本冲突时刷新并提示重试，权限不足时隐藏写入口，读取失败时提供重试。

资源导航按已启用、已安装类型的目录配置生成；独立的 Backup 入口保留。刷新导航时保持页面附加控件的交互状态。

### 指令权威与职责边界

本页通过 `ListResourceTypes`、`GetResourceType` 读取声明与状态，通过 `UpdateResourceType` 更新启停状态。三条指令由 ResourceService 执行；正式参数、返回结构、`expected_version` 信封与冲突回执统一由资源指令规范 decision_ad3e21094f14 定义，并由 `GetInstructionSchema` 提供当前机器契约。本文保留声明、安装/启停政策和管理页行为，不再维护第二份 ISA 参数表。

`UpdateResourceType` 的授权规则由权限方案规范 decision_600ae27b0538 与资源指令规范 decision_ad3e21094f14 统一说明。当前正式 ISA 声明与内建方案实际要求 `@shared` 的 admin，与上文管理页要求“Portal 运维”admin 的规则存在偏差，需另行核对并修正；本次正文整理不改变该管理规则或授权行为。类型的不可登记、停用或未安装是资源域拒绝原因；它们如何映射为 ISA 错误及调用方怎样识别，见 decision_ad3e21094f14。
