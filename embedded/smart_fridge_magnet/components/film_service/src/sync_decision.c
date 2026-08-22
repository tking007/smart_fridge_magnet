/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/sync_decision.c
 * Description: Wi-Fi 同步「版本门控 + force 强制应用」纯决策逻辑实现。
 *              见 sync_decision.h 顶部真值表说明。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "sync_decision.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool sync_should_download(uint32_t version,
                          uint32_t last_applied_version,
                          bool force)
{
    /* Property 9 真值表：
     *   force == true                         → 恒下载并应用当前版本（B4/Req 10.4）
     *   force == false 且 version >  last     → 下载并应用新版本（常规门控）
     *   force == false 且 version <= last     → 不下载、不刷新（Req 10.3）
     * 合并为：force || (version > last_applied_version)。 */
    return force || (version > last_applied_version);
}

sync_decision_t sync_decide_download(uint32_t version,
                                     uint32_t last_applied_version,
                                     bool force)
{
    sync_decision_t d;
    d.should_download_and_apply = sync_should_download(version, last_applied_version, force);

    /* B4（Req 10.4）：force 强制应用【当前版本】，但绝不重置本地 last_applied_version，
     * 也不引入专用 BLE 复位命令。last_applied_version 仅在刷新成功后由 service_wifi
     * 前向保存，因此本纯决策恒不要求重置。 */
    d.resets_last_applied_version = false;

    return d;
}
