# 项目规范（AGENTS.md）

> 本文件是本仓库的强制规范。分区细则见 `android/AGENTS.md`、`backend/AGENTS.md`、
> `embedded/AGENTS.md`；与本文件冲突的历史做法以本文件为准。

## 1. 通用原则

- 只做与任务相关且完整的改动，不留半成品。
- 改动前后运行相关测试或构建；跨端字段与协议变更必须同步实现并更新契约。
- 优先使用项目已有工具和生态工具，验证后再声明完成。

## 2. 注释与文档

- 注释只解释原因和不显然之处，设计沿革写入 `CHANGELOG.md` 或 `docs/`。
- 禁止提交被注释掉的死代码、无主 TODO 和与实现不一致的注释。
- 公共接口用简短 docstring/KDoc 说明用途、参数和返回值。
- 同一事实只在一处权威描述，其余文档链接到该处。

## 3. 提交与分支

- `main` 只接收可验证改动；较大变更使用独立分支和合并请求。
- 提交信息遵循 Conventional Commits（`feat:`、`fix:`、`docs:`、`chore:` 等）。
- 三端变更统一记录在根目录 `CHANGELOG.md`。

## 4. 安全红线

- 禁止提交密钥、令牌、签名文件、SQLite 运行库、日志、上传图片、IDE 缓存和构建产物。
- 所有外部凭据通过环境变量提供，不得硬编码或提交到 Git。
- App 不得内置后端 Token；任何已泄露凭据必须立即轮换。

## 5. 跨端不变量

- 渲染唯一实现在后端 `backend/src/menu_render.py` 与 `film_encoder.py`；App 预览解码后端产物。
- ESP32 只接收裸像素或文件，不解析菜谱 JSON，不承担中文或 Emoji 排版。
- 三端规格统一为六色电子纸 792 x 528、`209120` 字节。
- App 的 AI 请求统一经后端处理，不在客户端持有模型凭据。
- 菜谱主料必须来自库存，库存不足时减少菜品，不生成采购清单。

## 6. 验证

- 后端：`cd backend && python -m pytest`；`ruff check src tests scripts`。
- Android：`cd android && ./gradlew testDebugUnitTest`；必要时运行 `:app:assembleDebug` 和 `lintDebug`。
- 固件：加载 ESP-IDF 环境后运行 `idf.py build`；真机步骤见 `embedded/README.md`。
- 版式输出发生变化时递增渲染版本号。

## 7. Android UI

- 业务屏幕使用 `ui/theme/` 的设计 token 和 `ui/components/` 的公共组件。
- 禁止在业务屏幕重复实现已有组件；新样式先进入主题或组件库。
- 每个屏幕统一处理空态、错误态、加载态和返回逻辑。

## 8. 目录

```text
android/      Kotlin / Compose 应用
backend/      Flask / AI / 渲染 / 调度
embedded/     ESP32 / ESP-IDF 固件
hardware/     硬件基线与许可证
docs/         跨端契约与公开文档
CHANGELOG.md  三端共用变更记录
```
