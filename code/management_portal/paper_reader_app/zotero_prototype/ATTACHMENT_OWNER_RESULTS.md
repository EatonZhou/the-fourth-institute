# 单篇附件 owner 隔离实现

新增 `management_portal/paper_reader_attachments.py` 和 `tests/cases/test_paper_reader_attachments.py`。没有修改 paper_store、http_paper、现有上传/下载入口或数据节点文件，没有创建真实论文副本、扫描论文库、迁移或发布。

## 既有能力与归属

已核对当前 Paper 生命周期规范 `decision_528bf9985705`、目录协议 `decision_4aa13bc21e8e`。现有 `paper_detail.resolve_asset` 解析可变 PDF 路径，Paper 入库以 `%PDF` 文件头检查及 512 MiB 上限保护；未发现可直接复用的 Paper 不可变版本登记 owner。`society_service/workspace_attachments.py` 属于对话/Resident 附件授权，不能据此创建另一套 Paper 身份。新 owner 复用已有 `_has_pdf_header`、`create_private_file`、`sync_directory` 和 DomainDatabase，不复制其公共职责。

构造必须显式传 `root`、稳定 `storage_key` 和可信 `authorize(context,paper_id,write)`。root 是部署配置给定的专用资料目录，不从 cwd、Paper 名称或另一个实例猜路径；测试只传 tmp_path。生产位置可由 Paper owner 配到其 bundle 内（区域表示为 `papers:items/.../<bundle>/reader-attachments/`），具体目录和容量未决策，本模块不选默认生产根。数据库只保存 storage_key 和根下相对文件路径，不保存实例绝对路径。storage_key→root 必须由部署保持稳定；不能把相同 key 随意改指另一个根。

版本文件与恢复 journal 属于 Paper 对象资料，旧版本不自动回收；Paper 整体删除时仍须接入正式对象注销/文件清理流程，不因版本引用而永久保留。本切片未接该生命周期。staging 为单次导入临时文件：复制错误立即删除，prepared 后成为有恢复 journal 的待发布材料；进程中断后由显式 recover 处理，不开全局扫描/清理线程。锁文件是恢复同步元数据，不在持锁时 unlink，随所属对象清理。

## API 与状态转换

- `import_pdf(context,paper_id,source,expected_version,operation_id)`：operation_id 为内部 32 位随机十六进制任务键，不是正式 Portal 资源 ID。调用方必须提供已获准读取的 source 路径，未来 HTTP 入口不得把任意浏览器路径直接传入。authorize 要求 Paper 写权限并返回正式稳定 actor；不是凭请求自报 owner。
- 导入 journal 在复制前事务记录 copying。流式复制到 staging 并计算 SHA-256，检查 regular file、文件头与大小，复制前后对打开文件 fstat 和来源路径 stat 的 device/inode/size/mtime_ns/ctime_ns；变化时拒绝发布。正常完成 fsync 文件和 staging 目录，事务记录 prepared/摘要/字节数。
- prepared 恢复用独占 hardlink 发布只读版本文件，不覆盖旧文件，fsync 版本目录；之后以单篇数据库锁/CAS 登记版本并推进 active pointer，在同一事务将 journal 标 done。每个 operation 的 flock 防止另一进程在复制中误恢复。显式参数根必须在支持 POSIX flock/hardlink 的本地挂载文件系统，staging 与 versions 在同一根；跨平台/不支持挂载未验证。
- `recover(context,operation_id)`：只恢复一个已知任务。copying 被中断时丢弃未完成 staging 并标 failed，要求新显式导入；prepared 或已发布但登记失败时无需源文件即可继续；done 幂等返回原版本，即使 active 后来已改变。重新鉴权先于文件锁创建。CAS 冲突保留 prepared 文件/journal，不擅自覆盖当前版本；后续只有调用者明确恢复且 expected 状态再次成立才可完成。
- `current(context,paper_id)` 读取当前描述符；`resolve_attachment(context,selector)` 检查 Paper 读取权限、附件归属、版本登记和文件存在，可作为 AnnotationStore 的可信 resolver。保留的旧版本可读、可绑定注释，不自动改写为 active。`open_version` 打开固定版本文件，路径不经可变 active pointer。
- `rollback(context,paper_id,attachment_id,version,expected_version)` 对已登记旧版本做 CAS pointer 切换，不复制文件、不删除新版本。版本 identity 与内容保留。

任务重放绑定 paper、actor、storage_key、expected_version 和来源 locator 的哈希；换来源路径或参数必须用新 operation_id。已准备/完成任务的重复调用恢复原来冻结的材料，不能用同 operation_id 要求重新读取已经变化的来源；新文件内容导入必须新任务。`recover` 不需源文件继续存在。

文件在 owner API 中不覆写，权限为只读；这不是防管理员/同 UID 非受控进程篡改的硬件不可变保证。恢复检查发布文件大小，不额外进行全量哈希重读；摘要来自首次流式复制。来源 stat 检查检测常规写入/替换，无法证明对抗性写入并恢复全部元数据不存在；严格接入应使用负责者独占/稳定的上传来源。当前不宣称所有掉电、磁盘故障或恶意修改可恢复。

## 注释接入方式

```
owner = AttachmentOwner(root=configured_root, storage_key=configured_key,
                        authorize=trusted_paper_authorizer)
store = AnnotationStore(authenticate=trusted_principal_from_context,
                        resolve_attachment=owner.resolve_attachment)
```

版本登记采用 paper schema 下独立 reader_attachment_heads/versions/imports 表，不修改共享 schema 注册或现有表。没有创建新全局后台框架。上游资源和 Paper 权限仍由注入的可信 authorizer 核验；测试 authorizer 只接受服务端固定 context。

## 实际验证与限制

最终执行 `/opt/tfi/the-fourth-institute-dev/dependencies/c121e8e7001cea66d33ef7900ecae39c98e6a7aa22582cee6867a8d3126cf89d/venv/bin/python -m pytest -q tests/cases/test_paper_reader_attachments.py`，退出 0：`8 passed in 0.98s`。使用既有 fixture 的临时 PG 和 tmp_path。验证首次导入、替换与 CAS 回滚、固定旧版本字节、旧/新版本注释隔离、未授权拒绝；prepared/published/before_commit 三处故障后重建 owner 并在源文件已删除时恢复；源文件中途变化拒绝；复制异常及 copying 模拟进程中断恢复；两个 writer 争同 active CAS 仅一个成功；同任务重放与换来源拒绝、非 PDF 拒绝。没有重跑既有浏览器或 transport 等价测试。

尚未验证正式授权器/区域绑定、生产容量/保留策略、Paper 删除接入、实际 HTTP/Range/ETag 缓存失效、任意挂载点行为及真实上传并发。替换通知/缓存仍未接入，因此正式替换尚未获实施决定。生产接入前必须决定 root/storage_key、容量和 whole-object 清理 owner，本模块不会默认迁移或回收。
