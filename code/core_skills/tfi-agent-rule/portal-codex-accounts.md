# Provider 账号与 Backend

本分支用于当前 Portal 的 Provider 登记、登录、运行时和 Backend 配置，不只适用于 Codex。先读取 `ListProviders`、`ListProviderAccounts`、`ListBackendRoles` 以及本次操作指令的 schema；参数与能力由当前 Provider 契约决定。

账号登记、授权状态、程序可用性和 Backend 分配是不同状态。完成登记后回读相应状态，必要时用一次真实请求验证；不能把账号已登记或已登录等同于 Resident 已能执行。

登记与管理使用 `RegisterProviderAccount`、`UpdateProviderAccountRegistration`、`DeleteProviderAccount`；Backend 分配使用 `UpdateBackendRole`。订阅登录使用 `OpenProviderAccountLogin`、`GetProviderAccountLogin`、`CancelProviderAccountLogin`；需要继续交互时按登录结果和 `UpdateProviderAccountLogin` 的 schema 提交。模型 API 使用 `CreateModelApi`、`UpdateModelApi`。不要从历史文件名或账号目录猜测当前登记清单。

- CLI Provider 可复用当前机器已有的可执行程序和已授权账号；Portal 管理的独立运行时也是可用选择，不强制用户重装或重新登录。
- Model API 路线按模型 API 登记和探测流程处理，不套用 CLI 安装、CODEX_HOME 或 systemd 要求。
- 新建 Resident 可使用 `backend_role` 或明确的 provider/account/settings；具体继承、默认角色及回退以当前创建结果和设置回读为准。修改角色不代表已经改写所有既有 Resident。
- 订阅账号的 `config.account_dir` 是 Provider 目录的规范绝对路径，不允许软链接。默认目录属于操作系统用户：`~/.codex-accounts/<名称>`、`~/.claude-accounts/<名称>`、`~/.kimi-accounts/<名称>`、`~/.antigravity-accounts/<名称>`；名称去掉账号 ID 的 `<provider>_` 前缀，主目录取操作系统用户记录，不取服务的 `HOME`。
- 新建订阅账号留空目录字段，在默认位置建空目录后登录；接入已有登录时直接登记现有目录，不复制凭据。删除登记保留目录，Portal 不移动或改名目录。
- 启动 Provider 只用登记路径设置 `CODEX_HOME`、`CLAUDE_CONFIG_DIR` 等变量。每个主机目录最多有一个 Portal 管理的常驻进程，父目录中的主机锁被占用时报冲突。终端直接运行 CLI 不受此锁约束。
- Portal 的 socket、索引备份、启动记录和锁放在实例 runtime，不写进 Provider 目录。不要硬编码源站路径或 service 名称。
- 使用现有程序或账号需按该 Provider 的实际接入方式验证。只在缺失时按支持的安装流程补依赖；失败时保留诊断，不用伪造 ready 状态绕过。

已有会话继续使用已持久化的执行绑定，不因默认 Backend 或账号偏好变化而自行切换。新会话的回退遵循当前 Backend 的能力与条件，不用更换账号掩盖认证、权限或额度错误。停用账号不是强制终止会话；删除前按正式入口检查活跃登录与占用状态。

登记的初始并发与运行中的调度容量分开处理。运行容量通过 `UpdateProviderAccount` 或账号管理界面调整；更新登记配置不能覆盖已保存的调度设置。订阅账号的认证位置取登记的 `config.account_dir`，不改 Unix `HOME` 来选择账号。

所有账号与设置操作使用当前身份的 HATG 指令。密钥只交给支持的授权/凭据入口，不写进 Skill、报告或日志。不凭账号名称、目录存在或旧会话推断授权仍有效。
