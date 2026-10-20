---
kb_id: decision_86721bb4e8c8
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 5
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-11T15:45:22Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_3913409c89ae
importance: 9
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/wall_backend.py
  - ${code_root}/management_portal/business_tasks.py
  - ${code_root}/management_portal/board_semantic_search_backend.py
  - ${code_root}/management_portal/paper_backend.py
  - ${code_root}/tests/cases/test_background_process_execution.py
  - ${code_root}/tests/cases/test_paper_review_execution.py
  - ${code_root}/tests/cases/test_paper_ccf_enrichment_execution.py
  related:
  - decision_25eb93d803a4
  - decision_6ad0384e020d
  - decision_e37f240e186d
  - decision_eb066fbea4ed
  supersedes: []
  derived_from: []
---
# Portal Business Task 的进程内异步运行

Portal Business Task 是在调用它的进程中运行短期后台函数的轻量机制。Web 进程用它处理页面触发的工作，ExecutionService 用它推进 Harness 离线演进。一个页面请求可以启动一段 Portal 自己的业务代码，然后立即返回，用户不用一直等着 HTTP 响应。例如用户在 Paper 页面点击“生成摘要”，页面马上显示“排队中”，摘要在后台生成，完成后页面刷新结果。

本文说明这种任务怎样启动、怎样避免在同一进程里重复启动，以及进程出错或重启时会发生什么。

## 先认识几个概念

- **Web 进程**：接收 Portal HTTP 请求的 Python 进程。
- **业务函数**：一段普通的 Portal 业务代码，例如运行一次语义搜索、推进一次论文审阅。
- **任务 ID**：调用方为一项业务工作选择的非空字符串，通常由业务前缀和业务对象 ID 组成，例如 `paper-review:<job_id>`。
- **活动登记表**：宿主进程内存中的一个集合，记录当前正在运行的任务 ID。
- **后台线程**：与请求并行运行业务函数的线程，默认是守护线程。

业务的进度和结果由具体功能自己保存，例如论文审阅任务的状态记在 Paper 的业务记录里。Business Task 本身不创建任何持久记录。

### 与 ExecutionService 的区别

ExecutionService（decision_25eb93d803a4）负责一次模型执行：它有稳定的 `execution_id`、持久队列、账号容量、Worker 租约、模型结果和崩溃恢复。Business Task 只负责当前宿主进程里的一段短期业务代码。业务函数内部需要模型时，可以再向 ExecutionService 提交执行或按职责投递给 Resident；外层的 Business Task 不会因此获得 ExecutionService 的这些能力。耗时长、需要跨重启恢复、需要统一取消、需要容量与公平调度或需要模型执行记录的工作，应使用 ExecutionService 或其他持久服务。

## 设计点

- **启动成功后，HTTP 处理函数可以立即返回。** 业务函数继续在同一个 Web 进程的后台线程中运行。
- **同一任务 ID 在当前进程中同时只运行一个。** 已有同 ID 的任务在运行时，再次启动返回 `False`，不创建第二个线程；首次接受并成功启动线程时返回 `True`。
- **启动失败不会占住任务 ID。** 创建或启动线程出错时，先从活动登记表中移除这次登记，再把异常交回调用方，后续请求可以重试。
- **不同任务 ID 可以同时运行。** 这个机制没有全局并发上限，也不排队。
- **任务结束后，任务 ID 总会被释放。** 业务函数正常返回或抛出异常后，任务 ID 都从活动登记表中移除，之后可以用同一个 ID 再次启动。
- **业务函数的异常交给调用方提供的错误回调。** 调用方用它把失败写回业务记录。错误回调自身再出错时，任务 ID 仍然会被释放；没有提供错误回调时，异常被丢弃，任务 ID 同样释放。
- **进程退出时任务随之结束。** 后台线程默认是守护线程，不会阻止进程退出；调用方可显式设置 `daemon=False`，让进程等待任务完成。活动登记表也随进程消失，框架不会自动续跑。
- **去重只在当前进程内有效。** 每个宿主进程有自己的活动登记表，任务 ID 不能当作跨进程的锁。

## 一次任务怎样运行

```text
页面请求
  │ 选定任务 ID 和业务函数
  ▼
business_tasks.start
  │ 在活动登记表中原子地检查并登记
  ├─ 已存在 ──> 返回 False
  └─ 新任务 ──> 启动守护线程 ──> 立即返回 True
                              │
                              ▼
                         运行业务函数
                              │
                   成功，或调用错误回调
                              │
                              ▼
                   移除登记，线程结束
```

启动入口是 `management_portal/business_tasks.py` 中的 `start(task_id, target, *args, on_error=None, daemon=True, **kwargs)`。它先去掉任务 ID 两端的空白，结果为空时抛出 `ValueError`；检查和登记由同一把进程内锁保护，同一进程中的两个请求不会同时通过检查。线程名为 `portal-business-` 加任务 ID 的前 80 个字符，便于排查。位置参数和关键字参数原样传给业务函数。

`is_active(task_id)` 是只读查询：它在同一把锁下回答“这个任务 ID 当前是否在本进程的活动登记表中”。它看不到其他进程，也不说明业务是否已经成功。业务方常用它判断一条“进行中”的记录是否还有线程在推进，例如 Paper 页面据此发现进程重启后已经没有人在运行的图表解释任务。

## 当前使用 Business Task 的功能

| 功能 | 任务 ID 前缀 | 后台运行的工作 |
|---|---|---|
| WALL 页面的语义搜索 | `semantic-search:<search_id>:<generation>` | 执行一次搜索并更新搜索记录 |
| Paper 语义搜索 | `semantic-search:<search_id>:<generation>` | 执行一次论文搜索 |
| Paper 登记 | `paper-registration-draft:` | 从上传的 PDF 生成登记草稿 |
| Paper 摘要、资产、集合、审阅、实验 | `paper-summary:`、`paper-assets:`、`paper-collection:`、`paper-review:`、`paper-exp:` | 推进对应的 Paper 业务任务 |
| Paper 图表 | `paper-figure-explain:`、`paper-figure-draft:` | 解释图表或生成图表草稿 |
| CCF 信息补全 | `paper-ccf:` | 为论文补充 CCF 等级信息 |
| Harness 离线演进 | `harness-evolution-` | 在 ExecutionService 进程内推进持久演进任务 |
| WALL 扫描 | `wall-scan:` | 扫描工程中的设计点并刷新缓存 |
| WALL 工作卡片 | `wall-job:` | 推进一张 WALL 工作卡片；Release 实例不启动这类任务 |

这些是当前的使用方，不是限制。是否适合用 Business Task，由业务方按上面“与 ExecutionService 的区别”判断。

## 状态、反馈和故障

Business Task 只拥有“这个任务 ID 当前是否在本进程运行”这一项临时状态。排队、完成、失败、进度和结果都由具体功能保存，也由它负责展示给页面。框架不提供结果查询、事件流、取消、优先级、确认、租约或持久重试。

异常不会传回已经结束的 HTTP 请求，调用方需要通过错误回调记录失败。进程崩溃时线程和登记一起丢失，重启后的请求可以用同一个 ID 再次启动，所以业务函数要自己处理可能的重复执行和部分完成的状态，例如先检查业务记录是否已经完成。

Paper Review 使用 `process_identity()` 记录宿主、启动周期、PID 命名空间、进程 ID 和启动时刻，再用 `process_is_alive()` 判断原进程是否已退出。无法读取进程或它位于另一宿主、另一命名空间时，不据此认定任务中断。
