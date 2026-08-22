/*
 * test_wifi_link_watch.c — 醒着期间 Wi-Fi 链路自愈判定的纯逻辑测试。
 *
 * 为什么盯这一块：`WIFI_EVENT_STA_DISCONNECTED` 刻意不重连（避免 Req 11 的
 * infinite fast reconnect），重连只编排在 sync_prepare 里。于是「设备醒着、同步已
 * 结束」这段时间没有人负责链路 —— 把路由器关掉再打开，设备不会自己连回去，必须手动
 * 重新下发配网（真实反馈）。本模块就是补这一段，而它的失效方式是静默的：判定写错
 * 只会表现为「它就是不重连」或者反过来「一直在重连」，两者都不报错。
 *
 * 钉住的行为：
 *   1. 退避恒落在 [BASE, MAX]，指数增长、到上限饱和、大 attempts 不溢出。
 *      间隔为 0 会退化成忙等重连，正是 Req 11 要禁的东西。
 *   2. 判定优先级：未配网 > 链路正常 > 忙 > 退避未到。
 *      「链路正常」必须早于「忙」：同步下载时链路恰好是连着的，若先判忙，日志会把
 *      一个健康链路说成被自愈逻辑挡住。
 *   3. 到达退避上限后**仍然继续重试**。彻底放弃的话，路由器恢复后设备仍然长期离线，
 *      现象与「没有自愈逻辑」完全一样。
 *   4. 单调时钟回绕时不能永久卡住。
 *
 * 只编译 wifi_link_watch.c —— 它只依赖标准 C 头。
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "wifi_link_watch.h"

static int g_checks;

#define CHECK(cond, msg)                                                   \
    do {                                                                   \
        g_checks++;                                                        \
        if (!(cond)) {                                                     \
            printf("FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__);        \
            return 1;                                                      \
        }                                                                  \
    } while (0)

/* 一个「已配网、链路断了、不忙」的基线输入，各用例只改自己关心的字段。 */
static wifi_link_watch_input_t base_input(void)
{
    wifi_link_watch_input_t in = {
        .enabled         = true,
        .provisioned     = true,
        .link_up         = false,
        .busy            = false,
        .now_ms          = 1000000u,
        .last_attempt_ms = 1000000u,
        .attempts        = 0u,
    };
    return in;
}

static int test_backoff_range_and_growth(void)
{
    /* 首次等待就是 BASE，不能是 0：间隔为 0 等于忙等重连。 */
    CHECK(wifi_link_watch_backoff_ms(0) == SFM_LINK_WATCH_BACKOFF_BASE_MS,
          "attempts=0 应等待 BASE");

    /* 指数增长 */
    CHECK(wifi_link_watch_backoff_ms(1) == SFM_LINK_WATCH_BACKOFF_BASE_MS * 2u,
          "attempts=1 应为 2*BASE");
    CHECK(wifi_link_watch_backoff_ms(2) == SFM_LINK_WATCH_BACKOFF_BASE_MS * 4u,
          "attempts=2 应为 4*BASE");

    /* 到上限饱和，并且此后恒为上限 —— 包括极大的 attempts（移位饱和保护）。 */
    for (uint32_t a = 0; a < 64u; a++)
    {
        uint32_t ms = wifi_link_watch_backoff_ms(a);
        CHECK(ms >= SFM_LINK_WATCH_BACKOFF_BASE_MS, "退避不得低于 BASE");
        CHECK(ms <= SFM_LINK_WATCH_BACKOFF_MAX_MS, "退避不得高于 MAX");
    }
    CHECK(wifi_link_watch_backoff_ms(1000u) == SFM_LINK_WATCH_BACKOFF_MAX_MS,
          "极大 attempts 应饱和到 MAX");
    CHECK(wifi_link_watch_backoff_ms(0xFFFFFFFFu) == SFM_LINK_WATCH_BACKOFF_MAX_MS,
          "UINT32_MAX 次尝试也必须饱和而不是溢出");

    /* 单调不减 */
    uint32_t prev = 0;
    for (uint32_t a = 0; a < 20u; a++)
    {
        uint32_t ms = wifi_link_watch_backoff_ms(a);
        CHECK(ms >= prev, "退避应单调不减");
        prev = ms;
    }
    printf("  ok: 退避区间、指数增长、上限饱和、无溢出\n");
    return 0;
}

static int test_priority_order(void)
{
    /* NULL、低功耗禁用与未配网 */
    CHECK(wifi_link_watch_eval(NULL) == WIFI_LINK_WATCH_SKIP_DISABLED,
          "NULL 入参应按禁用处理");

    wifi_link_watch_input_t in = base_input();
    in.enabled = false;
    in.link_up = false;
    in.now_ms += SFM_LINK_WATCH_BACKOFF_MAX_MS;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_DISABLED,
          "低功耗模式不得维持常驻 Wi-Fi");

    in = base_input();
    in.provisioned = false;
    in.link_up = false;
    in.busy = true;                 /* 未配网优先于其它一切 */
    in.attempts = 5u;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_NOT_PROVISIONED,
          "未配网应优先于忙与退避");

    /* 链路正常必须早于忙：同步下载时链路是连着的，先判忙会把健康链路
     * 说成被自愈逻辑挡住，排障时方向完全跑偏。 */
    in = base_input();
    in.link_up = true;
    in.busy = true;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_LINK_UP,
          "链路正常应优先于忙");

    /* 忙优先于退避 */
    in = base_input();
    in.busy = true;
    in.now_ms = in.last_attempt_ms + SFM_LINK_WATCH_BACKOFF_MAX_MS * 10u;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_BUSY,
          "忙应优先于退避已到");

    printf("  ok: 判定优先级 禁用 > 未配网 > 链路正常 > 忙 > 退避\n");
    return 0;
}

static int test_backoff_gate(void)
{
    wifi_link_watch_input_t in = base_input();

    /* 刚记录过尝试：还没到 BASE，不许连 */
    in.now_ms = in.last_attempt_ms + SFM_LINK_WATCH_BACKOFF_BASE_MS - 1u;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_BACKOFF,
          "未到退避间隔不应重连");

    /* 恰好到点：允许（边界取 >=，否则每次都要多等一拍） */
    in.now_ms = in.last_attempt_ms + SFM_LINK_WATCH_BACKOFF_BASE_MS;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_CONNECT,
          "恰好到退避间隔应允许重连");

    /* 失败次数增加后退避变长：同样的等待时间不再够 */
    in.attempts = 3u;
    in.now_ms = in.last_attempt_ms + SFM_LINK_WATCH_BACKOFF_BASE_MS;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_BACKOFF,
          "失败次数增加后退避应变长");
    in.now_ms = in.last_attempt_ms + wifi_link_watch_backoff_ms(3u);
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_CONNECT,
          "达到该次数对应的退避后应允许重连");

    printf("  ok: 退避闸门（未到/恰好/随失败次数变长）\n");
    return 0;
}

static int test_never_gives_up(void)
{
    /* 这一条是本模块存在的理由：路由器关掉几分钟再打开，设备必须还能自己连上。
     * 若实现成「尝试 N 次后彻底放弃」，那时的现象与完全没有自愈逻辑一样。 */
    wifi_link_watch_input_t in = base_input();
    in.attempts = 1000u;    /* 已经失败了很多次 */
    in.now_ms = in.last_attempt_ms + SFM_LINK_WATCH_BACKOFF_MAX_MS;
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_CONNECT,
          "失败很多次后仍应继续以上限间隔重试，不能彻底放弃");

    /* 而且间隔不会无限增长 —— 恢复时间有上界（用户可感知指标）。 */
    CHECK(wifi_link_watch_backoff_ms(1000u) == SFM_LINK_WATCH_BACKOFF_MAX_MS,
          "间隔应停在上限而不是继续增长");

    printf("  ok: 退避到上限后持续重试且间隔有上界\n");
    return 0;
}

static int test_clock_wraparound(void)
{
    /* 单调时钟回绕（约 49.7 天）。用有符号差值的实现会在回绕后长期为负，
     * 于是自愈永久停摆 —— 那种 bug 要跑七周才会出现一次。 */
    wifi_link_watch_input_t in = base_input();
    in.last_attempt_ms = 0xFFFFFFF0u;      /* 回绕前 16ms */
    in.now_ms = 0x00000010u;               /* 回绕后 16ms，实际间隔 32ms */
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_BACKOFF,
          "跨回绕的真实间隔仍应按退避判定（32ms < BASE）");

    in.now_ms = 0xFFFFFFF0u + SFM_LINK_WATCH_BACKOFF_BASE_MS; /* 自然回绕 */
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_CONNECT,
          "跨回绕达到退避间隔后应允许重连");

    printf("  ok: 单调时钟回绕不会卡住自愈\n");
    return 0;
}

static int test_healthy_link_needs_no_action(void)
{
    wifi_link_watch_input_t in = base_input();
    in.link_up = true;
    in.attempts = 7u;   /* 之前失败过，但现在连上了 */
    CHECK(wifi_link_watch_eval(&in) == WIFI_LINK_WATCH_SKIP_LINK_UP,
          "链路已连上时不应发起重连");
    printf("  ok: 链路正常时不动作\n");
    return 0;
}

int main(void)
{
    printf("== test_wifi_link_watch ==\n");
    if (test_backoff_range_and_growth() != 0) return 1;
    if (test_priority_order() != 0) return 1;
    if (test_backoff_gate() != 0) return 1;
    if (test_never_gives_up() != 0) return 1;
    if (test_clock_wraparound() != 0) return 1;
    if (test_healthy_link_needs_no_action() != 0) return 1;
    printf("PASS: %d checks\n", g_checks);
    return 0;
}
