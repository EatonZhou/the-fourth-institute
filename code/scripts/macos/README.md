# macOS 安装包 · 试用版

支持 macOS 15 及以上，分别提供 Apple Silicon（arm64）和 Intel（x64）DMG。
下载与校验文件见仓库的 macOS Preview Release。此版本仍在试用阶段，暂不支持覆盖升级。

打开 DMG，将 **The Fourth Institute.app** 拖到 Applications，弹出磁盘映像，然后打开应用。
首次启动可以取消勾选“安装 Lenia 预置案例”。不勾选时只解压基础资料，案例相关的
项目、Resident、工作区、文件和数据库记录均不导入。安装重试保持首次选择。

Python 3.12、核心 Python 包、PostgreSQL、NATS、Node.js 已内置，准备基础平台无需联网，
也不依赖电脑已有的 Python 或 Homebrew。连接 AI Provider、下载其运行工具及使用在线模型
需要网络。TeX、浏览器自动化和测试工具由用户按需安装；缺失不阻止基础平台启动。

应用将运行代码与数据放在 `~/Library/TFI/`，不会写入只读 DMG 或应用包。
实例位于 `instance/desktop/`，数据库与账号保存在该实例；日志位于
`instance/desktop/runtime/`，首次启动日志位于 `.build/macos/logs/desktop.log`。
launchd 用户任务由本机登录用户管理，无需管理员授权。浏览器打开后退出启动窗口，
后台服务继续运行；以后打开应用会复用原实例，不重新导入数据。

停止服务并移除自动启动项（保留数据），在 Terminal 执行：

```sh
"$HOME/Library/TFI/dependencies/python/bin/python3" \
  "$HOME/Library/TFI/code/scripts/tfi_macos_launcher.py" --unregister
```

然后可移除 Applications 中的应用。`~/Library/TFI/` 中的资料仍保留，需删除时先自行备份。
不要在运行时移动该目录。过长的用户主目录会触发 Unix Socket 路径限制，安装器会明确报告。

试用包使用 ad-hoc 完整性签名，尚无 Developer ID 签名和 Apple 公证。
macOS 可能阻止首次打开；核对下载来源与
SHA-256 后，可按 Apple 的[打开受阻应用说明](https://support.apple.com/102445)
在“系统设置 → 隐私与安全性”允许本次打开。不要关闭系统的整体安全检查。
若提示“已损坏”且没有“仍要打开”，请保留弹窗，并提供以下只读诊断的输出；
该提示不能单独区分下载损坏、签名错误和系统信任策略。

```sh
codesign --verify --deep --strict --verbose=2 "/Applications/The Fourth Institute.app"
spctl --assess --type execute --verbose=4 "/Applications/The Fourth Institute.app"
```

ad-hoc 试用包的 `spctl` 评估可能拒绝执行；不将完整性校验通过称为 Gatekeeper 放行。

## 构建与验证

案例工程在打包时准备并校验。安装时直接读取应用包内选定的工程压缩包，省去 seed
中间副本和压缩包复制，取消安装阶段的逐文件 SHA 校验及共享代码重复比对。
复制、解压显示文件数、MiB 和百分比；路径检查、解压错误检查和启动健康检查保留。

在对应芯片的 macOS 15 构建机、Python 3.12 环境中执行：

```sh
python3 -m pip install -r code/requirements.txt
python3 code/scripts/macos/build.py
```

构建器校验固定摘要的 Python 与原生程序，生成基础和完整案例包，运行真实初始化、
数据库导入、launchd 启停、登录页就绪、重复启动与注销后重启检查，再生成与校验 DMG。
完整应用在组装后签名，运行验证后复核签名，成品 DMG 只读挂载并再次核验应用和入口。
初始化检查经过应用的实际可执行入口；Tk 界面测试覆盖复制过程中的响应与失败重试。
两种架构都通过后 GitHub Actions 才发布预览版；产物附带 SHA-256 与构建回执。
仓库只保留最新完整发布的 macOS 预览版，同版 Apple Silicon 和 Intel 安装包均保留。
新版完整上传并校验后，自动删除更旧的预览 Release、附件及标签；失败或上传未完成时保留当前可用版。
这些检查不替代用户设备上的 Gatekeeper、Finder 拖拽和所有业务功能验收。
