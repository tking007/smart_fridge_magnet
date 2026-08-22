/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/wake_schedule.h
 * Description: 「按每日计划唤醒 + 兜底周期」纯决策逻辑：给定当前的当地分钟数与一组
 *   唤醒时间点，算出距下一次该醒来的秒数。
 *
 *   设备靠电池供电，一次唤醒要做「连 Wi-Fi → TLS 握手 → 拉 manifest → 可能下载
 *   4 张 .film → 刷屏」，成本很高，因此不能按固定短周期猛醒。当前 App 只把后端
 *   daily_publish_time 写成一个计划点（默认 06:30），再配一个 24 小时自愈兜底；
 *   用餐时间只用于设备醒来后的本地切图。
 *
 *   只依赖 <stdint.h>/<stdbool.h>，不碰 ESP-IDF / NVS / FreeRTOS / 电源 HAL，可在
 *   宿主机直接编译测试；读参数与装载深睡定时器等副作用留在 service_monitor 与
 *   hal_pwr。
 *
 *********************************************************************/
#ifndef __WAKE_SCHEDULE_H__
#define __WAKE_SCHEDULE_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stdbool.h>

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* 一天的分钟数。slot 的合法域是 [0, 1439]。 */
#define WAKE_SCHEDULE_MINUTES_PER_DAY   (1440u)

/* 唤醒时间点的最大个数。上限 6 保留上游兼容能力；当前 App 只下发一个每日更新
 * 时间。每多一个唤醒点就是每天多一次完整的联网和可能下载。 */
#define WAKE_SCHEDULE_MAX_SLOTS         (6u)

/* 返回值的下限（秒）。钳 60 秒的原因：一次完整唤醒（含 Wi-Fi 同步）本身就要几十秒，
 * 若允许返回「距下一个唤醒点还有 3 秒」，设备刚醒完又睡 3 秒再醒，醒来时早已越过该
 * 唤醒点，于是算出「距明天同一点 1439 分钟」，白折腾一轮。60 秒同时避免分钟粒度
 * 时间源因舍入抖动而反复触发。 */
#define WAKE_SCHEDULE_MIN_SECONDS       (60u)

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 判断一个唤醒时间点是否合法（当地时间的分钟数，必须 < 1440）。
 *
 * 纯谓词，单独导出以便 BLE 0x48 的载荷校验与 service_monitor 的装载路径共用同一份
 * 判据，避免两处各写一遍而漂移。
 *
 * @param slot_minutes 当地时间分钟数
 * @return true 当且仅当 slot_minutes < WAKE_SCHEDULE_MINUTES_PER_DAY
 */
bool wake_schedule_slot_valid(uint16_t slot_minutes);

/**
 * @brief 算出距下一次唤醒的秒数。
 *
 * 纯函数。规则：
 *  1) 取所有合法 slot 中 `((slot - now + 1440) mod 1440)` 的最小正值。用模运算而
 *     非「先筛今天剩下的、再看明天第一个」，可天然处理跨零点且少一个分支。
 *  2) 差值恰为 0（slot 等于当前分钟）视为 1440 而不是 0：本次唤醒就是为它而来，
 *     装 0 会让设备在同一分钟里反复空转。
 *  3) 与兜底周期取较小值，使「饭点前必醒」与「最长 fallback 必醒一次」同时成立。
 *     兜底的意义是非饭点时段也能拿到手动发布的新画面。
 *  4) count == 0 或所有 slot 非法 → 只用兜底；兜底也为 0（不启用）→ 返回 0。
 *     返回 0 是「本模块不作决定」的信号，而非「立刻唤醒」，调用方据此回落到轮询周期。
 *  5) 非 0 返回值一律钳到至少 WAKE_SCHEDULE_MIN_SECONDS。
 *
 * @param now_minutes      当前当地时间的分钟数（0..1439；越界值按 mod 1440 归一）
 * @param slots            唤醒时间点数组（各元素为 0..1439；非法元素被忽略）；
 *                         可为 NULL（等价于 count == 0）
 * @param count            slots 的有效个数（> WAKE_SCHEDULE_MAX_SLOTS 时按上限截断）
 * @param fallback_minutes 兜底唤醒周期（分钟）；0 表示不启用兜底
 * @return uint32_t 距下一次唤醒的秒数；0 表示「无时间表也无兜底」，调用方自行决定
 */
uint32_t wake_schedule_next_seconds(uint32_t now_minutes,
                                    const uint16_t *slots, uint8_t count,
                                    uint16_t fallback_minutes);

/** 第 1/2/3 次同步失败分别返回 15/60/240 分钟，第 4 次起返回 0。 */
uint16_t wake_schedule_retry_minutes(uint8_t failure_count);

/** 返回两个非零候选中的较小值；任一为 0 时返回另一个。 */
uint32_t wake_schedule_earliest_seconds(uint32_t scheduled_seconds,
                                        uint32_t retry_seconds);

#ifdef __cplusplus
}
#endif

#endif /* __WAKE_SCHEDULE_H__ */
