/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/sleep_decision.h
 * Description: 低功耗管理器「深睡互斥」纯决策逻辑（Req 16.2、16.4、17.3）：
 *   给定一组明确的活动状态标志，判断当前是否允许进入深睡。
 *
 *   任何一旦被打断就会损坏结果的活动进行中时都必须禁睡：BLE 已连接或正在收文件、
 *   Wi-Fi 忙、文件事务处于 FT_RECEIVING/FT_VALIDATING、电子纸正在刷新、按键唤醒
 *   打开的 BLE 广播窗口仍活动。全部为假才允许深睡。
 *
 *   判定只读显式状态，不做任何超时/延迟推断（Req 16.4）——「任务是否结束」由这些
 *   完成结果决定。定时器与唤醒窗口计时归 service_monitor，其结果以布尔量喂入。
 *   无副作用、不依赖 ESP-IDF/FreeRTOS/电源 HAL，可宿主机测试；真实状态采集与
 *   esp_deep_sleep 调用留在 service_monitor。
 *
 *********************************************************************/
#ifndef __SLEEP_DECISION_H__
#define __SLEEP_DECISION_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>
#include "file_txn.h"   /* file_txn_state_t（纯枚举，host 可编译） */

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * TYPEDEFS
 */

/**
 * @brief 深睡互斥决策的输入：低功耗管理器采集的一组明确活动状态标志。
 *
 * 每个字段都是显式的完成/忙状态，不含任何超时或延迟推断（Req 16.4）。
 * service_monitor 负责把真实状态填入本结构后交由 monitor_can_deep_sleep() 判定。
 */
typedef struct {
    bool ble_connected;          /* BLE 已连接 */
    bool ble_receiving;          /* BLE 正在接收文件分片 */
    bool wifi_busy;              /* Wi-Fi 忙：连接 / 请求 manifest / 下载 */
    file_txn_state_t file_txn_state; /* 文件事务状态；RECEIVING/VALIDATING 视为未完成 */
    bool epd_refreshing;         /* 电子纸正在刷新 */
    bool ble_adv_window_active;  /* 按键唤醒的 BLE 广播+可连接窗口处于活动 */
} monitor_activity_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 判断给定的文件事务状态是否代表「事务仍在进行、尚未完成」。
 *
 * 纯谓词，语义与 file_txn.h 的事务状态机一致：
 *   - FT_RECEIVING / FT_VALIDATING → true（进行中，禁睡）；
 *   - FT_IDLE / FT_COMMITTED / FT_ABORTED 及任何其它值 → false（已显式结束）。
 *
 * @param state  文件事务状态（来自 service_file_get_receive_state().state）
 * @return true 当且仅当事务处于 FT_RECEIVING 或 FT_VALIDATING
 */
bool monitor_file_txn_in_progress(file_txn_state_t state);

/**
 * @brief 判断当前是否允许进入深睡（深睡互斥核心决策）。
 *
 * 纯函数。只要 ble_connected / ble_receiving / wifi_busy /
 * monitor_file_txn_in_progress(file_txn_state) / epd_refreshing /
 * ble_adv_window_active 任一为真即禁睡，全部为假才允许深睡。判定完全依据这些显式
 * 状态而非固定延迟或超时推断（Req 16.4）。
 *
 * @p act 为 NULL 时返回 false（禁睡）：状态未知时绝不深睡，以免打断传输或刷新。
 *
 * @param act  当前活动状态标志集合（可为 NULL）
 * @return true 当且仅当所有活动标志均为假
 */
bool monitor_can_deep_sleep(const monitor_activity_t *act);

#ifdef __cplusplus
}
#endif

#endif /* __SLEEP_DECISION_H__ */
