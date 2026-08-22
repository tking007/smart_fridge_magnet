#!/usr/bin/env bash
#
# 一键构建智能冰箱贴 Android APK
#
# 用法：
#   ./build-apk.sh              # 构建 debug APK（默认，可直接安装调试）
#   ./build-apk.sh release      # 构建 release APK（未签名，除非配置了签名）
#   ./build-apk.sh debug -i     # 构建完成后自动安装到已连接设备
#   ./build-apk.sh clean        # 清理构建产物
#
# 说明：
# - 始终通过项目自带的 ./gradlew（Gradle 8.13）构建，不使用系统 gradle。
#   项目要求 AGP 8.13.2，与系统上可能存在的 Gradle 9.x 不保证兼容；用 wrapper
#   才能保证本机、队友和 CI 构建出同样的产物。
# - 需要 ANDROID_HOME 或 android/local.properties 中的 sdk.dir 指向 Android SDK。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------- 输出着色（非 TTY 时自动关闭，便于 CI 日志与重定向） ----------
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_INFO=$'\033[36m'; C_OK=$'\033[32m'
    C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
else
    C_RESET=''; C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''
fi
info()  { printf '%s\n' "${C_INFO}==>${C_RESET} $*"; }
ok()    { printf '%s\n' "${C_OK}✓${C_RESET} $*"; }
warn()  { printf '%s\n' "${C_WARN}!${C_RESET} $*"; }
die()   { printf '%s\n' "${C_ERR}✗${C_RESET} $*" >&2; exit 1; }

# ---------- 参数解析 ----------
BUILD_TYPE="debug"
DO_INSTALL=0
EXTRA_ARGS=()

for arg in "$@"; do
    case "$arg" in
        debug|release)  BUILD_TYPE="$arg" ;;
        clean)          BUILD_TYPE="clean" ;;
        -i|--install)   DO_INSTALL=1 ;;
        -h|--help)
            # 只打印文件头部的注释块（到第一个非注释行为止）
            sed -n '2,${/^#/!q;p;}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)              EXTRA_ARGS+=("$arg") ;;   # 其余参数透传给 Gradle
    esac
done

# ---------- 环境检查（尽早失败并给出可操作的提示） ----------
[ -x ./gradlew ] || die "找不到 ./gradlew。请在 android/ 目录下运行本脚本。"

if ! command -v java >/dev/null 2>&1; then
    die "未找到 java。Android Gradle Plugin 8.13 需要 JDK 17 或更高版本。"
fi
JAVA_MAJOR="$(java -version 2>&1 | sed -n '1s/.*version "\([0-9]*\).*/\1/p')"
if [ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -lt 17 ]; then
    die "检测到 JDK $JAVA_MAJOR，AGP 8.13 需要 JDK 17+。"
fi

# SDK 位置：ANDROID_HOME 环境变量 或 local.properties 的 sdk.dir
SDK_DIR="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$SDK_DIR" ] && [ -f local.properties ]; then
    SDK_DIR="$(sed -n 's/^sdk\.dir=//p' local.properties | head -1)"
fi
[ -n "$SDK_DIR" ] || die "未找到 Android SDK。请设置 ANDROID_HOME，或在 android/local.properties 写入 sdk.dir=/path/to/Sdk"
[ -d "$SDK_DIR" ] || die "Android SDK 路径不存在：$SDK_DIR"

info "SDK      : $SDK_DIR"
info "JDK      : $(java -version 2>&1 | head -1)"
info "构建类型 : $BUILD_TYPE"

# ---------- clean ----------
if [ "$BUILD_TYPE" = "clean" ]; then
    info "清理构建产物…"
    ./gradlew clean "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
    ok "已清理"
    exit 0
fi

# ---------- 构建 ----------
if [ "$BUILD_TYPE" = "release" ]; then
    GRADLE_TASK="assembleRelease"
    APK_DIR="app/build/outputs/apk/release"
else
    GRADLE_TASK="assembleDebug"
    APK_DIR="app/build/outputs/apk/debug"
fi

info "执行 ./gradlew $GRADLE_TASK …"
START_TS=$(date +%s)
./gradlew "$GRADLE_TASK" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
ELAPSED=$(( $(date +%s) - START_TS ))

# ---------- 定位产物 ----------
APK_PATH="$(find "$APK_DIR" -maxdepth 1 -name '*.apk' -print -quit 2>/dev/null || true)"
[ -n "$APK_PATH" ] || die "构建结束但未找到 APK，请检查上方 Gradle 输出。"

APK_SIZE="$(du -h "$APK_PATH" | cut -f1)"
ok "构建完成（${ELAPSED}s）"
printf '  APK : %s\n' "$SCRIPT_DIR/$APK_PATH"
printf '  大小: %s\n' "$APK_SIZE"

if [ "$BUILD_TYPE" = "release" ]; then
    case "$APK_PATH" in
        *unsigned*) warn "release 包未签名，无法直接安装。需要签名请配置 app/build.gradle.kts 的 signingConfigs。" ;;
    esac
fi

# ---------- 可选安装 ----------
if [ "$DO_INSTALL" = "1" ]; then
    ADB="$SDK_DIR/platform-tools/adb"
    [ -x "$ADB" ] || ADB="$(command -v adb || true)"
    [ -n "$ADB" ] || die "未找到 adb，无法安装。"

    DEVICE_COUNT="$("$ADB" devices | grep -cw 'device' || true)"
    if [ "$DEVICE_COUNT" -lt 1 ]; then
        die "没有已连接的设备/模拟器（adb devices 为空）。手机需开启 USB 调试。"
    fi
    info "安装到设备…"
    "$ADB" install -r "$APK_PATH"
    ok "已安装"
fi

printf '\n手动安装：%s install -r %s\n' "${SDK_DIR}/platform-tools/adb" "$APK_PATH"
