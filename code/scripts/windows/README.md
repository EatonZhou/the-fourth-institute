# Windows 安装与使用（预览版）

目标系统为 Windows 11 x64。Windows 原生安装、Resident 对话与设备连接由实际
使用者验收；当前不声明通过 Windows 实机验收。

## 使用

1. 双击 `TheFourthInstitute-Setup-x64.exe`，选择新的、当前用户可写的空目录。
   桌面入口以当前普通用户运行；不要选择“以管理员身份运行”。启动器会在提升的
   管理员进程中提前提示退出，避免数据库拒绝启动后留下未完成的初始化。
   在“预置案例”页选择是否安装 Lenia，默认勾选。取消勾选后，基础平台仍会安装，
   Lenia 项目及其团队、工作区、资料、代码和投影不会进入安装后的实例；安装器只释放
   对应的基础资料包，不会先解压完整案例再删除。
2. 安装完成后自动准备默认实例 `example_instance`：安装器把它建在程序目录之外的
   `%LOCALAPPDATA%\TFI\instances\example_instance`，并登记在本机实例登记中。进度窗口
   显示当前阶段，不需要选择实例或点击创建。Python、PostgreSQL、NATS 和 Node.js 已随安装包提供。
   PostgreSQL 所需的 Microsoft C/C++ 运行库也随包提供，经摘要核验后放在其程序目录，
   不依赖系统预装运行库，不为此提权或修改系统目录。
   运行库解压通过 Unicode 工作目录和 ASCII 相对路径调用 Windows 工具，支持中文安装目录。
   核心 Python 包及其传递依赖也已内置，首次准备从本地安装，无需联网下载。
   可选浏览器、测试、模型及排版工具不随首次安装准备，缺失不阻止 Portal 启动。
   基础安装可离线完成；连接在线 AI Provider、账号登录和扩展工具下载仍按功能需要联网。
   只有所选内容在当前目录下确实超出 Windows 默认路径限制，且系统尚未启用长路径时，
   才请求管理员授权；也可取消后改用更短的安装目录。基础工程缓存使用 `.build/w/`。
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

## 可选功能工具

Portal 安装器和通用 bootstrap 不自动安装 TeX。Article 的资料与编辑功能可以继续使用；
编译 PDF 时需要相应 TeX 编译器和 `latexmk`，语言服务需要 `texlab`。
请按需要自行安装这些工具及文档所用宏包，让 Portal 进程的 PATH 能找到它们，
然后重启 Portal 进程以加载新的环境。功能再次执行时重新检测工具，无需重装实例。

浏览器自动化、测试和其他扩展工具也按需配置。功能已包含在发布包中，并不表示它的
外部工具已经就绪；安装记录分别保存已验证的核心 profiles 和按需检查的可选 profiles。
缺少核心 Python 包、数据库或消息服务仍会阻止启动，并给出失败原因。

## 目录与故障

安装目录只放程序（代码与依赖）；实例放在程序目录之外，由安装器建在
`%LOCALAPPDATA%\TFI\instances\<名称>`，并登记在用户配置目录的 `.config\tfi\instances.json`。

- `code/`：共享代码；`dependencies/`：共享程序和 Python 环境，以及 `<依赖摘要>/install.log` 依赖安装日志。
- `dependencies/python-wheels/`：Windows Python 3.12 的核心依赖、锁定清单和构建回执。
- `dependencies/native/archives/`：安装包提供的原生依赖归档和按摘要命名的下载缓存。
- `.build/windows/logs/<名称>.log`：初始化和生命周期命令输出。
- `<实例根>/instance.json`、`site.json`、`secrets/`：实例清单、站点设置与密钥。
- `<实例根>/runtime/<组件>.log`：Windows 用户态组件日志；`runtime/service-units/` 是组件定义。
- `<实例根>/runtime/creation.json`：创建未完成时的阶段与错误；创建成功后删除。

失败时进度窗口显示原因，并提供 **重试** 与 **查看日志**。创建未完成的实例不可启动；
重试先用安装器退役它（停止服务、删除定义、注销、删除实例根），再从同一工程重新创建，
不续做半个实例。案例选择保存在安装目录的 `installation-options.ini`，首次准备时读取。
命令行准备支持 `prepare_install.py --without-examples`。
构建器从公开种子分别生成基础包和案例包，同时筛选 SQL、文件及关联清单，不修改源种子。
安装向导只写入所选的已准备工程包。首次启动直接解压到 `.build/w/engineering`，
省掉 seed 中间目录及 seed 到工程的复制，不再在用户机器上调用 `prepare_install.py`。
解压和复制到实例时显示文件数、百分比和已处理大小；完整解压后的重试复用该工程。
健康检查保留 HTTP 503 的组件诊断，并在错误窗口列出未就绪组件。Windows 用户态
组件进程已退出时立即报告，不再等待完整的六分钟超时。失败组件日志末尾同时进入
主安装日志，便于直接看到异常。数据库和实例文件保留供诊断，直到重试退役它。
启动完 web 后，进度进入整体就绪检查，显示尚未通过的项目与已等待时间。
HTTP 检查保留状态码；网页返回错误时，即使 web 进程仍在运行，也附上其有界日志末尾。
安装工程先解压到临时目录，完成后改名；中断后可重试，切换案例选择不会遗留旧案例文件。
桌面创建实例时传入 `--skip-file-verification`，取消全量文件 SHA 校验和代码比对，
不再重新扫描案例内容。ZIP 自带的损坏检测、路径边界、实例归属与旧任务冻结检查仍保留。
验收用 Resident 监督工程仍使用其显式核验流程。
依赖下载尚未完成的实例也可停止并卸载平台，实例文件仍保留。
后台 Python 统一使用 UTF-8；服务使用操作系统用户自己的用户目录，
初始化检查与桌面启动器读取同一份实例登记和组件定义。
管理页面启动的独立控制作业保留 Windows 的系统运行环境（包括 `SYSTEMROOT`），
但不继承其他实例的数据库连接、密钥或 Python 搜索路径。移动区域中断后，
若文件已移动但 Provider 会话引用未完成，实例保持停止；用原区域及同一目标重试即可继续，
不能以一次普通启动跳过引用迁移。
原生依赖先校验并复用随包归档；需要下载时显示组件与已下载大小，连接中断自动重试，
并保留 `.part` 文件供下次续传。服务器不支持续传时重新下载；完整摘要校验通过才会解压。
离线桌面包不会退回网络下载；缺件或损坏时提示重新获取完整安装包。
窗口显示结构化错误中的原因，完整调用栈只保留在日志。“重试”只重新执行已安装代码；
更新程序请运行新安装包（见 `UPGRADE.md`）。

Windows TCP 通信目录使用私有目录接口创建，不能用 `mkdir(mode=0o700)` 代替 DACL。
默认缓存叶目录为 `TFI/Portal/ipc-v2`，旧 `ipc` 目录及权限保持原样；显式配置的目录仍
按原路径进行权限检查。Python 和 Node 使用同一实现，不放宽对其他用户访问权限的检查。

登录及注册页面注入公开目录时，Windows 原生目录先按本机规则解析，再以正斜杠输出给
浏览器；盘符、UNC、中文和空格路径可用，目录穿越、控制字符和文件系统根仍被拒绝。

示例数据库及运行时工作区表接受 Windows 盘符、UNC 和 POSIX 绝对路径，
并拒绝相对路径或磁盘、共享根目录。SQL 导入使用单个事务；失败时保留诊断日志，
不自动删除数据库；重试先退役未完成的实例再重新创建。

同一平台只允许执行一次桌面启动准备。卸载前请等待准备完成或关闭失败窗口；
卸载会先停止运行本程序的实例并删除它们的组件定义。卸载向导的“数据与缓存”页面默认
保留实例和缓存；如需全新开始，勾选 **同时删除实例和缓存**，再次确认后由安装器退役
这些实例（数据库、实例文件和对话随实例根删除），并清理 `.build/` 和 `dependencies/`。
删除无法撤销，请先备份需要保留的内容。账号目录等主机资源不受影响；
放在实例根之外的区域目录保留。实例目录中的链接或重解析点会阻止自动删除。
停止实例后，Python 使用 Windows 扩展路径清理 `.build/` 中的深层准备缓存，
再退役实例；缓存清理失败时保留实例数据与平台程序，以便诊断和重试。
辅助进程退出后，卸载器再删除启动锁、日志、运行时和平台，最后移除空安装目录。
原生安装器和卸载器声明 Windows 长路径支持，与 Python 辅助进程使用同一系统设置。
清理后仍有残留时，提示列出剩余文件或目录名称，卸载详情中列出完整的顶层路径；
文件被占用或目录内有自行添加的文件时，不报告完整清理成功。
在已安装的目录再次运行新版 Setup 即更新程序：先停止实例、替换代码与内置运行环境，
下次启动时由安装器 `update-code` 准备依赖、刷新组件定义并重启，数据库由服务启动时
自己更新（见 `UPGRADE.md`）。
本版卸载器只在当前安装仍持有登记时移除公共快捷方式，保留另一个目录中较新安装的入口。
安装包尚未代码签名，Windows 可能显示未知发布者或信誉提示。
TAU 原生执行器使用独立 Windows Job，浏览器使用受管 Job 与清理回执。
需在该 Windows 实例安装可选 TAU 依赖、配置测试用户、目标与模块 Resident，
并接入本机可运行的 Codex 账号，再由它自己的 Test 工作区启动 Campaign。
原生 CI 的进程/浏览器链路测试不调用模型，不能代替正式 TAU 用例结果；
具体准备步骤见 `management_portal/test_framework/tau_runtime/README.md`。
各 AI Provider、Article 编译、Device 与实际 Resident 对话仍需逐项验证。

## 发布与启动检查

Windows 当前仍为试用版，公开安装包和 README 下载入口已撤下。新测试包通过 Portal
交付，不创建 GitHub Release，也不把安装包写入 Git 文件历史。
更新 `test-build.json` 并发布到 main，或手动触发 Windows preview installer 工作流，
会生成仅保留一天的 Actions 测试文件，再在 Windows 上检查真实 DACL、进程记录、
本机通信、离线初始化、首次管理员注册及 AMA、启动器退出后的服务存活、停止注销后
重启和数据保留。完整服务验收同时覆盖包含和不包含 Lenia、中文及空格路径。
实际 EXE 安装使用 CI 临时创建的标准用户，启动回执记录非提升身份；权限单元检查
仍在 CI 原管理员身份下执行，覆盖两种身份的文件权限行为。
验收不调用外部 AI；原生回执属于实际测试的 EXE SHA，不可套用到另一个包。
仅修改验收脚本时，`test-build.json` 可指定 `reuse_package_run` 复验该次 Actions
的同一 EXE，回执分别记录包源码和测试脚本源码。产品代码有变更时必须移除此字段重建。
CLI 验收独立安装同一 EXE，结束后卸载，不使用桌面验收已经卸载的程序目录。
若仅补跑 CLI，可同时指定 `native_cli_only: true`；工作流先校验被复用 EXE 的摘要、
源码及已通过的原生安装回执，再运行 CLI，并保存所引用的原始验收记录。
静默安装使用 `/S`，不会自动弹出启动窗口；添加 `/NOEXAMPLES` 可不安装 Lenia。
静默安装只展开平台，后续仍由桌面入口完成实例初始化。
新版发布且附件齐全后，仅保留最新 Windows 试用版，自动删除更旧的试用版 Release、
安装附件及对应标签。发布失败或缺件时保留当前可用版本。历史版本下载链接会失效。
`Clean old preview installers` 工作流也可清理现有 Windows/macOS 旧版本，各平台独立保留最新版。

首次安装复用安装器刚完成的就绪检查，直接准备注册入口，不再重复启动服务。
后续桌面启动和安装器使用同一个就绪检查：等待上限约 120 秒，HTTP 单次最长 3 秒，
用户模式服务退出立即反馈；相同 HTTP 应用错误连续出现三次时提前终止，
503 等暂时不可用响应仍允许恢复。保留实例身份、后台服务、登录页和 worker 检查。
密码不再要求至少 12 个字符；仍须非空、不超过 256 个字符，并满足原有字符类型要求。

## 构建

公开示例已缩短架构资料的重复层级及动画文件名，并保持资源 ID、条目名称和内容
引用一致。路径整理应用于所有平台的公开 seed；已安装实例不在此操作范围内。
较长的安装目录和后续依赖仍可能需要 Windows 长路径支持。

程序更新见 `UPGRADE.md`：只换代码、重启服务，没有快照、升级日志或回执。

在 Linux 上安装 NSIS，或在 Windows 安装 NSIS 并将 `makensis` 加入构建 PATH。
从仓库根目录执行：

```sh
python3 code/scripts/windows/build.py
```

输出 `.build/windows/TheFourthInstitute-Setup-x64.exe`、SHA-256 与 `receipt.json`。
构建器只读取 `code/`、公开示例 seed 和指定说明文件，拒绝链接与 Git 元数据；
不读取其他实例。Python 使用 `runtime.json` 固定 URL、大小和 SHA-256，
保留上游许可证。PostgreSQL、NATS、Node.js 使用 `code/config/release-native-dependencies.json`
的固定版本和摘要，构建时下载、校验并检查解压目录；归档随包放入共享依赖缓存，
收据记录每项摘要和大小。用户首次启动无需再次下载这三项。

Windows 准备受管 PostgreSQL 时，为其可执行文件加入进程级 UTF-8 manifest，
保留原有资源和权限设置，并在本地依赖收据记录派生处理。这避免英文系统的 ANSI
代码页破坏中文数据库路径；不改变系统语言或全局代码页。上游归档摘要仍按原值校验。
生成平台声明只影响 Windows 派生工程，公开 seed 原文件保持不变。
编译安装包前，从实际待打包的代码和 seed 压缩包运行 `prepare_install.py --platform windows`，
由正式安装器校验生成工程的入口、数据库摘要及全部文件清单；失败时停止构建。
带案例和不带案例两种准备流程都必须通过此校验。
通过后将准备完成的工程压成 `example-engineering.zip` 和 `base-engineering.zip`，
替代原 seed 归档进入安装包；完整性校验在构建阶段执行。

安装向导使用 [NSIS](https://nsis.sourceforge.io/Docs/)；内置 Python 使用
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)。
首轮手工验收包括安装、管理员注册、两个实例的独立启停、实际 AI 回复、长路径与中文路径、
退出登录后重新启动，以及默认卸载保留数据、显式勾选删除实例两条流程。
反馈时提供错误步骤、Windows 版本和日志；
不要公开账号授权文件、邀请链接或数据库。

## 开发验证

### 连接实际 Windows 机器验收

CI 的普通用户验收用于确认可重复的安装和协议行为；使用者机器上的代理设置、
CLI 授权、终端环境和模型权限需要在那台机器上另外验收。建议新建专用测试实例，
保留它的安装包 SHA、源码提交、Windows 版本和实例目录。先在本机浏览器确认
Portal 的实际地址，例如 `http://127.0.0.1:19400`，并在这台 Windows 上完成
要测试的 Provider 授权。原生 CLI 版本、Portal 受管运行时及其账号目录分别记录；
不把终端里已经登录过视为 Portal 的隔离账号已经完成授权。

由 Windows 主动连接测试服务器的 SSH 反向隧道，只转发该实例的 Web 端口。
测试服务器必须是测试执行环境能够访问的机器；服务端保持 `GatewayPorts no`
并允许该测试 SSH 账号监听指定的回环端口。核实服务器 SSH 主机指纹后，在
Windows PowerShell 中运行（把占位参数和两个端口换成实际值）：

```powershell
ssh.exe -N -T -a -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:19490:127.0.0.1:19400 -i "$env:USERPROFILE\.ssh\portal_test" testuser@test-server
```

测试服务器上的 `http://127.0.0.1:19490` 此时通往 Windows Portal。这里只提供
Web 访问，不提供 Windows Shell；需要读取服务日志时由 Windows 侧收集指定日志。
测试结束按 Ctrl+C 关闭隧道，撤销临时测试访问凭据。
端口转发语义见 [OpenSSH ssh(1)](https://man.openbsd.org/ssh.1)。

网络连通与 Portal 授权分别准备：Windows 实例为测试执行者签发限时、限测试项目的
访问权限，不能把另一个实例的 Resident 身份或令牌套到这里。账号管理验收需要
该测试实例明确授予管理权限；普通用户权限测试另用无管理权限的测试身份。
真实 Provider 的密钥/订阅授权留在 Windows 本机，测试回执只记录账号 ID、版本、
操作、结果和脱敏错误类别。

验收从浏览器实际按钮和网络回执进入，覆盖注册、AMA/Lineage 打开、添加/编辑/
测试/停用/删除账号、运行时模型保存回读、消息发送与流式回复、Stop、刷新恢复、
会话绑定和权限拒绝。每条 CLI 路线再验证进程启动、中文空格目录、失败反馈及
本机已授权模型的最小一轮回复。最后由 Windows 侧重启 Portal/注销再登录，
核对后台服务存活、账号与消息保存，以及 `web.log`、`society.log`、`execution.log`
中的对应测试时间段。提交逐项结果和失败复现，不把模拟模型测试记为真实授权推理。

Windows 安装、启动与卸载用例位于 `code/tests/cases/`，沿用 Portal 的
`code/pytest.ini`、Test Catalog 和测试工作台。没有单独的 Windows 测试框架。
在 `code/` 下运行 `python -m management_portal.test_framework.tools.sync_catalog`
检查目录，用测试工作台的 `operations.deployment` 分类选择相关用例。
命令行定向验证使用同一入口，例如
`python -m pytest tests/cases/test_windows_uninstall.py`。
`scripts/windows/` 只存放 NSIS 构建脚本、固定运行时声明和图标等构建素材；
实例的创建、启停、代码更新与退役由共享安装器 `scripts/tfi_release_install.py`
（服务部分 `scripts/instance_services.py`）实现，桌面启动器只调用它的命令。
