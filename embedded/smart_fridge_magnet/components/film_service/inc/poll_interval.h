/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/poll_interval.h
 * Description: Wi-Fi 同步轮询周期钳制纯决策逻辑（Req 16.3）。
 *   把任意配置值收敛到合法闭区间 [30 秒, 24 小时]：低于下限饱和到 30，高于上限
 *   饱和到 86400，区间内为恒等映射。
 *   无副作用、不依赖 ESP-IDF/NVS/FreeRTOS，可宿主机测试。参数持久化
 *   （service_param）与定时器周期换算（service_monitor）在本钳制之后各自完成。
 *
 *********************************************************************/
#ifndef __POLL_INTERVAL_H__
#define __POLL_INTERVAL_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* 轮询周期合法区间与低功耗默认值（Req 16.3、14.3）。
 * MIN=30s 是演示模式可选的最小周期（非强制默认）；MAX=DEFAULT=86400s，正常配置下
 * 一天只成功联网一次。供 service_param 与 service_monitor 复用，避免各处硬编码
 * 续航假设。 */
#define MONITOR_POLL_INTERVAL_MIN_SEC       (30u)       /* 30 秒 */
#define MONITOR_POLL_INTERVAL_MAX_SEC       (86400u)    /* 24 小时 */
#define MONITOR_POLL_INTERVAL_DEFAULT_SEC   (86400u)    /* 低功耗默认每天一次 */

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 将请求的轮询周期钳制到合法闭区间 [30, 86400] 秒（Req 16.3）。
 *
 * 纯函数：输出恒落在 [MONITOR_POLL_INTERVAL_MIN_SEC, MONITOR_POLL_INTERVAL_MAX_SEC]，
 * 对区间内输入为恒等映射。本函数不引入默认值语义——「无配置时采用 24 小时默认」由
 * service_param 初始化另行决定。
 *
 * @param seconds  请求的轮询周期（任意 uint32，含 0 与 UINT32_MAX）
 * @return uint32_t 落在 [30, 86400] 内的合法轮询周期秒数
 */
uint32_t monitor_clamp_poll_interval(uint32_t seconds);

#ifdef __cplusplus
}
#endif

#endif /* __POLL_INTERVAL_H__ */
