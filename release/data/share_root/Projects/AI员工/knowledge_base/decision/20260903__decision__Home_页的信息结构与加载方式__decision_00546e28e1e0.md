---
kb_id: decision_00546e28e1e0
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 13
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/index.html
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/management_portal/static/portal_workbench.css
  - ${code_root}/management_portal/static/home_workbench.css
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_api.js
  - ${code_root}/management_portal/static/portal_state.js
  - ${code_root}/tests/cases/test_home_frontend_contract.py
  related:
  - decision_e486671af066
  - decision_fc26f883773d
  - decision_481dc4e6292c
  - decision_ed57c4ad1001
  - decision_e30897dd3dc3
  - decision_4dea0db1f45a
  - decision_865ac8ebef25
  - decision_5d2bf4a72f09
  - decision_605810a1292b
  - decision_152d5c046a23
  - decision_538e155e1102
  - decision_ef80c6d3b5ee
  - decision_6e230393221c
  - decision_741b04821171
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Home 页的信息结构与加载方式

Home 是用户登录 TFI Portal 后看到的概览页，地址为 `/`。它回答“现在发生了什么”：最近的公告与新闻、团队和自己的工作进展、正在运行的 Resident、执行账号的额度和定时与自动触发的工作。Home 只提供概览和快捷入口，完整的阅读、编辑和操作在各自的页面中完成。

本 Design 说明 Home 上有哪些区域、它们怎样排列、什么时候加载和刷新，以及页面状态怎样保存。每个区域显示什么数据、提供哪些操作，由各自的 Design 说明（见“页面上的区域”）。

## 页面上的区域

页面最上方是所有 Portal 页面共用的顶栏：品牌、主导航和当前用户名账号菜单，退出登录在账号菜单里（decision_e486671af066）。顶栏下方是 Home 的内容区。

内容区分为三个**一级区域**，每个一级区域里又有几个**子面板**：

```text
Home 内容区
├─ Get Me Aligned        快速了解最近发生了什么
│  ├─ Notice Board       公告与讨论
│  ├─ Daily News         今日行业新闻
│  └─ Agent Access       常用 Resident 入口
├─ My Pulse / Publish    团队和自己的工作进展
│  ├─ Knowledge Pulse    团队知识和项目变化摘要
│  └─ Personal Pulse     个人进展摘要与公开发布
└─ Agent Status          Agent 与资源的运行情况
   ├─ AI Activity & Accounts  正在运行的 Resident 与执行账号
   ├─ GPU / Disk Resources    GPU 与磁盘状态
   ├─ Scheduled & Triggered   定时与自动触发的 Resident（按 Lineage 分组）
   └─ Usage Attribution       token 用量统计
```

| 子面板 | 负责的 Design |
| --- | --- |
| Notice Board | decision_605810a1292b |
| Daily News | decision_5d2bf4a72f09 |
| Agent Access | decision_152d5c046a23 |
| Knowledge Pulse、Personal Pulse | decision_865ac8ebef25 |
| AI Activity & Accounts | decision_4dea0db1f45a（执行账号）、decision_e30897dd3dc3（活动 Resident） |
| GPU / Disk Resources | decision_ed57c4ad1001 |
| Scheduled & Triggered | decision_481dc4e6292c |
| Usage Attribution | decision_fc26f883773d |

Notice Board 的帖子详情和发帖表单以对话框的形式覆盖在页面上，关闭后回到原来的位置。

## 布局

Home 的布局使用 Portal 共享的工作台组件 Workbench（decision_538e155e1102）。Workbench 把页面分成可以拖动、分栏、组成标签组、折叠和隐藏的面板，并按用户保存布局。Home 有两层 Workbench：外层排列三个一级区域，每个一级区域内部再用一个 Workbench 排列自己的子面板。

- **用户可以自由调整两层布局。** 拖动面板标签可以改变分栏或组成标签组，拖动分隔条可以改变大小；面板可以折叠或隐藏，隐藏的面板可以从布局菜单恢复。隐藏只影响显示，不删除任何数据。
- **首次进入且当前用户没有保存布局时使用“账号与用量”布局。** 外层只显示 Agent Status；内部 AI Activity & Accounts 在上、Usage Attribution 在下，GPU / Disk Resources 和 Scheduled & Triggered 隐藏。Get Me Aligned 和 My Pulse / Publish 两个一级区域隐藏，可从布局菜单恢复。已保存的两层布局继续优先恢复，不因默认预设调整而重置。
- **外层布局菜单提供五个预设布局。** “每日概览”和“账号与用量”同时设置外层和相关一级区域的内部布局，其余三个只改变外层排列，一级区域内部保持原样：

  | 预设 | 外层排列 | 同时设置的内部布局 |
  | --- | --- | --- |
  | 每日概览 | 上方 Get Me Aligned 与 My Pulse / Publish（宽度 2:1），下方 Agent Status | Notice Board 与 Daily News 并排；Knowledge Pulse 在上、Personal Pulse 在下；AI Activity & Accounts 在上、Scheduled & Triggered 在下；Agent Access、GPU / Disk Resources 和 Usage Attribution 隐藏 |
  | 概览 | 与“每日概览”相同 | 无 |
  | 从上到下 | 三个一级区域从上到下排列 | 无 |
  | 账号与用量 | 只显示占满内容区的 Agent Status，另外两个一级区域隐藏 | Agent Status 中 AI Activity & Accounts 在上、Usage Attribution 在下，另外两个子面板隐藏 |
  | 标签组 | 三个一级区域组成一个标签组 | 无 |

  每个一级区域的内部 Workbench 另有自己的布局按钮，与子面板标签在同一行，提供该区域自己的预设（例如 Get Me Aligned 的“概览”：Notice Board 与 Daily News 并排，Agent Access 在下方）。
- **外层布局按钮位于顶栏右侧。** 外层 Workbench 的当前布局名称、布局菜单和恢复隐藏面板的入口放在全局导航右侧，与 Society 页面的位置一致。
- **只剩一个标签的一级区域收起标题栏。** 鼠标移到这个区域的上边缘时标题栏出现，移开后收起；鼠标在内部子面板的标签或按钮上时，外层标题栏保持收起。
- **手机等窄屏上一次只显示一个面板。** 使用 Workbench 的单面板切换方式；首次“账号与用量”布局显示 Agent Status 中的 AI Activity & Accounts，可切到 Usage Attribution。

## 加载与刷新

- **每个区域独立显示加载中、空内容、成功和失败。** 一个区域读取失败，只在这个区域显示错误，其他区域照常阅读和操作。
- **一个区域第一次显示时才开始读取数据。** 页面先读取当前用户；读取成功或失败之后，当前可见的子面板开始加载。被隐藏、被折叠、在未选中的标签页里，或所在一级区域被折叠的子面板暂不加载，等用户让它显示出来时再加载。
- **加载过的区域保留组件和内容。** 之后折叠、隐藏再打开不重新初始化。运行器只管理首次连接与销毁；已连接面板是否暂停定时读取、恢复可见时是否重读，由该面板的刷新合同决定。例如 Scheduled & Triggered 隐藏时停止计时、重新可见时读取当前状态（decision_481dc4e6292c）。
- **Home 没有统一的刷新周期。** 各面板的刷新时机、后台标签页行为和错误重试由对应 Design 唯一说明：Daily News（decision_5d2bf4a72f09）、Notice Board（decision_605810a1292b）、Pulse（decision_865ac8ebef25）、Agent Access（decision_152d5c046a23）、账号（decision_4dea0db1f45a）、活动 Resident（decision_e30897dd3dc3）、GPU / Disk（decision_ed57c4ad1001）、Scheduled & Triggered（decision_481dc4e6292c）和 Usage Attribution（decision_fc26f883773d）。Knowledge Pulse 与 Personal Pulse 共用一次读取，两个面板各自选择时间范围。

- **Agent Status 的刷新按钮只刷新可见的区域。** 按钮位于 AI Activity & Accounts 中。点击后，AI Activity & Accounts 可见时要求服务端重新采集账号数据；GPU / Disk Resources 可见时要求重新采集 GPU 和磁盘；Scheduled & Triggered 可见时重新读取列表。Usage Attribution 使用自己的刷新按钮。
- **列表中的链接只负责跳转。** 点击 Resident、帖子或资源进入对应页面，由目标页面提供完整功能并检查权限。
- **图标按钮都有文字说明。** 箭头表示进入，刷新图标只刷新所在区域，铅笔和垃圾桶表示编辑和删除；只有图标的按钮同时提供悬停标题和无障碍名称。

## 页面状态怎样保存

- **两层 Workbench 的布局按用户自动保存在浏览器中。** 保存内容包括面板排列、每个标签组当前选中的标签、隐藏和折叠的面板、分栏比例和用户命名的布局。拖动结束、折叠或隐藏之后立即保存，离开页面前再保存一次，不需要手动点“保存”。
- **再次打开 Home 时恢复上次的布局。** 切换页面后返回、刷新页面或浏览器前进后退时，两层布局分别恢复；窗口大小变化时按保存的比例适配，并遵守每个面板的最小尺寸。
- **布局按 Portal 用户分开保存。** 保存位置的名称包含用户名，同一浏览器中的不同用户互不影响；没有用户名的旧记录不会被使用。页面读取到当前用户之前，布局先保存在内存中；读取到用户后，如果这个用户还没有保存过布局，就把这段时间的调整保存到该用户名下。
- **选择预设后仍可继续调整。** 预设涉及的每个 Workbench 分别保存应用后的布局。
- **布局只保存在当前浏览器中。** 换一台电脑或换一个浏览器且没有保存布局时，使用“账号与用量”重新开始。
- **临时展开状态只在当前页面中保留。** 打开的帖子详情、Scheduled & Triggered 中展开的行在离开页面后不保留。刷新 Agent Status 时页面保留已展开的行；活动 Resident 图标带内容没有变化时不重新绘制，避免动画和键盘焦点被重置。

## 实现：组件树与一次加载

Home 的前端由三部分组成：

| 部分 | 文件 | 职责 |
| --- | --- | --- |
| 页面模板 | `index.html` | 定义三个一级区域和 Home 自有子面板的根节点、Workbench 挂载点、对话框和加载占位文字 |
| Home 组件运行器 `PortalHomeComponents` | `static/portal_home_components.js` | 把每个子面板包装成 Workbench 可以放置的面板；记录每个面板是否可见；决定各子面板的加载函数何时执行；接收功能登记的子面板；页面离开时释放资源 |
| Home 控制器 | `static/home.js` | 声明组件树、布局和预设；实现 Home 自有子面板的读取、渲染和操作 |

Notice Board 属于可选功能，不在 Home 的模板和控制器中。它的标记、对话框、样式和脚本是一个页面片段（`notice_board_home.html`、`static/notice_board_home.{css,js}`），由功能通过 `web.home_panel` 接入点登记（decision_15aab18c0ab3）；Web 返回 Home 时把已安装功能的片段插在 `home.js` 之前。片段脚本调用 `PortalHomeComponents.registerPanel`，登记面板 ID、所属一级区域、区域内次序、标题、图标、根节点和加载函数；Home 把每个一级区域自有的子面板与登记的子面板按次序合并，组成组件树、区域内 Workbench 的面板和“标签组”预设。默认布局和保存的布局可以写到没有安装的面板，Workbench 规范化布局时跳过它们。深链接（例如 `?notice_post=`）由登记面板自己标记为请求打开，Home 挂载后展开它所在的一级区域并聚焦它。Portal 不带 Notice Board 时，Home 照常显示其余子面板。

组件树以 `home-shell` 为根，下面是三个一级区域，再下面是各区域的子面板，每个节点有稳定的 ID（例如 `agent-access`）和页面中的根节点。`home-shell` 在页面打开时立即执行，只读取当前用户；每个子面板有一个加载函数，由运行器在合适的时机调用。

运行器为每个节点记录一个状态，并写在根节点的 `data-home-component-state` 属性上，同时发出 `portal:home-component-state` 事件，供测试和调试使用：

| 状态 | 含义 |
| --- | --- |
| `deferred` | 还没到加载时机：当前用户尚未读取完成，或者这个节点或它的上级区域不可见 |
| `mounting` | 加载函数正在执行 |
| `ready` | 加载函数执行结束；加载函数返回 Promise 时，等 Promise 完成后才进入这个状态。它表示连接已经建立，业务数据是否读取成功由面板自己显示 |
| `error` | 加载函数抛出错误；只影响这个节点，兄弟节点照常加载 |
| `destroyed` | 页面离开，节点已经释放 |

一次加载的过程：

1. 浏览器显示页面骨架，所有区域都有加载占位。
2. Home 先创建三个一级区域内部的 Workbench，再创建外层 Workbench，按当前用户恢复两层布局。Workbench 显示或隐藏一个面板时，向面板根节点发出 `portal:home-panel-lifecycle` 事件，运行器据此记录面板是否可见。
3. 运行器建立组件树：`home-shell` 开始读取当前用户，其他节点处于 `deferred`。
4. 当前用户读取成功或失败后，运行器检查每个 `deferred` 节点：节点自己和所有上级都可见，就调用它的加载函数。等待期间用户折叠或隐藏的面板保持 `deferred`。
5. 之后每当面板显示或隐藏，运行器等这一轮布局变化全部完成（下一个微任务）再统一检查一次，把新变为可见的 `deferred` 节点加载起来。运行器只使用 Workbench 报告的显示状态，不自己计算布局或屏幕位置。
6. 每个子面板只更新自己的根节点。Knowledge Pulse 和 Personal Pulse 共用一次读取，各自绘制自己的内容。
7. 页面离开时，运行器按从内到外的顺序取消每个节点尚未完成的请求、执行它返回的清理函数（释放事件监听、刷新定时器和订阅），然后拆除 Workbench，把面板内容放回暂存节点。页面从浏览器往返缓存恢复时重新执行整个过程，事件监听和订阅各只有一份。

各子面板的定时刷新使用 Portal 共享的刷新协调器（decision_6e230393221c），它负责定时、合并重复请求和订阅服务端推送的变化通知。

## 深链接

Home 的地址可以带参数，直接打开页面上的某个对象：

| 参数 | 效果 |
| --- | --- |
| `notice_post=<帖子 ID>` | 让 Get Me Aligned 和 Notice Board 显示出来，帖子列表加载完成后打开这篇帖子 |
| `background_resident=<Resident ID>` | 让 Agent Status 和 Scheduled & Triggered 显示出来，并展开这个 Resident 的详情行 |

带参数的访问被视为用户明确要求打开目标，即使保存的布局隐藏了所在区域，也会把它显示出来。没有参数的普通访问保持用户保存的布局。参数不绕过登录和权限检查：帖子不存在或无权查看时，由 Notice Board 显示错误。
