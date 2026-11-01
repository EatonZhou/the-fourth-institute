# 安装器：创建、运行、代码更新与退役

实例的生命周期由安装器 `code/scripts/tfi_release_install.py`（入口 `code/scripts/install_tfi_release`）执行，规则见《Portal 实例协议》（`decision_d88306d0bd17`）。命令行、桌面启动器和管理界面调用的是同一套操作。

## 实例在哪里

- **代码目录**：仓库或安装包的程序目录，只放代码、示例实例的发布内容（`instance/example_instance/seed/`）和代码旁的依赖（`dependencies/`）。
- **实例根目录**：默认在代码目录之外的操作系统用户数据目录：Linux `~/.local/share/tfi/instances/<名称>`，macOS `~/Library/Application Support/TFI/instances/<名称>`，Windows `%LOCALAPPDATA%\TFI\instances\<名称>`。可以用 `--root` 指定其他位置（例如服务器上的 `/var/lib/tfi/instances/<名称>`）；落在代码目录内的位置一律拒绝。
- **本机实例登记**：`~/.config/tfi/instances.json` 只记录名称与实例根目录。安装器、管理界面和命令行都从这里查找实例。

实例根目录下：

```text
instance.json   清单：身份、部署、网络、服务、数据绑定的依赖、区域位置
site.json       站点设置（功能设置，不含路径）
secrets/        数据库连接、签名密钥等
database/       PostgreSQL 集群
data/<区域>/     各区域的默认位置
cache/ runtime/ 可重建的缓存与运行文件
```

清单中没有 Python 路径。服务按所运行代码目录的依赖清单，在代码旁 `dependencies/<依赖摘要>/venv` 找到运行环境；找不到就报错。

## 创建实例

先在仓库根目录组装安装工程（校验示例实例的发布内容，不修改任何数据库）：

```bash
python3 prepare_install.py
```

然后用 Python 3.12 创建实例（默认创建后立即启动）：

```bash
python3 code/scripts/tfi_release_install.py create \
  --engineering .build/example_instance/engineering/manifest.json --name my_portal
```

安装器依次：

1. 校验工程（文件清单、摘要、`data/<区域>/` 布局）和目标平台；用 `--skip-file-verification` 可跳过逐文件摘要，只核对布局与身份。
2. 选择实例根目录、端口（`--web-port`、`--postgres-port`、`--nats-port`，未指定的取本机空闲且不与其他已登记实例重复的端口）和服务名前缀 `tfi-<名称>`；确认清单完整，端口和前缀不与本机其他实例冲突。`--bind 0.0.0.0` 允许局域网访问。
3. 在代码旁准备核心 Python 环境和 PostgreSQL、NATS、Node.js（`--python` 指定创建环境的基础 Python；`--offline` 只用安装包内置的依赖；`--postgres <目录>` 使用已有的 PostgreSQL）。
4. 写入 `instance.json`、`site.json`、`secrets/postgres.env`，登记到本机实例登记。
5. 把示例实例的 `data/<区域>/` 复制到新实例对应区域，初始化数据库并导入示例数据，安装服务单元（Linux systemd 用户单元、macOS launchd、Windows 用户态监督器）。
6. 核查导入的历史已冻结（有监督 Resident 的工程须用 `--freeze-evidence <核查JSON>` 提供该 Resident 的核查结果，须含本次 `run_id`、`supervisor_resident_id`、`manifest_sha256`、`passed:true`，`violations` 均为 0），应用工程声明的功能默认值，再启动服务并检查健康。`--no-start` 只创建不启动。

创建中途失败时，实例保留在登记中并带有未完成记录（`runtime/creation.json`，含失败阶段与原因），不能启动；查看原因后用 `retire` 退役，再重新创建。

新实例用首次管理员初始化建立第一个账号，邀请链接写入实例的 `secrets/first-admin-link.txt`，不输出到终端：

```bash
python3 code/scripts/tfi_release_install.py first-admin --instance my_portal --username yourname
```

`plan` 接受与 `create` 相同的参数，只校验并输出将要写入的清单，不改动主机。

## 日常操作

```bash
python3 code/scripts/tfi_release_install.py list
python3 code/scripts/tfi_release_install.py status --instance my_portal
python3 code/scripts/tfi_release_install.py stop   --instance my_portal
python3 code/scripts/tfi_release_install.py start  --instance my_portal
```

只启停清单中列出的服务；停止时保留全部数据。两个实例可以同时运行，各用自己的端口。不给 `--instance` 时使用环境变量 `PORTAL_INSTANCE_ROOT` 选择的实例。

修改端口须先停止实例；安装器重新检查与本机其他实例不冲突后更新清单（PostgreSQL 端口同时更新 `secrets/postgres.env`）：

```bash
python3 code/scripts/tfi_release_install.py configure-ports --instance my_portal --release-port 28300
```

## 代码更新

实例没有单独的“升级”操作。代码按开发流程或安装包更新后，运行：

```bash
python3 code/scripts/tfi_release_install.py update-code --instance my_portal
```

它为实例各部署的代码准备依赖（已有则复用），按当前代码刷新服务单元，再重启服务。服务启动时自己把数据库结构更新到新代码需要的样子；示例实例的发布内容不会重新导入。需要退路时，更新前用备份软件做一次备份。

## 退役

```bash
python3 code/scripts/tfi_release_install.py retire --instance my_portal
```

停止服务、删除服务单元、从本机实例登记注销、删除实例根目录及其中的数据库。放在实例根目录之外的区域只列出，加 `--delete-external-areas` 才删除。账号目录、模型、实例之外的 Workspace 等主机资源不受影响。

## 平台说明

Linux 使用 systemd 用户服务；macOS 使用 launchd；Windows 由用户态监督器运行组件，不需要系统服务账号。Windows 与 macOS 的桌面启动器调用同一套命令。适配代码存在不代表原生验收通过；Windows/macOS 的依赖、IPC、登录和 Resident 对话仍需在对应主机上验证。
