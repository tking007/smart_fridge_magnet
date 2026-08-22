# 变更记录

简体中文 | [English](CHANGELOG.en.md)

智能冰箱贴的所有重要变更记录在此。本文格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- 项目统一命名为 Smart Fridge Magnet / 智能冰箱贴。
- 将组织专属的包名、服务、域名、模型名、设备标识和部署配置替换为公开配置。
- 删除非公开项目材料、部署产物、生成文件、本地数据和原仓库 Git 历史。
- 恢复营养数据表，并将其导入路径与数据用途写入公开说明。
- 固件默认只使用 ESP-IDF 公共 CA；局域网自签 CA 改为本地显式构建配置。
- BLE 控制特征改为 Secure Connections bonding 后的加密访问，并在恢复出厂时清除 bond。
- 恢复用户确认来自公开来源的食材/分类 SVG、Android 派生 PNG 和角色 PNG，并保留映射说明。
- 诊断健康接口改为业务 Token 保护，`/healthz` 保留为最小存活探针。
- 上传图片、第三方条码响应和错误输出增加大小或内容边界；设备 Token 改为哈希 verifier。
- Android 升级到 Kotlin 2.3.21 / Compose BOM 2025.08.00，删除未使用依赖和存储权限并恢复食材图标。
- BLE 远程 SD 卡格式化命令改为安全拒绝；固件序列号由设备 MAC 派生。

### 新增

- 增加公开的贡献指南、安全策略、许可证和上游归属说明。
- 增加固件公开构建基线、真机验证清单和版本校验脚本。
- 增加 GitHub Actions 的后端、Android、固件 host 和仓库结构检查。
