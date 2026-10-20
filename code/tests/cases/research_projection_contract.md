# 研究投影测试入口与证据范围

责任模块：`workbench.panels`。测试目录从测试源码生成，下表的稳定 ID 登记在 `tests/cases/case_ids.json`。
选测适配器：`tests/cases/test_research_projection.py`，runner 为 `pytest`，profile 为
`local.fixture`。适配器调用应用包原测试，不复制会话、过滤或构建算法。

| 稳定 Test ID | 适配器函数 |
| --- | --- |
| `test_78f26138a59e` | `test_permission_revocation_and_service_errors` |
| `test_a2dbdbd45e8e` | `test_hidden_destroyed_session_rejects_late_reads` |
| `test_30c0830ef66f` | `test_discussion_context_and_changed_source_cancellation` |
| `test_9a8ed8d24236` | `test_selection_race_and_closed_selection` |
| `test_49cb3f9a901d` | `test_index_filters_unreadable_sources_before_counting` |
| `test_ff088cc11bd6` | `test_research_package_isolated_build` |
| `test_fe6e73fa9186` | `test_migration_patch_uses_workbench_fields_and_preserves_metadata` |

## 单元与迁移定义选测

在仓库根目录选择上述 Test ID，或使用精确 pytest node ID，例如：

```sh
.venv/bin/python -m pytest -q tests/cases/test_research_projection.py::test_migration_patch_uses_workbench_fields_and_preserves_metadata
```

前五项需要 Node；每项通过 `node --test --test-reporter=tap --test-name-pattern=^<精确标题>$`
调用 `management_portal/workbench_projections/research_apps/tests/session.test.mjs`
中的一个原合同，并检查确有一个通过、没有失败。Node 名称与映射保存在适配器中。
原整文件入口仍为在 `research_apps` 内运行 `node --test tests/session.test.mjs`。
这是测试替身中的资源与反馈调用，不是正式业务投递。

定义入口调用原 `tests/test_definition.py`：更新字段必须属于 Workbench 定义字段，
维持两个原 ID、原期望修订与团队角色；title/description/owner/project/navigation
不进入迁移 patch。它不保存、构建或发布真实工作台。

## 离线构建选测

```sh
PORTAL_RESEARCH_BUILD_REPORT=/absolute/existing-output-dir/build.json \
  .venv/bin/python -m pytest -q \
  tests/cases/test_research_projection.py::test_research_package_isolated_build
```

原合同固定使用 `/home/example-user/.npm` 的离线缓存，必须已有应用锁文件所需 tarball
（包括 `esbuild@0.25.12` 及当前平台包）。环境还需 Git、`/usr/bin/node`、
`/usr/bin/npm`、`/usr/bin/bwrap` 和可用的隔离用户/网络命名空间。
不在线补依赖、不跳过缺失前置。报告路径省略时，适配器使用 pytest 临时目录。
显式报告路径的父目录须预先存在。

原测试在临时 Git 仓库执行生产 `build_application`，实际离线安装、打包并读取产物；
资源解析与 actor 是隔离替身，产物根目录也在 pytest 临时目录。
报告中的 commit 和双入口 artifact 是测试样本，不能替代两个原工作台的正式发布回执。

## 历史浏览器证据

迁移时的独立 Chromium fixture 及原始报告属于下节列出的固定源码版本，
不是当前仓库的可运行测试入口。当前源码不含 `research_apps/tests/browser.cjs`，
因此不再提供该路径的执行命令，也没有对应的正式 UI Test ID。
原迁移证据保留，不改写其通过范围；它不能证明后续源码、当前用户授权或真实 Family 回复。
如任务明确要求新的界面验收，应由正式测试职责按当前实现另行执行，不能将历史报告当作本轮结果。

## 本次登记复用范围

冻结应用源树 `21ac1fe7394271facc662c51358edefbaae02ee720a1cc4ca371091dbc26d82e`
的构建与浏览器原件保存在迁移父成员的 `research-migration/child-delivery/evidence/`。
仅 `definition.py` 后续改变，及新增 `tests/test_definition.py`，由
`generator-increment.json` 和 `definition-contract.xml` 独立证明。
Node 原始 TAP 仅保留于子成员工具输出 chunk `8dba98`，没有另造日志。

登记只核对原件 SHA、目录模型、入口映射与适配器收集，不重跑已通过的行为。
已有 fixture 证据和定义增量分别保留其版本范围；正式保存/发布及真实使用由迁移父任务负责。
预算治理与发布决策遵循当前 Design；本说明不新增全仓测试或统一发布等待条件。
