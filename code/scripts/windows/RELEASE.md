# Windows 11 x64 · 试用版 / Preview

Windows 版目前仍是试用版，完整 Windows 真机流程尚待进一步验证。
下载 `TheFourthInstitute-Setup-x64.exe`，安装到新的空目录。
安装开始时可以选择是否包含 Lenia 预置案例。

修复启动时读取 `nats.process.json` 等进程记录的 Windows 文件共享冲突：
读取允许共享删除，覆盖写入等待短暂读取完成；短暂占用进行有界重试，持续拒绝访问仍明确失败。
发布前新增 Windows 原生并发读写、替换和删除回归。

安装欢迎页精简为两段短文案，并随界面语言显示中文或英文。
案例选择和卸载选项在对应页面说明。

本版将案例工程的准备和完整性校验提前到打包时完成。首次安装直接解压所选工程，
省去 seed 中间副本和工程组装；取消安装阶段的全量文件 SHA 校验及共享代码比对。
解压与复制显示已处理文件数、百分比和大小。路径边界与解压器自带损坏检测保留。

没有 AI 账号时，AI Activity & Accounts 页提供“新增 AI 账号”入口，直接打开现有账号登记对话框。

本次更新取消密码至少 12 个字符的限制，保留字符类型要求；首次安装完成后直接打开
注册或登录入口，不再重复启动和检查服务。启动检查的等待上限调整为约 2 分钟；
服务退出或同一 HTTP 应用错误连续出现时更早显示具体原因。

Python、PostgreSQL、NATS 和 Node.js 已内置，首次准备核心 Python 包仍需联网。
TeX、浏览器自动化和测试工具按需自行安装。安装包尚未签名，尚不支持覆盖旧版升级。
已有实例请先备份，不能把重新运行 Setup 当作数据迁移。

Windows remains a preview/trial release. Full validation on physical Windows machines
is pending. Use a new, empty installation directory and choose whether to include
the Lenia example. Python and native services are bundled; first use still requires
internet access for core Python packages. Optional tools are not installed automatically.
This unsigned installer does not provide an in-place upgrade of an existing instance.

Process-record readers allow shared deletion on Windows; atomic replacement retries
while brief reads finish. Persistent access denial never counts as an absent service.
Native Windows concurrent read/write, replacement and removal checks gate publication.

The welcome page now uses two short paragraphs in the interface language.
Example selection and uninstall options are explained on their own pages.

Case engineering bundles are now assembled and verified during packaging. Installation
extracts the selected bundle directly, without the seed intermediate copy, local assembly,
full file SHA scans or duplicate shared-code comparisons. Extraction and instance copying
show file counts, percentage and bytes processed. Path boundaries and ZIP corruption
detection remain enforced.

When no AI accounts are configured, AI Activity & Accounts now offers an Add AI account
button that opens the existing account registration dialog.

This update removes the 12-character password minimum, retains character-type rules,
and avoids repeating service startup and readiness checks after initial installation.
Readiness retries are bounded to about two minutes, with earlier diagnostics for exited
services and repeated HTTP application errors. The SHA-256 file and `receipt.json`
identify the installer and the exact source commit used to build it.
