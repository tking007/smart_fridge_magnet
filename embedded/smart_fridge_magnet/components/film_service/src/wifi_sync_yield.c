/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/wifi_sync_yield.c
 * Description: 唤醒期「单写者让路」纯决策逻辑实现。见 wifi_sync_yield.h 顶部说明。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "wifi_sync_yield.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool wifi_sync_should_yield_to_ble(file_txn_state_t state, file_source_t owner)
{
    /* 「进行中的 BLE 文件事务」：写所有者为 BLE 且事务处于活动态。
     * 活动态与 file_txn.c 的 file_txn_is_active() 一致（FT_RECEIVING/FT_VALIDATING）；
     * 终态 (FT_IDLE/FT_COMMITTED/FT_ABORTED) 不占用写事务，不需让路。
     * 定时 Wi-Fi 同步据此在发起写事务之前主动跳过本轮（Req 18.1 / 修正项 D10）。 */
    const bool ble_txn_active =
        (owner == FILE_SRC_BLE) &&
        (state == FT_RECEIVING || state == FT_VALIDATING);

    return ble_txn_active;
}
