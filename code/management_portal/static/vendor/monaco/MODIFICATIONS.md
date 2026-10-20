# Monaco 分发来源与本地修改

本目录来自 [monaco-editor 0.56.0 官方 npm 发布包](https://registry.npmjs.org/monaco-editor/-/monaco-editor-0.56.0.tgz)。
Monaco 自身许可见 [LICENSE](LICENSE)；原包的完整第三方声明见 [ThirdPartyNotices.txt](ThirdPartyNotices.txt)，按原文保留。

以下文件包含 Portal 的本地修改，不能当作未改动的上游字节：

- `vs/assets/ts.worker-BWKtMYOk.js`
- `vs/language/typescript/ts.worker.js`

Git 提交 `6cc29157f1863aae5e733ffb92d667514474c850` 从上述 TypeScript worker 的内置 Node 模块名称集合中删除了 `node:sqlite` 项。其余文件内容与该提交前版本一致；现有版权和许可文本保持原样。
此说明记录已存在的修改，本次许可整理只在两个 worker 文件顶部增加上述本地修改的显著声明，代码体保持原样。上述修改不改变上游许可。

`vs/loader.js` 的历史头部年份/版本文字来自官方 0.56.0 包，不能仅据该头部推断本目录版本。
完整分发清单见仓库根 [THIRD_PARTY_NOTICES.md](../../../../THIRD_PARTY_NOTICES.md)。
