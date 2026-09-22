# HTTP API / v1

同源 JSON；成功结构按具体接口返回，不包统一 data 字段。失败是 `{"error":"可读信息"}`，并使用 400/401/403/404/409/413/429/500 状态码。默认 JSON 上限 256 KiB。静态文件与 API 共用 8787。

用户写入需 Cookie 与 `X-CSRF-Token`；登录、注册和设备 poll 是例外。登录返回 csrf，刷新后 GET auth/me 可重新取出。设计生成额外必须传 `Idempotency-Key`（8–100 字符）。

| 方法 | 路径 | 输入 / 返回 |
|---|---|---|
| GET | `/api/health` | ok, version, mode；不含凭据 |
| POST | `/api/auth/register` | name,email,password（至少10字符） |
| POST | `/api/auth/login` | email,password → user,csrf；设置会话 Cookie |
| GET / POST | `/api/auth/me` / `/api/auth/logout` | 当前用户 / 退出 |
| GET | `/api/state` | 当前用户的记忆、设计、实例、设备、事件，公开花园与 capabilities |
| POST | `/api/memories` | title,body,theme → id |
| PATCH / DELETE | `/api/memories/:id` | 完整 title,body,theme / 删除 |
| POST | `/api/designs` | title,prompt,theme,provider,memory_id?,source_id?,accept_charges? → id |
| GET | `/api/designs/:id` | 自己或公开设计；去掉不可共享字段 |
| POST | `/api/designs/:id/review` | acknowledge:true,note（至少8字符） |
| POST | `/api/designs/:id/publish` | public:true,confirm_rights:true；public:false 撤下 |
| POST | `/api/designs/:id/recover` | task_id，继续查询异常任务，不重新计费提交 |
| POST | `/api/designs/:id/favorite` | favorite:true/false |
| GET | `/api/designs/:id/package` | 二进制制造 ZIP |
| GET | `/api/designs/:id/asset/:file` | 白名单中的 STL/GLB/JSON/SCAD；仍检查权限 |
| POST | `/api/instances` | design_id,name → 独立实例 id，默认 planned |
| POST | `/api/instances/:id/stage` | stage:printed/verified,acknowledge:true；严格顺序 |
| POST | `/api/instances/:id/bind` | device_id；下发待确认绑定命令 |
| POST | `/api/devices` | name → id,一次性明文 token；设备保持离线 |
| POST | `/api/devices/:id/light` | power,brightness,projection,theme → queued 命令 |
| POST | `/api/devices/:id/rotate-token` | 生成新 token，使旧 token 失效 |
| POST | `/api/device/poll` | Bearer 设备 token；详见 DEVICE_PROTOCOL.md |
| POST | `/api/sim/install` | device_id,instance_id；仅允许模拟器 |
| GET | `/api/hardware/files` | 工程导出文件清单 |
| GET | `/api/hardware/file/:name` | 下载硬件 STL/JSON/日志 |

主题只允许 `sunflower`、`forest`、`stars`。亮度字段为整数；主通道 API 0–100，固件独立限为 60%；投影 API 与固件均 0–35。模拟器可显示 100% 主亮度，真实硬件会通过 reported 返回限幅后的值；它不是实际光通量测量。

## 示例：创建不调用付费服务的任务

```json
{"title":"森林里的小房间","prompt":"Rounded forest leaves around a flat annular crown","theme":"forest","provider":"parametric"}
```

客户端给此提交生成唯一幂等键，同一请求重试复用；用户修改描述或主动建立新版本时换新键。参数化结果由 prompt、theme、seed 确定，但不会语义理解描述。

没有 WebSocket/SSE；网页约2.2秒查询 state，ESP32约2秒 poll。UI 发出请求不会先宣称设备已经动作。
