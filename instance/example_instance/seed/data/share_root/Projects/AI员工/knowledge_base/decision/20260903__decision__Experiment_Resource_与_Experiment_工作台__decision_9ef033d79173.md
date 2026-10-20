---
kb_id: decision_9ef033d79173
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 8
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-18T06:40:21Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_7a4a1f23c358
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/knowledge_board_lan/knowledge_backend.py
  - ${code_root}/knowledge_board_lan/knowledge_board.html
  - ${code_root}/management_portal/resource_catalog.html
  - ${code_root}/management_portal/static/resource_catalog.js
  - ${code_root}/management_portal/resources/providers/experiment.py
  - ${code_root}/management_portal/page_renderers.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_experiment_resource_provider.py
  related:
  - decision_ad3e21094f14
  - decision_8dc6b3d03ca7
  - decision_3ece26686464
  supersedes: []
  derived_from: []
---
# Experiment Resource 与 Experiment 工作台

## 这项设计解决什么问题

Experiment Resource 把一次可持续维护的实验登记为稳定的 `experiment_*` 资源。用户由此可以按 Project 发现实验，阅读实验主文档和目录内产物，并让 Agent 或服务在不改变资源身份的前提下更新状态、重要性和正文。

本篇负责实验目录的规范边界、Resource Catalog 中的 Experiment 工作台、登记和结构化更新、整目录退役及其恢复。实验内部文件使用 Knowledge 的通用详情能力读取，但实验身份和生命周期由 Experiment provider 独自拥有。

## 先认识 Experiment 与工作台

### 实验目录和主文档

每个 Experiment 属于一个已经登记的 Project，并位于该项目的 `knowledge_base/experiment` 目录。规范形态是：

```text
knowledge_base/experiment/
└── <slug>__<experiment_id>/
    ├── experiment.md
    └── 实验产物与伴随文件
```

`slug` 是便于人阅读的短目录名；`experiment_id` 是全局唯一且不可变的资源身份。规范主文档 `experiment.md` 保存 `kb_id`、类型、创建时间、状态、重要性、一级标题和正文。目录内的日志、图像、配置和结果属于同一资源，用 `experiment_ID/file/相对路径` 读取，不产生独立生命周期。

### Experiment 工作台可见区域

访问 `/resource/experiment` 进入 Resource Catalog。页面先展示资源目录，再以嵌入详情打开具体实验。

| 区域 | 用户看到什么 |
| --- | --- |
| 类型标签与搜索栏 | 在 Experiment 等资源类型之间切换，按标题、摘要或 ID 搜索 |
| Project 筛选与刷新 | 限定所属项目，或重新加载索引 |
| 语义搜索面板 | 创建自然语言检索任务，查看状态、分数和匹配原因 |
| Project 分组 | 显示项目名、实验数量和折叠箭头 |
| Experiment 卡片 | 显示标题、摘要、修改时间、类型，并从状态、重要性、节点数中展示最多两个徽章 |
| 嵌入详情 | 顶部身份与编辑控件，左侧文件树/关系图，中部预览或编辑器，右侧私有 Chat |

卡片只承载发现所需的摘要；详情才读取主文档和伴随文件。页面允许在详情中编辑文本，但没有 Experiment 登记表单，也没有删除按钮。登记、结构化更新和退役通过 HATG 资源指令进行（见“接口与字段”），供 Agent 和服务使用。

## 设计点

### 发现与阅读

- Resource Catalog 通过统一资源索引查询 `resource_type=experiment`，只返回当前用户可见 Project 中的实验。
- 登记回执、索引、解析与卡片使用主文档的一级标题，保留空格与大小写；目录 slug 只用于定位文件。没有一级标题的旧文档才以目录名作为展示标题。登记后的首次标题搜索应能使用用户提交的原始标题片段。
- 打开卡片时，稳定 ID 解析到当前实验目录。修改标题或 `slug` 不应成为新的资源身份。
- 文件树只展示实验目录边界内可读取的内容。伴随文件的嵌套地址不能越过该目录。
- Markdown 和代码以文本呈现，图像和其他支持类型使用相应预览。实验详情复用 Knowledge 的展示界面，身份和生命周期仍由 Experiment provider 管理。

### 登记

- 调用方通过 ResourceService 请求登记，并指定目标 `project_id` 与 `title`。服务先保留 `experiment_*` ID，再由 Experiment provider 创建目录和 `experiment.md`。
- 可选 `slug` 必须以小写字母或数字开头，只包含小写字母、数字、下划线和连字符，长度不超过 80。后端直接校验调用方提交的值，不把大写静默改成小写。未提供时由标题转成 ASCII；没有可用字符时使用 `experiment`。未来提供登记表单时，界面可以预览规范化建议，但必须明确标为尚未保存，并在提交前检查规范化结果是否冲突。
- `project_id` 长度不超过 80，标题必填且不超过 200。`status` 默认 `planned`，最长 80；`importance` 默认 5，必须是 0 到 10 的整数；布尔值、小数、数字字符串和空值不接受，登记与结构化更新使用同一校验，不做静默截断或类型转换。
- 初始正文可以通过 `body` 提供；未提供时使用 `summary`。正文最多 2,000,000 个字符。
- 目标 Project 不存在、目录名冲突、ID 重复或字段非法时，登记失败。创建文件失败时，provider 清理本次留下的空目录，ResourceService 不把失败当作已登记。
- 同一已登记 ID 的幂等重试返回现有资源，不创建第二个目录。调用方不能通过 payload 自报外部资源 ID 或幂等内部字段。

### 更新

- ResourceService 的结构化更新只接受 `status`、`importance`、`body` 和 `summary`，且至少提供一项。
- `body` 与 `summary` 都表示新正文；更新时保留文档已有的第一个一级标题。正文仍受 2,000,000 字符限制。
- 更新状态和重要性会改写主文档的规范文首。写入在 Project 级实验锁内执行，避免同一项目的登记、更新和退役互相穿插。
- Experiment provider 当前没有修订号或 `expected_version`。并发调用方不能假定服务会检测旧版本；成功的后一次写入成为当前内容。
- 页面详情的文本保存是当前接入的另一条入口，它直接保存所选实验文本。结构化 provider 更新是 Agent/服务能力，页面尚未为四个字段提供专门表单。

### 退役

- ResourceService 的 `unregister` 退役整个实验资源，只接受资源根。
- 更新和退役要求目标主文档位于该 Project 的独立实验目录中，且解析后的文件仍在该目录内。旧式平铺主文件、指向目录外的符号链接或其他越界布局在写入和移动前被拒绝，保留原文件与相邻实验；读取支持不扩大修改边界。
- provider 把 `<slug>__<id>` 整个目录移入同一 `knowledge_base` 下的 `.trash`，保留主文档和全部伴随产物。
- 回收目标重名时添加随机十六进制后缀，避免覆盖。成功结果返回 `deleted: true` 和 `trash_path`。
- ResourceService 在 provider 完成移动后忘记登记身份。以后重新登记会取得新的 `experiment_*` ID；旧 ID 不指向新实验。
- Experiment 页面没有退役按钮，退役只能通过指令进行。

## 页面、服务与权限的边界

| 层次 | 当前能力 |
| --- | --- |
| 页面 | 搜索、Project 筛选、语义搜索、卡片、详情、伴随文件预览、文本编辑、Chat |
| HATG 资源指令 / ResourceService | 登记、统一解析、读取、结构化更新、退役 |
| provider 内部 | Project 锁、目录命名、规范主文档生成、边界校验和移入 `.trash` |

页面读取需要查看权限（viewer）；详情文本保存、登记和结构化更新需要编辑权限（contributor）；退役需要管理权限（admin）。Experiment provider 进程能写 Project 文件，但只执行 ResourceService 已经授权的操作。

## 组件与一次工作流

| 组件 | 责任 |
| --- | --- |
| Resource Catalog | 呈现实验卡片、筛选和详情入口 |
| Resource Index | 汇总当前 actor 可见的 Experiment 摘要与属性 |
| ResourceService | 保留统一 ID、授权并协调登记记录与 provider |
| Experiment provider | 创建规范目录、验证字段、更新主文档、整目录退役 |
| Knowledge detail backend | 解析实验主文档和目录内嵌套文件，提供通用预览 |

一次登记按以下顺序流动：

```text
调用方提交 project_id、title 和可选初始字段
        ↓
ResourceService 授权并保留 experiment_* ID
        ↓
Experiment provider 取得 Project 实验锁并复核目录
        ↓
创建 <slug>__<id>/experiment.md
        ↓
ResourceService 提交登记，Resource Index 随后发现它
```

先保留身份，再写入文档，使 `kb_id` 与最终登记一致。目录锁协调扫描、创建和退役，避免这些操作互相穿插；整目录回收让实验叙述与产物一起保留。

## 精确接口与字段

### Resource Catalog 与详情

| 请求 | 用途 |
| --- | --- |
| `GET /resource/experiment` | 打开 Experiment Resource Catalog；`item=<id>&embedded=1` 打开详情 |
| `GET /api/resource-index/query?resource_type=experiment&include_attributes=1&limit=<n>` | 查询实验卡片，可追加 `q`、`project` |
| `GET /api/resource-index/resources/<experiment_id>` | 读取单个索引记录 |
| `GET /knowledge/api/detail?kb_id=<experiment地址>` | 读取主文档或伴随文件详情 |
| `POST /knowledge/api/detail/save` | 以 `kb_id`、`text` 保存当前可编辑文本 |

Resource Catalog 语义检索使用 `/api/resource-semantic-searches` 系列接口，并把资源范围设为 `experiment`。

### HATG 资源指令

| 指令 | 参数 | 作用 |
| --- | --- | --- |
| `RegisterResource` | `resource_type: "experiment"`，`fields` 中必填 `project_id`、`title`，可选 `slug`、`status`、`importance`、`body`、`summary` | 登记新实验，调用者成为 owner |
| `UpdateResource` | `resource_id`，`changes` 中只允许 `status`、`importance`、`body`、`summary` | 结构化更新；未知字段和空更新被拒绝 |
| `UnregisterResource` | `resource_id`（必须是资源根） | 退役整个实验目录 |

指令的通用规则见 decision_ad3e21094f14。退役不删除 Project，也不修改其他知识目录。

## 故障与恢复

Project 缺失返回找不到目标；同一 ID 在多个目录出现时视为数据冲突，不任意选择一个。登记失败会回收本次空目录；更新使用原子文件替换，失败时保留旧主文档；退役只有在目录移动成功后才注销身份。已移入 `.trash` 的目录可由维护者恢复材料，但 Portal 没有把旧 ID 重新激活的一键控件。
