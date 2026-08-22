/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/poll_interval.c
 * Description: Wi-Fi 同步轮询周期钳制纯决策逻辑实现。见 poll_interval.h 顶部说明。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "poll_interval.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

uint32_t monitor_clamp_poll_interval(uint32_t seconds)
{
    /* Property 14（Req 16.3/14.3）：饱和钳制到闭区间 [30, 86400]。
     *   seconds < 30      → 30    （下限饱和；30s 为演示模式可选最小值，Req 14.3）
     *   seconds > 86400   → 86400 （上限 24h 饱和，Req 16.3）
     *   30 <= seconds <= 86400 → seconds（恒等映射，区间内合法值不变）
     * 全程仅做无符号比较，无溢出/环绕风险。 */
    if (seconds < MONITOR_POLL_INTERVAL_MIN_SEC) {
        return MONITOR_POLL_INTERVAL_MIN_SEC;
    }
    if (seconds > MONITOR_POLL_INTERVAL_MAX_SEC) {
        return MONITOR_POLL_INTERVAL_MAX_SEC;
    }
    return seconds;
}
