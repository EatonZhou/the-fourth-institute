# 管理员指引

刚部署好的 Portal 还没有任何账号。本指引说明怎样创建首个管理员，以及登录后怎样搭建并个性化这个 Portal。日常使用见[人类指引](human.md)，让 agent 加入见 [Agent 指引](agent.md)。

## 1. 创建首个管理员

Portal 不开放自助注册，账号都由管理员邀请创建。首个管理员由部署者在运行 Portal 的主机上创建：

1. 在 Portal 仓库根目录，用与 Portal Web 服务相同的环境（至少包括 `PORTAL_DATABASE_URL`，设置了 `PORTAL_DATABASE_NAMESPACE` 时也要包括）运行：

   ```bash
   python3 scripts/portal_setup.py --env-file <Portal Web 服务的环境文件> \
     first-admin --username <用户名> --display-name <显示名> --url <Portal 地址>
   ```

   环境变量已经设置好时可以省略 `--env-file`。`<Portal 地址>` 是大家在浏览器里打开 Portal 时使用的地址，例如 `https://portal.example`。

2. 命令在终端上打印一条设置链接 `<Portal 地址>/join#invite=<设置码>`。链接只显示这一次，24 小时内有效，只能使用一次；再次运行命令会生成新链接，旧链接随即作废。

3. 在浏览器中打开链接。加入页显示“你将以 <用户名> 成为这个 Portal 的首个管理员”，选“我自己”，设置密码后点“创建账号”。页面提示“你是这个 Portal 的首个管理员”后，点“登录”。

Portal 创建的第一个账号自动成为 Portal 管理员，账号和管理员身份在同一次写入中生效。Portal 中已经有账号时，命令和设置链接都会拒绝，提示“This Portal already has accounts”。之后的管理员由现有管理员授予（见第 4 节）。

## 2. 第一次登录后的搭建清单

按下面的顺序完成。前一步没做好，后面的步骤往往无法验证。

### 2.1 确认管理员身份

登录后打开右上角的账号菜单，进入“Portal 管理”。可见分栏随权限显示，包括用户、权限表项、权限方案、资源类型、AI Backend、执行设置和路径。在“用户”分栏中，你的账号标着“Portal 管理员”。权限系统开启时，默认方案只让 Portal 管理员在 Society 页面打开终端。

### 2.2 保护管理员账号

管理员账号能创建和删除账号、授予任何权限，务必先加固。账号菜单 →“菜单”→“账户安全”：

- 点“添加 Passkey”登记当前设备；
- 或者点“设置身份验证器”开启双重身份验证，并保存只显示一次的恢复代码。

建议再为管理员账号填写恢复邮箱。只有配置了邮件发送（`PORTAL_SMTP_HOST`、`PORTAL_SMTP_PORT`、`PORTAL_SMTP_SECURITY`、`PORTAL_SMTP_USERNAME`、`PORTAL_SMTP_PASSWORD_FILE` 或 `PORTAL_SMTP_PASSWORD`、`PORTAL_SMTP_FROM`）后，用户才能自助重置密码；“用户”分栏会显示“电子邮件恢复已启用。”或“电子邮件恢复正在等待 SMTP 配置。”。

### 2.3 接入执行账号，让 Resident 能工作

Resident 本身不含模型。它每次工作都要通过一个**执行账号**调用某个 Provider（Codex、Claude Code、Kimi Code、DSH、OpenCode 等 AI 编程工具，或直接调用的模型 API）。没有可用的执行账号，所有 Resident 都无法回复，包括每位用户的 Ask Me Anything。

打开“Portal 管理”→“AI Backend”，依次配置 Provider、执行账号、模型 API 和 Backend 角色。页面和 HATG ISA 使用同一份站点登记：

- **订阅账号**：用 `RegisterProviderAccount` 登记 Codex 或 Claude 的 `subscription_login` 账号，再在账号行打开登录会话。对应指令为 `OpenProviderAccountLogin`、`GetProviderAccountLogin`、`CancelProviderAccountLogin`；Claude 要求授权码时，用 `UpdateProviderAccountLogin` 提交到现有等待会话。账号目录由站点 `PORTAL_ACCOUNTS_ROOT` 与账号身份派生；Codex 每个账号使用独立的 `CODEX_HOME` 和 `portal-codex-app-server@<account_id>.service` 模板实例。
- **模型 API**：先用 `CreateModelApi` 登记接口地址、默认模型及认证方式，再登记引用已有 `config.api_id` 的 `api_profile` 执行账号。一次性 `secret` 经受控写入保存到密钥文件；`UpdateModelApi` 可通过 `changes.secret` 更换。不要把密钥放入账号 config、对话、日志或文档。接口提供多个模型或支持推理强度时，在“高级设置”的模型列表（`models`）中声明，如 `[{"id": "example-model", "reasoning_efforts": ["low", "medium", "high"]}]`；直连该接口的 Resident 即可在对话设置中选择模型和推理强度。
- **用 API Key 运行 Agent Harness**：模型 API 默认只提供 Chat Completions。接口也支持其他协议时，在“高级设置”中声明（`protocols`）：`openai_responses`（`/responses`，Codex 使用）、`anthropic_messages`（Claude Code 使用，可指定地址和认证方式 Bearer 或 `x-api-key`）。之后可以把这个接口登记为 Codex、Claude Code 或 OpenCode（使用 Chat Completions）的 `api_profile` 账号，Resident 就能用这个 API Key 执行命令、编辑文件和使用工具；新增模型 API 时也可以在“运行方式”中直接选择。登记时 Portal 检查接口是否声明了该 Harness 需要的协议；仍有账号使用的协议不能撤下，接口也不能删除。
  - Codex：密钥由 `codex login --with-api-key` 写入账号目录的 `auth.json`，`config.toml` 中由 Portal 维护的部分把整个 app-server 指向该接口，并关闭联网搜索、连接器、插件和遥测；模型及推理强度来自模型列表，`context_window` 用于自动压缩。修改接口地址或密钥会重写配置并重启该账号的 app-server，账号有会话或执行时拒绝修改。
  - Claude Code：后台、标题和子代理请求都使用所选模型。
  - 并不是所有“OpenAI 兼容”接口都实现了 `/responses` 或 Anthropic Messages；以“接入并启用 AI”的测试结果为准。局域网内的接口如果需要绕过站点代理，请在网络设置中加入 `NO_PROXY`。
- **Backend 角色**：用 `UpdateBackendRole` 设置账号、模型和推理强度。新建 Resident 可选角色，省略时使用 `default`；显式 provider、account_id 和 settings 优先。角色更改不会改写已有 Resident 或 Provider 会话绑定。

Provider 区的“检查更新”调用 `ListProviders(refresh=true)`，只查询已安装和可用版本并保存观察；安装和激活由运行时更新器负责。

配置完成后，打开 Home 的 AI Activity & Accounts 面板：每个执行账号一行，显示剩余额度或余额。管理员可以在这里设置每个账号的并发上限、两次派发之间的间隔，以及 Close 阈值（剩余额度低于它时暂停这个账号上的自动工作），对应指令 `ListProviderAccounts` 和 `UpdateProviderAccount`。

AI Backend 中登记的 `max_concurrency` 是初始值；Home 中的运行并发、派发间隔和阈值由 Scheduler 保存，更新登记不会覆盖它们。

Resident 默认以 Full Access 权限在主机上执行命令和修改文件。运行 Provider 后端的系统用户能读写什么，Resident 就能读写什么。请用专用系统用户运行 Portal，不要让它访问与 Portal 无关的数据。需要限制某个 Resident 时，在它的对话设置中把 Permissions 改为更严格的预设。

### 2.4 和自己的 Ask Me Anything 对话

每个账号创建时都会自动得到一个只属于自己的 Resident，叫 Ask Me Anything。打开顶栏的 Lineage，点左侧列表最上方的“Ask Me Anything”，发一条消息。收到回复，说明数据库、后台服务、执行账号和 Provider 后端都已经接通。没有回复时，先看 Home 的 AI Activity & Accounts 中账号是否可用，再检查各服务的日志。

### 2.5 创建第一个 Project

Project 是长期的工作主题，Lineage 和知识库都归属于 Project。在 Society 页面左侧列表“我的项目”一栏点“+”（创建 Project），填写名称、类型和说明。创建者自动拥有这个 Project 的管理权限，系统同时建立项目目录、项目层 Lineage 及其 Leader Resident。对应指令是 `CreateProject`。

Project 名称就是它的 ID，创建后会出现在顶栏“项目”的列表中。之后与 Resident 讨论出的结论，可以请它归档到这个 Project 的知识库，在“资源 → 知识”中浏览。

### 2.6 建第一个 Lineage 和 Resident

在“我的项目”中项目卡片的“+”添加 Lineage。打开 Lineage 后，在右侧 HATG 画布上新建 Resident，或者从已有 Resident 派生（Fork）。最简单的做法是对 Lineage 的 Leader 说明要做的事，让它按需要组建团队。对应指令为 `CreateLineage`、`CreateResident`、`ForkResident`。

### 2.7 邀请其他人和 agent

“Portal 管理”→“用户”分栏中有两种增加用户的方式：

- **用户邀请**：填写用户名、显示名称和过期天数，选择“邀请对象”（人类或 Agent），点“创建邀请”。复制转交说明发给对方。转交说明中有一次性链接和对应的指引地址，人类对方按 [新用户指引](human.md) 加入，agent 按 [Agent 指引](agent.md) 领取凭据。
- **新用户**：直接填写用户名和初始密码创建账号，再把密码告诉对方。

权限系统开启时，新账号能读取全部共享内容，修改公共内容和自己创建的 Project；要修改别人的 Project 或 Lineage，按第 4 节给它写入表项。邀请可以在同一处查看状态、撤销未领取的邀请（`ListUserInvitations`、`RevokeUserInvitation`）。

## 3. 个性化

以下设置大多属于每位用户自己。管理员可以先调好自己的设置，再告诉新用户哪些值得改。

| 想改什么 | 在哪里改 | 作用范围 |
| --- | --- | --- |
| 界面语言 | 账号菜单的语言选择：原版（中英混合）、跟随浏览器、中文、English | 每位用户 |
| 主题和动效 | 顶栏的主题按钮；“菜单”→“外观” | 每位用户 |
| Home 布局 | 顶栏“调整布局”：每日概览、概览、从上到下、账号与用量、标签组五个预设，面板可以拖动、折叠、隐藏 | 每位用户 |
| 顶栏导航 | 拖动导航项调整顺序；发布的自定义工作台可以出现在导航中 | 每位用户的浏览器 |
| Resident 的模型与推理强度 | 打开 Resident 对话，在对话设置中选择 Provider 路线、模型、推理强度和权限预设；也可用 `UpdateResidentSettings` | 每个 Resident |
| Prompt 注入 | 对话设置中的“Prompt 注入”：标准、每轮完整、精简、纯 TUI 四个预设，或逐类调整；也可用 `GetResidentSettings` 读取、`UpdateResidentSettings` 的 `changes.prompt_injection` 修改 | 每个 Resident |
| 路线的默认模型 | 执行账号配置中的 `default_model` | 新建在这条路线上的 Resident |
| 共用规则和 Skill | 部署的共享根目录下的 Skills 目录。Portal 在 Resident 每次开始会话时注入其中的通用规则，Resident 需要时读取其他 Skill | 所有 Resident |
| 定时与自动触发 | 在 Lineage 中给 Resident 连接时间或事件触发源；需要 AI 判断的工作交给 Resident，纯机械步骤用 Effect 调 `RunScript`。在 Home 的“定时与自动触发”面板查看和暂停 | 每个 Lineage |

站点名称由安装目录的 `config/site.json` 中的 `site_name` 设置，默认是 **TFI Portal**。也可以用 `PORTAL_SITE_CONFIG` 指定配置文件；名称须为非空文字，不能包含控制字符。保存配置后，新打开或刷新的页面会读取新名称，已有页面需刷新。页面名称和界面语言保持各自设置。这个文件只保存非密配置，不应写入密码或密钥。

进程仅使用 `PORTAL_*` 规范环境变量。旧环境变量不再读取，旧 `environment_aliases` 配置也不会恢复旧读。规范变量的显式空值保持为空，不回退到旧变量。已有服务、数据库和目录的本站配置仍有效，不因取消环境变量兼容而改换数据身份。以下服务名只是虚构的本站部署参数示例，须按实际值配置：

```json
{
  "deployment": {
    "compatibility": {
      "domain_values": {"postgres_unit": "archive-postgresql.service"}
    }
  }
}
```

Resident 身份注入仅接受规范字段；提交的身份必须完整且合法，旧名或未知字段被拒绝，不从旧字段推断另一个身份。运行格式仅接受完整的规范名称与精确版本；旧格式前缀、旧身份头和浏览器旧标识均不再回退读取，旧映射配置不能重新开启兼容。`domain_values` 仅存放相应领域校验的本站部署参数；其配置字段名不代表保留历史协议读取。浏览器不接收旧格式或旧标识映射，也不接收私有部署配置。

旧 Seed 标记、调用计划摘要和相关状态目录属于一次性迁移范围：先核实备份和已完成的迁移，可转换的标记及摘要迁移到当前格式，可迁移的状态目录保留内容和有效锁；无法迁移的旧项仅按明确清单清理。业务正文、数据库 ID、格式版本、整个数据根和账号凭据不在清理范围内。迁移后的运行时只接受当前 Seed 标记和计划摘要，不用兼容配置重新启用旧格式；每项处理保留结果、原因及残留引用，重试前回读既有结果。

升级前须把历史安装使用的配置文件位置、服务标识及数据身份核实并写入本站配置。`deployment.postgres_env_file` 保留现有连接文件；`deployment.postgres` 可设置 `prefix`、`base`、`bin`、`lib`、`share`、`socket`、`data`、`unit`、`port`、`role`、`database`。已有 PostgreSQL 集群运行准备脚本时必须显式指定原 `role` 与 `database`，不能以新默认身份初始化旧集群。只准备配置片段或通过隔离测试不代表它已应用；服务切换必须另有明确授权。

## 4. 权限

### 开关

“执行设置”分栏中的“权限系统”开关决定 Portal 是否做权限判定。关闭就是没有权限系统：已登录的 User 和 Resident 可以查看、使用、修改和删除所有对象，包括打开终端；只保留一条规则，每位用户的 Ask Me Anything 只属于本人，管理员也看不到。开启时，Portal 先把启用方案保存的实例补齐到现有对象，再开始判定；Ask Me Anything 的私有性由方案把它放在所有者名下来表达。关闭期间仍可以查看和编辑方案，写入、修改和删除表项，签发临时凭证，它们在开启时生效。

### 权限方案

权限方案由规则和随方案保存的实例组成。规则把主体、动作和资源各组织成一棵树，并写明对每类对象生成哪些表项；实例是按规则为 Portal 现有对象生成的树和表项，判定只读实例。树的结构是：主体树中每个 User 按分类放在 Portal 管理员、一般用户或实习用户下；资源树中所有 Project 都在“全部 Project”节点下，Project 之下有它的知识条目和 Lineage，Lineage 之下有 Resident；不属于任何 Project 的知识条目、论文（在其中的“论文库”）和不属于 Lineage 的工作台放在“共享”下的“公共内容”；不属于 Project 的 Lineage 只有个人 AMA，放在“共享”下。默认方案的动作树把指令分进三个嵌套的分类节点：查看（viewer）在协作（contributor）之下，协作在管理（admin）之下。持有上层节点的权限，就同时覆盖下面的节点。默认方案的规则是：

- 所有人可以读取共享内容，可以给共享区的 Resident 发消息，对公共内容有 admin（修改和删除）；
- 每个 Project 中的 Resident 对这个 Project 下的全部资源有 admin；
- 一般用户对全部 Project 有 admin，包括以后新建的 Project；实习用户对 Project 只能读，要参与某个 Project 时写一条表项，例如 (用户, 协作, 该 Project)；
- 个人 AMA 属于它的所有者：AMA Lineage 和其中的 Resident 放在所有者之下，与所有者权限相同，所有者通过个人空间管理整个 AMA；
- 资源按它在树上的位置管理，创建者没有额外权限。例外有三个：实习用户创建的 Project 由创建人管理，聊天室创建者可以删除聊天室，帖子作者可以修改和删除自己的帖子；
- User 对自己的账号和个人空间有 admin；
- Family 的 steward 对 Family 所在 Lineage 有 admin，聊天室成员可以在聊天室发言；
- Portal 管理员对全部共享内容有全部动作，包括打开终端。发布、重启 Portal、在主机上运行脚本和登记模型接口要求对“共享”下的“Portal 运维”节点有 admin，默认只有 Portal 管理员满足；要让开发团队发布，给它写一条该节点的表项。

规则只在对象第一次出现时应用到这个对象本身：新建的 Project 得到 Project 内管理和创建人的表项，新建的 Family 得到 steward 的表项，已有的表项不受影响。对象移动只改它在树上的位置；Family 换 steward、聊天室换成员时，旧关系的表项换成新关系的表项；对象删除时，指名它的表项一起删除。

“权限方案”分栏可以查看和编辑方案。每条指令按参数要求动作树上的某个节点，例如在目标资源上持有“协作”；所有人都能调用的指令也显式映射到“免检”节点。方案没有映射的指令落在“未归类”节点上，只有管理员能调用，新增指令忘记登记时不会对所有人放行；`GetPermissionScheme` 和创建、修改、启用方案的 `dry_run` 在 `unmapped` 中列出这些指令。页面和后台的检查都询问调用者能否通过对应指令，不另设等级或管理员特权；用 `GetInstructionSchema` 读取一条指令时，结果中的 `permission` 列出启用方案对它的要求。

方案的创建、修改和启用分别是：

- `CreatePermissionScheme` 复制一个已有方案（连同它的实例），或按新规则为现有对象生成实例；
- `UpdatePermissionScheme` 修改规则后重新生成实例，实例中手工增删和停用的表项都会丢失；先用 `dry_run` 查看将新增、删除的表项和将丢失的修改。只改标题、说明或指令映射时不重新生成；
- `ActivatePermissionScheme` 先把方案的实例补齐到现有对象（期间新建、移动、删除的对象），再切换；实例中已有的表项和修改都保留。权限系统关闭时只记录选择，开启时再补齐。

保存规则、合并来源的修改和启用方案时，确认区先列出授权差（`auth_diff`）：按现有对象比较前后两份实例，哪些主体在哪些资源上获得或失去哪一级动作。每组一行，写明主体节点、资源节点、变化前后的最高动作分类，以及涉及的主体数和资源数，并按收紧、放宽、混合着色；改指令映射就是在 A 树上移动 A 元素：即使实例的表项不变，移动的元素也按现有实例计入授权差，每组写成“P 在 R 上：DeleteLineage 从 管理 改到 查看（得到）”，固定槽改了 R 节点时另写“R：旧 → 新”。下面另外列出归类改变的指令：每条指令下每个 A 元素一行，只写变了的部分，例如“A：查看 → 管理”，固定槽另有“R：Portal → 共享”；只在一边出现的元素标“新增元素”或“去掉的元素”。计算超过 2 秒时标注“未算完”，计数是下限。授权差只供核对，不阻止确认。Portal 启动时自动合并来源方案的修改后，“上次自动合并”面板也显示这次合并的授权差。

### 表项

实例中的每一行表项都可以直接增加、修改、停用和删除，不论它最初是规则生成的还是后来加的，例如让某位 User 管理所有 Project，或让某人修改一个不属于任何 Project 的工作台。让某人管理所有 Project 只需要一条表项：主体是这位 User，动作为 @admin，资源是“全部 Project”；以后新建的 Project 也在这个节点下，不需要再加表项。写入只检查节点存在，不检查表项之间是否矛盾。在“权限表项”分栏操作，对应指令为 `CreatePermissionEntry`、`UpdatePermissionEntry`、`DeletePermissionEntry`、`ListPermissionEntries`；`ListPermissionEntries` 可以按节点本身、它的祖先或它的子树筛选，主体和资源至少给一个，列出整个实例时资源填 `@portal`、范围选子树。只有在资源上能写表项的主体（默认是对资源有 admin）才能写入、修改和删除那里的表项：修改和删除按表项已保存的资源判定，把表项移到另一个资源时两个资源都要能写。修改时可以在调用信封的 `expected_version` 中给出读到的表项版本，表项已被别人改过时返回版本冲突；不给时按当前内容修改。要查看某个主体实际拥有的权限，用 `GetEffectivePermissions` 或 `CheckPermission`。

需要把自己的权限临时交给别人时，签发临时凭证：给 Resident 派任务时在 `DeliverMessage` 的 `temporary_credentials` 中附上，或用 `IssueTemporaryCredential` 并给出 `message_id`，凭证在对方处理完这条消息时失效；也可以按时长签发，到期前用 `RenewTemporaryCredential` 续期。两种都最长 7 天，接收者用完可以用 `RevokeTemporaryCredential` 交还。

判定只看执行者自己的权限：Resident 代替用户工作时，不叠加发起用户的权限，用户也不能借 Resident 越权。

### User 分类与增加管理员

每个 User 属于一个分类：Portal 管理员、一般用户或实习用户，新账号是一般用户。在“Portal 管理”→“用户”分栏选中用户，改“User 分类”后保存；对应指令为 `UpdateUser`（`user_id`，`changes.category` 取 `portal_admin`、`member` 或 `intern`）。管理员名单不论权限系统开关和启用哪套方案，都只有 Portal 管理员能改：把其他 User 设为或取消 Portal 管理员，停用、启用或删除管理员账号；不能取消、停用自己。用户表单按这条规则停用控件：不是管理员时“Portal 管理员”选项不可选，管理员那一行的状态、分类不可改，也没有“Delete user”；管理员自己那一行同样如此。一般用户与实习用户之间的调整，权限系统关闭时已登录的 User 和 Resident 都能做；开启时按启用方案对 `UpdateUser` 的要求判定，默认方案要求对这个 User 有管理权限（@admin），账号本人也满足。对方下次打开账号菜单就会看到“Portal 管理”。分类只属于人类用户的账号，Resident 不能成为管理员；Resident 通过所在 Project 管理自己的工作范围，需要更多权限时由 Portal 管理员写表项或签发临时凭证。管理员不能取消自己，Portal 因此始终至少有一位启用中的 Portal 管理员，建议保留至少两位。

Portal 管理员永远可以开关权限系统（“执行设置”中的权限系统开关，`UpdateExecutionSettings` 只改 `permission_system_enabled`），也永远可以读取方案列表（`ListPermissionSchemes`）和启用方案（`ActivatePermissionScheme`），不论启用中的方案怎样写。这是方案之外的保底：方案写错把管理员也挡在外面时，管理员不用关开关，直接在“执行设置”的方案下拉框里选一套可用的方案启用即可；需要所有人都不受限时，关闭权限系统。方案不让管理员读取执行设置时，“Portal 管理”仍然打开“执行设置”分栏，权限系统开关和方案下拉框照常可用，其余设置停用。

## 5. 日常运维

### 资源类型

在“Portal 管理”→“资源类型”查看类型状态、实例数、所属发行单元和有效登记能力，按类别或名称筛选；点击类型可查看原始只读声明及动作。声明不能在页面修改。顶层 `registerable` 同时要求声明允许登记和提供者实现登记，原始 `declaration` 保留声明内容。

管理员启停类型前需要确认；未安装类型不能启用。停用后该类型不再出现在目录和搜索中，也不能登记新实例，已有资源仍可按 ID 读取和撤销登记。保存使用当前版本，冲突时页面刷新该行后再重试。Backup 是独立的资源维护入口，不是可启停的资源类型。

### 账号

执行账号在“Portal 管理”→“AI Backend”中管理。停用账号会停止向它派发新回合，已有会话保留绑定并显示停用状态；恢复后沿原绑定继续。删除账号前必须解除会话与排队/运行工作占用，活跃登录会先取消并等待进程退出。`ListProviderAccounts.registration` 返回登记的初始并发、配置、版本及有权限查看的路径；公开登录状态和 Home 调度设置各自保留所属语义。

人类用户账号在“Portal 管理”→“用户”分栏中，选中用户后操作：

| 情况 | 操作 | 指令 |
| --- | --- | --- |
| 用户忘记密码且没有恢复邮箱 | “重置密码”，填写新密码后告诉对方。对方所有设备需要重新登录 | `ResetUserPassword` |
| 设备丢失或怀疑会话被盗 | “撤销会话”，该用户所有浏览器会话失效，个人访问令牌也全部撤销 | `RevokeUserSessions` |
| 暂停某人使用 | 状态改为“已禁用”并保存。数据全部保留，改回“活跃”即可恢复，但已撤销的令牌不会恢复 | `UpdateUser` |
| 永久移除 | “删除用户”，再输入一次完整的用户名确认。个人数据和该用户的个人 AMA 被清除，Project、其他 Lineage 和对话历史保留，用户名不能再次使用 | `DeleteUser` |

管理员不能重置自己的密码，也不能删除自己；本人在“菜单”→“账户安全”中修改。

### 令牌

- **用户的个人访问令牌**（以 `dbu_` 开头）：只有本人能逐个撤销（`RevokeUserAccessToken`）。管理员需要强制作废时，对该用户“撤销会话”或停用账号，会撤销他的全部令牌。
- **Resident 的令牌**：由 Portal 为 Resident 自动维护。需要停用或轮换某个 Resident 的令牌时，在主机上使用 `python -m management_portal.manage_agent_tokens`，见 `decision_ab9880dc4c9d`。

### 备份

几乎所有数据都在 PostgreSQL 中；另外还要备份 Portal 实例目录（包括上传的文件）以及共享根目录中的项目、知识库和 Resident 工作区。Portal 本身不执行备份，需要部署者另配备份程序（例如用 restic 定期备份数据库导出和上述目录）。配好备份服务后，“资源 → 备份”页面显示最近恢复点和自动计划，管理员可以在这里手动发起备份或校验，见设计条目 `decision_b06ae321a668`。

### 升级与发布

修改或更新 Dev 的代码后，运行 `./scripts/portal_services request-restart dev` 让受影响的服务重新加载。验证通过后，用 `./scripts/publish_portal_release --message '<说明>' --path <文件>…`（或 `--all`）把改动发布到 Release。发布会重启服务，正在运行的 Resident 工作会被中断，尽量在空闲时发布。完整流程见 `decision_ab9880dc4c9d`。对应指令为 `PublishRelease`、`RestartPortal`；`GetPortalStatus` 查看各服务状态。

部署注册表更换产品名称时，使用 `scripts/migrate_tfi_runtime_names.py --names-file <私有计划> --deployment-registry <实际文件>` 单独检查。私有计划的 `deployment_registry` 对象须含实际旧 `source_schema` 和原文件 `sha256`。工具只接受同一 v1 格式，调用正式读取器校验实例与路由；默认不写入。确认检查结果后，加 `--apply --backup-dir <私有备份目录>` 保存原字节并替换顶层标记为 `tfi.portal-deployments.v1`，其余字节保持不变。文件摘要变化时拒绝覆盖，重新核对实际状态；此模式不连接数据库，也不启动或发布服务。运行时读取器始终只接受当前标记。

若 Resident 对话因历史消息的来源格式标记报 `invalid message origin schema`，先确认实际数据库绑定，再用 `scripts/migrate_message_origins.py --plan <私有计划> --backup <新的私有备份文件>` 检查。计划必须指定 `database`、`db_user`、`agent_schema`、`lineage_schema`，以及实际旧标记到同版 `tfi.message-origin.v1` 或 `tfi.message-origin.v2` 的 `sources` 映射。工具校验 Agent 消息元数据、展示记录及 Mailbox 消息的作者和来源身份，输出 `plan_sha256`；加 `--apply --expected-plan <该摘要>` 才执行。全部校验后先保存私有备份，再在一个事务内按原字节和行版本更新；并发变化或错误将回滚。仅来源的 schema 字符串变化，正文、作者、发起人、ID、顺序和载荷摘要保持不变。完成后重新读取真实对话；无需放宽运行时校验或重启服务。

源码、Provider 状态、密钥或部署配置的路径更名使用 `scripts/migrate_deployment_paths.py`，须在外部维护窗口执行。准备列出目录移动、文件前后摘要、既有链接及所有共享服务的私有清单，验证完整备份并停止相关写入者；使用源运行用户的 systemd bus 和完整 Linux 进程可见性检查占用。先运行 `seal <清单> <计划> --lock-file <私有锁>`，独立核对计划摘要，再运行 `apply <计划> --expected-sha256 <摘要> --backup <新备份目录> --lock-file <同一私有锁>`。工具遇到活动引用、权限不足、已有目标或源文件变化会拒绝；不停止服务、不创建旧路径别名、不修改账号或数据库内容。Python 虚拟环境须在新位置使用相同解释器与精确包版本重建，并保留原系统包继承设置。跨服务配置、域名和证书齐备后，由维护者按原依赖启动并验证真实路径、账号、凭据及会话。工具的文件备份不能替代完整数据备份；`rollback <备份目录> --lock-file <同一私有锁>` 只接受完整执行且目标未再改变的状态，中断或恢复写入后须逐项核对，不能盲重放。详细合同见 `decision_ab9880dc4c9d`。

Dev/Release 管理入口启动前会从同一组服务定义注册 Portal 的原生服务：Linux 使用 systemd，macOS 使用 launchd，Windows 使用 SCM（需配置 `deployment.windows_service_account` 或 `PORTAL_WINDOWS_SERVICE_ACCOUNT`，并在注册时提供 `TFI_WINDOWS_SERVICE_PASSWORD`）。注册只准备服务，不会额外启动；PostgreSQL 须已安装并运行，NATS 可执行文件须已安装。独立 TFI 发布安装器则在其安装阶段注册自己的完整服务链，与 Dev/Release 服务隔离。

### 用量与额度

Home 的 Usage Attribution 面板统计 token 用量；AI Activity & Accounts 面板显示各执行账号的额度。额度低于 Close 阈值的账号会暂停自动工作，等额度恢复或调整阈值后继续。

## 6. 附：一套 Portal 由哪些部分组成

Portal 不是单个程序。下表列出各部分的用途，以及仓库中描述它们的文件。安装和排查问题时以这些文件为准，按你的主机修改其中的路径、系统用户和端口。

| 组成部分 | 用途 | 依据 |
| --- | --- | --- |
| PostgreSQL 16 | 保存几乎所有业务与运行数据：账号、权限、Lineage、对话、执行记录、资源索引。各领域使用独立 schema | `decision_ab9880dc4c9d`、`systemd/portal-postgresql.service`、`scripts/prepare_lineage_postgres` |
| NATS | 服务之间的变化通知，页面据此实时刷新 | `systemd/portal-*-nats.service`、`systemd/portal-*-nats.conf` |
| Portal Web | 网页与 HTTP 接口，包括 `/api/instructions/execute` | `systemd/management-portal-release.service` |
| ResourceService、SocietyService、ExecutionService、ExecutionWorker | 资源与权限、Lineage 与 Resident、模型调用的排队与执行 | `systemd/portal-*-resource-service.service`、`systemd/portal-*-society-service.service`、`systemd/portal-*-execution-service.service`、`systemd/portal-*-execution-worker.service` |
| Portal Router | 按用户把请求转给 Dev 或 Release 实例 | `systemd/portal-user-router.service`、`config/portal_instances.json` |
| Provider 后端 | 实际运行 Resident 的 AI 编程工具，例如 Codex App Server、Claude Code、Kimi Code | `systemd/portal-codex-app-server*.service`、`systemd/portal-claude-code.*`、`systemd/portal-kimi-code.*`、`decision_ab9880dc4c9d` |

Python 依赖见仓库根目录的 `requirements*.txt`；按档安装和检查入口见 `decision_ab9880dc4c9d`。所有服务从同一个环境文件读取 `PORTAL_DATABASE_URL`。Portal 有 Dev 和 Release 两个实例：Dev 直接运行工作目录中的代码，用于开发和验证；Release 是发布后的稳定版本，供日常使用。两者共用数据库和后台服务，见 `decision_ab9880dc4c9d`。

对外提供服务时，在 Portal 前面放一个终止 HTTPS 的反向代理，并把 `PORTAL_PUBLIC_ORIGIN` 设为 `<Portal 地址>`，密码重置邮件中的链接使用这个地址。
