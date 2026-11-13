# 原型持久化 transport 适配

新增 `persistence.js` 的 `createPersistence({scope,transport,operationId,status})`，没有生产 URL、路由注册、凭据查找或翻译调用。接口依照草案而非现有服务能力；只有提供完整 scope 与匹配身份的 transport 才能创建会话，不从文件名、Paper ID 或 fingerprint 猜附件 ID/版本。

scope 为 `{actorKey,paper_id,attachment_id,attachment_version}`，创建时复制并固定。actorKey 仅是本地身份隔离标签，不发送为请求 owner，也不构成认证凭证。transport 提供固定身份的 `{actorKey,read(document,context),commit(batch,context)}`；实现必须使用该会话绑定的认证，不能在重试时读取可能已切换账号的全局凭据。未来若使用随浏览器账号变化的 cookie，服务端必须核对预期 principal 的会话绑定，不满足则拒绝；本原型无法用一个字符串证明真实认证隔离。

read/commit 响应必须含精确 `{document:{paper_id,attachment_id,attachment_version},revision:string}`，commit 还需返回 `acknowledged_operation_id`。账号由认证服务核验；客户端校验响应附件身份、版本和操作确认，不把错文档响应安装到本会话。响应缺字段、确认错配、网络故障均保留未确认请求。

接现有原型宿主时可传入：

```js
const persistence = createPersistence({scope, transport, status});
const host = await mountReader(frame, {
  load: async () => ({...await persistence.load(), pdf: await loadBoundPdf(), author}),
  save: persistence.save,
  remove: persistence.remove,
  savePosition,
  status,
});
// 正常切换：先 await host.close() 成功，再 dispose，再创建新 scope。
// 账号失效/强制切换：保留旧 Reader 待存修改，dispose 旧会话，另建新会话；
// 不能把旧会话 snapshot 当作新文档的初始化内容。
```

本轮未改 index.html 的本地存储演示，未把草案 transport 接到真实服务。

## 请求与失败语义

每次回调在调用时复制为规范 JSON 意图，内部队列串行提交。delete 回调完成后才执行 save，分别推进 revision。空回调不产生写请求。批次第一次发出时分配 operation_id 并固化请求字节，包括 expected_revision；重试重新解析同一字节，transport 修改参数也不会改变保留副本。

响应丢失时保留未确认批次。后续回调先重放该精确批次以确认旧提交状态；相同意图确认后直接返回，变化后的意图必须用新 operation_id 和确认后的 revision 发送。旧状态未确认期间不会把新正文塞入旧 operation_id。部分成功时删除可再次幂等提交，但旧 save 必须先确认，之后才发送更新内容。

409 永久暂停当前会话写入，保留 pending 与 status 提示，不自动刷新、合并或创建冲突副本。Reader 收到 reject 后保留待存修改。显式冲突解决流程尚未实现；调用 load 也不能在已加载会话里覆盖 revision/待存状态。snapshot 提供诊断与显式导出材料；没有跨刷新持久化请求日志。

dispose 后未发出的队列操作拒绝，迟到 read/commit 响应在更新 revision 前被拒绝。已到服务端的旧请求可能仍对旧文档提交，不能声称 dispose 撤销了事务；snapshot 保留其未确认请求，恢复旧文档后仍需按原操作 ID 查明状态。新账号/附件版本必须新建会话，旧响应不触达新 Reader。

## 服务端附件能力核对

已检查当前 `paper_detail.py:get_paper_detail`，正式详情输出的是 `/paper/api/asset?id=<paper_id>&kind=pdf`。`http_paper.py` 的资产处理调用 `paper_detail.resolve_asset`；后者对 PDF 使用 `_paper_store_file(paper,"pdf")` 或 `pdf_path`，返回 `(Path,content_type)`。`paper_store.py:_paper_public` 当前提供 has_pdf、路径等公共记录字段，没有在这些 owner 中发现正式附件 ID 或不可变内容版本描述符；对 `paper*.py` 搜索 `attachment_id/attachment_version/pdf_sha256/pdf_fingerprint` 亦未发现此字段契约。

因此真实 owner 分工是：Paper 详情/资产定位由 `paper_detail.py` 与 `http_paper.py` 提供，Paper 记录/文件定位由 `paper_store.py` 提供，旧个人高亮身份范围由 `paper_highlights.py` 提供。当前缺口是服务端发行和校验稳定附件 ID、内容替换产生的不可变版本、以及注释 revision/operation 去重存储；原型不自行创建这些服务端真值，也不修改这些 owner。浏览器 SHA-256/PDF fingerprint 只能作内容证据，不能代替正式附件描述符。

## 直接验证

实际命令 `node tests/cases/javascript/zotero_reader_transport.mjs` 退出 0。内存服务端先查 operation receipt 再校验 revision，模拟响应丢失与提交前失败，验证：

- 同 ID 同正文精确重放；内容改变先确认旧请求，再新 ID 提交。
- 删除成功/save 失败后上游重放两回调，记录不会复活，合并后的新内容保存正确。
- revision 冲突不覆盖，后续回调不再发送，pending 保留。
- 并发调用 remove/save 仍按顺序执行且无并发 writer。
- 账号、附件 ID、附件版本的命名空间隔离；输入对象后续变化不改变请求。
- 延迟旧 commit、延迟旧 read、排队未发请求在 dispose 后均拒绝；新文档 revision/记录保持独立。

未重跑已通过浏览器、保存状态机与布局测试。未验证真实 HTTP、数据库并发、真实认证会话绑定、跨刷新请求日志、离线恢复或冲突解决 UI；不因此宣布生产可接入或正式替换。
