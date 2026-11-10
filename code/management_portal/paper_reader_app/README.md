# 全屏论文阅读组件

Paper Reader 的“全屏阅读”按需加载 React 外壳，覆盖浏览器内容区。同源 iframe 使用 Mozilla PDF.js **6.5.438 完整 Viewer**，直接复用其文字选择浮动高亮按钮、点击标记后的颜色和删除工具、撤销、搜索与缩放。页内 PDF 保持现有公共预览器，不加载 React 或高亮工具。

`viewer_adapter.js` 只衔接 Viewer 生命周期、阅读位置和个人持久化。账号、Paper、PDF fingerprint 和高亮 UUID 保持原有语义，沿用 `/paper/api/highlights`。记录保留归一化矩形，并可带经过服务端校验的 `native` 高亮几何信息，以恢复颜色、旋转、文字或手绘标记。旧矩形记录按原 PDF 页面的固有旋转转换，打开时不写回；实际修改后才保存。扫描 PDF 可阅读及手绘，但文字高亮需要文本层。

个人高亮载入前禁止编辑。编辑操作先由原生 Viewer 显示，每 500ms 检查变化并逐条保存；只有接口成功才更新已保存状态。失败保留当前修改并显示重试入口，停止自动重试；退出会先保存，失败时留在阅读器。退出时交还页码和页内位置，释放 iframe、React 和轮询。高亮工具开启时沿用 PDF.js 的直接高亮行为，关闭工具后选中文字显示浮动按钮。PDF 内嵌注释不属于 Portal 的个人高亮记录；本次没有新增批注、签名、图片编辑的账号存储，因此隐藏这些编辑入口。禁止从 Viewer 拖入或打开另一个文件，以免混淆 Paper 归属。

上游资源位于 `static/vendor/pdfjs-viewer/6.5.438/`，与旧预览器分目录、分 iframe 运行，保留 Apache-2.0 LICENSE。来源为官方发布包：
`https://github.com/mozilla/pdf.js/releases/download/v6.5.438/pdfjs-6.5.438-dist.zip`。
归档 SHA-256：`fcbe9ba21d9911843e3502adc178a7f549a524a4055f887bedc942e5490c6ba1`。
提取时排除 source map 和示例 PDF，其余资源原样保留，无 CDN 运行依赖。外观限制放在 `static/paper_pdfjs_viewer.css`，不改上游代码。适配器使用固定版本 Viewer 的 editor layer 接口；升级时必须复验导入、删除、撤销和保存。发布须同时包含前端 bundle、样式、整个版本资源目录及 `paper_highlights.py`，后端变更需要激活 Web。

构建：在本目录执行 `npm ci --ignore-scripts` 和 `npm run build`。提交源码、lockfile 和生成的 `../static/paper_fullscreen_reader.js`，不提交 node_modules。浏览器运行不依赖第三方 CDN。React 与 React DOM 使用 MIT 许可证，构建产物保留许可证声明，完整许可见 `LICENSE-React.txt`。

定向检查：

```sh
python -m pytest -q tests/cases/test_paper_highlights.py
node tests/cases/javascript/paper_fullscreen_reader.cjs
```

Python 使用 Portal 当前依赖环境；浏览器检查使用已安装的 TAU Playwright、单浏览器及合成 PDF，不连接真实论文数据。
