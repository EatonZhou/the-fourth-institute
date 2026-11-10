# Reader 删除恢复与 HTTP 变更约束

本轮仅修改 Reader 新模块和直接测试，未修改 http_paper、paper_store、页面、翻译缓存或执行文件，未发布、未调用真实文件导入。已有原生浏览器结果复用，没有重跑 UI。

## 删除入口契约

正式 Paper 删除 owner 完成鉴权后，推荐调用：

```
service.delete_with(paper_id, lambda: unregister_owned(...))
```

此 wrapper 替代分散的 prepare/finish 接线。它先持久准备意图，再取得与 Reader 单篇读写相同的跨进程文件 gate，重新确认冻结，持锁执行唯一正式注销调用。正常返回或抛异常后，都读取权威注册/注销状态恢复，不依据返回值或异常猜测事务结果。文件清理仍完全交给已有 bundle 删除队列，Reader 不自行 unlink 版本或 bundle。

`prepare_delete`、`finish_delete` 保留为底层阶段接口，不能仅串两个调用就宣称覆盖注销中的跨进程竞态。父节点尚未获共享文件释放，本轮没有挂正式 hook。wrapper 和按 ID 的 `recover_delete` 已给父节点协调。

## 恢复决策

生产 `lifecycle_state` 使用 registry 的 deleted_identities 和 paper 登记读取：

| 权威事实 | 行为 |
|---|---|
| 已注销 | 调用各元数据 owner 的 purge 接口，事务清理单篇记录，意图标 done；异常回滚后可按 ID 重试 |
| 仍活跃，原注销 gate 无人持有 | 再次核对活跃事实，删除 abandoned prepared 意图，保留全部版本/注释，允许继续阅读 |
| 仍活跃，但注销 gate 正被持有 | 返回 pending，不抢占、不解除冻结 |
| 无登记也无注销事实，或权威查询失败 | 保留意图和数据，不执行清理或擅自解冻；查询恢复后再读事实 |

下次**显式 POST open** 会对遗留 prepared 意图做上述恢复，因此注销失败/prepare 后进程退出不再让正常活跃 Paper 永久冻结。已注销 Paper 无法由普通读取复活；正式维护 owner 需从持久意图 paper_id 调用 recover_delete，处理“注销成功但 finish 前退出”及清理重试。无需全库目录扫描或新后台框架。bundle 的锁目录意外缺失属于无法确认的文件故障，保守保留 pending，不能据此编造“原注销已停止”。

意图新增 bundle_location，使用正式 stored_location/located_path 区域引用；它只定位同一对象内 gate，文件删除仍属于正式目录队列。权威 bundle 定位与原 PDF 定位拆开：原 PDF 已删除时仍可读取已导入历史版本，也可准备整个 Paper 删除，不猜测 PDF 路径。

## HTTP 行为与默认状态

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/paper/api/reader/open` | 单篇显式按需导入/恢复；可能写文件和元数据 |
| POST | `/paper/api/reader/annotations` | 账号范围内原子保存批次 |
| GET | `/paper/api/reader/annotations` | 仅已有版本快照；没有 scope 时返回 revision 0 空快照，不 INSERT 空 scope |
| GET | `/paper/api/reader/pdf` | 只读已登记固定版本字节 |

所有路由默认**未注册、未启用**，只有父节点挂 production_routes 才会进入正式服务；factory 装配不导入论文。正式认证复用 http_auth._get_agent_api_auth 与 page_api_auth.authorize。所有请求验证 X-Reader-Principal 等于本次实际认证主体；身份切换在读取或写入前返回 409。

两类 POST 使用现有 http_auth._same_origin_request 检查 Origin/Referer，另拒绝 Sec-Fetch-Site:cross-site，并要求 application/json、受限 Content-Length 和正式有界 JSON reader；不靠 expected principal 代替 CSRF。无 Origin 的机器客户端遵循既有 helper 行为，仍必须通过真实 bearer/session 鉴权。GET 不做附件导入/迁移，不创建锁目录或业务 scope；通用 DomainDatabase 首次使用的懒 DDL 仍遵循既有数据库初始化规则。

## 文件与依赖

- 修改 `paper_reader_service.py`：delete_with/recover_delete、权威 lifecycle_state、bundle 独立定位、POST open 遗留恢复。依赖既有 registry_store/directory_registrations 的真实事实、区域位置 helper、单篇 gate 和三个 Reader owner；不接翻译线。
- 修改 `paper_reader_attachments.py`：只读 gate 打开模式，GET 不创建目录/锁文件。
- 修改 `paper_reader_annotations.py`：读取已有 scope，不再由 read INSERT 业务记录。
- 修改 `http_paper_reader.py`：正式 same-origin 与 JSON POST 约束；没有新增路由方法或改默认启用状态。
- 新增 `tests/cases/test_paper_reader_recovery.py`；调整已有 HTTP 负例为正式 JSON Content-Type。新测试只用临时 PG、合成字节和临时目录。

## 证据

运行工程 Python：

```
.../venv/bin/python -m pytest -q tests/cases/test_paper_reader_recovery.py tests/cases/test_paper_reader_http.py -k 'not native and not assembly'
```

最终该命令退出 0：`9 passed, 3 deselected in 1.43s`。测试使用真实临时 registry 登记/注销墓碑作为权威事实，覆盖失败/退出恢复、活跃 gate、提交成功丢回执、清理异常回滚重试、unknown/unavailable 不清理、原 PDF 缺失、GET 无导入无 scope 写入、跨站 POST/类型/主体拒绝。未改现有注销实现，只在测试 fixture 内通过 registry writer 构造正式事实；正式 Paper 注销→目录清理队列的完整验收仍需 hook 挂接后完成。

另提供 `recover_pending_deletions(limit=100)` 给现有 maintenance caller，从本职责 prepared 意图表按限额读取 ID 并逐项恢复；没有新 worker 或目录扫描。挂接时调用该方法即可，不需要维护 owner 直接读取 Reader 表。该枚举接线的修改仅补跑对应清理重试 case。

补跑命令：`.../venv/bin/python -m pytest -q tests/cases/test_paper_reader_recovery.py -k committed_unregister`，退出 0：`1 passed, 7 deselected in 0.41s`，确认持久意图枚举能完成注销后清理失败的重试。

## 定向查找与 active-version 保存竞态修正

`production_service` 已去除 `_paper_by_id` 和 `paper_detail.resolve_asset` 调用（后者内部也会调用允许扫描的旧 helper）。现在统一用 `PAPER_STORE.manifest_for_paper_id(..., allow_scan=False, registered_only=True)` 读取已登记 manifest，再用 `manifest_to_paper` / `paper_file` 解析同一记录中的 PDF；未登记或未声明 PDF 直接拒绝，不使用旧 pdf_path 旁路。没有修改共享 paper_store/paper_detail。

新增附件 owner 的 `active_write`，使用与 import 发布、rollback 相同的 `reader_attachments:<paper_id>` PostgreSQL 事务 advisory lock，并锁定 head 行；在锁内验证附件 ID 和 active version，持锁直到 annotation commit 完成。服务层文件 gate 继续负责本层删除/打开协调，但不再依赖它阻止独立 owner 替换。允许保存先完成、替换随后提交；替换已先完成则旧版本保存拒绝，保留待存修改。历史读取语义不变。

新增两个直接测试：生产查找禁止调用旧 helper/resolve_asset/扫描且断言两个 lookup flag；真实临时 PG 中暂停保存，观察独立 rollback 在数据库 Lock 等待，释放保存后 rollback 才能完成，并验证之后旧版本写入被拒绝、revision 未增加。没有重跑 UI 或删除恢复的已过用例；prepare_delete 的失败/退出恢复仍使用上文 delete_with/recover_delete。

命令均为工程 venv Python 加 `-m pytest -q tests/cases/test_paper_reader_recovery.py`：首次 `-k 'serializes or registered_only'` 得到 `1 failed, 1 passed, 8 deselected in 5.53s`，失败是测试 SQL 的 LIKE 百分号未参数化（不是保存断言失败）；修正参数后仅补跑失败项 `-k serializes`，结果 `1 passed, 9 deselected in 0.46s`。两个新增用例分别通过，无真实文件导入/发布。路由及正式删除 hook 仍待父节点协调挂接。
