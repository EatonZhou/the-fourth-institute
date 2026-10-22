# macOS 安装包 · 试用版

此试用版包含 Codex 后台启动环境、长路径通信、登录等待提示及应用图标修复。
目标为 macOS 15 及以上，分别构建 Apple Silicon（arm64）和 Intel（x64）DMG。

打开 DMG，将 **The Fourth Institute.app** 拖到 Applications，弹出磁盘映像，然后打开应用。
首次启动可以取消勾选“安装 Lenia 预置案例”。不勾选时只解压基础资料，案例相关的
项目、Resident、工作区、文件和数据库记录均不导入。安装重试保持首次选择。

**正在使用 20261022.2 及更早测试包的用户**：这些包不支持覆盖旧运行目录。请在 Terminal 使用独立目录启动，
保留原有资料与账号；新目录需要重新连接 AI 账号：

```sh
"/Applications/The Fourth Institute.app/Contents/MacOS/TheFourthInstitute" --platform "$HOME/Library/TFI-trial-20261022"
```

兼容升级实现已经接入源码，经过后续原生构建验证的包会在发现旧目录时显示
“升级并保留资料”。相同芯片、内置运行环境与 Python 依赖的应用更新保留原账号、
项目、数据库和案例选择；无需重新授权，也不导入新包的 Lenia 案例。
升级前关闭任务并等待队列完成。启动器停止本实例服务、显示复制进度、保留旧目录备份，
然后切换新版；失败时恢复旧版，失败现场另存。运行环境不兼容时继续使用独立测试目录。
命令行的对应操作为 `--upgrade`，旧包不支持此参数。私有备份和升级记录在
`~/Library/.TFI-launcher/`；它们包含账号及资料，勿上传或作为普通缓存清理。

完整基础安装包内置 Python 3.12、核心 Python 包及全部间接依赖、PostgreSQL、NATS、Node.js，
准备基础平台无需联网，也不依赖电脑已有的 Python 或 Homebrew。两种芯片各有固定版本和
摘要的离线 wheel 包；优先复用预装环境，缺少 Python 包时从包内恢复到独立环境。
离线材料缺失或损坏时明确报错，不转为联网下载。此机制将在下一次原生构建验证后随测试包交付。
连接 AI Provider、下载其运行工具及使用在线模型
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
# Apple Silicon 用 arm64，Intel 用 x86_64。
python3 -m pip install --require-hashes --only-binary=:all: -r code/scripts/macos/requirements-core-arm64.lock
npm install --ignore-scripts --prefix /tmp/tfi-codex-check @openai/codex@0.163.0
export TFI_TEST_CODEX_BINARY=/tmp/tfi-codex-check/node_modules/.bin/codex
python3 code/scripts/macos/build.py
```

构建只消费已提交的架构锁文件，包内安装使用 `--no-index --require-hashes`，并运行
`pip check` 与空虚拟环境的离线安装、核心检查及 WebAuthn/CBOR 调用检查。
维护者更新依赖时显式运行 `python3 code/scripts/macos/python_dependencies.py --refresh-locks`，
下载并验证两种架构的完整依赖后才替换锁文件；正常安装和构建不重新解析版本。
Intel 使用仍提供兼容二进制包的 cryptography 48.0.1 与 cbor2 5.9.0，配套锁定
webauthn 2.8.0、pyOpenSSL 26.2.0；选择在维护脚本中显式记录，缺件不自动降级或现场编译。

构建器校验固定摘要的 Python 与原生程序，生成基础和完整案例包，运行真实初始化、
数据库导入、launchd 启停、登录页就绪、重复启动与注销后重启检查，再生成与校验 DMG。
完整应用在组装后签名，运行验证后复核签名，成品 DMG 只读挂载并再次核验应用和入口。
初始化检查经过应用的实际可执行入口；Tk 界面测试覆盖复制过程中的响应与失败重试。
两种架构分别验证，产物附带 SHA-256 与构建回执。测试包保存到 Workspace，
通过 Portal 文件下载链接直接交付；“重新打包”“给我试用”仅授权构建和交付测试文件。
构建时还通过 launchd 实际启动验证专用的 Codex，使用空账号检查模型列表和长短通信路径、
退出清理；不登录、不调用模型，该验证工具不打进安装包。应用图标与欢迎页复用 TFI Logo。
源码提交和版本号更新均不触发 GitHub 打包或发布。工作流只支持手动运行，默认只构建，
临时构建归档保留一天。只有用户明确要求“发布到 GitHub”后，才可启用
`publish_to_github`；直接使用发布脚本同样须显式传入 `--publish-to-github`。
仓库只保留最新完整发布的 macOS 预览版，同版 Apple Silicon 和 Intel 安装包均保留。
新版完整上传并校验后，自动删除更旧的预览 Release、附件及标签；失败或上传未完成时保留当前可用版。
这些检查不替代用户设备上的 Gatekeeper、Finder 拖拽和所有业务功能验收。
