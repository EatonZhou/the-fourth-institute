---
name: manage-knowledge-base
description: 在 TFI Portal 项目知识库中搜索、读取、创建、修改、审计、凝练、统一口径或整理资料文件时使用。
metadata:
  id: skill_577f37784184
---

# 管理项目知识库

本技能是项目注册目录下 `knowledge_base/` 的统一操作入口。项目路径从 `GetProject` 读取；`${share_root}` 从当前实例配置解析。普通自然语言写作、项目代码修改和 Portal 资源管理不触发本技能。

## 路由

根据任务只读取需要的分支：

- 搜索、读取或回答知识库内容：`./search.md`
- 任何写入操作：先读 `./knowledge-base-rules.md`
- 创建或归档条目：再读 `./create-entry.md`，并按类型读取 `./archive-<type>-item.md`
- 编写或整理改进 Plan：读取 `./archive-plan-item.md`，每份文件只承载一个值得独立审查的显著改进
- 设置或检查抽象层级：读取 `./knowledge-base-rules.md` 的可选 `metaness` 规则；后台处理再读 `./automation.md`
- 跟踪待处理事项：先读 `./archive-todo-item.md` 区分 Mailbox 待办和历史知识 TODO；不向不支持写入的类型造记录
- 修改已有条目：`./modify-entry.md`
- 凝练正文：`./condense-entry.md`
- 通过问答逐轮统一口径：`./cleanup-knowledge-base.md`
- 查找错误、矛盾、过时或重复条目：`./audit.md`
- 整理 `knowledge_base/material/` 资料文件：`./materials.md`
- 判断后台检查器与人工操作的关系：`./automation.md`

示例只在需要时读取对应的 `*-examples.md`。

## 专用入口

以下内容有独立结构或工作流，不由本技能代替；仅在任务涉及且本站已提供该技能时读取。未部署时说明缺口，不假定源站研究技能都会带出：

- 实验条目：`${share_root}/Skills/create-experiment-entry/SKILL.md`
- 研究地图（项目的研发方向，由地图指令维护，不写成知识条目；产出条目在 `links.related` 写方向 ID）：`${share_root}/Skills/write-research-map/SKILL.md`
- 研究计划：`${share_root}/Skills/write-research-plan/SKILL.md`
- 资源类型的新增、名称维护与停用：`${share_root}/Skills/add-resource-type/SKILL.md`、`${share_root}/Skills/disable-resource-type/SKILL.md`、`${share_root}/Skills/rename-resource-type/SKILL.md`
- 新建项目：`${share_root}/Skills/create-project/SKILL.md`

用户只要求检查、解释或预览时，不修改知识库。用户明确要求创建、修改、整理、合并或写回时，按相应分支直接执行。
