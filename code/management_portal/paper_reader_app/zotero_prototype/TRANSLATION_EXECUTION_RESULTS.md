# 翻译 canonical 执行适配最小切片

新增 `paper_translation_execution.py` 和 `test_paper_translation_execution.py`。没有创建 Resident、修改账号/模型配置、注册路由、启动 Worker/Provider 或发送真实模型请求，未发布。缓存原型的同步假执行器入口仍只用于原有隔离测试；未来生产入口必须使用正式 canonical adapter，不能把旧假执行器路径直接接 provider。

## 正式能力核对与配置缺口

- Paper 当前入口是 `paper_resident._resident_reply → paper_projection.turn → workbench_projections.native → resident_handoff`。`paper_projection.ROLES` 的九个业务角色没有 translation。当前选区翻译规范复用讨论入口，不等于已经存在独立无历史翻译角色。
- 工作流共同契约 `WorkflowBindings.prepare/trigger/observe` 使用稳定 request_id、TriggerEvent 幂等键和 GetInstructionExecution 回执；Resident invocation 也有稳定 message_id 和恢复查询。不能借用 summary/review 等业务角色运行翻译。
- 底层正式 `ExecutionRequest` 支持由调用方确定 execution_id；Scheduler 的 `SubmissionIdentity`/`submission_digest` 对原请求做持久匹配，同 ID 同正文返回原提交，不同正文拒绝；`execution_lifecycle.check_new_submission` 在队列回执过期时继续依据 Agent 持久 admission 记录阻止重执行。`ExecutionServiceClient.status/record` 可查询现有执行，cancel 有正式 RPC。

本轮只构造可注入正式调用器的 adapter，不把底层 Execution RPC 当成绕过 Paper 角色的生产路径。构造需要显式 rpc、可信 plan 和结果 decode；plan 默认缺失，且只接受标识为 `paper:translation` 的规划器。这个属性是可信调用器的接口约束，**不是**角色存在/绑定/权限的证明。生产规划器必须先通过现有正式角色与绑定服务验证 translation 能力；未配置就保持关闭，本轮没有替你注册该角色。

## 请求冻结与提交窗口

plan 返回正式 `ExecutionRequest`，必须 stateless，无继承历史。它应由 Portal 上游的统一 prompt 物化器及可信身份/路由解析器生成。适配器把除 execution_id、agent_id、correlation_id 外的完整请求投影与缓存 descriptor.context.canonical_request 作精确比较，包含实际 prompt、system_instructions、session route、provider、runtime_options、actor 等。差异在 RPC 前拒绝，不让“缓存按旧 prompt，执行用新 prompt”发生。

每个 task 的完整请求与 execution_id 在本职责 PG 表 `reader_translation_execution_bindings` 中先提交，再跨 RPC。adapter 在持有任务有效 observer lease 时写入 submit_started，然后只调用既有 `execution.submit`。这改变了窗口顺序：**本地绑定先于远端接受**，因此远端已接受但调用者未收到响应/进程退出时，本地仍有稳定身份可查询。测试在真实 Scheduler 持久接受后注入进程中断，重建 adapter 后仅 status 原 execution_id，未再 submit；另用共同 Scheduler 验证同请求回执重放和改正文拒绝，没有用内存 receipt 假装持久幂等。

submit_started 后即便查询不到执行也只观察，不自动再 submit、不换 ID。写 submit_started 后恰好在 RPC 发出前崩溃，是当前保守的**可用性缺口**：不能分辨尚未提交与远端暂不可见，任务保留观察/阻塞。虽然共享相同 execution_id 的精确重放具有幂等保护，本 adapter 按“恢复只 observe”约束没有自动启动重放。需要后续明确的共同回执查证/人工处置才能恢复这个分支，不能据“不存在一个队列 snapshot”推出从未接受。队列历史已清理且 status 不可查时同样保留；生产还需明确接 `execution.record` 的归档回执解释，不伪造 terminal 状态。

绑定后不再调用 plan，不读取当前角色/模型设置重建请求。路由变化只影响未来新任务，既有任务只观察冻结执行；取消也使用冻结 execution_id 和 actor。不同新任务由规划器生成独立 execution/context，不允许重用持久历史会话。

## Pending、长任务、fencing 与取消

`tick(context,task_id)` 是调用方调度的一次有界步骤，没有新建后台线程/Worker。复用缓存 owner 的任务 lease，每个 RPC 前将有效 lease 延长到当前时刻后 60 秒，RPC 明确设置 5 秒 timeout、禁用自动不可用重试。Pending/超时/未知响应保持 running 与 active task，不当作失败缓存；步骤结束只释放当前 token。后续 tick 重新取得 lease，再观察同一绑定。因此任务可以跨越多个 60 秒窗口，不要求模型 60 秒内完成，也不依赖固定等待。

只有当前有效 token 能发布结果；被新观察者接管的旧 token 即使收到 completed，也无法写缓存或清理新租约。该保证指当前有效观察者/提交者，不能声称过期 RPC 的底层网络包已经被撤销。生产 RPC 实现必须真正遵守 timeout；无限阻塞的调用器不符合此接口。

终态 failed/cancelled 调用缓存 finish(error)，保留安全失败状态并解除 active，不自动重投；只有新显式业务请求可创建新任务。`cancel=True` 在未绑定时本地结束，不创建角色/Execution；已开始提交则持久化 cancel_requested，调用既有 execution.cancel 并继续确认终态。取消与完成竞态时本任务不发布新译文，先前成功缓存仍保留。目前取消映射为缓存 failed 状态，没有宣称新增独立的 cancellation UI 协议。

每次远端动作及结果发布前重新验证原请求者授权。权限失效则停止动作/缓存发布并保留绑定，不借历史 actor 或后台高权限身份继续取消。已经运行的远端模型是否被权限失效自动终止，由正式执行/授权 owner 决定；本 adapter 不宣称能用失效凭证强制取消，这是生产接入必须明确的语义。

## 直接验证

命令：

```
/opt/tfi/the-fourth-institute-dev/dependencies/c121e8e7001cea66d33ef7900ecae39c98e6a7aa22582cee6867a8d3126cf89d/venv/bin/python -m pytest -q tests/cases/test_paper_translation_execution.py
```

使用临时 PG、隔离 RPC 假实现和真实 Scheduler admission。测试 fixture 的 execution defaults 只在临时测试实例中登记，没有修改生产配置。没有 Worker 或真实 provider；终态由假快照提供，decode 显式读取测试用 canonical output。

最终命令退出 0：`5 passed in 0.97s`。覆盖：接受后退出及共享幂等回执；恢复不 replan/不重新 submit；Pending/timeout/终态失败；角色配置缺失、请求物化不一致均不发 RPC；模拟跨越多个租约窗口；旧 observer fencing；取消及授权失效不发后续 RPC；提交结果不明且暂时无 snapshot 时保留阻塞、不重投。

未验证正式 translation 角色、实际 prompt 物化规则、真实 RPC timeout/重启行为、真实模型取消、队列归档 record 解码，以及接受前不明窗口的共同恢复策略。测试通过不等于该角色或端到端生产接入已经存在。不重跑此前已通过的 Reader/附件/翻译缓存等价测试。
