# 项目文档

简体中文 | [English](README.en.md)

本仓库对每个主题只维护一份权威文档：

| 主题 | 文档 |
| --- | --- |
| 跨端字段、鉴权、BLE、manifest 和 `.film` | [跨端契约](contracts/README.md) |
| Android 构建与页面职责 | [Android 说明](../android/README.md) |
| 后端配置、API 与部署 | [后端说明](../backend/README.md) |
| 营养数据来源与导入 | [来源与署名](../NOTICE.md) / [演示数据脚本](../backend/scripts/setup_demo_data.py) |
| 固件构建、同步与低功耗 | [固件说明](../embedded/README.md) |
| 固件构建基线与真机验证 | [BUILD](../embedded/smart_fridge_magnet/BUILD.md) / [验证清单](../embedded/VERIFICATION.md) |
| BLE 字节协议 | [BLE 协议](../embedded/smart_fridge_magnet/docs/ble_protocol.md) |
| 功耗模型与测量边界 | [功耗模型](power-model.md) |
| 硬件来源与原型范围 | [硬件说明](../hardware/README.md) |
| 发布变化 | [变更记录](../CHANGELOG.md) |

跨端协议变化必须同步更新跨端契约。可执行命令只放在所属模块 README 中，其它文档通过链接
引用，避免重复说明逐渐不一致。
