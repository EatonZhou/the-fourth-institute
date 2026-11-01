---
kb_id: decision_f2a6a5dd4646
type: decision
project: AI员工
created_at: '2026-09-02'
version:
  number: 33
  created_at: '2026-09-02'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-29T12:20:48Z'
  updated_by:
    user: ''
    agent: resident_96d93d0d568b
importance: 7
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/test_framework/page/test_native_view.html
  - ${code_root}/management_portal/test_framework/page/test_page.js
  - ${code_root}/management_portal/test_framework/page/test_page_core.js
  - ${code_root}/management_portal/test_framework/page/test_page_overview.js
  - ${code_root}/management_portal/test_framework/page/test_page_catalog.js
  - ${code_root}/management_portal/test_framework/page/test_page_case.js
  - ${code_root}/management_portal/test_framework/page/test_page_run.js
  - ${code_root}/management_portal/test_framework/page/test_page_plan.js
  - ${code_root}/management_portal/test_framework/page/test_page_tau.js
  - ${code_root}/management_portal/test_framework/page/test_page.css
  - ${code_root}/management_portal/test_framework/page/test_live_adapter.js
  - ${code_root}/management_portal/test_framework/web.py
  - ${code_root}/management_portal/test_framework/service.py
  - ${code_root}/management_portal/test_framework/api.py
  - ${code_root}/management_portal/static/development_workspace.js
  - ${code_root}/management_portal/static/portal_design_live_testlab.js
  - ${code_root}/management_portal/static/portal_demo_player.js
  - ${code_root}/management_portal/static/portal_live_tour.js
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/resources/providers/test.py
  - ${code_root}/management_portal/static/portal_resource_chips.js
  related:
  - decision_8273271b67b1
  - decision_c953eab654cd
  - decision_6febe7bb57f9
  - decision_46c37026e8ea
  - decision_ef80c6d3b5ee
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
# Portal Test 页面：测试总览、用例目录、运行与 TAU

Test 页面是开发投影页中与 WALL 并列的一个视图，开发者和审查者在这里了解各模块的测试覆盖，查找和阅读用例，运行用例，阅读每次运行的证据，并安排 TAU 真实使用验收。它和项目页（decision_46c37026e8ea）的组织方式相同：总览先给出全局状况，再按地址进入用例目录、单个用例、新运行、单次运行、运行记录和 TAU 页；页面右侧是测试团队的对话。

测试条目、测试目录、运行计划和运行记录的数据与接口见 `decision_8273271b67b1`，TAU 的执行机制见 `decision_c953eab654cd`，发布前验证见 `decision_6febe7bb57f9`。

## 地址

| 地址 | 内容 |
| --- | --- |
| `/development?view=test` | 总览 |
| `/development?view=test&page=cases` | 用例目录；筛选写在同一地址中：`q`、`module`（可重复）、`group`、`importance`、`design`、`aspect`（可重复）、`live`、`feature`、`runner` |
| `/development?view=test&case=<用例 ID>` | 用例页 |
| `/development?view=test&page=plan` | 新运行 |
| `/development?view=test&run=<运行 ID>` | 运行页 |
| `/development?view=test&page=runs` | 运行记录 |
| `/development?view=test&page=tau` | TAU 页 |

地址是页面唯一的状态：打开链接、前进和后退都显示地址所指的内容。页面内的链接在原处切换并记入浏览器历史；改变目录筛选只替换当前地址，不增加历史记录。`/wall?case=<用例 ID>`、`/wall?run=<运行 ID>` 和 `/wall?view=test` 是旧地址，服务端把它们重定向到上表中对应的地址；Portal 正文中出现的 `testrun_…` 运行 ID 显示为资源引用，点击打开运行页。

每一页顶部是“总览、用例、运行、TAU”四个标签；用例页、运行页和新运行页另有面包屑，回到它们所属的列表。

## 总览

- **页头是一块紧凑的卡片。** 标题“Portal 测试”旁是执行方式是否可用（pytest、TAU）、TAU 是否暂停接收新测试和目录错误数；一句说明之后，最后一行并排列出用例数、有用例的模块数、P0 与 P1 数、设计点数、TAU 任务数、缺实现的用例数和最近运行的结果（通过、有问题、进行中）。右侧是“浏览用例”和“刷新”。
- **覆盖地图按领域画出每个功能模块。** 一个格子是一个二级模块：数字是用例数，色条是 P0–P3 的构成，底色随用例数（对数）加深；没有用例的格子画成虚线框，缺少 P0/P1 或缺少设计点的格子右上角有警示角，格子里写明缺什么；圆点是最近一次包含该模块用例的运行结果。点击格子打开按该模块筛选的用例目录，点击领域标题打开按整个领域筛选的目录。
- **最近运行列出最新的 8 次。** 每行显示结果、标题（只有一条用例时是它的标题，否则是用例数）、执行方式、所在模块、发起者和时间，点击打开运行页；全部记录在运行记录页。
- **待关注列出需要处理的缺口。** 声明有误而暂不在目录中的用例（写出 ID 和原因）、还没有实现的用例（链接到 `runner=none` 的目录）、没有用例的模块、缺少 P0/P1 的模块和缺少设计点的模块，模块都可以点击。没有缺口时说明目录没有需要处理的缺口。
<!-- wall: {"key": "8b09a05d067f51bd"} -->
- **快捷组合是三条按规则即时选出的用例。** “关键路径”是 P0 用例，“当前设计点”是设计点用例，“测试框架与 Test 页”是 `operations.test-framework` 与 `operations.test-workbench` 两个模块的用例；每条都是一个目录筛选，退役用例不计入。卡片显示用例数和涉及的模块数；“查看用例”打开带同样筛选的目录，“生成运行计划”通过 `GET /test/api/catalog/query`（`current=true`）取得同一批用例 ID 后打开新运行页；用例数为 0 时这个按钮不可用。组合不保存门禁、不登记版本，也不直接启动运行。
- **TAU 卡片概括真实使用验收。** 显示 TAU 任务数、涉及的模块数、最近 TAU 运行的结果和是否正在接收新测试，并链接到 TAU 页。

总览先显示浏览器已有的总览数据，再在后台读取最新的一份替换它；“刷新”重新读取总览和 TAU 准入状态。

## 用例目录

- **列表按模块排列。** 服务端按模块在目录中的顺序、再按重要等级和 `key` 排列，模块变化处插入模块标题（可点击，按该模块筛选）。每行有多选框、标题、`key`、测试方面，以及重要等级、设计点（已绑定 WALL 设计点时注明）、Live、执行方式（pytest、TAU 或“缺实现”）和非 active 的生命周期标记；点击标题打开用例页。
<!-- wall: {"key": "db41a312acfcbdd0"} -->
- **筛选由服务端计算，条件写在地址里。** 工具栏提供搜索（标题、说明、key、模块与实现入口）、功能模块（单个模块、整个领域，或由地址给出的多个模块）、重要等级、是否设计点、执行方式、所需功能、测试方面（多选，同一维度内按“或”）和 Live。不同维度按“且”组合；已退役的用例始终不显示。搜索在输入停顿 220 毫秒后才发出请求。每次读取 100 条，“再显示”继续读取下一批；页头写明匹配数和目录总数。“清除筛选”回到完整目录。
- **筛选结果只采用最新一次请求。** 条件一改变，先前的请求立即失效，迟到的响应不会覆盖新的结果；读取失败时显示原因和“重试”，不把旧列表或空列表当作当前结果。
- **多选生成运行计划。** 勾选的用例在当前标签页停留在 Test 期间保留；有筛选时可以“选择全部 N 条”（`GET /test/api/catalog/query`，`current=true`）；“生成运行计划”把选择交给新运行页。

## 用例页

- **页头说明这条用例检查什么。** 标题旁是重要等级、测试方面、设计点、Live 和执行方式标记，下面是说明与 `key`；“运行”用当前选中的实现生成运行计划，“复制链接”复制用例地址。页头最后一行是实现数、关联设计数、最近运行数和最近一次的结果。
<!-- wall: {"key": "faeb41830307a1bc"} -->
- **实现一节列出每个实现。** 每个实现显示执行方式、实现 ID、执行风险（未声明时用虚线标出）、运行配置、入口、源码文件、需要的团队准备和任务说明（默认折叠，标出字数）。有多个实现时，单选按钮决定“运行”使用哪一个，只改变页面上的选择，不写入任何数据；“查看源码”通过 `GET /test/api/source` 读取登记的源码，在对话框中打开并定位到入口所在的行；“用这个实现运行”直接以该实现生成运行计划。
- **定义一节是只读的。** 列出功能模块（可点击）、重要等级、测试方面、所需功能、生命周期和 WALL 设计点（一对一）的绑定：页面通过 `/api/wall/test-binding` 核对绑定是否有效、指向哪个设计点，有效时链接到墙面上的设计点。关联设计显示为资源引用。标题、说明、分类、设计点绑定和生命周期都写在测试源码的 docstring 或显式用例 JSON 中（字段写法见 `decision_8273271b67b1`），提交后目录随源码更新；退役一条用例就在源码中写 `:lifecycle: retired`。
<!-- wall: {"key": "c35cd4fac1dc8352"} -->
- **运行记录一节列出包含这条用例的保留运行。** 列表从最近运行中按运行计划的用例 ID 筛选，不论谁发起，最新的在前；结果和时间都来自运行记录，不写回用例。
<!-- wall: {"key": "b7cf3ad25909339b"} -->
- **Live 一节只在登记了演示时出现。** 显示演示标题、方式（真实页面或沙盒）和步数，“播放”从 `GET /test/api/demo` 读取登记的定义后开始只读演示。演示帮助读者理解测试步骤，不产生运行结果。
<!-- wall: {"key": "33bc1370edb38533"} -->
- **用例由地址决定。** 打开用例页是一次地址变化，浏览器后退回到原来的页面；地址中的用例 ID 不存在时显示读取失败和回到总览的入口，不改为显示其他用例。

用例的完整定义只在打开用例页时读取（`GET /test/api/catalog/case`），同一条用例在页面上只读一次。

## 新运行

- **新运行页把选中的用例变成运行计划。** 选择来自用例目录的勾选、用例页的“运行”、快捷组合、TAU 页或运行页的“再次运行”；它们保存在当前浏览器标签页中（用例 ID、实现限定和来源说明），刷新后仍在，其他标签页和其他用户看不到。
- **打开就生成计划，开始运行要再确认。** 页面立即调用 `POST /test/api/preview` 并显示：能否运行、用例数、实现数、不会运行的条数和执行方式（TAU 写明模块数和同时执行的上限）；“将要运行”列出每个实现的用例、执行方式、运行配置、执行风险与环境风险和入口（先显示 40 个，可展开全部）；“不会运行”列出被排除的用例和原因。“运行方式”可以改用某一种执行方式或某个运行配置，改动后重新生成计划。
- **只有“开始运行”启动运行。** 计划可运行且目录响应的 `access.can_operate` 允许时按钮可用；它把计划中的请求提交给 `POST /test/api/run`，随后把地址替换为新运行的运行页，并清空这份选择。

## 运行页与运行记录

- **运行页的页头说明这次运行的结果。** 状态、标题、执行方式（TAU Campaign 或本地运行）、运行配置和目标实例，发起者、发起时间与用时，以及运行 ID。“中止”只对发起者、只在运行未结束时出现；“再次运行”用同一批用例和实现限定打开新运行页；“删除”只对已结束的运行出现。页头最后一行是用例数、通过数、有问题的数、进行中的数和分组（或模块）数。
- **结果按分组排列，有问题的在前。** 本地运行按 pytest 文件分片分组（如 `pytest 1/3`），TAU Campaign 每个模块一组。分组显示状态、结果计数、正在运行的用例或等待原因和错误；每条结果显示状态、用例（可点击）、失败分类、摘要、问题或观察与改进项、截图审查、证据（折叠）以及体验记录、源码与 SPEC 诊断和其他元数据（折叠）。TAU 结果有“查看对话”，在右侧打开执行这条用例的模块 Resident 的对话。TAU 运行另写明模块间的并发上限和各失败分类的数量。
- **运行中每 900 毫秒重新读取。** 读取失败时保留已显示的记录并提示，1.5 秒后重试；运行结束后停止。切换到另一个运行或离开运行页后，旧运行的迟到响应不会覆盖当前显示。重新读取时保留读者展开或收起的部分。是否通过只看运行记录的状态，不从输出文字推断。
- **原始输出原样保留。** Runner 原始输出用 `textContent` 写入，保留开头换行、CRLF 和字面 HTML；没有输出时，已结束的运行写明没有输出（有错误时显示错误），进行中的运行显示等待输出。失败的本地运行默认展开原始输出。运行计划（选中的实现与排除原因）折叠在页尾。
- **打开已不存在的运行时说明报告已不存在。** 页面提供查看其他运行的入口，不改为显示其他运行，也不启动新运行。
<!-- wall: {"key": "6822717bb2f93fa4"} -->
- **删除运行报告需要明确确认。** 运行页可以删除当前报告，运行记录页可以勾选多份后一起删除；正在执行的运行不能删除。确认后调用 `POST /test/api/runs/delete`，总览、运行记录和用例页随之移除这些报告；产物清理失败时显示服务端返回的警告。删除报告不影响用例、Design 或 TAU Resident。
<!-- wall: {"key": "0373e18a0ac45a6d"} -->
- **运行记录列出服务端保留的全部运行。** 不按发起者筛选，最新的在前；可以只看有问题、通过或进行中的运行，也可以只看 TAU 或本地运行。打开已结束的运行只读取它的记录，不启动新运行。

## TAU 页

- **页头说明 TAU 的工作方式和当前能否接收测试。** 每个功能模块有一个负责的 Resident，它在受测的 Portal 上像真实用户一样完成本模块的任务，写下体验、问题和证据；不同模块同时进行，同一模块的任务依次执行。页头显示执行方式是否可用（不可用时写明原因）、任务数、模块数和最近 TAU 运行的结果，并提供“接收新测试”开关：开关读写 `/test/api/tau/admission`，只有准入的管理者可以操作，暂停后已经开始的运行继续进行。
- **每个模块一张卡片。** 卡片显示模块与领域名称、任务数、负责的 Resident（可打开，并写明执行账号路由和更换时间；还没有时写明首次运行时创建）和本模块的任务：每个任务有多选框、标题（可打开用例页）、重要等级与测试方面，以及最近一次包含它的运行的结果。
<!-- wall: {"key": "8b70ca06d257a93c"} -->
- **更换模块 Resident 先确认。** “更换”调用 `/test/api/tau/resident/rotate`，成功后卡片显示新的执行身份，下一次运行使用它；失败时显示实际原因，不预先显示更换成功。“对话”在右侧打开与该模块 Resident 的对话。旧成员释放、忙碌保护和历史记录边界见 `decision_c953eab654cd`。
<!-- wall: {"key": "095d448419037092"} -->
- **任务选择与运行按钮反映真实能力。** 可以逐个勾选、整组选择或全选；“运行”只在有操作权限、TAU 可用、正在接收新测试且已有选择时可用，按钮提示缺少哪一项。TAU 不可用时任务列表照常显示，并写明不可用原因。“运行”把选择以 TAU 执行方式和 `live.controlled` 运行配置交给新运行页。

## 测试团队对话

页面右侧是测试团队的对话，所有用户共用。对话对象是内置 `test` 工作台记录在 `bindings.main.residents.facade` 中绑定的门面 Resident（测试团队 Family 的门面），页面通过 `GET /api/workbenches/native?name=test` 读取；没有绑定门面时不显示对话。对话是普通的 Resident Chat，在 Test 的各页之间切换不重建；它可以收起成一条窄栏，收起状态按用户保存在 `PortalPageState("test-chat")`。在用例页或运行页发出的消息附带正在查看的用例或运行 ID。

TAU 页的“对话”和运行页的“查看对话”让模块 Resident 暂时接过这个位置；“回到测试团队”切回门面。

## Live 演示

- **演示定义来自目录。** `GET /test/api/demo` 返回登记的演示，用户不能输入任意地址；演示不创建运行记录，不代替真实的通过或失败。
- **真实页面模式在本地页面上逐步演示。** `mode=direct` 且声明只读的条目在登记的页面执行具名适配器步骤，显示“真实页面 · 只读保护”、步骤计数和说明，以及重播、下一步、暂停/继续和返回 Test 的控件；结束后回到演示前的地址、滚动位置和焦点。Test 页面自己的适配器 `test-lab` 通过地址打开目录、用例页和各个标签页；`design-testlab` 把真实的页面模块挂在独立的演示层中核对筛选、快捷组合、用例页、运行记录和 TAU 页，只发出读取请求。
<!-- wall: {"key": "3ea61b68cf659888"} -->
- **自动步骤中的写入被拦截。** 自动步骤里的网络写入、跨源读取和表单提交都被只读保护拦截，用户自己操作页面会暂停演示；对话框丢弃临时输入这类本地确认照常工作。这层保护不阻止页面自身的后台读取。
- **沙盒模式在隔离的框架中回放。** `mode=sandbox` 的条目在覆盖 Test 页面的演示层中播放：顶部是安全标签、步骤计数和关闭入口，中间的 iframe 设置 `sandbox=allow-scripts` 与 `referrerpolicy=no-referrer`，底部是说明、进度以及重新开始、下一步、播放/暂停。沙盒宿主只代理登记路由的 GET/HEAD 请求，校验允许和必需的查询参数并拒绝重复参数；消息同时校验来源窗口、条目、会话和协议范围；沙盒使用不透明源、内存存储和禁止连接与表单的内容安全策略。
- **跨页面演示以保存的演示会话为准。** 演示在多个页面之间推进时，目标页和返回页都以保存的会话为当前进度；演示代码只在演示时载入（`portal_live_tour.js` 及按适配器名载入的 `portal_design_live_*.js`）。

## 读取与速度

总览不带用例行：`GET /test/api/catalog?projection=overview` 返回目录摘要、每个模块的用例构成（总数、P0–P3、设计点、TAU、缺实现、Live）、目录错误、执行方式与运行配置、最近 100 次运行（附用例数、所在模块和单条用例的标题）、TAU 模块 Resident 和调用者的 `access`，约三十 KB。用例目录每次读取一页：`projection=page` 接受目录筛选与 `offset`、`limit`（最多 500），返回匹配总数和这一页用例的浏览字段，不带退役用例（请求指定生命周期时除外）。目录查询接受 `runner`（`none` 表示没有实现）和 `current=true`（排除退役用例）。2026-10-29 在 Dev 上测得总览从打开到画出约 0.36 秒，原先的面板工作台需要下载 3.8 MB 的完整目录，约 0.9 秒。

| 内容 | 接口 |
| --- | --- |
| 总览、模块统计、最近运行、TAU 模块 Resident | `GET /test/api/catalog?projection=overview` |
| 用例目录的一页 | `GET /test/api/catalog?projection=page&…` |
| 一批用例 ID（快捷组合、选择全部） | `GET /test/api/catalog/query?…&current=true` |
| 用例定义、源码、演示 | `GET /test/api/catalog/case`、`/test/api/source`、`/test/api/demo` |
| 运行计划、启动、读取、中止、删除 | `POST /test/api/preview`、`POST /test/api/run`、`GET /test/api/run`、`POST /test/api/run/cancel`、`POST /test/api/runs/delete` |
| TAU 准入与模块 Resident | `/test/api/tau/admission`、`POST /test/api/tau/resident/rotate` |
| 测试团队门面 | `GET /api/workbenches/native?name=test` |

## 权限、状态保存与恢复

目录响应的 `access.can_operate` 用于提示运行、删除报告和更换模块 Resident 是否可用；服务端认证、发起者取消限制和保留规则以 `decision_8273271b67b1` 为准，按钮状态不能代替服务端校验。能读取运行报告不代表可以控制模块团队，与模块 Resident 的对话仍按普通 Resident 权限检查。

| 状态 | 保存位置 | 生命周期 |
| --- | --- | --- |
| 当前页面、用例、运行和目录筛选 | 地址 | 随地址 |
| 对话收起 | `PortalPageState("test-chat")`，按用户 | 长期 |
| 目录的多选 | 页面内存 | 当前标签页停留在 Test 期间 |
| 新运行的选择 | 当前标签页的 sessionStorage | 开始运行或关闭标签页为止 |
| 用例的默认实现选择 | 页面内存 | 刷新后回到第一个实现 |
| 用例定义 | 测试源码（见 `decision_8273271b67b1`） | 随代码 |
| 运行记录 | 服务端（见 `decision_8273271b67b1`） | 按运行保留上限 |

从 Test 页面、命令行、Lineage 或授权工具启动的测试，只要经过测试框架登记运行，就出现在同一份运行记录中；任意 Resident 聊天记录不是正式测试记录。

## 页面组成

Test 视图由 Test 功能自己提供：`test_native_view.html` 保存视图模板（页面根节点和沙盒演示层）以及样式与脚本清单，Test 功能通过 `web.native_view` 接入点把它登记到开发投影页（`decision_15aab18c0ab3`）。用户第一次切换到 Test 时开发投影页才读取模板、载入脚本；`test_page.js` 用 `PortalDevelopmentWorkspace.registerView("test", …)` 登记挂载、显隐和销毁，之后每次进入 Test 都按当前地址重新显示对应的页。没有安装 Test 功能的 Portal 不在活动栏列出 Test，`/wall?view=test` 打开开发投影。

| 文件 | 职责 |
| --- | --- |
| `test_page.js` | 按地址显示各页、标签与浏览器历史、测试团队对话 |
| `test_page_core.js` | 测试框架接口、总览缓存、地址、新运行的选择和共用的显示片段 |
| `test_page_overview.js` | 总览：页头、覆盖地图、最近运行、待关注、快捷组合、TAU 卡片 |
| `test_page_catalog.js` | 用例目录 |
| `test_page_case.js` | 用例页与源码对话框 |
| `test_page_plan.js` | 新运行 |
| `test_page_run.js` | 运行页与运行记录 |
| `test_page_tau.js` | TAU 页 |
| `test_live_adapter.js` | `test-lab` 演示适配器与沙盒演示播放器 |
| `test_page.css` | 样式 |

## 失败与恢复

- 总览、目录、用例或运行读取失败时显示原因；用例或运行不存在时说明并提供回到总览或查看其他运行的入口。
- 目录筛选失败时保留筛选条件并提供重试。
- 生成计划或启动失败时保留新运行的选择，并显示服务端给出的原因。
- 更换模块 Resident、改变准入或删除报告失败时显示实际原因，页面保持原来的状态。
- 视图在挂载完成前被销毁时不再挂载；之后再次进入 Test 会重新挂载一次。
