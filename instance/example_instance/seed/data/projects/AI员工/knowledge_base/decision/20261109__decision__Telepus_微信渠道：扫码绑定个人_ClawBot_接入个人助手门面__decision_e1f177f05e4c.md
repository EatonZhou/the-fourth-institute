---
kb_id: decision_e1f177f05e4c
type: decision
project: AI员工
created_at: '2026-11-09'
version:
  number: 16
  created_at: "2026-11-09"
  created_by:
    user: ""
    agent: "resident_a3dc51f3859d"
  updated_at: "2026-11-14T10:43:24Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 6
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/channel.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/weixin_client.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/weixin_store.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/weixin_binding.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/weixin_format.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/weixin_adapter.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/weixin_service.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/telepus/router_store.py
  - https://github.com/Tencent/openclaw-weixin
  - https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md
  - https://github.com/xmanrui/dsh-im
  related:
  - decision_66f5be947b67
  - decision_692a484e9bea
  supersedes: []
  derived_from: []
metaness: 2
---

# Telepus 微信渠道：扫码绑定个人 ClawBot 接入个人助手门面

Telepus 的微信渠道让 Portal 用户在微信里使用自己的个人助手门面：用户在 Portal 页面上用微信扫码，得到一个只属于自己的微信机器人联系人（微信 ClawBot）；之后发给它的消息交给这位用户的个人助手门面，门面上的回复发回这个联系人。按 Resident ID 精准路由、`/history`、问答卡片和引用的语义与 Telegram 渠道相同（decision_66f5be947b67），本文说明微信渠道独有的部分：接口、绑定、消息格式与发送额度，以及 Telepus 为同时承载两个渠道而分出的渠道层。

## 微信 ClawBot 接口

微信为 AI 助手开放了 ClawBot 接口（iLink，HTTPS JSON，地址 `https://ilinkai.weixin.qq.com`）。腾讯以 MIT 许可发布了参考插件 `Tencent/openclaw-weixin`，其中的 `docs/protocol_zh_CN.md` 说明了协议；接口没有正式的服务端契约，行为以该插件和实测为准。Telepus 用到的部分：

| 接口 | 用途 |
| --- | --- |
| `ilink/bot/get_bot_qrcode`、`ilink/bot/get_qrcode_status` | 生成绑定二维码，轮询扫码结果；确认后得到 `bot_token`、`ilink_bot_id`、`ilink_user_id`、`baseurl` |
| `ilink/bot/getupdates` | 长轮询取新消息，游标为 `get_updates_buf` |
| `ilink/bot/sendmessage` | 发送文字、图片、文件、视频；返回服务端消息 ID |
| `ilink/bot/getuploadurl` 与 CDN | 媒体以 AES-128-ECB 加密后上传，收到的媒体同样加密，下载后解密 |
| `ilink/bot/getconfig`、`ilink/bot/sendtyping` | 取得输入状态票据，显示“正在输入” |
| `ilink/bot/msg/notifystart`、`notifystop` | 开始与停止收取时通知微信 |

一个绑定对应一个机器人：`bot_token` 只代表扫码人自己的 ClawBot，`ilink_user_id` 是扫码人。机器人只接受这位扫码人的消息，别人的消息不处理。

接口有三条约束，决定了下面的设计：

- **上下文令牌。** 每条收到的消息带 `context_token`，发送时回传最近一次的令牌。
- **发送额度。** 用户发来一条消息后，机器人能发出的消息条数有限；用完后发送返回 `ret=-2`，直到用户再发一条消息才恢复。参考实现观测到的上限约为 10 条，这个数字没有公开约定，可能变化。
- **会话失效。** 接口返回 `-14` 表示 `bot_token` 已失效，需要重新扫码。

请求头按参考插件填写：`AuthorizationType: ilink_bot_token`、`Authorization: Bearer <bot_token>`、`iLink-App-Id: bot`；`base_info.channel_version` 必须以参考插件的三段版本开头，Telepus 写成 `<插件版本>/tfi-telepus-<Portal 版本>`，`bot_agent` 写 `TFI-Telepus/<版本>`。

## 渠道层

Telepus 的消息路径分为渠道无关的核心和各渠道的适配层：

- **核心**负责目标模式（个人助手、精准路由）、投递与幂等消息 ID、输入跟踪与回复读取、只发一次、`/history` 的取数、问题会话、命令语义和全部 Portal 调用。核心产出“要发给某个聊天的内容”：文字（Markdown）、引用来源、附件候选、问题提示，以及这些内容对应的 Portal 消息 ID。
- **适配层**负责与平台的连接：接收、身份对应、格式转换、分段、按钮或文字指令的呈现、附件收发、消息对应的平台 ID，以及发送额度。

每个渠道声明自己的能力，核心按能力选择呈现方式：

| 能力 | Telegram | 微信 |
| --- | --- | --- |
| 按钮 | 有：选项、展开、更早、附件下载 | 无：用编号和文字命令代替 |
| 编辑已发消息 | 有：问答卡片原地换题 | 无：每一步发一条新消息 |
| 主动发送 | 不限 | 受额度限制，见“发送额度” |
| 命令菜单 | `setMyCommands` | 无，`/help` 给出全部命令 |
| 单段长度 | 3000 字符 | 3800 字符 |

聊天由“渠道 + 渠道内聊天 ID”确定。微信的聊天 ID 是绑定的 `ilink_bot_id`，一个绑定只有一个聊天。桥接数据中凡按聊天保存的表都带渠道列，两个渠道的同一套表结构共用。

## 绑定

Telepus 门面的微信区域向所有已登录查看者展示绑定列表，包括 Portal 用户名与绑定状态；列表不含机器人凭证、扫码令牌或微信用户标识。每位 User 可管理自己的绑定，Portal 管理员也可管理其他用户的绑定。本人尚未绑定时，列表补上本人的未绑定入口。

1. 用户或管理员在目标用户的行上点“绑定微信”或“重新绑定微信”。弹窗明确显示“绑定到 Portal 用户：<用户名>”，由该用户用自己的微信扫码。Portal Web 校验操作者为本人或 Portal 管理员后，为目标用户调用 `get_bot_qrcode?bot_type=3`（本人绑定，请求体只有空的 `local_token_list`），页面显示返回的二维码。
2. 页面轮询绑定状态，Portal Web 转发 `get_qrcode_status`，每次上游等待最多 5 秒，安静等待或网络超时返回原会话供页面继续查询。页面遇到临时网络错误或 504 时自动重试，待提交的手机数字保留到收到确认；绑定已保存但确认响应丢失时，再次查询返回实际绑定状态。状态展示：`scaned` 显示“已扫码，请在手机上确认”；状态包含 `need_verifycode` 时页面让用户输入手机上显示的数字，随下一次轮询提交；`expired` 或 `verify_code_blocked` 时自动换码，连续三次后停止并提示重新开始；`scaned_but_redirect` 时改到返回的主机继续轮询。
3. `confirmed` 后，Portal 把 `bot_token`、`ilink_bot_id`、`ilink_user_id`、`baseurl` 写入该用户的微信绑定记录，状态为 `active`。微信收发服务在下一轮开始收取这个绑定的消息，并调用 `notifystart`。

页面显示当前登录用户，并逐行列出绑定用户名与状态。普通用户在其他人的行上仅能查看，本人和管理员在可操作的行上获得按钮：未绑定时提供“绑定微信”；绑定成功后显示“微信已绑定”，只保留“解除微信绑定”；绑定失效时显示“需要重新扫码”，提供“重新绑定微信”和解除绑定。每个用户的微信消息默认发往自己的 AMA，操作一个用户的绑定不会改变其他用户的绑定。

绑定归属于操作中明确指定的 Portal 用户；省略目标时使用当前认证 User。每个 Portal 用户最多一个微信绑定，重新绑定替换该用户的旧记录，旧机器人先 `notifystop` 再删除。用户或管理员可以在同一位置解除绑定；Portal 删除用户时一并删除其微信绑定。扫码会话按目标用户隔离，开始、轮询、提交数字及解绑每次均重新校验本人或管理员规则，权限系统关闭时也不绕过；管理员身份被撤销后，后续操作立即拒绝。

绑定由 Telepus 四项应用工具承载（信封见 decision_ccbb1cac24aa，引用见 decision_692a484e9bea），不登记 ISA，均接受可选 `user_id`，省略为本人，指定他人须为 Portal 管理员；与 Watchboard 共用目标用户校验：`StartTelepusWeixinBinding`（取码，返回二维码内容与绑定会话）、`GetTelepusWeixinBinding`（轮询状态或读取当前绑定）、`SubmitTelepusWeixinPin`（提交手机数字）、`UnbindTelepusWeixin`。工具结果不包含 `bot_token`、`context_token` 或二维码令牌；这些值只在桥接数据库中保存，日志与审计只记录脱敏诊断。

## 消息进入 Portal

微信收发服务（`telepus-weixin-router`）为每个 `active` 绑定维持一条 `getupdates` 长轮询：

1. 处理完一批消息后保存返回的 `get_updates_buf`；消息按微信消息 ID 记入去重表，重启后重放的消息不再投递。
2. 只接受 `from_user_id` 等于绑定 `ilink_user_id` 的用户消息，并保存它的 `context_token` 与收到时间。
3. 内容按类型转换：文字原样；语音使用消息自带的转写文字，没有转写时回复“没能识别这条语音”；图片、文件、视频从 CDN 下载解密，存入 Portal 上传目录，以 `[image: <路径>]` 或 `[file: <路径>]` 附在文字后。
4. **引用。** 用户在微信里引用一条消息时，Telepus 把它对应到 Portal 中的那条消息，投递时带 `quote: {message_id}`。Chat 页面因此像网页引用一样显示被引用的消息；门面按原有规则路由：引用成员回复的交给该成员，引用用户自己的消息的按普通手机消息交给手机助手。对应按下面的顺序查找，找到一条即停：
   1. **按微信消息 ID。** 引用标识取 `ref_msg.svr_id`，没有时取 `ref_msg.message_item.msg_id`（参考插件的同一规则），在消息对应表中查找。对应表登记两类消息：Telepus 发出的每一段回复、问答卡片和 `/history` 条目（发送返回的服务端消息 ID → 它显示的 Portal 消息），以及用户自己发来、已成功投递的消息（微信消息 ID → 投递生成的 Portal 消息 ID）。
   2. **按引用文字。** 没有标识或标识查不到时，用引用自带的摘录（`ref_msg.title` 或 `message_item.text_item.text`）与本聊天最近登记的消息比对：去掉首尾空白和 Markdown 标记后，摘录等于某条消息的开头或完整包含在其中。只在恰好匹配一条时采用，多条或没有都视为找不到。用户只选中了一部分文字（`partial_text`）时同样按整条消息引用。
   3. **找不到。** 把引用摘录以 `> ` 开头放在消息前面，按普通消息投递，与网页上粘贴引用文字的效果相同。

   每次收到引用时，审计记录一条 `quote_received`，只含是否带标识、按哪一步找到（`id`、`text`、`none`），不含消息内容，用来核对微信不同版本实际给出的引用字段。Telegram 的回复引用使用同一张对应表，用户回复自己的消息时同样对应到投递生成的 Portal 消息。
5. 之后与 Telegram 相同：问题会话、命令、Resident ID、按模式投递，消息 ID 由“渠道 + 绑定 + 微信消息 ID”确定性生成并作为幂等键。

投递成功后不单独回复“已收到”，改为显示“正在输入”：本聊天有未结束的输入时，每隔几秒调用一次 `sendtyping`，输入全部结束或发出回复时取消。这样确认消息不占用发送额度。

### 等待提示

个人助手门面处理一条消息要经过 Router 判断、steward 分派和成员工作，回复可能要等很久。“正在输入”之外，Telepus 在等待过长时再发一次文字提示：本聊天最早一条未结束的输入超过 5 分钟（站点设置 `deployment.telepus.still_working_after_seconds`，默认 300 秒）仍没有回复，就发出“⏳ 仍在处理，回复后会发给你”。自用户上一条消息起最多发一次；用户再发消息后重新计时。微信上这条提示计入发送额度，额度只剩预留的通知位时不发。Telegram 渠道使用同样的规则。

### 消息来源

Telepus 投递的消息在 Portal 中带渠道来源：消息的 `origin.channel` 记录 `{"kind": "weixin"}` 或 `{"kind": "telegram"}`（字段定义见 decision_ae422b92c6f3）。渠道由 Telepus 服务在调用入口的上下文中声明，指令参数和消息正文都不能设置。发出者仍是绑定的 Portal 用户本人，渠道只说明这条输入经哪条路径到达。

接收方据此知道读者在手机聊天里：

- 模型输入中的发送者说明写成“当前发送者是用户 <用户名>，经手机微信（Telepus）发送；回复会转为微信消息，宜简短，不依赖本地路径、Portal 页面链接和大表格，长回复会被分段”。Telegram 写“经 Telegram（Telepus）”。
- Family 门面把请求交给 Router、steward 或成员时，转交的输入沿用原请求的渠道来源，写回复的成员看到同样的说明。
- Chat 页面在这条消息的作者旁显示“微信”或“Telegram”标记。

渠道来源不改变身份和权限：这条输入就是该用户本人的输入，用户在微信里明确给出的授权与网页上同样有效，接收方按原有规则用 `AcceptUserAuthorization` 提交。

## 回复回到微信

回复刷新服务（`telepus-portal-refresh`）处理全部渠道的聊天，回复的读取方式与 Telegram 相同；发送交给对应渠道的适配层。微信适配层发送时：

- 每次发送回传该绑定最近的 `context_token`，用发送返回的服务端消息 ID 登记消息对应表，供之后的引用使用。
- 第一行是承接成员的标题（粗体）；主动消息以“📨 发送者”开头。
- 回复中的图片直接以图片消息发出；其他文件与链接到的资源列在回复末尾，编号显示，用户发送 `/file <编号>` 后由 Telepus 以用户身份读取、加密上传并发送。

### 发送额度

微信会限制用户两次发言之间机器人能发出的消息条数，Telepus 为每个绑定记录自用户上一条消息以来已发出的条数，并按下面的顺序使用额度：

1. **预留一条通知。** 额度设为每次用户发言后最多 8 条（站点设置 `deployment.telepus.weixin_send_budget`）。发到倒数第二条时，如果还有待发内容，最后一条改发“还有 N 条回复，回复任意消息继续接收”。
2. **长回复只发首段。** 回复超过一段时只发第一段，末尾注明“回复 /more 继续（共 N 段）”；`/more` 依次发出后续各段。
3. **暂存未发内容。** 额度用完或发送返回 `ret=-2` 时停止发送：未发出的回复撤销已发送登记、所属输入重新打开，留在待发队列中。用户下一条消息到达时额度重置，Telepus 先按时间顺序补发待发内容，再处理这条新消息的回复。
4. **不重试拒发。** 一次 `ret=-2` 后本轮不再尝试；网络错误按普通失败下一轮重试，不当作额度用完。

用户主动操作产生的消息（命令结果、问答卡片的下一题、`/history`）同样计入额度，但它们紧跟在用户发言之后，额度此时刚刚重置。

## 格式

微信客户端按 Markdown 渲染机器人消息：标题、列表、引用、链接、表格、粗体、行内代码和代码块都能显示；这是客户端的表现，接口没有约定，不同微信版本可能不同。Markdown 中单个换行不分段，前后两行会连成一段：成员标题会和第一句连在一起，列表或引用后面的一句会被画进列表或引用里。Telepus 发送前按下面的规则排版（`management_portal/telepus/weixin_format.py`）：

- **块之间空一行。** 标题、段落、列表、引用、表格和代码块之间插入空行；同一个列表（含缩进的续行）、同一个引用、同一张表格的行保持相连。第一行的承接成员名因此独占一行。
- **宽表格改为逐行列出。** 三列及以上的表格在手机上很挤，改为每行一组：第一列作粗体小标题，其余各列写成“列名：值”的列表。两列表格保持原样。
- **链接。** `http(s)` 链接保留 Markdown 写法，由微信显示为可点的文字；Portal 内部的相对地址在手机上打不开，只保留链接文字。
- **其余过滤**沿用参考插件的规则：五、六级标题去掉标记，图片语法去掉并改为附件发送，删除线保留文字。
- **按钮的替代命令**（`/file`、`/full`、`/earlier`、`/action`）在正文后空一行，排成列表，例如“- /file 1　⬇ report.pdf”。
- **分段。** 分段在行边界切开，代码块跨段时补齐围栏。回复分成多段时，每段末尾空一行注明“（第 k/n 段，回复 /more 继续）”，最后一段注明“（第 n/n 段，完）”。

Telepus 自己生成的说明（`/help`、问答卡片等）按同样规则排版，`/help` 按“发消息、问答、发送额度、命令”分组列出。

## 问答卡片

没有按钮时，问题集逐题以文字作答：

1. 回复带有未关闭的问题集时，Telepus 在回复之后发出“❓ 问题 1/N”、题目和编号选项，并说明“回复编号选择，或直接写你的答案；/back 上一题，/skip 不再回答”。
2. 问题会话打开期间，用户的下一条普通消息就是当前题的答案：纯数字且在选项范围内时选对应选项，其他内容作为自填答案。每答一题发出下一题；答完后与 Telegram 相同地投递回答，并发出“✅ 已提交回答”及摘要。
3. `/skip` 以 `cancel` 关闭问题集；会话打开时发送其他命令照常执行，不关闭问题集。
4. 权限请求的规则与 Telegram 相同：门面上展示的成员问题集只提供“不交出”，需要授权时用 `/route` 直接联系该成员或在网页上作答。

## 查看完整对话

`/history [n]` 的取数与 Telegram 相同，每轮一条消息，编号显示。被截断的轮次注明“/full <编号> 查看全文”，最后一条注明“/earlier 查看更早”。用户引用其中一条回复即可在微信里接着这个话题。

## 命令

除 Telegram 的全部命令外，微信渠道增加：

| 命令 | 作用 |
| --- | --- |
| `/notify` | 查看提醒开关；`/notify mention on\|off` 开关聊天室 @ 我；`/notify question on\|off` 开关待我回答，两类默认关闭 |
| `/more` | 发出长回复的下一段 |
| `/file <编号>` | 发送回复中列出的附件 |
| `/full <编号>`、`/earlier` | `/history` 中查看全文、向前翻页 |
| `/back`、`/skip` | 问答卡片中回到上一题、不再回答 |

`/help` 按渠道给出说明：微信版说明扫码绑定后的用法、引用、编号作答、额度提示的含义和全部命令。引用单条聊天室 @ 提醒会以绑定用户本人身份回复到该聊天室并引用原消息；汇总提醒需回 Portal 回复，不转给个人助手。当前聊天室附件上传不支持时仅发送文字并明确告知。引用完整的单条问题提醒可直接作答：单题回复选项序号或文字，多题每行“题号 答案”，原生批准请求回复“同意”或“拒绝”。汇总、未列全或含权限授权的问题组回 Portal 回答。原生问题和批准请求沿 `AnswerResidentRequest`，消息问题组沿网页的 `DeliverMessage` 提交正式回答位，不转给个人助手；具体规则见 decision_c41e1310c463。

## 桥接数据

| 表 | 内容 |
| --- | --- |
| `weixin_accounts` | Portal 用户、`ilink_bot_id`、`ilink_user_id`、接口地址、`bot_token`、收取游标、最近的 `context_token` 与时间、输入状态票据、状态（`active`、`expired`）、最近错误的脱敏诊断 |
| `weixin_bindings` | 进行中的扫码绑定：发起用户、二维码值、换码次数、轮询主机、状态，完成或过期后删除 |
| `outbound_queue` | 因额度暂停的待发内容：聊天、Portal 消息 ID、剩余分段；主动 `/more` 续读与自动补发分开保存 |
| `notification_preferences` | 用户、类别、开关、开启时间与基线是否建立；解绑或绑定失效时清除 |
| `notification_deliveries` | 稳定来源键、用户、类别、基线/待发/已发/已作废状态及时间；问答卡片按同一来源登记成功 |
| `platform_message_ids` | 渠道、聊天与完整平台消息 ID 到本地整数 ID 的对应；保留微信无符号 64 位 ID 的精度 |

`chat_state`、`telegram_inputs`、`delivered_messages`、`message_links`、`question_sessions`、`processed_updates` 增加渠道列，已有记录属于 Telegram；表名中的 telegram 只是沿用。发送额度的计数保存在 `chat_state`。当前桥接 schema 为 11。启动升级在一个事务内持有事务级 advisory lock：版本 7 先建微信表、加渠道列（旧记录属于 Telegram），版本 8 只为 `message_links` 增加 TEXT 类型的 `excerpt` 列，版本 9 建立两张提醒表，版本 10 只为提醒投递表增加微信服务端 ID、文本指纹与汇总标记列，最后记为 11；其他非当前版本拒绝运行。新列默认空串，旧聊天与消息对应保留；旧发出消息仍可按 ID 查找，文字查找从新增记录开始可用。几个服务同时启动只升级一次，无需单独迁移脚本或停服窗口。

聊天的存储主键编码渠道与平台聊天 ID：Telegram 沿用已有数字 ID，微信使用 `weixin:<ilink_bot_id>`；渠道列由该键生成，已有主键与数据保留。微信去重使用平台 ID 对应的负本地 ID，与 Telegram 正数 update ID 分开。附件编号、全文和翻页操作沿用桥接的回调记录，并按聊天保存文字命令的对应关系。

## 代码与服务

微信提醒的入口为绑定行上的“聊天室 @ 我”“待我回答”两个开关及 `/notify`；设置应用工具为 `GetTelepusWeixinNotifications` 和 `UpdateTelepusWeixinNotifications`，只允许本人或 Portal 管理员操作。`notifications.py` 消费本人 Mailbox，`refresh_service.py` 调度监听，`weixin_adapter.py` 在普通回复之后发送经复核的提醒，每批最多五项、其余保持待发，保留额度提示位。基线、字段缺失、来源去重及链接约定见 decision_c41e1310c463。

引用解析优先由 `notifications.py` 查询同一用户已发提醒的服务端 ID 或可见文本指纹，命中单条 @ 时交给 `mention_reply.py` 调用聊天室接口；命中问题提醒交给 `question_answer.py` 复核并回答，含授权的问题组提示回 Portal；汇总只回说明。其他引用映射由 `management_portal/telepus/weixin_adapter.py` 提取标识和摘录，`router_store.py` 查找同聊天的对应关系；`quote_text.py` 使用 Markdown 解析器取可见文字、统一空白。文字比对取最近 500 条登记记录（发出段与用户输入），按 Portal 对话和消息 ID 去重，恰好一条来源时才采用。`router_core.py` 在投递成功后登记用户输入，并为问答卡片设置来源；两个适配层在每段发送成功后记录实际文字与来源，包含 `/more`、`/history` 和 `/full`。已保存的用户输入也可参与 ID 和文字查找。`quote_received` 审计只带 `has_id` 与 `matched_by`，不带引用正文。Portal 现有引用 owner 填充作者与摘录，门面投影及 Chat 保留引用块和跳转目标。回归入口为 `tests/cases/test_telepus_quote_mapping.py`、`test_personal_phone_assistant.py` 与 `javascript/chat_conversation_projection.cjs`。

等待提示由 `management_portal/telepus/waiting.py` 统一判断，计时、已回复和已提示状态保存在桥接库的 `meta` 中；每聊天事务锁避免并发刷新重复发出。两个适配层确认发送成功后登记，微信在发送前检查额度，跳过的提示不入队。渠道经 `telepus/portal_client.py`、`hatg/contracts.py` 的可信 `InstructionContext.channel`、`society_service/resident_protocol.py` 的投递上下文进入输入署名；Family 的路由任务和经过核验的转交从持久原消息继承渠道。`resident_prompt_domain.py` 生成手机回复说明，`static/chat_workspace_messages.js` 显示作者旁标记。定向回归在 `tests/cases/test_telepus_waiting.py`、`test_telepus_origin.py` 与 `test_resident_user_authorization.py`。

微信渠道位于 `management_portal/telepus/`：`weixin_client` 实现 iLink 请求、CDN 加解密与上传，`weixin_adapter` 负责收取、转换、发送与额度，`weixin_format` 负责格式与分段，`weixin_binding` 实现绑定领域服务。`router_core` 通过渠道呈现对象生成交互，`channel` 声明按钮、编辑、发送、命令菜单和长度能力并提供公共分段；`telegram_presentation` 保持 Telegram 呈现，`weixin_format` 提供微信文字问答与命令。`telepus-weixin-router` 是新的 Portal 内置组件；Telepus 服务开关管理三个服务（decision_692a484e9bea）。联系人入口位于 Telepus 门面（`resident_4d7f422f024f`）的 Watchboard 应用，按 Telegram 与 WeChat · 微信分区，使用登录用户身份展示二维码、轮询与提交 PIN，状态变化后更新联系人区域。测试使用模拟的 iLink 服务端，覆盖绑定状态流转、去重、引用对应（微信消息 ID、引用文字、用户自己的消息）、额度暂停与补发、编号作答和 `-14` 失效。

## 失败与恢复

| 情况 | 表现 | 处理 |
| --- | --- | --- |
| `bot_token` 失效（`-14`） | 绑定改为 `expired`，停止收取与发送，Telepus 页面显示“需要重新扫码” | 用户重新绑定；待发内容在新绑定后补发 |
| 额度用完或 `ret=-2` | 停止发送，内容进入待发队列 | 用户发任意消息后补发 |
| 绑定二维码过期或数字错误过多 | 自动换码，三次后停止 | 重新点“绑定微信” |
| 媒体下载或上传失败 | 收到的媒体以“[附件下载失败]”代替；发出的附件回复失败说明 | 用户重发或再次 `/file` |
| 微信接口变化导致请求持续失败 | 该绑定记录错误诊断，服务按退避重试，其他绑定与 Telegram 不受影响 | 维护者按诊断更新适配层 |
| 收发服务中断 | 微信消息暂不进入 Portal | 恢复后从保存的游标继续收取 |

## 适用范围

微信渠道连接的是用户个人微信上的 ClawBot，适合本人使用自己的个人助手。它依赖微信尚未正式公开契约的接口，发送额度与会话时效由微信决定，Telepus 只做节制与补发，不绕过这些限制。Telegram 渠道不受影响，两个渠道可以同时绑定，各自的聊天独立跟踪。
