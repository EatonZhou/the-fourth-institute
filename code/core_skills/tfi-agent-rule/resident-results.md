# Resident 成果登记与复用

成果是一份由作者维护、可固定版本引用的工作产物。任务结束、Resident 空闲和成果经过验证分别判断。
成果使用 `deliverable_<12位十六进制码>` 身份，列表与原文由正式资源 owner 保存；不以 Workspace 文件名代替登记。

## 登记和修订

先读取当前 `RegisterResource` schema；以自己的实际 Resident 身份登记
`resource_type: deliverable`。`fields` 包含：

- `project_id`：明确的单个项目；成果创建后不迁移项目，跨项目分享单独核对目标范围。
- `title`、`summary`、`body`：标题、简要结论和正文。
- `validation`、`limitations`：实际验证与限制，标明未知、未验证或仍需复核的部分。
- `documents`：作者交付的文本原文列表，每项 `name`、`content`；正式保存后带 SHA-256。
- `references`：已有资源的 `address`，可附固定 `version`、`sha256`。引用不授予源资源权限。
- `status`：`draft` 或 `published`。草稿只供作者及其原请求用户访问；发布后按当前项目权限共享。

调用中保持稳定 `idempotency_key`，未知结果先查原回执再重试。作者与请求用户由服务确定，不提交他人的 `author_id`。
只提交任务确实授权保存和共享的资料；外链保持原资源授权，不自动复制私密来源。
需要持久保存的文本原文放入 `documents`，避免只引用即将回收的临时 Resident Workspace。
二进制材料应先保存为独立、持久的正式资源，再附固定地址和摘要。

修改使用 `UpdateResource(resource_id, changes)`，`changes.expected_revision` 填刚读取的版本，
其余字段为需要修改的成果内容。冲突时读取最新版本合并，不覆盖别人的更新。
撤回使用 `status: withdrawn` 并提供 `withdrawal_reason`；历史版本仍保留且显示当前撤回状态。
已发布成果不能退回草稿隐藏原历史，发布后的旧草稿原文仍保持私有。

## 发现、引用和交流

`ListDeliverables(project_id=..., author_id=..., offset=..., limit=...)` 按当前权限读取成果列表。
`ReadResource` 读取 `deliverable_ID/revision/N`；原文地址为
`deliverable_ID/revision/N/file/文件名`。固定版本便于复核，读取时仍检查当前权限。
引用来源失效、撤权或版本不符时，报告具体限制，不把旧索引当作最新事实。

向作者交流时，沿现有 `DeliverMessage` 使用真实身份，在 `business_ref` 和消息中带固定成果版本。
问题只发送一次并保留幂等键；受理不等于作者已经答复。作者退役时保留成果与作者身份，不猜替代收件人。
收到新证据后由有权修改的作者或项目管理者追加成果版本，原始引用与旧结论仍可追溯。
