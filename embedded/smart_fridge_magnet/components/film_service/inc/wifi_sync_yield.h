/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/wifi_sync_yield.h
 * Description: 唤醒期「单写者让路」纯决策逻辑（Req 18.1）：定时 Wi-Fi 同步在已有
 *   进行中的 BLE 文件事务时是否应跳过本轮、不启动写事务。
 *
 *   BLE 与 Wi-Fi 共用同一文件事务且强制单写者，file_txn_begin() 在事务活动态对任何
 *   来源返回 FILE_TXN_ERR_BUSY，已兜底并发（Req 18.2/18.3）。本模块补的是主动让路：
 *   在发起写事务之前就跳过，而不是先连接下载、到 begin 处才撞上 busy。
 *
 *   让路当且仅当 owner == FILE_SRC_BLE 且 state ∈ {FT_RECEIVING, FT_VALIDATING}。
 *   无副作用、不依赖 ESP-IDF/网络/文件系统，可宿主机测试；状态快照读取与
 *   last_sync_error 记录由 service_wifi.c 负责。
 *
 *********************************************************************/
#ifndef __WIFI_SYNC_YIELD_H__
#define __WIFI_SYNC_YIELD_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>

#include "file_txn.h"   /* file_txn_state_t / file_source_t（纯枚举，host 可编译） */

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 定时 Wi-Fi 同步是否应向进行中的 BLE 文件事务让路（跳过本轮，Req 18.1）。
 *
 * 纯谓词。让路当且仅当 owner == FILE_SRC_BLE 且 state ∈ {FT_RECEIVING,
 * FT_VALIDATING}；其余情形返回 false，同步可继续（真正 begin 时文件事务侧仍会对
 * 并发做 busy 兜底）。
 *
 * @param state  当前文件事务状态（来自 service_file_get_receive_state().state）
 * @param owner  当前写所有者（来自 service_file_get_receive_state().owner）
 * @return true 当且仅当应让路（跳过本轮定时 Wi-Fi 同步，不启动写事务）
 */
bool wifi_sync_should_yield_to_ble(file_txn_state_t state, file_source_t owner);

#ifdef __cplusplus
}
#endif

#endif /* __WIFI_SYNC_YIELD_H__ */
