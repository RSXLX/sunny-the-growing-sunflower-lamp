# SUNNY — The Growing Sunflower Lamp
## A Gift for the Empty Room

把一段生活变成一件外装，再变成房间里的光。

这是一个**可以本地运行的软件 MVP + 可编辑的硬件工程原型包**，不是效果图合集。包含六页工作台、真实账户与 SQLite 持久化、生成任务、制造 ZIP、设备协议、NFC 绑定、ESP32 固件、OpenSCAD CAD 和实际导出的 STL。

**交付边界：**本包未包含已经组装的硬件；未完成 ESP32 全量交叉编译/烧录、真实 NFC/温升/装配测试，也没有用真实密钥调用付费 Tripo。执行过的测试和未验证事项见 `docs/TEST_REPORT.md`。演示模型不冒充 AI 生成，模拟设备不冒充实物。

## 1. 三步启动

要求 Python **3.10 或以上**。HTTP 和 SQLite 使用 Python 标准库，参考图处理使用固定版本 Pillow；没有安装时参数化演示仍可运行，但图片功能不可用。无需 npm、独立数据库服务或 API Key 即可体验本地演示。

```bash
cd growing-lamp-mvp
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

浏览器打开 **http://127.0.0.1:8787**。终端保持运行；停止用 `Ctrl+C`。

Mac 也可运行 `bash start.command`；Windows 可双击 `start.bat` 或执行 `py -3 run.py`。如果双击脚本被系统阻止，直接使用终端命令，不需要降低系统安全设置。

演示账号：

| 账号 | 密码 | 用途 |
|---|---|---|
| `alice@bloom.local` | `BloomDemo!2026` | 嘉一的房间，创作与装配流程 |
| `bob@bloom.local` | `BloomDemo!2026` | 第二位创作者，分享、收藏与二创 |

这只是本地演示密码，禁止将演示模式直接开放到公网。新用户可在登录页创建独立本地账号。

## 2. 十分钟走通闭环

进入「我的房间」并调光，状态会等模拟设备回执。保存一条私人记忆，在「外装工作室」填写你愿意用于生成的视觉描述，选择参数化演示并生成。

进入「我的衣橱」：保存设计检查记录 → 打开制造版本 → 检查实际文件并完成制造审核 → 制作一件 → 模拟 NFC 绑定 → 等待已绑定 → 模拟换装。制造草案可先下载检查，批准版本才允许创建制作档案。随后发布指定版本，退出并登录第二个账号，在「交换花园」基于它创作；原始日记和参考图不会被分享。

参数化演示产生真正的、有种子变化的 STL；它不是语义 AI。所有打印和装配状态需要人工确认；不要为了演示把没有打印的对象标成已打印。

## 3. 文件结构

```text
app/                         标准库 API、SQLite、生成 Worker、设备协议
web/                         六页响应式工作台、WebGL + 软件三维预览
hardware/cad/lamp.scad        可编辑结构 CAD，含装配视图
hardware/cad/interface.json   统一夹持接口 v1
hardware/exports/            17 个 STL + 检查结果
hardware/electronics/         接线 SVG、连接网表、BOM
hardware/firmware/            ESP32 / PlatformIO 工程
hardware/firmware/test/       可在电脑编译运行的固件逻辑测试
data/assets/                 运行时模型目录，首次运行自动创建
sample-data/                 无私人数据的示例参数
tools/                              CAD 导出、GLB 适配、检查、备份
tests/                       Python 集成测试、可选浏览器测试
docs/                       组装、协议、制造、部署、安全与验收
evidence/                   本次实际测试日志与工作台截图
```

## 4. 接真实 Tripo

```bash
cp .env.example .env
```

在 `.env` 中填 `TRIPO_API_KEY`，按允许创建的任务数设置 `TRIPO_MAX_SUBMISSIONS`（默认 0，不允许新建真实任务），重启。工作室选择 Tripo API、文字或图片输入，核对发送预览并确认费用。次数限制是此数据库累计任务数，不是供应商积分或金额上限。

API 负责生成装饰原稿。服务保留供应商任务 ID，持续查询；不因刷新网页重复提交。提交结果不明时，手动从 Tripo 控制台核对任务 ID 后恢复，避免重复付费。

本地安装 OpenSCAD 后，服务可执行环形裁切和固定载体适配。没有 OpenSCAD 或遇到不支持的 GLB，仍保留原稿、适配脚本和明确错误，要求人工处理。**任意 AI 原稿不保证自动可制造。**详见 `docs/TRIPO.md`。

## 5. 接真实硬件

先阅读 `hardware/electronics/BOM.md`、`docs/HARDWARE.md` 和 `docs/PRINT_AND_ASSEMBLY.md`。无需定制 PCB：采用采购模块和绝缘线束。

工作台「硬件与联调」创建设备，复制只出现一次的 token。复制固件配置，填 Wi-Fi、服务器局域网地址和 token：

```bash
cp hardware/firmware/include/config.example.h hardware/firmware/include/config.local.h
# 编辑 config.local.h。然后，在已安装 PlatformIO 的电脑执行：
cd hardware/firmware
pio run
pio run -t upload
pio device monitor
```

手机和 ESP32 访问服务器时，不能使用 `127.0.0.1`；填运行电脑的局域网 IP。仅在可信网络上启动：

```bash
ALLOW_LAN=1 python3 run.py --host 0.0.0.0
```

Windows 在 `.env` 中设置 `ALLOW_LAN=1`、`HOST=0.0.0.0` 后再启动。端口默认 8787；主机防火墙仅允许可信局域网。不要关闭整个防火墙，不要把此端口映射到公网。

这版固件需要有效的 NTC 温度分压。缺失或异常传感器会锁定关闭输出。先接对传感器，不要删除保护代码来“让它亮”。

## 6. 验证与数据

```bash
python3 -m unittest discover -s tests -v
python3 tools/check.py
```

默认数据保存在 `data/bloom.sqlite3` 和 `data/assets/`。删除整个 `data/` 会丢失本机用户、记忆与设计；服务停止后再操作。备份方式见 `docs/OPERATIONS.md`。

真实使用不需要演示账号时，请选择全新目录：

```bash
python3 run.py --no-demo --data ./private-data
```

关闭演示不会删除已经存在的演示数据，因此必须用新目录获得干净环境。没有演示模式时需配置真实 Tripo 才能生成新设计；可先配置 Key，再启动。网页仍允许注册本地用户。

## 7. 必读说明

- `docs/PRODUCT.md`：功能、界面与边界。
- `docs/ARCHITECTURE.md` / `docs/API.md`：开发入口。
- `docs/DEVICE_PROTOCOL.md`：设备认证、命令、回执、NFC。
- `docs/HARDWARE.md` / `docs/PRINT_AND_ASSEMBLY.md`：电气与机械。
- `docs/HEYGEARS_WORKFLOW.md`：真实制造证据链，不伪造自动打印 API。
- `docs/TEST_REPORT.md` / `docs/SAFETY_AND_LIMITATIONS.md`：实际测试和未验证项。
- `docs/DEMO_SCRIPT.md`：三分钟展示顺序。

源码采用 MIT；本包原创硬件设计和预置参数化样例采用 CC BY 4.0。第三方服务/库保持各自许可。用户发布设计前需自行确认权利，不能仅凭使用生成工具就保证拥有公开授权。
