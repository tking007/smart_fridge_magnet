/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/wake_schedule.c
 * Description: 「按每日计划唤醒 + 兜底周期」纯决策逻辑实现。规则与理由见 wake_schedule.h。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stddef.h>   /* NULL */

#include "wake_schedule.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool wake_schedule_slot_valid(uint16_t slot_minutes)
{
    return (uint32_t)slot_minutes < WAKE_SCHEDULE_MINUTES_PER_DAY;
}

uint16_t wake_schedule_retry_minutes(uint8_t failure_count)
{
    static const uint16_t delays[] = {15u, 60u, 240u};
    return (failure_count >= 1u && failure_count <= 3u) ? delays[failure_count - 1u] : 0u;
}

uint32_t wake_schedule_earliest_seconds(uint32_t scheduled_seconds,
                                        uint32_t retry_seconds)
{
    if(scheduled_seconds == 0u) return retry_seconds;
    if(retry_seconds == 0u) return scheduled_seconds;
    return scheduled_seconds < retry_seconds ? scheduled_seconds : retry_seconds;
}

uint32_t wake_schedule_next_seconds(uint32_t now_minutes,
                                    const uint16_t *slots, uint8_t count,
                                    uint16_t fallback_minutes)
{
    /* 入参归一，绝不因为一个越界的 now 就算出荒谬的结果。
     * now 由调用方从 localtime 派生，理论上恒在 [0,1439]；这里仍取模，是因为
     * 「时间源异常」时我们更希望得到一个仍然合理的唤醒周期，而不是一个巨大的
     * 差值让设备一整天不醒。 */
    uint32_t now = now_minutes % WAKE_SCHEDULE_MINUTES_PER_DAY;

    /* count 上限截断而不是拒绝：本函数是决策路径的最后一环，此处返回 0（放弃决策）
     * 会让设备回落到轮询周期，反而丢掉饭点唤醒。写入侧（BLE 0x48）已做严格校验，
     * 这里只做防御性收敛。 */
    uint8_t n = (count > WAKE_SCHEDULE_MAX_SLOTS) ? (uint8_t)WAKE_SCHEDULE_MAX_SLOTS : count;

    /* 先算「距最近一个 slot 的分钟数」。best_min == 0 表示还没有任何候选。 */
    uint32_t best_min = 0u;
    if (slots != NULL) {
        for (uint8_t i = 0; i < n; i++) {
            if (!wake_schedule_slot_valid(slots[i])) {
                continue;   /* 非法 slot 直接忽略：一条坏配置不该让整张时间表失效 */
            }

            /* 模运算同时覆盖「今天稍后」与「跨零点到明天」两种情形，无需分支。 */
            uint32_t delta = ((uint32_t)slots[i] + WAKE_SCHEDULE_MINUTES_PER_DAY - now)
                             % WAKE_SCHEDULE_MINUTES_PER_DAY;

            /* delta == 0 意味着「现在就是那个点」。本次唤醒就是为它而来，
             * 它下一次需要被唤醒是明天的同一时刻，故记为整天之后（见 .h 规则 2）。 */
            if (delta == 0u) {
                delta = WAKE_SCHEDULE_MINUTES_PER_DAY;
            }

            if (best_min == 0u || delta < best_min) {
                best_min = delta;
            }
        }
    }

    /* 与兜底取较小值：谁先到就先醒（见 .h 规则 3）。 */
    if (fallback_minutes > 0u) {
        uint32_t fb = (uint32_t)fallback_minutes;
        if (best_min == 0u || fb < best_min) {
            best_min = fb;
        }
    }

    if (best_min == 0u) {
        /* 既没有可用的时间表，也没有兜底 —— 本模块不作决定，交回调用方
         * （service_monitor 会回落到既有的轮询周期逻辑）。 */
        return 0u;
    }

    uint32_t seconds = best_min * 60u;
    if (seconds < WAKE_SCHEDULE_MIN_SECONDS) {
        seconds = WAKE_SCHEDULE_MIN_SECONDS;   /* 抖动保护，见 .h 规则 5 */
    }
    return seconds;
}
