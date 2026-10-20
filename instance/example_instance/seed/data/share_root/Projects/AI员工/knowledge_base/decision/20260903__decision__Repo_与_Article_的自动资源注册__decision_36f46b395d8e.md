---
kb_id: decision_36f46b395d8e
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 10
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T22:37:12Z'
  updated_by:
    user: ''
    agent: resident_83d75ad3a6c7
importance: 7
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/server.py
  - ${code_root}/management_portal/resource_auto_registration.py
  - ${code_root}/management_portal/article_auto_registration.py
  - ${code_root}/management_portal/portal_features.py
  - ${code_root}/tests/cases/test_resource_auto_registration_optional.py
  - ${code_root}/management_portal/resources/registration.py
  - ${code_root}/management_portal/resources/providers/repo.py
  - ${code_root}/management_portal/resources/providers/article.py
  - ${code_root}/management_portal/resource_identity_backend.py
  - ${code_root}/management_portal/article_identity_backend.py
  - ${code_root}/tests/cases/test_resource_auto_registration.py
  - ${code_root}/tests/cases/test_repo_workspace_backend.py
  - ${code_root}/tests/cases/test_article_identity_backend.py
  - ${code_root}/tests/cases/test_exported_repo_source.py
  related:
  - decision_b0997bc3a922
  - decision_cf27d39e39cd
  - decision_4a9ba5dfada6
  - decision_b6e74d78f7b6
  - decision_8dc6b3d03ca7
  - decision_6476c69b33da
  - decision_956154cd02f6
  supersedes: []
  derived_from: []
---
# Repo 与 Article 的自动资源注册与失效 Repo 清理

Project 的文件树里常常已经有 Git 仓库或论文稿件目录，但它们还没有 Portal 的统一 ID。自动资源注册是 ResourceService 进程中的一个后台任务：它定期扫描已登记 Project 的文件树，把找到的 Git 仓库登记为 Repo 资源，把稿件目录登记为 Article 资源，并按 Repo 声明的内容来源注销已经失效的登记。已显式登记的导出源码树不需要 `.git`，其存活按唯一的规范根目录判断。登记之后，它们会出现在资源目录中，可以用资源地址引用，并接受统一的权限检查。

本 Design 说明扫描范围、Repo 与 Article 的判定顺序、登记和清理规则、“不再自动登记”的记录、调度和失败恢复。自动注册没有独立页面。用户在页面上手动登记资源见 `decision_b0997bc3a922`；ResourceService 的登记接口见 `decision_b6e74d78f7b6`。

## 基本概念

- **登记（registration）**：为一个已经存在的目录分配稳定的统一 ID，并在对应领域的登记表中记录它的路径。目录移动或改名后，其他地方仍用这个 ID 指向同一资源。Git Repo 的登记表记录一个或多个工作副本（checkout）；导出源码 Repo 只记录一个规范内容根。Article 的登记表记录每篇稿件的根目录。
- **一轮核对**：后台任务的一次运行。它比较“Project 文件树里现在有什么”和“登记表里已经记了什么”，登记缺少的资源、注销失效的 Repo，并清理分错类的资源。所有登记和注销都通过 ResourceService 调用 Repo 或 Article 的领域实现完成，扫描器本身不写登记表。
- **Article 目录**：Project 根目录本身或它的直接子目录中，名称为 `article`、`articles`、`artical`、`artical_arxiv`，或以 `article_`、`artical_` 开头的目录。这样的目录还必须包含一个 `.tex`、`.md` 或 `.markdown` 主稿件文件，才是有效的 Article；否则记为跳过项。
- **Git 仓库**：Project 文件树中包含有效 `.git` 的目录。扫描时跳过 `node_modules`、`.venv`、`venv`、`build`、`dist`、`site-packages`、`__pycache__` 和各类缓存目录，不跟随符号链接。
- **Repo 内容来源**：`tfi.repo.v1` 中省略 `source_kind` 或明确为 `git_checkout` 时，按 Git 工作副本判断；明确为 `exported_tree` 时，按非空、绝对路径 `canonical_checkout` 指定的导出源码根判断。未知或空的类型值拒绝，不自动推断为 Git 或导出源码。来源与能力的完整合同见 `decision_6476c69b33da`。
- **忽略记录**：按资源类型保存的一组路径，表示“后台不要再自动登记这些路径”。它只影响后台任务，不删除目录，也不阻止用户手动登记。

## 设计点

### 只扫描已登记 Project 的文件树

- **扫描范围是 Project 登记表（PostgreSQL 资源登记表 `project_registry` 集合）中每个 Project 的根目录。** 其他目录即使位于 `${share_root}` 下或包含 `.git`，也不会被自动登记。
- **每轮报告写明实际扫描了多少个 Project**（`project_count`）。没有可用的 Project 根目录时，本轮仍检查已登记 Repo 的内容来源，跳过新 Repo 和 Article 的目录发现。

### Article 按安装状态参与

自动登记和 Repo 的正式登记提供器共同使用 `resource.article_registration` 接入点。Article 源负责读取和校验已有稿件登记、发现稿件目录及判断目录是否属于稿件存储；它返回原有逐资源错误，不由共享扫描器解释 Article 内部的登记格式。

Article 未安装时，不读取 Article 登记、不发现或登记稿件，也不把稿件名称和 Article 忽略路径当作 Repo 排除边界；有效 Git 目录继续依 Repo 规则处理。功能已安装时按下面的顺序完成分类、忽略与清理，Repo 正式登记仍拒绝 Article 存储目录。模块存在但内部依赖损坏时报告原错误，不静默降级为纯 Repo 模式。

### 先判定 Article，再判定 Repo

同一个目录只能有一个领域身份。稿件目录经常也是 Git 仓库，所以后台先确定所有 Article 所在的范围（称为 Article 边界），再在边界之外寻找 Repo。Article 边界由以下路径组成：

- 本轮找到的有效 Article 目录；
- 本轮判定为跳过的 Article 目录；
- 已经登记的 Article 的所有根目录；
- 忽略记录中的 Article 路径。

位于 Article 边界之内的 Git 仓库不会登记为 Repo。这样，页面上不会同时出现指向同一份内容的 Article 卡片和 Repo 卡片。

### 清理失效的 Repo

存活判断由 Repo 领域实现统一提供；扫描器不自行探测后改变 `source_kind`。

- **Git 来源（`git_checkout`）**：检查全部 checkout 以及主 checkout；所有工作副本均失效，或没有任何工作副本路径时，后台自动注销 Repo，并把 ID 记入报告的 `removed`。只要一个副本有效，登记继续保留。
- **Git 副本的有效性**：必须具有可读取的 Git 元数据，且 Git 返回的仓库根目录与登记路径一致。目录消失、路径变为普通文件、`.git` 消失、Git 标记损坏或 worktree 指向的 Git 目录失效，都属于失效。无 remote、尚无 commit、detached HEAD 或没有 push 权限的有效仓库继续保留。
- **导出源码来源（`exported_tree`）**：只检查非空、绝对 `canonical_checkout` 指定的目录。目录存在即可保留，不要求 `.git`；根消失、路径变为普通文件或父路径不再是目录时，按正式注销流程处理。其他 checkout 路径、来源 Git 元数据或远端不能替代该根，不允许回退到别的副本。
- **不能确认失效时保留并报告**：未知 `source_kind`、损坏的规范根声明、权限不足、Git 检查超时及其他读取错误，写入 `resource_errors`，操作为 `validate`；不据此注销，不静默修正登记。下一轮重新检查。
- **自动注销只移除 Portal 登记和相应搜索投影，保留目录、文件与 Git 历史。** 后台通过 ResourceService 调用正式注销流程，不直接删除磁盘内容。
- **自动清理不留下永久忽略记录。** Git 工作副本恢复后，只要位于扫描范围，下一轮可重新登记并获得新的 Repo ID。无 `.git` 的导出源码不属于自动发现候选，须由明确的导出/导入登记流程建立记录；不自动恢复已注销 ID。用户主动注销留下的忽略记录仍由手动登记解除。

### 清理被误登记为 Repo 的稿件目录

- **一个已登记 Repo 的全部登记内容路径（Git 的全部 checkout，或导出源码的唯一 canonical 根）都位于 Article 边界之内时，后台注销这个 Repo，并把它的 ID 记入报告的 `removed`。**
- **Git Repo 只有部分工作副本位于 Article 边界之内时，保留这个 Repo。** 它还有独立的工作副本，自动注销会丢失这部分内容。
- **清理成功后，后台清除这些路径在 Repo 忽略记录中的条目。** 注销 Repo 时领域实现会把它的路径写入忽略记录（见下文），而这里的注销是在纠正分类，这些路径以后若不再属于稿件目录，仍应能重新被识别为 Repo。
- 注销失败时保留原登记，错误写入报告的 `resource_errors`。

### 登记新的 Repo 和 Article

- **Repo：** 在 Article 边界之外找到的每个 Git 仓库，如果它的路径既不在已登记 Repo 的工作副本中，也不在 Repo 忽略记录中，后台就通过 ResourceService 登记它。Repo 的领域实现再次检查路径，并拒绝登记稿件目录。
- **已登记的导出源码：** 唯一 canonical 根纳入已登记路径集合；即使该根后来出现 `.git`，也不重新自动登记或改成 Git 来源。普通 Git 登记不得借残留远端或 Git 元数据合并到导出身份，同根登记也不能静默替换该身份。普通无 `.git` 目录不因这项规则成为 Repo 候选。
- **Article：** 本轮找到的每个有效 Article 目录，如果它既不是已登记 Article 的根目录，也不在 Article 忽略记录中，后台就通过 ResourceService 登记它。Article 的领域实现再次检查目录必须位于已登记的 Project 内，并检查主稿件文件。
- **自动登记跳过用户权限检查。** 后台以内部身份 `resource-auto-registration` 调用登记，并附带“自动登记”标记。这个标记只有 Portal 内部进程能设置：页面和 Resident 通过 `RegisterResource` 指令登记资源，指令不接受这个标记。
- **一项登记失败不影响其他候选。** 失败项写入 `resource_errors`，其余候选继续处理。

### 忽略记录由注销和手动登记维护

- **用户注销一个 Repo 或 Article 时，领域实现把它的全部路径写入忽略记录。** 后台以后不会把用户刚刚注销的目录重新登记回来。
- **用户手动登记一个路径时，领域实现清除该路径的忽略记录。** 用户的明确操作优先于之前的注销。
- 忽略记录保存在 PostgreSQL 资源登记表的 `auto_registration_ignore` 集合，新写入的格式标识为 `tfi.resource-auto-ignores.v1`；已有忽略记录继续读取，不因标签更名而重写。当前命名与部署配置规则见 decision_15aab18c0ab3，每项包含资源类型、规范路径、被注销资源的统一 ID 和写入时间。

### 无效的 Git 标记会被报告

- **存在 `.git` 却无法解析为有效仓库的目录，写入报告的 `invalid_git_markers`。** 这些目录不会登记为 Repo，维护者可以根据报告修复。

## 一轮核对的顺序

```text
读取 Project 根目录、忽略记录、已登记的 Repo 与 Article
  → 找出 Article 目录与跳过项，确定 Article 边界
  → 按 source_kind 核对内容来源，注销已确认失效或全部落在 Article 边界内的 Repo
  → 在 Article 边界之外找出 Git 仓库，登记新的 Repo
  → 登记新的 Article
  → 返回本轮报告
```

登记和注销都会通知资源索引，索引随后更新摘要（`decision_8dc6b3d03ca7`）。自动核对本身不等待索引。

## 报告

每轮核对返回一份报告：

| 字段 | 含义 |
| --- | --- |
| `project_count` | 本轮扫描的 Project 数量 |
| `registered` | 本轮新登记的 Repo 与 Article 的统一 ID |
| `removed` | 本轮注销的失效或误分类 Repo 的统一 ID |
| `skipped_articles` | 不能作为 Article 登记的目录及原因 |
| `invalid_git_markers` | 无法解析为仓库的 `.git` 位置及原因 |
| `resource_errors` | 验证、登记或注销失败的资源，包括资源类型、ID 或路径、操作和错误说明 |

## 调度

- **后台任务随 ResourceService 进程启动。** 首次核对在启动后 60 秒进行（环境变量 `PORTAL_RESOURCE_AUTO_REGISTER_INITIAL_DELAY_SECONDS` 可调整），之后每 24 小时一轮（`PORTAL_RESOURCE_AUTO_REGISTER_INTERVAL_SECONDS` 可调整，最小 60 秒）。环境变量 `PORTAL_RESOURCE_AUTO_REGISTER` 设为 `0`、`false`、`no` 或 `off` 时不启动。 这些变量及状态文件变量均由 `read_product_env` 只按规范的 `PORTAL_*` 名称读取；读取不改写环境。通用规则见 decision_15aab18c0ab3。
- **上次成功完成的时间保存在运行状态文件中**（由 `PORTAL_RESOURCE_AUTO_REGISTER_STATE_PATH` 指定；默认位于 `portal_paths.path("staging_root") / "shared" / "portal_runtime" / "resource-auto-registration.last-run"`）。距离上次成功不足一个间隔时，本次不扫描，并在剩余时间后再试。
- **同一时刻只运行一轮。** 运行状态文件同时作为锁；另一个进程正在核对时，本次跳过并在 60 秒后重试。
- **有失败项的一轮不记为成功。** 报告中出现 `resource_errors` 时不更新上次成功时间，下一次最迟 15 分钟后重试；核对本身抛出异常时，同样在“正常间隔”与“15 分钟”中较短的时间后重试。

## 状态归属与恢复

| 状态 | 归属 | 出错后怎样恢复 |
| --- | --- | --- |
| Git 目录与 Git 元数据 | Project 文件树 | 修复后，下一轮重新扫描 |
| 显式导出源码目录 | 登记的唯一 canonical 根 | 保留登记时下一轮重新验证；已注销时须通过明确登记流程恢复，不由 Git 扫描推断 |
| Repo 与 Article 登记 | 各自领域的登记表 | 通过 ResourceService 登记或注销 |
| 不再自动登记的路径 | 忽略记录 | 用户手动登记该路径，或由注销流程写入 |
| 上次成功时间 | 运行状态文件 | 下一次触发时重新核对 |
| 可搜索摘要 | 资源索引 | 变化通知或周期刷新 |

核对可以重复运行：已经登记的路径会被识别并跳过，部分成功不需要回滚。每一轮都重新读取文件树和登记表，不依赖上一轮留在内存中的状态。
