# The Fourth Institute

TFI Portal v0.9 的源码和初始化数据。

## 目录

- `release/code/`：当前源码及安装器。
- `release/database/distribution.sql`：供新实例导入的 PostgreSQL 初始化数据。
- `release/data/`：本次方案选定的知识、技能、工作台和成果文件。
- `release/manifest.json`：发行范围、文件大小及 SHA-256 校验清单。
- `release/INSTALL.md`：安装说明。

发行工程不含原开发仓库的 Git 历史、源账号凭据或旧阶段进展。新实例自行注册管理员、配置 AI 账号，并从空的阶段进展开始积累。Lenia 工作台使用第 166 版，两个宽泛的额外 Workspace 已排除。

默认带入“创建与维护 Skill”，同时保留运行所需的 Agent 共用规则和统一资源 ID 核心规则。

## 准备和安装

Git 不保存空目录。克隆或下载本仓库后，先执行以下命令恢复发行工程的空数据目录，并核对全部文件：

```sh
python3 prepare_release.py
python3 release/code/scripts/tfi_release_install.py --help
```

然后按 [安装说明](release/INSTALL.md) 配置独立的安装目录、数据库、数据目录、服务名和端口。需要联网准备运行依赖。

数据库文件是初始化导出，运行中的数据库目录、用户账号及密钥不应提交到本仓库。安装会使用自己的数据目录。

本次交付核对发行文件完整性、空阶段进展、工作台资源及 SQL 导入；完整实例安装和 AI 账号对话应在部署环境中验收。

## 许可证

见 [LICENSE](release/code/LICENSE)、[NOTICE](release/code/NOTICE) 和 [第三方说明](release/code/THIRD_PARTY_NOTICES.md)。
