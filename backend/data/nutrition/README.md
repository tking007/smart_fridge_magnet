# 营养数据

简体中文 | [English](README.en.md)

`china_food_composition.json` 是后端原型使用的完整营养数据表，共 1657 条记录。运行
`backend/scripts/setup_demo_data.py` 时，如果数据库尚无营养数据，脚本会优先导入本文件；
文件不存在时才使用脚本内的少量合成演示记录。

数据字段由 `backend/src/database.py::import_nutrition_data` 解析，包括食物编码、名称、可食部、
能量、宏量营养素、维生素和矿物质。该数据只用于原型查询和菜单提示，不构成医疗建议、诊断或
个体化营养处方。

本文件随公开快照发布；独立产品再利用时应核验数据表原始来源及适用的再分发条款。项目归属和
再分发说明见根目录 [`NOTICE.md`](../../../NOTICE.md)。
