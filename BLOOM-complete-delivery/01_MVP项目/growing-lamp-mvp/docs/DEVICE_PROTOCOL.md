# 设备协议 / bloom-device-v1

## 1. 身份
在用户工作台创建设备，获得独立 id 与只显示一次的 token。固件使用 `Authorization: Bearer <token>` 调用 POST `/api/device/poll`。不需要用户 Cookie/CSRF。token 是权限边界，NFC 标签不是。

设备初始 seen=0，15秒内有有效上报才显示在线。旋转 token 后旧固件会得到401，需更新配置。默认内置模拟器由服务内部调用相同应用协议，但 kind=simulator，所有事件标 simulated。

## 2. 请求 / 回应

```json
{
  "reported":{"power":true,"brightness":45,"projection":20,"theme":"forest","instance_id":null,"temperature_c":27.1,"fault":false,"firmware":"bloom-esp32-1.0.0"},
  "events":[{"id":"boot-unique-counter","kind":"button","payload":{"detail":"toggle"}}],
  "acks":[{"id":"command-id","ok":true}]
}
```

```json
{
  "commands":[{"id":"32hex","kind":"light","payload":{"power":true,"brightness":45,"projection":20,"theme":"forest"},"expires":1789900000}],
  "known_tags":[{"instance_id":"32hex","tag_payload":"BLM1:32hex","theme":"forest"}],
  "server_time":1789899950,
  "protocol":"bloom-device-v1"
}
```

上面的短 `32hex` 只是说明占位符；真实 id 是32位小写十六进制字符串。每次 events/acks 上限16条。命令默认60秒有效，绑定90秒；设备根据回应 server_time 计算剩余TTL，不依赖自身时间校准。NTP 仅用于 HTTPS证书时间。

light 命令包含完整目标状态，不做增量计数。后来的 light 会 supersede 未确认旧命令。状态从 queued → acked/failed/expired/superseded。网络断开后到期旧命令不会突然执行；本地亮度保持安全限幅。

## 3. NFC 绑定

先创建实例；真实设备要求实例已记录打印与装配检查。点击绑定后，拿标签靠近读卡器，在90秒内**短按实体按钮**授权写入。用户主动写入会覆盖该标签指定用户区域原有内容，先使用空白标签。

用可写 NTAG213，固件检查 Capability Container，不写 manufacturer/lock/config 页。用户区第4至15页共48字节：

```text
03 2C D1 01 28 54 02 65 6E [ASCII "BLM1:" + 32位实例ID] FE 00
```

这是单个 NFC Forum Text RTD；实例ID指向软件中的设计版本和主题。先把 TLV 长度清零，写完后最后提交首页，再完整读回验证。手机不承担关键绑定功能。

成功上报：

```json
{"id":"stable-event-id","kind":"tag_bound","payload":{"instance_id":"实际32位ID","tag_uid":"实际UID","verified":true}}
```

服务检查同设备有对应的未过期绑定命令，再将实例标 bound。未经过真实绑定的模拟实例不会被真实设备 known_tags 返回。绑定后先移开标签，再安装到指定天线位置，等待 `outfit_installed`。

## 4. 离线使用
固件持久化亮度、主题、实例ID和最近16个已绑定标签映射。断网可本地开关、调光、识别缓存标签。超出16个的旧外装需要联网重新同步或调整缓存上限；首版不保证无限衣橱离线可用。

投影通道上电默认关闭，连续开启15分钟自动关闭。按键/NTC/NFC在主循环，网络在独立 FreeRTOS 任务，避免HTTP阻塞本地保护。故障关闭后需冷却并手动复位。

## 5. 去重与限制
事件ID跨重试稳定，服务唯一约束去重。固件缓存最近16个命令ID，并保留有限RAM事件/回执队列；掉电不保证未上报事件不丢失，队列满会打印告警。它不是不可丢失的审计日志。

普通 NFC 可复制、重写，本项目不提供防克隆、防拆或版权证明。标签里没有原始记忆和认证秘密。光影图案依赖用户手动插入树影/星点卡，theme 不会物理换片。
