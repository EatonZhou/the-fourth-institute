---
kb_id: decision_6febe7bb57f9
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 19
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T20:53:45Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 9
metaness: 4
ai_generated: false
links:
  source:
  - ${share_root}/Skills/tfi-agent-rule/portal-development.md
  - ${code_root}/tests/cases/conftest.py
  - ${code_root}/tests/cases/postgres_test_database.py
  - ${code_root}/management_portal/test_framework/catalog_source.py
  - ${code_root}/management_portal/test_framework/local_runs.py
  - ${code_root}/management_portal/test_framework/tau.py
  - ${code_root}/management_portal/test_framework/tau_runtime/package.json
  - ${code_root}/package.json
  - ${code_root}/tests/cases/test_publish_portal_release.py
  - ${code_root}/tests/cases/test_test_framework.py
  related:
  - decision_08bb2d9141f9
  - decision_850e6248d680
  - decision_8273271b67b1
  - decision_f2a6a5dd4646
  - decision_c953eab654cd
  - decision_06defc47e960
  supersedes: []
  derived_from: []
---
# Portal 定向验证与发布证据

Portal 的开发持续并行进行，每次修改之后都运行全部测试既不现实，也不能说明修改是否正确。本 Design 说明开发者和 Agent 修改 Portal 后怎样确定需要验证的范围、按什么层次运行检查、测试怎样与正式数据隔离，以及交付时留下哪些证据说明“验证了什么、实际上线了什么”。

发布流程本身见 `decision_08bb2d9141f9`；测试条目、测试目录和运行记录见 `decision_8273271b67b1`；TAU 任务与运行记录见 `decision_c953eab654cd`，当前浏览器执行能力的限制见下文。

## 基本概念

- **受影响面**：一次修改可能影响的范围，包括修改的文件、负责这些文件的模块、被修改的共享协议、直接调用者和关键的用户操作路径。
- **定向验证**：只运行能够发现本次修改回归的检查，例如语法检查、相关单元测试或单个可运行的测试用例。
- **聚合门禁**：覆盖一整块受影响面的一组测试。当前可运行的本地聚合检查是与受影响模块对应的一批 pytest 文件；仓库没有浏览器聚合测试入口。
- **全面回归**：由 WALL 的“全面回归与修复”入口单独组织，对工程中当前可运行的测试进行回归。不可用的执行方式与未验证的范围单独列出，不能计为通过。
- **验证证据**：交付时列出的检查命令和结果、测试框架的运行 ID（`testrun_…`）、以及发布命令输出、单元日志和 Release 分支的 commit。

## 设计点

### 验证范围

- **验证范围由受影响面决定。** 实施者先确认修改属于哪个模块、改动了哪些共享协议、谁直接调用这些代码、用户会在哪条路径上感受到变化，再选择对应的检查。测试数量和运行时长不代表验证是否充分。
- **开发迭代中只运行直接相关的检查。** 每完成一小批修改，运行相关的语法检查、单元测试或单个可运行的用例。
- **已经通过的检查在它覆盖的内容没有变化时直接复用。** 覆盖的源码、测试夹具、配置和依赖的协议都没有改变，同一任务内不再重复运行。
- **失败后优先重跑失败的用例。** 修复后先重跑失败的用例或受影响的文件；相关修改稳定后，再运行一次对应的聚合门禁。
- **一个连贯的里程碑最多运行一次与受影响面匹配的后端聚合门禁。** 界面、视觉、性能和 Live 行为按本次改动选择必要的验证，并说明实际使用的方式与前提。当前没有浏览器聚合门禁，不能将已移除的命令列为可执行的验证步骤。
<!-- wall: {"key": "e8142ca95411d058"} -->
- **交付时说明验证的实际情况。** 列出运行过的检查、复用的有效结果和有意没有运行的门禁及原因，使验证范围可以复核。跳过的用例、预期失败、环境缺项和并行修改造成的失败分别说明，不合并成“全部通过”。

Playwright 浏览器测试和 TAU 实时用例负载高；整套界面测试与 TAU 仅在人类明确要求或本次改动直接改变对应能力且有必要时执行，不因准备发布或刚发布而自动运行。对应浏览器 spec 仍存在且确需验证时，只运行相关文件一次，使用 `--workers=1`；当前已经退役的套件或命令不能因此恢复为门禁。可复用的既有有效证据直接复用。

### 当前测试能力与证据边界

<!-- wall: {"key": "29b4e02fc4e15d06"} -->
- 当前目录发现与执行入口、pytest 与纯 TAU 的选择规则、稳定 ID 及历史读取由 `decision_8273271b67b1` 定义；本 Design 只决定本次改动需要哪些验证及其证据是否仍有效。
- **浏览器套件、配置和运行命令不再提供。** 当前没有 Playwright 用例总预算或 isolated/live 浏览器配置，也没有 `test:ui`、`test:ui:behavior`、`test:ui:budget` 等 npm 入口。
<!-- wall: {"key": "1812adb4c067fc7c"} -->
- TAU 可选运行时、受控账号登录、唯一 Dev、模块身份与账号额度条件由 `decision_c953eab654cd` 定义；条件未满足应记未覆盖或前提不足，不算已验证。
- **清理测试不产生通过证据。** 同步清理失效的脚本、专用夹具、命令、目录映射和文档引用，保留有效的稳定 Test ID 与历史运行记录；没有实际验证的行为明确列为未验证。

### 失败的归属

- **失败按原因分类处理。** 区分产品代码缺陷、错误或过时的测试断言、测试环境问题和其他人并行修改造成的暂时失败；依据当前有效的 Design 和真实业务语义修正代码或测试。删除用例、跳过失败或放宽正确的断言不能作为修复。
- **测试与实现一起维护。** 页面上的按钮被移除或测试改为参数化后，清理失效的测试入口，并在测试目录中记录实际有效的覆盖与缺口；测试函数改名时保留原测试条目的 ID（见 `decision_8273271b67b1`）。

### 测试与正式数据隔离

- **pytest 从不连接 Portal 自己的数据库服务器。** 测试使用显式指定的测试数据库，或本次会话启动的一次性数据库；指定地址与 Portal 数据库位于同一台服务器时拒绝运行，因此测试不会在正式服务器上创建 schema。
- **pytest 在独立的数据库命名空间中运行，结束后删除。** 每次 pytest 进程为自己生成唯一的数据库命名空间，所有表都建在这个命名空间下的 schema 中，会话结束时删除；锁文件、状态目录和缓存也放在临时目录中。
- **每个测试从空的业务数据开始。** 每个测试开始和结束时清空本次命名空间中的业务表（结构版本表保留），并重新打开各领域存储；授权配置也为每个测试换成独立的存储。
- **测试对共享对象的替换不会泄漏到后面的测试。** Lineage 的领域端口把自己没有的属性转发给绑定的实现；测试用 `monkeypatch` 替换端口上的函数时，还原会把原函数写在端口上，使它此后不再转发。每个测试前后，测试配置移除端口上这类被写入的属性，后面的测试替换绑定实现时仍然生效，测试结果不因执行顺序而改变。

### 发布与发布证据

- **发布不运行测试，也不以测试结果为门槛。** 发布前的验证由实施者按本 Design 完成；发布工具只负责提交、切换和健康检查（见 `decision_08bb2d9141f9`）。
- **发布后，实施者核对实际结果再报告。** 实施者检查发布命令输出中的 `ok`、`changed_paths`、实例健康和推送结果，并在 Release 中确认目标功能确实生效，然后向用户报告发布的 commit；失败时说明尚未生效的部分。
- **Release 分支的 Git 历史说明哪些 commit 已经发布。** 命令输出包含本次 commit、相对上一个 Release 的实际变化路径、组件、健康和推送结果，独立 systemd 单元的日志保留输出供中断后查询。交付者核对实际分支与健康；WALL 用 commit 是否为 Release HEAD 或祖先校验发布结果。测试工作台中的一次“通过”不能替代上线验证，发布工具不持久保存报告或索引。
- **全面回归独立于发布。** WALL 的“全面回归与修复”由固定的回归测试 Resident 执行，结果附测试运行 ID；它不自动触发发布，也不是发布的前提。回归中发现并修复的问题，按日常流程定向验证后各自发布。

## 实现说明

### pytest 的运行环境

测试配置 `tests/cases/conftest.py` 在导入任何业务模块之前设置运行环境：

1. 通过 `tests/cases/postgres_test_database.py` 选择测试数据库。pytest 从不使用 Portal 自己的数据库服务器：
   - 设置了 `PORTAL_POSTGRES_TEST_DSN` 时使用它。它与 Portal 数据库位于同一台服务器时（socket 目录或主机、端口与 Shell 继承的 `PORTAL_DATABASE_URL`，或 `postgres_env_file()` 选中的本站环境文件中的地址相同（依次采用 `PORTAL_POSTGRES_ENV_FILE`、`deployment.postgres_env_file`、默认 `~/.config/tfi/portal-postgres.env`）），拒绝运行。
   - 没有设置时，本次会话用 `PORTAL_TEST_POSTGRES_ROOT` 指定目录（默认 `~/.local/postgresql-16`）中的 PostgreSQL 16，在 `/tmp/tfi-portal-pytest-pg-*` 启动一个一次性数据库：只开 Unix socket，编码、排序规则和时区与 Portal 数据库一致，会话结束时停止并删除。被强行终止的会话留下的一次性数据库，由下一次 pytest 启动时清理。
   - 选定的地址写回 `PORTAL_DATABASE_URL` 和 `PORTAL_POSTGRES_TEST_DSN`，测试中启动的子进程使用同一个数据库。
2. 设置 `PORTAL_DATABASE_NAMESPACE=pytest_<进程号>_<8 位随机十六进制>`。Portal 的数据库层把命名空间加在每个 schema 名前，因此本次测试的所有表都位于独立的 schema 中。
3. 把搜索身份锁、Codex 线程锁、Lineage 目录、看板搜索任务、两步验证密钥、执行状态目录和 Article 构建目录指向本次测试的临时目录，并关闭资源索引的文件监视。
4. 自动 fixture 在每个测试前后清空本次命名空间中的业务表、保留显式列出的结构版本表，并重置进程内存储及权限端口实现；同时移除 `domain_ports` 端口实例上除自身字段（`_name`、`_implementation`、`_lock`、`_bound`、`_handler`）以外的临时属性。普通测试由 `permission_system_off` fixture 将权限模式设为 OPEN；权限行为测试通过专用权限测试支持显式开启隔离实例，不依赖生产权限配置，也不跨测试复用权限实现。设置 `ISA_RESULT_CONTRACT=1` 时，另一个 fixture 检查每次指令调用的返回值是否包含声明为必填的结果字段，缺少时让该测试失败。
5. 会话结束时释放各存储的连接，删除所有以本次命名空间开头的 schema，再删除临时目录。

需要使用固定路径作为默认参数的函数，测试时显式传入临时路径；资源相关的测试使用明确的测试用户，不用空用户绕过授权。在开发环境中运行单个测试文件的命令形如：

```bash
cd <Portal源码根> && \
  portal_instances/dev/runtime-venv/bin/python -m pytest -q tests/cases/test_x.py -p no:cacheprovider
```

不需要另外准备测试数据库，也不需要清除 Shell 继承的 `PORTAL_DATABASE_URL`。

### 测试目录与执行入口

目录发现、显式用例、稳定 ID、计划选择和正式运行记录以 `decision_8273271b67b1` 为权威。核对入口时只使用当前登记的实现，不用已删除的命令或空壳测试代替行为覆盖。

JavaScript 工具安装与可选 TAU 浏览器运行时不是验证通过证据；TAU 的安装、可用条件及平台限制统一见 `decision_c953eab654cd`“可用条件”。

安装检查、计划预览、实际执行和业务验收必须分别记录：前一阶段成功不能推断后一阶段通过。已有失败、取消、未执行与取证缺口继续保留，不以文档整理替换实际验证。

### 通过测试框架运行

需要可追溯的运行记录时，实施者通过 WALL 的 Test 工作区或 `/test/api/run` 运行测试（见 `decision_8273271b67b1`），交付时附上运行 ID。测试框架以 Portal Web 的环境启动 pytest 进程，数据库隔离同样由上面的测试配置完成。

### 实现来源与入口定位

验证责任由实施者承担。`scripts/publish_portal_release.py#publish` 和 `scripts/portal_services.py#restart` 负责提交发布、实例换代与健康检查；`management_portal/wall_backend.py` 通过 `scripts/portal_release.py#require_released_commit` 核对完整 commit 是否进入 Release 历史。这些是发布阶段及发布回执的实现定位，按 `decision_08bb2d9141f9` 管理，不代表发布工具自动运行测试或把测试结果作为发布门槛。本条实现来源列验证框架，发布实现仍在此保留为关联依据。

