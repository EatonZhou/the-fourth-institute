# 固定 Reader 保存补丁验收

基线提交为 `04795bfbafd55600e042d62e05f9a4147458ae53`。`save-contract.patch` 是独立、可审查的 Git patch，仅修改上游 `src/common/reader.js` 与 `src/common/annotation-manager.js`；`upstream/` 是应用该补丁后生成的 Web 构建，COPYING 保留。补丁没有进入生产阅读器或后端，未发布。

公开 `flushAnnotations()` 返回保存屏障。manager 的 flush 复用唯一在途 Promise，跳过 debounce，循环按批次 await 删除与保存确认。失败恢复待存记录/墓碑，以较新修改为准，保留图片与高频编辑标记，暂停自动重试。再次显式 flush 重试；自动错误通过 `onAnnotationSaveError` 上报。部分成功后允许幂等重放 delete/upsert，宿主必须支持此语义。

`host.js` 将保存和删除 Promise 原样交还上游，关闭时递归 blur 焦点编辑、将 iframe 设为 inert，再 await 屏障；失败恢复交互、保留 iframe，允许再次关闭重试。无固定延时保存保证，无读取私有字段的宿主逻辑。

重现步骤：递归克隆 zotero/reader，checkout 固定提交，再运行 `bash management_portal/paper_reader_app/zotero_prototype/build.sh /path/to/reader-checkout`。脚本验证固定 HEAD 和干净源码、应用 patch、按 lockfile 安装、构建并同步生成资源。源码路径没有更换版本。首次 PDF.js 构建结果有效且其源码未变化，此轮复用；补丁后实际执行 `npx webpack --config-name web`，退出 0，仅有 bundle 大小提示。实际执行 `git apply --reverse --check .../save-contract.patch` 退出 0，确认 patch 与当前源码差异一致；未另行重复整套冷构建。

实际验证命令（从 Portal code 根执行）：

```
node tests/cases/javascript/zotero_reader_save_contract.cjs /tmp/portal-zotero-reader-a
node tests/cases/javascript/zotero_reader_host.mjs
node tests/cases/javascript/zotero_reader_save_ui.cjs
```

状态机矩阵与 host 测试通过。前者从实际补丁源码装载 AnnotationManager 类，在隔离状态与受控时钟/Promise 下验证：不推进 debounce 即可 flush、并发 flush 共用 Promise、在途修改顺序 drain、删除确认、同步/异步保存及删除失败后重试、失败期间较新编辑/墓碑优先、图片恢复、删除成功保存失败的幂等重放、自动错误上报且暂停。首次断言因跨 VM microtask 计数不足失败，改为等待一个事件循环 turn 后通过；不是增加固定保存延时。host 测试验证错误不销毁并可再次关闭。

Firefox 与 Chromium 使用合成 PDF、真实原生 UI 和浏览器 localStorage，逐个顺序运行：

- 通过 note 工具创建便签，编辑器仍有焦点时直接关闭，重开后 comment 存在。
- 点击注释列表 header，验证 selected；诊断探针观察真实 PDF view navigate 收到正确 annotationID。探针只在测试中读取内部视图，不参与保存或适配。
- 列表选中后按 Delete，关闭并重开，记录未复活。
- 注入 Storage.setItem 抛错，关闭被拒绝且 iframe 保留；恢复存储后重试关闭、重开，便签存在。
- 通过 highlight 工具和真实鼠标文字选择，立即调用关闭；重开后高亮和便签均在列表中。
- 无 pageerror。首轮尝试列表外层右键未打开删除菜单，改为原生列表焦点与 Delete 键完成删除测试。

未验证多页滚动定位的视觉精度、旋转/扫描/双栏 PDF、加载中退出、真实远程持久化延迟、页签强制关闭或进程崩溃恢复、生产账号隔离。屏障保证已提交到 manager 的修改，无法保存进程被杀时仍未提交的 DOM 状态。原有 Firefox 卡死根因不在本轮结论内；未跑全量 UI 或生产门禁。旧 `zotero_reader_save_feasibility.cjs` 针对未打补丁基线，只作为历史缺陷复现，不能用来验收当前已修补实现。
