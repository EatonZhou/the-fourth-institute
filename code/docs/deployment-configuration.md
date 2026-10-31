# 实例部署配置

已安装实例使用 `config/target.json` 作为部署参数的唯一输入。源码目录、Python 和原生依赖位置、数据根目录、端口及可选的 Dev/Release 双入口拓扑都由它持有。站点业务选项保存在 `config/site.json`，凭据由各自的密钥存储持有。

| 文件 | 归属 |
| --- | --- |
| `config/target.json` | 部署输入，通过配置命令变更 |
| `installation.json` | 安装状态、已应用配置摘要；v2 不再保存 `target` 副本 |
| `config/development.json` | 生成的 Dev/Release 运行视图 |
| `config/deployments.json` | 生成的浏览器入口登记 |
| `config/postgres.env`、`config/network.env` | 生成的连接与网络环境 |
| systemd 单元和双入口 drop-in | 服务适配器维护启动方式，配置工具更新部署字段 |
| `config/generation.env` | 发布和 Worker 换代状态 |

`management_common.instance_config` 持有部署读取、摘要验证和安装状态格式。运行组件从该接口取得配置，不把生成文件作为另一个可写的权威来源。生成的 `site.json` 部署路径由配置工具更新，其站点名称、认证和业务选项保留。

已安装实例不再生成或需要 `config/paths.json`；数据目录直接读取 `target.json` 的 `data_roots`。新生成服务的 `PORTAL_PATHS_CONFIG` 指向 `config/target.json`。尚未重启的旧进程可以继续携带旧的 `config/paths.json` 定位值：它只用于识别实例目录，不读取该文件，文件不存在也不会改变路径。部署服务定义更新后，旧定位值在下一次正常启动时退出，无需为删除副本重启服务。

没有安装状态的源码运行和测试仍可使用独立的四项路径配置；它不属于已安装实例的配置输出。

配置事务使用稳定的 `.configuration.lock` 协调读取和提交，避免在线导入时读到“新 target、旧状态摘要”的中间组合。旧实例的只读检查不创建文件；首次导入和后续写入建立该锁。代码升级备份和恢复保留当前锁文件的身份。

## 设备 SSH 本机路径

`config/site.json` 的 `deployment.device_ssh_paths` 按 Device ID 显式配置本机的 `config_path`（SSH config）与 `identity_file`（私钥文件引用）。值必须是绝对路径或空字符串；配置中只保存路径，不保存私钥或密码。

为某个设备配置该项后，这两个路径完全由部署配置提供，省略字段表示不指定该路径。主机、账号和端口仍取设备记录。未配置该项的设备继续使用自身登记的 SSH 元数据。GPU 子设备沿父链找到实际 SSH 入口后，使用该入口设备的部署配置。

设备连接测试、终端和 GPU 状态采集共用设备后端的路径解析，每次读取当前配置。修改本机路径无需改源码或重写设备身份记录。配置格式或路径无效时返回配置错误。

## Provider 程序入口

Provider 当前版本的稳定入口在 `config/site.json` 的 `deployment.providers` 中显式配置：

| 字段 | 用途 |
| --- | --- |
| `codex_binary` | 所有 Codex 账号共用的程序入口 |
| `claude_runtime_current` | Claude Agent SDK 虚拟环境入口 |
| `claude_app_server_current` | Portal Claude daemon 代码包入口 |
| `kimi_runtime_current` | Kimi Code 程序包入口 |
| `dsh_runtime_current` | DSH 依赖与桥接运行时入口 |
| `opencode_runtime_current` | OpenCode 程序包入口 |

这些值为绝对路径，保留最后一级软链接；不能把入口解析成某个版本后再写入服务定义。启动方和更新器使用相同字段，更新器通过切换该入口选择新版本，并继续遵守空闲检查与失败回退。版本包仍安装在 `runtime_root` 下。未配置入口的源码安装保留首次接入的发现和引导默认值。

Codex 各账号的主机目录保存凭据和会话，共用 `codex_binary`；socket 和 Portal 控制文件保存在实例 runtime。账号下由 Codex 自身生成的 `packages/app-server-daemon` 属于独立的自管理安装产物，不能充当 Portal 程序入口；清理前须检查运行进程和实际服务命令是否引用它。

## 导入已有实例

从匹配的 v1 安装记录导入时运行：

```bash
python3 code/scripts/tfi_instance_config.py adopt --root /实际实例目录
python3 code/scripts/tfi_instance_config.py check --root /实际实例目录
```

导入核对现有数据目录、连接配置及双入口配置是否一致，将已有双入口拓扑纳入 `target.json`，随后把安装记录转为只保存状态和摘要的 v2。它保留现有运行值，不移动数据、创建路径别名或切换依赖版本。应用前先发布支持 v2 的源码，并保留备份；旧代码直接读取内嵌 `target` 的方式无法读取 v2。

## 变更部署参数

复制已应用的 `target.json` 为候选文件，在候选中修改参数。用 `plan --candidate /候选文件 --root /实际实例目录` 查看变更，再把返回的 `source_sha256` 传给 `apply --expected-sha256`。不要直接编辑已应用的主配置或生成文件。

参数变化需要停止实例消费者，并事先准备好数据目标目录。配置命令只负责配置事务，数据迁移和安装位置、代码、依赖的移动由部署工具负责。Dev/Release 拓扑通过 `tfi_development_runtime.py configure` 变更。

`check` 验证生成文件和受管理服务字段，包括 URI 编码后的数据库连接、内部 socket、公共网关和双入口定义。手工修改配置导致漂移时会明确报错。中断事务保留备份和日志；`recover` 仅恢复仍匹配事务前后内容的文件，拒绝覆盖后续人工修改。

已有公共网关可以有独立的入口端口和网络访问策略；实例配置工具更新其共享数据路径、数据库地址和双入口路由 drop-in，保留由网关维护的网络配置。

## 执行账号目录

实例的四个数据根为 share、staging、instances 和 secrets。订阅账号的主机目录由登记的 `config.account_dir` 持有，必须是规范绝对路径且不能经过软链接。默认位置按 Provider 为 `~/.codex-accounts/<名称>`、`~/.claude-accounts/<名称>`、`~/.kimi-accounts/<名称>`、`~/.antigravity-accounts/<名称>`；名称去掉账号 ID 的 `<provider>_` 前缀。主目录取操作系统用户记录，忽略服务的 `HOME`。测试与安装器可显式设置 `PORTAL_ACCOUNT_HOST_HOME`，运行服务不需要配置该变量。

新建账号在默认位置建空目录后登录；接入现有账号直接登记其路径。Portal 不复制凭据，不移动或改名目录，删除登记保留 Provider 数据。运行时必须使用登记路径，缺失目录或登记路径时直接报错。更改目录需要账号无绑定会话、执行或登录，随后按新登记重建常驻进程。

每个 Codex 服务使用实例 `runtime/provider-accounts/codex/<account_id>/launch.json` 中的生成记录启动；生成记录来自账号登记。Kimi 按账号使用同一 runtime 下的控制目录。常驻宿主在账号目录的父目录持有 `.<名称>.portal-daemon.lock`，锁占用时报冲突；终端直接调用 CLI 不受此锁约束。索引备份、启动锁、作业状态与日志保存在实例 runtime。
