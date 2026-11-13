# 局部更新与真实存储集成

本切片修改原型 `persistence.js`、独立 `paper_reader_annotations.py`，新增 `test_paper_reader_bridge.py` 和 `javascript/zotero_reader_pg_bridge.mjs`。未注册正式路由，未改数据节点文件，未导入/迁移附件或发布。

## 完整记录合成

transport 在 load 后保存已确认完整记录，成功 commit 后才更新基线或移除删除记录。局部更新意图在调用时快照，按回调顺序排队；轮到它时先确认任何未确认旧操作，再基于此时最新已确认记录合成。`onlyTextOrComment:true` 只更新显式提供的 text/comment/dateModified，保留基线 position/image/tags 及其他字段，不采纳局部回调携带的旧几何或 tags。完整 upsert 去除 onlyTextOrComment 标志后按完整记录提交。无基线（包括已删除）明确 reject，不推测坐标或重建记录。

旧操作响应丢失时保存原请求字节；先使用原 operation_id 和完全相同正文重放，确认后才将新的局部意图合成为新 operation_id 请求。冲突不更新 confirmed、不自动合并，保留未确认请求及 Reader 的待存修改。已确认缓存只存于当前 scope 的实例；切换后不共享。

## 几何校验依据

固定 Reader 提交 `04795bfbafd55600e042d62e05f9a4147458ae53`：`src/pdf/pdf-view.js` 的 note 创建分支约 3408–3426 行输出 `position:{pageIndex,rects:[[x1,y1,x2,y2]]}`；`src/common/annotation-manager.js:updateAnnotations` 保留原 position 字段并处理跨页 nextPageRects，保存循环为高频文本更新附加 onlyTextOrComment。PDF.js/Reader 的矩形采用 PDF 页面坐标，不是 0–1 归一化矩形，裁切或贴边便签可能出现负坐标，因此不任意裁剪或归一化。

服务端对 highlight/note 验证 rects 为 1–500 个四元有限数矩形，每轴严格正尺寸，数值绝对值不超过 1e7；note 仅单页单矩形；highlight 可带 nextPageRects，形状同样验证。pageIndex 是非负整数；可选 rotation 只接受整数 0/90/180/270；这些类型的未知 position 字段明确拒绝，不静默剔除。真实页数、crop box 与实际 PDF 内容匹配仍依赖可信附件解析器，当前不能验证；其他已接受类型只受现有通用 JSON/pageIndex 限制，尚无完整几何验收。扩大 Reader 输出格式前须显式扩展校验。

## 有界读取与回执生命周期

读取上限为每个 actor/Paper/附件版本 4096 条记录（包括墓碑）和 4 MiB 序列化数据预算。持有 scope 行锁时先在 PG 聚合 count 与文本字节量，再读取记录；超限明确失败，不返回被截断却貌似完整的快照，避免客户端根据残缺基线覆盖数据。读取后再次检查返回预算。该上限是原型完整快照保护；未来需要服务端分页及固定 revision 快照协议后才能提高规模能力。

operation receipts 与墓碑当前不自动过期或清理。幂等有效期为 scope 保留期间，同 operation_id 必须永久返回原确认或正文冲突；不能仅因“时间够久”删回执后重新解释为新操作。将来若要归档，需先定义 scope 封存及客户端请求可重放期限、确认不存在旧在途请求，并对封存 scope 的迟到请求明确拒绝，而非删除回执后放行。未实现这套归档协议前保留全部回执；磁盘增长是上线前需解决的容量条件。

## 实际验证

使用工程依赖环境运行：

```
/opt/tfi/the-fourth-institute-dev/dependencies/c121e8e7001cea66d33ef7900ecae39c98e6a7aa22582cee6867a8d3126cf89d/venv/bin/python -m pytest -q tests/cases/test_paper_reader_annotations.py tests/cases/test_paper_reader_bridge.py
```

最终退出 0，`6 passed in 1.39s`。pytest 使用已有 fixture 启动真实临时 PG，不连接生产数据库。隔离 loopback HTTP 桥只存在于测试线程，服务端闭包绑定唯一认证上下文，JS 不提交 actor；桥调用真实 AnnotationStore。验证 load→评论局部更新、geometry/image/tags 保留、删除与墓碑、真实提交后丢响应再合并新编辑、顺序 full→partial、无基线拒绝、CAS 冲突、payload owner 拒绝；PG 直接负例覆盖倒置/空/布尔坐标、非法旋转、未知位置字段、note 跨页及读取行数/字节超限。因 transport owner 源码改变，补跑其直接回归 `node tests/cases/javascript/zotero_reader_transport.mjs`，退出 0；未重跑已过浏览器测试。

仍未连接真实附件解析器、正式 HTTP 认证或模型，不宣称生产可用。附件首次导入/替换/回滚的待决策方案见 `ATTACHMENT_LIFECYCLE_PROPOSAL.md`。
