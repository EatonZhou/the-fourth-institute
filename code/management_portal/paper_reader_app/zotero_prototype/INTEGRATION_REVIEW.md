# Reader / 翻译线集成评审交付

当前停在集成评审节点。交付包含隔离原型、保存可靠性补丁、独立持久化 owner 和定向证据；未替换生产阅读器、注册真实角色/路由、调用真实模型、迁移附件或发布。本轮只核对文件与正式调用链，未重复测试。现有源码中这些新增文件仍为待父节点整合的工作区产物，不代表已提交或上线。

所有路径相对 Portal `code/`。完整逐文件清单见 `DELIVERY_FILES.txt`；该清单列出本次评审前的全部已有文件，包括 upstream 的每个资源。新增的清单本身与本评审文档是两个收敛说明文件。

## 一、可复用源码与来源

| 交付 | 精确位置 | 范围与许可 |
|---|---|---|
| 固定 Reader 源码来源 | zotero/reader `04795bfbafd55600e042d62e05f9a4147458ae53` | AGPLv3；本地仅交 patch 和构建产物，不把压缩 bundle 充当可审查源码 |
| 独立保存契约 patch | `management_portal/paper_reader_app/zotero_prototype/save-contract.patch` | 仅上游 `src/common/reader.js`、`src/common/annotation-manager.js`；公开 flush、单 writer、失败恢复、删除确认 |
| 构建步骤 | 同目录 `build.sh` | 输入干净的固定提交递归克隆，应用 patch，按 lockfile 安装并构建、同步资源；不自动发布 |
| 翻译来源核对 | windingwind/zotero-pdf-translate `4e9ca763b9d8f07604aceb7671b5e0c29c3b5a3a` | AGPL-3.0；本轮原样复制源码为零。llmEndpoint 可独立抽取但不用于 Portal provider 路由；llmPrompt、services、Reader UI 依赖 Zotero，只有输入/交互思路借鉴 |

Reader 子模块固定为 pdf.js `5f95f712bc629ac11159cb4859ed8091fdffa87b`、epub.js `e68c8b9c868be6ccb5b9684213117e95e52ef880`、structured-document-text `7963c11faac4595be3bbe86b7574704a22c67bed`；locale 源固定 Zotero `a6a919234a360f5cc0f8dbf45b84e1fafc546f41`。未引入 Note Editor。

构建产物完整目录为 `management_portal/paper_reader_app/zotero_prototype/upstream/`，核对时 239 个文件，约 12 MiB 磁盘占用。入口 `reader.html`、`reader.js`、`reader.css`，另含 `115.js`、`317.js`、`903.js`、PDF Worker/Viewer、cmaps、fonts、wasm 等整套匹配资源，不能只拿一个 JS 文件发布。许可位置包括 `upstream/COPYING`、`upstream/pdf/LICENSE`、`upstream/903.js.LICENSE.txt`、cmaps/iccs 的 LICENSE；其余随行文件以清单为准。没有完成整个 Portal 组合分发的许可证审查，AGPL 对应源码/修改及获取方式必须在真正分发时一并处理。

已执行 Web webpack 构建成功，仅 bundle 大小提示；PDF.js 构建有效结果复用。独立 patch 的反向 apply check 与 build.sh 语法检查通过。完整冷构建脚本未再重复运行，不能称已验证所有新环境离线重建。

## 二、原型与独立 owner

| 精确源码 | 已实现 | 生产接入限制 |
|---|---|---|
| `management_portal/paper_reader_app/zotero_prototype/index.html` | 本地测试 PDF、localStorage 演示 | 没有账号隔离，不是正式页面入口 |
| 同目录 `host.js` | 原版 iframe、blur 提交焦点、flush 屏障、失败保留交互、生命周期 | 无正式 HTTP/位置失败完整协议 |
| 同目录 `persistence.js` | 可注入 transport、精确幂等重放、顺序 full/partial materialization、CAS 暂停、旧会话隔离 | 没有正式认证 transport 或生产路由 |
| `management_portal/paper_reader_annotations.py` | 可信上下文/描述符注入，账号+附件版本隔离，事务 revision/CAS、回执、墓碑，JSON/几何/读取预算 | 正式权限/附件 resolver 尚未挂接；highlight/note 以外几何未完成；无迁移 |
| `management_portal/paper_reader_attachments.py` | 单篇 staging→哈希→固定版本文件→登记/CAS，替换/回滚/显式恢复，可信 resolver | root/storage_key/权限需配置，POSIX 挂载假设；不自动复制现有库、回收或接对象删除 |
| `management_portal/paper_translation_cache.py` | 完整实际输入键、成功缓存、在途合并、操作幂等、refresh、租约与原请求者约束 | prepare/授权/执行均注入；生产 prompt/模型实际指纹与执行 binding 仍缺正式衔接 |
| `management_portal/paper_translation_execution.py` | 持久保存原 ExecutionRequest/ID 后 submit，Pending 观察、fencing/取消的底层契约实验 | **不属于已打通的 Paper Event→Effect→Resident 路线；不能直接部署作为正式翻译调用器** |

## 三、测试证据（复用记录，不合计为一次全量验收）

| 直接测试文件 | 已有结果与边界 |
|---|---|
| `tests/cases/javascript/zotero_reader_host.mjs` | 宿主保存/删除/定位、关闭、失败重试通过 |
| `tests/cases/javascript/zotero_reader_browser.cjs` | 初轮 Chromium 初始化/退出 smoke，不能代替完整 UI |
| `tests/cases/javascript/zotero_reader_save_feasibility.cjs` | **未打补丁基线**缺陷复现，不用于当前修补版通过判定 |
| `tests/cases/javascript/zotero_reader_save_contract.cjs` | 补丁真实状态机矩阵通过：drain、并发 flush、确认、失败/更新/墓碑/image 恢复 |
| `tests/cases/javascript/zotero_reader_save_ui.cjs` | Firefox/Chromium 真实快速高亮退出、焦点便签、失败重试、删除重开、列表定位调用通过 |
| `tests/cases/javascript/zotero_reader_layout.cjs` | 两浏览器实际页码和注释中心进入视口、合成双栏/90°页、加载中关闭、远程延迟/失败交互通过 |
| `tests/cases/javascript/zotero_reader_transport.mjs` | 精确重放、换正文新 ID、先确认旧请求、作用域/迟到响应/冲突通过；materialization 修改后直接回归有效 |
| `tests/cases/javascript/zotero_reader_pg_bridge.mjs` + `tests/cases/test_paper_reader_bridge.py` + `tests/cases/test_paper_reader_annotations.py` | JS→真实 AnnotationStore→临时 PG，最终联合命令 6 passed；包括局部更新保留 geometry/image/tags、丢响应新编辑、CAS、回滚/几何/预算 |
| `tests/cases/test_paper_reader_attachments.py` | 临时目录+PG 最终 8 passed；固定旧字节、注释绑定、源变化、并发 CAS、三个故障点、copying 恢复 |
| `tests/cases/test_paper_translation_cache.py` | 临时 PG+假执行器最终 13 passed；完整键/访问范围、去重、失败/refresh、过期租约与操作 |
| `tests/cases/test_paper_translation_execution.py` | 临时 PG+真实 Scheduler admission+假 RPC 终态最终 5 passed；接受后崩溃、共同幂等、Pending、取消/权限/租约、配置门禁；没有模型 Worker |

详细命令与失败修正记录分见 SAVE_PATCH_RESULTS、LAYOUT_AND_REMOTE_RESULTS、TRANSPORT_RESULTS、SERVER_STORAGE_RESULTS、MATERIALIZATION_RESULTS、ATTACHMENT_OWNER_RESULTS、TRANSLATION_CACHE_RESULTS、TRANSLATION_EXECUTION_RESULTS。没有全量回归、生产权限、真实模型、原 Firefox 卡死根因或长期资源泄漏验收结论。

## 四、最小真实接入顺序与唯一执行 owner

1. **先决定正式翻译角色及业务边界。** Paper/workbench/Lineage 负责者配置真实 translation 角色、Event Source、Effect→Resident Invoke 连线、任务模板及每任务 fresh/隔离策略；验证绑定和权限，不以 `paper:translation` 字符串或本地 plan.role 属性自证能力。不借 summary/review 角色，不由缓存 owner 创建 Resident。
2. **先解决缓存键与实际装配一致性，再允许复用成功结果。** 翻译业务 owner 提供精确文本/语言/术语/论文上下文。Resident 上游装配 owner 使用 `agent_prompt_context.portal_system_instructions/portal_turn_instructions` 物化实际规则、prompt 和有效模型设置。需正式 owner 提供可信的冻结/预览及提交前一致性校验，或在正式 durable request 可核对之后再认定缓存键有效；目前尚无已完成桥接，不能拿原型静态 plan 投影冒充实际 Resident prompt。若无法在触发前得到一致的实际指纹，就仍是阻塞，不能提前共享错误缓存。
3. **业务 task 绑定工作流提交句柄。** 通过已存在的 `paper_projection` / `workbench_projections.native` / `resident_handoff` 与 `WorkflowBindings.prepare/trigger/observe`。task 对应稳定 request_id；在触发前持久保存 prepared 的 lineage/resident/event_source/effect、幂等键。触发 Event，经 Effect 投递唯一 Resident input。回执不明时使用共同 GetInstructionExecution/工作流观察恢复，不能再 TriggerEvent。当前缓存模块尚未实现这套正式 workflow-handle 存储接线。
4. **Resident owner 唯一创建并持久绑定 Execution。** `society_service/resident_execution_domain.py` 构造请求；`resident_execution_engine_domain.durable_resident_execution_request` 先保存 planned execution_id 和完整 request，装配实际规则观察；engine 提交到 ExecutionService，恢复使用原 request/ID。业务 cache 不自行生成第二个 ExecutionRequest，也不将 Resident 整个业务流程包成外层 Execution 再生成内层模型 Execution。
5. **业务只观察原 input/Execution 与正式结果。** 从工作流 typed output、确认 message_id、GetInputState/Resident handoff 等正式回执关联 execution_id；提取正式终态 reply/usage，核对实际装配指纹后写成功缓存。Pending、取消和失败沿现有输入/执行状态处理，不读取“当前最近回复”冒充本任务回复。
6. **最后再接可控 UI/数据链路。** 独立附件与注释 owner 完成目录/权限/删除决策后，单篇显式导入样本，正式 resolver/transport 接线；再接选区翻译、成功缓存和确认保存便签。正式全屏替换必须另外决定并做生产范围验收。

**当前 Execution adapter 的处置：** 它是底层共同契约实验，不能放在上述 Event→Effect→Resident 链旁直接 submit，否则会重复执行且绕过角色/Resident 的装配、持久绑定与恢复 owner。生产应复用其“原句柄不变、Pending 不重投、lease fencing、取消不发布”的原则和测试场景；实际提交/恢复改由正式 workflow/Resident 层承接。是否保留该实验文件作测试参考或在生产适配完成后移除，由集成评审决定；本轮不自行改共享 Execution 协议。

## 五、阻塞与负责者

| 阻塞 | 应负责的 owner / 评审动作 |
|---|---|
| 正式 translation 角色、Event/Effect、fresh 策略 | Paper/workbench/Lineage owner；显式配置/诊断，确认每任务上下文隔离，不由本字符串门禁替代 |
| 实际 prompt/route 与缓存键一致 | Resident 上游 prompt 装配 owner + 翻译业务缓存 owner；确定可信冻结/校验点，绑定后不得因当前路由改变而重投 |
| 附件 root/storage_key、容量、旧版本保留 | 部署配置/Paper 文件 owner；生产目录不由原型默认选择，不全库扫描复制 |
| Paper 对象删除与附件/注释/回执清理 | Paper 生命周期 owner + 数据 owner；正式注销/清理流程协调，不绕过目录规范，不因引用无限保留已删除对象 |
| 队列归档后的 execution.record / reply 解码 | Resident handoff/Execution 查询 owner + Paper 结果解释 owner；沿原 execution/input 身份查持久事实，当前底层实验 adapter 未实现归档解码 |
| submit_started 后无法确认是否已发出 | 若采用正式 Resident 路线，复用共同 workflow/Resident intent 和回执恢复；底层实验 adapter 只观察并保留阻塞，没有自行发明负面证明或重投协议 |
| 权限失效、原执行继续运行与取消 | 认证/权限、Resident 输入控制及 Execution owner 联合明确；业务停止动作不等于远端已停，不能用失效凭证或冒用历史身份取消 |
| receipts/墓碑/任务保留 | 各数据 owner + 运维容量评审；当前不自动过期，任何清理必须先规定封存与迟到重放拒绝语义，不能删回执后放行旧操作 |

## 六、数据库懒建与回退边界

均通过现有 DomainDatabase 和公共 PG 连接，在 `paper` schema 下首次实际方法调用才建表，不在 import 时迁移数据；构造只配置依赖。不修改共享 schema 注册或原有 Paper 表。

- 注释：reader_annotation_scopes、reader_annotations、reader_annotation_operations。
- 附件：reader_attachment_heads、reader_attachment_versions、reader_attachment_imports。
- 翻译：reader_translation_keys、reader_translation_tasks、reader_translation_operations。
- 底层实验 adapter：reader_translation_execution_bindings。

读取注释 4096 条（含墓碑）/4 MiB 完整快照上限，超限拒绝而非截断；写批次 256 KiB。测试数据库由标准 fixture 的临时集群/namespace 管理，本线未在真实库调用 owner 触发建表。未来若已落地，禁用入口即可停止新写入，但**不等于能直接 DROP 表/删版本文件**：需先确认任务/Execution终态与恢复材料，按正式生命周期处理。版本回滚仅 CAS active pointer，不删新版本/注释；pending staging/receipt 与已绑定 Execution 不能在代码回退时随手清空。

当前无生产入口切换，因此回退只需不接入新增代码，现有 PDF.js 路径继续使用；它既未被替换，也不因本线测试而被宣称已修复 Firefox 卡死。补丁/构建若将来正式接入，需随整套固定资源成组回退，不能只换 Reader bundle。真实接入是否实施留待本评审节点决定，当前停止新增切片。
