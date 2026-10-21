# 本地开发与主分支发布

共享仓库的 `main` 是公开与部署版本；同一 Git 仓库的 `dev` 分支在独立
worktree 中开发，只保存在本机。实例的数据库、账号和运行目录不随分支切换。

在 main 仓库根目录执行一次：

```bash
python3 code/scripts/tfi_dev.py init
```

这会在相邻的 `<仓库名>-dev` 目录建立 dev worktree，并把本机路径记录在
Git 私有配置 `tfi-development.json` 中，不把主机路径写入公开实例数据。
本地 pre-push hook 仅允许 `main:main`，防止误推 dev 和试验分支。
本地 hook 是误操作保护，管理员仍可显式改变配置。

在 dev worktree 修改、验证并正常 `git commit`。发布前，用 `sync` 把
main 的新提交合入 dev；有冲突时先在 dev 中解决。main 或 dev 有未提交
改动时命令会停止，保留原改动。

```bash
python3 code/scripts/tfi_dev.py sync
python3 code/scripts/tfi_dev.py publish --message "Describe the verified fix" \
  --push --check python3 -m unittest discover -s code/tests/cases -p test_local_development.py
```

`--check` 后提供本次修改实际需要的测试命令，示例只验证此发布工具。
发布将已提交的 dev 改动 squash 为 main 上的一次提交，再将 main 合回 dev
作为下一轮基础。中间开发提交留在本机。命令输出正式提交和推送结果，
**不会自动重启服务**：代码提交后应按改动选择实例及应用组件，等待活动任务
安全交接后激活并检查健康。数据库和六个账号服务不属于普通代码发布重启范围。

安装器生成的应用环境不携带 PostgreSQL 的私有动态库目录。该目录只用于
PostgreSQL 服务及其原生命令，Portal、Node、PDF 和 TeX 工具使用各自依赖，
避免宿主工具误载数据库附带的旧版 C++ 库。修改这一环境边界后，激活受影响的
应用服务并验证实际 PDF 提取；无需为此重启数据库或 Provider 账号服务。

Linux 应用保留用户服务管理器的 `XDG_RUNTIME_DIR` 和
`DBUS_SESSION_BUS_ADDRESS`，使 RunScript 能启动独立任务；HOME 和配置仍归属
实例。WALL 在每个独立实例内处理该实例的任务，周期工作由本站的调度开关控制，
不再以旧的 `dev` / `release` 名称禁用派发或取消收尾。

运行 Dev UI 或执行服务时，必须另建测试实例并绑定 dev 的 `code/`，
使用独立数据库、资源副本、端口和消费者。不能让两套执行服务同时消费
dirtybase_instance 的正式任务。单元测试和静态检查不需要启动 Dev 服务。

旧的 `PublishRelease` / `publish_portal_release.py` 针对旧主机站双版本部署。
共享仓库采用本规范和 `tfi_dev.py`，旧发布器不能用于这类实例。
