---
kb_id: decision_bd179998ba08
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 13
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:32:21Z'
  updated_by:
    user: ''
    agent: resident_139519ddd6fa
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/presentation_store.py
  - ${code_root}/management_portal/http_ui_state.py
  - ${code_root}/management_portal/portal_ui_state.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/static/portal_identity.js
  - ${code_root}/tests/cases/test_portal_ui_state.py
  related:
  - decision_a6bebf4ac283
  - decision_6eac174d85bf
  - decision_725dedbe89b2
  - decision_152d5c046a23
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Portal UI Association 的服务端存储与并发

UI Association（界面关联）是 Portal 为每位登录用户在服务端保存的一小段界面记录：它记住“这个用户在某个界面位置上关联了什么对象”，例如个人问答使用哪个 Resident、某个 Lineage 画布上次采用什么视图、从某篇论文发起的讨论由哪个 Resident 承接。用户换一台电脑或换一个浏览器打开 Portal 时，这些关联仍然存在。

同一浏览器里的界面偏好（搜索词、折叠状态、布局比例、滚动位置和未提交的输入）由浏览器中的页面偏好层保存，见 decision_9f6135cbbb22。UI Association 只保存需要跨设备恢复、或者后端业务流程需要读取的关联。

## 基本概念

| 概念 | 含义 |
| --- | --- |
| 用户（actor） | 关联的所有者，即当前登录的 Portal 用户名。每位用户只能读写自己的关联。 |
| 命名空间（namespace） | 关联所属的界面或功能，例如 `lineage.hatg-view`。同一用户在不同命名空间中可以使用相同的键。 |
| 键（key） | 命名空间中一条关联的名称，例如某个 Lineage ID，或固定名称 `primary`。 |
| 值（value） | 关联的内容，是一个 JSON 对象，例如 `{"resident_id": "resident_…"}`。 |
| 更新时间（updated_at） | 服务端最近一次写入这条关联的 UTC 时间。 |

“用户 + 命名空间 + 键”唯一确定一条关联。

当前 Portal 使用的命名空间：

| 命名空间 | 键 | 值记录什么 | 使用者 |
| --- | --- | --- | --- |
| `lineage-workbench.personal-ama` | `primary` | 用户的个人问答 Resident | Society Workbench 侧栏、Home 的 Agent Access（decision_152d5c046a23） |
| `lineage-workbench.chatrooms` | Chatroom ID | 用户置顶的聊天室与置顶时间（decision_a6bebf4ac283） | Society Workbench 的 Chatroom 面板 |
| `lineage-workbench.workspaces` | 个人工作区分组键 | 分组名称 `name` 和文件夹绝对路径列表 `folders`；删除关联不删除磁盘文件 | Society Workbench 的 Workspace 导航（decision_769a35d6b5c7） |
| `lineage.hatg-view` | Lineage ID | 用户在该 Lineage 画布上的个人视图设置 | HATG 画布 |
| `resident-invocations`、`resident-invocation-lineages`、`resident-invocation-creations` | 业务范围 | 业务页面发起讨论时绑定的 Resident、Lineage 和正在进行的创建 | 业务调用流程（decision_725dedbe89b2） |
| `resident-recommendation` | 固定键 | 用户最近一次推荐 Resident 的请求与结果 | 推荐 Resident 功能（decision_8fe87d812a4c） |

## 设计点

- **每位用户只能读写自己的关联。** 服务端从当前登录 Session 取得用户名作为 actor，请求中不能指定其他用户。
- **关联中的对象 ID 只是引用。** 值里记录的 Resident、Lineage 或资源 ID 不授予任何权限；页面或业务流程使用这些对象时，仍按对象当前的权限检查。
- **写入一条关联时整体替换它的值。** 两次写入同一条关联时，后提交的一次生效；写入只影响这一条关联，同一用户的其他关联和其他用户的关联保持不变。
- **删除一条不存在的关联也算成功。** 删除接口用返回值 `removed` 说明这次是否真的删掉了记录，重复删除不会报错。
- **输入超出限制时拒绝写入。** 服务端按下面的规则校验，任何一项不满足都返回 400，已有关联保持不变：
  - 用户名去掉首尾空白后不能为空，最多 160 个字符，不能包含控制字符；
  - 命名空间以字母或数字开头，其余字符只能是字母、数字、`.`、`_`、`:` 和 `-`，最多 160 个字符；
  - 键去掉首尾空白后不能为空，最多 600 个字符；
  - 值必须是 JSON 对象，按紧凑格式编码成 UTF-8 后最多 1 MiB；
  - 每位用户在一个命名空间中最多 2,000 条关联；达到上限后仍可以修改已有的键，新增键被拒绝。
- **存储不可用时请求明确失败。** 数据库暂时不可用或存储内容格式不符时，接口返回错误，已有关联保持原样。

## 存储与并发

关联保存在 PostgreSQL 的 `portal_ui.associations` 表中，主键为 `(actor, namespace, association_key)`。每条关联单独一行，值保存在有大小限制的 `value_json`，更新时间保存在 `updated_at`。同一 schema 的 `user_preferences` 按用户保存偏好，`favorites` 按用户、界面和资源保存收藏：Project 和 Lineage 收藏使用 `surface='lineage'`，Resident 收藏使用 `surface='resident'`，论文收藏使用 `surface='paper'`。导航列出用户能访问的全部对象，Portal 不保存“在导航中显示或隐藏”的偏好；收藏只决定“我的收藏”视图和星标（decision_6eac174d85bf）。

写入和删除都在一个数据库事务中完成：

```text
开始事务
  → 对用户与命名空间加事务级咨询锁（PostgreSQL advisory lock）
  → 读取当前数量与目标关联是否存在
  → 检查数量上限，修改目标关联
  → 插入、更新或删除目标关联一行
提交事务，释放锁
```

同一用户、同一命名空间的写入按锁顺序进行，避免并发突破数量上限；不同用户互不阻塞。删除不存在的关联不产生写入。读取不加锁，按主键或索引查询已提交内容；接口返回数据副本，调用方修改返回值不会影响存储。关联不维护整组数据的修订号。

有些业务步骤需要先检查关联、再调用外部服务创建对象、最后写回关联，例如业务页面第一次发起讨论时创建 Resident。这类步骤使用关联锁 `association_lock(用户, 命名空间, 键)`：它是按“用户 + 命名空间 + 键”加的跨进程文件锁，保存在 `portal_path("staging_root") / "shared" / "portal_data" / "ui-association-locks"`，由站点配置解析 staging 根，同一条关联同时只有一个进程在执行这组步骤。锁文件只用于进程间排队，不保存内容；外部调用期间不占用数据库事务，最终结果仍通过普通写入保存。

## HTTP 接口

浏览器通过 `PortalIdentity`（`static/portal_identity.js`）中的三个方法调用以下接口。三个接口都要求有效的登录 Session；写入和删除还要求请求来自 Portal 自己的页面（同源检查），否则返回 403。

| 方法 | 地址 | 请求 | 返回 |
| --- | --- | --- | --- |
| `listUIAssociations(namespace)` | `GET /api/ui-state/associations?namespace=…` | 命名空间 | `namespace` 与 `items`：当前用户在该命名空间中的全部关联，按键排序，每项包含 `key`、`value`、`updated_at` |
| `putUIAssociation(namespace, key, value)` | `POST /api/ui-state/associations/put` | `namespace`、`key`、完整的 `value` | `association`：写入后的关联 |
| `removeUIAssociation(namespace, key)` | `POST /api/ui-state/associations/remove` | `namespace`、`key` | `removed`：是否删除了记录 |

读取请求使用 `cache: "no-store"`，浏览器每次都向服务端读取最新内容。输入不合法或超过限制时返回 400；存储不可用时返回 500。

后端代码通过 `PortalUIStateStore`（`portal_ui_state.py`）直接读写，提供 `list`、`get`、`put`、`remove`、`association_lock`，以及跨用户读取整个命名空间的 `list_namespace`。`list_namespace` 只供内部诊断使用，例如 Lineage 管理者查看哪些业务绑定指向本 Lineage 的 Resident；调用方先完成管理权限检查。
