---
kb_id: decision_328fb2548b8f
type: decision
project: AI员工
created_at: '2026-10-19'
version:
  number: 4
  created_at: "2026-10-19"
  created_by:
    user: ""
    agent: "resident_4b1da6140cf2"
  updated_at: "2026-11-05T02:06:37Z"
  updated_by:
    user: ""
    agent: "resident_62dbdb2288fb"
importance: 7
ai_generated: true
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/personal_assistant.py
  related:
  - decision_44cf587c1721
  - decision_bd5217d08c09
  - decision_89b9df038a95
  - decision_a64b3ea2c3c5
  - decision_8fe87d812a4c
  supersedes: []
  derived_from:
  - decision_44cf587c1721
metaness: 3
---

# 私人 Family 与个人助手

[Family 主文](/resource/knowledge?item=decision_44cf587c1721) 定义通用组合；[AMA 入口关联](/resource/knowledge?item=decision_bd5217d08c09) 定义用户入口与身份生命周期。本文维护私人 Family 的初始化、历史边界、偏好和组织约束。

每位用户的私人 Lineage（`lineage_membership=personal`）建成一个私人 Family，门面就是用户的个人助手入口。原 AMA 是默认成员，保留原身份、Session、`resident.md` 和历史；用户只和门面对话，Router 在用户自己的成员之间分派，外部协作由成员给出卡片、经用户确认后进行。

## 初始化

 账号创建、每次登录签发会话时，以及已登录用户打开页面时，`personal_ama.provision` 先确保 AMA 存在，再调用 `ensure_personal_assistant`，以用户本人身份执行 `personal_assistant.ensure`：

1. 按用户加文件锁，确认 AMA 入口指向本人私人 Lineage 中的有效 Resident。入口失效、指向他人的 Lineage，或该 Lineage 已有绑定另一个 AMA 的私人 Family 时报错，稍后再试，不替用户另选 AMA。
2. Lineage 标题仍为旧默认值「Ask Me Anything」时改为「Ask Me Anything | My Lineage」；用户改过的标题保持不变。新用户的个人 Lineage 在同一流程里得到新标题。
3. 没有私人 Family 时，以 AMA 为唯一成员 Familize。门面、Router、steward 使用 `creation_key=personal-assistant:<AMA>:<role>` 创建，中断后重试会复用已建角色。门面成为 Lineage leader。用于保存历史的 AMA 分支留在 Lineage 中，不加入名册。
4. 重试资源发布和 steward 授权，用 CAS 初始化个人偏好键（已有值不覆盖），返回门面、Lineage 和 Family 资源 ID。Portal 把结果写进用户的个人助手入口关联；原 AMA 关联不变。

失败只记录错误，不阻止登录或打开页面。会话最长保留 30 天，已有用户不必等下次登录：页面每次加载都会读取 `/api/me`，每个 Portal Web 进程在某用户第一次读取时于后台补做（用户已有个人助手入口关联时直接跳过），不延迟页面返回；补做失败的用户 10 分钟后再次打开页面时重试。同一流程重复执行不产生变化。

## 历史对话

 Familize 时 `history_before` 记录时间，`history_before_ordinal` 记录 AMA 对话的下一个消息序位。门面对话读完自己的消息后，接着按页读取 AMA 序位在分界之前的消息，不复制到门面：

- 每条历史消息保留原 `message_id`、`role`、`author_id`，并带 `family_history:{resident_id, title, before_at, message_id}`；页面顶层的 `family_history` 另含 `before_ordinal`。
- 历史分页使用 `personal-history:<ordinal>` 令牌，客户端原样回传。
- 引用历史消息时，门面把原消息 ID 解析为 AMA，请求直接交回 AMA。
- 分界之后用户绕过门面直接与 AMA 的对话不投影；只授权单个请求的快照也不附带历史。
- 用户在门面清空此前对话上下文后，门面的 `context_reset_at` 同时终止这段 AMA 历史投影。快照、分页和按消息 ID 查找或引用均遵守该边界，旧 `personal-history:<ordinal>` 令牌失效；已存在清空标记的门面也直接适用。清空后新增的门面对话照常展示和分页，AMA 原始对话保留在原入口。

## 路由与偏好

 只有一个成员时，Router 脚本直接转交，不调用模型。多个成员时，Router 在通用规则之外遵循个人助手规则：同一话题交给上一轮答复的成员，用户换话题、点名或要求改派时才重新选择；个人偏好和改派纠正优先于职责相似度；没有明显更合适的成员时交给 AMA，AMA 接受自由对话。个人偏好里的外部 Resident 不能作为内部转交目标。

**个人偏好。** 私人 Lineage 的共享状态 `family_preferences_<门面ID>` 保存 `{preferences:[{topic, resident_id, reason}], my_teams:[{lineage_id, resident_id, title, brief}]}`，与改派纠正 `family_corrections_<门面ID>` 相邻。Router 按需通过 `preferences_key` 定位并读取它；成员用 `GetSharedState` 读取，用带 `expected_value` 的 `UpdateSharedState` 更新。偏好随私人 Lineage 的权限受保护。

## 成员行为

 个人助手的共同行为写在 `personal_assistant.py`。业务成员仍每轮接收 `personal_assistant` 部分；内部 Router/Steward 的个人助手职责只在 Provider 会话首轮注入，场景工作由可编辑指南与精简任务承担。`resident.md` 仍由用户定制人格和习惯。成员行为包括：

- **外部卡片。** 需要外部协作者时，成员在用户发起的回合中按实际授权检索候选、读取共享摘要并参考个人偏好，输出 `portal-resident-handoff` 卡片（字段与界面见 decision_8fe87d812a4c）。卡片只是建议。
- **替我问一下。** 收到用户“替我问一下 <title>（<resident_id>）：<ask_message>”后，成员用 `DeliverMessage` 投递卡片上的任务和已展示的材料，投递按实际执行主体与输入授权核验，不转发私人对话或附加未确认的资源。消息以门面署名发出；对方回复门面时，平台按原轮次或 `reply_to_message_id` 把回复交回写信的成员，成员在个人对话中转述并标明来源。对方没有回信时，成员按原消息用 `GetInputState`、`GetConversation` 查阅，不重复投递。
- **专题独立。** 话题需要持续推进时，AMA 提议独立。用户确认后，AMA 整理专题简报（目标、已作决定、必要背景、待办），以此建立新的 Lineage 和 Resident，并把关联写入 `my_teams`；不全文 Fork 私人对话。

## 组织变更与维护

**Steward 的组织变更先提建议并取得确认。** 新增、删除、合并、移出成员和改职责，都要先通过门面向联络人提出具体方案，得到明确确认后才执行；队列积压、低置信度和周期回顾都不构成授权。成员积压信号的通知也写明只提建议。用户已要求的 high 内容与视频审核采用固定职责：实际高质量请求进入时，在负责成员自己的直接 Family 内查找“生成内容审核”和“视频内容审核”两类成员，缺少则按正式创建权限补齐，后续复用。普通 Family 和私人 Family 使用同一机制；不能把这项明确的请求要求再次变成私人 Family 专用的组织确认，也不能改为外部搜索或转借开发团队成员。此要求只覆盖两类审核职责，不授权 steward 任意增加其他成员、调整私人保护或扩大历史读取。角色准备与固定作品审查合同见 decision_b12782e7f490。

私人 Family 没有每小时门面摘要汇总，回顾周期至少 86400 秒：

- 事件源“个人对话活动”（`resident.message_received`，观察门面和成员）把最近的消息 ID 写进 `family_activity_<门面ID>`。
- 每次周期回顾由 Effect 先检查：没有活动、活动与上次回顾记录的 `family_review_<门面ID>` 相同，或 steward 不空闲、队列非空时跳过；否则按可编辑 review 模板投递给 Steward，并记下本次回顾到的活动。


成员近期事实、Steward 时间层归纳、队列整理与故障恢复仍适用 [运行维护契约](/resource/knowledge?item=decision_a64b3ea2c3c5)；“只提建议”限定组织变更，不禁止执行已经授权的日常维护。实际 Event/Effect 与首轮指南差异见 [角色工作流](/resource/knowledge?item=decision_89b9df038a95)。

## 私有保护

 私人 Lineage 中新建的 Resident 都登记 `personal_owner_id` 为 Lineage 所有者，门面、Router、steward 和后加入的成员因此与 AMA 一样只对所有者可见，权限系统关闭时和管理员也不例外。私人 Family 的所有角色不进入任何人的找人候选，包括所有者本人的默认检索和显式限定检索。
