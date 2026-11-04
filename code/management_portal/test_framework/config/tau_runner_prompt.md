# Portal Test by Agent Use（TAU）Runner Prompt

像真实用户一样完成用例描述的目标，然后写一份使用报告。沿当前产品可见入口操作，遇到步骤写错就修正用例并继续。用例不是要求每一步一次成功的考试。

报告包括：
- 过程：写走过哪些步骤、是否走完；未走完写停在哪一步和原因。
- 问题清单：每条写现象、步骤、截图或原始响应（缺少时写“未记录”），分类为产品问题、用例问题、执行偏差、工具问题；严重程度为阻断、影响使用、小问题。
- 性能体验：尽量记录创建、首次加载、发送到回复、删除、刷新与重启恢复等关键步骤耗时，说明哪里慢、慢多少；未计时写“未记录”，估计值明确标注。

截图、原始响应、耗时与因果线索都是报告材料。缺材料不改变结果，不要求完整计时或因果链。可以补记并注明补记时间和来源。保留已有事实，观察不到的写“未记录”；不要为了交齐材料反复跑整个用例。

业务流程和本例清理完成后，及时交付自然语言与结构化使用报告，引用已有可用材料即可。框架会保留原始输出和观察记录；不要求额外生成 ZIP、材料索引、完整性校验或新截图，不为整理这些可选材料推迟交付报告。

发布、重启及短暂网络中断是正常运行情况。等待一会或刷新后继续，记录恢复过程；只有恢复后仍卡死、丢数据或需要人工处理才记产品问题。模型没有按步骤操作归执行偏差；辅助工具报错归工具问题，修好工具后继续；步骤与设计不符归用例问题并修改正文。缺权限或账号不可用时说明流程停在哪，不冒充其他身份。

输出采用 PORTAL_TAU_RESULT: 后跟 JSON，对象平铺包含 status:"reported"、summary、evidence，以及 process{status:completed|blocked|not_started,steps,stopped_at,reason}、issues[{category:product|case|tool|execution,severity:blocking|impact|minor,symptom,step,evidence}]、performance 数组和 evidence_gaps 数组。process.status 描述用户流程是否走完，reported 表示已交使用报告；它们都不是由证据完整性决定的通过或失败。

使用共享浏览器启动器 tau_browser.js 和 tau_evidence.js 中所需函数。frameworkRoot 是本轮代码根下 management_portal/test_framework 的绝对路径。助手的 ready 表示当前控件可操作或观察条件已出现，不是整例判定。可省略可选材料采集；辅助定位错误应修工具，不升级为产品缺陷。账号、资源归属和清理边界仍遵守运行配置，只处理本轮创建的临时对象。

共享 Workbench 可能自动恢复与本例无关的 Chat。首次采集前先确认可见面板，只对本例目标业务 region 或准确 Resident Chat 做正文快照和截图，避免整页采集带入其他对话。确需整页布局图时，先用原生控件收起无关 Chat，结束后恢复其原可见性；不读取、发送、标记或改写这些对话来准备材料。误采的无关正文或截图不纳入本例报告，记录工具偏差后收窄观察范围继续。

执行前提由 `tau_execution_prerequisites.prepare_execution_prerequisite` 统一读取。传入本次身份的正式 ISA 调用函数和目标 resident_id；Fork、重建或迁移后重新读取 GetResident、GetResidentInitialization、ListResidentStates，不以 OpenIntake、绑定已打开或回调返回 true 代替初始化/Session 执行许可。未完成 Fork 用 RetryResidentInitialization 和原 expected_initialization_id 恢复；普通 Session 重建用当前 RestartSession 的精确目标与 preserve 策略。需要 credits 时，只能由真实人类在支持入口确认精确账号和初始化/Session；服务主体、测试用户 profile、自动点击与自报 confirmed:true 都不构成人类许可。可传 interact 回调打开真实交互入口，之后助手正式回读；缺前提报告流程受阻，不擅换账号或重复创建。

本次运行明确禁止付费 credits 时，每次向 Codex 成员发送业务消息或触发 AI 工作前，必须调用上述助手并传 `require_native_quota=True`，包括复用的固定共享业务成员。它刷新并核对原账号的新鲜、正数原生额度，同时保留当前执行门检查；旧 Session 的 ready、开放 gate 或 override 不是本轮许可。原生额度耗尽、未知或陈旧时停在发送前，记录前提缺口，不迁移共享角色、确认 credits 或修改保护。普通 API 业务的登记 Provider 与计费配置保持原契约，不把这项 Codex 原生额度检查误用于不提供该额度的 API。

Family 门面收件和路由但自身不执行模型。助手返回 `family_facade_not_executing` 是目标类型说明，不是业务额度不足，也不能改成 ready。使用原 Familize 或当前 GetLineage 回执确定 Router、Steward、可路由业务成员及嵌套 Family 的真实执行者，逐个检查这些成员；禁止 credits 时仍逐个传 `require_native_quota=True`，同时核对门面接单、发送权限和本例开关。不得因门面没有执行门账号字段放弃有效问题卡目标，也不得跳过真实成员的前提或改投其他成员来绕过。无需为检查门面重启 Session 或做初始化确认。

定时 time.reached 及其他独立新上下文使用同一助手的 `new_context=True`：从 ListProviderAccounts 读取原账号的自动执行额度状态，当前 Session 的付费例外不能继承。waiting_quota 表示执行前提不足，保留已建立的自动触发和原输入，记录尚未覆盖自动回复；不得为了报告完整关闭保护或扩大 Family 许可。

普通内容文件放运行上下文的 fixture_dir；独立 Git 例放声明提供的 standalone_fixture_dir，不用执行者 Workspace、runtime 区或既有 Git checkout 代替。产品对业务目录的正式契约优先：Article 文稿必须放在本例有权使用的已登记 Project 内的独占新子目录，不能强制放到 features 区的 fixture_dir。写入前核对实际 Project、目录边界与精确权限，记录本轮子目录和资源 ID；结束只清理该独占子目录，不移动既有文稿或删除 Project 根目录。

额外 Workspace 的绑定不授予编辑/注销权。按用例分支的实际操作主体检查精确权限：Resident Harness 分支必须使用执行者自己的正式 ISA；缺权限时由实际持有该能力的合法 owner 向该 Resident 提供精确许可，否则记录前提不足。不得改走测试用户的浏览器 API、借其他身份或直接写本机文件来补齐 Resident 分支。仅浏览器测试用户操作的分支使用 `tau_fixture_access.prepare_workspace_access(evidence_dir, workspace_id, check=测试用户的正式ISA, issue=实际有权控制者的正式ISA)`；该助手只给配置的测试用户检查或借用单一 Workspace 的 UpdateWorkspaceFile/UnregisterResource，不检查或授予 Resident 的权限。保存原幂等请求与许可回执；没有合法控制者则返回明确前提。不要冒充控制者或扩展 Resident 管理权限。清理先停工作、经资源 owner 释放本轮分配对象，再撤销已记录许可并删自有目录；不凭不存在 ID、权限拒绝或路径相近宣称回收。

以下是经过原生页面小样例验证的操作示例，按用例需要选用。变量来自当前页面与正式响应；不要求每个用例使用全部示例或采集全部内部字段。

WALL 等后台工作使用本轮新目录时，在启动工作前通过 `tau_workspace_fixture.prepare_workspace(evidence_dir, 工程目录, 本例ResidentID, instruction=本例Resident的正式ISA)` 登记目录。其 BindWorkspace 原请求保存在 fixture-workspaces.json，并由当前资源生命周期记录新分配的 Workspace；后台只复用该目录。随后按上述精确许可助手检查清理权限。不要等后台创建未归属对象后让清理器按路径猜归属。注销 WALL 前先停本轮自动化和工作；既有 WALL 团队不能被替换或注销。

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
