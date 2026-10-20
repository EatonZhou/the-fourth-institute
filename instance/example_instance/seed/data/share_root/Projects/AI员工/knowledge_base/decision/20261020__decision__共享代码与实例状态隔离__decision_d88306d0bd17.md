---
kb_id: decision_d88306d0bd17
type: decision
project: AI员工
created_at: '2026-10-20'
version:
  number: 1
  created_at: "2026-10-20"
  created_by:
    user: ""
    agent: "resident_977d1a613d44"
  updated_at: "2026-10-20T10:02:18Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 7
ai_generated: false
links:
  source: []
  related: []
  supersedes: []
  derived_from: []
---

# 共享代码与实例状态隔离

规范所有者：实例安装与生命周期模块。

## 目标与布局

所有 Portal 实例使用同一份应用代码和数据库迁移定义。实例之间共享结构与行为，
各自保有用户、权限、账号、对话、知识库、Paper、Article、Workspace、Device 和运行状态。
结构相同不意味着业务数据相同，不通过数据库行同步维持代码一致。

```text
code/                                 共享应用、脚本、数据库迁移
instance/
  example_instance/
    seed/                             可发布的初始 SQL、资源及完整性清单
    config/ data/ runtime/ home/       安装后产生，Git 忽略
  dirtybase_instance/                 私有实例，整体 Git 忽略
    installation.json                 实例身份、共享代码位置、服务与依赖
    config/                           路径、站点、数据库连接和私有配置
    data/database/                    持久 PostgreSQL 集群
    data/{share,staging,instances,accounts,secrets}/
    runtime/                          Socket、PID、临时文件和运行日志
    home/                             该实例的用户态配置与工具状态
    maintenance/                      私有迁移、备份、核验与恢复记录
.build/                               可再生成的发行工程，Git 忽略
dependencies/                         可复用的版本化依赖，Git 忽略
```

共享代码中不得写入业务数据、凭据或运行产物。实例不得私自复制并维护应用代码。
安装器只在旧独立工程兼容模式保留实例内 `code/`；新结构通过
`shared_code_root` 指向公共代码，所有服务工作目录与启动脚本使用该位置。
`tfi_instance_layout.py` 是这些路径的唯一解释者。

## 发布与初始化

Git 默认忽略 `instance/*`，仅允许提交 `example_instance/` 的模板与 seed。
该目录的实际 `config/data/runtime/home/maintenance` 同样忽略。
seed 必须有文件清单、摘要、数据库格式和资源路径映射，不包含现用账号或密钥。
`prepare_install.py` 验证 seed，并从当前共享代码组装独立工程；不会修改运行数据库。

首次安装才可导入示例数据并执行首次用户初始化。重复启动、升级和老实例迁移不得
重新导入 seed，不得清空历史或生成替代身份。示例安装的阶段进展从空白开始；
主机实例迁移保留已有阶段进展及其他历史。

## 实例运行与依赖

每个实例具有独立数据库、NATS、服务名称、端口、Socket、配置和五个数据根。
应用只从该实例显式配置取得数据位置，不依靠当前工作目录选择数据。
LAN 访问由实例的监听地址及端口决定，不强制仅允许服务器本机。
实例可复用已经安装且验证可用的 Python、Provider CLI 和其他依赖；
账号与会话状态的位置必须明确，凭据不得进入公共代码或示例 seed。

基础平台只需准备一次。共享模式的自动依赖放在仓库 `dependencies/`：
原生程序按平台、版本及发布摘要复用，Python 环境按依赖声明和 profile 复用，
与实例的 seed 内容无关；跨实例准备使用共享锁。显式传入的现有程序仍可直接使用。
TeX 和浏览器程序同样放在共享依赖目录；账号、浏览器登录状态和编译结果属于实例。
Node 包沿工具既有的模块解析位置安装于共享代码的 `node_modules/`，由 Git 忽略；
打包及共享源码完整性检查统一排除这些已安装依赖和 Python/测试缓存。
每个新实例只初始化自己的配置与数据；已完成初始化的实例使用安装器
`start --root <实例目录>`、`stop --root <实例目录>`、`status --root <实例目录>`。
这些生命周期操作不得再次导入 seed。不同端口的实例可以同时运行；由同一业务
数据库克隆的迁移实例必须完成切换核验，不能并发执行复制来的待办任务。
迁移记录中 `business_execution_allowed=false` 时，实例启动和业务服务入口拒绝执行。

数据库结构由共享迁移定义管理；不同实例分别记录已应用迁移。
同一共享目录更新前应检查所有使用者的代码及 schema 兼容性，必要时安排共同停机。
现有独立工程的整体替换升级不得用于共享目录。不得因升级一个实例而删除或
覆盖公共代码，也不得将示例 SQL 当作升级迁移。

## 主机迁移与切换

迁移采用复制、校验、切换顺序，源文件和原数据库保留作为回退依据。
数据库必须使用 PostgreSQL 一致性备份，不能复制正在写入的数据库文件冒充备份。
保留对象 ID、用户密码哈希、TOTP/会话密钥、Provider 账号与会话、Resident 对话、
Workspace、Device 凭据及授权关系。路径迁移仅更正配置与结构化绑定，
不对历史消息正文做任意字符串替换。

预复制期间源站继续运行；最终切换需要停止或隔离源站写入，补齐文件增量并获得
与文件一致的最终数据库状态。新旧站不得同时消费同一业务任务、定时器或外部入口。
进程、Socket 和锁不是可移植状态；应从持久任务与会话记录恢复。

停用 RoboLab 与语义向量搜索表示不启动对应功能，不授权删除原始历史数据。
发现不可读取文件、无法移植的正在运行进程、外部设备身份或浏览器认证来源限制时，
必须记录实际差异，解决或交用户决定，不得标记为完全等价。

## 验收与回退

核验至少包括：

- 用户、Resident、对话、消息及关键资源的 ID、数量和内容校验；
- 原用户登录、原 Resident 历史读取与实际续聊；
- Provider 账号、已有 Workspace、Paper/Article、Device 连接与资源访问；
- 新端口从 LAN 访问，源站与新站不存在重复任务消费；
- 私有实例文件 Git 忽略，公共代码没有运行时写入，示例 seed 未被实例污染。

仅 HTTP 健康、数据库行数或复制成功不等价于完成迁移。
验收失败时停止新实例的业务执行，按切换记录恢复源站；切换后的新业务数据应先
保存并处理，不能直接回退旧快照而丢弃新输入。迁移记录与数据库备份保持私有。
