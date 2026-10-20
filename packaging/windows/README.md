# Windows 安装与实例管理（预览版）

目标系统为 Windows 11 x64。Windows 原生安装、Resident 对话与设备连接由实际
使用者验收；当前不声明通过 Windows 实机验收。

## 使用

1. 双击 `TheFourthInstitute-Setup-x64.exe`，选择当前用户可写的目录。
2. 打开桌面的 **The Fourth Institute**。示例资料包含长文件路径；如果 Windows
   尚未启用长路径，启动器会询问是否启用，并由 Windows 请求管理员确认。
   完成后重新打开启动器。取消不会初始化实例。
3. 使用默认的 `example_instance`，点击 **创建 / 继续初始化**。需要联网下载
   PostgreSQL、NATS、Node.js、Python 包和所选功能的工具；Python 本身已打包。
   如需局域网访问，在创建前勾选对应选项，并按 Windows 防火墙提示允许访问。
4. 初始化成功后点击 **注册首个管理员**，在打开的页面设置自己的密码。
   后续登录使用这个账号，不存在预置管理员密码。
5. 在 Portal 的执行配置页面添加 AI Provider 账号并配置 Backend。可让自己的
   AI Agent 协助安装、查看错误和配置账号，也可复用已安装的 Provider 程序。

日常使用 **启动 / 停止 / 打开 Portal**。创建另一个名称的实例会复用平台和依赖，
同时保有独立数据库、端口、账号与资料。当前预览版启动器管理安装目录内的实例；
导入已有实例需要沿正式迁移流程处理路径与数据库，界面不提供任意目录导入。
关闭启动器不会停止实例；退出 Windows 用户会话后需要重新打开启动器并启动实例。
不注册系统服务、不收集 Windows 登录密码、不修改系统 PATH。

## 目录与故障

- `code/`：共享代码；`dependencies/`：共享程序和 Python 环境。
- `instance/<名称>/`：各实例独立数据、配置和运行记录。
- `.build/windows/logs/<名称>.log`：初始化和生命周期命令输出。
- `instance/<名称>/runtime/dependency-install.log`：依赖下载与安装日志。
- `instance/<名称>/runtime/<组件>.log`：Windows 用户态组件日志。

错误后先保存日志，再使用 **创建 / 继续初始化** 重试。已经导入完成的数据库不会
重复导入；中断在导入或功能初始化内部的情况会拒绝自动覆盖，需诊断后处理。
初始化由正式安装器完成完整性校验，并在应用启动前只读检查旧任务冻结状态。
验收用 Resident 监督工程仍使用其显式核验流程。

卸载会先停止本站实例，保留 `instance/`、依赖缓存与诊断记录。此预览安装包拒绝
覆盖已有平台代码，尚不提供跨版本 schema 升级；不要把重新运行 Setup 当作数据迁移。
安装包尚未代码签名，Windows 可能显示未知发布者或信誉提示。
TAU 执行器当前依赖 Linux；浏览器工具安装成功不代表 Windows TAU 可执行。
各 AI Provider、Article 编译、Device 与实际 Resident 对话仍需逐项验证。

## 构建

在 Linux 上安装 NSIS，或在 Windows 安装 NSIS 并将 `makensis` 加入构建 PATH。
从仓库根目录执行：

```sh
python3 packaging/windows/build.py
```

输出 `.build/windows/TheFourthInstitute-Setup-x64.exe`、SHA-256 与 `receipt.json`。
构建器只读取 `code/`、公开示例 seed 和指定说明文件，拒绝链接与 Git 元数据；
不读取其他实例。Python 使用 `runtime.json` 固定 URL、大小和 SHA-256，
保留上游许可证。生成平台声明只影响 Windows 派生工程，公开 seed 原文件保持不变。

安装向导使用 [NSIS](https://nsis.sourceforge.io/Docs/)；内置 Python 使用
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)。
首轮手工验收包括安装、管理员注册、两个实例的独立启停、实际 AI 回复、长路径与中文路径、
退出登录后重新启动，以及卸载后实例数据保留。反馈时提供错误步骤、Windows 版本和日志；
不要公开账号授权文件、邀请链接或数据库。
