---
kb_id: decision_f48c2514bf33
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 11
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/skill_board_lan/skill_backend.py
  - ${code_root}/management_portal/resource_index_backend.py
  - ${code_root}/management_portal/resources/schema.py
  - ${code_root}/management_portal/portal_access_control.py
  - ${code_root}/knowledge_board_lan/knowledge_backend.py
  - ${code_root}/knowledge_board_lan/knowledge_board.html
  - ${code_root}/management_portal/resource_catalog.html
  - ${code_root}/management_portal/static/resource_catalog.js
  - ${code_root}/management_portal/page_renderers.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_skill_files.py
  - ${code_root}/tests/cases/test_skill_deletion.py
  - ${code_root}/tests/cases/test_resource_portal_routes.py
  - ${code_root}/tests/cases/test_resource_index_backend.py
  - ${code_root}/tests/cases/test_portal_access_control.py
  related:
  - decision_eb066fbea4ed
  - decision_58db6282c244
  - decision_8dc6b3d03ca7
  - decision_4a9ba5dfada6
  - decision_61e50306cc79
  supersedes: []
  derived_from: []
---
# Skill 资源与 Skill 工作台

Skill 是供 Agent 或开发者反复使用的一组操作说明及配套文件，例如“怎样安排定时工作”“怎样登记资源”。Portal 把每个 Skill 登记为一个 `skill_<12 位十六进制>` 资源，用户在 `/resource/skill` 页面查找不同来源的 Skill、阅读主说明和其中的文件，有权限时修改说明或删除整个 Skill。

本 Design 说明 Skill 的组成和来源、页面结构，以及读取、保存和删除怎样落到文件。统一资源 ID 与地址规则见 decision_4a9ba5dfada6，资源索引见 decision_8dc6b3d03ca7。

## Skill 由什么组成

**Skill 包**是一个可以独立复用的说明单元：一个 Markdown 文件，或者一个目录。目录中的**主说明**是打开 Skill 时默认显示的文件，其余的脚本、图片、模板和参考文档称为**内部文件**。

Portal 从三个位置发现 Skill：

| 来源 | 位置 | 用途 |
| --- | --- | --- |
| 共享 | `${share_root}/Skills` | TFI Portal 共同维护的 Skill |
| Project | `${share_root}/Projects/<Project>/knowledge_base/skill` | 只属于某个 Project 的 Skill |
| 第三方 | `${share_root}/Skills/third-party` | 外部来源的 Skill |

**主说明怎样确定**：目录型 Skill 可以在顶层的 `skill.yaml` 或 `skill.yml`（两者只能有一个）中用 `primary_file` 指定主说明，它必须是包内存在的 Markdown 文件；指定无效时，这个包记为扫描错误，不会自动改选其他文件。没有 manifest 时依次选择：顶层的 `SKILL.md`、与目录同名的顶层 Markdown、按名称排序的其他顶层 Markdown、按路径排序的子目录 Markdown。隐藏文件、自动生成的索引和 `__pycache__` 不计入。

**身份**：每个 Skill 有一个稳定的 `skill_*` ID。目录型 Skill 的 ID 写在主说明的头部；Project 中的单文件 Skill 用文件自己的 ID。内部文件的地址写作 `skill_<ID>/file/<相对路径>`，没有单独的资源身份。标题相同或相似的 Skill 不会合并，身份只看 ID。

## 页面

`/resource/skill` 是统一资源目录（Resource Catalog）中的 Skill 页：

| 区域 | 内容 |
| --- | --- |
| 资源类型标签 | 在 Skill 和其他资源类型之间切换 |
| 搜索与 Project 筛选 | 按标题、摘要、ID 或所属 Project 缩小列表 |
| 语义检索按钮与任务面板 | 用自然语言创建搜索任务，查看进度、分数和匹配理由 |
| 刷新按钮 | 重新读取资源索引 |
| 分组 | 共享、各 Project、第三方分别成组，组标题显示条目数，可以折叠 |
| Skill 卡片 | 标题、更新时间、摘要、类型，以及最多两个短标签（状态、重要性或文件数） |

点击卡片打开详情：顶部是 Skill 身份和编辑、删除、关闭按钮；左侧可以在文件树和关系图之间切换；中间预览或编辑主说明与内部文件；右侧是私有 Chat（见 decision_58db6282c244）。详情复用 Knowledge 条目的详情界面。

## 查找与排列

**列表只显示当前用户能读取的 Skill：共享和第三方 Skill 对所有已登录用户和有效 Resident 可读，Project Skill 需要所属 Project 的 viewer 角色。**

- 页面通过资源索引查询 `resource_type=skill`。索引中共享和第三方 Skill 标为 `read_scope: global`，`project_ids` 为空，页面按 source=shared/third_party 分组；Project Skill 标为 `read_scope: project`，project 只使用真实 Project ID。
- 分组顺序固定为共享、Project、第三方；选择了某个 Project 时只显示这个 Project 的一组。每组内按修改时间从新到旧，时间相同时按标题排列。排列只影响显示，不影响权限和身份。
- 某个 Skill 包扫描失败时，该包记为错误，其他 Skill 照常显示。来源目录不存在时，该来源没有条目。

## 阅读与编辑

**打开 Skill 时显示主说明和包内文件列表；有 contributor 权限的用户可以直接编辑所选文本并保存。**

- 读取内部文件时，路径必须仍在该 Skill 包内：绝对路径、`..` 和包外文件都被拒绝。
- 私有 Chat 的身份、历史隔离与发送规则统一见 decision_58db6282c244；Skill 详情复用该面板。
- 文件树、关系图、Chat 与主说明使用同样的读取权限：共享 Skill 在这些入口也不要求 Project 成员角色。
- Markdown 和代码按文本显示，图片按原始内容预览。不能预览的文件显示文件类型提示，不按文本显示二进制内容。
- Skill 使用“包式编辑器”：直接编辑所选文件的文本，没有普通知识条目的元数据表单。
- 保存由权限系统判定（decision_b820faf42218）：默认模板要求对该 Skill 有 contributor。Project Skill 在 R 树上挂在所属 Project 下，对该 Project 的表项覆盖它；全局 Skill 挂在“共享”下（decision_600ae27b0538）。`read_scope` 是来源与展示字段，不额外推导资源权限。保存成功后，服务清除缓存并通知资源索引，再次读取即得到新文本；保存失败时页面保留正在编辑的内容。
- 保存不比较版本，两人同时编辑时后保存的内容生效。

## 删除

**只能删除 Skill 本身；删除把整个 Skill 移入对应来源的回收目录，可以由维护者恢复。**

- 内部文件地址不能单独删除。目录型 Skill 移动整个目录，Project 中的单文件 Skill 只移动这个文件。页面在删除前说明影响范围。
- 共享和第三方 Skill 移入各自来源目录下的 `.trash`；Project Skill 移入该 Project 的 `knowledge_base/.trash`。目标名称已被占用时，使用带时间的新名称，不覆盖已有内容。
- 删除后服务通知资源索引刷新。路径越界、地址不是 Skill 本身、权限不足或 Skill 不存在时，不移动任何内容。
- Portal 没有“还原”按钮，恢复由维护者在回收目录中操作。

## 新建 Skill

Portal 不提供登记新 Skill 的入口，Skill 资源类型也不接受 `RegisterResource`。维护者按上述来源目录和主说明规则把文件写入文件系统，扫描和资源索引随后发现它。

## 组件与流程

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| Skill 页面 | `management_portal/resource_catalog.html`、`static/resource_catalog.js` | 筛选、分组卡片、语义搜索结果和详情入口 |
| 详情界面 | `knowledge_board_lan/knowledge_board.html`（嵌入模式） | 文件树、预览、编辑、Chat 和删除确认 |
| 资源索引 | `management_portal/resource_index_backend.py` | 汇总可见 Skill 的标题、摘要、Project、修改时间和属性 |
| ResourceService | `management_portal/resources/` | 解析 Skill ID、检查当前用户能否访问，把操作交给 Skill 后端 |
| Skill 后端 | `skill_board_lan/skill_backend.py` | 扫描三个来源、确定主说明、限制包内路径、保存文本、整包移入回收目录 |

```text
Skill 页面查询当前用户可见的 Skill
        ↓
用户点击 skill_* 卡片
        ↓
详情通过 ResourceService 解析 Skill，并按当前用户检查读取权限
        ↓
Skill 后端确定主说明，列出包内文件
        ↓
详情界面显示预览、编辑和协作区域
```

详情界面与 Knowledge 条目共用，读者在不同资源之间有一致的阅读体验；包内路径检查和整包删除由 Skill 后端负责，避免通用的文件操作只删掉包中的一部分。保存时，当前用户身份从页面或 Resident 入口一直传到 Skill 后端的文件写入，权限检查在写入之前完成。

## 接口

| 请求 | 作用 |
| --- | --- |
| `GET /resource/skill` | Skill 页面；带 `item=<skill_id>&embedded=1` 时打开嵌入的详情 |
| `GET /api/resource-index/query?resource_type=skill&include_attributes=1&limit=<n>` | 查询 Skill 卡片，可以加 `q` 和 `project` |
| `GET /api/resource-index/resources/<skill_id>` | 读取一个 Skill 的索引记录 |
| `GET /knowledge/api/detail?kb_id=<Skill 地址>` | 读取主说明或内部文件 |
| `POST /knowledge/api/detail/save` | 保存文本，参数 `kb_id` 和 `text` |
| `POST /knowledge/api/detail/delete` | 删除 Skill，`kb_id` 必须是 Skill 本身的 ID |
| `/api/resource-semantic-searches`、`/api/resource-semantic-search/{start,refresh,delete}` | Skill 页的语义搜索（范围 `skill`），交互见 decision_eb066fbea4ed |

后端在删除时再次检查地址是否等于 Skill 本身的 ID，不依赖页面的检查。
