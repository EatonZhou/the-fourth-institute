# 独立服务端注释存储与附件接入提案

新增 `management_portal/paper_reader_annotations.py` 及 `tests/cases/test_paper_reader_annotations.py`。未注册 HTTP 路由、未连接原型浏览器、未修改现有 Reader、paper_store/resource_index_store/paper_search/paper_library 或数据节点文件，未迁移生产数据、未发布。

## 实现边界

`AnnotationStore(authenticate=...,resolve_attachment=...)` 必须显式提供两项可信依赖。调用方传入正式认证上下文；authenticate 负责从该上下文取得稳定 principal，不能把 HTTP payload 或任意字符串直接当作 principal。测试上下文为夹具账号，仅用于测试。resolve_attachment 每次读写重新检查权限及请求选择器，返回冻结 `Attachment(paper_id,attachment_id,attachment_version)`；返回与请求不符会冲突，未知版本由解析器拒绝。没有默认生产解析器，Python dataclass 本身也不是鉴权证明。

存储仅复用 `DomainDatabase('paper','reader_annotations',DDL)` 与公共 PostgreSQL 连接管理，不创建连接池或另设 DSN。首次实际调用时才初始化本职责三表：reader_annotation_scopes、reader_annotations、reader_annotation_operations。业务 scope 有账号/Paper/附件/版本唯一约束；记录和操作回执按 scope 外键隔离。使用现有 paper schema，不需要修改共享 schema 注册、现有 Paper 表或数据库入口。

每批 INSERT scope 后 SELECT FOR UPDATE 锁该 scope，检查操作回执早于 revision。相同规范 JSON 正文和 operation_id 返回原确认，即使 expected_revision 已过期；不同正文返回 Conflict(status=409)。规范 JSON 对象键排序，数组顺序仍属于请求正文。随后检查 revision CAS，删除墓碑、完整 upsert、新 revision 和操作回执在同一事务提交。删除不存在 ID 仍生成墓碑；旧 revision 不能复活该记录。未来用户显式使用当前 revision upsert 同 ID 视为新编辑。当前不实现自动合并或冲突副本。

JSON 上限：256 KiB 编码后请求、16 层深度、单字符串 64000 字符、单集合 2048 元素、单批最多 256 项操作；拒绝非有限数、过大数、NUL、非 JSON 类型、重复 ID、同批 upsert/delete 同 ID、非法页码及自报 owner。支持限定原生注释类型，position.pageIndex 必须为非负整数。其他原生 JSON 字段在上述边界内原样保留，不静默裁剪。版本描述符尚无实际页数，不能据此校验页码小于文件页数；几何形状的完整格式校验仍待真实 Reader schema 接入。存储的是数据，不执行 HTML/script。

该入口只接受完整 upsert，拒绝 `onlyTextOrComment`：正式宿主必须在传输前基于已确认完整记录 materialize 此类局部更新，不能清空 geometry/image。当前 JS 原型 transport 尚未与模块连接，其草案字段基本一致，但没有实现该 materialization；不能据两侧单测宣称端到端通过。注释与 receipts 暂无清理策略或分页；上线前必须明确附件归档、回执保留期和查询上限。内部返回读/写 document、revision、acknowledged_operation_id 与草案兼容。

## 真实临时 PostgreSQL 结果

按数据库设计 `decision_86aed2f0e8c7`、公共 DomainDatabase 和 tests/cases/conftest.py 惯例，pytest 启动自有临时 PostgreSQL 集群及 namespace，拒绝连接生产地址；本轮没有使用真实业务账号或数据库。首次系统 python 缺 pytest，改用 `management_common.code_environment.environment_key` 所指定依赖环境：

```
/opt/tfi/the-fourth-institute-dev/dependencies/c121e8e7001cea66d33ef7900ecae39c98e6a7aa22582cee6867a8d3126cf89d/venv/bin/python -m pytest -q tests/cases/test_paper_reader_annotations.py
```

最终命令退出 0：`4 passed in 0.78s`。测试验证：同 operation 重放、不同正文拒绝、CAS 冲突、两个线程使用独立事务争同 scope 仅一人成功；真实 COMMIT 后丢弃响应再重放；delete 后 upsert 触发真实 PG CHECK 约束失败，整个批次回滚、revision 不变且没有回执；账号和附件版本隔离；墓碑与鉴权/描述符拒绝；有限 JSON 与整体大小上限。未重跑已通过的浏览器、状态机或 mock transport 测试，未跑全量门禁。

## paper_detail / http_paper 附件描述符最小实现提案

真实入口仍是 `paper_detail.get_paper_detail` 返回 PDF URL，`http_paper` 调用 `paper_detail.resolve_asset` 得到当前 Path/content-type，文件定位由 Paper 负责者提供。本模块不会用浏览器自报 version 建立可信附件身份。

建议由 Paper 附件负责者新增自己管理的附件版本登记表，字段为稳定 attachment_id、paper_id、active_version、存储 locator；版本行含随机不可变 version_id、内容 sha256、字节数、已验证来源 stat 指纹和生成时间。attachment_id 在 PDF 替换时保持，version_id 随实际内容变化更新；路径或 mtime 不直接充当版本。

受控下载/上传/替换流程将 PDF 写入 staging 文件时流式计算 sha256，完成后原子发布不可变版本文件，再在 Paper 负责者事务中推进 active_version；原文件引用与旧版本按生命周期保留。这样详情与资产读取不必每次全量重读 PDF；响应携带附件 ID、版本、内容摘要，下载 URL 绑定版本，不能继续用可变路径兑现旧版本 URL。跨文件与数据库失败由明确 staging 状态和幂等恢复任务处理，不假设文件 rename 与 PG 同事务。

对外部直接覆盖现有 PDF：轻量 stat 指纹至少含 device/inode/size/mtime_ns/ctime_ns；变化即标记 descriptor pending，后台流式哈希一次，打开文件前后 fstat 一致才能发布。检查期间不得把旧坐标绑定新文件。文件 watcher 用于失效提示，stat 用于读取时低成本复核。任意外部写入可绕过或恢复元数据，单靠 stat 无法严格保证实际内容身份；若要求可靠不可变版本，必须将材料导入由负责者管理的不可变文件，或者在显式维护中重新校验内容，不能宣称仅靠 mtime 已证明字节一致。不得让读取路径每次扫描全库或哈希整份 PDF。

缓存按 attachment_id/version_id/content digest 键控；活跃版本变化使详情 active pointer 及可变 URL 缓存失效，固定版本资源可使用内容 ETag。HTTP Range 和全文响应必须来自同一个固定打开的版本文件；路径替换时继续服务旧已固定版本，不能一半旧字节一半新字节。旧版本不可访问时明确返回 stale-version，不静默切到新文件。

`resolve_attachment(context,selector)` 应调用该负责者：验证 Paper 可读、附件确实属于该 Paper、版本被保留且可读，并返回服务端真实描述符。当前若只允许 active_version，应对旧版本返回冲突；若允许保留旧注释则须明确旧版本的读取权限。接口可在 paper_detail 增加只读 descriptor 字段，http_paper 增加版本选择/校验，但不在本轮实施；需要协调新增附件登记 owner 和受控替换入口，不能仅加两个响应字符串。

尚未验证实际生产解析器、PDF 替换检测、资产缓存失效、HTTP 认证、真实多用户客户端集成、数据迁移和升级回退；这些仍阻止生产可用声明。翻译仅保留原草案，未触发模型调用。
