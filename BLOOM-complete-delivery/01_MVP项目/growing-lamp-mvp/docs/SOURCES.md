# 外部技术依据与实现参考

核对日期：2026-09-20。使用官方资料验证协议/接口；运行时模型可用性与材料规格仍需实际账户、设备与采购件核实。第三方库由PlatformIO按各自许可下载，本ZIP不捆绑其二进制或字体文件。

| 依据 | 官方来源 | 用途 |
|---|---|---|
| Tripo Python SDK | https://github.com/VAST-AI-Research/tripo-python-sdk | 文本任务、轮询、模型结果 |
| Tripo官方client源码 | https://raw.githubusercontent.com/VAST-AI-Research/tripo-python-sdk/master/tripo3d/client.py | `/v2/openapi/task` 与 task_id |
| HeyGears Blueprint | https://www.heygears.com/SAP/Blueprint | 人工检查、排布与制造工作流；不推断公开控制API |
| Adafruit PN532指南 | https://learn.adafruit.com/adafruit-pn532-rfid-nfc | 读写硬件原型参考 |
| Adafruit PN532头文件 | https://raw.githubusercontent.com/adafruit/Adafruit-PN532/1.3.4/Adafruit_PN532.h | SPI构造与Type-2分页读写接口 |
| PlatformIO ESP32 Dev | https://docs.platformio.org/en/latest/boards/espressif32/esp32dev.html | 板型标识与构建方式 |
| Espressif32 6.9.0发行版 | https://github.com/platformio/platform-espressif32/releases/tag/v6.9.0 | Arduino-ESP32 2.0.17兼容基线 |
| Espressif LEDC文档 | https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ledc.html | PWM背景及新旧API差异；本固件锁定2.x |

BOM中的功耗、机械尺寸、光学焦距、保护阈值均为本项目工程假设/设计约束，不是引用资料的实测结论。不得把资料里存在某功能解释为本项目已经通过硬件验证。
