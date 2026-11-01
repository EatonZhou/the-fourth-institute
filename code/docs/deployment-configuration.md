# 实例配置

一个实例的配置只在实例根目录下的三类文件中（《Portal 实例协议》`decision_d88306d0bd17`）：

| 文件 | 归属 |
| --- | --- |
| `instance.json` | 清单：身份、部署（代码根与网页端口）、网络、服务名前缀与组件、数据绑定的依赖（PostgreSQL、备份程序）、区域位置 |
| `site.json` | 站点设置：认证、备份、Provider、Pulse、通知等功能设置，不含路径 |
| `secrets/` | 数据库连接（`postgres.env`）、签名密钥、外部服务凭据、私密代理设置 |

`management_common.instance_manifest` 是唯一的读取与校验模块；`portal_paths`、`site_config` 是它的投影。进程只接收 `PORTAL_INSTANCE_ROOT`，网页进程另加 `PORTAL_DEPLOYMENT`；命令行工具用 `--instance <名称>`（经本机实例登记 `~/.config/tfi/instances.json`）或 `PORTAL_INSTANCE_ROOT` 选择实例。清单中没有 Python 路径：服务按所运行代码的依赖清单，在代码旁 `dependencies/` 中选择运行环境。

创建、端口修改、代码更新和退役由安装器执行，见 [安装器说明](../INSTALL.md)。修改端口时安装器在本机登记锁内确认端口和服务名前缀不与其他已登记实例重复，再替换清单；写清单与生命周期操作共用实例根目录下的 `.configuration.lock`。

## 设备 SSH 本机路径

设备连接用的 SSH 配置文件（`config_path`）和私钥文件引用（`identity_file`）保存在设备登记的 `access_methods.ssh` 中，站点设置不含这类路径。值必须是主机上文件的规范绝对路径（不含 `..`），这些文件属于主机，不随实例备份、搬迁或退役；登记中只保存路径，不保存私钥或密码。

修改入口是 `UpdateResource` 对设备的 `access_methods` 修改（设备登记的 `update_device`）。主机、账号和端口同在这份登记中。GPU 子设备沿父链找到实际 SSH 入口后，使用该入口设备的登记。设备连接测试、终端和 GPU 状态采集共用设备后端的同一份读取，每次读取当前登记。路径不是绝对路径时返回配置错误。

## Provider 程序入口

Provider 程序属于代码层，安装在实例所运行代码旁的 `dependencies/providers/` 中，由 `management_common.code_environment.provider_entry` 定位，不写在清单或站点设置里：

| 入口 | 用途 |
| --- | --- |
| `codex`（Windows 为 `codex-cli-current`） | 所有 Codex 账号共用的程序入口 |
| `claude-agent-sdk-current` | Claude Agent SDK 虚拟环境入口 |
| `claude-app-server-current` | Portal Claude daemon 代码包入口 |
| `kimi-code-current` | Kimi Code 程序包入口 |
| `dsh-runtime-current` | DSH 依赖与桥接运行时入口 |
| `opencode-current` | OpenCode 程序包入口 |
| `antigravity-current` | Antigravity 程序包入口 |

入口保留最后一级软链接；服务定义引用入口而不是某个版本。启动方和更新器使用同一入口，更新器通过切换入口选择新版本，并继续遵守空闲检查与失败回退。不在主机 PATH 中查找 Provider 程序。更新器等待切换时启动的瞬态作业 `<前缀>-provider-activation` 属于实例：停止实例时一并停止，停止前核对它是瞬态单元且环境选择的是本实例。

Codex 各账号的主机目录保存凭据和会话，共用 `codex_binary`；socket 和 Portal 控制文件保存在实例 runtime。账号下由 Codex 自身生成的 `packages/app-server-daemon` 属于独立的自管理安装产物，不能充当 Portal 程序入口；清理前须检查运行进程和实际服务命令是否引用它。

## 执行账号目录

订阅账号的主机目录由登记的 `config.account_dir` 持有，必须显式提供规范绝对路径，不能经过软链接。目录的位置由主机用户选择，Portal 不设默认目录。

指定目录不存在时创建空目录后登录；接入现有账号直接登记其路径。Portal 不复制凭据，不移动或改名目录，删除登记保留 Provider 数据。运行时必须使用登记路径，缺失目录或登记路径时直接报错。更改目录需要账号无绑定会话、执行或登录，随后按新登记重建常驻进程。

每个 Codex 服务使用实例 `runtime/provider-accounts/codex/<account_id>/launch.json` 中的生成记录启动；生成记录来自账号登记。Kimi 按账号使用同一 runtime 下的控制目录。常驻宿主在操作系统用户的 `~/.config/tfi/provider-locks/` 中按规范账号目录的 SHA-256 持有主机锁，锁占用时报冲突；终端直接调用 CLI 不受此锁约束。索引备份、启动锁、作业状态与日志保存在实例 runtime。
