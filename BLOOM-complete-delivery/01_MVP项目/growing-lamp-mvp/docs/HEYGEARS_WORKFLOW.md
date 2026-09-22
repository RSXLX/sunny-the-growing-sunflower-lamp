# HeyGears / Blueprint 人工制造流程

本版**没有虚构的打印API**。已交付模型、制造清单、哈希、结构源、投影片和操作步骤。导入、排布、切片、打印与后处理由操作员在真实设备和Blueprint中完成；网页状态由操作员明确记录。

## 操作流程
在我的衣橱下载制造ZIP，先检查manifest、模型单位、源版本和人工检查记录。Tripo的raw.glb是生成原稿，不自动当成制造文件；优先使用已审查crown.stl。

把STL导入Blueprint，核对毫米、实际尺寸、朝向、壁厚、孤立组件、排液/清洁可达性、支撑触点和装配关键面。使用与实际设备/材料匹配的工艺配置，先打印尺寸试片。软件检测通过也不能代替工程判断。

制作前记录设备型号、材料型号/批次、切片软件版本、配置名称、文件SHA256与任务编号。保存实际排布截图。打印后按材料要求清洗、固化、去支撑，记录最终尺寸与缺陷，不编造统一曝光参数。

真正完成后，在网页把实例从待制作标为已打印；实际装配检查完成后再标为检查通过。NFC写入和读回由设备另外产生绑定事件，不因为人工标记打印就自动绑定。

## 建议证据目录

```text
physical-evidence/<instance-id>/
  job-notes.md                设备/材料/时间/操作者
  input-manifest.json         文件与哈希
  slicer-layout.png           实际排布
  printed-part.jpg            实际后处理件
  caliper-measurements.md     接口尺寸记录
  assembled-lamp.jpg          同一底座上的实际装配
  projection-video.mp4        原始光影视频，注明环境与距离
  temperature-and-nfc.csv     实测数据，不用参数化演示替代
```

本包不预填这些不存在的实物证据。比赛展示时不要把其他打印机制造的件称为HeyGears制造，也不要把录制过程伪装成现场几秒完成打印。
