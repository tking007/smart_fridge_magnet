/*********************************************************************
 * Host test: 深睡互斥纯决策逻辑（sleep_decision.c）。
 *
 * Part of the Smart Fridge Magnet project (based on FrameFilm 562f330,
 * GPL-3.0-or-later — see ../../NOTICE.md and LICENSE).
 *
 * 在普通主机上编译【真实的生产源码】sleep_decision.c（该模块只依赖
 * <stdbool.h> 与纯枚举 file_txn.h，不需要 ESP-IDF、FreeRTOS 或电源 HAL），
 * 锁住 sleep_decision.h 写明的语义：
 *
 *   1) act == NULL -> 禁睡（状态未知时绝不深睡）；
 *   2) 全部活动标志为假 -> 放行；
 *   3) 任一活动标志为真 -> 禁睡（六个标志逐个各一例）；
 *   4) 全部活动同时为真仍禁睡，避免超时路径并发拆除驱动；
 *   5) monitor_file_txn_in_progress() 只认 FT_RECEIVING / FT_VALIDATING。
 *
 * TEST-ONLY. NOT part of the firmware build.
 *********************************************************************/
#include <stdio.h>

#include "sleep_decision.h"

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, msg)                                                        \
    do {                                                                        \
        if (cond) {                                                             \
            printf("  PASS: %s\n", (msg));                                      \
            g_pass++;                                                           \
        } else {                                                                \
            printf("  FAIL: %s  (%s:%d)\n", (msg), __FILE__, __LINE__);         \
            g_fail++;                                                           \
        }                                                                       \
    } while (0)

/* 一个完全空闲的基线，各用例只改动自己关心的那一个字段。 */
static monitor_activity_t idle_baseline(void)
{
    monitor_activity_t act = {
        .ble_connected = false,
        .ble_receiving = false,
        .wifi_busy = false,
        .file_txn_state = FT_IDLE,
        .epd_refreshing = false,
        .ble_adv_window_active = false,
    };
    return act;
}

static void test_null_and_idle(void)
{
    printf("Test: NULL 入参与完全空闲\n");

    CHECK(!monitor_can_deep_sleep(NULL), "act == NULL -> 禁睡");

    monitor_activity_t act = idle_baseline();
    CHECK(monitor_can_deep_sleep(&act), "全部标志为假 -> 放行");
}

/* 六个活动标志各自单独为真时都必须禁睡：任何一个漏掉，对应的那类进行中操作
 * （传图/刷屏/联网）就会被深睡打断。 */
static void test_each_flag_blocks(void)
{
    printf("Test: 每个活动标志单独为真都禁睡\n");

    monitor_activity_t act;

    act = idle_baseline();
    act.ble_connected = true;
    CHECK(!monitor_can_deep_sleep(&act), "ble_connected -> 禁睡");

    act = idle_baseline();
    act.ble_receiving = true;
    CHECK(!monitor_can_deep_sleep(&act), "ble_receiving -> 禁睡");

    act = idle_baseline();
    act.wifi_busy = true;
    CHECK(!monitor_can_deep_sleep(&act), "wifi_busy -> 禁睡");

    act = idle_baseline();
    act.epd_refreshing = true;
    CHECK(!monitor_can_deep_sleep(&act), "epd_refreshing -> 禁睡");

    act = idle_baseline();
    act.ble_adv_window_active = true;
    CHECK(!monitor_can_deep_sleep(&act), "ble_adv_window_active -> 禁睡");

    act = idle_baseline();
    act.file_txn_state = FT_RECEIVING;
    CHECK(!monitor_can_deep_sleep(&act), "file_txn_state=FT_RECEIVING -> 禁睡");

    act = idle_baseline();
    act.file_txn_state = FT_VALIDATING;
    CHECK(!monitor_can_deep_sleep(&act), "file_txn_state=FT_VALIDATING -> 禁睡");
}

/* 事务终态不该阻止深睡：把 COMMITTED/ABORTED 也算「进行中」会让设备在一次
 * 正常提交后再也不睡。 */
static void test_txn_terminal_states_allow_sleep(void)
{
    printf("Test: 文件事务终态不禁睡\n");

    monitor_activity_t act;

    act = idle_baseline();
    act.file_txn_state = FT_COMMITTED;
    CHECK(monitor_can_deep_sleep(&act), "FT_COMMITTED -> 放行");

    act = idle_baseline();
    act.file_txn_state = FT_ABORTED;
    CHECK(monitor_can_deep_sleep(&act), "FT_ABORTED -> 放行");
}

static void test_all_flags_block(void)
{
    printf("Test: 全部活动标志同时为真仍禁睡\n");

    monitor_activity_t act = idle_baseline();
    act.ble_connected = true;
    act.ble_receiving = true;
    act.wifi_busy = true;
    act.epd_refreshing = true;
    act.ble_adv_window_active = true;
    act.file_txn_state = FT_RECEIVING;
    CHECK(!monitor_can_deep_sleep(&act), "全部活动同时进行 -> 禁睡");
}

static void test_file_txn_predicate(void)
{
    printf("Test: monitor_file_txn_in_progress 只认 RECEIVING / VALIDATING\n");

    CHECK(monitor_file_txn_in_progress(FT_RECEIVING), "FT_RECEIVING -> 进行中");
    CHECK(monitor_file_txn_in_progress(FT_VALIDATING), "FT_VALIDATING -> 进行中");
    CHECK(!monitor_file_txn_in_progress(FT_IDLE), "FT_IDLE -> 已结束");
    CHECK(!monitor_file_txn_in_progress(FT_COMMITTED), "FT_COMMITTED -> 已结束");
    CHECK(!monitor_file_txn_in_progress(FT_ABORTED), "FT_ABORTED -> 已结束");
}

int main(void)
{
    printf("== sleep_decision host tests ==\n");

    test_null_and_idle();
    test_each_flag_blocks();
    test_txn_terminal_states_allow_sleep();
    test_all_flags_block();
    test_file_txn_predicate();

    printf("\n== result: PASS=%d FAIL=%d ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
