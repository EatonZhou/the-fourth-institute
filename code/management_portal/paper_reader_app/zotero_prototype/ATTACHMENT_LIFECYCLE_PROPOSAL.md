# 附件生命周期最小接入提案（待决策）

本提案尚未实施，不新增文件副本或迁移现有论文。

1. 首次导入：Paper 附件负责者鉴权并选定单个现有 PDF，复制到受控 staging，复制时流式计算 SHA-256；前后检查文件身份与 stat，变化则重试。原子发布不可变版本文件，附件登记事务创建稳定 attachment_id、version_id、内容摘要及 active pointer。失败 staging 清理须可重入；登记失败留下可恢复任务，不删除原文件。先按明确样本范围导入，不全库扫描或复制。
2. 替换：沿用 attachment_id，新文件走相同 staging/摘要流程，创建新不可变 version_id，再以 expected_active_version CAS 推进 active pointer。旧版本文件和注释原样保留；新版本不能默认使用旧坐标。响应/通知在事务提交后使详情和可变资产 URL 缓存失效；版本 URL/ETag 指向固定字节，不在每次读取时重新哈希整份 PDF。
3. 回滚：校验旧版本仍存在且可读，以 CAS 将 active pointer 指回旧版本，发同样失效通知。保留替换后的新版本及其注释；失败不偷偷重指路径。跨文件发布/数据库提交的中间状态由附件 owner 的恢复任务处理。

需要协调的现有 owner：

- Paper 文件定位与受控导入/替换入口：提供附件版本登记的唯一写 owner；需要改动现有资产下载/上传完成入口及文件定位接口，具体文件由负责这些入口的数据节点确定。本节点不编辑 paper_store 或其调用方。
- `paper_detail.get_paper_detail`：只读输出正式附件 descriptor；`paper_detail.resolve_asset`：按 version_id 定位固定版本，并校验 Paper 关联。
- `http_paper` 资产入口：校验权限和版本，Range/完整响应使用同一固定版本文件，设置内容 ETag；不让旧版本 URL 返回新文件。
- 新注释模块的可信 resolver：从附件 owner 查询描述符与权限，明确旧版本是否允许继续读写，不接受浏览器自报版本当作真值。

决策前需确认副本目录、容量预算、旧版本保留与删除条件、外部原文件是否继续可写，以及恢复任务 owner。若允许外部修改现有路径，watcher/stat 只能触发重新校验，不能证明元数据相同就字节相同；可信版本必须来自受控不可变副本。未授权前不开始导入、替换或回收。
