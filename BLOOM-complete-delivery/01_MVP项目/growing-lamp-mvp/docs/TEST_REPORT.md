# 本次验证报告 / 2026-09-20

此报告区分“代码/文件存在”“已执行测试”和“真实设备验证”。测试数据是临时生成的演示数据，不是用户真实日记；运行时数据库、会话、设备token不随ZIP交付。

## 已执行

| 项目 | 结果 | 原始证据 |
|---|---|---|
| Python应用与真实本机HTTP测试 | **37项通过** | `evidence/python-tests.txt`、`evidence/check-all.txt` |
| 前端JS语法 | app.js、viewer.js通过node --check | `evidence/check-all.txt` |
| 六页UI与交互流程 | **16个检查点通过，无未捕获JS异常**；特殊环境见下文 | `evidence/browser-tests.json`、工作台截图 |
| 实际STL读取与三维预览 | 通过软件三维绘制路径读取实际模型，不是静态模型图片 | UI截图、browser-tests.json |
| 手机390px布局 | 无横向溢出 | `evidence/workbench-mobile.png` |
| 标准CAD导出 | OpenSCAD成功导出14个通用/试验零件 | `hardware/exports/*.log` |
| 全部STL几何检查 | **17个STL：边计数封闭，0个退化面，正有向体积** | `evidence/all-stl-validation.json` |
| 固件纯C++逻辑 | g++严格警告编译并执行通过 | `evidence/check-all.txt` |

## 后端测试覆盖
真实HTTP静态资源、账号密码、Cookie属性、CSRF、跨站写入、Host限制、路径遍历、私有记忆隔离、公开设计权限、设计文件权限、幂等生成、模型变化和闭合、人工检查与发布授权、来源继承、删除记忆保留外装、设备token、初始离线、模拟/真实隔离、绑定读回与命令对应、事件去重、命令覆盖/超时/回执、数据重启恢复、缺失Tripo密钥、未知计费提交不重复、恢复已有任务、Tripo请求合同、下载协议限制、最小GLB节点变换。

固件纯逻辑测试覆盖NDEF往返、损坏头部、写入中断、实例ID校验、温度换算、无传感器禁止输出、温度锁定/手动恢复、PWM限幅和millis回绕。**它没有编译Arduino、Wi-Fi、PN532与FreeRTOS驱动，不能替代板级构建与烧录。**

## 浏览器环境限制
测试环境：Python3.13.5、Node22.16.0、OpenSCAD2021.01、Chromium144；具体调用记录在evidence。

本环境的受管理Chromium禁止所有URL导航，直接访问本地服务返回 `ERR_BLOCKED_BY_ADMINISTRATOR`，且WebGL上下文不可用。没有更改或关闭这些管理设置。

因此执行的是：Playwright将本项目HTML/CSS/JS放入浏览器内存中渲染；经Python本地HTTP桥接器操作同一个实际后端；前端软件三维绘制读取实际STL。登录、表单、任务、下载、模拟绑定、发布、第二账号二创和移动布局在此模式下测试。账户Cookie、CSRF和网络协议另由真实localhost HTTP测试验证。

**尚未通过：浏览器原生网络/ES模块加载/CSP执行的完整端到端测试、WebGL GPU路径、Safari及其他实际设备兼容性。**代码包含原生HTTP浏览器测试脚本，可在普通开发电脑运行：

```bash
# 在已安装Playwright及Chromium的开发环境中，先启动工作台
python3 tests/browser_smoke.py --url http://127.0.0.1:8787
```

当前证据对应的受限环境模式可用 `--in-memory` 复现。它是明确标注的测试适配，不是生产代码要求，也不是对管理策略的修改。

## 未执行，不能写为通过

| 项目 | 状态 / 原因 |
|---|---|
| Tripo付费真实生成 | 无实际API密钥；只做无网络合同与异常分支测试 |
| HeyGears真实打印 | 没有打印机/材料；制造文件与人工流程已交付 |
| ESP32完整PlatformIO构建 | 本环境无PlatformIO/嵌入式工具链，网络依赖不可下载；CI配置已写但未执行 |
| ESP32烧录与真实设备回执 | 没有连接的ESP32与PN532 |
| NFC真实写入、读距、成功率 | 未实测；协议和NDEF核心已测 |
| 实体装配、树脂强度与收缩 | 未制造；尺寸为原型设计假设 |
| 真正光影与灯具照度 | 未测；网页光影不是实拍 |
| 热、倾倒、长期运行与电气认证 | 未测，不允许作为已验证成品使用 |
| Docker镜像构建及GitHub Actions | 配置已交付，未执行 |

## 如何复测
`python3 tools/check.py` 会执行现有环境能够运行的检查，缺少node/g++时明确SKIP。`python3 tools/export_cad.py` 重新导出CAD。平台完整构建使用 `pio run -d hardware/firmware`。实物部分按HARDWARE.md分阶段上电，记录测量与照片，不用数字测试替代物理证据。

ZIP最终完整性与重新解压启动结果见 `evidence/package-handoff.json`（打包阶段生成）。
