# Lenia 最终保留工程

这是一套冻结的核心代码与36段精选交互动画，不包含中间版本、运行缓存和完整历史轨迹。

- `code/`：方法、对照方法、配置、必要初态和随机种子；安装与运行说明见该目录。
- `projection/public/`：可直接由静态 HTTP 服务托管的动画墙和学习演示。
- `projection/source/`：播放器与渲染器可编辑源码。
- `projection/`：执行 `npm run build` 生成 Portal 投影产物。
- `retention/`：保留文件的校验清单。清单路径为本机规范位置；原始实验路径仅作历史来源标识。

论文及全部图表数据保存在原 article 资源 `article_0565c33e0c03`：
`${share_root}/Projects/元胞自动机研究/src/articles/experience_memory`。
该目录内保留 LaTeX、参考文献、PDF、18份可编辑 PPTX、配图及 `data/` 源数据。

本工程只含一套最终代码，不包含 Git 历史的中间研究版本。现有结果的适用范围和失败条件以冻结论文为准。
