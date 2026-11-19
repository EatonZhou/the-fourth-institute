# Linux 从零安装 Release 示例

本例在 Linux x86_64 主机上，从固定 Git 提交导出发行源码，用正式安装器新建一个 Portal 示例。每次使用不存在的短目录、新数据库、新数据根、随机实例名和独占服务/端口。它验证全新安装；宿主 Linux、systemd 用户管理器和浏览器测试工具可以复用，不声称新建了虚拟机。

前提：Python 3.12+、Git、Node、可用的 `systemctl --user`、能够下载发行依赖的网络，以及单独安装的 Playwright Chromium。禁止继承现有 Portal 的数据库、账号、API 凭据、浏览器 profile 或 `PORTAL_*` 环境。安装器只使用独立实例；当前用户的实例登记文件和 systemd 用户目录中会新增并随后移除本例唯一项。

## 一次完整执行

```bash
python3.12 tests/cases/fixtures/run_linux_release_tau.py \
  --base /tmp/tfi-linux-UNIQUE \
  --release /path/to/release/git/repository \
  --revision EXACT_RELEASE_COMMIT \
  --playwright-module /path/to/test-tools/node_modules/playwright
```

`--base` 必须不存在且路径较短，以满足 PostgreSQL Unix socket 长度限制。`--revision` 在开始时解析成完整提交；文件来自 `git archive`，不复制调用者工作树。`prepare_install.py` 产生正式示例发行输入，`tfi_release_install.py` 完成 create、status、first-admin、stop、start、retire。浏览器两次使用新 context，实际执行首次管理员注册、登录、打开 seed Design、读取正文、收藏；重启后重新登录，核对收藏仍存在。

退出码为 0 需要业务步骤和清理同时通过。`finally` 走正式 retire；随后检查本例服务定义消失、PID 为零、端口关闭、实例登记及数据根消失。若清理失败，`case-result.json` 单独记录，不强杀、不清其他实例、不把失败当成功。

## Campaign 的分段 fixture 与浏览器 TAU

`linux_release_deployment.py` 提供 prepare/create/status/first-admin/stop/start/retire，以及 verify-stopped/verify-retired。传入同一个 `--base`；重复某步骤时用新的 `--label` 保存原失败，不能覆盖旧日志。

Campaign 先声明本例独占短目录和固定发行提交，然后用该 helper 建实例。只有此实例可成为本用例浏览器目标；从 `instance/instance.json` 的 `network.entry_port` 取得 localhost URL，不能沿用 Campaign 原站点 URL 或登录态。first-admin 返回独立实例内的一次性邀请文件路径。TAU 从该文件取得邀请，在新 context 内完成真实注册/登录和资源阅读/收藏，随后检查正式停启后的持久化。测试用户为本例合成的 `linux_tau_admin`，不调用模型、不配置真实服务账号。

自动浏览器 helper `javascript/linux_release_deployment.browser.cjs` 是上述真实路径的确定性验收入口，不使用 HTTP mock；它的结果不能冒充已运行的 Campaign TAU。唯一正式登记在 `tests/cases/tau_cases.json`：ID `test_05e761272cec`，module `operations.deployment`，key `tau.operations.linux-release-example-clean-deployment`，entrypoint `tau:operations-linux-release-example-clean-deployment`。原草稿登记片段已撤除，不另建第二个入口。若 Campaign 不支持独立目标或安装权限，应明确该前提缺失，不在现有站点降级执行。

## 证据与保密

`fixture.json` 固定提交、archive SHA、机器与隔离方式；`ownership.json` 记录服务/端口；`evidence/*/command.json` 保存真实 argv、cwd、退出码、耗时。浏览器 JSON 和注册后截图、停机/退役检查均在 `evidence`；`case-result.json` 给总结果与入口源码 pins。

邀请、密码和数据库凭据只在本例 mode-0600 文件及实例 secrets 中；退役后删除。冻结只收 `fixture.json`、`ownership.json`、`case-result.json`、`evidence`、明确的源码差异，不打包实例数据、secret 文件、浏览器凭据、缓存或整个 `/tmp`。停止 fixture 进程前应等待其 finally 清理；若进程意外终止，按该 base 的正式 `retire` 补收尾，再运行 `verify-retired`。

不要在已组装发行包的源码树中放回归测试覆盖；正式安装器会正确拒绝代码 inventory 不匹配。回归测试应在另一个源码导出目录运行。候选补丁必须通过 prepare 的显式 `--candidate-patch` 记录，不能绕过发行校验。
