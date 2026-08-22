# 来源与署名

简体中文 | [English](NOTICE.en.md)

智能冰箱贴是独立开源项目。构建和运行本仓库不依赖任何公司或产品名称。

除非具体文件或子目录另有说明，本仓库中的源代码、文档和项目资产采用
[GPL-3.0-or-later](LICENSE) 发布。

## FrameFilm

`embedded/smart_fridge_magnet/` 中的部分固件，以及 `hardware/` 中的硬件快照，源自
[FrameFilm](https://github.com/kirmisaki/FrameFilm) 开源项目：

- 固件导入基线：`562f3300774ee512061abe1e8589a4477e39afb0`
- 硬件快照导入提交：`514b0361a570452f50d320a41ca61d6a9a30bf82`
- 上游许可证：[GNU General Public License v3.0](LICENSE)

固件目录继续保留[详细上游说明](embedded/smart_fridge_magnet/NOTICE.md)。相应许可证全文保留在
`embedded/smart_fridge_magnet/LICENSE` 和 `hardware/LICENSE`。

`backend/data/nutrition/china_food_composition.json` 是本项目明确纳入公开快照的营养数据表，
随本仓库发布。再分发时请保留该文件及本说明；营养数据仅用于原型查询和菜单提示，不构成医疗
建议或营养诊断。若将数据用于独立产品，请按该数据表的原始来源和适用法律另行核验再分发范围。

`Icon/` 中保留了用户确认来自公开来源的食材/分类 SVG、Android 派生 PNG 和角色 PNG，
并由 `Icon/README.md` 与 `food_map.json` 维护映射。由于本快照未能为每个素材建立独立来源
清单，后续再分发时应按各素材原始页面补充对应署名和许可证；第三方依赖仍分别受其包内许可
证和依赖元数据约束。
