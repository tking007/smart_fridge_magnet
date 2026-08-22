/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/wifi_sync_gate.c
 * Description: Wi-Fi 同步「STA 连接 + SNTP 时间同步」门控的纯决策逻辑实现。
 *              见 wifi_sync_gate.h 顶部真值表与退避策略说明。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "wifi_sync_gate.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool wifi_sync_gate_time_synced(int64_t unix_time_s)
{
    /* 修正项 A1：冷启动墙钟为 1970(=0)，早于任何证书 notBefore，TLS 时间校验必失败。
     * 只有推进到可信近期时刻（>= 阈值）才认为 SNTP 成功。 */
    return unix_time_s >= SFM_SNTP_MIN_VALID_EPOCH;
}

wifi_sync_gate_result_t wifi_sync_gate_eval(bool sta_connected, bool time_synced)
{
    /* Req 11 真值表：
     *   !connected            → ABORT_NO_LINK （连接失败/超时，降级 BLE）
     *   connected & !synced   → ABORT_NO_TIME （SNTP 失败，降级 BLE，不进证书时间重试循环 Req 11.3）
     *   connected &  synced   → PROCEED       （放行 manifest HTTPS，Req 11.1/11.2）
     */
    if (!sta_connected)
    {
        return WIFI_GATE_ABORT_NO_LINK;
    }
    if (!time_synced)
    {
        return WIFI_GATE_ABORT_NO_TIME;
    }
    return WIFI_GATE_PROCEED;
}

bool wifi_sync_gate_https_allowed(wifi_sync_gate_result_t result)
{
    /* 只有 PROCEED 放行 HTTPS —— SNTP 未成功之前绝不发起 manifest HTTPS（Req 11.2）。 */
    return result == WIFI_GATE_PROCEED;
}

const char *wifi_sync_gate_result_str(wifi_sync_gate_result_t result)
{
    /* 均为非机密文本，可直接写入 last_sync_error 与串口日志。 */
    switch (result)
    {
    case WIFI_GATE_PROCEED:
        return "ok";
    case WIFI_GATE_ABORT_NO_LINK:
        return "wifi connect failed";
    case WIFI_GATE_ABORT_NO_TIME:
        return "sntp time sync failed";
    default:
        return "unknown";
    }
}

bool wifi_connect_should_retry(uint32_t attempts_done, uint32_t max_attempts)
{
    /* 有限退避：完成的尝试数达到上限即不再重试（放弃本轮，绝不无限重试）。 */
    return attempts_done < max_attempts;
}

uint32_t wifi_connect_backoff_ms(uint32_t attempt_index)
{
    /* 指数退避 min(BASE << idx, MAX)，对大 idx 做移位饱和保护，避免 UB / 回绕。
     * 一旦左移会超过 MAX（或位宽），直接钳到 MAX。返回值恒落在 [BASE, MAX]，
     * 非零下界保证「断开不无限快速重连」。 */
    uint32_t backoff = SFM_WIFI_BACKOFF_BASE_MS;

    for (uint32_t i = 0; i < attempt_index; i++)
    {
        /* 若再翻倍会越过上限或溢出 uint32，则钳到 MAX 并停止。 */
        if (backoff >= SFM_WIFI_BACKOFF_MAX_MS ||
            backoff > (0xFFFFFFFFu >> 1))
        {
            return SFM_WIFI_BACKOFF_MAX_MS;
        }
        backoff <<= 1u;
    }

    if (backoff > SFM_WIFI_BACKOFF_MAX_MS)
    {
        backoff = SFM_WIFI_BACKOFF_MAX_MS;
    }
    return backoff;
}

bool wifi_sync_run_try_enter(bool *in_progress, bool display_pending)
{
    if(!in_progress || *in_progress || display_pending)
    {
        return false;
    }
    *in_progress = true;
    return true;
}

void wifi_sync_run_leave(bool *in_progress)
{
    if(in_progress)
    {
        *in_progress = false;
    }
}
