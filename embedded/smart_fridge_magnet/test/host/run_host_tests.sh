#!/usr/bin/env bash
#
# run_host_tests.sh — build & run Smart Fridge Magnet host unit tests.
#
# These tests compile REAL production sources on a plain host with just gcc —
# no ESP-IDF, no hardware. Only the dependency-free pure-logic modules qualify:
# they include nothing but standard C headers, so `cc -I inc mod.c test.c` works.
#
# 历史说明：本目录的上一批测试（35 项，覆盖 film_validate / file_txn / sfm_crypto
# 等）在「精简测试」那次提交里被整体移除，运行器也一并删掉了。本文件按当时的约定
# 重建，当前接线下列 6 个纯逻辑模块。要恢复历史测试，把对应的 test_*.c 与
# fakes 目录取回后照下面的模式各加一段 build_and_run 即可。
#
# Currently covers:
#   - test_param_migration: v2 参数记录无损升级到 v3，保留配网、密文和同步版本
#   - test_film_set_txn   : 多图集合的文件名边界与掉电恢复决策
#   - test_sync_run_guard : 同步入口只允许一个占用者，leave 后可再次进入
#   - test_wake_schedule  : F3 —— 按饭点唤醒 + 兜底周期的纯决策逻辑
#       （跨零点、slot 等于当前分钟、多 slot 取最近、与兜底取较小值、
#         无时间表只用兜底、无时间表且无兜底返回 0、非法 slot 被忽略、60 秒下限）。
#   - test_manifest_items : F4 —— manifest items[] 一套多张解析与逐条校验
#       （4 条正常解析、缺 items 的向后兼容、条目 url/size/sha256/name 各类违规
#         返回可区分的错误码、超过 4 条整份拒绝），外加
#         manifest_contains_item_name() —— service_wifi.c「不删自己刚下载的文件」
#         那条不变量的判据（包含/不包含/NULL/item_count==0/大小写敏感/越界）。
#   - test_wifi_link_watch: 醒着期间的 Wi-Fi 链路自愈判定
#       （退避区间与指数增长、上限饱和与大 attempts 不溢出、判定优先级
#         未配网>链路正常>忙>退避、退避到上限后仍持续重试不放弃、
#         单调时钟回绕不卡住、链路正常时不动作）。
#   - test_film_pin       : 置顶自定义画面（手绘涂鸦 / 相册照片持续显示，BLE 0x4C/0x4D）
#       （四种 mode 的判定、WINDOW 的正常窗口/跨零点/边界/时间不可信、
#         OUTSIDE_MEAL 在窗口内外与时间不可信时的取舍、空文件名一律 false、
#         cfg_is_valid 的每条拒绝理由各一例、NULL 入参、全天扫描恰好一进一出）。
#   - test_sleep_decision : 深睡互斥判定
#       （NULL 禁睡、全假放行、六个活动标志各自及同时禁睡、事务终态不禁睡、
#         monitor_file_txn_in_progress 只认 RECEIVING/VALIDATING）。
#
# Usage:   ./test/host/run_host_tests.sh
# Exit:    0 = all tests passed, non-zero = failure.

set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJ="$(cd -- "${HERE}/../.." >/dev/null 2>&1 && pwd)"   # smart_fridge_magnet/
CC="${CC:-cc}"
BUILD_DIR="${HERE}/build_host"
CFLAGS=(-std=c11 -Wall -Wextra -Werror -g -O0)

mkdir -p "${BUILD_DIR}"
overall_rc=0

# build_and_run <name> <output-bin> <include-args...> -- <source-files...>
build_and_run() {
  local name="$1"; shift
  local bin="$1"; shift
  local includes=()
  while [[ "$1" != "--" ]]; do
    includes+=("$1"); shift
  done
  shift  # drop the "--"
  local srcs=("$@")

  echo "== building ${name} (${CC}) =="
  if ! "${CC}" "${CFLAGS[@]}" "${includes[@]}" "${srcs[@]}" -o "${bin}"; then
    echo "BUILD FAILED: ${name}"
    overall_rc=2
    return
  fi

  echo "== running ${name} =="
  if "${bin}"; then
    :
  else
    echo "TEST FAILED: ${name}"
    overall_rc=1
  fi
  echo ""
}

# ---- test 1: 按饭点唤醒 + 兜底周期（F3） ---------------------------------
# wake_schedule.c 只依赖 <stdint.h>/<stdbool.h>/<stddef.h>，无需任何 fake。
build_and_run \
  "test_wake_schedule" \
  "${BUILD_DIR}/test_wake_schedule" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/wake_schedule.c" \
  "${HERE}/test_wake_schedule.c"

# ---- test 2: manifest items[] 一套多张（F4） -----------------------------
# manifest_parse.c 内置的是依赖精简的手写 JSON 读取器（不依赖 cJSON），
# 故同样只需要组件的 include 目录。
build_and_run \
  "test_manifest_items" \
  "${BUILD_DIR}/test_manifest_items" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/manifest_parse.c" \
  "${HERE}/test_manifest_items.c"

# ---- test 3: 置顶自定义画面（涂鸦/照片持续显示） -------------------------
# film_pin.c 只依赖 <stdint.h>/<stdbool.h>/<stddef.h>/<string.h>，无需任何 fake。
build_and_run \
  "test_film_pin" \
  "${BUILD_DIR}/test_film_pin" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/film_pin.c" \
  "${HERE}/test_film_pin.c"

# ---- test 4: v2 -> v3 参数记录迁移 ---------------------------------------
build_and_run \
  "test_param_migration" \
  "${BUILD_DIR}/test_param_migration" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/param_serialize.c" \
  "${HERE}/test_param_migration.c"

# ---- test 5: 多图集合提交/回滚纯逻辑 ------------------------------------
build_and_run \
  "test_film_set_txn" \
  "${BUILD_DIR}/test_film_set_txn" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/film_set_txn.c" \
  "${HERE}/test_film_set_txn.c"

# ---- test 6: 同步入口 try-enter 状态转移 --------------------------------
build_and_run \
  "test_sync_run_guard" \
  "${BUILD_DIR}/test_sync_run_guard" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/wifi_sync_gate.c" \
  "${HERE}/test_sync_run_guard.c"

# ---- test 7: 醒着期间的 Wi-Fi 链路自愈判定 -------------------------------
build_and_run \
  "test_wifi_link_watch" \
  "${BUILD_DIR}/test_wifi_link_watch" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/wifi_link_watch.c" \
  "${HERE}/test_wifi_link_watch.c"

# ---- test 8: manifest_url 推导同源兄弟端点地址（applied / request-refresh）----
build_and_run \
  "test_sync_url" \
  "${BUILD_DIR}/test_sync_url" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/sync_url.c" \
  "${HERE}/test_sync_url.c"

# ---- test 9: manifest 里可选 firmware 对象的解析（Wi-Fi OTA）--------------
build_and_run \
  "test_manifest_firmware" \
  "${BUILD_DIR}/test_manifest_firmware" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/manifest_parse.c" \
  "${HERE}/test_manifest_firmware.c"

# ---- test 10: 深睡互斥判定与单次清醒绝对上限 -----------------------------
# sleep_decision.c 只依赖 <stdbool.h>/<stdint.h> 与纯枚举 file_txn.h。
build_and_run \
  "test_sleep_decision" \
  "${BUILD_DIR}/test_sleep_decision" \
  -I "${PROJ}/components/film_service/inc" \
  -- \
  "${PROJ}/components/film_service/src/sleep_decision.c" \
  "${HERE}/test_sleep_decision.c"

if [[ "${overall_rc}" -eq 0 ]]; then
  echo "ALL HOST TESTS PASSED"
else
  echo "HOST TESTS FAILED (rc=${overall_rc})"
fi
exit "${overall_rc}"
