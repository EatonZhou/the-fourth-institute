---
kb_id: decision_58db6282c244
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 5
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:46:17Z'
  updated_by:
    user: ''
    agent: resident_83d75ad3a6c7
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/static/article_workspace.js
  - ${code_root}/knowledge_board_lan/knowledge_board.html
  - ${code_root}/management_portal/http_knowledge.py
  - ${code_root}/management_portal/resource_lineages.py
  - ${code_root}/management_portal/discussion_residents.py
  - ${code_root}/management_portal/knowledge_item_chat_backend.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_knowledge_item_chat_backend.py
  - ${code_root}/tests/cases/test_knowledge_http_workflow.py
  related:
  - decision_6591e7793116
  - decision_f560803947c6
  - decision_3ece26686464
  - decision_c524c775e0be
  supersedes: []
  derived_from: []
---
# 资源详情中的私有 AI 对话

Knowledge 条目详情和 Article 工作区的右侧都有一个 Chat 面板：用户可以让一个 Resident 围绕当前资源和自己单独讨论，例如解释内容、提出修改建议、查找关联条目。每位用户的对话只属于他自己。

本 Design 说明私有对话的 Resident 从哪里来、发送时带上什么上下文。Chat 面板的通用界面与发送协议见 decision_f560803947c6。

## 核心概念

- **资源**：当前打开的 Knowledge 条目或 Article，用它的根资源 ID 识别（例如 `decision_*`、`article_*`）。
- **私有对话 Resident**：为“一位用户、一个资源”准备的 Resident。同一位用户再次打开同一资源，回到同一个 Resident 和同一段对话；另一位用户打开同一资源，得到他自己的 Resident。只有用户本人能看到自己的对话历史。
- **资源对话 Lineage**：私有对话 Resident 所属的 Lineage，由 Portal 应用目标配置 `application_targets` 的 `resource_chat:<页面类型>` 指定，页面类型为 `knowledge`、`article`、`repo`、`skill`、`experiment` 或 `device`。每种页面类型的对话 Resident 都集中在对应的 Lineage 中。

## 面板

Chat 面板包含私有对话历史、输入框、附件、发送、停止、刷新、运行设置和斜杠命令。Knowledge 详情页右侧只有这一个面板，占满右栏高度；窄屏时它是详情的“对话”分区。

## 私有对话

**用户第一次在 Chat 面板中发送消息时，Portal 为“这位用户、这个资源”创建私有对话 Resident；之后的消息都发给同一个 Resident。**

- **读取**：打开面板时，页面读取对话状态（`GET /knowledge/api/item-chat?kb_id=<资源 ID>`），要求当前用户对该资源至少有 viewer 权限。还没有发过消息时，Resident 尚不存在，面板显示空对话。只打开面板不会创建 Resident。
- **发送前准备**：每次发送前，页面调用 `POST /knowledge/api/item-chat/prepare`，要求 contributor 权限，消息内容不能为空。服务通过 ResourceService 以当前用户的身份重新读取资源，确认资源仍然存在且 ID 一致，然后找到或创建私有对话 Resident，返回对话状态和本次的初始上下文。
- **初始上下文**：包含资源 ID、标题和资源路径。Resident 需要正文时按路径读取当前内容，因此它看到的是资源此刻的内容，而不是创建对话时的一份副本。
- **发送**：消息通过 Portal 统一的 Chat 发送流程投递给 Resident（`DeliverMessage`），附件、资源引用和斜杠命令都按统一规则处理。附件保存在该 Resident 的工作区中；消息中显式引用的 Skill、Resident 或资源在发送前按当前用户的权限解析，无法解析或无权访问的引用不会以其他身份读取。
- **运行设置**：Resident 存在后，面板可以用 `UpdateResidentSettings` 修改它的模型和推理强度等设置。
- **图片读取**：对话中的图片通过 `GET /knowledge/api/item-chat/attachment-image?path=<路径>` 读取，路径必须位于 Resident 的图片存储目录内，并通过当前用户的工作区可见性检查。

对话 Resident 的身份键为 `resource-chat:<用户>:<资源 ID>`，这些键只用于找到同一个 Resident，不代表任何权限。

## 组件

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| Knowledge 详情页 | `knowledge_board_lan/knowledge_board.html` | Chat 面板 |
| Article 工作区 | `management_portal/static/article_workspace.js` | Chat 面板，使用同一套接口 |
| 私有对话后端 | `management_portal/knowledge_item_chat_backend.py` | 读取资源、找到或创建对话 Resident、生成对话状态与初始上下文 |
| 对话 Resident 解析 | `management_portal/discussion_residents.py`、`resource_lineages.py` | 通过 `application_targets` 的 `resource_chat:<页面类型>` 找到 Lineage，在其中找到或创建 Resident；目标解析仍校验调用者访问权限 |
| HTTP 路由 | `management_portal/http_knowledge.py` | 取得登录用户，检查 viewer / contributor 权限，转换错误码 |

```text
用户在 Chat 中发送
        ↓
prepare：确认 contributor → 以用户身份重新读取资源 → 找到或创建对话 Resident
        ↓
统一 Chat 发送流程（DeliverMessage）→ 私有对话 Resident
```

## 接口与错误

| 请求 | 作用 | 权限 |
| --- | --- | --- |
| `GET /knowledge/api/item-chat?kb_id=` | 读取私有对话状态 | viewer |
| `POST /knowledge/api/item-chat/prepare` | 发送前准备：`kb_id`、非空 `message` | contributor |
| `GET /knowledge/api/item-chat/attachment-image?path=` | 读取对话中的图片 | 图片路径与工作区可见性检查 |

缺少参数或内容为空返回 400；无权访问返回 403；资源或对话不存在返回 404。

## 出错后怎样恢复

- ResourceService 读不到资源时，本次发送准备失败，不使用旧的资源信息。
- Resident 工作失败时，对话和已经发送的消息保留，用户可以重试。
