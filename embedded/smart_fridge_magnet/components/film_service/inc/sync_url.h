/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/sync_url.h
 * Description: 从 manifest_url 推导同源兄弟端点地址的纯字符串逻辑。
 *
 *   设备只持久化一个 manifest_url（`.../api/devices/{id}/display/manifest`），其余
 *   设备侧端点同源同宿、只差最后一段：request-refresh（请求后端重算菜单）、
 *   applied（回报上屏结果）。
 *
 *   推导而不各存一个字段：新增持久化字段会改动 ServiceParam_Def_t 的 NVS 布局，
 *   触发配置版本迁移并有重置用户配网的风险，而这些地址在契约上本就同源。
 *   单独成纯模块：这里是 memcpy + 手算长度写进调用方缓冲，边界算错即缓冲区溢出，
 *   且有多个调用点，抽出来才能在宿主机上直接测边界。
 *
 *********************************************************************/
#ifndef __SYNC_URL_H__
#define __SYNC_URL_H__

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 推导结果。刻意不复用 esp_err_t：本模块要能在没有 ESP-IDF 的宿主机上编译。 */
typedef enum {
    SYNC_URL_OK = 0,
    SYNC_URL_ERR_ARG,        /**< 入参为 NULL、容量为 0，或源地址不以 "manifest" 结尾 */
    SYNC_URL_ERR_TOO_LONG,   /**< 目标缓冲装不下推导结果 */
} sync_url_result_t;

/**
 * [sync_url_build_sibling 把 manifest_url 末段换成 suffix]
 *
 * @param out     输出缓冲；成功时为 NUL 结尾，失败时置空串（不留半截地址）。
 * @param out_sz  out 的容量（含结尾 NUL）。
 * @param src     源地址，必须以 "manifest" 结尾；否则返回 SYNC_URL_ERR_ARG。
 * @param suffix  替换用的末段，如 "applied"。
 *
 * 源地址不以 "manifest" 结尾时不猜地址：用户可能配了完全不同的路径，猜出来的地址
 * 会把设备令牌发给一个未知端点。
 */
sync_url_result_t sync_url_build_sibling(char *out, size_t out_sz,
                                         const char *src, const char *suffix);

#ifdef __cplusplus
}
#endif

#endif /* __SYNC_URL_H__ */
