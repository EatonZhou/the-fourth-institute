# 实例配置

一个实例的配置只在实例根目录下的三类文件中（《Portal 实例协议》`decision_d88306d0bd17`）：

| 文件 | 归属 |
| --- | --- |
| `instance.json` | 清单：身份、部署（代码根与网页端口）、网络、服务名前缀与组件、数据绑定的依赖（PostgreSQL、备份程序）、区域位置 |
| `site.json` | 站点设置：认证、备份、Provider、Pulse、通知等功能设置，不含路径 |
| `secrets/` | 数据库连接（`postgres.env`）、签名密钥、外部服务凭据、私密代理设置 |

`management_common.instance_manifest` 是唯一的读取与校验模块；`portal_paths`、`site_config` 是它的投影。进程只接收 `PORTAL_INSTANCE_ROOT`，网页进程另加 `PORTAL_DEPLOYMENT`；命令行工具用 `--instance <名称>`（经本机实例登记 `~/.config/tfi/instances.json`）或 `PORTAL_INSTANCE_ROOT` 选择实例。清单中没有 Python 路径：服务按所运行代码的依赖清单，在代码旁 `dependencies/` 中选择运行环境。

创建、端口修改、代码更新和退役由安装器执行，见 [安装器说明](../INSTALL.md)。修改端口时安装器在本机登记锁内确认端口和服务名前缀不与其他已登记实例重复，再替换清单；写清单与生命周期操作共用实例根目录下的 `.configuration.lock`。

`load()` 读取并校验核心配置：身份、部署、核心端口、服务清单结构与存储位置。程序定义和附加监听配置按服务归属校验，`service_code()`、`service_environment()`、`service_program()` 与 `listener()` 在使用选定服务时执行对应检查；一个服务的定义错误不阻断无关页面或其他服务启动。配置写入前使用 `validate()` 校验整个候选清单，拒绝未注册的程序或监听、缺失程序的附加组件、不完整定义、非法路径及端口冲突。实例状态返回按组件归属的 `configuration_errors`，保留核心页面和停服入口。

运行时路径读取通过 `instance_manifest.current_instance()` 复用已校验的核心配置，`portal_paths` 与登记位置解析共用此入口。每次读取核对所选实例及清单文件的身份、修改时间、变更时间与大小，修改、替换、删除或切换实例立即使旧版本失效；清单不能是软链接。此复用只保存配置，文件遍历、资源授权、个人空间边界和正文仍按各自领域的当前事实检查。

## 附加服务注册

附加服务通过安装器的 `register-service` 入口登记，见 [安装器说明](../INSTALL.md)。服务组件、完整程序定义与可选监听在实例配置锁和主机登记锁内一起校验、一次原子替换，不能分别手工写入 `components` 和 `programs`。Provider 账号服务使用同一注册与服务定义安装入口，内置启动器目录由代码统一持有，不重复登记程序。

程序对象包含 `entrypoint`、`arguments` 和 `working_directory`；实例区域内的位置必须使用区域引用，其他位置使用规范绝对路径。`--listener` 可提供包含 `bind`、`port` 的 JSON 文件；`--listener-bind` 则在指定 IP 上分配一个可用端口。二者只能选一个，监听与程序一并提交。注册后只安装选定服务的单元，不启动或重启其他服务；原生单元安装失败可以重试，已提交的清单始终保持完整。

## Home 汇报的可选媒体依赖

文字汇报不依赖媒体工具。Portal视频编码使用FFmpeg（libx264；合成已有音轨时使用AAC）和CJK字体。报告自带的动画为无配音字幕视频；解释页接受固定音轨文件，按媒体合同合成视频。

可使用主机已安装的编码器和字体，或导入与目标平台匹配的已验证离线包：

```sh
python3 code/management_portal/home_report_dependencies.py --code code \
  --bundle /path/to/home-report-media.tar.gz --sha256 <交付的完整摘要>
```

命令核对包摘要与逐文件清单，再安装到`dependencies/native/home-report-media-v2/`。构建器只打包视频编码器和字体；目标平台要求见包内清单。Dev与Release的代码旁分别准备，版本不同的包不能原位覆盖。该命令不安装系统包，不改实例配置或重启。

需要自定义工具或字体位置时，进程可显式设置`TFI_HOME_REPORT_FFMPEG`、`TFI_HOME_REPORT_FONT`；默认字体为Noto Sans CJK。视频生成失败独立显示并支持重试，不能将文字汇报可用或依赖安装完成当作播放验收。

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

每个 Codex 服务通过 `--account-id` 指定账号，启动入口从正式账号登记解析并校验 `CODEX_HOME`。实例 runtime 可以清理重建，账号目录始终从登记取得。Kimi 按账号使用实例 runtime 下的控制目录。每个实例运行自己的常驻进程，多个实例与终端 CLI 共享同一目录里的登录与会话，由 Provider 自己协调登录刷新。进程控制记录、socket 和 Windows Job 身份归各实例所有。索引备份、启动锁、作业状态与日志保存在实例 runtime。

## 旧 Windows 配置的一次性转换

`migrate_instance_manifest.py` 支持旧版 Windows 当前用户服务的离线转换。转换前核对旧服务定义的实例根、程序和组件归属，确认核心服务及 Provider 子进程均已退出；SCM 服务尚不支持这条转换入口。转换失败恢复已移动的配置和服务定义。旧程序包含实例数据时，可用 `--code-root` 指向独立安装的新程序，保留旧程序和数据。

数据库路径转换同时扫描正斜杠与反斜杠形式的文本和 JSON，只转换位置字段，保留消息正文。旧订阅账号缺少 `account_dir` 时，`migrate_instance_registrations.py --account-directories` 接收经核对的账号到现有目录映射；映射必须完整，目录必须存在且互不重复。写入前仅允许 PostgreSQL 运行，转换不复制登录凭据。配置、数据库位置和账号登记是分别验证的阶段，单项转换通过不能作为完整迁移验收。
