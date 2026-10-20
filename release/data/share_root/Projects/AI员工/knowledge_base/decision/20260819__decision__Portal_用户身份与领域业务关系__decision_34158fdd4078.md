---
kb_id: decision_34158fdd4078
type: decision
project: AI员工
created_at: '2026-08-19'
version:
  number: 22
  created_at: '2026-08-19'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:32:27Z'
  updated_by:
    user: ''
    agent: resident_d43295e5e043
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/isa/definition.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/http_portal_session.py
  - ${code_root}/management_portal/society_service/lineage_access_policy.py
  - ${code_root}/management_portal/portal_access_control.py
  - ${code_root}/management_portal/portal_two_factor.py
  - ${code_root}/management_portal/resources/access.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/permission_system/__init__.py
  - ${code_root}/tests/cases/test_portal_access_control.py
  - ${code_root}/tests/cases/test_security_wave5.py
  related:
  - decision_5bc3fdfc7eec
  - decision_c953eab654cd
  - decision_6eac174d85bf
  - decision_b820faf42218
  - decision_30528aff7035
  - decision_3fe3cca8fd95
  - decision_721c64f9bdcc
  - decision_8838269736d3
  - decision_d918ebebc062
  - decision_6fc653d55c79
  - decision_b6e74d78f7b6
  - decision_4a9ba5dfada6
  supersedes: []
  derived_from: []
---

# Portal 用户身份与领域业务关系

User 是稳定账户；Resident 是稳定运行节点。身份认证见 decision_3fe3cca8fd95，账号管理见 decision_30528aff7035，权限系统见 decision_b820faf42218。Project 角色、memberships 与 system_capabilities 已退役，GetUserRoles/UpdateUserRoles 和 CreateUser 对应参数已删除，存量字段不产生权限，也不再作为返回值或授权迁移来源。

## 用户与账号生命周期

账号保存用户名、显示名称、恢复邮箱、active/disabled/deleted 状态、会话版本和审计时间。用户名按既有 principal 比较规则处理；删除后的用户名不可复用。停用撤销可用会话并保留恢复所需事实；永久删除清理个人认证与个人数据，保留共享业务历史及其中的署名（例如 `created_by`），不删除停用或已删除用户名的历史关系。

账号名称没有固定管理员特权，也不形成停用、删除或邀请的例外。账号写入由权限系统按当前模板逐条指令判定，/api/me 的 access.allows 报告调用者能否通过页面关心的指令；名册和审计读取要求有效登录身份。普通用户保留自己的资料、凭据、偏好和导航入口。TFI portal 仍需认证且遵守领域规则。

## 谁能管理一个对象

Portal 对象不保存所有者（owner）列表。Lineage、Resident、Workbench、语义搜索和论文收集任务的 owner 字段与表已于 2026-10-17 删除（`lineage.residents.owner_ids_json`、`workbenches.owners`、`semantic_search.jobs.owner_ids_json`、`paper.collector_jobs.owner_ids`）。谁能查看、修改或删除一个对象，只由权限系统回答：

- 权限开关的开放模式、管理员保底与个人 AMA 例外统一遵循 decision_b820faf42218 §3.1；身份认证、个人秘密、固定工作流角色和数据完整性等领域规则在两种模式下都有效（同文第 4 节）。
- 权限系统开启时，按当前模板和权限实例判定；模板读取的事实只有对象的自然归属（所属 Project、Lineage 等）、创建者、Family steward 和聊天室成员。

页面需要的操作权限以 `allows` 按指令名返回，领域入口通过权限端口询问对应指令及参数（decision_b820faf42218 §3.1）。历史上命名为 `_can_manage`、`_ensure_manager` 等的内部入口不能替代逐指令授权；它们保留的身份、私人归属或数据完整性检查按各自领域职责执行。

## 保留的业务事实

- `created_by` 记录创建者，用于署名、审计和模板中的创建者规则，不单独授予管理权。
- Chatroom 以 members、created_by、@/called 为事实，这些事实同时是模板中聊天室成员规则的输入。
- Workspace 保留规范路径、稳定 ID、自有关系、Binding 和生命周期。
- 个人 AMA 的所有者仍是业务事实。开启权限系统时，私有性由方案的 P/R 树放置表达，默认规则见 decision_600ae27b0538 第 2、3 节；关闭时由权限端口保留所有者边界，见 decision_b820faf42218 §3.1、§5.5。管理员分类本身不覆盖默认方案的个人空间。
