# Reader 发布验收

正式全屏入口、原版固定 Zotero、认证 HTTP 与 PG owner 一同交付；页内预览不变。规范更新已有 decision_528bf9985705 的“全屏阅读与个人批注”章节，SPEC 已索引该 ID，无新增重复 Design。

新增页面联动测试使用实际 paper_reader.js/paper_personal.js、全屏 bundle、正式认证 cookie/路由和临时 PG：Chromium/Firefox 2 passed, 2 deselected in 5.40s。覆盖失败预览不记录、全屏真实渲染确认、嵌入式重复 ready 去重、退出重开确认更新、个人列表两条记录的排序与去重。页面外壳为最小隔离 DOM，嵌入式预览 ready 可控；不宣称完整生产页面人工验收。复用此前保存/账号/删除/临时对象物理删除证据，不重复跑旧原型。

资源闭包为 upstream 固定构建目录全部文件，包含 pdf worker、locale、CSS、字体、许可证；保护路由禁止匿名访问并限制路径。当前 Paper 文档 HTTP owner 未配置额外 CSP，遵循实际部署响应；不人为加入会阻断同源 iframe/worker 的策略。翻译缓存/执行/workflow、相关实验报告和未交付文件不在发布清单。回退重发旧全屏入口/bundle，保留 PG 数据、版本文件及 operation 回执。

真实人类会话手动步骤：正常登录 Release，选合成测试论文进入页内预览，打开全屏；确认近期阅读仅一条；编辑便签退出重开；退出后再打开确认时间更新。切换账号后旧窗口保存应拒绝。不得借用或伪造真实用户 cookie。本自动验收只创建隔离临时账号与合成对象。
