/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/wifi_sync_gate.h
 * Description: Wi-Fi 同步「STA 连接 + SNTP 时间同步」门控的纯决策逻辑
 *   （Req 11.1/11.2/11.3）。无副作用、不依赖 ESP-IDF/网络/时钟，可宿主机测试；
 *   真正的 esp_wifi_connect / esp_netif_sntp 调用在 service_wifi.c。
 *
 *   为什么需要 SNTP 门控：ESP32-S3 没有 RTC，冷启动墙钟是 1970-01-01，此时发起
 *   HTTPS 必然因当前时间早于证书 notBefore 而失败。故 STA 连接成功后必须先做成功
 *   一次 SNTP 才放行 manifest HTTPS；SNTP 失败即放弃本轮、降级 BLE 兜底并保留当前
 *   画面，不进入证书时间校验失败的重试循环。
 *
 *   门控：仅 (sta_connected && time_synced) 放行 HTTPS；未连接 → ABORT_NO_LINK，
 *   已连接但未同步 → ABORT_NO_TIME，两者都降级 BLE 兜底。
 *
 *   连接退避为有限退避：单次超时 SFM_WIFI_CONNECT_TIMEOUT_MS，次数上限
 *   SFM_WIFI_CONNECT_MAX_ATTEMPTS，相邻尝试间指数退避且下限非零，因此断开后不会
 *   无限快速重连、也不会无限重试。
 *
 *********************************************************************/
#ifndef __WIFI_SYNC_GATE_H__
#define __WIFI_SYNC_GATE_H__

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
 * MACROS — connect timing / bounded backoff policy
 */
/* 单次 STA 连接尝试的超时（毫秒）。 */
#define SFM_WIFI_CONNECT_TIMEOUT_MS   (10000u)

/* STA 连接的最大尝试次数（达到上限即放弃本轮，不无限重试）。 */
#define SFM_WIFI_CONNECT_MAX_ATTEMPTS (4u)

/* 指数退避基准与上限（毫秒）。退避随尝试序号指数增长并被 MAX 钳制；下限非零
 * 保证断开后不会无限快速重连。 */
#define SFM_WIFI_BACKOFF_BASE_MS      (1000u)
#define SFM_WIFI_BACKOFF_MAX_MS       (30000u)

/* 一次性 SNTP 时间同步的等待超时（毫秒）。超时即放弃本轮同步（Req 11.3）。 */
#define SFM_WIFI_SNTP_TIMEOUT_MS      (10000u)

/*
 * SNTP「已同步」阈值：Unix 秒，墙钟 >= 该值才认为同步成功。
 * 取 2024-01-01T00:00:00Z：远晚于冷启动的 1970，又早于本项目所有服务器证书的
 * notBefore，是 TLS 时间校验能成立的一个安全下界。
 */
#define SFM_SNTP_MIN_VALID_EPOCH      ((int64_t)1704067200)

/*********************************************************************
 * TYPEDEFS
 */
/*
 * 「连接 + SNTP」门控的判定结果，决定是否放行 manifest HTTPS。
 */
typedef enum {
    /* STA 已连接且时间已同步 —— 放行 manifest HTTPS。 */
    WIFI_GATE_PROCEED = 0,

    /* STA 未连接（连接失败/超时）—— 放弃本轮同步、降级 BLE 兜底、保画面。 */
    WIFI_GATE_ABORT_NO_LINK = 1,

    /* STA 已连接但 SNTP 未成功 —— 放弃本轮同步、降级 BLE 兜底，不进入证书时间
     * 校验失败的重试循环（Req 11.3）。 */
    WIFI_GATE_ABORT_NO_TIME = 2,
} wifi_sync_gate_result_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 判定墙钟时间是否可视为「SNTP 已同步」。
 *
 * 纯谓词：@p unix_time_s >= SFM_SNTP_MIN_VALID_EPOCH。冷启动的 1970 及任何早于
 * 阈值的时间都返回 false。
 *
 * @param unix_time_s  当前墙钟时间（自 Unix 纪元起的秒数，可为任意 int64）
 * @return true 当且仅当时间已推进到可信近期时刻
 */
bool wifi_sync_gate_time_synced(int64_t unix_time_s);

/**
 * @brief 计算「连接 + SNTP」门控判定（是否放行 manifest HTTPS）。
 *
 * 纯函数：
 *   - sta_connected == false            → WIFI_GATE_ABORT_NO_LINK；
 *   - sta_connected && !time_synced      → WIFI_GATE_ABORT_NO_TIME（Req 11.3）；
 *   - sta_connected &&  time_synced      → WIFI_GATE_PROCEED。
 *
 * @param sta_connected  STA 是否已连接并取得 IP
 * @param time_synced    SNTP 是否已成功（通常来自 wifi_sync_gate_time_synced）
 * @return wifi_sync_gate_result_t 门控判定
 */
wifi_sync_gate_result_t wifi_sync_gate_eval(bool sta_connected, bool time_synced);

/**
 * @brief 门控是否放行 manifest HTTPS。
 *
 * 等价于 (@p result == WIFI_GATE_PROCEED)，是发起 manifest HTTPS 前必须满足的
 * 前置条件（Req 11.2）。
 *
 * @param result  wifi_sync_gate_eval() 的返回值
 * @return true 当且仅当允许发起 manifest HTTPS
 */
bool wifi_sync_gate_https_allowed(wifi_sync_gate_result_t result);

/**
 * @brief 门控结果的可读字符串（用于 last_sync_error 记录 / 日志）。
 *
 * 返回文本恒不含 SSID/密码/令牌等机密，可直接落盘与打日志。永不返回 NULL。
 *
 * @param result  wifi_sync_gate_eval() 的返回值
 * @return const char* 稳定的静态字符串
 */
const char *wifi_sync_gate_result_str(wifi_sync_gate_result_t result);

/**
 * @brief 是否还应继续下一次 STA 连接尝试（有限退避的次数上界）。
 *
 * 纯谓词：@p attempts_done < @p max_attempts。
 *
 * @param attempts_done  已经完成的连接尝试次数
 * @param max_attempts   允许的最大尝试次数（如 SFM_WIFI_CONNECT_MAX_ATTEMPTS）
 * @return true 当且仅当仍允许再尝试一次
 */
bool wifi_connect_should_retry(uint32_t attempts_done, uint32_t max_attempts);

/**
 * @brief 计算第 @p attempt_index 次重试前应等待的退避时长（毫秒，有界）。
 *
 * 纯函数：min(SFM_WIFI_BACKOFF_BASE_MS << attempt_index, SFM_WIFI_BACKOFF_MAX_MS)，
 * 对大 @p attempt_index 做移位饱和保护，返回值恒落在 [BASE_MS, MAX_MS]。
 *
 * @param attempt_index  0 表示第一次重试前的退避（即第 2 次尝试之前），依次递增
 * @return uint32_t 退避毫秒数，落在 [BASE_MS, MAX_MS]
 */
uint32_t wifi_connect_backoff_ms(uint32_t attempt_index);

/**
 * 在调用方已持有临界区时完成不可分割的“检查并占用”状态转移。
 * pending 画面未完成时必须拒绝新同步，避免同名槽位覆盖其版本身份。
 */
bool wifi_sync_run_try_enter(bool *in_progress, bool display_pending);

/** 在调用方已持有临界区时释放同步运行状态。 */
void wifi_sync_run_leave(bool *in_progress);

#ifdef __cplusplus
}
#endif

#endif /* __WIFI_SYNC_GATE_H__ */
