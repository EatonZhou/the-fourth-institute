# Zotero Reader 接入检验原型

当前正式全屏接入与发布验收以 FULLSCREEN_INTEGRATION_RESULTS.md 及 RELEASE_ACCEPTANCE.md 为准；下文为分阶段历史证据。固定上游、独立保存 patch、build.sh 与 upstream/COPYING 必须同构建交付。翻译实验不属于本次 Reader 发布。

此前阶段总交付、证据、正式接入层级与阻塞责任见 [INTEGRATION_REVIEW.md](INTEGRATION_REVIEW.md)，该阶段逐文件快照见 [DELIVERY_FILES.txt](DELIVERY_FILES.txt)。当前继续推进正式接入实现，尚未切换生产阅读器。

后续已获授权继续独立 Reader HTTP/服务装配，实现与最新接线状态见 [HTTP_INTEGRATION_RESULTS.md](HTTP_INTEGRATION_RESULTS.md)。历史“评审暂停”已解除，生产切换仍未实施。

删除与 CSRF 的最新契约以 [DELETION_RECOVERY_RESULTS.md](DELETION_RECOVERY_RESULTS.md) 为准：正式接线使用 delete_with 包装唯一注销调用，maintenance 使用有界 recover_pending_deletions。

当前原型维护固定上游的小范围保存补丁 `save-contract.patch`，只改 `src/common/reader.js` 与 `src/common/annotation-manager.js`。原始固定版本的阻塞证据见 [保存契约提案](SAVE_CONTRACT_PROPOSAL.md)；该提案描述补丁前基线。当前实现与验证见 [补丁验收](SAVE_PATCH_RESULTS.md)。未进入生产接入。

后续独立切片已完成[多页布局与远程保存模拟验收](LAYOUT_AND_REMOTE_RESULTS.md)，正式宿主接入前参考[持久化与翻译缓存契约草案](PERSISTENCE_CONTRACT.md)。草案 API 尚未实现。

可注入 transport 的原型适配器与直接测试见 [TRANSPORT_RESULTS.md](TRANSPORT_RESULTS.md)，包含请求重放、冲突暂停与文档切换隔离；未绑定生产服务。

独立服务端注释存储及真实临时 PG 测试见 [SERVER_STORAGE_RESULTS.md](SERVER_STORAGE_RESULTS.md)，附服务端附件描述符接入提案；仍要求注入可信认证和解析器，未注册 HTTP 路由。

局部更新合成与 JS→真实 AnnotationStore→临时 PG 集成结果见 [MATERIALIZATION_RESULTS.md](MATERIALIZATION_RESULTS.md)，包含读取上限、receipt 保留语义和附件生命周期待决策提案。

新增单篇不可变附件 owner 与隔离验证见 [ATTACHMENT_OWNER_RESULTS.md](ATTACHMENT_OWNER_RESULTS.md)。根目录、授权器显式注入，没有绑定生产文件入口或迁移真实 PDF。

独立翻译缓存/在途任务切片见 [TRANSLATION_CACHE_RESULTS.md](TRANSLATION_CACHE_RESULTS.md)：临时 PG 与假执行器验证，未接 canonical 模型执行或生产入口。

canonical 执行适配最小切片见 [TRANSLATION_EXECUTION_RESULTS.md](TRANSLATION_EXECUTION_RESULTS.md)：复用共同 Execution 身份/提交契约做隔离验证，正式 translation 角色及规划器缺失时保持关闭，未知提交状态只观察不重投。

仅阶段 A 的隔离实验，不接正式 Paper API。入口 `index.html` 选择本地测试 PDF，按文件 SHA-256 将原生 annotation 和 position 保存到浏览器 localStorage；不提供多账号隔离。不要用私人生产材料。Note Editor 不引入。

固定上游：

| 来源 | 提交 | 许可证 |
|---|---|---|
| https://github.com/zotero/reader | 04795bfbafd55600e042d62e05f9a4147458ae53 | COPYING: AGPLv3 |
| https://github.com/zotero/pdf.js | 5f95f712bc629ac11159cb4859ed8091fdffa87b | Apache-2.0（构建内保留 LICENSE） |
| https://github.com/zotero/epub.js | e68c8b9c868be6ccb5b9684213117e95e52ef880 | 随子模块保留许可 |
| https://github.com/zotero/structured-document-text | 7963c11faac4595be3bbe86b7574704a22c67bed | 随子模块保留许可 |
| https://github.com/windingwind/zotero-pdf-translate | 4e9ca763b9d8f07604aceb7671b5e0c29c3b5a3a | LICENSE: AGPL-3.0 |

Reader 的 `src/index.web.js` 提供 `window.createReader`，内置 React 18 与 Fluent。PDF 使用它自己的 PDF.js 子模块构建，不能替换为 Portal 现有 Viewer。构建要求 Node 18+、npm、webpack、Sass；locale 插件从固定 Zotero 提交 a6a919234a360f5cc0f8dbf45b84e1fafc546f41 下载 Fluent 文本。运行需要同源 iframe、TypedArray、Worker、字体、cmaps/wasm 资源与匹配 CSP。无需桌面 Zotero 全局。原始接入未改上游；当前保存补丁由独立 patch 管理；宿主只调用公开 createReader/navigate 和回调，通过移除 iframe 释放整个全局环境。

从固定提交的干净递归克隆执行 `bash build.sh /path/to/reader-checkout`，先应用独立 patch，再安装锁定依赖、构建 PDF.js/Web Reader，将生成的 `build/web/` 同步为本目录 `upstream/`，并保留 COPYING。构建产物由命令生成，不手工改写。该目录没有正式页面路由，可用本地静态 HTTP 服务打开此原型目录。

宿主契约：`load()` 返回 PDF Uint8Array、原生 annotations、primaryViewState、author；`save(annotations)` 按原生 id upsert 完整 JSON；`remove(ids)` 删除；`savePosition(state)` 保存位置。正式接入必须由账号、Paper ID、PDF fingerprint 确定存储命名空间，保留原生结构及版本，不压平为旧矩形字段。`locate(id)` 调用 `navigate({annotationID:id})`。

补丁后的状态机、宿主与 Firefox/Chromium UI 结果见 `SAVE_PATCH_RESULTS.md`；首轮初始化 smoke 的历史结果不作为当前保存验收依据。

补丁前缺口：原固定版本 AnnotationManager 延迟交付修改且公开 Reader API 没有 flush，保存失败也未恢复待存队列。本轮小范围补丁提供保存屏障与显式重试，实际结果以 `SAVE_PATCH_RESULTS.md` 为准；完整替换仍需后续文档类型和生产数据验收。

Translate for Zotero 的具体复用边界：

- `src/utils/llmEndpoint.ts` 的 `normalizeLLMEndpoint` 是无宿主依赖的字符串函数，可原样抽取（保留 AGPL 许可）。Portal 已有模型路由时无需重复引入端点构造。
- `src/utils/llmPrompt.ts` 的模板拆分、语言替换思路可沿用；函数直接读取 getPref 与 Zotero.Items，不能原样导入。应将模板、标题和摘要作为显式输入，由 Portal 模型入口执行。
- `src/modules/services/base.ts` 定义 service/task 合约，但类型导入连接插件 utils；不能直接当作独立 SDK。`qwenmt.ts` 等服务依赖 Zotero.HTTP、addon 刷新、prefs、secret，不能直接放入网页；认证和请求必须由 Portal 服务端承担。
- `src/modules/reader.ts` 依赖 Zotero.Reader.registerEventListener 与 ztoolkit UI，tabpanel、menu、settings 和插件 hooks 不可直接复用到 Web Reader。只复用选区手动翻译与译文存便签的交互设计。
- 本轮未复制翻译源码、未调用翻译模型。插件缓存及全局任务状态不作为 Portal 个人翻译缓存；阶段 E 需独立实现按账号和访问范围隔离、成功持久化及并发合并。

两项目含 AGPL，后续分发/网络部署应保留版权、许可与对应源码获取入口；当前原型不宣称已完成整个 Portal 组合许可审查。
