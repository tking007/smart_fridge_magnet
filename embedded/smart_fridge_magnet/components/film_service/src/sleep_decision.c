/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/sleep_decision.c
 * Description: 低功耗管理器「深睡互斥」纯决策逻辑实现。见 sleep_decision.h 顶部说明。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "sleep_decision.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool monitor_file_txn_in_progress(file_txn_state_t state)
{
    /* 「未完成」依据显式状态判定（Req 16.4）：只有 RECEIVING/VALIDATING 表示
     * 事务仍在写临时文件或正在校验；终态 FT_IDLE/FT_COMMITTED/FT_ABORTED 表示
     * 事务已显式结束（提交或中止），不再阻止深睡。与文件事务状态机一致
     * （file_txn.h / design.md「文件事务状态机」）。 */
    return (state == FT_RECEIVING) || (state == FT_VALIDATING);
}

bool monitor_can_deep_sleep(const monitor_activity_t *act)
{
    /* 保守约定：状态未知时禁睡，避免打断可能进行中的传输/刷新（Req 16.2）。 */
    if (act == NULL) {
        return false;
    }

    /* Property 13（Req 16.2/16.4/17.3）：任一活动为真则禁睡。
     * 全部依据【显式状态标志】判断任务是否结束，不依赖任何固定延迟推断
     * （Req 16.4）。 */
    const bool any_activity_in_progress =
        act->ble_connected                              ||  /* BLE 已连接（Req 16.2、17.3） */
        act->ble_receiving                              ||  /* BLE 正在接收（Req 16.2、17.3） */
        act->wifi_busy                                  ||  /* Wi-Fi 连接/manifest/下载（Req 16.2） */
        monitor_file_txn_in_progress(act->file_txn_state) || /* 文件事务未完成（Req 16.2、16.4） */
        act->epd_refreshing                             ||  /* 电子纸刷新中（Req 16.2、16.4） */
        act->ble_adv_window_active;                         /* BLE 广播窗口活动（Req 17.3） */

    /* 仅当无任何进行中活动时才允许深睡。 */
    return !any_activity_in_progress;
}
