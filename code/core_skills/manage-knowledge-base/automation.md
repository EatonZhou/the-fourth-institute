# 知识维护与自动工作

知识审计、凝练、关系维护及确定性格式检查的实际承担者取决于本站部署。不要把源站的 Resident ID、Lineage 路径、固定时刻或旧 Agent 目录写成通用依赖。

## 查找与运行

- 通过 `SearchResidents`、`SearchResources` 和当前项目配置定位维护者，再读其职责说明、Lineage 事件源和连线。已有同类工作时优先调整，不重复创建。
- 需要判断与写作的工作由 Resident 承担；确定性脚本按 Lineage 的 Effect / RunScript 流程执行，参考 `${share_root}/Skills/scheduled-work/SKILL.md`。
- 按需用 `ListResidentStates`、`GetComponentRuntime`、`GetScriptRun` 检查状态和结果，不能把事件源启用或历史日志存在当作本轮已完成。
- 知识登记、修改、读取由当前 HATG 资源指令完成；无需寻找某个固定命名的归档 Agent 目录。

## 写入边界

维护者按用户授权或已配置职责修改对应资源，使用自己的身份。只读审计生成建议，不擅自批量改正文。职责说明引用本 Skill 的格式规则，不维护第二份格式定义。

`metaness` 遵守 `./knowledge-base-rules.md`：缺失合法，1–10 的严格整数有效；非法值报告具体条目和字段，不猜值、截断或自动重写正文。Plan 审查按 `./archive-plan-item.md` 保留场景、因果、价值、方案、边界和证据，不按条数或每个 Design 的配额造计划。

修改自动化流程后核对实际指令、职责、事件源及连线；只有用户请求的触发范围才可以运行。
