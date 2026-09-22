# 架构与修改入口

## 运行结构

```text
同源浏览器 / web/*.js
       │ Cookie 会话 + CSRF
       ▼
ThreadingHTTPServer / app/server.py
       │
应用服务 / app/service.py ── SQLite WAL / app/store.py
       │                           │
       ├── 一个生成 Worker 线程     └── data/assets/<design-id>/
       │      ├── 本地参数化生成
       │      └── Tripo POST / GET → GLB → OpenSCAD 草案
       │
       └── 设备协议 /api/device/poll
               ├── 内置模拟设备线程（明确标注）
               └── ESP32 独立 Bearer 凭据
```

选择单机、一个 API、一个生成 Worker，是为了让 ZIP 解压即跑。没有复制交易/分析系统的多进程架构。模拟设备另有轻量线程，不让长耗时模型生成阻塞模拟灯光回执；真实设备请求由 HTTP 线程接收。不是分布式任务队列：同一数据目录只能运行一个服务进程。

## 数据对象
users 与 sessions 存本地用户和哈希凭据；memories 存私有日记。designs 是数字版本，有 owner、memory_id、source_id、provider、生成任务 ID、状态与文件目录。instances 是实物实例，一设计可多实例，标签标识为 `BLM1:<实例32位ID>`。devices 与 commands 存设备和下发命令。events 存带事件 ID 的去重日志；favorites 不改变设计所有权。

删除记忆会让设计 memory_id 置空，不删除外装。公开序列化不返回原始 memory_id、任务 ID 和私人检查记录。制作 ZIP 不含原始记忆、账号密钥或设备 token。设计提示词是用户主动公开的内容，发布前必须预览。

## 任务状态

```text
queued → generating [parametric] → needs_review → reviewed
queued → submitting [Tripo] → polling → downloading → needs_review
submitting → submission_unknown → 用户核对任务ID → polling
不可恢复错误 → failed；已知任务的查询/下载可有限重试
```

幂等键唯一约束防止重复创建同一个请求。提交已产生费用但回包丢失时不盲目重试 POST。服务重启恢复已有 task_id 的轮询，未知提交停在 submission_unknown。没有 task_id 的参数化生成可安全重做。

## 权限与持久化
用户通过 PBKDF2 密码校验取得 HttpOnly/SameSite=Strict Cookie；写请求要求 CSRF token，带 Origin 时必须同源。每个对象按 owner 判断，公开设计只开放允许公开的部分。设备只持设备 token，不拥有用户会话。设备凭据在数据库中仅存 SHA256，不在 state 返回。

SQLite 启用 WAL、外键、busy_timeout。每次短操作独立提交；这不是金融级审计事务。设备事件有唯一 ID；网络重复发送会被去重。部分批次错误可能在已接收事件之后失败，重试已处理 ID 不重复应用。只有 owner 对应设备可写其事件。

## 未来生产化前要做什么
将标准库 HTTP 服务换成维护中的生产 ASGI/WSGI 服务与反向代理；加 HTTPS、连接/请求限额、统一迁移、备份与恢复演练、任务租约、可观测性、权限审计和删除生命周期。模型处理放进资源受限的隔离进程，完整验证外部文件。现有 GLB/下载大小上限不等同恶意资产沙箱。
