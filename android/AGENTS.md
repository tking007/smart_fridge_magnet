# Android 分区规范（AGENTS.md）

> 继承根目录 `../AGENTS.md`，本文件只描述 Android 特有规则。

## 设计系统

- 统一主题位于 `app/src/main/java/org/smartfridgemagnet/ui/theme/`。
- 应用入口使用 `AppTheme {}`；业务屏幕使用 `ui/components/` 的公共组件。
- 颜色、间距、圆角与字号必须引用设计 token，不在业务屏幕硬编码。

## 结构与约束

- 屏幕文件保持聚焦，可复用块提取为组件或私有 `@Composable`。
- 值得跨配置保留的状态使用 `rememberSaveable`，短暂加载状态不持久化。
- 网络 DTO 位于 `network/`，跨端字段变化同步更新 `docs/contracts/`。
- 后端地址与 App Token 由开发者设置提供，App 不持有 AI 服务密钥。
- 预览必须通过 `FilmDecoder` 解码后端产物，保证与设备逐像素一致。

## 验证

- 运行 `./gradlew testDebugUnitTest`。
- 编译验证运行 `./gradlew :app:assembleDebug`；发布前额外运行 `./gradlew lintDebug`。
