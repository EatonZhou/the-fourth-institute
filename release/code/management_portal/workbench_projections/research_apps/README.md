# 研究工作台应用包

`release-dependencies.json` 使用独立的 `tfi.projection-release-dependencies.v1` 发行合同，`code_repository` 与应用仓库一致。本应用没有固定的 Workspace 文件入口，故 `files` 为空；历史统计 JSON 位于仓库中，实验、Information 和团队读取仍由对应资源的发行依赖核实，不以空文件表表示这些资源已齐备。

本工程把既有“语义化优化研究工作台”和“模型 × 硬件优化矩阵”迁入投影协议 v2。两个入口共享报告解析及研究会话逻辑，各自保留原来的研究表达；Portal 宿主负责侧栏、原生 Chat、当前用户鉴权、确认及 sandbox。

| 入口 | 原工作台 | 迁移前修订 | 保留的内容 |
| --- | --- | --- | --- |
| semantic | workbench_d82dedd31208 | 7 / published 7 | 12 模型 × 3 硬件历史统计、方案测量、组合历程、回执时间线、原始来源及负例 |
| matrix | workbench_0d598218b194 | 22 / published 22 | 当前模型 Steward 目录、平台实验性能、同契约配对加速比、方法与机器测量证据 |

绑定继续使用 `lineage_98e68feb0c21` 和 coordinator `resident_5549c42d1317`。同一既有 Resident 增加 `facade` 别名，提供原生对话；它的真实名称为“语义化优化研究”，不宣称其为 Family 门面。原 owner、导航位置、项目、工作台 ID 和旧链接保持。清单只有 research 功能区，同时发布和订阅 focus。Chat 与应用资源由宿主独立装配，应用不控制原生 Chat 生命周期。

## 构建与集成

在本目录执行 `npm install --package-lock=false --ignore-scripts` 和 `npm run build`。构建时解析 esbuild 的可用版本，两个入口都生成自包含 JS/CSS/HTML，不访问网络、无运行时 import、不扩展 CSP。正式保存仍经过平台的固定 Git 提交、隔离构建、产物校验。

集成者先提交本目录，取得含这些源码的完整 Git SHA，再执行：

```sh
python3 management_portal/workbench_projections/research_apps/definition.py --commit <完整源码提交>
```

输出包含两个原资源的完整更新字段和 `expected_revision`，不执行写入。分别使用当前授权身份的 `UpdateResource` 保存，核对构建回执和新修订后另行发布；基线变化必须重新审阅，不能用去掉 expected_revision 的方式覆盖。源码候选、保存修订、发布修订和真实读者验收分别记录。原 v1 HTML 在新定义中为空，旧修订只作为不可变历史保留。

## 研究数据和语义对象

原两页的相同报告解析与模型名称归一化函数由 `src/reports.js` 唯一维护，模型目录和方法匹配使用同一名称规则。测量、方法、组合、原始来源及未归属记录继续从原实验和 `information_6d772dbfa65b` 读取。协议迁移不触发实验、不重新评价已有结果、不把历史 NO_GO、候选、受干扰或缺测转换成通过。

`data/semantic-snapshot.json` 是原 revision 7 内嵌的 36 行历史统计及原消息的逐值迁移。它经 `repo_5d287fed9c7e/file/management_portal/workbench_projections/research_apps/data/semantic-snapshot.json` 从 SDK 读取，不打包进可执行脚本。构建时把该文件 SHA256 固定到入口；读取时比对宿主权威 metadata.content_sha256，任何变化均明确失败，不混用新数据和旧程序。此文件属于有日期和原消息 ID 的迁移档案，不充当当前实测结果。页面同时核验原统计成员及团队；源文件、团队或统计成员不可读时撤下历史表格。要更改档案，需要更新来源依据并构建发布新的应用修订。

选中历史格子使用档案的真实行锚点，kind 包含模型及硬件，source 指向原统计成员；选中当前实验使用实际实验资源地址。status=current 表示正在阅读的记录，不声明测量已经独立验收。方法测量用实际来源文件作为 ref，指标 JSON pointer 和来源摘要放入 scene。所有对象保持 `{ref,kind,status,source,observed_at}` 五字段。讨论 subject 仅用于确认标题，不混入对象身份。

对象先经当前用户 resolve/read；索引中的测量、策略、分支、缓存正文和目录条目在计数、绘图和连线前过滤不可读来源。删除了前置节点的分支保留原 parent_id，页面不把缺失关系解释为可执行。源服务故障保留故障，不显示为零或通过。

## 生命周期与讨论

会话只通过 SDK 读取数据、保存筛选、发布 focus 和观察资源。每轮读取携带内部 epoch；隐藏、销毁、来源通知及重新读取都会使旧结果失效，清空旧表格/详情并取消 pending feedback。显示后重新读取，筛选保留；页面销毁释放 watch 和 SDK 监听。完整来源组交给宿主分批观察，不以 HTTP 单批上限截断。异步释放失败会记录错误并保留清理函数供重试，显式等待 hide/destroy 可取得原错误；销毁后晚到的订阅同样释放。异步选择 A 晚于 B 返回时不能覆盖 B。

讨论沿用宿主同一个原生确认。发送前重读所选依据并比较内容摘要；来源变化取消。用户点击后原文传给 SDK.feedback，收件人由宿主绑定决定，确认中隐藏/换对象/撤权继续使用宿主取消边界。页面没有直接 DeliverMessage 或变更指令调用。打开团队对话调用现有 openPage 当前工作台 facade 入口，布局拒绝时显示失败。

## 定向检查

- `npm test`：资源权限竞态、隐藏晚到回复、对象乱序、来源版本、完整观察组、异步清理与报告模型匹配。
- `PORTAL_RESEARCH_BUILD_REPORT=<报告路径> python -m pytest -q <本目录>/tests/test_build.py`：临时 Git fixture 使用真实平台隔离构建器，验证两入口、锁依赖和产物；不修改正式资源。

历史迁移的浏览器 fixture 证据按 `tests/cases/research_projection_contract.md` 中的固定版本归档；当前源码不含 `tests/browser.cjs`，不能把旧命令当作可运行入口，也不据历史结果宣称当前 UI 通过。公共 SDK/SPEC 的集成索引由平台父成员统一维护；本工程遵循 `decision_7a5ae487e2e9`、`decision_f67ae5e70793`、`decision_4348ed990621`、`decision_538e155e1102`。
