# Provider 账号与 Backend

本分支用于当前 Portal 的 Provider 登记、登录、运行时和 Backend 配置，不只适用于 Codex。先读取 `ListProviders`、`ListProviderAccounts`、`ListBackendRoles` 以及本次操作指令的 schema；参数与能力由当前 Provider 契约决定。

账号登记、授权状态、程序可用性和 Backend 分配是不同状态。完成登记后回读相应状态，必要时用一次真实请求验证；不能把账号已登记或已登录等同于 Resident 已能执行。

登记与管理使用 `RegisterProviderAccount`、`UpdateProviderAccountRegistration`、`DeleteProviderAccount`；Backend 分配使用 `UpdateBackendRole`。订阅登录使用 `OpenProviderAccountLogin`、`GetProviderAccountLogin`、`CancelProviderAccountLogin`；需要继续交互时按登录结果和 `UpdateProviderAccountLogin` 的 schema 提交。模型 API 使用 `CreateModelApi`、`UpdateModelApi`。不要从历史文件名或账号目录猜测当前登记清单。

- CLI Provider 可复用当前机器已有的可执行程序和已授权账号；Portal 管理的独立运行时也是可用选择，不强制用户重装或重新登录。
- Model API 路线按模型 API 登记和探测流程处理，不套用 CLI 安装、CODEX_HOME 或 systemd 要求。
- 新建 Resident 可使用 `backend_role` 或明确的 provider/account/settings；具体继承、默认角色及回退以当前创建结果和设置回读为准。修改角色不代表已经改写所有既有 Resident。
- Codex 账号目录、运行路径与可选的 app-server 服务由本站 hosting/runtime 配置派生；不要硬编码源站账户根、socket 或 service 名称，也不把所有 Provider 都视为 Codex。
- 使用现有程序或账号需按该 Provider 的实际接入方式验证。只在缺失时按支持的安装流程补依赖；失败时保留诊断，不用伪造 ready 状态绕过。

已有会话继续使用已持久化的执行绑定，不因默认 Backend 或账号偏好变化而自行切换。新会话的回退遵循当前 Backend 的能力与条件，不用更换账号掩盖认证、权限或额度错误。停用账号不是强制终止会话；删除前按正式入口检查活跃登录与占用状态。

登记的初始并发与运行中的调度容量分开处理。运行容量通过 `UpdateProviderAccount` 或账号管理界面调整；更新登记配置不能覆盖已保存的调度设置。账号 home、认证位置和运行路径取当前 hosting 配置，不改 Unix `HOME` 来选择账号。

所有账号与设置操作使用当前身份的 HATG 指令。密钥只交给支持的授权/凭据入口，不写进 Skill、报告或日志。不凭账号名称、目录存在或旧会话推断授权仍有效。
