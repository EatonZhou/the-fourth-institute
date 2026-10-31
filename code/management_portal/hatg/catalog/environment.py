"""Runtime environment instructions (decision_4527448ad07f)."""
from __future__ import annotations

from management_portal.execution.service_settings import RETENTION_DAYS

from ..declarations import FIXED, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target="", owner="web"):
    return D(name=name, category="environment", owner=owner, description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


COMPONENTS = ("web", "resource", "execution-service", "execution-worker", "lineage")
MODEL_API = R("api", "object", "模型接口登记：api_id、base_url、default_model、provider、enabled、version、routes…；不含密钥")
SETTINGS = ("执行设置：model_request_timing_enabled、harness_recording_enabled、permission_system_enabled、"
            "harness_retention_days")
LOGIN_SESSION = ("登录会话：account_id、state（pending、awaiting_user、succeeded、failed、expired）、verification_code、"
                 "verification_url、error、expires_at、requires_code_input")
REGISTRATION = "账号登记：account_id、provider、kind、route_id、config、max_concurrency、enabled、version、login_state…"

DECLARATIONS = (
    _d("GetExecutionSettings", FIXED, "管理员读取全系统执行设置。", (),
       (R("settings", "object", SETTINGS),), owner="execution"),
    _d("UpdateExecutionSettings", FIXED, "管理员修改全系统执行设置：请求时间记录和 Harness 记录通过系统 Event 开关，另有权限系统开关和 Harness 保留天数；至少提供一项。", (
        P("model_request_timing_enabled", "boolean", "打开或关闭模型请求时间记录"),
        P("harness_recording_enabled", "boolean", "打开或关闭 Harness 调用记录"),
        P("permission_system_enabled", "boolean",
          "关闭后不再调用权限系统：已登录的 User 与 Resident 可以查看、使用、修改和删除所有对象，个人 AMA 仍只属于所有者；"
          "重新开启前先把启用中权限方案的实例按 Portal 现有对象补齐，几秒内对所有 Portal 服务生效；"
          "Portal 管理员只改这一项时永远通过，不问方案"),
        P("harness_retention_days", "integer", "Harness 证据、调用记录和未被引用的产物保留天数",
          minimum=RETENTION_DAYS["harness_retention_days"][0], maximum=RETENTION_DAYS["harness_retention_days"][1]),
    ), (R("settings", "object", "修改后的" + SETTINGS), P("reconciled", "object", "开启权限系统时的补齐结果：scheme_id、regenerate、added_nodes、moved_nodes、removed_nodes、added_entries、removed_entries")),
       mutates=True, owner="lineage"),
    _d("GetPortalStatus", FIXED, "读取 Portal 实例与各组件的运行状态。", (
        P("instance", description="只读取这个实例；省略时读取 dev 和 release", enum=("dev", "release")),
    ), (R("instances", "array", "每个实例的 Web 健康检查、源码目录与共享服务单元"),)),
    _d("GetRuntimeCapacity", FIXED, "只读观察共享数据库连接、本机磁盘与 Execution 队列容量；排队总数包含配额等等待。", (),
       (R("capacity", "object", "tfi.runtime-capacity.v1；各来源独立返回 ok 或 unavailable"),)),
    _d("RestartPortal", FIXED, "请求重启一个 Portal 实例，或实例中的指定组件。", (
        R("instance", description="要重启的实例", enum=("dev", "release")),
        P("components", "array", "省略时重启 web、resource、execution-service、lineage，不含 execution-worker",
          items={"type": "string", "enum": list(COMPONENTS)}),
    ), (R("request_id", description="执行这次重启的临时单元 tfi-pair-restart-<id>"),
        R("components", "array", "请求的组件；空列表表示默认组件")), mutates=True),
    D(name="PublishRelease", category="environment", owner="web", decides=FIXED,
      description="把调用者指定的 Dev 代码同步到 Release，并返回发布提交；只能在 Dev 实例执行。",
      parameters=(
          R("message", description="本次发布的提交说明"),
          P("paths", "array", "要发布的文件或目录", items={"type": "string"}),
          P("all", "boolean", "为 true 时发布全部受 Git 管理的源码"),
      ),
      result=(R("commit", description="Dev 上的发布提交"),
              R("paths", "array", "Release 相对上次发布改变的文件；合并发布时含同批其他提交的改动"),
              R("health", "object", "激活状态：status 为 pending，receipt 是激活完成后写入健康检查与发布事实的文件"),
              P("push", "object", "把发布提交推送到各 Git 远端的结果")),
      mutates=True, one_of=(("paths",), ("all",))),
    _d("GetDeviceStatus", FIXED, "读取 GPU 或磁盘的当前状态：kind 选择设备种类，省略 device_id 时读取这一种类的全部设备及汇总。", (
        R("kind", description="gpu 为 GPU 服务器与 GPU，disk 为磁盘", enum=("gpu", "disk")),
        P("device_id", description="只读取这一台：GPU 为 Device 的统一 ID，磁盘为磁盘资源 ID（如 nas_storage）"),
        P("refresh", "boolean", "跳过缓存重新采集"),
    ), (R("devices", "array", "GPU：全部服务器，或指定的一台，未知 ID 时为空；磁盘：全部磁盘，或指定的一块"),), target="device_id"),
    _d("OpenTerminal", FIXED, "为调用者 User 打开一个 Portal 主机 shell，或经 SSH 连到一台 Device 的终端；会话之后由 Portal 页面读写。"
       "权限系统开启时按启用的方案判定，默认方案只给 Portal 管理员；关闭时任何已登录的 User 都可以打开。", (
        P("device_id", description="Device 的统一 ID；省略时打开 Portal 主机 shell"),
        P("cwd", description="主机 shell 的起始目录"),
        P("cols", "integer", "终端列数，默认 100；超过 240 按 240", minimum=20, maximum=500),
        P("rows", "integer", "终端行数，默认 30；按 6–80 生效", minimum=5, maximum=200),
    ), (R("session_id", description="终端会话 ID，Portal 页面用它读写终端"), R("alive", "boolean", "终端进程是否仍在运行"),
        P("cwd", description="终端的起始目录")), mutates=True),
    _d("ListModelApis", FIXED, "列出已登记的模型接口。", (),
       (R("apis", "array", "模型接口登记：api_id、base_url、default_model、provider、enabled、version、"
          "harness_protocols（Agent Harness 可用的协议）…；不含密钥"),),
       owner="execution"),
    _d("CreateModelApi", FIXED, "登记一个新的模型接口。", (
        R("api_id", description="新模型接口的 ID"),
        R("base_url", description="接口基础地址，请求发往其下的 chat/completions"),
        R("default_model", description="调用时未指定 model 所用的模型"), P("secret_file", description="密钥文件路径"),
        P("secret", description="一次性密钥，只写入站点密钥文件，不回显"),
        P("provider", description="服务商名称"),
        P("routes", "array", "明确绑定的服务路线；每项包含 backend 和 route_id", items={"type": "object"}),
        P("models", "array", "模型目录；每项含唯一的 id，可选 reasoning_efforts（该模型接受的推理强度，"
          "如 low、medium、high）、enabled 等", items={"type": "object"}),
        P("protocols", "object", "Agent Harness 可用的其他协议：openai_responses（{}，Codex 使用）、"
          "anthropic_messages（可含 base_url 与 auth=bearer|x-api-key，Claude Code 使用）；Chat Completions 无需声明"),
        P("default_parameters", "object", "协议允许的默认生成参数"),
        P("timeout_seconds", "number", "单次请求超时秒数，默认 360", minimum=1),
        P("retries", "integer", "请求失败后的重试次数，默认 0", minimum=0),
        P("headers", "object", "不含密钥"), P("enabled", "boolean", "是否启用，默认 true；停用后不能调用"),
    ), (MODEL_API,), mutates=True, owner="execution"),
    _d("UpdateModelApi", FIXED, "修改一个模型接口的登记。", (
        R("api_id", description="模型接口 ID"),
        R("changes", "object", "要修改的登记字段，如 base_url、default_model、models、protocols、enabled；不能改 api_id"),
        P("secret", description="一次性密钥")), (
        MODEL_API, R("version", "integer", "修改后的登记版本"),
    ), mutates=True, target="api_id", owner="execution"),
    _d("DeleteModelApi", FIXED, "删除一个模型接口的登记；仍有执行账号使用时拒绝。", (R("api_id", description="模型接口 ID"),),
       (R("api_id", description="被删除的模型接口"),),
       mutates=True, target="api_id", owner="execution"),
    _d("CompleteChat", FIXED, "调用一个已登记的模型接口，完成一次无状态的文本生成。", (
        R("api_id", description="模型接口 ID"), P("model", description="省略时使用接口的 default_model"),
        R("messages", "array", "OpenAI 兼容的对话消息，每项含 role 与 content", items={"type": "object"}),
        P("options", "object", "生成选项；typesafe_system_one 接口必须提供 questions（choice、score、noul）"),
        P("response_schema", "object", "回答必须满足的 JSON Schema；提供时把回答解析为 JSON 并按它校验"),
    ), (R("message", "object", "模型的回答消息：OpenAI 兼容的 role、content…"),
        R("usage", "object", "接口返回的 token 用量；未返回时为空"),
        P("structured", "any", "提供 response_schema 时的校验结果，或 typesafe_system_one 的结构化答案")), mutates=True, target="api_id", owner="execution"),
    _d("ProbeModelApi", FIXED, "向模型接口的各协议地址各发一次很短的请求，检测它为 Agent Harness 提供哪些协议；不保存任何内容。", (
        P("api_id", description="已登记的模型接口；其余参数覆盖它的登记值"),
        P("base_url", description="接口基础地址"), P("default_model", description="检测请求使用的模型"),
        P("secret_file", description="密钥文件路径"), P("secret", description="一次性密钥，只用于本次检测，不保存、不回显"),
        P("headers", "object", "不含密钥"),
        P("anthropic_base_url", description="Anthropic Messages 地址；省略时用登记的地址或去掉 /v1 的接口基础地址"),
    ), (R("model", description="检测请求使用的模型"),
        R("results", "object", "按协议（openai_chat、openai_responses、anthropic_messages）给出 status（supported、unsupported、"
          "auth_failed、rejected、error、unreachable）、http_status 与 message；anthropic_messages 支持时另有 auth（bearer 或 x-api-key）")),
       mutates=True, target="api_id", owner="execution"),
    _d("ListProviderAccounts", FIXED, "读取执行账号的额度窗口、余额、Codex credits 或可用性与调度设置。", (
        P("account_id", description="只读取这个执行账号；省略时读取全部"),
        P("refresh", "boolean", "先请求重新采集额度与余额"),
    ), (R("accounts", "array", "执行账号：account_id、provider、额度窗口或余额、login_state、max_concurrency、"
          "派发间隔、close_threshold、registration；Agent 账号包含 kind 和 migration_type（id、label、default_priority），用于自动迁移的账号种类排序"),
        R("default_routes", "object", description="Provider 到默认执行路由 ID 的映射")), owner="execution"),
    _d("ListProviders", FIXED, "列出 Provider、账号种类、能力与运行时状态。", (
        P("refresh", "boolean", "检查可用版本并保存观察；不安装或激活运行时"),
    ), (R("providers", "array", "Provider：provider、title、kinds、capabilities、runtime（已装与可用版本）、"
          "default_route_id、account_count"),), owner="execution"),
    _d("PrepareProviderRuntime", FIXED, "准备所选 Provider 的运行环境；复用已装程序，缺失时异步安装。", (
        R("provider", description="Provider 名称"),
    ), (R("preparation", "object", "ready、preparing、busy、failed 或 pending；ListProviders 或账号接入状态可继续观察"),), mutates=True, target="provider", owner="execution"),
    _d("GetProviderAccountSetup", FIXED, "读取账号的运行环境、授权、模型与声明能力。", (
        R("account_id", description="执行账号 ID"),
    ), (R("setup", "object", "运行环境、准备进度、授权状态、模型目录与工具能力；尚未执行测试"),), owner="execution"),
    _d("CheckProviderAccount", FIXED, "通过实际执行层发送最小测试消息；返回可观察的持久执行 ID。", (
        R("account_id", description="已登记的执行账号"), P("model", description="测试模型，省略时使用默认模型"),
    ), (R("execution_id", description="测试执行 ID"), R("status", description="queued")), mutates=True, target="account_id", owner="execution"),
    _d("GetProviderAccountCheck", FIXED, "读取账号测试结果；账号登记或模型 API 配置改变后原测试失效。", (
        R("account_id", description="执行账号"), R("execution_id", description="CheckProviderAccount 返回的执行 ID"),
    ), (R("check", "object", "status、passed、model、configuration_current 与脱敏提示"),), owner="execution"),
    _d("OpenProviderAccountLogin", FIXED, "打开订阅账号登录会话。", (
        R("account_id", description="执行账号 ID"),
        P("region", description="仅 Kimi：mainland-cn 中国区或 global 国际区；切换区域会替换未完成的授权流程，省略则沿用原生选择", enum=("mainland-cn", "global")),
    ), (R("session", "object", LOGIN_SESSION),
        P("login_options", "object", "Kimi 登录的 regions（id、label）及当前 region")), mutates=True, target="account_id", owner="execution"),
    _d("UpdateProviderAccountLogin", FIXED, "向已有登录会话提交 Claude 授权码或 Antigravity 完整回调地址。", (
        R("account_id", description="执行账号 ID"), R("authorization_code", description="Claude 授权码或 Antigravity 完整回调地址；只送到当前登录进程，不保存或回显"),
    ), (R("session", "object", "提交授权码后的" + LOGIN_SESSION),), mutates=True, target="account_id", owner="execution"),
    _d("GetProviderAccountLogin", FIXED, "读取登录会话和账号认证状态。", (R("account_id", description="执行账号 ID"),),
       (R("session", "any", "登录会话；尚未开始时为 null"),
        R("login_state", description="账号认证状态：logged_in、login_expired 或 logged_out"),
        P("login_options", "object", "Kimi 登录的 regions（id、label）及当前 region；无历史流程时建议 mainland-cn")),
       owner="execution"),
    _d("CancelProviderAccountLogin", FIXED, "取消订阅账号登录并结束登录进程。", (R("account_id", description="执行账号 ID"),),
       (R("session", "any", "取消后的会话；尚未开始时为 null"),), mutates=True, target="account_id", owner="execution"),
    _d("RegisterProviderAccount", FIXED, "登记执行账号并创建 Provider 目录与服务。", (
        R("account_id", description="新执行账号 ID：小写字母开头，可含数字、_ 和 -"),
        R("provider", description="ListProviders 返回的 Provider 名称"),
        R("kind", description="账号种类，须是该 Provider 支持的种类", enum=("subscription_login", "api_profile", "service")),
        P("route_id", description="执行路由 ID；省略时由账号 ID 得出（codex 去掉 codex_ 前缀），provider_api 与 kimi 留空"),
        P("config", "object", "Provider 专属设置：api_profile 为 api_id；codex 为 fallback_account_ids、is_default；"
          "claude 为 models、default_model"),
        P("max_concurrency", "integer", "并发上限，默认 8", minimum=1, maximum=256),
        P("enabled", "boolean", "是否启用，默认 true"), P("secret", description="api_profile 的一次性密钥"),
    ), (R("registration", "object", REGISTRATION),), mutates=True, owner="execution"),
    _d("UpdateProviderAccountRegistration", FIXED, "修改账号登记；调用信封的 expected_version 给出登记版本时按它比较，省略时直接更新。调度设置仍由 UpdateProviderAccount 修改。", (
        R("account_id", description="执行账号 ID"),
        R("changes", "object", "要修改的登记字段：config、route_id、enabled、max_concurrency"),
    ), (R("registration", "object", "修改后的" + REGISTRATION),), mutates=True, target="account_id", owner="execution"),
    _d("DeleteProviderAccount", FIXED, "删除空闲账号及其主机目录与服务实例。", (R("account_id", description="执行账号 ID"),),
       (R("account_id", description="被删除的执行账号"),), mutates=True, target="account_id", owner="execution"),
    _d("ListBackendRoles", FIXED, "读取 Backend 角色映射。", (),
       (R("roles", "array", "Backend 角色：role、account_id、model、reasoning_effort、version…"),), owner="execution"),
    _d("UpdateBackendRole", FIXED, "设置 Backend 角色；调用信封的 expected_version 给出角色版本时按它比较（尚未建立的角色为 0），省略时直接写入。", (
        R("backend_role", description="角色名，如 default"),
        R("account_id", description="角色指向的执行账号 ID，须已启用"),
        P("model", description="按该角色创建 Resident 时使用的模型"),
        P("reasoning_effort", description="按该角色创建 Resident 时使用的推理强度"),
        P("verification_execution_id", description="接入流程测试回执；提供时必须匹配当前账号、模型及登记与模型 API 版本"),
    ), (R("role", "object", "写入后的角色：role、account_id、model、reasoning_effort、version…"),),
       mutates=True, target="backend_role", owner="execution"),
    _d("GetProviderUsageEvents", FIXED, "分页读取 Codex 账号的脱敏用量与额度观察；不返回消息正文或凭证。", (
        R("account_id", description="执行账号 ID"), R("since", description="含时区的 ISO 时间，包含起点"),
        R("until", description="含时区的 ISO 时间，不含终点；窗口最多 60 天"),
        P("cursor", description="上一页结果的 next_cursor，原样交回；不填时读第一页"),
        P("limit", "integer", "每页最多的记录数，默认 1000", minimum=1, maximum=1000),
    ), (R("account_id", description="被读取的执行账号"),
        R("records", "array", "脱敏记录：source（会话文件）与 record（timestamp、type、payload：会话元数据、模型、token 用量或额度窗口）"),
        R("next_cursor", description="还有后续记录时交回 cursor 读下一页；读完时为空")), owner="execution"),
    _d("UpdateProviderAccount", FIXED, "修改一个执行账号的调度设置：并发上限、相邻派发间隔与暂停阈值。", (
        R("account_id", description="执行账号 ID"),
        R("max_concurrency", "integer", "并发上限", minimum=1, maximum=256),
        R("dispatch_interval_min_seconds", "number", "相邻派发的最小间隔秒数，不能大于最大间隔", minimum=0, maximum=3600),
        R("dispatch_interval_max_seconds", "number", "相邻派发的最大间隔秒数", minimum=0, maximum=3600),
        P("close_threshold_percent", "number", "额度型账号：剩余额度 ≤ 该百分比时暂停账号上的自动工作", minimum=0, maximum=100),
        P("close_threshold_amount", "number", "余额型账号：余额 ≤ 该原币金额时暂停账号上的自动工作", minimum=0),
    ), (R("settings", "object", "生效的调度设置：account_id、max_concurrency、dispatch_interval_min/max_seconds、"
          "close_threshold、updated_by、updated_at"),), mutates=True, target="account_id", owner="execution"),
    _d("GetUsage", FIXED, "读取按 Resident、Lineage、Project 和时间归因的 token 用量；Resident 的用量经它当前的 Lineage 归入该 Lineage 所属的 Project。", (
        R("period", description="统计周期：按本地时间的今天、本周、本月或今年", enum=("today", "week", "month", "year")),
        R("group_by", description="排名与时段归属按 Resident、Lineage 或 Project 分组", enum=("resident", "lineage", "project")),
        P("provider", description="只统计这个 Provider；省略时统计全部"),
        P("account_id", description="只统计这个执行账号；省略时统计全部"),
        P("include_cached", "boolean", "是否计入命中缓存的输入；缺省不计入，与 Home 用量的默认口径一致"),
    ), (R("usage", "object", "totals（周期总量）、series（时间序列）、ranking（归属排名）、updated_at（账本更新时间）与 source（来源状态）；"
                              "collection_started_at/collection_finished_at 为实际账本读取及汇总采集起止，缓存读取保留原值；"
                              "collection_windows 含各 UTC 查询窗口的 start/end、实际 read_started_at/read_finished_at 与 status；"
                              "bucket_groups 按 group_by 返回完整时段归属（bucket、groups[{id,total_tokens,anonymous?}]），隐藏 ID 合并匿名空 ID；"
                              "today 为本地 24 小时，week/month 按日、year 按月，不截断组；"
                              "token 分为 input、cached_input、output、reasoning_output；只读取小时用量账本"),),
        owner="execution"),
)
