# 翻译缓存与在途任务 owner

新增 `management_portal/paper_translation_cache.py` 和 `tests/cases/test_paper_translation_cache.py`。仅开发可调用 owner 与直接测试，未注册路由、接 UI、调用真实模型或发布，未修改近期阅读、http_paper 或数据节点文件。附件生产接入保持原边界。

## 固定插件源码核对

核对 https://github.com/windingwind/zotero-pdf-translate 固定提交 `4e9ca763b9d8f07604aceb7671b5e0c29c3b5a3a`，上游 LICENSE 为 AGPL-3.0。

- **原样复用源码：本轮为零。** `src/utils/llmEndpoint.ts:normalizeLLMEndpoint` 是可独立抽取的端点字符串函数，但 Portal 必须复用 canonical 执行路由，本切片没有重新拼 provider endpoint，故没有引入它。
- `src/utils/llmPrompt.ts:buildPromptParts` 直接依赖 getPref、Zotero.Items、标题/摘要读取。仅借鉴把精确原文、语言与实际论文上下文作为显式输入的方式，没有复制函数实现。
- `src/modules/services/base.ts` 的任务/service 类型依赖插件 utils；`qwenmt.ts` 等服务依赖 prefs、addon 刷新、Zotero.HTTP 和 secret。没有复用这些执行代码，也没有提供网页版 provider 请求。
- Reader popup、tabpanel、menu 的 Zotero.Reader/ztoolkit 接口不可原样用于 Web Reader。本切片没有复制插件 UI、全局任务或缓存实现；缓存键、事务与租约按 Portal 的权限与持久化需求独立实现。

上游许可证仍适用于将来复制或派生其源码的场景，须保留版权、许可与对应源码入口。本轮没有新增翻译插件源码副本，不宣称因此已完成整个 Reader/Portal 的许可证审查。

## owner 接口和数据来源

`TranslationCache(authenticate,prepare,authorize_access,execute)` 四项均显式注入。authenticate 从正式上下文取得稳定账号；prepare 由可信服务端验证原文来源/访问权限、解析实际模型配置并产出完整 descriptor；authorize_access 对每次 request/get/claim/finish 复验访问。浏览器声明 shared 不构成共享授权，测试 prepare 明确拒绝未被服务端认可的共享请求。

descriptor 包含精确 text、源/目标语言、实际 model（service/model/settings/revision）、prompt（版本与实际 system 内容）、terminology（版本与内容）、实际发送 context，以及 access。不得只填可变路由名或提示词标签；正式 prepare 必须包含物化后真正发送的提示词/规则内容或其不可变版本，以及任何影响模型输入的设置，不能在 execute 阶段再悄悄增加未入键的上下文。禁止放入 API key 等凭据。

descriptor 用有限 JSON 规范编码，保留原文空白与数组顺序，对象键排序；整份输入及访问范围哈希作为缓存键。private 范围强制绑定认证 actor；shared 范围使用经服务端验证的 scope 及同一实际输入。不同账号默认隔离；共享任务跨账号只共享可访问文本与状态，不允许后来读者借共享命中切换任务的执行/计费主体。

API：

- `request(context,operation_id,request,refresh=False)`：每个 actor+operation_id 固定实际 descriptor 与 refresh 语义。同操作重放返回原任务（即使已失败），内容变化拒绝；新显式请求使用新 operation_id。成功缓存返回 hit；无成功缓存时复用同键 active task，否则创建 queued。refresh 跳过成功结果，但合并同键在途刷新；普通请求在刷新期间仍可读旧成功。
- `get(context,task_id)`：复验权限后返回状态/译文/安全错误，不靠 task_id 或哈希授予读取能力。
- `claim(context,task_id)`：queued 或已过期 running 任务原子取得 token 和有界 lease。只能使用原请求者的正式认证上下文执行，不能从共享读取权限推导代另一个账号执行的权限。
- `finish(context,task_id,token,...)`：未过期且未被接管的租约才允许发布；成功原子保存译文和成功缓存指针；失败清理 active 指针但不创建有效缓存，也不覆盖之前成功结果。错误消息固定化，不把执行器异常中的 secret 暴露给客户端。
- `run(context,task_id)`：供隔离测试和未来受控调度调用的同步 orchestration，claim 后调用注入执行接口，再 finish。没有线程池、守护进程、专用 Worker 或全局任务扫描。

持久化复用现有 DomainDatabase/公共 PG 连接、paper schema 及领域任务的具名 status/lease 记录惯例，新增 reader_translation_keys/tasks/operations 三张职责表；没有改现有任务 KINDS 或共享 schema 注册。每键行锁合并请求，operation 锁保护同操作重放；执行器在数据库事务外运行，过期 token 无权发布迟到结果。

## 正式 canonical 执行约束

注入 execute 接收 `{task_id,fresh_context_id:task_id,history:[],descriptor}`，必须按 task_id 持久绑定 canonical Execution 并实现 submit-or-resume。每个**新任务**使用新上下文；租约恢复仍是原任务，只恢复其已绑定 Execution，不能再 submit 新模型调用。完成业务缓存不等于允许绕过 `ExecutionRequest`、统一 prompt 物化、正式身份/模型路由或用 Zotero.HTTP 直接请求 provider。

本轮 execute 全是假执行器，没有实现 canonical adapter、真实 execution binding 或模型调用。因此测试证明缓存/租约/幂等状态机，不能证明远端模型端的 exactly-once。正式适配器必须首先查询并恢复 binding，并保持原请求者授权；授权失效时明确失败，不借用历史 actor 继续调用。同步 run 的默认 lease 为 60 秒，生产长任务应由 canonical 状态推进及合适租约续期策略承接，不能直接照搬阻塞执行后宣称任意长任务可靠。

过期任务不会由读取自动启动。显式 run/claim 可接管同一任务，旧 token 的迟到 finish 拒绝；模拟测试确认恢复仍向执行器传同一 task/context ID。任务和 operation 回执当前不自动清理，失败结果只属于任务记录，不是有效缓存。上线前需确定容量、回执保留及授权失效后的销毁策略，不能删回执后让旧 operation_id 重新执行。

## 验证

实际命令使用工程 Python：

```
/opt/tfi/the-fourth-institute-dev/dependencies/c121e8e7001cea66d33ef7900ecae39c98e6a7aa22582cee6867a8d3126cf89d/venv/bin/python -m pytest -q tests/cases/test_paper_translation_cache.py
```

最终退出 0：`13 passed in 1.10s`。临时 PostgreSQL fixture 与假执行器验证：精确文本/语言/模型/提示词/术语/上下文变化均不错误命中；账号隔离与服务端核验共享；并发同键只建一个任务，两个 run 只执行一次；成功命中、失败不缓存、refresh 并发合并及失败保留旧成功；操作重放/正文冲突；过期任务重领与旧 token fencing；既有执行绑定恢复模拟、每新任务空历史、无效执行结果按失败处理。

未验证真实 canonical 调用、HTTP/UI、模型取消与长任务续租、进程/数据库灾难恢复或共享权限生产策略。未重跑已通过的 Reader、附件或浏览器测试。
