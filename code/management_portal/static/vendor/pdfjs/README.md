# PDF.js 静态资源

`pdf.min.js`、`pdf.worker.min.js` 和 `cmaps/` 来自同一版本的官方 npm 包 `pdfjs-dist@3.11.174`。包的完整性标识为：

```text
sha512-TdTZPf1trZ8/UFu5Cx/GXB7GZM30LT+wWUNfsi6Bq8ePLnb+woNKtDymI2mxZYBpMbonNFqKmiz684DIfnd8dA==
```

`cmaps/` 保留发行包中的全部压缩字符映射及原始许可证。中文等 CID 字体可以把字符映射交给 PDF 阅读器提供；字体已嵌入 PDF 时也可能需要这些资源。

共享加载器 `static/portal_pdf_preview.js` 统一设置同源 `cMapUrl` 和 `cMapPacked: true`。升级 PDF.js 时同步更新主库、Worker 和 CMap，避免混用版本或依赖外部 CDN。

`tests/cases/ui/fixtures/pdf-cjk.pdf` 由相邻的 `pdf-cjk.tex` 经 XeLaTeX 生成，包含需要 Adobe-GB1 字符映射的 Fandol 常规和粗体中文。`portal_preview_zoom.spec.js` 使用真实 PDF.js Worker 检查中文文本层、画布字形、映射请求和字体加载错误，避免仅用模拟 PDF 掩盖字体回归。
