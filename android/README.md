# Android App

Android 客户端使用 Kotlin、Jetpack Compose 和统一设计系统，负责食材录入、库存管理、家庭
画像、菜谱交互、设备状态展示、BLE 配网与设备操作。AI 识别和菜谱生成统一经后端完成，
APK 不持有模型密钥。

## 当前体验

- 首页在标准手机首屏展示问候、设备摘要、紧凑库存入口和家庭成员摘要。
- 完整库存位于 `AppSheet` 抽屉，支持件数、重量、容量、保质期、增删改和同步重试。
- 拍照、条码和手动录入进入统一确认流程；识别可信度与保质期状态分开显示。
- 菜谱页读取后端方案，流式思考区域占据剩余屏幕并保留最近 1200 字。
- 设备页区分云端最近快照和 BLE 实时状态；超过 36 小时的快照标记可能过期。
- 设备预览固定按实体竖向展示，缩略图和全屏查看器使用统一加载与错误状态。
- 版式应用由后端同时重渲染完整四张菜单，不再要求用户额外点击一次发布。

## 架构边界

```text
ui/          Compose 页面、设计系统和交互状态
data/        本地模型、Gson 存档、食材图标和同步状态
network/     后端 DTO、HTTP/SSE 客户端、库存同步
transfer/    BLE 协议、连接、文件和设备命令
film/        仅解码后端 .film，用于预览
device/      唤醒时间表计算
audio/       PCM 录音与 WAV 封装
barcode/     ZXing 一维商品码识别
```

重要约束：

- `ui/theme/` 和 `ui/components/` 是颜色、间距、圆角、动效和控件的唯一来源。
- App 不生成 `.film`，只请求或解码后端产物。
- 业务网络配置存放在开发者选项，不硬编码 Token。
- 只有 BLE 已连接时才开放 Wi-Fi 当前状态、文件管理、配置写入和立即同步等实时操作。

跨端字段见 [../docs/contracts/README.md](../docs/contracts/README.md)。

## 开发环境

- [Android Studio](https://developer.android.com/studio)（推荐），或
  [Android SDK Command-Line Tools](https://developer.android.com/tools/sdkmanager)
- JDK 17 或更高版本；项目已使用 OpenJDK 22 验证，Java/Kotlin 编译目标均为 JVM 17
- Android SDK Platform 36（`compileSdk` 为 36，`targetSdk` 为 34）
- Android SDK Build-Tools 36 与 Android SDK Platform-Tools
- 最低运行版本为 Android 8.0 / API 26；构建时不需要额外安装 SDK Platform 26

项目没有本地 C/C++ 模块，构建 APK 不需要 NDK 或 CMake。Android Emulator 和系统镜像也不是构建依赖，只在需要模拟器运行时安装。

### 使用 Android Studio

1. 安装 Android Studio 并打开 `android/` 目录。
2. 在 SDK Manager 中安装 Android SDK Platform 36、SDK Build-Tools 36 和
   SDK Platform-Tools。
3. 等待 Gradle Sync 自动下载 AndroidX、Compose、OkHttp、Coil、ZXing 等依赖。

### 使用命令行

先安装 JDK 与 Android SDK Command-Line Tools，然后配置：

```bash
export JAVA_HOME="/path/to/jdk-17-or-newer"
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

sdkmanager --licenses
sdkmanager "platforms;android-36" "build-tools;36.0.0" "platform-tools"
```

如果 SDK 不在 `$ANDROID_HOME`，可在 `android/local.properties` 写入绝对路径；该文件已被
`.gitignore` 排除，不能提交：

```properties
sdk.dir=/absolute/path/to/Android/Sdk
```

项目固定使用 Android Gradle Plugin `8.13.2`、Kotlin `2.3.21` 和 Gradle Wrapper `8.13`，不需要安装系统级 Gradle。首次构建需要联网下载 Gradle 分发包和 Maven 依赖。

### 构建

```bash
cd android
./build-apk.sh
```

常用命令：

```bash
./build-apk.sh                 # Debug APK
./build-apk.sh release         # Release APK；未配置签名时为未签名包
./build-apk.sh debug -i        # 构建并通过 adb 安装到已连接设备
./build-apk.sh clean           # 清理 Gradle 模块构建产物
./gradlew testDebugUnitTest    # JVM 单元测试
```

Debug APK 位于 `app/build/outputs/apk/debug/`，构建产物不进入 Git。

## 连接后端

在设置页连续点击版本号 5 次打开开发者选项，填写：

- HTTPS 后端地址，例如 `https://host:8443`
- `app_token`
- `device_id`
- 首次登记或轮换时使用的 `device_token`

原生 Android 不依赖浏览器 CORS。设备的 `manifest_url` 由后端地址和 `device_id` 推导，
通过 BLE 写入固件；同一 `device_token` 需要同时登记到后端。

## 设备与低功耗

App 根据家庭用餐时间和提前量计算完整唤醒表并通过 BLE 下发。设备默认每天三次计划唤醒，
正常情况下每天只完成一次联网同步，午餐和晚餐从本地缓存切图。未连接 BLE 时配置页显示
云端只读快照，操作控件保持禁用，并提示按 FN 键唤醒设备。

## 测试

```bash
cd android
./gradlew testDebugUnitTest
./gradlew :app:assembleDebug
```

重点单测覆盖 DTO、容量单位、保质期、旧存档迁移、唤醒计划、BLE 协议、Film 解码和页面纯
逻辑。Compose 截图和动效仍需在 `360x800dp`、`412x915dp`、浅色/深色及长内容场景人工验收。

## 常见问题

- 后端未配置：识别、菜谱、云端状态和版式预览不可用，先检查开发者选项。
- 扫不到设备：设备深睡时不广播，按 FN 打开 60 秒窗口；长按 FN 可延长到 180 秒。
- 版式已更新但屏幕未变化：后端已发布新四张，设备需在下一次唤醒或手动同步后下载。
- 预览与真机不一致：确认预览来自后端版本文件，不要使用本地示意图或重新编码结果。
