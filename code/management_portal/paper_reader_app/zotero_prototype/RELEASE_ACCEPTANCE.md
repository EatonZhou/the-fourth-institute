# Reader 发布验收

已发布并激活功能提交 `bb37509388e9079c1ba2c24442c615e0e9f49a30`，原 Release 基线 `bcb0fb7ea27971d9ba59be3e420c609b0eda0013`。激活回执 `/var/lib/tfi/instances/dirtybase_instance/runtime/tfi-pair-publish-4c8d54fc4afb4bc989b6a6a6c8f3e0df-result.json`：ok=true，resource/web/dev-web 激活，release/dev/public 健康均true。241个bundle/上游资源文件在Release与Dev逐字节SHA256一致。

发布后无凭证访问真实Release 28400的 /paper、bundle、CSS、pdf.worker.mjs、en-US/viewer.ftl 均302到/login，响应无额外CSP；未把登录页200冒称资源200。没有借用人类cookie；已登录Release资源200与最终代理策略须按下方手动步骤检查。隔离真实签名cookie的浏览器已成功加载上游iframe/worker/locale/CSS并实际渲染，HTTP owner源码未对Paper额外设置CSP。Release磁盘闭包和匿名保护验证不替代真实人类会话验收。

补强全屏open端点503失败不记阅读后，定向命令 `python -m pytest -q tests/cases/test_paper_reader_fullscreen.py -k page_recent --tb=short`：2 passed, 2 deselected in5.94s；没有重跑保存/账号/删除旧用例。该补强仅更新测试与验收文档，后续按文件无激活发布。回退方案为以新发布恢复基线的reader.jsx、static/paper_reader.js、static/paper_fullscreen_reader.js，保留服务端数据和已登记版本；不得全树reset或回滚他人提交。本轮未实际回退。

正式全屏入口、原版固定 Zotero、认证 HTTP 与 PG owner 一同交付；页内预览不变。规范更新已有 decision_528bf9985705 的“全屏阅读与个人批注”章节，SPEC 已索引该 ID，无新增重复 Design。

新增页面联动测试使用实际 paper_reader.js/paper_personal.js、全屏 bundle、正式认证 cookie/路由和临时 PG：Chromium/Firefox 2 passed, 2 deselected in 5.40s。覆盖失败预览不记录、全屏真实渲染确认、嵌入式重复 ready 去重、退出重开确认更新、个人列表两条记录的排序与去重。页面外壳为最小隔离 DOM，嵌入式预览 ready 可控；不宣称完整生产页面人工验收。复用此前保存/账号/删除/临时对象物理删除证据，不重复跑旧原型。

资源闭包为 upstream 固定构建目录全部文件，包含 pdf worker、locale、CSS、字体、许可证；保护路由禁止匿名访问并限制路径。当前 Paper 文档 HTTP owner 未配置额外 CSP，遵循实际部署响应；不人为加入会阻断同源 iframe/worker 的策略。翻译缓存/执行/workflow、相关实验报告和未交付文件不在发布清单。回退重发旧全屏入口/bundle，保留 PG 数据、版本文件及 operation 回执。

真实人类会话手动步骤：正常登录 Release，选合成测试论文进入页内预览，打开全屏；确认近期阅读仅一条；编辑便签退出重开；退出后再打开确认时间更新。切换账号后旧窗口保存应拒绝。不得借用或伪造真实用户 cookie。本自动验收只创建隔离临时账号与合成对象。
