# Reader 正式 hooks（源码接通，未发布）

回读并保留 181b15bb 发布后的 Paper 路由和删除逻辑。本轮只在其上增加 Reader 接线；现有页面和生产 Reader 未切换，翻译文件未修改，无真实论文导入或发布。

## 接线与默认行为

- `management_portal/http_paper.py`：正式 routes 集合挂 `http_paper_reader.production_routes`，位于 API fallback 前。源码中的四条路由现在默认注册；运行中实例未发布，不能宣称已生效。POST `/paper/api/reader/open` 显式按需导入，POST `/paper/api/reader/annotations` 保存；GET 同 annotations 路径读取，GET `/paper/api/reader/pdf` 读取固定版本。正式认证/同源/expected principal 约束保持。仅装配不查论文、不导入、不初始化 Reader 表。
- `management_portal/paper_store.py`：已有 `delete_paper` 在唯一 unregister_owned 调用外挂 `production_service().delete_if_used`，保留 actor/projection_ids 传递及原返回行为。
- `management_portal/paper_reader_service.py`：open 与 delete_if_used 使用现有 PostgreSQL 独立会话 advisory_lock，按 schema+单篇 ID 隔离。不能使用外层事务包裹注销：注销与 Reader 属于不同 owner 的提交边界，外层回滚会丢失已提交注销对应的意图。prepare/注销/恢复仍各自持久提交。会话连接断开释放锁，无新任务框架。
- 未启用 Reader 的 Paper：在不触发 DomainDatabase.ensure 的 raw 连接上，先检查服务所属三张表是否存在以及单篇是否有 source/open/deletion 意图。无数据直接原注销，不建 Reader 表/附件目录、不插冻结意图。与 open 的同篇锁避免检查后并发首次导入漏挂删除。此探测针对正式 service 导入链，不将实验独立 AttachmentOwner 导入视为正式已接入对象。
- `management_portal/directory_maintenance.py`：既有维护流程在最终 bundle 队列执行前调用有界 recover_pending_deletions；表不存在返回空，不建表。pending/错误计入既有 report.errors。Reader 只处理元数据，现有 bundle 删除队列继续唯一拥有文件清理。没有启动新定时器。

沿用定向 registered_only manifest/PDF 解析、active_write 与 import/rollback 共同数据库锁。旧版本仍可读，不允许在替换提交后写旧版本。原 PDF/旧版本保留策略不变。

## 新证据

新增 `tests/cases/test_paper_reader_hooks.py`，真实临时 PG + 临时目录合成 PDF：

1. 通过 **http_paper.routes 实际装配 → production_routes → production_service → 三个 owner**，验证打开、保存、重开 revision、expected principal 错配、PDF 替换后旧写 409；随后走真实 PaperStore.delete_paper → unregister_owned → Reader 清理，验证权威注销事实与恢复队列为空。
2. 未使用 Reader 的真实 Paper 删除前后 reader_* 表集合不变，未创建 reader-attachments 目录。
3. 正式删除中注入 annotation purge 失败，确认注销已提交；新 production_service 从持久意图恢复到 done，bundle 保留给正式队列。

测试在认证边界注入服务端认证结果 `{type: human, id: user_fixture}`，仍走正式 page_api_auth 主体解析；资源 ACL 替为允许，JSON/响应 helper 注入，不宣称覆盖真实 cookie/token 解析或线上权限配置。registry 注册、注销、目录队列登记、附件/批注持久化均真实执行；不执行队列物理删除。

命令：工程 venv `python -m pytest -q tests/cases/test_paper_reader_hooks.py`，最终 **3 passed in 0.91s**。首次认证 fixture 形状错误产生 403，修正后该项通过；新增故障恢复项首次发现外层事务吞掉意图，改独立会话锁后重跑本文件全部通过。没有重复 UI/模块已过测试。

## 可切换门槛

源码 HTTP 与删除/有界恢复 hooks 已接通；前端尚未切换。切换前需在同一构建中验证真实登录会话/CSRF/退出换账号、既有全屏宿主加载版本资源与关闭屏障，并验收正式目录队列实际删除及维护调度（本轮只验证注销登记和恢复调用，不运行全量 maintenance）。已有原生 Chromium/Firefox 合成 UI 证据仍有效，但不是已挂正式页面的验收。发布、真实单篇按需导入及根目录容量/保留政策仍由整合方统一决定，禁止全库复制或自动回收旧版本。
