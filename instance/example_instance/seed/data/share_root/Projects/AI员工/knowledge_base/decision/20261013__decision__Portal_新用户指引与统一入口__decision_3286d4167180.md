---
kb_id: decision_3286d4167180
type: decision
project: AI员工
created_at: '2026-10-13'
version:
  number: 9
  created_at: '2026-10-13T03:38:20Z'
  created_by:
    user: tfi-exported-user
    agent: resident_974313e40e72
  updated_at: '2026-10-19T20:21:09Z'
  updated_by:
    user: ''
    agent: resident_8586ec083cd0
importance: 7
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/docs/guide/human.md
  - ${code_root}/docs/guide/agent.md
  - ${code_root}/docs/guide/admin.md
  - ${code_root}/scripts/portal_setup.py
  - ${code_root}/management_portal/http_user_enrollment.py
  - ${code_root}/management_portal/http_user_guide.py
  - ${code_root}/management_portal/user_guide.html
  - ${code_root}/management_portal/static/portal_nav.js
  - ${code_root}/scripts/portal_user.py
  - ${code_root}/tests/cases/test_user_guide.py
  related:
  - decision_30528aff7035
  - decision_6eac174d85bf
  - decision_aa4fdbac8364
  - decision_e486671af066
  - decision_898b8911a2ff
  - decision_bd5217d08c09
  - decision_27253e25f016
  - decision_b820faf42218
  - decision_22227d0ad075
  supersedes: []
  derived_from: []
---

# Portal 新用户指引与统一入口

Portal 的 User 可能是人类，也可能是以 User 身份工作的外部 agent（decision_6eac174d85bf）。新用户指引给三类读者各写一份：人类指引说明怎样加入、登录和使用网页界面；Agent 指引说明怎样领取身份、调用 HATG 指令、收发消息；管理员指引写给刚部署 Portal 的人，说明怎样创建首个管理员，以及怎样搭建、个性化和日常维护这个 Portal。三份指引共用一个公开入口 `/guide`，并出现在新用户可能经过的每一个位置：登录页、加入页、账号菜单、新账号的欢迎卡片、邀请转交说明、首个管理员的设置命令、命令行客户端、指令目录和未认证错误。

## 指引正文

- **正文是仓库里的三份 Markdown。** `docs/guide/human.md` 是人类指引，`docs/guide/agent.md` 是 Agent 指引，`docs/guide/admin.md` 是管理员指引。开源仓库可以直接阅读这些文件，运行中的 Portal 也只读这些文件，不另存副本。每类指引同时维护中文 `<名称>.md` 和完整英文 `<名称>.en.md`，正文、目录与 Markdown 原文链接使用同一语言。显式 `lang` 参数优先，其次复用 Portal 语言请求头、Cookie 与浏览器语言协商；没有语言信息时沿用中文。Guide 的公开语言选择器切换时重新读取对应正文，保留所选标签页。英文版缺失属于随附文档错误，不静默显示中文正文。
- **正文只写任何 Portal 部署都成立的内容。** 部署相关的值写成占位符，例如 `<Portal 地址>`；不写某台主机的路径、内部人名或尚未发布的入口。
- **Agent 指引里的指令名必须真实存在。** 正文用反引号写出的 HATG 指令名由 `tests/cases/test_user_guide.py` 与指令目录逐个比对；指令改名或删除时，测试失败提示同步修改指引。
- **Agent 指引以 agent 读者的实际试读为验收。** 修改 Agent 指引后，请一位以 User 身份工作的 agent 按正文步骤只读走一遍，按它的反馈修订。验收者只做设计反馈和只读验证，不为验收创建账号、轮换凭据或执行写操作。

## 公开入口

以下地址不需要登录，由 Web 的 public 阶段路由处理：

| 地址 | 返回 | 读者 |
| --- | --- | --- |
| `/guide` | HTML 页面，“人类用户 / Agent 用户 / 管理员”三个标签；`/guide#agent`、`/guide#admin` 打开对应标签 | 浏览器 |
| `/guide`（请求头 `Accept: text/markdown`，且不含 `text/html`） | 三份指引的 Markdown 索引 | agent |
| `/guide/human.md`、`/guide/agent.md`、`/guide/admin.md` | 指引原文，`text/markdown`；`ETag` 是正文的内容摘要，agent 据此判断指引是否更新，请求带 `If-None-Match` 且与当前 `ETag` 相同时返回 304、不带正文 | agent、工具 |
| `/llms.txt` | 同一份索引，`text/plain`，指向 Agent 指引和指令入口 | 按 llms.txt 约定发现站点的 agent |
| `/guide/portal_user.py` | 只依赖 Python 标准库的 User 命令行客户端 | 正在加入的 agent |

页面由服务端用 markdown-it（CommonMark + 表格，禁用原始 HTML）渲染。三份指引在同一页面中，每个标题锚点都带指引名前缀（`human-…`、`agent-…`、`admin-…`），因此不同指引里同名的标题不会冲突。正文里指向 `human.md`、`agent.md`、`admin.md`（可带 `#标题`）的相对链接在页面上改成切换标签并跳到对应标题；在仓库里阅读时，同一链接打开另一份文件。每个标签左侧是二级标题目录和“Markdown 原文”链接。已登录用户打开页面时，右上角的“登录”换成“返回 Portal”。

## 发现入口

新用户可能在不同位置开始，每个位置都指向 `/guide`：

- **刚部署 Portal、还没有任何账号的人**：在主机上运行 `scripts/portal_setup.py first-admin`，得到首个管理员的设置链接，命令输出同时给出 `/guide#admin`；加入成功后页面提供“打开管理员指引”（见下一节）。
- **还没有账号的人**：登录页底部有“有邀请？加入 Portal”和“新用户指引”；加入页的标题栏品牌链接和底部链接指向指引；加入页的 agent 路径给出 Agent 指引按钮。
- **收到邀请的人或 agent**：管理员创建邀请时生成的转交说明包含指引地址；agent 版还包含下载客户端和领取邀请的命令（decision_30528aff7035）。
- **刚创建的账号**：账号创建后 14 天内、尚未关闭欢迎卡片时，每个 Portal 页面右上角显示一次“欢迎加入 Portal”卡片，提供“打开新用户指引”“和 Ask Me Anything 对话”（打开本人的 Ask Me Anything Resident，decision_bd5217d08c09）和“知道了”。打开指引或点“知道了”后，用户偏好 `onboarding.welcome_dismissed` 设为 true，所有设备不再显示。创建时间来自 `/api/me` 的 `access.created_at`。
- **已登录的用户**：账号菜单的“新用户指引”在新标签页打开 `/guide`（decision_e486671af066）；聊天 `/portal-help` 的末尾也给出指引链接。
- **已经持有凭据、不经过网页的 agent**：`ListInstructions` 结果带 `guide: "/guide/agent.md"`（decision_aa4fdbac8364），agent 每次发现指令时都能看到；`portal_user.py guide` 打印 Agent 指引，不需要凭据。
- **还没有凭据就调用接口的 agent**：`/api/instructions/execute` 的 401 错误带 `details.guide: "/guide/agent.md"`（decision_898b8911a2ff）。

## 首个管理员

新部署的 Portal 没有账号，也不开放自助注册。首个管理员通过一次性的设置链接产生，账号本身仍走普通加入流程：

1. 部署者在运行 Portal 的主机上，用与 Portal Web 服务相同的环境（`PORTAL_DATABASE_URL`，以及设置了的 `PORTAL_DATABASE_NAMESPACE`；也可用 `--env-file` 指定该服务的环境文件）运行 `python3 scripts/portal_setup.py first-admin --username <用户名> [--display-name <显示名>] --url <Portal 地址>`。
2. 命令写入一条带 `first_admin` 标记的邀请（`user_invitations.first_admin`），有效期 1 天，只能使用一次。设置码只在终端打印一次，存储中只保存它的摘要。再次运行命令会撤销尚未使用的旧设置链接。
3. 加入页预览这条邀请时显示“你将以 X 成为这个 Portal 的首个管理员，设置链接有效至 …”。Portal 创建的第一个账号在创建它的同一事务中成为 Portal 管理员（账号记录 `category = portal_admin`，TFI portal 2026-10-19，decision_b820faf42218 §3.1），不论它来自设置链接、普通邀请还是 CreateUser；设置链接本身不授予任何东西。账号创建后记审计 `first_admin_granted`，领取响应带 `administrator: {granted: true}`；页面显示“你是这个 Portal 的首个管理员”并提供“打开管理员指引”（`/guide#admin`）。

只要 Portal 中已经有账号（已删除账号留下的记录也算），命令拒绝创建设置链接，已经发出的设置链接也在领取时拒绝（`This Portal already has accounts`）。之后的管理员由现有 Portal 管理员用 UpdateUser 的 `changes.category` 设为 `portal_admin`；管理员名单只有管理员能改，不能取消自己，所以 Portal 始终至少有一位启用中的管理员（decision_6eac174d85bf）。

## 与相邻设计的关系

- 邀请、加入页和个人访问令牌的规则属于 decision_30528aff7035 与 decision_6eac174d85bf；本条目只规定它们怎样指向指引。
- 欢迎卡片和账号菜单项属于共享顶栏（decision_e486671af066），由 `portal_nav.js` 渲染，页面不各自实现。
- 新增界面文字遵循中英文界面规范（decision_27253e25f016），源串写入 `portal_translations.json`。
