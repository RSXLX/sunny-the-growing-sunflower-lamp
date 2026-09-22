# BOM / 原型采购规格

**无价格承诺；以下是工程选型约束，不是已购清单。选定实际模组后量尺寸再打印。**

| 编号 | 部件 | 数量 | 规格 / 注意 |
|---|---|---|---|
| U1 | ESP32 DevKit V1 / ESP32-WROOM-32 | 1 | 5V VIN, 3.3V GPIO; pin labels must match GPIO numbers |
| U2 | PN532 SPI module | 1 | 3.3V-compatible; measure actual PCB before editing 43 × 41 mm CAD assumption |
| TAG | NTAG213 NFC sticker | 3 | genuine writable Type-2; at least 48 bytes writable user area; max 22 × 22 mm target |
| PS1 | External isolated regulated supply | 1 | 5V / 2A; purchased enclosed supply; no mains wiring |
| F1 | Inline fuse + holder | 1 | 1.5A nominal prototype target; assess inrush and wire rating |
| P1 | Reverse-polarity protection module | 1 | 5V-compatible; verified current rating and pinout |
| L1 | Current-limited warm-white LED module | 1 | 5V rated, ≤150 mA full drive; no bare high-power LED |
| L2 | Current-limited projection LED module | 1 | 5V rated, ≤200 mA full drive; small apparent source; shield direct view |
| Q1,Q2 | Logic-level N-MOSFET stage | 2 | RDS(on) specified at 2.5V or 3.3V gate drive; use module/breakout |
| RG1,RG2 | 100 Ω gate resistor | 2 | one per gate |
| RPD1,RPD2 | 100 kΩ gate pull-down | 2 | gate to source |
| RT | 10 kΩ 1% resistor | 1 | 3V3 to ADC node |
| NTC1 | 10 kΩ B3950 thermistor | 1 | insulated leads, thermal coupling to hotspot; calibrate against reference |
| CT | 100 nF capacitor | 1 | ADC node to GND |
| SW1 | Momentary normally-open panel button | 1 | CAD bore 8.2 mm is an assumption; modify to real switch |
| OPT1 | Convex lens | 1 | diameter 25 mm; nominal focal length about 35 mm; real focusing required |
| DIFF1 | Diffusion film/disc | 1 | cut 55.5 mm nominal; confirm heat suitability; not structural |
| STEM | Metal tube | 1 | OD12 / ID8 / length174 mm; deburr; insulated cable entry |
| FAST | M3 bolts, washers and nuts | set | retainer nominal M3×45; bracket M3×10; clamp M3×35; select after fit check |
| BASE | Nonconductive ballast + non-slip feet | set | secure ballast to base; check full-assembly tipping; not included in STL |
| HARNESS | Wire, insulated joints, grommets, zip ties | set | wire size/rating suitable for ≤1.5A fused branch; avoid bare Dupont joints in final demo |
| PRINT | Printed mechanical parts | 17 | 14 common parts + 3 crown variants; geometric checks only |

## 电源预算（设计估计，非测量）

以 ESP32/Wi-Fi 瞬时 0.50 A、PN532 0.15 A、主照明 0.15 A、投影 0.20 A 作为保守选型预算，总计 1.00 A；5 V / 2 A 电源留余量。实际峰值、模组效率、保险丝时间曲线与线材温升须上电测量。PWM 限幅不替代 LED 模块自身限流。

不提供 PCB Gerber：此 MVP 采用可采购模块 + 接线板 / 线束装配。wiring.svg 和 netlist.json 为电气连接设计，不能当成已认证电路板。