---
kb_id: decision_85222f2d316b
type: decision
project: AI员工
created_at: '2026-08-25'
version:
  number: 14
  created_at: "2026-08-25"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-10-21T18:09:47Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_common/portal_network.py
  - ${code_root}/management_portal/http_admin.py
  - ${code_root}/scripts/portal_instance_storage.py
  - ${code_root}/tests/cases/test_portal_network_settings.py
  - ${code_root}/scripts/portal_user.py
  - ${code_root}/scripts/portal_service_units.py
  - ${code_root}/management_portal/portal_password_reset.py
  - ${code_root}/management_portal/portal_deployment_registry.py
  - ${code_root}/management_portal/portal_deployment_router.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_portal_deployment_router.py
  - ${code_root}/tests/cases/test_portal_security_repair_wave1.py
  related:
  - decision_99bb5dc0f96b
  - decision_3bce8b455a3c
  - decision_626fb02b3894
  - decision_08bb2d9141f9
  - decision_ab9880dc4c9d
  - decision_fb910c43ab07
  - decision_3fe3cca8fd95
  - decision_34158fdd4078
  supersedes: []
  derived_from: []
---

# Portal 网络入口、TLS 与可信代理边界

Portal 可以从 TFI Portal 主机所在的局域网访问，也可以经受控的公网入口访问。公网请求依次经过边界网关、加密隧道、主机 Nginx 和 Portal Router，最后到达主机上的某个 Portal 实例。本文说明请求经过哪些节点、每个节点负责什么、Router 怎样选择实例和转发请求，以及应用可以信任哪些网络信息。登录身份和业务权限由 Portal 应用检查，见 decision_3fe3cca8fd95。

## 基本概念

- **网络入口**：浏览器或程序开始访问 Portal 的地址。入口只决定请求能否到达服务。
- **边界网关**：一台承接公网流量的 VPS（部署在外部机房的虚拟服务器）。它监听公网域名，把通过验证的流量送往 TFI Portal 主机。
- **TLS**：HTTPS 使用的传输加密协议，加密浏览器与网关之间的数据，并用证书证明域名属于这个入口。**TLS 终止**指网关完成证书握手后，再把请求转发给受控的下一跳。
- **WireGuard**：边界网关与 TFI Portal 主机之间的私有加密隧道。
- **反向代理**：接收客户端请求，再代表客户端请求内部服务的程序。边界网关上的 Nginx 负责 TLS 和第一段转发；主机 Nginx 接收隧道流量和局域网 HTTPS，再交给 Portal Router 选择实例。
- **Portal 实例**：一套可以处理 Portal 请求的运行中服务。当前登记了 Dev（`dev`，开发用）和 Release（`release`，正式使用）两个实例（decision_08bb2d9141f9）。
- **Portal Router**：运行在 TFI Portal 主机上的用户路由服务（`management_portal/portal_deployment_router.py`，用户服务 `portal-user-router.service`）。它为每个请求选择一个实例并代理过去。
- **回环地址**：只指向本机的 IP，例如 `127.0.0.1` 或 `::1`。
- **转发信息**：代理写给下游的请求头，说明原始客户端的 IP（`X-Forwarded-For`）和原始请求是否使用 HTTPS（`X-Forwarded-Proto`）。

## 请求经过的路径

```text
公网浏览器
  → 域名与 HTTPS
  → VPS 边界网关上的 Nginx（TLS 终止）
  → WireGuard 私有隧道
  → TFI Portal 主机 Nginx（WireGuard 地址 10.77.0.2:18087）
  → 本机 Portal Router（127.0.0.1:18084）
  → 选中的 Portal 实例（Dev 或 Release）
  → Portal 登录认证与授权
```

局域网 HTTPS 先连接主机 Nginx 的 443 端口（192.168.115.204、10.138.30.36、172.16.0.95），再到 Router；局域网 HTTP 可直连 Router 18084。无论从哪个入口进入，Portal 都执行同样的登录会话、Resident Bearer 令牌、权限系统和领域业务边界检查。

## 设计点

### 公网入口

- **公网入口只有两种状态：私有和发布。** 私有状态下，边界网关不开放 Portal 的公网 HTTP 与 HTTPS，只保留使用密钥的 SSH 管理和 WireGuard 隧道。
- **域名受控、证书可信、Nginx 配置和隧道下一跳都通过验收后，才进入发布状态。** 缺少其中任一项时保持私有。
- **发布状态下只有边界网关一个公网入口。** 域名指向 VPS，Nginx 完成 TLS，再经 WireGuard 转到 TFI Portal 主机。
- **公网 HTTP 只用于证书验证和跳转到 HTTPS。** 登录只在 HTTPS 上进行。
- **Nginx 不另设网络层账号。** 用户在 Portal 的 `/login` 登录，公网和局域网得到同一种 `db_session` 浏览器会话。

### 身份与权限

- **网络可达不产生 Portal 身份。** 域名、TLS、VPN 和来源网络只决定请求能否到达应用。
- **所有入口使用同一套应用认证。** 公网和局域网都使用 Portal 登录会话、Resident Bearer 令牌（decision_fb910c43ab07）或 User 的个人访问凭据（`dbu_` 前缀，decision_6eac174d85bf）。
- **个人凭据只发往客户端批准过的入口。** 命令行客户端 `scripts/portal_user.py` 在凭据文件中保存批准的入口列表 `trusted_urls`；同一 Portal 有多个入口（例如局域网地址、公网域名或本机 SSH 隧道）时，用户确认新入口属于同一 Portal 后执行 `trust-url` 加入，`untrust-url` 移除。客户端不会根据 IP、服务版本或公开响应自动认定同一实例，入口列表也不改变服务端的身份或权限。
- **更换网关或隧道不改变调用者。** 用户和 Resident 的身份只由应用凭据确定。

### 选择 Portal 实例

Router 按下面的顺序为每个请求选择实例：

1. **账号安全相关的请求总是交给 Dev。** 包括 `/api/auth/passkeys/`、`/api/auth/password-reset/`、`/api/account/` 下的接口，以及 `/forgot-password`、`/reset-password` 和账号设置页面的脚本与样式。这样同一次安全操作不会跨越两个版本不同的实例。
2. **已登录的用户使用自己保存的实例偏好。** 偏好必须属于当前用户、指向当前登记的实例；否则忽略。
3. **没有有效偏好时使用默认实例。** 显式 `PORTAL_ROUTER_DEV_USERS` 或站点配置 `router_dev_users` 名单（未配置时为空）中的用户默认使用 Dev，其他用户默认使用 Release；默认实例不可用时依次改用 Release、Dev。
4. **未登录时，`/login` 与 `/login/verify` 交给 Dev，其他请求交给 Release。**

- **用户可以读取和修改自己的实例偏好。** 接口 `/__portal_router/preference`：`GET` 返回默认实例、当前偏好、实际生效的实例和可选实例列表；`POST` 提交 `{"instance": "<实例 ID>"}`，只接受当前登记的实例。未登录返回 401，请求体超过 4096 字节返回 413，不支持的方法返回 405。
- **偏好保存在未签名 Cookie `db_portal_instance` 中。** 内容为 Base64 编码的用户名和实例 ID；它只选择路由，不证明身份或权限。Router 与当前已认证用户名比对后使用该偏好，有效期 180 天，属性为 `Path=/`、`HttpOnly`、`SameSite=Lax`；经可信 HTTPS 访问时再加 `Secure`。用户名不匹配的偏好被忽略。POST 在读取内容前检查已有 Origin 与可信请求协议、Host 是否同源，不符返回 403。

### 转发请求的安全规则

- **Router 按站点访问范围检查直接连接方。** 本机模式只接受回环来源；远程模式允许远端连接，应用认证仍然生效。未显式保存访问模式的旧网段配置继续按原 IPv4/IPv6 地址和 CIDR 限制，始终保留 `127.0.0.0/8`、`::1/128`。此判断不采用客户端自报的转发头，也不代替公网网关访问控制。新连接重读访问配置；监听地址和端口在 Router 启动时确定，改变它们需重启 Router。
- **Router 只转发以 `/` 开头的站内路径。** 带协议和主机名的完整外部地址被拒绝。
- **长度含糊的请求在转发前被拒绝。** 多个不一致的 `Content-Length`、负数长度返回 400；分块传输（chunked）的请求体返回 501，Router 不支持这种请求体。
- **客户端不能自己选择内部实例。** Router 删除客户端提供的逐跳头（只对当前网络连接有意义的请求头）、实例选择头和转发头，再写入自己确认过的客户端 IP 与协议。
- **固定长度的请求体按每块最多 1 MiB 流式转发。** 客户端在发完声明的长度之前断开时，Router 中止这次转发；这时实例可能已收到部分请求体，这次请求按失败处理。
- **响应按每块 64 KiB 流式返回。** Router 不透传实例响应中的逐跳头、`Server`、`Date` 和实例标识头；客户端断开后停止写入。
- **实例不可达或超时时返回 502。** 响应不含内部地址、本地路径或异常信息。Router 等待实例响应的时间默认 3600 秒（`PORTAL_ROUTER_BACKEND_TIMEOUT`）。

### 可信的客户端信息

- **只有直接连接方是回环地址时，才采用请求中的转发信息。** 这时的转发信息来自本机上的代理；远端客户端自己填写的同名请求头被忽略。
- **Router 保留来自回环代理的合法 IP 转发链，协议只接受 http 或 https。** 非法链退回直接连接方地址；远端请求自报转发信息被忽略。
- **ASGI 可信代理中间件确定请求协议和客户端地址。** 它从转发链右侧寻找不可信节点；Portal 应用只读取 ASGI 结果，不再解释 X-Forwarded-*。公网链中代理地址作为限速客户端的部署问题仍需另行处理。

### HTTPS 对应用的影响

- **经可信 HTTPS 访问时，Cookie 带 `Secure` 属性。** 登录会话、两步验证挑战和实例偏好只通过安全连接发送。
- **非本机访问时，Passkey 需要 HTTPS。** Portal 用可信的协议和主机名确定 WebAuthn 站点身份（decision_626fb02b3894）。
- **同源检查使用可信的入口信息。** 远端请求头不能伪造页面来源。
- **密码重置链接使用配置的公开 HTTPS 地址。** 地址的配置优先级、未配置时的禁用行为及 HTTPS 校验以 decision_3bce8b455a3c 为准；链接不按当前请求的 Host 生成。

### 状态与故障

- **Router 自己提供健康检查和运行状态。** 两个接口都不经过被选中的实例，也不写普通访问日志。
- **`/__portal_router/health` 直接返回 `ok`。**
- **`/__portal_router/status` 只返回可以公开的运行状态字段。** 内部说明和本地路径被移除；只接受 `GET` 与 `HEAD`，其他方法返回 405；读取运行状态失败时返回固定结构的 `blocked` 状态。运行状态的含义见 decision_99bb5dc0f96b。
- **实例登记文件损坏时继续使用最后一份合法内容。** 读取失败不会让 Router 转发到未经校验的地址。

## 实例登记

Router 从实例登记文件（默认 `portal_paths.path("instances_root") / "deployments.json"`，可由 `PORTAL_DEPLOYMENT_REGISTRY_PATH` 指定；当前格式为 `tfi.portal-deployments.v1`，读取仅接受该精确格式，读取不迁移原文件）读取可用实例。每个实例记录 ID、类型（`dev`、`release` 或 `exploration`）、显示名称、排序、是否启用和内部地址。文件修改后，Router 在下一次请求时读取新内容，不需要重启。

内部地址必须满足：

- 使用 HTTP；
- 主机是明确的回环 IP，`localhost` 这样的名字不被接受；
- 不带路径。

实例还可以登记一个供浏览器直接访问的地址（`browser_origin`），它必须是 `http://localhost:<端口>`。这些限制保证修改登记文件不能把 Router 变成访问任意主机的代理。登记文件只保存实例目录，不保存用户权限。

## 组件职责

| 组件 | 负责 |
| --- | --- |
| DNS 与域名 | 让用户找到公网网关 |
| VPS 边界网关与 Nginx | 公网监听、TLS 证书、HTTP 跳转，以及经隧道转发 |
| WireGuard | 网关与主机之间的私有传输 |
| 主机 Nginx | 接收隧道及局域网 HTTPS，转交本机 Router |
| Portal Router | 选择实例、约束转发的请求、重建可信的转发信息、提供健康检查与运行状态 |
| Portal 实例 | 登录、账号状态和各领域的授权 |

端口、网段、域名、证书路径和防火墙规则由部署配置决定；相关安装、检查和恢复步骤见 `decision_ab9880dc4c9d`。本文列出的端口与网段是当前部署的取值，信任边界的规则不随这些取值变化。

## 站点网络设置

Portal 管理中的网络设置由部署层的 `management_common.portal_network` 唯一读写。配置文件按显式 `PORTAL_NETWORK_CONFIG`、站点 `deployment.network_config`、当前服务账号的 `~/.config/tfi/portal-network.env` 顺序定位；不探测旧产品目录，也不隐式读取另一实例的配置。需要沿用既有部署文件时，由部署者显式指定路径。指定路径必须为绝对路径，Dev、Release、Router 与进程启动器使用同一个站点文件。文件权限为 0600，可能包含代理认证信息，不进入源码、诊断响应或普通日志。

访问设置由 `access.mode`（`local` 或 `remote`）和 `access.port`（1024–65535 的整数）组成；默认端口为 18084。尚无显式模式时保留原 `--bind`/`--allow-network` 启动与旧网段兼容；已有旧网段不会被启动参数覆盖，读取以 `legacy_restriction` 标记它。显式保存访问设置后使用新模式与端口，并移除旧网段键；只读不转换历史限制。保存不重启 Router，不改写代理组；端口变化会在提交前检查本机可绑定性。配置缺失按本机范围处理；非法配置被拒绝，不能静默扩大访问范围。

出站代理保存 HTTP、HTTPS、通用代理和逗号分隔的不走代理地址。地址仅接受 HTTP(S) 代理 URL；每个协议为空时使用通用代理，全部为空表示明确直连。不走代理列表始终保留 localhost、127.0.0.1、::1。首次保存代理前继续读取既有 Provider 出口文件与服务环境；显式保存后，配置文件同时保存表单原值和大小写环境变量，HTTP/HTTPS 的通用回退只影响有效环境，不覆盖表单原值。

部署渲染器在每个服务已有 EnvironmentFile 后追加此可选文件，保存的代理作用于随后启动的服务；运行中的服务需要重启才会使用新环境。Resident 新进程和 Provider 登录进程在启动时读取已保存代理，保留既有身份与凭据隔离；已运行的 AI 会话不在保存时重启。此配置不修改 Nginx、TLS、域名或防火墙。

管理 API 只接受已登录的 Portal 管理员，不采用请求正文或外层 actor 参数自报身份。GET `/api/admin/network` 返回 `version`、`access={mode,port,urls,legacy_restriction}`、`proxy={http,https,all,no_proxy}`、`proxy_source=environment|saved` 和 `activation={access:router_restart,proxy:new_processes}`。POST 携带原样 `version` 和 `access={mode,port}` 或 `proxy`；一次只保存一组，不能将只读的 `urls`、`legacy_restriction` 写回。未知字段、非法地址或缺失版本返回 400，无管理员身份返回 403，版本冲突返回 409，存储错误返回不含内部细节的 500。

版本是权威文件字节的 SHA256。跨进程文件锁覆盖版本重读、校验与原子替换，所有字段先校验再提交，另一组配置保持原样，返回本次提交的状态。UI 遇到冲突保留输入，显式重读后由用户决定是否覆盖；不能静默复用旧版本。
