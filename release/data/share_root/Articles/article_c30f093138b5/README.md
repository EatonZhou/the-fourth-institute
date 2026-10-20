# 智能软件开发的组织与交互框架

面向能力放大与持续演化的中文概论。正文按已确认的 11 章大纲组织，结合 Portal 的实践动机，提出组织、交付、验证与智能交互框架。案例是用于解释机制的构造示例，效果假设留待后续研究验证。

完成版共 48 页（含题名、摘要、目录与参考文献），11 章，约 2.7 万汉字，16 张示意图，16 条参考文献。经过三轮内容、图文一致性和排版检查；修订记录见 [editorial/progress.md](editorial/progress.md)。

- Portal Article：`article_c30f093138b5`
- 项目：AI员工
- 主文件：`main.tex`
- 阅读版：[article.pdf](article.pdf)
- 可编辑图稿：[figures-editable.pptx](figures-editable.pptx)，每张图占一页，形状、文字和连线均为原生 PPT 对象，共 509 个对象，无整图位图嵌入。
- 源文件包：[source-package.zip](source-package.zip)，包含 LaTeX 正文、参考文献、矢量图、PPT、构建脚本和检查记录。阅读版 PDF 单独交付。
- 图的矢量导出：`figures/*.pdf`、`figures/*.svg`；预览：`figures/*.png`。
- 生成器：`tools/make_figures.py`。PPT 与矢量图来自相同场景描述，修改图稿后应同步更新论文所引用的 PDF。
- 编译：在本目录运行 `python3 tools/build.py`；重建图稿再编译可运行 `python3 tools/build.py --figures`。
- 检查：`python3 tools/check_artifacts.py`；打包：`python3 tools/package.py`。检查生成的页面预览位于 `build/`，不收入源文件包。

环境依赖：XeLaTeX、ctex、Noto Serif/Sans CJK 字体、python-pptx、CairoSVG、Pillow、Poppler。参考文献保存在 `references.tex`，无需 BibTeX/Biber。构建日志与临时文件位于 `build/`。

文章属于独立 Article 资源，未同时登记为 Repo。资料与核对记录位于 `editorial/`，不进入阅读版正文。
