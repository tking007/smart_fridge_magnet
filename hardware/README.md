# 智能冰箱贴硬件基线

本目录保存智能冰箱贴样机的硬件基线，以及来自 FrameFilm Pro 开源项目的 PCB 快照、结构件
和许可证。上游名称只用于来源追溯，不作为当前产品或协议名称；本项目不宣称原创上游 PCB、
原理图或外壳。

## 来源

| 项目 | 记录 |
| --- | --- |
| 上游 | [kirmisaki/FrameFilm](https://github.com/kirmisaki/FrameFilm) |
| 导入提交 | `514b0361a570452f50d320a41ca61d6a9a30bf82` |
| PCB tree | `d1f850e57bfd41a986888b852218653e45be7e37` |
| Model tree | `88cffbb4a1766b74b0967fbb4cfea1c38cb663e9` |
| 许可证 | [GNU GPL v3](LICENSE) |

## 样机规格

| 项目 | 值 |
| --- | --- |
| 当前产品 | 智能冰箱贴六色电子纸 |
| 上游硬件分支 | FrameFilm Pro |
| 主控 | ESP32-S3 mini N4R2 |
| 屏幕 | 3.68 英寸六色电子纸，792 x 528，SE0368-C |
| 存储 | 板载 SDnand |
| 交互 | 三个独立按键 |
| 电池 | 244147 可充电锂电池 |
| 软件格式 | 智能冰箱贴 `.film`，209120 字节 |

Android、后端和固件已统一到当前规格；App 不再编码 `.film`，后端是唯一渲染实现。

## 目录

```text
pcb/FrameFilmPro/Schematic_framefilmpro_1.1.pdf
model/SmartFridgeMagnet/外壳/外壳.stl
model/SmartFridgeMagnet/按键/按键.stl
model/SmartFridgeMagnet/面板/
model/SmartFridgeMagnet/电池/
LICENSE
```

当前快照不包含可编辑的立创 EDA 工程、PCB 布局、完整 BOM、Gerber、坐标或钢网文件。
生产前从 [FrameFilm Pro 立创开源项目](https://oshwhub.com/kiritro/project_wgzqduhs)
核对最新修订，并按实物丝印、采购单和焊接记录建立本地制造基线。

## 维护要求

- 修改按键、电源、SDnand 或 EPD 驱动时记录对应 PCB/样机修订。
- 新增生产资料前删除姓名、手机号、地址和订单号等个人信息。
- 修改上游文件时保留来源、提交和 GPLv3 声明，并标记本项目改动。
- 固件构建与真机验收以 [../embedded/README.md](../embedded/README.md) 为准。
