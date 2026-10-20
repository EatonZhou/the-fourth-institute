---
kb_id: decision_dca903d71575
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 11
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T01:56:46Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 9
metaness: 1
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/portal_image_editor.js
  - ${code_root}/management_portal/static/portal_image_editor.css
  - ${code_root}/management_portal/society_service/workspace_contracts.py
  - ${code_root}/management_portal/society_service/workspace_domain.py
  - ${code_root}/management_portal/static/markdown.js
  - ${code_root}/management_portal/static/portal_components.css
  related:
  - decision_605810a1292b
  - decision_f560803947c6
  - decision_61e50306cc79
  - decision_741b04821171
  supersedes: []
  derived_from: []
---
# Portal 内容图片的插入、引用、查看与编辑组件

Portal 的聊天、公告、知识正文和其他富文本区域都可能包含图片。用户需要在写作时把图片放到具体文字位置，看到上传进度，重新打开已有内容继续编辑，并在阅读时放大或标注图片。共享内容图片组件为这些页面提供同一套交互，使图片不会在“正文位置”和“附件列表”之间失去对应关系。

本 Design 负责图片进入内容、正文占位、附件状态、草稿恢复、阅读查看和全屏标注。领域上传与存储服务通过公开契约声明允许的格式与数量、上传目标、权限、永久引用和保存冲突。页面选择业务对象并接入这份契约；同一种附件不因页面位置而拥有不同的格式或发送规则。普通文件附件及通用 Preview 由 `decision_61e50306cc79` 负责。

## 用户看到的图片对象

- **正文图片引用**是正文中的一个不可直接改写对象。它占据明确文字位置，可以随文本换行和拖动排序。
- **原位缩略图**让用户在上传期间也能看到图片放在什么位置。
- **附件托盘**是正文外的状态列表，显示缩略图、名称、路径、进度、失败、重试和移除。
- **全屏查看器**是点开图片后覆盖页面的 lightbox。只读时提供查看和关闭。
- **图片编辑器**是在 lightbox 中增加标注、撤销、清空、复制和保存的模式。

正文引用和附件托盘指向同一个图片附件项。删除任一入口都会同步另一处，拖动正文引用只改变内容顺序。

## 设计点

### 编辑期间的图片身份

- **图片附件项**是编辑器内的一份状态，包含稳定本地 token、文件名、MIME 类型、大小、上传状态、错误、持久地址和预览地址。它可以先代表尚未上传完成的本地文件，随后在同一身份上取得服务端地址。

- 草稿把正文位置序列化为 `[[portal-image:<token>]]`。这个 token 只在当前编辑模型中定位附件；提交时由业务 adapter 转成永久表示。Notice Board 使用 Markdown 图片地址；Resident Chat 把上传 ID 放进消息的原生媒体内容部分，读取会话时再还原为可预览的图片引用。

- 本地 object URL 只用于上传前预览，不能进入最终正文。上传成功、移除附件、清空草稿或销毁组件时要释放它。

### 图片怎样进入内容

- 用户可以从当前光标位置选择、粘贴或拖入图片。多个图片按用户给出的顺序创建附件项和正文引用。
- 调用方可以限制单条内容的图片数量；共享组件不建立跨业务统一上限。
- 本地 object URL 立即显示缩略图，用户可以在上传期间继续编辑其他文字。
- 上传中时业务提交按钮不可用。
- 上传 adapter 必须根据实际传输字节报告进度；进度条不能只在请求开始与结束时从 0 跳到 100。
- 服务端持久化前解码图片，以解码得到的 PNG、JPEG、WebP 或 GIF 格式为准，并同时执行字节上限和像素总数上限。客户端文件名、扩展名与 MIME 声明只作为提示，不能代替内容校验。
- 上传成功后，同一附件项取得持久地址，释放本地 object URL，并保持正文位置。
- 上传失败时，对应引用与托盘显示具体错误、重试和移除。失败或已移除图片不进入最终内容。
- 尚未提交的已上传附件被用户从托盘、正文引用或撤销栈移除时，业务 adapter 同步删除服务端临时文件；若移除发生在上传途中，上传完成后立即删除刚生成的临时文件。提交成功后的编辑器清空只释放本地预览，保留消息已经引用的服务端文件。
- 用户拖动正文引用时只改变 token 顺序，不重复上传或更换附件身份。

### 保存和重新编辑

- 最终内容只保存业务 adapter 提供的稳定引用。共享组件不发明永久 URL，也不把临时地址写入正文。

- 重新打开已有内容时，业务 adapter 解析本领域的永久引用，重建附件项、草稿 token 和原正文位置。最终序列化只输出上传成功且仍留在正文中的图片。服务端临时文件何时清理由上传业务负责。

- 调用方需要提供四项规则：
  - 接受哪些图片，以及数量、大小和格式限制。
  - 图片上传到哪个服务端位置。
  - 永久正文使用 Markdown 地址、Workspace path 还是原生 media part。
  - 当前用户是否可以读取、删除、替换或保存图片。

### 阅读和点开图片

- **纯图片附件消息只显示图片卡片，隐藏系统生成的附件说明。** 仅当完整文本匹配自动生成的“图片附件”说明或旧式 `[image: 文件名]` 占位时省略该说明；用户输入的正文继续按原位置显示。这个规则只影响聊天中的呈现，保留已保存的消息原文和媒体内容。

- Markdown、消息图片、正文引用和托盘缩略图使用同一打开入口。可预览节点带 `data-portal-preview-image`；Markdown 渲染器生成带 `portalMarkdownImage` 外观的图片。`PortalImageEditor.bind(root, options)` 统一绑定 root 本身及其后代的图片交互，点击后把可读地址和标题交给 `PortalImageEditor.open`。独立 Preview renderer 直接调用图片责任方，不依赖 Chat 的绑定实现。

- 图片节点只拥有一份点击监听，重新绑定更新当前回调和配置。共享图片模块接管动态加入的图片；默认绑定不会覆盖业务显式提供的回调，不会一次点击同时打开默认与定制查看器。修饰键和移除附件动作保持各自语义。

- 成功读取图片不会自动带来写权限。只有当前业务对象允许修改并提供保存回调时，调用方才传入 `editable: true`；附件 token、DOM 位置与来自某个页面均不能推导写权限。共享组件不判断 Resource、Workspace 或领域 ACL。

### 全屏查看与关闭

- `PortalImageEditor.open(url, title, options)` 在全页面共享 overlay 中启动独立会话。URL 为空时不打开。
- 每次打开都会重置图片、工具、“有未保存标注”标记（源码称 dirty 状态）、最多 24 步历史和保存回调，不继承上一张图的权限或标注。
- 图片加载期间和加载失败后禁用标注、复制与保存。加载失败在查看器内显示明确错误，并清空上一张图片留下的画布尺寸；取消和关闭仍可用。
- 未提供名称时，导出基名使用 `annotated-image`。
- 只有 `editable === true` 才显示并启用绘制、撤销、清空、复制和保存。画布尺寸尚未与图片同步时不能绘制。
- 关闭按钮、Escape、允许的背景点击和公开 `close()` 都经过同一个 Dialog controller。
- 有未保存标注时，关闭会询问“放弃尚未保存的图片标注？”。保存期间关闭和工具操作禁用；`close()` 返回实际关闭结果。

### 标注工具和画布

- 工具栏当前提供五种方式：画笔、箭头、矩形、文字和马赛克。再次选择当前工具会退出绘制。当前没有独立直线工具。

- 默认颜色是 `#ff3b30`，预设颜色为红、绿、黄、蓝、黑、白。
- 线宽范围为 2–24，默认 5。
- 画笔跟随指针轨迹；箭头和矩形在拖动时实时预览。
- 文字工具通过 Portal prompt 取得非空文本。
- 马赛克从原图采样，并以至少 4 像素的块像素化选区。
- 画布 CSS 尺寸跟随当前图片，内部像素尺寸乘以设备像素比。
- 视口大小、设备方向或工具栏换行改变可用空间时，查看器重新计算图片与画布尺寸，并等比迁移当前标注和撤销快照。可用高度扣除 overlay padding、实际工具栏高度和间距，横宽或纵长图片都保持在当前视口内。
- 每次操作前保存 ImageData；Undo 恢复上一份状态。
- 历史最多保存最近 24 份快照；丢弃更早快照后，即使用户撤销完这 24 步，只要画布仍包含更早标注，dirty 状态继续为真。
- 清空移除全部会话标注，并复位 dirty 与历史。

- 原图、标注层和业务保存是三层独立责任：image 元素显示原图，透明 canvas 保存当前会话标注，导出时再按原图自然分辨率合成 PNG。

- 查看器以实际容器可用宽度限制图片和内容区域，包含 overlay 的 padding；窄屏工具栏允许换行，放宽后恢复自然排列。页面 CSS 不另行决定图片查看器的溢出或按钮排列策略。

### 复制与保存

- 复制只在浏览器支持 Clipboard API 和 `ClipboardItem` 时启用。
- 复制按原图自然分辨率合成 PNG 并写入剪贴板，不调用 `onSave`。失败只显示局部错误并保留当前会话。
- 没有修改时点击保存直接关闭。
- 有标注时，编辑器生成 PNG Blob 和文件名 `<原基名>-annotated.png`，再调用 `onSave({blob, file, previewUrl, state, overlay})`。
- 保存回调成功后编辑器强制关闭，调用方接管新 preview URL 的后续生命周期。
- 保存回调失败时，编辑器撤销本次临时 URL，保留界面、标注和 dirty 状态，允许用户重试或复制。

## 组件怎样协作

```text
选择 / 粘贴 / 拖放图片
          ↓
业务上传 adapter
          ↓
一个图片附件项
   ├─ 正文引用
   └─ 附件托盘
          ↓ 业务序列化
永久正文引用
          ↓ 阅读渲染
Markdown 或消息图片
          ↓
PortalImageEditor
          ↓ 可选 onSave
业务保存 adapter
```

`PortalChatWorkspace` 实现附件归一化、引用插入与移动、草稿、最终序列化、附件托盘、粘贴与拖放，并将图片交互委托给共享图片责任方。`markdown.js` 把允许的 Markdown 图片标记为可预览。`PortalImageEditor` 负责图片节点绑定及其动态更新、lightbox、标注、dirty guard、复制和 PNG 合成。页面只提供图片对象与业务回调，不创建平行的 lightbox 或图片点击监听器。

Notice Board 的图片地址（新图片使用通用上传 `/api/uploads/upload_*`，早期公告保留 `notice_image_*`）、Chat 消息中的图片和 Paper Figure 的来源保持各自存储语义。Chat 发送的上传图片作为消息媒体部分保存 `upload_id`，重新显示或放回输入历史时还原为受会话权限保护的 `/api/uploads/<upload_id>`；本地路径图片仍按 Workspace 路径显示（decision_f560803947c6）。图片解码、格式、体积、像素、网络和权限错误由业务上传 adapter 返回；预览地址失效只影响该图片；保存失败保留标注。新增图片来源或永久格式时扩展业务 adapter，新增通用标注工具时修改图片编辑器。
