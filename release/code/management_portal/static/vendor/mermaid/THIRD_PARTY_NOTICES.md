# Mermaid 内嵌依赖声明

本目录是 [mermaid 11.17.2 官方 npm 发布包](https://registry.npmjs.org/mermaid/-/mermaid-11.17.2.tgz) 的浏览器 ESM 入口与 chunks。
[LICENSE](LICENSE) 是 Mermaid 自身的 MIT 许可证。打包后的 chunks 同时包含第三方依赖；这些依赖的许可证分别适用。

依赖的固定版本、来源、原始版权与完整许可文本统一保存在仓库根
[THIRD_PARTY_NOTICES.md](../../../../THIRD_PARTY_NOTICES.md#components-bundled-with-mermaid)。
其中保留 MIT、ISC、BSD-3-Clause、Apache-2.0 等原始声明；DOMPurify 保留原双许可文本。
分发本目录时应同时携带根清单及其完整原文。升级入口或 chunks 时须重新核对嵌入依赖，不能只更新 Mermaid 的 MIT 文本。
