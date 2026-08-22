/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/wifi_link_watch.c
 * Description: 醒着期间的 Wi-Fi 链路自愈判定（纯逻辑）。见 inc/wifi_link_watch.h。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "wifi_link_watch.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

uint32_t wifi_link_watch_backoff_ms(uint32_t attempts)
{
    uint32_t backoff = SFM_LINK_WATCH_BACKOFF_BASE_MS;

    for (uint32_t i = 0; i < attempts; i++)
    {
        /* 再翻倍就会越过上限或溢出 uint32 时，直接钳到上限并停止。
         * 不做这层保护的话，attempts 累积到 32 以上会左移出未定义行为，
         * 而这个函数每断线一次就被调用一次，长时间离线必然走到那里。 */
        if (backoff >= SFM_LINK_WATCH_BACKOFF_MAX_MS ||
            backoff > (0xFFFFFFFFu >> 1))
        {
            return SFM_LINK_WATCH_BACKOFF_MAX_MS;
        }
        backoff <<= 1u;
    }

    if (backoff > SFM_LINK_WATCH_BACKOFF_MAX_MS)
    {
        backoff = SFM_LINK_WATCH_BACKOFF_MAX_MS;
    }
    return backoff;
}

wifi_link_watch_action_t wifi_link_watch_eval(const wifi_link_watch_input_t *in)
{
    if (in == NULL || !in->enabled)
    {
        return WIFI_LINK_WATCH_SKIP_DISABLED;
    }

    if (!in->provisioned)
    {
        return WIFI_LINK_WATCH_SKIP_NOT_PROVISIONED;
    }

    /* 链路正常要早于「忙」判定：忙的时候链路往往正好是连着的（同步在下载），
     * 若先判忙就会把一个健康链路报成「因忙跳过」，日志读起来像是被自愈逻辑挡住了。 */
    if (in->link_up)
    {
        return WIFI_LINK_WATCH_SKIP_LINK_UP;
    }

    if (in->busy)
    {
        return WIFI_LINK_WATCH_SKIP_BUSY;
    }

    /* 无符号减法天然处理单调时钟回绕：now < last 时差值绕回成一个很大的正数，
     * 最坏后果是这一拍立刻允许重试（多试一次），而不是永久卡住。 */
    const uint32_t elapsed = in->now_ms - in->last_attempt_ms;
    if (elapsed < wifi_link_watch_backoff_ms(in->attempts))
    {
        return WIFI_LINK_WATCH_SKIP_BACKOFF;
    }

    return WIFI_LINK_WATCH_CONNECT;
}
