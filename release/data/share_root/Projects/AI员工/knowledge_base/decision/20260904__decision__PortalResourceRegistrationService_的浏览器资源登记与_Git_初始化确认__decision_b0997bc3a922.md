---
kb_id: decision_b0997bc3a922
type: decision
project: AI员工
created_at: '2026-09-04'
version:
  number: 5
  created_at: '2026-09-04'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T01:54:45Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/static/portal_instruction_client.js
  - ${code_root}/management_portal/static/portal_resource_registration_service.js
  - ${code_root}/management_portal/static/resource_catalog.js
  - ${code_root}/management_portal/static/article_workspace.js
  - ${code_root}/management_portal/static/repo_workspace.js
  related:
  - decision_ad3e21094f14
  - decision_b6e74d78f7b6
  - decision_36f46b395d8e
  - decision_2f67ed28db67
  - decision_6476c69b33da
  - decision_956154cd02f6
  - decision_daba6706a788
  supersedes: []
  derived_from: []
---
# PortalResourceRegistrationService 的浏览器资源登记与 Git 初始化确认

用户可以在三个页面上手动把已有目录或设备登记为 Portal 资源：资源目录（Resource Catalog）、Repo 工作区和 Article 工作区。每个页面用自己的对话框收集这类资源需要的字段，再交给同一个浏览器端共享服务提交。这个共享服务名为 `PortalResourceRegistrationService`，它通过 HATG ISA 的 `RegisterResource` 指令完成登记。登记 Repo 时如果目标目录还不是 Git 仓库，共享服务会先请用户确认，同意后再提交一次，由服务端初始化 Git 仓库并完成登记。

本 Design 说明三个页面的登记入口、共享服务的提交和确认流程，以及页面与服务端各自负责什么。`RegisterResource` 指令的定义见 `decision_ad3e21094f14`，ResourceService 的登记规则见 `decision_b6e74d78f7b6`，后台自动发现并登记 Repo 与 Article 见 `decision_36f46b395d8e`。

## 三个页面上的登记入口

### 资源目录

资源目录是跨类型的资源列表页。工具栏上的登记按钮随当前分类变化：

- **“注册新 Repo”**：只在 Repo 分类下显示。点击后打开“注册 Repo”对话框，包含工作副本路径输入框、可选的 Project 输入框（多个 Project 用逗号分隔）、“取消”和“注册”按钮，以及右上角的关闭按钮。
- **“注册新设备”**：只在设备分类下显示。对话框收集设备名称、设备类型、说明、主机名、型号、主要 IP、位置，以及可选的 SSH 连接信息（主机、用户、端口、SSH 配置文件、密钥文件）和 SSH 密码。设备登记成功后，如果填写了 SSH 密码，页面再用 `RunResourceAction` 执行设备的 `ssh.credential.save` 动作保存密码；密码保存失败时设备仍已登记，页面单独提示密码保存失败。

登记成功后，页面关闭对话框，打开新资源的详情，并刷新列表。

### Repo 工作区

Repo 工作区是浏览和操作代码仓库的页面。工具栏上的“注册新 Repo”按钮打开与资源目录相同字段的对话框。登记成功后，页面关闭对话框，直接打开新 Repo 的工作区并刷新列表。

### Article 工作区

Article 工作区是论文稿件的编辑与编译页面。“注册新 Article”按钮打开“注册 Article 目录”对话框：

1. 用户填写目录路径，点击“检查目录”。页面调用 Article 领域的检查接口 `/api/articles/preflight`，在对话框中显示检查结果和问题说明。
2. 检查通过后，页面显示主稿件文件选择和编译器选择（`pdflatex`、`xelatex` 或 `lualatex`）。检测到多个主稿件时，用户必须确认要登记哪一个。
3. 用户填写可选的 Project 后提交。提交前页面再检查一次目录，检查不通过时不提交。

目录检查是 Article 工作区自己的功能，只用于这个对话框；共享服务只接收页面组装好的字段。

## 共享服务怎样提交

共享服务挂在浏览器全局对象上，只提供一个方法 `register(payload, options)`：

- `payload` 是页面准备好的对象，其中 `resource_type` 指定资源类型（`repo`、`device` 或 `article`），其余字段原样作为资源字段提交。
- `options.confirmInitialize` 是可选的确认函数，由页面实现。它收到服务端的说明文字和目标路径，返回用户是否同意初始化 Git 仓库。

服务把 `payload` 拆成资源类型和字段，调用 `RegisterResource`，每次提交使用一个新的随机幂等键。服务端成功时，服务原样返回指令结果，其中包含新资源的统一 ID（`resource_id`）和资源记录。页面脚本重复加载时复用已经创建的服务，不会覆盖它。

## 设计点

- **页面负责收集字段和本地校验，服务端负责决定能否登记。** 共享服务不生成资源 ID，也不判断用户角色。调用者身份来自当前登录会话，ResourceService 和对应资源类型的领域实现负责权限检查、路径规范化和登记。
- **提交成功后，页面关闭对话框、显示成功提示，并按自己的规则刷新列表或打开详情。**
- **普通失败原样交给页面。** 页面保留表单内容并显示错误，用户可以修改后重新提交。
- **只有“目录还不是 Git 仓库”这一种错误会触发确认。** 其他错误一律直接交给页面，不做猜测式重试。

## Git 初始化确认

登记 Repo 时，如果目标目录还不是 Git 仓库，`RegisterResource` 以错误结束，错误码为 `instruction.conflict`，错误详情中 `reason` 为 `git_checkout_required`，同时给出规范化后的目录 `path`。这次失败不会修改目录。

```text
第一次提交：原字段，不含 initialize_git
  → 服务端返回 git_checkout_required
  → 页面的确认函数显示确认框（说明目录路径与将要创建 .git）
      → 用户拒绝：返回 {cancelled: true}，不再提交
      → 用户同意：原字段加上 initialize_git: true，第二次提交
                    → 服务端先初始化 Git 仓库，再登记 Repo
```

- **第一次提交从不包含 `initialize_git`。** 只有用户明确同意后，第二次提交才加入这个字段，其余字段保持不变。
- **最多提交两次。** 第二次提交失败时直接把错误交给页面，不再弹出确认。
- **用户拒绝时返回 `{cancelled: true}`。** 这表示用户取消了操作，服务端没有登记任何资源；页面不显示成功提示，是否关闭对话框由页面决定。
- **确认文字由页面提供。** 资源目录和 Repo 工作区的确认框标题为“初始化 Git 仓库？”，说明目录路径，确认按钮为“初始化并注册”。
- **没有提供确认函数时，这个错误直接交给页面。**
- **`initialize_git: true` 只表示用户同意这次写入。** 它不提升用户权限，也不跳过路径校验。

把这段流程放在共享服务中，可以保证任何登记 Repo 的页面都遵守“先确认、只重试一次”，不会因为各自实现而跳过确认或重复初始化。各方分工如下：

| 职责 | 共享服务 | 页面 | 服务端 |
| --- | --- | --- | --- |
| 收集和校验表单字段 | | 是 | |
| 调用 `RegisterResource` | 是 | | |
| 识别 `git_checkout_required` 并只重试一次 | 是 | | |
| 显示确认框并取得用户决定 | 调用确认函数 | 是 | |
| 显示提示、打开详情、刷新列表 | | 是 | |
| 权限检查、初始化 Git、登记资源 | | | 是 |

## 故障与恢复

- **网络错误、服务端错误和格式异常的响应**都会结束当前流程并交给页面显示。表单内容留在页面上，用户可以修改后再提交。
- **确认后的第二次提交如果断线**，页面不能假定服务端没有完成操作。用户应先刷新列表或按同一路径查找，确认资源是否已经登记，再决定是否重试。
- **登记成功但资源索引尚未更新时**，指令结果仍包含新资源的统一 ID，资源记录中带有“索引尚未确认”的标记（`index_projection`）。页面用返回的 ID 打开详情；列表稍后由索引更新补上，不能为了让列表立即出现而再次登记。
