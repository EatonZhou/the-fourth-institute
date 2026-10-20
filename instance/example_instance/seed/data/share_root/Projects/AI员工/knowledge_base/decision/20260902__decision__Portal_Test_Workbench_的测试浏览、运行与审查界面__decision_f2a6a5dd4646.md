---
kb_id: decision_f2a6a5dd4646
type: decision
project: AI员工
created_at: '2026-09-02'
version:
  number: 29
  created_at: '2026-09-02'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:21:12Z'
  updated_by:
    user: ''
    agent: resident_7c2eadd4b668
importance: 7
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/test_framework/page/test_native_view.html
  - ${code_root}/management_portal/test_framework/web.py
  - ${code_root}/management_portal/static/development_workspace.js
  - ${code_root}/management_portal/test_framework/page/test_workbench.js
  - ${code_root}/management_portal/test_framework/api.py
  - ${code_root}/management_portal/resources/providers/test.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/portal_demo_player.js
  - ${code_root}/management_portal/static/portal_live_tour.js
  - ${code_root}/management_portal/test_framework/page/test_workbench_core.js
  - ${code_root}/management_portal/test_framework/page/test_catalog_panel.js
  - ${code_root}/management_portal/test_framework/page/test_case_panel.js
  - ${code_root}/management_portal/test_framework/page/test_run_console_panel.js
  - ${code_root}/management_portal/test_framework/page/test_tau_panel.js
  - ${code_root}/management_portal/test_framework/page/test_gate_composer_panel.js
  - ${code_root}/management_portal/test_framework/page/test_review_panel.js
  - ${code_root}/management_portal/test_framework/page/test_live_adapter.js
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/management_portal/static/portal_workbench_context.js
  - ${code_root}/management_portal/static/portal_command_registry.js
  - ${code_root}/management_portal/resources/portal.py
  - ${code_root}/management_portal/static/portal_resource_chips.js
  related:
  - decision_8273271b67b1
  - decision_c953eab654cd
  - decision_6febe7bb57f9
  - decision_ef80c6d3b5ee
  - decision_538e155e1102
  - decision_741b04821171
  - decision_ce44377c3257
  - decision_79de71d9890d
  - decision_c524c775e0be
  - decision_6ad0384e020d
  - decision_9f6135cbbb22
  - decision_cf27d39e39cd
  - decision_4a9ba5dfada6
  - decision_f67ae5e70793
  - decision_e5f66e2f6a5e
  - decision_06defc47e960
  supersedes: []
  derived_from: []
---
# Portal Test Workbench 的测试浏览、运行与审查界面

Test 工作区是开发投影页中与 WALL 并列的一个视图，供开发者和审查者查找测试、阅读测试定义、运行测试、观察运行证据和发现覆盖缺口。它由六个可以并排摆放的面板组成，按用户当前的任务切换五种布局。本 Design 说明这些面板显示什么、用户能做什么、页面保存哪些状态；测试条目、测试目录、运行计划和运行记录的数据与接口见 `decision_8273271b67b1`，TAU 测试的执行机制见 `decision_c953eab654cd`，发布前验证见 `decision_6febe7bb57f9`。

## 基本概念

- **Workbench（工作台）**：Portal 的公共分栏容器，可以把多个面板左右排列、拖动分隔线调整宽度、隐藏或显示面板，并按用户保存布局。
- **Panel（面板）**：工作台中带标题、图标和独立内容的区域。每种面板以 Panel Package（面板包）登记创建函数、尺寸限制、实例数量、所需权限和保存格式版本，通用协议见 `decision_538e155e1102`。
- **Activity Rail（活动栏）**：页面左侧的竖直图标栏。开发投影页的活动栏在开发投影和已安装的 WALL、Test 视图之间切换；进入 Test 后，Test 的活动栏在五种视图之间切换。
- **视图**：一套预设布局，决定显示哪些面板以及它们的初始宽度比例。
- **Catalog store（目录缓存）**：浏览器中各测试面板共享的数据：已加载的测试目录、当前选中的条目、多选集合、实现选择和最近运行列表。它是服务端数据的副本，刷新页面后重新从服务端读取。

## 页面结构

测试工作区的地址是 `/development?view=test`。条目链接 `/wall?case=<条目 ID>` 和运行链接 `/wall?run=<运行 ID>` 会自动进入 Test 并选中对应内容；Portal 中出现的 `testrun_…` 运行 ID 显示为可点击的引用，打开同样的运行链接。Test 没有独立页面，`/test/api/*` 接口保持不变。 `/wall?view=test` 作为旧地址进入同一工作区，保留条目与运行定位参数，不作为另一套实现。

Test 视图由 Test 功能自己提供：`management_portal/test_framework/page/test_native_view.html` 保存它的页面模板和样式、脚本清单，Test 功能通过 `web.native_view` 接入点把它登记到开发投影页（接入点见 `decision_15aab18c0ab3`）。页面首次进入 Test 时才读取这个模板并载入 Test 的脚本；`test_workbench.js` 用 `PortalDevelopmentWorkspace.registerView("test", …)` 登记挂载、显隐和销毁函数。没有安装 Test 功能的 Portal 不在活动栏列出 Test，`/wall?view=test` 打开开发投影。

用户第一次切换到 Test 时才创建测试面板并加载目录；之后在墙面和 Test 之间切换，面板实例、筛选条件、草稿和正在观察的运行都保留。

| 视图 | 图标 | 显示的面板（从左到右，初始宽度比例） | 用途 |
| --- | --- | --- | --- |
| 目录 | 列表 | Test Catalog、Test Case（360 / 900） | 找到条目并阅读定义 |
| 运行 | 播放 | Test Catalog、Run Console、TAU Campaign（300 / 640 / 420） | 选择范围、执行并观察 |
| AI 真实使用 | 对话 | Test Catalog、TAU Campaign、Run Console（300 / 520 / 620） | 按模块选择 TAU 用例 |
| 门禁 | 勾选 | Gate Composer、Test Catalog、Run Console（520 / 360 / 600） | 用模板组合一批测试 |
| 审查 | 搜索 | Review、Test Catalog、Test Case（520 / 320 / 600） | 查看覆盖分布和缺口 |

用户可以拖动分隔线调整宽度。Test Catalog 的最小宽度是 280，其他测试面板是 360。六种测试面板都以面板包版本 1、保存格式版本 1 登记，每种在工作台中只有一个实例。

## 设计点

### Test Catalog：查找和选择条目

- **面板顶部显示目录摘要，下面是筛选区和结果列表。** 摘要包含条目数、设计点数、没有实现的条目数和待检查条目数；筛选区提供搜索、重要级别、功能模块分组、功能模块、是否设计点、覆盖角度和“仅可实时运行”（有 Live 演示）。
- **结果列表每行显示一个条目。** 行内显示自然语言说明、模块、重要级别、源码来源和标记（覆盖角度、设计点是否已绑定 WALL 设计点、Live）；每行有多选框，点击摘要选中该条目。完整的 `key`、生命周期和实现细节在 Test Case 面板中查看。
- **筛选结果由服务端计算。** 页面把筛选条件交给 `GET /test/api/catalog/query`，只取回匹配的条目 ID，再用已加载的目录显示它们。搜索输入停止 160 毫秒后才发请求；较早请求的迟到响应不会覆盖较新的结果。清除全部筛选恢复完整列表。
- **列表始终隐藏已退役的条目，每批显示 100 条。** 用户可以继续加载更多；面板没有生命周期筛选控件。
- **“生成运行计划”把多选集合送到 Run Console。**
- **地址中的 `case` 参数决定初始选中的条目。** 参数是目录中存在的条目 ID 时选中它，否则选中第一条可见条目。选中变化时，页面用 `history.replaceState` 更新地址，不增加浏览器历史记录。
- **筛选弹层可以用 Escape 或点击外部关闭。** 工具栏中筛选摘要和结果计数各占一块，摘要过长时省略显示，窄面板中仍能看清计数。

### Test Case：理解一个条目

标题区显示 `key`、标题、说明、模块、重要级别、覆盖角度、设计点和生命周期标记，并提供“运行计划”和“加入 TAU”按钮。正文分为四个可折叠区域：

- **概览以只读方式显示条目定义。** 内容包括重要级别、模块、覆盖角度、是否设计点、关联的 Design，以及“WALL 设计点（一对一）”绑定状态：页面通过 `/api/wall/test-binding` 查询绑定是否有效、指向哪个设计点。概览末尾说明这些内容写在测试源码的 docstring 中。
- **条目在测试源码中修改。** 页面不提供编辑、退役或恢复动作；标题、说明、分类、WALL 设计点绑定和生命周期都在测试的 docstring 或显式用例 JSON 中声明，提交后目录随源码更新（字段写法见 `decision_8273271b67b1`）。退役一个条目就在它的 docstring 中写 `:lifecycle: retired`，删除测试函数则条目随之消失。
- **测试实现区列出每个实现。** 每项显示执行方式、入口、运行配置、执行风险和可选的任务说明；实现风险与运行配置风险分开显示，未声明风险时显示提醒。单选按钮决定默认实现；“准备运行”只选中该实现；“预览源码”通过 `/test/api/source` 读取源码，在公共预览中打开并定位到起始行。
- **运行证据区列出包含本条目的保留运行。** 列表从最近运行中筛选，因此其他用户发起的相关运行也可见；状态、输出和错误来自运行记录，不写回条目。
- **Live 区提供只读演示入口。** 条目没有登记演示时显示不可用说明。

目录首次加载时只取每个条目的浏览字段；选中某个条目时，面板再通过 `/test/api/catalog/case` 取完整定义，同一条目只请求一次。

测试用例面板按当前登录用户及用例 ID 保存分区展开状态，匿名浏览只使用内存；切换面板、布局重建和刷新后恢复，切换到其他用例时各自保存。

### Run Console：计划、运行和证据

- **Run Console 分为运行计划、当前运行和最近运行三块。** 计划区显示选择条件、命中的条目、每个具体实现、运行配置、排除原因和是否可以执行；当前运行区按分组显示结果和输出：本地运行按 pytest 文件分片，以分组名称（如 `pytest 1/3`）标识，等待主机并发名额时显示等待状态；TAU Campaign 每个模块一组，还显示模块数、用例数、通过数、问题数、并发策略、各模块状态以及每个用例的体验、诊断和证据；最近运行区列出服务端保留的全部运行，不按发起者筛选。
<!-- wall: {"key": "75c102096d38a68d"} -->
- **每个 Run Console 实例保存自己的运行计划草稿。** 两个实例分别选择的用例互不影响；从 Test Case 打开 Run Console 时，已有的草稿不被覆盖；没有明确的运行 ID 时不自动恢复历史运行。
<!-- wall: {"key": "b799af335b2335a0"} -->
- **计划预览与开始运行由 Run Console 明确发起。** “生成计划”调用 `POST /test/api/preview`；“开始”只在计划可执行且 `access.can_operate` 允许时可用，把计划请求提交给 `POST /test/api/run`，控制台以返回的运行记录为准。接口认证、启动重建计划和运行状态契约统一见 `decision_8273271b67b1`，界面不另定义 `test_lab` 权限。
- **运行中每 900 毫秒刷新一次状态。** 读取出错时保留已有输出并显示错误，1500 毫秒后重试；运行结束后停止刷新。切换到另一个运行或关闭面板后，旧运行的迟到响应不会覆盖当前显示。
- **取消只提交当前运行的 ID。** 控制台先显示服务端返回的 `cancelling` 或最终状态，再继续等待结果；是否通过只看运行记录的状态，不从输出文字推断。
- **打开或启动运行后，地址记录 `run=<运行 ID>`。** 重新打开该地址会读取同一运行，未结束的继续刷新；地址中没有运行 ID 时，控制台不会从最近运行中自动挑选一个。
- **打开已不存在的运行时，控制台显示“运行报告已不存在”。** 同时清空之前显示的运行和计划，提示从最近运行中选择其他报告或返回墙面，不自动启动新运行。
<!-- wall: {"key": "6822717bb2f93fa4"} -->
- **删除已结束的运行报告需要明确确认。** 最近运行列表支持勾选后批量删除，当前报告也有单独的删除入口；运行中的报告不能删除。删除后各面板同步移除对应报告和选择；产物清理失败时显示服务端返回的警告。服务端权限和批量删除原子校验见 `decision_8273271b67b1`，删除报告不影响条目、Design 或 TAU Resident。

### TAU Campaign：按模块选择真实使用任务

- **面板按功能模块列出所有带 `tau` 实现且未退役的条目。** 用户可以跨模块勾选任务或整组选择，“运行”把选择交给 Run Console 生成 TAU Campaign：不同模块并发执行，同一模块内按顺序执行。
- **每个模块显示负责执行的 Resident。** “身份详情”显示该模块的 Resident、Lineage 和创建账号，所有登录用户可见；模块 Resident 已存在时，“对话”在工作台侧栏打开与它的聊天。
<!-- wall: {"key": "8b70ca06d257a93c"} -->
- **模块更换操作显示当前槽位与执行身份。** “更换”调用 `/test/api/tau/resident/rotate`，成功后刷新槽位投影，下一次运行使用新槽位；失败显示实际原因，不在界面先行宣告更换成功。旧成员释放、忙碌保护和历史记录边界统一见 `decision_c953eab654cd`；按钮沿目录返回的 `access.can_operate` 控制，服务端认证见 `decision_8273271b67b1`。
- **TAU 执行方式不可用时，任务列表照常显示。** 面板显示不可用原因并禁用运行按钮。模块 Resident 的身份、账号和执行规则见 `decision_c953eab654cd`。

### Gate Composer：用模板组合测试

- **Gate Composer 提供三个即时模板。** “关键路径”选择 P0 条目，“当前设计点”选择设计点条目，“测试工作台”选择测试框架与测试工作台两个模块的条目；三个模板都排除已退役条目。
- **每个模板显示匹配数量、涉及的模块数、P0/P1 数和设计点数。** 用户可以展开查看范围（先显示前 8 条），“生成运行计划”把同一批条目送到 Run Console；没有匹配条目的模板禁用该按钮。
- **模板只生成选择，不保存门禁。** 页面不创建门禁 ID、不保存版本，也不触发发布；执行仍需在 Run Console 中预览并明确启动。

### Review：观察覆盖缺口

- **Review 先给出覆盖结论，再按需展开明细。** “审查摘要”显示测试条目数、功能模块数、缺口模块数和 AI 使用（TAU）条目数；“待审查信号”按“没有测试用例”“缺少 P0/P1”“缺少设计点”三类列出模块；“模块分布”表列出每个模块的总数、P0/P1 数、设计点数和 TAU 数，默认显示前 12 个模块，可以展开全部。
- **Review 只提供可以执行的操作。** 它不显示不可用的占位按钮，也不自动创建审查、修改 Design 或派发修复。

### Live 演示

Runner 原始输出用 `textContent` 写入独立区域，保留开头换行、CRLF 与字面 HTML，避免 HTML 解析改变证据。Inbox 演示在 prepare 阶段展开并完成状态加载；commit 保持原有 POST 写入保护，使用已加载的只读状态验证 closed 标记。WALL 来源演示按正式资源 ID 与工程内 Markdown 路径分别验证 iframe 详情与本地 Markdown 详情，关闭后核对墙面顺序和滚动位置。

保留历史演示通过 Run Console 的 `data-run-raw-output` 区域核对原始输出；TAU 体验、诊断与元数据各有独立正文，不参与这项比较。Inbox 接收关闭演示使用当前面板支持的触控展开语义，并在结束时恢复原展开状态，不修改消息已读标记或 Resident 的接收状态。

历史全目录 Live 浏览器自动化的样本与结果只作为对应版本的证据，不作为当前已登记的浏览器套件或覆盖承诺。Live 的只读约束继续有效，演示状态不代表测试通过；新的真实使用验收按 `decision_c953eab654cd` 的样本与证据规则实施，是否执行按 `decision_6febe7bb57f9` 判断。

演示按当前页面能力验收：Test 实现详情按需加载，并以实现 ID 关联源码；仅有一个实现时，它可以是隐式默认值。WALL 的虚拟化列表通过滚动收集各可见窗口核对完整结果，演示结束恢复原滚动位置。隔离探针按依赖顺序加载文本、翻译、UI 和资源协议。导航演示先展开目标菜单再定位，控制条在连续步骤中持续避开目标。全局搜索遵循当前的资源搜索设计，目录不再包含页面命令演示。


- **Live 帮助读者理解测试步骤，不产生测试结果。** 演示定义从 `GET /test/api/demo` 读取目录中的登记内容，用户不能输入任意地址；演示不创建运行记录，也不能代替真实的通过或失败。
- **真实页面模式在本地真实页面上逐步演示。** `mode=direct` 且声明只读的条目，在登记的真实页面执行具名适配器步骤，显示“真实页面 · 只读保护”、步骤计数、说明，以及重播、下一步、暂停/继续和返回 Test 控件；退出时恢复原来的工作台。自动步骤中的写入和跨源读取被拦截，用户自己操作页面会暂停演示；这层保护不阻止页面自身的后台初始化。
- **沙盒模式在隔离的框架中回放。** `mode=sandbox` 的条目在覆盖工作台的演示层中播放：顶部显示安全标签、步骤计数和关闭入口，中间的 iframe 设置 `sandbox=allow-scripts` 与 `referrerpolicy=no-referrer`，底部提供说明、进度以及重新开始、下一步、播放/暂停。沙盒宿主只代理登记路由的 GET/HEAD 请求，校验允许和必需的查询参数并拒绝重复参数；消息同时校验来源窗口、条目、会话和协议范围；沙盒使用不透明源、内存存储和禁止连接与表单的内容安全策略，fetch 写入、XHR、表单、弹窗和 Beacon 都被阻止。生成沙盒页面时，动态资源路由脚本使用 Portal 统一的路由发布函数，并内联在依赖它的组件之前。
- **跨页面演示以持久会话记录为准。** 演示在多个页面之间推进时，目标页和返回页都以保存的演示会话为当前进度，不用进程内的旧会话覆盖；回到 Test 后，Test 按自己的版本约定恢复原来的状态。
- **演示代码只在演示时加载。** 页面和生产组件（导航、全局搜索、各资源页）不带演示控制器和适配器。从 Test 发起演示时，演示播放器载入控制器 `portal_live_tour.js`；演示会话存在时，任一页面在自己的脚本执行完后由 `portal_state.js` 载入控制器继续演示。控制器按演示定义中的适配器名载入对应的页面适配器（`portal_design_live_*.js`）；Test 自己的适配器 `test-lab` 随 Test 视图载入。适配器通过组件的公开接口操作页面，例如导航的二级菜单与账号菜单（`PortalNav.secondaryNav`、`PortalNav.userMenu`）和全局搜索的打开状态（`PortalCommandPalette.isOpen`、`close`）。
- **WALL 与 Test 按开发投影的视图区分演示宿主。** `/development?view=wall` 与 `/development?view=test` 分别挂载对应视图，演示只能从 Test 视图发起；旧地址 `/wall?view=…` 重定向到开发投影，演示定义中的旧地址与新地址按同一宿主核对。演示启动、续播和返回都核对视图。筛选及条目选择属于同一视图的页面状态，退出同页演示保留尚未保存的编辑。WALL 的隔离演示可以在 Test 视图运行：它把开发投影页以 `view=wall` 载入隔离框架，使用内存数据和存储，不要求隐藏的墙面先完成初始化。
- **本地确认通过普通按钮完成。** 对话框丢弃临时输入等本地确认在只读演示中正常工作；确认操作本身不提交表单，实际网络写入和表单提交仍由只读保护拦截。

### 窄面板

- **TAU 模块身份与操作在窄面板中分行。** 面板宽度不超过 420px 时，“身份详情”与“更换”“对话”分别占一行；操作可换行，英文标签也不覆盖身份说明。

- **面板内容按面板宽度排版。** 恢复多栏布局后，即使某个面板缩窄到约 185px，页头操作、条目标题、说明和标记都在面板内换行，不依赖整页宽度，也不出现横向裁切。

## 权限、状态保存与恢复

目录响应的 `access.can_operate` 用于提示运行、删除报告和模块管理按钮是否可用；服务端认证、发起者取消限制和保留规则以 `decision_8273271b67b1` 为唯一正文，后续业务资源的授权仍按正式接口执行。按钮状态不能代替服务端校验。当前实现不再检查独立的 `test_lab` 系统能力，旧的 Test Lab 文案不能作为授权依据。

| 状态 | 保存位置 | 生命周期 |
| --- | --- | --- |
| 面板布局、尺寸和可见性 | 公共工作台，按用户保存在浏览器中 | 长期 |
| 当前视图和当前条目 | Portal 页面状态（按用户）和地址 `case` 参数 | 长期 |
| 当前打开的运行 | 地址 `run` 参数 | 随地址 |
| 条目定义 | 测试源码（见 `decision_8273271b67b1`） | 随代码 |
| 运行记录 | 服务端（见 `decision_8273271b67b1`） | 按 `decision_8273271b67b1` 的运行保留上限 |
| 筛选、多选集合、实现选择、未启动的计划 | 面板或目录缓存 | 刷新后丢失 |

运行是否在进行、结果如何，只以地址中的运行 ID 和服务端记录为准，不从恢复的布局推断。页面偏好的一般保存规则见 `decision_9f6135cbbb22`。一个执行方式要在页面上显示为可运行，需要同时满足三个条件：目录中登记了实现、接口报告该执行方式可用、页面提供对应控件。

## 面板之间的数据流

```text
Test Framework 接口
  → 目录缓存：条目浏览字段、选中条目、多选集合、最近运行
      ├─ Test Case：按需读取完整定义（只读，定义来自测试源码）
      ├─ TAU Campaign：按模块选择 tau 实现，显示模块 Resident
      ├─ Gate Composer / Review：从目录缓存计算模板和覆盖
      └─ Run Console：生成计划 → 启动 → 带发起者的运行记录 → 刷新状态、打开产物
```

面板通过公共工作台的上下文和命令登记协作，不直接修改彼此的页面元素或私有状态。

## 与 TAU 执行和 Lineage 的关系

TAU 面板展示框架返回的模块、Resident 与执行状态；Campaign 的工作流、投递、恢复和账号准备统一见 `decision_c953eab654cd`，本面板不另定义调度或建队规则。安装与运行条件不足时显示真实不可用原因；已保存的计划和历史结果仍按测试协议读取，不能把未执行显示为通过。

从 Test 工作区、命令行、Lineage 或授权工具启动的测试，只要经过测试框架登记运行，就出现在同一个最近运行列表和查询结果中；任意 Resident 聊天记录不是正式测试记录。能读取运行报告不代表可以控制模块团队，与模块 Resident 的对话仍按普通 Resident 权限检查。

