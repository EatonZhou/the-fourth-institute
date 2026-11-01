# 安装

Windows 11 x64 用户双击 `TheFourthInstitute-Setup-x64.exe` 即可安装；完成后自动从示例实例创建默认实例、启动服务并打开 Portal 网页。详见 [Windows 预览安装说明](code/scripts/windows/README.md)，包括联网依赖、长路径授权、账号配置、日志位置与当前验证范围。

在 Linux 上从源码创建实例：

```bash
python3 prepare_install.py
python3 code/scripts/tfi_release_install.py create \
  --engineering .build/example_instance/engineering/manifest.json --name my_portal
python3 code/scripts/tfi_release_install.py first-admin --instance my_portal --username yourname
```

- 应用源码位于 `code/`；示例实例的发布内容位于 `instance/example_instance/seed/`，代码目录中不放任何实例的数据。
- `prepare_install.py` 校验发布内容并从当前代码组装安装工程，不导入数据库。
- 实例根目录默认在代码目录之外的用户数据目录（Linux `~/.local/share/tfi/instances/<名称>`），登记在 `~/.config/tfi/instances.json`。
- 运行环境与 PostgreSQL、NATS 等程序准备在代码旁的 `dependencies/`，由使用这份代码的实例共用。

启动、停止、修改端口、代码更新和退役等完整说明见 [安装器说明](code/INSTALL.md)。
