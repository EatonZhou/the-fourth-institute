---
kb_id: decision_8273271b67b1
type: decision
project: AI员工
created_at: '2026-09-02'
version:
  number: 38
  created_at: "2026-09-02"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-10-21T18:08:47Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/test_framework/models.py
  - ${code_root}/management_portal/test_framework/taxonomy.py
  - ${code_root}/tests/cases/case_ids.json
  - ${code_root}/management_portal/test_framework/catalog_source.py
  - ${code_root}/management_portal/test_framework/catalog.py
  - ${code_root}/management_portal/test_framework/registry.py
  - ${code_root}/management_portal/test_framework/planner.py
  - ${code_root}/management_portal/test_framework/records.py
  - ${code_root}/management_portal/test_framework/service.py
  - ${code_root}/management_portal/test_framework/api.py
  - ${code_root}/management_portal/test_framework/paths.py
  - ${code_root}/management_portal/test_framework/runner_adapters.py
  - ${code_root}/management_portal/test_framework/executor.py
  - ${code_root}/management_portal/test_framework/tau_campaign.py
  - ${code_root}/management_portal/test_framework/local_runs.py
  - ${code_root}/management_portal/test_framework/slots.py
  - ${code_root}/management_portal/test_framework/pytest_results.py
  - ${code_root}/management_portal/test_framework/isolation.py
  - ${code_root}/management_portal/test_framework/live_adapter.py
  - ${code_root}/management_portal/test_framework/tools/sync_catalog.py
  - ${code_root}/management_portal/test_framework/catalog_metadata.py
  - ${code_root}/management_portal/test_framework/web.py
  - ${code_root}/management_portal/resources/providers/test.py
  - ${code_root}/management_portal/wall_backend.py
  - ${code_root}/config/resource-types/test.json
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_test_framework.py
  related:
  - decision_6febe7bb57f9
  - decision_f2a6a5dd4646
  - decision_c953eab654cd
  - decision_ef80c6d3b5ee
  - decision_06defc47e960
  - insight_ee85097d560b
  - insight_62b384b1e497
  supersedes: []
  derived_from:
  - thinking_091fdad228c4
---
# Portal Test Case、Catalog 与运行证据协议

Portal 的测试框架（Python 包 `management_portal/test_framework`）把仓库里的自动化测试登记为带说明的测试条目，支持按模块、重要级别和覆盖角度查找、计划、执行与保存证据。开发者、审查者、Agent、ResourceService 和 Test 工作区共用目录及运行记录。当前本地执行只运行 pytest；原 Playwright 浏览器套件已移除。TAU 声明式条目继续可运行；真实页面验证使用独立的可选 Playwright/Chromium 运行时，与已删除的本地浏览器测试套件分开。运行由独立执行进程承担，Web 重启不直接中断已有记录。

本 Design 定义测试条目和测试目录、运行计划、运行记录、pytest 本地执行及 TAU 声明式运行，也定义 `/test/api/*` 接口和权限。Test 工作区见 `decision_f2a6a5dd4646`；TAU 的原任务模型见 `decision_c953eab654cd`；开发者验证范围见 `decision_6febe7bb57f9`。

## 基本概念

- **Test Case（测试条目）**：一项稳定的测试意图，说明验证什么、属于 Portal 哪个功能模块、有多重要、从哪个角度覆盖。它有一个永久 ID，Python 函数改名或移动文件不改变这个 ID。
- **Implementation（实现入口）**：把测试意图连接到可运行的 pytest 函数，或保留的 TAU 任务声明。一个 Test Case 可以有零个或多个 Implementation；没有 Implementation 的条目只登记测试意图。
- **Catalog（测试目录）**：全部 Test Case 的集合。pytest 函数从源码自动发现，标题、说明和分类写在 docstring；TAU、SPEC Design 和设计点 Live 用例保存在各自 JSON。仓库不保存生成目录。
- **runner（执行方式）**：`pytest` 是当前可运行的本地执行方式；`tau` 执行 Resident 真实页面验收；运行前须安装独立 TAU 浏览器运行时并配置 portal-test 状态。`browser-script` 不再注册。
- **profile（运行配置）**：实现声明可以在哪种环境下运行。当前有 `local.fixture`（本地测试数据，风险 `isolated`）和 `live.portal-test`（在当前唯一的 Dev 上使用受控账号 `portal-test`，风险 `live-account`）。
- **RunPlan（运行计划）**：一次请求选中的具体 Implementation 列表，固定每个实现的入口、配置和源码摘要。
- **Run record（运行记录）**：一次执行的发起者、状态、输出、逐项结果、产物和报告。
- **功能模块**：`module` 字段的取值来自测试分类表（`management_portal/test_framework/taxonomy.py`），它按 SPEC.md 的十一个顶层领域分组，每个模块列出相关的 Design ID。
- **WALL 设计点绑定**：一个 Test Case 可以声明它专门验证 WALL 中的某一个设计点（`design_point_ref`，形如 `<来源>#<点>`），WALL 的设计点卡片据此显示对应的测试。
- **Live 演示**：Test Case 可以附带一段只读的步骤演示，帮助读者理解测试在页面上验证什么。

Test Case 的字段：

| 字段 | 含义 |
| --- | --- |
| `id` | `test_` 加 12 位小写十六进制数，全局唯一；规则见“测试目录” |
| `key` | 供人搜索和引用的短名，即首个实现的入口 |
| `title`、`description` | 标题和自然语言说明，取自测试的 docstring；测试没有 docstring 时标题是测试名，说明为空 |
| `importance` | `P0`（最高）、`P1`、`P2`、`P3` |
| `module` | 功能模块 ID；接口另外返回 `module_path`，包含顶层领域与模块的 ID 和显示名 |
| `aspects` | 覆盖角度，非空：`functional` 功能、`ui` 界面、`behavior` 行为、`performance` 性能、`architecture-conformance` 架构约束 |
| `is_design_point` | 是否是当前关键设计点的测试 |
| `design_refs` | 该测试直接验证的 Design ID 列表 |
| `design_point_ref` | 可选；绑定的 WALL 设计点 |
| `lifecycle` | `active`、`needs-review`、`obsolete`、`retired`（已退役） |
| `implementations` | 实现入口列表 |
| `live` | 可选；Live 演示定义 |

Implementation 的字段：

| 字段 | 含义 |
| --- | --- |
| `id` | 实现 ID，形如 `impl-` 加 16 位十六进制数，目录内唯一 |
| `runner` | `pytest` 或 `tau`；TAU 依赖可选浏览器运行时 |
| `entrypoint` | 入口，例如 `tests/cases/test_x.py::test_y`；目录内唯一 |
| `source` | 源码文件；运行和查看源码时按入口解析当前声明行，目录不保存行号。JSON 定义的源码预览从首行开始 |
| `profiles` | 可以使用的运行配置 |
| `risk` | 执行风险：`no-external-side-effects`、`external-side-effects` 或 `unspecified`（未声明时的取值） |
| `task_prompt` | 仅 `tau`：交给 Agent 的任务说明 |
| `lineage_fixture` | 仅声明了 `external-side-effects` 的 `tau` 实现：需要独立临时团队的理由（见 `decision_c953eab654cd`） |

## 设计点

### 测试目录

- **每个 pytest 测试自动成为 Catalog 中的 Test Case。** `tests/cases/test_*.py` 的顶层 `test_` 函数和 `Test*` 类（或 `unittest.TestCase` 子类）的方法由 `catalog_source.py` 在加载时发现；pytest fixture 不生成用例。显式 JSON 声明的入口归相应用例所有。
- **测试的 docstring 是目录条目。** 第一段为标题，其余为说明；末尾可写 `:module:`、`:importance:`、`:aspects:`、`:design:`、`:design-point:`、`:lifecycle:`、`:risk:`、`:profiles:`。文件 docstring 提供默认值，测试自身字段覆盖默认值，`:design:` 合并；未声明的字段由 `catalog_metadata.py` 推断。例如：

  ```python
  def test_arrow_history_recalls_own_inputs():
      """输入历史只取当前登录者自己发的消息

      共享会话里按上箭头只取回自己的输入。

      :importance: P1
      :design: decision_f560803947c6
      """
  ```
- **保留的用例 ID 稳定，新测试的 ID 由入口派生。** 仍存在的旧 pytest 入口在 `tests/cases/case_ids.json` 中保留 ID；已删除的浏览器入口从该映射中清除。其他测试 ID 由入口摘要派生。测试移动、改名和显式 JSON 入口的冲突仍由目录同步校验。
- **`sync_catalog` 只做检查和清理。** `python -m management_portal.test_framework.tools.sync_catalog` 报告被拒绝的字段和无法解析的源码、测试已经不存在的 `case_ids.json` 条目、按移动规则沿用的 ID，以及测试已经不存在的 `live_cases.json` Live 定义；有错误时以状态 1 退出。它还报告两类需要人处理、但不影响退出状态的问题（weihao 2026-10-18 定）：标题不含中文的测试（没有 docstring 标题，目录显示函数名），以及设计点已不在设计文档中的绑定。WALL 用设计点标签文字的哈希作点 ID，改写标签就会让绑定失效，除非该点带有 `<!-- wall: {"key": ...} -->` 显式 key。工具按 `<Projects>/<项目>/knowledge_base/<类型>/*__<ID>.md` 读取设计文档，读不到的绑定只计入“未检查”。`--apply` 把沿用的 ID 写入新入口并删除失效条目（在各文件的 `.lock` 文件锁内原子替换）；`--export <路径>` 把完整目录写成一份 `tfi.portal-test-catalog.v2` 文档，供留存证据。`live_cases.json` 继续按入口为自动发现的测试提供 Live 定义。
- **SPEC.md 索引的每份 Design 都要出现在测试分类表中。** `tests/cases/test_test_framework.py` 检查分类表覆盖 SPEC.md 的全部顶层领域和当前索引的每个 Design ID，并检查每个条目的 `module` 都来自分类表；在 SPEC.md 新增或移动 Design 时，同时把它的 ID 加入对应模块，否则该检查失败。
- **目录校验在读取时进行。** 每个条目必须有合法 ID、`key`、标题和能识别的模块，说明可以为空；`is_design_point` 为真或含 `architecture-conformance` 的条目必须有非空 `design_refs`（含 `architecture-conformance` 而没有声明时默认为 SPEC.md）；Test Case 的 `id`、`key` 以及 Implementation 的 `id`、`entrypoint` 在目录内唯一。模块 ID 必须来自当前分类表；旧模块名没有别名映射。`tests/cases/test_catalog_from_code.py` 检查仓库目录没有错误。
- **单个条目有错误时，其余条目照常可用。** docstring 中被拒绝的字段（未知字段、未知模块、无效等级）记入 `catalog_errors`，这个测试仍按其余字段和推断值留在目录中；无法解析的源码文件记一条错误，它的测试暂时不出现；重复的 ID、入口或设计点绑定使相关条目进入 `catalog_errors`，其他条目正常返回。
<!-- wall: {"key": "ae08985efa54f238"} -->
- **目录按源码文件缓存。** 每个测试文件按修改时间和大小缓存解析结果，任一来源文件变化时重建目录；首次构建需要解析全部测试文件，之后的读取只检查文件状态。ResourceService 的 `test` 资源按同一组来源文件判断是否需要刷新。
- **条目只在源码中修改（weihao 2026-10-16 定）。** Test 工作区和接口都不能修改条目：标题、说明、分类、WALL 设计点绑定和 `lifecycle` 写在测试的 docstring 或显式用例 JSON 中，随代码评审和提交生效。退役一个条目就是在 docstring 中写 `:lifecycle: retired`。
- **一个 WALL 设计点最多绑定一个 Test Case。** docstring 字段 `:design-point: source#point` 设置绑定，同时把条目标记为设计点并把来源文档加入 `design_refs`；显式用例 JSON 设置 `design_point_ref` 时必须自己写 `is_design_point: true` 和来源文档。两个条目绑定同一个点时，两者都进入 `catalog_errors`。Test Case 面板通过 `/api/wall/test-binding` 显示绑定的点在当前用户可访问的 WALL 工程中是否仍然存在。
- **Test Case 也是只读的标准资源。** 资源类型 `test` 的提供者把 Catalog 中的条目发布给 ResourceService，所有人可读；资源地址在 Portal 中打开为 `/wall?case=<id>`。`test` 类型没有修改提供者，`UpdateResource` 对它返回错误。

### 查询与运行计划

- **筛选条件之间取交集，覆盖角度之内取并集。** 可以按条目 ID、模块、重要级别、覆盖角度、是否设计点、是否有 Live 演示、是否已绑定设计点、绑定的设计点、生命周期和搜索词筛选。搜索词不区分大小写，匹配 ID、标题、说明、模块、`key` 和实现入口。重要级别或覆盖角度取值不合法时返回错误；多选条件只有以列表形式传入才生效。
- **预览计划只说明“现在会运行什么”，不执行任何测试。** 对每个命中的条目，计划选出与请求的执行方式、实现 ID 和运行配置都匹配、且执行方式当前可用的全部实现，并记录每个实现源码文件的 SHA-256 摘要；没有可运行实现的条目列入 `excluded`。
- **声明式 TAU 的历史计划保持可读。** 目录保留其声明和源码摘要以解释既有记录；满足 TAU 运行前提时，新计划可以继续选择它执行。pytest 计划按源码文件摘要校验。
- **未指定条目 ID 时，计划默认排除已退役的条目。** 明确指定条目 ID 时仍可运行已退役条目，用于诊断。
- **启动运行时，服务重新生成计划。** 客户端提交的是选择条件而不是预览结果；预览和启动之间源码或目录如有变化，以运行记录中保存的计划为准。
<!-- wall: {"key": "10a3f2f371ff82d7"} -->
- **纯 TAU 计划形成 Campaign，本地计划只执行 pytest。** 当选中的可运行实现全部是 `tau` 时，计划按模块组织为 TAU Campaign，执行资格及恢复见 `decision_c953eab654cd`；本地与 TAU 混合选择时只保留本地实现，只有 TAU 实现的条目列入 `excluded`，原因为 `tau-campaign-only`。历史 TAU 计划与结果仍可读取。
- **本地运行按 pytest 文件分片，默认失败后继续。** 分片按文件大小均衡，组内单进程执行，组间并发。请求 `fail_fast: true` 时停止其他组；每组入队和取得名额后都核对源码摘要与声明，等待期间变化的实现记为 `error`。
- **运行结果只记录执行事实。** 运行记录不修改 Test Case，也不修改 Design；一次通过不代表发布已被批准。

### 运行记录与权限

运行记录的索引与详情由同一持久化 owner 管理。Web 进程和独立执行器并发创建、追加结果或修改状态时，对完整读改写持有跨进程文件锁；读者在同一锁下取得一致视图。每次替换文件使用独立临时文件并原子提交，记录权限为 0600，避免并发运行相互覆盖。锁仅覆盖状态读写，不覆盖用例执行或等待。


- **所有登录用户都能查看测试目录、运行记录和演示，并能预览计划。**
<!-- wall: {"key": "8a4fde8f7ea1acf3"} -->
- **测试操作沿用 Web 的会话或 Resident 凭证认证。** `/test/api/` 在认证成功后提供运行、报告删除和模块管理入口，当前不另设 `test_lab` 系统能力检查。此入口不绕过运行发起者约束、活动状态校验或后续业务资源的正式授权；通用认证与权限规则见 `decision_b820faf42218`。
<!-- wall: {"key": "05144a9e88f5a7cc"} -->
- **只有运行的发起者可以取消运行。** 服务端比较认证身份与 `created_by`；已结束的运行原样返回，未结束的运行进入取消流程。取消请求不等于被测输入已终止。
<!-- wall: {"key": "a215e09fc162cad9"} -->
- **服务保留全部未结束的运行和最近 100 条已结束报告。** 创建新运行时只淘汰超出上限的最早终止报告；已通过接口认证的调用者也可以批量删除已经结束的报告。
- **一次运行属于执行它的进程。** 本地运行和 TAU Campaign 都由各自的测试执行进程执行，Web 重启不影响它们。执行进程在运行结束前退出时，Web 在启动时或下一次读取这条运行时重新启动执行单元：已经开始的本地运行记为 `error`，TAU Campaign 由新的执行进程从已记录的 Resident 消息继续。仍在运行的执行进程继续拥有自己的运行，其他进程不抢占。

## 接口协议

所有接口都要求有效的 Portal 登录或 Resident 凭证，未认证返回 `401`。写接口还受 Portal 的同源和请求大小保护。页面通过目录响应中的 `access.actor` 和 `access.can_operate` 决定按钮状态；安全边界是服务端的检查。

| 接口 | 权限 | 作用 |
| --- | --- | --- |
| `GET /test/api/catalog` | 登录 | 目录与摘要；`projection=workspace` 时返回精简版本 |
| `GET /test/api/catalog/query` | 登录 | 只返回匹配的条目 ID |
| `GET /test/api/catalog/case?case_id=` | 登录 | 单个条目的完整定义 |
| `GET /test/api/source?case_id=&implementation_id=` | 登录 | 实现入口的源码文本 |
| `GET /test/api/demo?case_id=`、`GET /test/api/demo-frame` | 登录 | Live 演示定义和沙盒演示页面 |
| `GET /test/api/run?run_id=` | 登录 | 一条运行记录 |
| `GET /test/api/tau/residents?module=` | 登录 | TAU 模块 Resident 状态（见 `decision_c953eab654cd`） |
| `POST /test/api/preview` | 登录 | 预览运行计划 |
| `POST /test/api/run` | 已通过接口认证 | 启动运行 |
| `POST /test/api/run/cancel` | 运行发起者 | 取消运行 |
| `POST /test/api/runs/delete` | 已通过接口认证 | 删除已结束的报告 |
| `POST /test/api/tau/resident/rotate`、`/configure` | 已通过接口认证 | 轮换或配置 TAU 模块 Resident（见 `decision_c953eab654cd`） |

错误的通用含义：条目、实现或运行不存在返回 `404`，权限不足返回 `403`，输入无效（包括目录校验错误）返回 `400`，其他异常返回 `500`。

### 目录查询

`GET /test/api/catalog` 的查询参数为：`search`（单值）；可重复的 `importance`、`module`、`aspect`、`lifecycle`；只识别字面 `true` 或 `false` 的 `is_design_point`、`has_live` 和 `has_design_point_ref`；单值 `design_point_ref`。响应 schema 为 `tfi.portal-test-framework.v2`，包含：

- `summary`：命中条目的总数、设计点数、没有实现的数量、待检查数量和按重要级别的计数；
- `catalog_errors`：格式错误或重复的条目及原因；
- `filters`：可选的重要级别、分类树和覆盖角度；
- `module_counts`：每个模块的条目数；
- `capabilities`：pytest 和 TAU 两种登记方式及当前可用状态、两种运行配置；
- `cases`：命中的完整条目；
- `recent_runs`：保留的全部运行摘要，按新到旧；
- `tau_residents`：有条目的模块的 TAU Resident 投影；
- `access`：当前用户及是否可以操作。

`projection=workspace` 时 schema 为 `tfi.portal-test-workspace-catalog.v1`，`cases` 中每条只含浏览所需的字段（ID、`key`、标题、重要级别、模块、覆盖角度、设计点、绑定、生命周期、执行方式列表和 Live 定义），完整定义通过 `catalog/case` 按需读取。`GET /test/api/catalog/query` 使用相同参数，返回 `tfi.portal-test-catalog-query.v1`、`case_ids` 和 `count`。

`GET /test/api/source` 只读取目录中登记的实现源码，文件必须位于仓库内，返回路径、内容、起始行和入口。

格式标签的新写入使用上述 `tfi.*`。具有严格 schema 判定的读取入口只接受精确的当前格式名与版本，由 `management_common/format_tags.py#schema_matches` 统一判断；错版本、额外空白或其他前缀均不接受。格式校验不改变其他字段校验、摘要、身份或幂等语义，不因读取而写回或批量改写历史记录。

### 预览与启动

`POST /test/api/preview` 的请求体：

```json
{
  "select": {"case_ids": [], "modules": [], "importance": [], "aspects": [], "lifecycle": [], "search": "", "is_design_point": true},
  "implementation": {"runners": [], "implementation_ids": [], "profile": ""}
}
```

纯 TAU 运行的 `implementation` 还可以带 `max_parallel_modules`、`resident_account_id` 和 `resident_runtime`（见 `decision_c953eab654cd`）；在其他运行中使用这些字段返回 `400`。本地运行的 `implementation` 可以带布尔值 `fail_fast`（默认 `false`）；用于 TAU Campaign 或取值不是布尔值时返回 `400`。成功响应 schema 为 `tfi.portal-test-run-plan.v2`：

| 字段 | 含义 |
| --- | --- |
| `plan_id` | `plan-` 加请求与结果内容摘要的前 16 位 |
| `request` | 规范化后的请求（含默认的生命周期筛选） |
| `selected` | 每个将要执行的实现：条目信息、实现定义、执行方式、运行配置、实现风险 `risk` 与配置风险 `profile_risk`、`source_sha256` |
| `excluded` | 命中但没有可运行实现的条目及原因 |
| `selected_case_count`、`selected_implementation_count` | 选中的条目数和实现数 |
| `execution` | 执行方式：`batch`（本地运行；每个分组有 `group_id`、`label`、`runner`、`lane`（`isolated` 或 `live`）、`slot`、`item_indexes` 和 `continue_on_failure`），或 `parallel-modules`（TAU，每模块一组、失败继续，附并发上限） |
| `campaign` | 纯 TAU 运行为 `tau`，否则为空 |
| `executable` | 是否至少有一个可运行的实现 |

`POST /test/api/run` 接受同样的请求体（兼容写法：只给 `case_id` 时等同于只选这一条），计划不可执行时返回 `400`，成功时返回新建的运行记录。运行 ID 为 `testrun_` 加 16 位小写十六进制数。接口没有幂等键，重复提交会创建多个运行。

### 运行记录

运行记录的状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 已记录，等待执行 |
| `running` | 正在执行某个实现 |
| `cancelling` | 已收到取消请求，等待当前执行结束 |
| `passed` | 计划中的实现全部通过 |
| `failed` | 至少一个实现给出失败结论 |
| `cancelled` | 已取消 |
| `error` | 框架、源码摘要、执行器或证据处理出错 |
| `unsupported` | 当前环境无法提供计划要求的执行方式，或用例的运行前提没有满足、用例没有执行 |

最终状态按 `error`、`unsupported`、`failed`、`cancelled`、`passed` 的优先顺序从各项结果中取最严重的一项。记录还包含 `created_by`（发起者）、`executor`（执行进程的身份）、`plan`、开始和结束时间、当前执行的条目、聚合输出、逐项 `results`、按组的 `groups`（本地运行每个 pytest 分片一组，TAU 每个模块一组）和 `report`（TAU 报告）。本地运行的分组还记录 `waiting`（等待主机并发名额时的说明）、`slots`（取得的名额数）和 `isolation_holes`（隔离漏洞，见下文）。

未通过的每项结果带 `failure_class`，说明未通过的原因：`product`（产品缺陷）、`test`（测试问题）、`environment`（环境问题）、`precondition`（运行前提没有满足，用例没有执行）、`concurrent-change`（并行修改）或 `unknown`（尚未判定）。框架能判断的直接写入：`unsupported` 为 `precondition`，`error` 和被信号终止的进程为 `environment`，TAU Resident 没有交付可解析的结论为 `test`；其余失败记为 `unknown`，由处理失败的人或 Resident 判定。TAU 报告的摘要按分类计数（`failure_classes`），Test 工作区在结果状态旁显示分类。

`POST /test/api/run/cancel` 接受 `run_id`：已结束的运行原样返回；否则记录进入 `cancelling`。本地运行的执行进程在下一次写入进度时（至多约 2 秒）看到这个状态，向各组进程发送 `SIGTERM`，10 秒后仍未退出时发送 `SIGKILL`；TAU Campaign 的执行进程在等待 Resident 时读到这个状态，通知正在执行的 Resident 输入停止。执行结束后写入最终状态。`POST /test/api/runs/delete` 接受 `run_ids` 列表：去重后，只要有一个 ID 不存在或运行尚未结束，整批拒绝；成功时删除记录并清理各运行的产物目录，返回 `tfi.portal-test-run-deletion.v1`、`deleted_run_ids`、`deleted_count` 和 `artifact_warnings`（产物清理失败的说明）。

## 实现说明

### 状态保存位置

测试框架的实例状态默认保存在当前运行用户的 `~/.local/state/tfi-portal/test-framework/<实例名>/`，由 `paths.py#state_root` 解析；`PORTAL_TEST_FRAMEWORK_STATE_ROOT` 显式配置优先并展开 `~`。不固定实验室用户名，也不因格式标签升级自动迁移已有状态路径：

| 文件 | 内容 |
| --- | --- |
| `tau-residents.json` | TAU 模块与 Resident 的绑定 |
| `runs.json` | 运行索引（schema `tfi.portal-test-run-index.v1`），每条运行一行摘要 |
| `runs/<run_id>.json` | 每条运行的完整记录 |
| `run-artifacts/<run_id>/` | 本地运行每组的完整输出和 `results.jsonl` 测试事件；历史 TAU 用例的证据 |

主机并发名额默认保存在当前运行用户的 `~/.local/state/tfi-portal/test-slots/`（`PORTAL_TEST_SLOT_ROOT` 显式配置优先）。同一部署中需要共享名额的 Dev、Release 执行进程应解析到同一路径。

记录文件权限为 `0600`，目录为 `0700`。运行列表只读取索引中的摘要，打开详情时才读取完整记录；一次运行的输出或状态变化只重写它自己的记录和索引中的一行，其他运行不受影响。聚合输出只保留最后 180000 个字符。

### 测试执行进程

Web 进程创建运行记录（`queued`）后，用 `systemd-run --user` 启动一个临时单元 `portal-testrun-<16 位十六进制>`，在 Web 所在代码目录中运行 `python -m management_portal.test_framework.executor --state-root <状态目录> <run_id>`。单元位于 Web 的 cgroup 之外，CPU 与 IO 权重为 20（默认 100）、nice 为 10。单元名称按运行 ID 确定，同一运行在同一时间只能有一个执行单元。启动失败时运行直接记为 `error`。

本地运行的单元最长运行 8 小时，只接收状态目录、实例名、`PATH`、portal-test 存储状态路径和名额目录这几个环境变量，不接收数据库地址或其他密钥。TAU Campaign 的单元不设运行时长上限，接收发起进程中全部不含密钥的 `PORTAL_*` 变量：服务套接字、ISA 套接字路由、SocietyService 与 ResourceService 的客户端模式、NATS 地址、实例目录和 TAU 测试设置，使它与 Web 用同一方式调用 Resident；数据库地址由单元从 PostgreSQL 环境文件（`PORTAL_POSTGRES_ENV_FILE`，默认 `~/.config/tfi/portal-postgres.env`）读取，不出现在命令行中。

执行进程先在运行记录的锁内认领运行并写入自己的进程身份：`queued` 改为 `running`；尚未开始就被取消的运行直接记为 `cancelled`；已有存活执行进程或已经结束的运行不执行。原执行进程已经退出时，TAU Campaign 由本进程接管，保持 `running` 或 `cancelling`；已经开始的本地运行无法继续，记为 `error`。TAU Campaign 随后按模块执行（见 `decision_c953eab654cd`）；本地运行为计划中的每组启动一个线程，各组并发执行。组内进程的输出、逐项结果和分组状态先在内存中累积，每 2 秒合并成一次记录写入；同一次写入读回的状态是 `cancelling` 时开始停止。全部组结束后，执行进程按各组状态写入最终状态。

pytest 和保留的 TAU 任务共用主机并发预算，默认 4 个名额，可用 `PORTAL_TEST_MAX_CONCURRENCY` 设为 1–32。pytest 分片每组 1 个；Live 组另受全主机 1 组限制。名额由文件锁持有，进程退出后释放。无空闲名额时分组保持 `queued`。

### 执行方式

| 执行方式 | 执行内容 | 结果判定 |
| --- | --- | --- |
| `pytest` | 每个分片运行一次 `python -m pytest -q --tb=short -p no:cacheprovider -p management_portal.test_framework.pytest_results --continue-on-collection-errors --basetemp=/tmp/pytest @<入口列表文件>`；插件 `pytest_results` 把每个测试的开始、各阶段结果和收集错误逐行写入组目录的 `results.jsonl`，普通组的临时目录位于该组 bwrap 私有 `/tmp`，组结束后随隔离环境销毁；短路径避免 Unix socket 地址超长。Live pytest 使用 pytest 自己分配的唯一临时目录，保留当前用户的 `XDG_RUNTIME_DIR` 与 `DBUS_SESSION_BUS_ADDRESS`，供明确声明的 systemd 用户单元测试使用。报告插件在加载时保存文件打开函数，测试对文件访问的临时替换不能中断报告写入 | 入口匹配的全部测试（含参数化实例和类内方法）都通过即 `passed`；任一阶段失败即 `failed`，失败在分片运行中就写入记录；全部跳过为 `unsupported`；文件无法收集为 `failed` |
| `tau` | 由模块 Resident 执行真实使用任务（见 `decision_c953eab654cd`） | Resident 返回的机器可读结论 |

进程退出后仍没有结果的实现：已有取消请求时记为 `cancelled`（失败即停时摘要写明“失败即停”）；进程被信号终止时记为 `error`，摘要写明信号名称和编号（无法识别的编号写为“未知信号”）；其他情况记为 `error`，摘要写明退出码。负的退出码只说明进程收到了信号，不能据此判断是谁发送的；排查时需要把运行时间、执行单元名与服务重启日志对照。框架不自动重试。

### 隔离

`isolated` pytest 组在 bwrap 沙箱运行：独立 PID、IPC 和 UTS 命名空间，私有 `/tmp`，无网络，隐藏凭证和 Portal 状态。数据库与状态隔离由测试配置完成；Live 组不进沙箱。

Portal 的真实状态目录（当前运行用户的 `~/.local/state/tfi-portal` 和当前测试框架状态目录）在沙箱中被替换为组目录旁的空白目录，只有本组自己的产物目录重新挂回。组结束后，替换目录中出现的文件说明有测试写入了真实状态：这些路径记入分组的 `isolation_holes`，分组和运行至少记为 `failed`，替换目录保留供排查；没有写入时替换目录随即删除。

`live` 组不进沙箱，在当前唯一的 DEV 上以 portal-test 身份运行，同一时间全主机只有一个 Live 组执行。执行进程本身也不进沙箱，它负责写运行记录。TAU Campaign 不进沙箱，它的用例由模块 Resident 在当前真实 DEV 上执行。

### 进程重启后的恢复

每条运行记录保存执行进程的身份 `executor`（主机、启动 ID、PID 命名空间、PID 和进程启动时刻；排队中的运行保存创建它的 Web 进程）。Web 启动时检查所有未结束的运行，读取单条未结束的运行时也检查它；执行进程仍然存活的保持不变。执行进程已退出时：已经开始的本地运行记为 `error`（“测试执行进程在运行完成前退出。”）；其余运行（TAU Campaign 和创建后还没有执行进程认领的运行）由 Web 重新启动执行单元，同一运行每 30 秒至多重启一次，启动失败留待下一次读取重试。PID 命名空间与当前进程不同的身份无法判断，按存活处理；构建测试框架服务本身不做任何恢复。TAU 接管原调用、恢复准备失败但仍有有效调用、取消收尾与提交前后源码摘要的专有规则统一见 `decision_c953eab654cd`“执行与恢复”；不得因重新启动执行单元重复投递业务。

### Live 演示定义

条目的 `live` 定义有两种模式：`direct` 在登记的本地真实页面执行具名适配器步骤，必须声明 `risk: "read-only"`、`adapter`、`adapter_version: 1` 和非空的 `direct_steps`（每步有 `capability`）；`sandbox` 在隔离的沙盒页面中回放 `steps`（每步有 `action`）。两种模式的 `target` 都必须是站内路径。`GET /test/api/demo` 返回登记的演示定义，未知条目返回 `404`，没有演示或定义无效返回 `400`；`GET /test/api/demo-frame` 返回沙盒页面，并附带禁止外部连接、表单和子框架的内容安全策略。演示的页面呈现见 `decision_f2a6a5dd4646`。

### 一次测试的流程

```text
测试源码（docstring）+ 显式用例 JSON + case_ids.json
  → 构建目录并校验（错误条目进入 catalog_errors）
  → 查询与筛选
  → 预览：选出可运行的实现并记录源码摘要
  → 启动：重新生成计划，创建运行记录（queued）
  → 本地运行：启动测试执行单元 → 认领运行 → 各组取得主机名额后并发执行（isolated 组在沙箱中）
    TAU Campaign：启动测试执行单元 → 认领运行 → 按模块并发、组内逐项执行（不进沙箱，作用于当前 DEV）
  → 核对源码摘要；结果、输出与产物写入运行记录（本地运行每 2 秒合并写入一次）
  → 最终状态；发起者可在此前取消
```

测试源码和显式用例保存长期的测试意图，运行计划保存本次选择和源码身份，运行记录保存执行历史。三者分开保存，所以修改条目说明不会改写旧运行，源码变化不会悄悄套用旧计划，一次失败也不会改变 Design。

测试用例面板的分区展开状态与用户偏好由 `decision_f2a6a5dd4646`“Test Case：理解一个条目”定义；运行记录不承担界面偏好存储。

TAU 模块的跨进程占用、容量等待及失败清理恢复统一见 `decision_c953eab654cd`；本 Design 只定义通用运行记录与保留上限，未结束 Campaign 同其他未结束运行一样不会被创建新运行时的保留清理淘汰。

原 Playwright 界面套件与固定浏览器 Campaign 检查已退役，不再作为当前覆盖证明。当前 TAU 样本、临时对象与证据生命周期以 `decision_c953eab654cd` 为准；是否执行及如何复用历史有效证据以 `decision_6febe7bb57f9` 为准。退役不改写原 Run、失败结论或稳定条目 ID，也不证明当前端到端能力已通过。

