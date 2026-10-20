---
kb_id: decision_2f67ed28db67
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 15
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:27:04Z'
  updated_by:
    user: ''
    agent: resident_96243cc26373
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/http_resources.py
  - ${code_root}/management_portal/resource_catalog.html
  - ${code_root}/management_portal/static/resource_catalog.js
  - ${code_root}/management_portal/static/resource_catalog.css
  - ${code_root}/management_portal/static/resource_tabs.js
  - ${code_root}/management_portal/static/resource_tabs.css
  - ${code_root}/management_portal/static/portal_detail_surface.js
  - ${code_root}/management_portal/static/portal_semantic_search.js
  - ${code_root}/management_portal/page_renderers.py
  - ${code_root}/management_portal/serve_management_portal.py
  related:
  - decision_f22e45dfd382
  - decision_ad3e21094f14
  - decision_8dc6b3d03ca7
  - decision_864d4fe41934
  - decision_9f6135cbbb22
  - decision_741b04821171
  supersedes: []
  derived_from: []
---
# Portal Resource Catalog 的统一分类导航、资源发现与详情入口

Portal 顶部导航中的 Resource 区按资源类型分成几个页面：Knowledge、Skill、Repo、Article、Experiment、Device 和 Backup。它们共用同一个资源页头，页头中的类别导航用来在这些页面之间切换。其中 Skill（`/resource/skill`）、Experiment（`/resource/experiment`）和 Device（`/resource/device`）三个类别使用同一个 Resource Catalog（资源目录）页面：用户在这里搜索资源、按项目筛选卡片、打开单项资源的详情，并使用语义检索、设备注册和设备终端这些类别自带的入口。Knowledge、Repo、Article 和 Backup 有各自的页面，只共用页头。

本篇说明共享的资源页头、资源目录页面的区域、地址与列表状态、打开详情的方式，以及各类别入口和故障恢复。资源的可查询摘要由资源索引提供（decision_8dc6b3d03ca7）；详情的浏览器历史和宿主见 decision_864d4fe41934。资源目录只把两者组织成一个可以浏览的页面，不保存资源正文。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 资源类别 | 当前浏览的资源类型：`skill`、`experiment` 或 `device`。页面一次只浏览一种类别，它同时决定页面标签、查询范围、卡片内容和详情的显示方式。 |
| 资源卡片 | 资源索引返回的一条摘要，显示标题、摘要、所属项目和几枚说明资源特点的小标签，不含资源正文。 |
| 语义检索任务 | 在当前类别中保存的一次 AI 辅助搜索，结果仍是统一资源 ID。 |
| 详情宿主 | 在当前页面打开单项资源详情的共享机制（decision_864d4fe41934）。 |

## 共享的资源页头

- **所有资源页面使用同一个页头和类别导航。** 各页面只提供页头容器（`resourceSectionPageHeader`、`resourceSectionFrame`、`data-resource-section-header`）和当前类别；`resource_tabs.js` 生成类别导航，`resource_tabs.css` 统一页头的外观和窄屏排列，共享的工具栏统一搜索和操作按钮。页面样式不覆盖页头和类别导航的内部结构。
- **页头和正文使用相同的左右边距。** 边距由 `--portal-resource-page-gutter` 决定：常规宽度为 18 像素，720 像素以下为 12 像素。复杂的工作区可以让正文在内部滚动，但不另设边距。
- **样式按固定顺序加载。** 依次为基础组件、可选的领域组件、`resource_tabs.css`、页面样式、设计系统、主题和最终的工具栏样式。页面样式只定义领域内容，不借加载顺序改变共享导航和工具栏。
- **工具栏的尺寸一致。** 共享工具栏自己为页头及其内部元素设置 `border-box`，不依赖页面的全局样式重置。桌面上搜索框、搜索输入区和操作区的高度分别为 38、30 和 38 像素；各类别的按钮内容可以不同，外层高度、圆角、间距和顶部对齐保持一致。
- **页头在窄屏中不会被撑出屏幕。** 页面不使用固定的最小宽度撑宽页头，700 像素宽时页头仍完整可见；Article 这类复杂工作区在自己的内容区中压缩或滚动。Article 的资源选择器把图标、标题和资源 ID 左对齐放在左侧，展开箭头固定在右侧。
- **操作提示放在输入框和按钮上。** 搜索框的占位文字、按钮名称和悬停提示说明怎样操作；类别导航下方不放常驻的说明文字，筛选摘要、语义检索任务和错误只有在有内容时才占用下一行。

## 资源目录页面

| 区域 | 内容 |
| --- | --- |
| 类别导航 | 在资源类别之间切换。 |
| 查询工具栏 | 搜索框、项目筛选、重新加载按钮、结果数量，以及类别自带的注册或语义检索入口。窄屏上搜索框单独一行，筛选、操作和结果数量共用第二行。 |
| 卡片列表 | 当前类别的资源卡片。 |
| 列表状态 | 首次加载中、已有卡片时的后台刷新、带重试按钮的错误、确认没有结果的空列表。 |
| 详情层 | 由共享的详情宿主或类别专用的详情显示。 |
| 语义检索区 | 保存的语义检索任务、运行状态和结果。 |
| 设备终端 | 针对一台设备打开的终端，可以中断任务或重新开始。 |

## 设计点

### 地址与查询

- **类别由地址决定。** 依次取路径中的类别（`/resource/<类别>`）和 `category` 查询参数，都不可用时为 `skill`。
- **列表通过资源索引查询。** 页面请求 `/api/resource-index/query`，参数为 `resource_type`、`include_attributes=1` 和 `limit=500`；搜索词非空时加上 `q`，选了项目时加上 `project`。
- **连续输入时停顿 220 毫秒后再查询。** “重新加载”沿用当前的类别、搜索词和项目。
- **只显示最后一次查询的结果。** 每次查询有递增的序号和取消信号，较早的响应不会覆盖新的查询。
- **列表状态分开显示。** 首次读取显示加载中；已有卡片时，后台刷新不先清空列表；失败时显示错误和重试；只有读取成功且没有结果时才显示空列表。这些状态使用共享的 `PortalUIPrimitives.asyncStateHtml`，各类别只提供标题、说明、图标和操作；状态文字使用中文，领域专有名称可以保留英文。
- **项目筛选的选项从已经看到的结果中累积。** 列表变化时保留当前选择；切换项目后同步地址并重新查询。
- **后退和前进恢复同一视图。** 地址中保存类别、搜索词、项目和打开的资源；浏览器后退或前进时恢复这些状态，刷新页面和分享链接也得到相同的视图。
- **卡片标签使用中文短名称。** `importance` 大于零时才显示；文件数、节点数这类真实计数即使为零也显示，因为零本身就说明了资源现状。

### 打开详情

- **单击卡片，或在卡片上按 Enter、空格，打开这项资源的详情。** 页面把资源写入详情历史；当前列表中已有这项资源时直接使用它的摘要，从链接直接打开、列表中没有的资源，再读取 `/api/resource-index/resources/<资源 ID>`。单项读取失败只影响详情，列表不变。
- **详情由共享的详情宿主打开。** Skill 与 Experiment 使用框架宿主，iframe 中加载同一类别页面的嵌入详情（地址带 `item`、`embedded=1` 和请求 ID）；Device 使用页内宿主，通过 `/api/resources/resolve` 读取最新内容。放弃确认、详情历史和 iframe 握手都由宿主处理。
- **选中状态跟随宿主接受的打开。** 页面发出打开请求时不提前改变选中的卡片，也不销毁当前详情；宿主接受切换、开始加载时才更新选中状态。历史接受、拒绝和超时恢复由 decision_864d4fe41934「详情历史」统一规定，资源目录只同步宿主已经接受的资源。
- **详情区域是可访问的对话框。** 每个详情区域都带有对话框角色、模态状态和可访问名称：页面提供了 `aria-labelledby` 时使用它，否则共享宿主用详情标题栏中的标题生成关联。
- **详情报告的变化反映到列表。** 详情报告资源已修改时，这张卡片需要重新读取；报告已删除时，移除这张卡片并关闭详情；普通关闭只清除选中状态和详情地址。筛选和其他卡片不变。
- **资源目录不根据详情推断权限。** iframe 已加载不代表用户可以修改资源，详情加载失败也不会让列表变成空。

```text
地址（类别、搜索词、项目、打开的资源、语义检索）
                 │
                 ▼
            资源目录页面
          │             │
          │ 查询         │ 打开的资源
          ▼             ▼
       资源索引        详情宿主
          │             ├─ 框架宿主（Skill、Experiment）
          ▼             └─ 页内宿主（Device）
       资源卡片
```

### 语义检索

- **Skill 和 Experiment 提供语义检索。** 任务以当前类别作为范围（`resource_scope`）保存，页面通过 `/api/resource-semantic-searches` 列出任务，通过 `/api/resource-semantic-search/start`、`refresh`、`delete` 创建、刷新和删除任务。普通搜索直接查询资源索引，两种结果分开显示，不在浏览器中混合排序。打开语义检索的结果同样经过详情宿主，历史和关闭行为一致。

设备详情的概览与登记信息按所在面板的实际宽度排版，窄面板自动换行，字段名称和值保持可读。资源类别切换后，搜索框占位文字与该类别保持一致，后续翻译刷新不能恢复成其他类别。

### 设备

- **注册设备使用资源登记。** 用户填写注册表单后，页面调用 `RegisterResource`（资源类型 `device`）；表单中填写了 SSH 密码时，再调用资源动作 `ssh.credential.save` 保存。成功后页面关闭表单、打开新设备的详情并重新读取列表，提示“设备已注册。”；登记成功而密码保存失败时，设备保留，提示“设备已注册，但 SSH 密码保存失败：”加原因。
- **设备详情中的 SSH 操作使用资源动作。** 测试连接为 `ssh.test`，保存和删除 SSH 密码为 `ssh.credential.save`、`ssh.credential.delete`，都通过 `RunResourceAction`（decision_ad3e21094f14）执行；保存或删除后详情标记为已修改。
- **每台设备的终端相互独立。** 用户点击“打开 Terminal”后，页面为这台设备启动一个终端（`/api/terminal/start`，见 decision_f22e45dfd382）；切换到另一台设备时先重置原来的终端。中断和重新开始只影响这个终端，列表和详情照常可用。

## 组件职责与故障恢复

| 组件 | 负责 |
| --- | --- |
| 资源目录页面 | 当前类别、搜索词、项目、资源卡片、选中的资源，以及各入口的操作状态。 |
| 资源索引 | 当前用户可见的资源摘要、属性和查询结果。 |
| 详情宿主 | 详情地址、详情历史、放弃确认和两种宿主的生命周期。 |
| 领域服务 | 资源内容、登记、修改、撤销登记、设备连接和权限。 |
| 设备终端 | 一台设备的一次终端会话。 |

- **ResourceService 不可用时只遮住资源目录自己的区域。** 页面进入共享的组件恢复流程，保留地址、筛选和页面结构；服务恢复后在原地重新读取当前列表。普通查询错误显示在列表中，不当作“没有资源”。
- **权限由服务决定。** 页面能调用某个接口，不代表当前用户有登记、修改或打开的权限；服务按当前用户检查后返回结果。
