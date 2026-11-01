# Windows x64 preview — 2026-10-28

完整安装包统一包含 Provider 实例服务生命周期、Claude 运行环境健康检查、Codex Windows 登录程序解析，以及账号添加、自动默认模型、Model API 入口和准备进度改进。包含 Kimi Windows 运行支持及其依赖文件。

下载 EXE、同目录 SHA256 与 receipt.json。收据绑定固定源码提交、构建时间、公开 seed 和依赖摘要，以及同一 EXE 的原生验收范围；以收据中的实测状态为准。账号登录授权和真实模型对话单独记录，不以环境 ready 代表认证成功。

此版本替代 2026-10-27 b00a7c0 私有试用包。实例放在程序目录之外（`%LOCALAPPDATA%\TFI\instances`）；以后的新版 Setup 选择同一安装目录即只更新程序，实例数据保留。

安装包尚未签名。Windows Server 2025 虚拟机验收不能代替 Windows 10/11 全部环境覆盖；详见本产物收据。
