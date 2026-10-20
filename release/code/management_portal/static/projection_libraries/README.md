# 投影登记库

`manifest.json` 是随 Portal 代码审查的固定资产清单。每个文件记录相对路径、SHA-256、字节数和内容类型；第三方条目另记录 npm 官方元数据地址、发布包完整性值、发布包 SHA-256、原仓库与许可证文件。运行时只读取本地资产。

| 库 | 固定版本 | 浏览器入口 | 许可 |
| --- | --- | --- | --- |
| D3 | 7.9.0 | `d3/7.9.0/d3.min.js` | ISC |
| dagre | 1.1.5 | `dagre/1.1.5/dagre.min.js` | MIT，附 graphlib 2.2.4 许可 |
| Cytoscape | 3.33.1 | `cytoscape/3.33.1/cytoscape.min.js` | MIT |
| marked | 18.1.1 | `marked/18.1.1/marked.umd.js` | MIT |
| DOMPurify | 3.4.17 | `dompurify/3.4.17/purify.min.js` | MPL-2.0 或 Apache-2.0 |
| Portal graph | 1.0.0 | `portal-graph/1.0.0/geometry.js` | Portal 仓库自身条款 |

`workbench_projections.libraries.registry()` 返回按库名索引的公开清单；`read_library(path)` 只读取清单中的规范相对路径，拒绝符号链接并校验内容哈希。HTTP 宿主另行鉴权工作台及修订，再调用这一接口。图标继续由 `PortalUIPrimitives.ICONS` 与 `ICON_ALIASES` 唯一维护，宿主通过 SDK 提供。

第三方入口均可由宿主读取后注入。marked、DOMPurify 和 Cytoscape 同时保留可打包的 ES module；D3 和 dagre 保留完整浏览器源码。应用也可从固定的 npm 锁文件打包依赖。marked 只解析 Markdown，插入页面前仍须调用 DOMPurify 并遵循应用的链接、图片与 HTML 允许规则。

## 共用几何

`portal-graph/1.0.0/geometry.js` 同时支持浏览器全局 `PortalGraphGeometry` 与 CommonJS 打包。Lineage 的 `portal_hatg_loader.js` 在图渲染器前加载它；投影宿主加载同一登记资产。模块不读取 DOM、网络、用户身份或业务数据。

Lineage 宿主通过精确静态路径 `/static/projection_libraries/portal-graph/1.0.0/geometry.js` 读取该文件；路由登记与嵌套路径解析共用 `http_public_pages.PUBLIC_SHARED_STATIC_ASSETS` 映射。此宿主依赖没有第二份文件，也不开放登记库目录的通用静态前缀；应用包仍通过鉴权的库接口与 SDK 加载登记资产。

坐标采用 Lineage 的约定：世界横、纵方向各 100 单位，对应 `worldSize.width/height` 像素。坐标允许超出 0–100；平移缩放使用 `{x, y, k}`。

- `screenToWorld(point, transform, worldSize, rect)` / `worldToScreen(...)` 转换客户端坐标，`rect` 为画布的 `{left, top}`。
- `viewCenter(transform, viewport, worldSize)`、`visibleWorldBounds(transform, viewport, worldSize, insets)` 计算当前镜头。
- `transformForCenter(center, scale, viewport, worldSize, scaleExtent)` 返回纯 `{x, y, k}`，由调用方接入自己的缩放控制器。
- `viewportInsets(configured, viewport)` 保留遮挡范围，同时为画布留下至少一像素。
- `bezierGeometry(points, worldSize, offset)`、`cubicPoint(segment, progress)`、`sampledRoute(segments)`、`routeLength(segments)`、`routePath(segments)` 计算连线与采样。曲线字段为 `x1/y1/c1x/c1y/c2x/c2y/x2/y2`；采样用于 Lineage 原有避障策略。
- `pointInBox(point, center, half)`、`boxIntersectsRect(center, half, rect)` 提供包含边界的命中判断；`half` 是半宽半高，框选 `rect` 使用 `left/top/right/bottom`。

Lineage 保留节点语义、端口选择、避障与交互状态，通用模块唯一维护坐标和几何计算。

## 更新资产

维护者在审查固定版本后修改 `sync.py` 的 `PACKAGES`，运行：

```sh
python3 management_portal/static/projection_libraries/sync.py
```

脚本优先复用 npm 缓存，校验官方发布包的完整性，再生成 vendor 文件与清单；不执行依赖安装脚本。该脚本不属于应用包构建过程，运行时与隔离构建器均不调用它。第三方版本升级使用新的版本目录，并由应用修订明确选用；已发布的版本目录不原地替换。

定向检查：`pytest tests/cases/test_projection_libraries.py`。其中 Node 几何契约检查也可直接运行 `node --test tests/cases/fixtures/projection_geometry.test.cjs`。
