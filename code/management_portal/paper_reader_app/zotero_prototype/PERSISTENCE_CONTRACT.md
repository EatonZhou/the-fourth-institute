# 正式宿主持久化最小契约草案

以下路径和字段是待实现提案，当前原型没有连接这些 API。未修改数据节点源码。

## 现有能力核对

已查 `paper_library.py` API 清单、`paper_highlights.py`、`paper_notes.py`、`paper_state_store.py`，并在 Portal Python 源码搜索翻译缓存入口。

`GET /paper/api/highlights?id=&document=` 与 `POST /paper/api/highlights` 已按认证 actor、Paper、PDF fingerprint 隔离并检查资源可读性，可复用鉴权原则。该存储仅接受 UUID 形式高亮 ID、非空 text 和归一化矩形；native 仅接受 PDF.js annotationType=9，不能直接承载 Zotero 的八字符 ID、空 text 便签、原生 position/comment/tags。现有逐条 add/delete 返回 id/operation，没有记录 revision 或乐观并发契约。不要将 Zotero 数据塞入该 native 字段，也不要修改旧记录身份来迁就校验。

`paper_notes.create_note` 保存 Paper markdown note，并有 created_by；它的当前接口不构成阅读注释的账号隔离、附件版本与坐标契约。现有 deep-read/summary/review 任务不等价于翻译缓存。上述检索未发现已声明的 Paper 翻译缓存 API；若数据节点新增能力，应按其实际契约复核复用，不另造同语义存储。

## 注释读取与原子批次提交

建议 `GET /paper/api/reader-annotations?paper_id=...&attachment_id=...&attachment_version=...` 返回 `{format:"zotero-reader-v1", reader_commit:"04795b...", revision:"17", annotations:[...], tombstones:[{id,revision}], document:{attachment_id,version,sha256,pdf_fingerprint}}`。页码采用原生 pageIndex，从零计数；保存原生 position 坐标、旋转及 comment，不转换为旧归一化高亮。列表只查询目标账号/附件版本，不扫描全库。

建议 `POST /paper/api/reader-annotations/batch`，认证账号由服务端取得，拒绝客户端指定 owner。请求示例（省略的 sha256、附件版本均由真实附件描述符提供）：

```json
{"paper_id":"paper_0123456789ab","attachment_id":"server-issued-attachment-key","attachment_version":"immutable-version-token","expected_revision":"17","operation_id":"client-session-unique-batch-42","format":"zotero-reader-v1","upserts":[{"id":"ABCD2345","type":"note","text":"","comment":"个人便签","color":"#ffd400","sortIndex":"00002|000001|00000","tags":[],"pageLabel":"3","position":{"pageIndex":2,"rects":[[100,500,122,522]]}}],"deletes":["EFGH6789"]}
```

稳定键为 `(authenticated_actor,paper_id,attachment_id,attachment_version,annotation_id)`，annotation ID 由 Reader 生成并原样保存，重开不重新分配；它是附件内注释键，不是 Portal 统一资源 ID。附件 ID 也必须取正式附件返回值，示例字符串不代表资源登记。PDF 内容替换必须生成新版本，即使文件名和 Paper 不变；服务端绑定其 sha256/fingerprint，不相信客户端任意声明的指纹。版本不一致拒绝写入并返回实际描述符，不把旧坐标套到新文件。

一批 upsert/delete 同一事务提交，成功返回 `{revision:"18", acknowledged_operation_id:"...", saved_ids:["ABCD2345"], deleted_ids:["EFGH6789"]}`；响应只在数据库提交完成后发送。同批同 ID 同时 upsert/delete 返回 422。操作 ID 在 actor+附件版本范围去重：同 ID 同正文重放返回原确认，不同正文返回 409。去重检查先于 expected_revision 检查，保证响应丢失后可重试。删除不存在记录也成功，保留墓碑以阻止旧页面写回复活。

注释本身 JSON 需限长、有限数字坐标、合法页码/类型和格式版本校验；禁止可执行 HTML 或外部凭据；authorName 仅展示，不作权限。图片若过大改为经认证受控 blob 引用，避免无限放大批次。正文不丢未知字段：明确允许字段或拒绝不支持格式，不静默裁剪导致丢失。

账号 A/B 打开同一 Paper 只读取各自注释，annotation_id 碰撞不共享记录。每次读取/写入都重新检查 Paper/附件可读性。401/403 不当作已保存；版本或 revision 冲突返回 409，不自动覆盖；422 显示具体记录问题；网络/5xx 保留内存待存队列并用同 operation_id 重试。404、权限丢失时保留本地修改并阻止关闭，提供显式导出后由用户决定处理。

宿主为单附件单 writer，收到下一批原生回调时顺序提交，只有持久化确认后 resolve。两个页面 revision 冲突时重新读取：独立 ID 可重放，冲突 ID 显示用户选择或保留冲突副本，不能自动采用最后到达者。上游 delete/save 是两个依次 await 的回调，最小宿主可各提交一个幂等批次，各自推进 revision；失败后重放必须复用原操作 ID。`onlyTextOrComment` 是局部更新提示，服务端不得据此清掉已有图片/geometry；宿主应显式区分 patch 与完整 upsert。本补丁失败重试会发送完整记录。

阅读位置建议独立按同一 actor/附件版本保存，失败不能伪报为已同步。注释 flush 只保证注释回调；当前原型的位置错误只作 status，正式宿主若承诺恢复阅读位置，需独立等待并传播位置保存失败。

## 翻译缓存与调用草案

建议 `POST /paper/api/translations` 输入 `{paper_id,attachment_id,attachment_version,source:{kind:"selection",text:"..."},source_language:"en",target_language:"zh-CN",model_route:"server-model-route",prompt_version:"translate-v1",terminology_version:"...",context:{title:"...",abstract:"..."},refresh:false,operation_id:"..."}`。不允许浏览器传 API key 或自定义 provider endpoint。服务端解析当前实际 provider/model 配置版本并构造规范键，不能仅按可变路由名缓存。

键包含：精确原文、源/目标语言、实际服务/模型与模型设置版本、提示词正文版本、术语内容版本、实际发送上下文，以及访问范围。私人选区/便签默认加入 actor；共享标题摘要只有经服务端确认为共享原文后才按有权访问的共享范围复用，不能由客户端选择 shared 绕过隔离。规范化不改变原文空白含义；哈希不是权限凭证。

已成功缓存命中返回 200 `{status:"succeeded",translation_id:"opaque-id",text:"...",cache:"hit",actual_model:"...",source_digest:"..."}`。未命中/相同在途请求返回 202 `{status:"running",request_id:"opaque-id",cache:"miss|inflight",status_url:"..."}`；状态查询仍认证并检查原始范围权限。持久化成功文本后才标为 succeeded；失败仅保留任务错误，不成为有效缓存。服务端以键唯一约束加租约合并并发，进程恢复后能识别过期任务，避免无限 pending。

`refresh:true` 明确绕过成功缓存但合并同一代在途重译，失败不得覆盖旧成功结果；新成功代次用 CAS 更新有效指针。同 operation_id 重放不得启动第二次模型调用。返回 cache 字段与实际调用 usage/request 标识供验收，不向其他账号泄露私人原文、命中状态或在途 ID。

译文保存到个人便签是独立确认操作，引用 translation_id 与源 annotation_id、原文 digest，不能自动覆盖原文/comment。服务端复验译文可读性及源版本。全文双语排版、Note Editor 不在本契约范围。
