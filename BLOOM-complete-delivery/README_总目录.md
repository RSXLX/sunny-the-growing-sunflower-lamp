# BLOOM｜会长大的台灯 · 完整交付合集

**A Gift for the Empty Room**  
把一段生活变成一件外装，再变成房间里的光。

本包合并本轮对话中已交付的 MVP 项目、硬件采购与 3D 清单、CAD/STL、固件、技术文档和预览图。项目原文件及原始 ZIP 均保留；新增的是目录与汇总说明，没有在本次打包中修改应用代码、固件、CAD、STL 或 Excel。

## 先看这两点

1. **这是可运行的软件 MVP 加硬件工程原型，不是已制造、已安全验收的成品。** 真实打印、完整 ESP32 构建与烧录、NFC 读距、装配、光学、温升及耐久仍需验证；真实 Tripo 付费调用和 HeyGears 打印也尚未完成。
2. **不要直接把全部 STL 当作定稿送印。** 上一轮复核发现投影支架、镜片压环、扩散片固定、灯板散热与模块孔位等需要适配。先读 `03_补充说明/打印前必读_已知问题与验证边界.md`。

## 总目录

| 路径 | 内容 |
|---|---|
| `01_MVP项目/growing-lamp-mvp/` | 原项目完整解包：工作台、后端、硬件、测试、文档、样例和证据 |
| `02_硬件与3D清单/BLOOM-hardware-and-3D-checklist.xlsx` | 采购、数量、打印文件、尺寸、状态及适配问题 |
| `02_硬件与3D清单/原始参考文件/` | 上次清单附带的 BOM、装配说明、接口 JSON 和 OpenSCAD 原文 |
| `03_补充说明/` | 产品路线说明、硬件与 3D 清单的可读文档、打印前问题与验证边界 |
| `04_预览图/本版/` | 当前项目工作台与结构预览；完整六页与移动端截图在项目 `evidence/` |
| `04_预览图/此前版本参考/` | 对话中此前版本的桌面与移动端图片，只作历史参考 |
| `05_原始交付归档/BLOOM-growing-lamp-MVP.zip` | 原始项目 ZIP，按字节原样保留，通常无需再次解压 |
| `文件总表.md` | 合集内逐文件目录 |
| `SHA256SUMS.txt` | 文件 SHA-256 校验清单 |
| `PACKAGING_REPORT.json` | 本次打包范围、源文件保留记录及校验说明 |

## 启动工作台

要求 Python 3.10 或以上。在本合集根目录打开终端：

```bash
cd "01_MVP项目/growing-lamp-mvp"
python3 run.py
```

浏览器访问 `http://127.0.0.1:8787`。Mac 也可在项目目录执行 `bash start.command`；Windows 在项目目录执行 `py -3 run.py` 或使用 `start.bat`。

| 本地演示账号 | 密码 |
|---|---|
| `alice@bloom.local` | `BloomDemo!2026` |
| `bob@bloom.local` | `BloomDemo!2026` |

默认演示不需要 API Key 或 npm 依赖。演示账号密码公开，仅供本地体验，禁止直接暴露到公网。真实接入、局域网和数据备份按原项目 README 与运维文档操作。

## 文档入口

以下路径相对 `01_MVP项目/growing-lamp-mvp/`：

| 要做的事 | 先读 |
|---|---|
| 启动、理解项目 | `README.md`、`START_HERE.md` |
| 产品能力与边界 | `docs/PRODUCT.md` |
| 软件二次开发 | `docs/ARCHITECTURE.md`、`docs/API.md` |
| 配置 Tripo | `docs/TRIPO.md`、`.env.example` |
| 真正打印与记录证据 | `docs/HEYGEARS_WORKFLOW.md` |
| 采购和搭建电路 | `docs/HARDWARE.md`、`hardware/electronics/BOM.md`、`hardware/electronics/wiring.svg` |
| 设备联调 | `docs/DEVICE_PROTOCOL.md`、`hardware/firmware/include/config.example.h` |
| 机械、接口和装配 | `docs/PRINT_AND_ASSEMBLY.md`、`hardware/cad/interface.json` |
| 验证与安全 | `docs/TEST_REPORT.md`、`docs/SAFETY_AND_LIMITATIONS.md`，再看合集补充说明 |
| 展示、备份和运行 | `docs/DEMO_SCRIPT.md`、`docs/OPERATIONS.md` |
| 第三方来源与许可 | `docs/SOURCES.md`、`LICENSE` |

## 版本与证据

本次打包核对文件复制哈希和 ZIP 完整性，不将历史测试视为本次重新执行。原项目中的测试报告、时间、限制与日志均原样保留。历史截图不是当前版本功能通过验收的证据；硬件装配图不是实物照片。此前发现的结构问题尚未在此包中修复。
