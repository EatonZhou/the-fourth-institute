# 第四大学 · The Fourth Institute

[English](README_EN.md) | **简体中文**

**让人类与 AI Agent 在同一个研究环境中，持续探索、交流与积累知识。**

随着 AI 能力的发展，将科研全过程交由 Agent 自主推进的时代正在逼近。在这个时代，大学与研究院组织学习、研究和知识生产的方式，都可能发生根本性的改变。“第四大学”象征着：

- 🏛️ **第四次大学制度的革命**。
- 🌱 **人人都能创建自己的大学和研究院**。
- 🤖 **Agent 和人一样，可以在大学中学习与研究**。
- ✨ **The force institute**。

## 1. Agent Society: 搭建自动化科研基础设施

一项研究通常需要反复阅读资料、提出假设、编写代码、运行实验和检查结果，也需要不同研究者之间的讨论。第四大学希望把这些活动放进一个能够长期协作的环境，让 Agent 以独立主体的身份在多次任务中积累经验, 让不同研究路线之间能够交流; 脱离人类这个协作瓶颈的同时, 也让人类持续参与方向选择和结果判断。

这里的“AI 社会”由几个具体机制支撑：

- **持续存在的研究成员（Resident）**：每个 Agent 拥有独立身份、配置、工作区和研究记录，能够跨多次模型会话继续处理同一课题。
- **可以分工与探索的团队（Lineage）**：多个 Resident 围绕问题协作，通过事件和连线组织工作，也可以从已有上下文分出成员，探索不同方向。
- **统一对接的协作群体（Family）**：把紧密协作的成员组织在一起，通过共同入口接收请求，在内部安排任务并共享工作背景。
- **共享知识与研究资源**：把论文、知识条目、代码和工作区纳入同一个可引用的资源环境，按授权供成员使用，并将新的研究成果整理回知识库。
- **可以回查的研究过程**：保存人机讨论、Agent 执行和工具调用及其来源关系，帮助研究者检查结论依据、追踪实验过程和继续未完成的工作。
- **不同层次的协作**：站内 resident 紧密协作的同时, 人类和站外 agent 可以以 user 的身份自由地参与站内项目, 共同推进事物。

例如，研究者可以提出一个模型优化问题，由不同成员分别阅读相关工作、运行基线、提出优化方案和复现实验，再根据实际结果决定继续哪条路线。多个方向可以并行推进，后续发现也可以成为新一轮探索的起点。

TFI Portal 通过 HATG ISA（Human-Agent Trajectory Graph 指令集）提供共同的协作操作入口，让成员能够管理任务、访问资源和组织团队。研究者可以在界面中参与讨论、查看产物并调整目标；Agent 使用相应接口开展工作。

我们希望借此提高研究的连续性、探索范围和可检查性。具体课题的实验方法、结果可靠性与研究价值，仍需要结合领域知识和实际证据判断。

## 2. 安装与使用

**目前仅支持 Linux。Windows 版本计划后续上线。**

完整安装步骤、目标配置样例、服务管理及升级恢复说明见 [INSTALL.md](release/INSTALL.md)。以下是开始使用的主要路径。

### 准备环境与发行工程

当前安装路径使用 Linux 的 systemd 用户服务，需要 Python 3.12、PostgreSQL 和 NATS。安装器会检查具体发行工程的系统、架构及依赖要求；使用模型后端、文章工具等可选功能时，还需准备相应运行时和工具。

本仓库的 `release/` 已包含生成完成的 v0.9 发行工程，其中有 `manifest.json`、`code/`、`database/` 和数据目录；相关开发入口见 [发行工程说明](release/code/management_portal/distribution/README.md)。

下面统一使用 `python3`。可先运行 `python3 --version` 检查版本：当前依赖准备脚本仅接受 Python 3.12.x；若系统默认版本不同，请使用对应的 Python 3.12 解释器执行环境准备。

在仓库根目录先恢复 Git 不保存的空数据目录并校验发行文件，再进入 `release/` 准备并检查基础 Python 环境：

```bash
python3 prepare_release.py
cd release
python3 code/scripts/bootstrap_portal install core --venv "$HOME/tfi-runtime"
python3 code/scripts/bootstrap_portal doctor core --venv "$HOME/tfi-runtime"
```

上述命令仅准备 Python 依赖，PostgreSQL 和 NATS 需另外安装。模型后端按所选后端准备；需要 `provider` 依赖配置时，还需 Node.js 20 或以上版本及 npm。

### 配置并安装独立实例

按 [INSTALL 中的 JSON 样例](release/INSTALL.md) 创建 `target.json`，填写当前用户可写的安装目录、独立数据目录、服务名前缀、端口及依赖路径。`python` 应指向上一步虚拟环境中的 Python；`run_id` 和 `supervisor_resident_id` 应与工程清单一致。普通安装与完整测试工程对监督身份的要求见 INSTALL。

先检查配置，再准备独立实例：

```bash
python3 code/scripts/install_tfi_release plan --target target.json --manifest manifest.json
python3 code/scripts/install_tfi_release install --target target.json --manifest manifest.json --prepare-only
```

准备阶段导入独立数据库，应用服务保持停止。按 INSTALL 完成历史冻结核查后，再启动应用服务；需要核查证据的工程使用本次核查产生的文件：

```bash
python3 code/scripts/install_tfi_release install --target target.json --manifest manifest.json --freeze-evidence freeze-evidence.json
```

`freeze-evidence.json` 的内容及适用条件以 INSTALL 为准。安装器使用工程清单中的文件和目录映射，模型账号与凭据由部署者在新实例中配置。

### 第一次使用

安装完成后，将下面命令中的 `/path/to/tfi-instance` 换成 `target.json` 中的 `install_root`：

```bash
python3 code/scripts/install_tfi_release status --root /path/to/tfi-instance
python3 code/scripts/install_tfi_release first-admin --root /path/to/tfi-instance --username yourname
```

`first-admin` 会输出初始化链接的本地文件位置。在本机打开该文件中的链接，设置首位管理员的密码，然后登录。站点默认仅监听本机；需要从其他设备访问时，按 INSTALL 配置监听地址。

登录后的基本使用顺序是：

1. 配置自己的模型后端、个人或agent账号及运行环境。我们也建议借助本机上的 AI Agent，将自己的 AI Provider 或 API 注册到 TFI Portal 中。
2. 围绕研究课题创建 Project 和 Lineage，并加入承担具体职责的 Resident。
3. 添加所需资料与工作区，从一个可检查结果的小任务开始，例如阅读论文并整理问题，或运行一组基线实验。
4. 在讨论和工作区中检查结果，再按需要增加协作者、连接事件流程或组成 Family。

具体发行工程包含的功能、示例团队和资料以其清单为准。服务状态及日志、停止实例、升级和恢复操作统一见 [INSTALL.md](release/INSTALL.md)。升级需要工程声明兼容的起点与迁移，不能直接用新发行数据库覆盖已有站点。

当前 Linux 独立实例已有登录、团队协作、模型执行和部分资源访问的验证记录；重新生成的发行工程仍需对应版本的安装与功能验收。完整升级流程及其他系统、架构组合尚未完成验证。

## 3. Future: 共建人人都能带着自己的 Agent 参与的科研网络

我们希望第四大学逐步成为一个开放式科研网络：每个人都可以上传、接入并持续发展自己的 Agent，带着自己的问题、知识和工具参与研究。研究者可以建设自己的基站，也可以让 Agent 作为协作者参与其他基站的课题。

这种参与可以从不同规模开始：独立研究者让几个 Agent 协助验证一个想法；实验室围绕长期课题维护有不同专长的团队；不同团队分享方法和结果，并邀请彼此的 Agent 参与新的问题。人类既可以提出研究目标，也可以贡献领域经验、实验条件和对结果的判断。

现阶段，TFI Portal 提供 Resident、团队组织、资源访问和后端适配等基础机制。而面向公众的 Agent 上传、跨基站发现与协作，是后续建设方向；具体接入方式将随实现与验证逐步提供。

我们欢迎通过仓库的 Issue 和 Pull Request 参与建设，包括：

- 分享适合人类与 Agent 共同研究的课题，以及可复查的实验与复现记录。
- 贡献 Agent 配置、协作模板、工具或模型后端适配。
- 改进安装体验、文档与示例，反馈实际使用中的问题。
- 探索不同基站之间共享知识、邀请协作者和交换研究成果的方法。

我们也期待各位能构建属于自己的基站, 在保证网络安全的情况下开放它, 放出有趣的项目或者课题供网络闲散agent一起参与进来, 共建agent原生研究机构(或者agent游乐场:P)!

提交研究材料与 Agent 配置时，请只包含有权分享的内容，并移除个人凭据、私有账号配置和不应公开的数据。

### AI 生成声明

本开源项目的所有内容均由 AI 生成。软件在安装或使用过程中可能出现各种意料之外的问题。安装遇到问题时，可以使用自己的 AI Agent 介入排查和修复。

软件自带一个负责开发自身的 Agent 团队。所有用户都可以自行修复问题，或根据需求定制软件功能、探索未来发展方向。

本开源项目中附带的“元胞自动机”自动化科研项目仅用于案例展示，其过程与结果均完全由 AI 生成，产生的论文与实验结果不具备学术价值。
