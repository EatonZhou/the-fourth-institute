# 全屏论文阅读组件

Paper Reader 的“全屏阅读”按需加载 React 组件，覆盖浏览器内容区。页内 PDF 继续使用公共 PDF.js 预览器，全屏组件也复用该渲染器。适应宽度、缩放、我的高亮和删除操作均在全屏组件内。

高亮采用 PDF 页内归一化矩形，通过当前认证账号调用 `/paper/api/highlights` 保存。PDF.js fingerprint 区分文件版本；无文本层的扫描 PDF 不支持文字选择高亮。失败不会显示为已保存。组件退出时释放 React、PDF 渲染和 resize observer。

构建：在本目录执行 `npm ci --ignore-scripts` 和 `npm run build`。提交源码、lockfile 和生成的 `../static/paper_fullscreen_reader.js`，不提交 node_modules。浏览器运行不依赖第三方 CDN。React 与 React DOM 使用 MIT 许可证，构建产物保留许可证声明，完整许可见 `LICENSE-React.txt`。

定向检查：

```sh
python -m pytest -q tests/cases/test_paper_highlights.py
node tests/cases/javascript/paper_fullscreen_reader.cjs
```

Python 使用 Portal 当前依赖环境；浏览器检查使用已安装的 TAU Playwright、单浏览器及合成 PDF，不连接真实论文数据。
