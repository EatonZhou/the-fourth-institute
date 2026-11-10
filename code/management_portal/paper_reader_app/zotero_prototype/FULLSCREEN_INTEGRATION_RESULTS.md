# 正式全屏宿主接入验收（未发布）

本报告覆盖并取代前报告中“前端未接/静态资源未挂”的状态。源码现有全屏按钮调用链为 static/paper_reader.js → paper_fullscreen_reader.js 的 openReader → zotero_adapter → 原版固定 Zotero iframe → production_routes → production_service → PG owner。普通嵌入式阅读仍走现有 PDF.js；没有发布、真实论文复制、全库维护或翻译文件修改。

## 可发布文件

- 修改 `management_portal/static/paper_reader.js`：仅现有全屏调用传入当前阅读 session.account；保留近期阅读 acknowledge 的 generation/instance/source/account 检查。
- 修改 `management_portal/paper_reader_app/reader.jsx`，新增 `zotero_adapter.js`：全屏使用 Zotero；等待首次嵌套 PDF 页面 canvas 有实际尺寸后才发 ready，携带权威 attachment_version（沿旧回调 fingerprint 字段传递稳定版本标识，不冒称 PDF.js 指纹）。关闭先经 host blur+公开 flush；保存失败返回 false，恢复 dialog 交互；重试保持原文档与修改。账号切换由每次正式 HTTP 请求核验 expected principal 拒绝。关闭加载中的文档等待本次初始化，加载失败且未建 Reader 可退出。
- 修改 prototype `http_transport.js`：向宿主暴露已认证版本描述符；无新的模型/翻译调用。
- 修改 `management_portal/http_paper_reader.py`：新增 protected GET prefix `/paper/reader-assets/`，只从固定 upstream 构建目录提供文件，限制 resolve 后路径，不能读取论文或任意路径。四条已有 API 方法及认证/CSRF/principal 限制不变。
- 生成 `management_portal/static/paper_fullscreen_reader.js`（225.0kb），`npm run build` 退出0。运行时依赖既有固定 upstream 构建目录全体文件；许可证沿原版 AGPL-3.0，React 许可文件继续保留。本轮不改 upstream。
- 测试 `tests/cases/test_paper_reader_fullscreen.py`、`tests/cases/javascript/zotero_reader_fullscreen.cjs`；更新 `test_paper_reader_http.py` 的路由装配断言，现为4条dual-auth API + 1条protected静态资源prefix。
- 继续需要前切片的 http_paper.py / paper_store.py / directory_maintenance.py 与 Reader service、annotations、attachments，以及 upstream/save-contract.patch 和可重现构建步骤；无共享 schema 注册变化，沿 DomainDatabase 懒建。回退仅切回旧全屏 bundle/入口，保留附件和 PG 数据；不能清空回执或版本。

## 新路径证据

临时 PG/临时注册 Paper/有效合成 PDF/临时用户，使用正式 PortalAccessStore、正式会话签名/校验与 http_auth cookie 解析、page_api_auth 主体解析。不替换认证结果，不伪造线上用户。测试将无关个人 AMA 自动配置替为空操作，资源 ACL 替为允许（并未测试复杂权限矩阵）；HTTP server 为隔离 BaseHTTPRequestHandler，使用实际 http_paper.routes 路由集合。页面直接调用现有 bundle 的 openReader，未复制宿主实现。

Chromium 和 Firefox 各验证：实际 PDF 渲染后 ready（一次、含稳定版本）、原生便签焦点编辑后关闭保存、注入503后 dialog/交互保留、解除故障后关闭及重开读取相同便签、外站 Origin POST 403、换成另一有效正式 session cookie 后旧实例保存被拒且保留 dialog。然后真实 PaperStore.delete_paper 注销时注入元数据清理失败，新 service 恢复持久意图至done，仅消费这次删除新建的临时 cleanup_id，确认正式队列实际移除临时 bundle。没有调用 maintenance.run 或不指定 ID 的队列扫描。

工程 venv 执行：

```
python -m pytest -q tests/cases/test_paper_reader_fullscreen.py tests/cases/test_paper_reader_http.py -k 'formal_fullscreen or assembly' --tb=short
```

最终退出0：**3 passed, 3 deselected in 7.49s**。新浏览器路径首轮2 passed in7.02s后，仅因补入删除恢复/物理清理断言而重跑该路径；旧原型用例全部 deselect。开发中修正无效PDF占位fixture、测试占用window.closed只读属性，以及重开后应先新建便签再编辑的选择器问题。没有声称修复历史Firefox卡死。

## 评审边界

全屏新宿主、正式认证HTTP、临时PG保存重开、账号隔离与对象物理删除已形成隔离闭环，可供统一发布评审。此次验证到原正式 onReady 信号；既有页面的近期阅读 acknowledge/post去重逻辑保持原样，未在完整Portal页面中重新测其列表排序。没有验证生产代理/CSP/资源压缩或实际发布激活；发布前仍需同一构建的静态资源完整性与页面烟测。旧PDF.js阅读位置锚点无法无损转换到新Reader，当前不宣称跨引擎位置迁移。真实附件容量/长期保留策略仍按既定接入决策管理，不自动回收。翻译线继续独立，未上线底层Execution实验。
