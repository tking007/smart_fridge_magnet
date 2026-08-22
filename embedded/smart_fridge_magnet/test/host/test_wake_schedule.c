/*********************************************************************
 * Host test: 按饭点唤醒 + 兜底周期的纯决策逻辑（wake_schedule.c）。
 *
 * Part of the Smart Fridge Magnet project (based on FrameFilm 562f330,
 * GPL-3.0-or-later — see ../../NOTICE.md and LICENSE).
 *
 * Feature: F3「按饭点唤醒 + 4 小时兜底 + 休眠参数可由 App 配置」。
 *
 * 在普通主机上编译【真实的生产源码】wake_schedule.c（该模块只依赖
 * <stdint.h>/<stdbool.h>/<stddef.h>，不需要 ESP-IDF、NVS、FreeRTOS 或电源 HAL），
 * 逐条验证 wake_schedule.h 里写明的五条规则：
 *
 *   1) 取所有合法 slot 中 ((slot - now + 1440) mod 1440) 的最小正值 —— 含跨零点；
 *   2) 差值为 0（slot 等于当前分钟）视为 1440（整天之后），不是立即唤醒；
 *   3) 与兜底周期取较小值 —— 两个承诺（饭点前必醒 / 最长 N 分钟必醒）同时成立；
 *   4) 无时间表 → 只用兜底；兜底也为 0 → 返回 0（调用方回落轮询周期）；
 *   5) 非 0 返回值下限钳到 60 秒。
 *
 * TEST-ONLY. NOT part of the firmware build.
 *********************************************************************/
#include <stdint.h>
#include <stdio.h>

#include "wake_schedule.h"

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

/* 断言 wake_schedule_next_seconds(now, slots, count, fallback) == expect。 */
static void check_next(uint32_t now, const uint16_t *slots, uint8_t count,
                       uint16_t fallback, uint32_t expect, const char *label)
{
    uint32_t got = wake_schedule_next_seconds(now, slots, count, fallback);
    if (got == expect) {
        printf("  PASS: %s (now=%u -> %u sec)\n", label, now, got);
        g_pass++;
    } else {
        printf("  FAIL: %s now=%u got %u expected %u  (%s:%d)\n",
               label, now, got, expect, __FILE__, __LINE__);
        g_fail++;
    }
}

/* 便于阅读：把 HH:MM 写成分钟数。 */
#define HHMM(h, m)  ((uint16_t)((h) * 60 + (m)))

static void test_slot_valid(void)
{
    printf("Test: wake_schedule_slot_valid —— 合法域 [0, 1439]\n");
    CHECK(wake_schedule_slot_valid(0), "0 (00:00) 合法");
    CHECK(wake_schedule_slot_valid(HHMM(7, 30)), "450 (07:30) 合法");
    CHECK(wake_schedule_slot_valid(1439), "1439 (23:59) 合法 —— 上边界");
    CHECK(!wake_schedule_slot_valid(1440), "1440 非法 —— 恰好越界");
    CHECK(!wake_schedule_slot_valid(2000), "2000 非法");
    CHECK(!wake_schedule_slot_valid(0xFFFFu), "0xFFFF 非法");
}

static void test_cross_midnight(void)
{
    /* 规则 1 的关键情形：now 在午夜前、slot 在次日早晨。
     * 23:50 -> 07:30 应为 460 分钟（10 分钟到零点 + 450 分钟），而不是负数或 1380。 */
    printf("Test: 跨零点（now=23:50, slot=07:30）\n");
    const uint16_t slots[] = { HHMM(7, 30) };
    check_next(HHMM(23, 50), slots, 1, 0, 460u * 60u,
               "23:50 -> 07:30 = 460 分钟（跨零点）");

    /* 反向：now 在凌晨、slot 在当天晚上，不该被算成跨零点。 */
    const uint16_t evening[] = { HHMM(18, 0) };
    check_next(HHMM(0, 10), evening, 1, 0, 1070u * 60u,
               "00:10 -> 18:00 = 1070 分钟（同一天，不跨零点）");
}

static void test_slot_equals_now(void)
{
    /* 规则 2：现在就是那个点 —— 本次唤醒就是为它而来，下一次是明天。
     * 若返回 0/极小值，设备会在同一分钟里反复睡醒空转。 */
    printf("Test: slot 恰等于当前分钟 -> 整天之后（不是立即）\n");
    const uint16_t slots[] = { HHMM(18, 0) };
    check_next(HHMM(18, 0), slots, 1, 0, 1440u * 60u,
               "now == slot == 18:00 -> 1440 分钟（明天同一时刻）");

    /* 与更近的另一个 slot 共存时，等于当前分钟的那个不该抢先。 */
    const uint16_t two[] = { HHMM(18, 0), HHMM(18, 30) };
    check_next(HHMM(18, 0), two, 2, 0, 30u * 60u,
               "now==18:00，另有 18:30 -> 取 30 分钟");
}

static void test_multi_slot_nearest(void)
{
    printf("Test: 多 slot 取最近的一个\n");
    /* 典型的三餐前唤醒点。 */
    const uint16_t meals[] = { HHMM(6, 30), HHMM(11, 0), HHMM(17, 0) };

    check_next(HHMM(5, 0), meals, 3, 0, 90u * 60u,
               "05:00 -> 最近是 06:30（90 分钟）");
    check_next(HHMM(7, 0), meals, 3, 0, 240u * 60u,
               "07:00 -> 最近是 11:00（240 分钟）");
    check_next(HHMM(12, 0), meals, 3, 0, 300u * 60u,
               "12:00 -> 最近是 17:00（300 分钟）");
    /* 全部 slot 都在今天过去了 -> 绕到明天最早的那个（06:30）。 */
    check_next(HHMM(20, 0), meals, 3, 0, (uint32_t)(10u * 60u + 30u) * 60u,
               "20:00 -> 绕到明天 06:30（630 分钟）");

    /* slot 顺序不影响结果（内部取最小值，不假设已排序）。 */
    const uint16_t unsorted[] = { HHMM(17, 0), HHMM(6, 30), HHMM(11, 0) };
    check_next(HHMM(5, 0), unsorted, 3, 0, 90u * 60u,
               "未排序的 slot 数组结果相同（90 分钟）");
}

static void test_fallback_takes_min(void)
{
    printf("Test: 与兜底周期取较小值（规则 3）\n");
    const uint16_t meals[] = { HHMM(6, 30), HHMM(11, 0), HHMM(17, 0) };

    /* 12:00 距下一个饭点 300 分钟 > 兜底 240 -> 取兜底。
     * 这正是「非饭点时段也能拿到手动发布的新画面」的那条保证。 */
    check_next(HHMM(12, 0), meals, 3, 240, 240u * 60u,
               "12:00：饭点 300 分钟 vs 兜底 240 -> 取兜底 240");

    /* 05:00 距 06:30 只有 90 分钟 < 兜底 240 -> 取饭点。
     * 「饭点前必醒」不会被兜底冲掉。 */
    check_next(HHMM(5, 0), meals, 3, 240, 90u * 60u,
               "05:00：饭点 90 分钟 vs 兜底 240 -> 取饭点 90");

    /* 相等时取值一致（较小=相等，任一都对）。 */
    const uint16_t one[] = { HHMM(10, 0) };
    check_next(HHMM(8, 0), one, 1, 120, 120u * 60u,
               "饭点与兜底都是 120 分钟 -> 120");
}

static void test_no_slots(void)
{
    printf("Test: count==0 / 全部非法 -> 只用兜底（规则 4）\n");
    check_next(HHMM(12, 0), NULL, 0, 240, 240u * 60u,
               "count=0 + 兜底 240 -> 240 分钟");

    /* slots 非 NULL 但 count=0：不该读取数组内容。 */
    const uint16_t ignored[] = { HHMM(6, 30) };
    check_next(HHMM(12, 0), ignored, 0, 240, 240u * 60u,
               "count=0（数组非空但不应被读）-> 兜底 240");

    /* 全部 slot 非法 -> 等价于没有时间表。 */
    const uint16_t all_bad[] = { 1440, 2000, 0xFFFFu };
    check_next(HHMM(12, 0), all_bad, 3, 240, 240u * 60u,
               "全部 slot 非法 -> 回落兜底 240");
}

static void test_no_slots_no_fallback(void)
{
    printf("Test: count==0 且兜底==0 -> 返回 0（交回调用方，规则 4）\n");
    check_next(HHMM(12, 0), NULL, 0, 0, 0u,
               "无时间表 + 无兜底 -> 0（service_monitor 回落轮询周期）");

    const uint16_t all_bad[] = { 1440, 3000 };
    check_next(HHMM(12, 0), all_bad, 2, 0, 0u,
               "全部 slot 非法 + 无兜底 -> 0");
}

static void test_invalid_slots_ignored(void)
{
    printf("Test: 非法 slot 被忽略，合法的仍然生效（一条坏配置不该毁掉整张表）\n");
    /* 混入非法值：1440（恰好越界）与 0xFFFF。合法的只有 06:30。 */
    const uint16_t mixed[] = { 1440, HHMM(6, 30), 0xFFFFu };
    check_next(HHMM(5, 0), mixed, 3, 0, 90u * 60u,
               "混合数组仍取合法的 06:30（90 分钟）");

    /* 非法值若被误当成有效，(3000-300+1440)%1440 = waaay off，会得到别的结果，
     * 这里断言它确实没有参与竞争。 */
    const uint16_t mixed2[] = { 3000, HHMM(12, 0) };
    check_next(HHMM(11, 0), mixed2, 2, 0, 60u * 60u,
               "非法 3000 不参与竞争，取 12:00（60 分钟）");
}

static void test_min_seconds_clamp(void)
{
    printf("Test: 返回值下限钳到 60 秒（规则 5）\n");
    /* 唯一能产生「小于 60 秒」的入口是兜底周期本身 —— 但兜底以分钟计，
     * 最小的非零值是 1 分钟 = 60 秒，恰好等于下限。 */
    check_next(HHMM(12, 0), NULL, 0, 1, WAKE_SCHEDULE_MIN_SECONDS,
               "兜底 1 分钟 -> 60 秒（恰为下限）");

    /* 距下一个 slot 1 分钟时同样是 60 秒，不会更小。 */
    const uint16_t one[] = { HHMM(12, 1) };
    check_next(HHMM(12, 0), one, 1, 0, WAKE_SCHEDULE_MIN_SECONDS,
               "距 slot 1 分钟 -> 60 秒");

    /* 全域扫描：任何非 0 的返回值都不得低于 60 秒。 */
    const uint16_t meals[] = { HHMM(6, 30), HHMM(11, 0), HHMM(17, 0) };
    int all_ok = 1;
    for (uint32_t now = 0; now < WAKE_SCHEDULE_MINUTES_PER_DAY; now++) {
        for (uint16_t fb = 0; fb <= 300; fb += 60) {
            uint32_t s = wake_schedule_next_seconds(now, meals, 3, fb);
            if (s != 0u && s < WAKE_SCHEDULE_MIN_SECONDS) {
                all_ok = 0;
            }
        }
    }
    CHECK(all_ok, "全天 1440 分钟 × 多档兜底：非 0 返回值恒 >= 60 秒");
}

static void test_bounds_and_defense(void)
{
    printf("Test: 边界与防御性行为\n");
    CHECK(WAKE_SCHEDULE_MAX_SLOTS == 6u, "WAKE_SCHEDULE_MAX_SLOTS == 6");
    CHECK(WAKE_SCHEDULE_MINUTES_PER_DAY == 1440u, "MINUTES_PER_DAY == 1440");
    CHECK(WAKE_SCHEDULE_MIN_SECONDS == 60u, "MIN_SECONDS == 60");

    /* now 越界按 mod 1440 归一：时间源异常时仍给出合理周期，而不是让设备一整天不醒。 */
    const uint16_t one[] = { HHMM(7, 30) };
    check_next(1440u + HHMM(23, 50), one, 1, 0, 460u * 60u,
               "now = 1440+23:50 归一后与 23:50 结果相同");

    /* count 超上限按 WAKE_SCHEDULE_MAX_SLOTS 截断，不越界读。
     * 数组只给 6 个元素，count 传 10；若实现不截断就会读到数组外。 */
    const uint16_t six[] = { HHMM(6, 30), HHMM(8, 0), HHMM(11, 0),
                             HHMM(13, 0), HHMM(17, 0), HHMM(19, 0) };
    check_next(HHMM(5, 0), six, 10, 0, 90u * 60u,
               "count=10 被截断到 6，结果仍是最近的 06:30");

    /* 满配 6 个 slot 正常工作。 */
    check_next(HHMM(12, 0), six, 6, 0, 60u * 60u,
               "6 个 slot 满配：12:00 -> 13:00（60 分钟）");
}

static void test_daily_retry_policy(void)
{
    printf("Test: 每日同步失败的临时重试策略\n");
    CHECK(wake_schedule_retry_minutes(1) == 15u, "第 1 次失败后 15 分钟重试");
    CHECK(wake_schedule_retry_minutes(2) == 60u, "第 2 次失败后 60 分钟重试");
    CHECK(wake_schedule_retry_minutes(3) == 240u, "第 3 次失败后 240 分钟重试");
    CHECK(wake_schedule_retry_minutes(4) == 0u, "第 4 次失败后停止额外重试");
    CHECK(wake_schedule_earliest_seconds(3600u, 900u) == 900u,
          "下一餐与临时重试取更早者");
    CHECK(wake_schedule_earliest_seconds(3600u, 0u) == 3600u,
          "无临时重试时保留餐次唤醒");
}

int main(void)
{
    printf("== test_wake_schedule (F3: 按饭点唤醒 + 兜底周期) ==\n");
    test_slot_valid();
    test_cross_midnight();
    test_slot_equals_now();
    test_multi_slot_nearest();
    test_fallback_takes_min();
    test_no_slots();
    test_no_slots_no_fallback();
    test_invalid_slots_ignored();
    test_min_seconds_clamp();
    test_bounds_and_defense();
    test_daily_retry_policy();
    printf("\n== result: PASS=%d FAIL=%d ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
