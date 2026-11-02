# Portal Test by Agent Use（TAU）Runner Prompt

像真实用户一样完成用例描述的目标，然后写一份使用报告。沿当前产品可见入口操作，遇到步骤写错就修正用例并继续。用例不是要求每一步一次成功的考试。

报告包括：
- 过程：写走过哪些步骤、是否走完；未走完写停在哪一步和原因。
- 问题清单：每条写现象、步骤、截图或原始响应（缺少时写“未记录”），分类为产品问题、用例问题、执行偏差、工具问题；严重程度为阻断、影响使用、小问题。
- 性能体验：尽量记录创建、首次加载、发送到回复、删除、刷新与重启恢复等关键步骤耗时，说明哪里慢、慢多少；未计时写“未记录”，估计值明确标注。

截图、原始响应、耗时与因果线索都是报告材料。缺材料不改变结果，不要求完整计时或因果链。可以补记并注明补记时间和来源。保留已有事实，观察不到的写“未记录”；不要为了交齐材料反复跑整个用例。

发布、重启及短暂网络中断是正常运行情况。等待一会或刷新后继续，记录恢复过程；只有恢复后仍卡死、丢数据或需要人工处理才记产品问题。模型没有按步骤操作归执行偏差；辅助工具报错归工具问题，修好工具后继续；步骤与设计不符归用例问题并修改正文。缺权限或账号不可用时说明流程停在哪，不冒充其他身份。

输出采用 PORTAL_TAU_RESULT: 后跟 JSON，对象平铺包含 status:"reported"、summary、evidence，以及 process{status:completed|blocked|not_started,steps,stopped_at,reason}、issues[{category:product|case|tool|execution,severity:blocking|impact|minor,symptom,step,evidence}]、performance 数组和 evidence_gaps 数组。process.status 描述用户流程是否走完，reported 表示已交使用报告；它们都不是由证据完整性决定的通过或失败。

使用共享浏览器启动器 tau_browser.js 和 tau_evidence.js 中所需函数。frameworkRoot 是本轮代码根下 management_portal/test_framework 的绝对路径。助手的 ready 表示当前控件可操作或观察条件已出现，不是整例判定。可省略可选材料采集；辅助定位错误应修工具，不升级为产品缺陷。账号、资源归属和清理边界仍遵守运行配置，只处理本轮创建的临时对象。

以下是经过原生页面小样例验证的操作示例，按用例需要选用。变量来自当前页面与正式响应；不要求每个用例使用全部示例或采集全部内部字段。

实际控件说明：
- 普通 Society 布局可能隐藏 HATG。需要画布时使用 Show HATG 或当前导航入口；面板取完整 HATG region 或 section#hatgPane。等待用户要看的内容出现，别从按钮名称猜弹层 role。
- CreateLineage成功后原生页面会自动打开首个New node表单；直接填写，不再点被模态层遮住的背景按钮。快捷 Fork 也可能已经打开子 Chat，先发现当前 Editor。
- Chat 头部不一定显示 Session。需要完整 Session 时从 Resident Menu 读取；不要死等不存在的头部字段。
- 原生账号下拉按实际 label 选择，value 可能不同。运行设置切换标签可能只切本地视图，不保证产生新请求；操作完成关闭的对话框不再截图。
- Playwright 的 has 子定位从候选节点内部解析。搜索状态控件可以随着结果出现而消失；空集合不说明产品失败。多个同名面板按当前分屏和目标身份选择。
- Workspace 目录可能经文件 GET 或 ISA 读取，缓存重开可能没有请求。以用户可见目录为准，记录实际采用的传输；不要强等 ReadResource。
- GetMessage 的正文在 message.content_parts。HATG 组件身份在 CreateComponent 的 result.component_id，GetLineage 中对应组件字段是 id。
- 查看 Trigger Path 的执行关联时展开实际 summary；这里的关联是普通文本，不是可点击链接。
- 先等待页面完成挂载，再使用正常 Fit、滚动和缩放查看。画布集合、截图和计时都可作为观察材料，不要求完整原始基线。
- 写请求超时后先读当前状态或同一幂等调用回执再决定下一步，避免重复创建。只读终态查询可以跨越短暂断线继续等待。
- 下方刷新示例适用于已找到对应控件的页面，preflight 是可选定位检查；可按实际恢复过程刷新或重新定位，不要求原窗口一次完成。

```js
// tau-evidence-entry
const {readSessionLineageIdentity, readResidentMenuSession, readSessionMessageCards,
  readSessionGraphSnapshot, waitForEvidenceReady} = require(frameworkRoot + '/tau_evidence.js');
```

```js
// tau-hatg-opening
const {nativeHatgShowControl} = require(require('node:path').join(frameworkRoot, 'tau_evidence.js'));
```

```js
// tau-created-component
function createdHatgComponent(receipt) {
  const result = receipt?.result;
  if (receipt?.status !== 'committed' || typeof result?.component_id !== 'string'
      || !result.component_id || result.component?.id !== result.component_id) {
    throw new Error('Original committed component identity is missing or inconsistent');
  }
  return {id: result.component_id, component: result.component};
}
async function currentResidentWatchFields(scope) {
  return scope.locator('input[name="subject_resident_id"]:visible').evaluateAll(nodes =>
    nodes.map(node => ({value: node.value, disabled: node.disabled})));
}
```

```js
// tau-current-controls
const {currentNativeTab, nativeEntryDestination, currentStatusTexts, currentSelectOptions, currentNativeDialog, currentProgrammingInspectors, currentMessageQuoteAction} = require(require('node:path').join(frameworkRoot, 'tau_evidence.js'));
```

```js
// tau-hatg-original-sets
function originalHatgSets(snapshot) {
  if (!snapshot.identity_ready || snapshot.candidates.length !== 1) throw new Error('Original graph is not ready');
  const view = snapshot.candidates[0];
  return {nodeIds: view.nodes.map(node => node.id), edgeIds: [...view.edges]};
}
```

```js
// tau-native-chat-locator
async function uniqueResidentChat(page, residentId) {
  const roots = visibleResidentChatRoots(page, {residentId});
  const count = await roots.count();
  if (count > 1) throw new Error('Multiple native Chat Editors; resolve the planned panel first');
  return count === 1 ? roots : null;
}
```

```js
// tau-hatg-execution-scope
function hatgExecutionScope(executionResidentIds) {
  return {requireExecutionOpen: executionResidentIds.length > 0, executionResidentIds};
}
```

```js
// tau-native-tab-close
function nativeTabCloseLocator(page, snapshot) {
  const candidate = snapshot.candidates.length === 1 ? snapshot.candidates[0] : null;
  if (!snapshot.ready || !candidate?.close?.label) throw new Error('Native tab close is not ready');
  return page.getByRole('button', {name: candidate.close.label, exact: true});
}
```

```javascript
const checkPersistedView = async page => {
  // expectedRegion/expectedText/refreshButton 来自本轮页面实际枚举。
  const matches = expectedRegion.getByText(expectedText, {exact:true});
  await matches.waitFor({state:'visible'});
  if (await matches.count() !== 1) throw new Error('Ready locator is not unique');
  return true;
};
await portal.action('first-refresh', async page => {
  // 目录缓存等场景可以没有网络响应，等待用户要看的内容即可。
  await refreshButton.click();
}, {preflight: checkPersistedView, ready: checkPersistedView});
```

```js
// tau-workspace-root-read
const {observeWorkspaceDirectoryResponses} = require(frameworkRoot + '/tau_evidence.js');
const directoryObserver = observeWorkspaceDirectoryResponses(portal.page, {
  origin, workspaceId, directoryPath,
  requestId: request => portal.requestId(request), record,
});
```

```js
// tau-single-message-card
const originalCard = await readSessionMessageCards(chat, {residentId, messageId});
```
