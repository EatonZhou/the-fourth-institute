# Reader 保存屏障与失败恢复契约提案

结论：固定 Reader 提交 `04795bfbafd55600e042d62e05f9a4147458ae53` 的 Web 公开 API 不足以在关闭前证明注释已全部持久化。阶段 A 阻塞。当前原型仅用于模块加载与回调实验，不能作为可靠保存实现；本提案未修改上游或 vendor。

## 调用链证据

`src/index.web.js:6` 返回原始 Reader 实例，没有附加保存包装。
`src/common/reader.js:352` 创建 AnnotationManager，`onSave` 指向宿主保存回调、`onDelete` 指向 `_handleDeleteAnnotations`。Reader 没有把 `saveNewAnnotationsImmediately` 选项传给 AnnotationManager；即使支持立即保存新建记录，也不能覆盖后续 comment/颜色/几何编辑。

`annotation-manager.js:343` 的 `_applyChanges` 将修改加入内部待存 Map，`_triggerSaving:393` 受 1 秒 debounce 与 10 秒阈值控制。返回的 Promise 在 debounce 分支直接完成，不能当作保存屏障。定时器触发也没有向 Web 宿主暴露 Promise。

`annotation-manager.js:440` 在保存前清空待存 Map，删除回调不 await，然后 await `_onSave:450`。保存拒绝时执行不会到达重置 `_savingInProgress` 的语句，失败批次也没有恢复。删除的异步失败不会参与保存结果。

`reader.js:2932` 的 `_handleDeleteAnnotations` 需同时改成返回 `_onDeleteAnnotations(ids)` 的结果，否则仅在 manager 增加 await 仍无法获得宿主删除确认。

`Reader.getUnsavedAnnotations:2259` 是空函数。`setAnnotations:948` / `unsetAnnotations:952` 表示宿主向 Reader 输入外部变更，不是持久化导出；`freeze:2744` 仅更新 UI freeze 状态，`setReadOnly:928` 也没有 flush；`setSuspended:2754` 只控制视图资源。`flushSync` 是 React 的同步渲染，不保证存储完成。公开位置、选中事件与 DOM 也不包含全部原生注释、删除墓碑和保存确认。

宿主可暂存已经交付的批次并自行重试，但无法获知最后一批尚未交付的修改何时已交付；保存回调保持 pending 也不会提供公开的队列排空信号。固定等待 10 秒、轮询 DOM、读取 `_annotationManager` 或替换其方法均不能满足本任务的公开契约要求。

## 最小上游契约改动

修改范围仅 `src/common/reader.js`、`src/common/annotation-manager.js` 和直接测试；无需修改 PDF.js、Web entry 或存储后端。

1. 新增公开 `Reader.flushAnnotations(): Promise<void>`，委托 manager 的 `flush()`；Promise 必须代表调用前已提交到 manager 的全部修改及在途保存、删除均已获得宿主确认。调用时绕过 debounce、清理 timer，复用唯一存储执行循环；不得直接并发调用第二个 writer。进行 flush 时产生的新修改也应顺序 drain，直至队列为空。宿主关闭前先将 iframe 设为 inert、移出焦点以提交已编辑的 comment，再调用屏障；未进入 manager 的 DOM 编辑还需真实 UI 测试证明已提交。
2. manager 的批次保留不可变快照，维护一个 in-flight Promise。依次 await 删除与保存回调；Reader 的删除包装必须返回 Promise。本批未全部确认前不得丢失恢复所需数据；保存函数也可能同步抛错，必须统一捕获。
3. 失败时将未确认的操作合并回待存队列，以同 ID 较新的操作为准；保留墓碑、image 与 onlyTextOrComment 所需元数据。finally 清理 in-flight 状态，停止自动重试并保留错误，屏障 reject。再次显式 flush 清除暂停状态并重试，宿主保留 iframe 与失败提示；成功后才能释放 iframe。
4. 部分成功重试允许重复 delete/upsert，宿主须幂等。Reader 的“client known”表示客户端已知与存储确认不能混用；失败后不得借此丢弃新建记录。未完成保存的标记不等同于已持久化。
5. 自动保存复用同一循环并捕获拒绝，避免未处理 Promise rejection；新增可选 `onAnnotationSaveError(error)` 报告自动保存失败。保持已有未返回 Promise 的同步保存回调可用；异步宿主必须返回真正的持久化确认 Promise。

这是接口与内部保存状态机的完整最小语义范围，不能只加一个 public 方法再调用当前 `_triggerSaving()`。不建议用空 `getUnsavedAnnotations()` 返回快照代替屏障，那仍需要另行规定并发修改、删除确认和错误归属。

## 测试与验收

已增加并运行 `tests/cases/javascript/zotero_reader_save_feasibility.cjs`：以 Acorn 从固定上游源码提取真实 `_triggerSaving` 方法，在隔离状态中验证 debounce 尚存记录、保存拒绝后批次丢失/worker 卡住、删除未确认即可返回。它是缺陷复现测试；通过表示阻塞存在，不代表实现通过验收。测试中读取内部状态只用于诊断，不用于宿主适配或可靠性实现。

后续契约补丁必须覆盖下列回归，当前尚未实现或通过：

| 场景 | 验收断言 |
|---|---|
| fake clock 下修改后立即 flush | 不推进 1/10 秒时钟也能交付；宿主 Promise 完成前 flush 不得 resolve |
| 自动保存正在进行再 flush | 单 writer，等待在途以及之后的待存批次 |
| upsert/delete 同步抛错或异步拒绝 | flush reject；数据/墓碑保留；无无限自动重试或未处理 rejection |
| 失败后同 ID 再编辑/删除再重试 | 最新操作获胜；旧失败批次不得覆盖新内容；image 元数据不丢 |
| 删除成功而保存失败后重试 | 幂等重放，不复活删除内容 |
| 保存期间新增修改、多个 flush | 无遗漏、无重复 writer，所有屏障按确认完成 |
| Firefox、Chromium 真实 UI | comment 输入仍获焦点时关闭、快速高亮退出、断网失败后重试、删除重开、列表点击定位均通过 |

先获得上游契约支持或批准维护上述 fork 后才投入两浏览器 UI 验收。此次按可行性门槛停止，未重新运行原有两条测试，未启动浏览器、未改后端和现有阅读器、未发布。

## 与当前 PDF.js 路径的取舍

保留现有 PDF.js 路径可维持当前功能和部署边界，避免立即承担 Reader 保存状态机 fork；现有路径不因此被视作已解决 Firefox 卡死或完成新便签功能。采用 Reader 可复用注释列表、便签和原文定位交互，但需要上游接受契约或长期维护固定补丁，并增加失败、并发与两浏览器回归。建议节点 A 保持阻塞、生产继续当前路径，待补丁获准后再推进；无须改动正式后端来绕过此阻塞。
