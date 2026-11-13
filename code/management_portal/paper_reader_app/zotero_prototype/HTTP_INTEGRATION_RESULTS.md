# Reader HTTP 与正式装配切片

新增可调用装配与独立 handler，不替换现有 PDF.js 阅读器；无生产请求、真实论文导入、配置变更或发布。翻译实验未接线。

## 已有代码

- `management_portal/paper_reader_service.py`：从 Paper 权威 locator 获取 bundle/source；首次显式 open 导入到该 bundle 的 `reader-attachments/`，保留原 PDF。source stat 指纹不变时读当前版本，不复制或整份哈希；变化时按新版本导入。每篇文件锁合并并发 open，不扫描全库。旧版本保留。
- `management_portal/http_paper_reader.py`：独立 POST open、GET/POST annotations、GET pdf，返回 RouteDefinition(dual-auth)。`production_routes(portal)` 默认复用 http_auth._get_agent_api_auth、page_api_auth.authorize、现有受限 JSON 读取与响应 helper；不会自行注册。
- `zotero_prototype/http_transport.js`：原生 host 可直接使用的 load/save/remove options。每个请求绑定 X-Reader-Principal，服务端必须与真实认证主体完全相同；cookie 切换返回 409，不把旧请求写到新账号。客户端只能提供 paper_id/正式 descriptor，不接受 source、路径或 owner。
- `management_portal/static/paper_reader_http_transport.js`：由现有 esbuild 生成的 ESM bundle，6.3 KiB；没有编辑现有 paper_reader.js 或个人页面。构建命令：`management_portal/paper_reader_app/node_modules/.bin/esbuild management_portal/paper_reader_app/zotero_prototype/http_transport.js --bundle --format=esm --target=es2022 --outfile=management_portal/static/paper_reader_http_transport.js`，退出 0。
- 已有附件/注释 owner 新增自身 metadata purge 接口，删除协调器调用 owner 接口，不直接解释其他 owner 的表；附件 owner 提供按操作查询 import_state。

生产装配目前没有访问真实库/文件；仅被隔离 fixture 构造验证懒初始化。实际启用需要父节点挂接下述 hook，不能称已注册生产路由或完成正式切换。

## 单篇恢复及删除边界

新增 reader_open_intents、reader_document_sources、reader_deletion_intents，仍为 paper schema 懒建表。

open 在复制前单独事务记录 signature/operation/expected_version；文件流式复制与 owner journal 不包在外层长数据库事务中。版本已接受但 source pointer 未写入时，下一显式 open 根据旧操作 ID 恢复；失败复制任务通过新显式 open 重新建立任务，不读取全库或启动新后台框架。源变化检测使用 stat，沿用前述非对抗性假设，不能据此证明外部恶意修改且恢复元数据不存在。

删除必须按正式 owner 顺序：

1. 正式 Paper 删除 owner 完成鉴权，在 unregister_owned 前调用 `service.prepare_delete(paper_id)`。它与 open/read/commit 使用同一单篇文件锁，等待已有导入完成，再持久写删除意图；之后 Reader 新读写拒绝。
2. 调用既有 unregister_owned，继续使用现有 Paper bundle 清理队列。版本文件、锁和 staging 都在 bundle 内，随正式对象目录删除，无独立无期限文件旁路。
3. 注销成功后调用 `service.finish_delete(paper_id)`；它先核对正式 is_deleted，再经各 owner 接口事务清理注释/receipt/版本/import/source metadata，幂等保留 done 墓碑。数据库异常可按单篇 intent 重试，不需重新导入。

当前实现有上述可调用协调接口，**正式 Paper 删除入口及其持久恢复调度尚未挂接**。如果 unregister 失败，Reader 保持冻结，必须由正式删除 owner 明确重试或取消删除；没有未经授权的自动恢复读写。对象删除队列的端到端测试和跨进程对象清理期间的竞态验收仍是接线门槛，不能以隔离回调模拟替代真实注销验证。

## 精确接线点（已向父节点协调，不编辑并行 worker 文件）

- `http_paper.routes(portal)` 的返回元组中追加 `*http_paper_reader.production_routes(portal)`，或由现有 feature route 聚合器合并同样 tuple；放在 Paper prefix_fallback 前。现有 feature 声明为 `portal_features.py` 的 paper / web.routes。新 handler 同时需要进入该 feature 的模块发行范围，不能只在开发环境可 import。
- `PaperStore.delete_paper`（paper_store.py 约 695 行）的 `unregister_owned(...)` 前后调用 prepare_delete/finish_delete；已有对象删除恢复流程需要携带 paper_id 并在正式 is_deleted 后重试 finish_delete。若父节点选择由正式 Resource 删除生命周期 provider 接入，要求相同顺序/持久恢复契约，不重复创建另一套删除机制。
- 新前端 bundle 需要现有静态资源供给入口纳入；现有全屏入口在切换验收前不改。测试演示是 `openHttpDocument({paperId,principal})` 后将 options 传给 mountReader；principal 必须来自正式页面已认证身份，不能猜用户名称。位置持久化尚未有正式 endpoint，options.savePosition 目前为 no-op，生产不能宣称进度保存。

## 新增隔离验证

`tests/cases/test_paper_reader_http.py`、`tests/cases/javascript/zotero_reader_http.cjs` 使用临时 PG、合成 PDF、测试服务端绑定 session，并直接装配独立 handler：

- Chromium 和 Firefox 原生 Web Reader 创建便签→HTTP→PG→关闭→重新打开恢复；旧版本请求在新 active 版本导入后 409。
- cookie 从 a 切 b 而请求仍期望 a 时 409；另一账号合法打开不继承原账号注释。
- 模拟版本登记完成但 open source pointer 未写入的中断，再次 open 恢复原操作。
- 删除准备后拒绝新打开；正式 deleted 未成立时 finish 拒绝；成立后重复 finish 清理 metadata 幂等。测试保留合成源文件，实际 bundle unlink 仍由正式队列验收。
- HTTP 边界：principal 不匹配、超大请求、非对象 JSON、客户端 source 路径均拒绝；production factory 装配不读取 Paper，不导入文件。

实际分批命令结果（使用已有工程 Python，未重跑模块单测）：

```
.../venv/bin/python -m pytest -q tests/cases/test_paper_reader_http.py
# 双浏览器/lifecycle 修改后的有效结果：2 passed in 6.28s
.../venv/bin/python -m pytest -q tests/cases/test_paper_reader_http.py -k validation
# 1 passed, 2 deselected in 0.32s（新增 assembly 前）
.../venv/bin/python -m pytest -q tests/cases/test_paper_reader_http.py -k assembly
# 1 passed, 3 deselected in 0.40s
```

后续仅加了 production factory 的正式依赖默认装配与请求边界校验；happy-path 浏览器结果复用，边界/装配由新增直接测试覆盖。测试并未使用真实生产 cookie/bearer 签发系统；其正式授权函数已在 production factory 连接，但完整真实认证回归需在路由挂接后验收。未运行真正 Paper 注销清理队列、静态资源发行/HTTP检查、PDF Range、生产容量和阅读进度保存；当前不能宣称达到可切换门槛。没有真实文件导入，也没有上线翻译实验。
