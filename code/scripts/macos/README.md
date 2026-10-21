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

试用包尚无 Developer ID 签名和 Apple 公证。macOS 可能阻止首次打开；核对下载来源与
SHA-256 后，可按 Apple 的[打开受阻应用说明](https://support.apple.com/102445)
在“系统设置 → 隐私与安全性”允许本次打开。不要关闭系统的整体安全检查。

## 构建与验证

在对应芯片的 macOS 15 构建机、Python 3.12 环境中执行：

```sh
python3 -m pip install -r code/requirements.txt
python3 code/scripts/macos/build.py
```

构建器校验固定摘要的 Python 与原生程序，生成基础和完整案例包，运行真实初始化、
数据库导入、launchd 启停、登录页就绪和重复启动检查，再生成与校验 DMG。
两种架构都通过后 GitHub Actions 才发布预览版；产物附带 SHA-256 与构建回执。
这些检查不替代用户设备上的 Gatekeeper、Finder 拖拽和所有业务功能验收。
