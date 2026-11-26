# 待处理事项与历史 TODO

先区分用户希望保存知识，还是在某个 Resident 的 Mailbox 中跟踪工作。

当前知识资源 `todo` 没有登记、更新或撤销接口（`GetResourceType` 返回相应能力为 false）。旧知识文件中的 `id`、`task_kind`、`task_status` 等仅用于识别既有数据，不能当作当前创建合同。不要手写旧 YAML、给 `RegisterResource` 塞不支持的字段，或把 Mailbox 待办伪装成知识 TODO。

- 用户要记 Mailbox 待办时，先确定接收用户。调用 `RegisterResource`，传 `resource_type: "mailbox_todo"`，并在 `fields` 提供 `user_id`、`text` 和可选的 `date`。用 `SearchResources` 按 `resource_type: "mailbox_todo"` 和 `user_id` 查清单。已有条目用 `ReadResource` 读正文与版本，再用 `UpdateResource` 修改，`changes.expected_version` 传读到的正整数版本。按当前身份和条目所属用户校验权限。待办独立于知识条目和对话输入，记录后不会开始执行。
- 用户要保存尚未解决的问题时，按实际内容与用户目的使用 `question`；完整改进方案按 `plan`。这种语义选择不是批量把旧 TODO 改名或迁移。
- 用户明确要求知识 TODO，而本站仍无写入能力时，如实说明该功能缺口，保留原内容；不另造登记入口。
- 阅读或迁移历史 TODO 时保留原 ID 与来源，先明确迁移目标和授权范围；不自动删除旧文件。

事项正文说明需要处理的对象、必要背景和当前未知点。问题尚未形成方案时，不擅自填执行负责人或承诺解决路径。
