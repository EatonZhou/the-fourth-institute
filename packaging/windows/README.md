# Windows 安装与使用（预览版）

目标系统为 Windows 11 x64。Windows 原生安装、Resident 对话与设备连接由实际
使用者验收；当前不声明通过 Windows 实机验收。

## 使用

1. 双击 `TheFourthInstitute-Setup-x64.exe`，选择新的、当前用户可写的空目录。
2. 安装完成后自动准备默认示例 `example_instance`。进度窗口显示当前阶段，
   不需要选择实例或点击创建。首次准备需要联网下载 PostgreSQL、NATS、Node.js、
   Python 包和所选功能工具，可能耗时几分钟；Python 本身已打包。
   如果 Windows 尚未启用长路径，会请求管理员授权，成功后自动继续。
3. 服务就绪后自动打开浏览器：首次使用进入管理员注册页，为 `admin` 设置密码；
   已有账号则进入正常 Portal 页面。不预置密码，不修改已有账号。
4. 在 Portal 的执行配置页面添加 AI Provider 账号并配置 Backend。可让自己的
   AI Agent 协助安装、查看错误和配置账号，也可复用已安装的 Provider 程序。

日常双击桌面的 **The Fourth Institute**，程序自动启动默认实例并打开浏览器。
准备成功后进度窗口自动关闭，后台服务继续运行。退出 Windows 用户会话后，
再次双击桌面图标即可。桌面入口不展示实例选择、状态、启动或首次管理员操作按钮；
共享平台的多实例能力仍由原命令行与 Portal 管理功能提供。
默认桌面实例使用本机地址；局域网与自定义实例沿正式安装配置处理。
不注册系统服务、不收集 Windows 登录密码、不修改系统 PATH。

## 目录与故障

- `code/`：共享代码；`dependencies/`：共享程序和 Python 环境。
- `instance/<名称>/`：各实例独立数据、配置和运行记录。
- `.build/windows/logs/<名称>.log`：初始化和生命周期命令输出。
- `instance/<名称>/runtime/dependency-install.log`：依赖下载与安装日志。
- `instance/<名称>/runtime/<组件>.log`：Windows 用户态组件日志。

失败时进度窗口显示原因，并提供 **重试** 与 **查看日志**。已经导入完成的数据库不会
重复导入；中断在导入或功能初始化内部的情况会拒绝自动覆盖，需诊断后处理。
初始化由正式安装器完成完整性校验，并在应用启动前只读检查旧任务冻结状态。
验收用 Resident 监督工程仍使用其显式核验流程。
依赖下载尚未完成的实例也可停止并卸载平台，实例文件仍保留。
后台 Python 统一使用 UTF-8；应用缓存放在实例的 `home/AppData/`，
服务登记目录保持稳定，初始化检查与桌面启动器读取同一份登记。

同一平台只允许执行一次桌面启动准备。卸载前请等待准备完成或关闭失败窗口；
卸载会先停止本站实例，保留 `instance/`、依赖缓存与诊断记录。此预览安装包拒绝
覆盖已有平台代码，尚不提供跨版本 schema 升级；不要把重新运行 Setup 当作数据迁移。
安装包尚未代码签名，Windows 可能显示未知发布者或信誉提示。
TAU 执行器当前依赖 Linux；浏览器工具安装成功不代表 Windows TAU 可执行。
各 AI Provider、Article 编译、Device 与实际 Resident 对话仍需逐项验证。

## 构建

公开示例已缩短架构资料的重复层级及动画文件名，并保持资源 ID、条目名称和内容
引用一致。路径整理应用于所有平台的公开 seed；已安装实例不在此操作范围内。
较长的安装目录和后续依赖仍可能需要 Windows 长路径支持。

在 Linux 上安装 NSIS，或在 Windows 安装 NSIS 并将 `makensis` 加入构建 PATH。
从仓库根目录执行：

```sh
python3 packaging/windows/build.py
```

输出 `.build/windows/TheFourthInstitute-Setup-x64.exe`、SHA-256 与 `receipt.json`。
构建器只读取 `code/`、公开示例 seed 和指定说明文件，拒绝链接与 Git 元数据；
不读取其他实例。Python 使用 `runtime.json` 固定 URL、大小和 SHA-256，
保留上游许可证。生成平台声明只影响 Windows 派生工程，公开 seed 原文件保持不变。
编译安装包前，从实际待打包的代码和 seed 压缩包运行 `prepare_install.py --platform windows`，
由正式安装器校验生成工程的入口、数据库摘要及全部文件清单；失败时停止构建。

安装向导使用 [NSIS](https://nsis.sourceforge.io/Docs/)；内置 Python 使用
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)。
首轮手工验收包括安装、管理员注册、两个实例的独立启停、实际 AI 回复、长路径与中文路径、
退出登录后重新启动，以及卸载后实例数据保留。反馈时提供错误步骤、Windows 版本和日志；
不要公开账号授权文件、邀请链接或数据库。
