/*********************************************************************
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 * Upstream: FrameFilm (GPL-3.0-or-later) — see ../../NOTICE.md and LICENSE.
 *   Requirements: 11.1, 11.2, 11.3, 10.1, 10.5, 12.3.
 *********************************************************************/
#ifndef __SERVICE_WIFI_H__
#define __SERVICE_WIFI_H__


/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>

#include "esp_err.h"
#include "manifest_parse.h"   /* manifest_firmware_t（取出待升级固件描述用） */

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */


/*********************************************************************
* TYPEDEFS
*/
typedef enum {
    WIFI_DOWNLOAD_IDLE = 0,
    WIFI_DOWNLOAD_DOWNLOADING,
    WIFI_DOWNLOAD_DONE,
    WIFI_DOWNLOAD_ERROR
} wifi_download_state_t;


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */


/*********************************************************************
 * GLOBAL FUNCTIONS
 */
extern void service_wifi_init(void);
extern void service_wifi_deinit(void);
extern void service_wifi_connect(void);
extern void service_wifi_disconnect(void);
extern uint8_t service_wifi_get_connect_status(void);
extern void service_wifi_clear_config(void);

/*
 * 取图状态查询。
 *
 * 需要立即取图请调用 service_wifi_sync_now()，它走 manifest 门控 + HTTPS +
 * 摘要校验 + 原子替换。下面两个查询沿用上游签名，内部反映同步任务状态：
 *   - get_state()    : 忙 -> WIFI_DOWNLOAD_DOWNLOADING，否则 WIFI_DOWNLOAD_IDLE。
 *                      不产生 DONE/ERROR —— 失败原因见 sync.last_sync_error（BLE 0x3B）。
 *   - get_progress() : 流式路径不跟踪字节级进度，忙时报 0、空闲报 100，判据以 state 为准。
 */
extern uint8_t service_wifi_download_get_progress(void);
extern wifi_download_state_t service_wifi_download_get_state(void);

/*
 * STA connect + SNTP time-sync gate (Req 11).
 *
 * ESP32-S3 has no RTC, so cold-boot time is 1970 and TLS cert notBefore
 * validation would always fail without SNTP first. These are the connect/SNTP
 * building blocks plus the single gated precondition that the manifest-HTTPS
 * step MUST call before issuing any HTTPS request.
 */

/**
 * [service_wifi_connect_blocking 阻塞式 STA 连接（单次尝试，有界超时）]
 * Applies the SSID/password from parameters and waits up to @p timeout_ms for
 * an IP. Does NOT retry internally (the bounded backoff loop lives in
 * service_wifi_sync_prepare). On timeout/failure it stops the pending attempt
 * so the driver does not linger and fast-retry.
 * @return ESP_OK if connected & got IP within the timeout, else an error.
 */
extern esp_err_t service_wifi_connect_blocking(uint32_t timeout_ms);

/**
 * [service_wifi_sntp_sync 一次性 SNTP 时间同步（有界超时）]
 * Runs a ONE-SHOT SNTP sync and waits up to @p timeout_ms. Success is defined
 * as the wall clock advancing to a plausible recent time (see wifi_sync_gate).
 * It never enters a cert-time failure retry loop: it either succeeds once or
 * gives up for this cycle.
 * @return ESP_OK if time is synced within the timeout, else an error.
 */
extern esp_err_t service_wifi_sntp_sync(uint32_t timeout_ms);

/**
 * [service_wifi_sync_prepare 连接 + SNTP 门控前置条件]
 * Ensures the STA is connected (bounded backoff, 10s per attempt) and then runs
 * a one-shot SNTP time sync. Returns ESP_OK IFF manifest HTTPS is allowed
 * (connected AND time synced). On any gate failure it records a non-secret
 * last_sync_error and returns an error, so the caller abandons this sync cycle,
 * degrades to BLE fallback and keeps the current screen without entering a
 * cert-time validation retry loop.
 * @return ESP_OK when HTTPS is allowed, else an error (fallback to BLE).
 */
extern esp_err_t service_wifi_sync_prepare(void);

/**
 * [service_wifi_time_is_synced 查询墙钟时间是否已同步]
 * @return 1 if the wall clock is past the SNTP-synced threshold, else 0.
 */
extern uint8_t service_wifi_time_is_synced(void);

/**
 * [service_wifi_sync_once 执行一次完整的 Wi-Fi 同步（Req 10.1/10.5/12.3）]
 *
 * 轮询定时器与 BLE SYNC_NOW 都走这条流程：
 *   1) service_wifi_sync_prepare() 门控（BLE 忙则让路 + connect + SNTP），仅 ESP_OK 继续；
 *   2) HTTPS GET {manifest_url}，带 "Authorization: Bearer {device_token}" 与 cert
 *      bundle，读取有界响应后 manifest_parse()，要求 HTTP 200；
 *   3) 版本门控 sync_should_download(version, last_applied, force)；判否即干净结束
 *      （不下载、不刷新）；
 *   4) 下载，按 manifest 是否携带 items[] 分两条路径：
 *      a) item_count > 0（一套多张）：逐条 GET {items[i].url} 流式写入文件事务，按
 *         items[i].name 落盘，每条独立校验长度 + .film 头 + SHA-256 后原子提交。
 *         任一条目失败即中止整套：不保存 last_applied_version（下一轮重下整套），
 *         正式文件回滚为完整旧套，last_sync_error 记下失败的文件名。全部成功后才保存
 *         last_applied_version，并删掉单张落盘名 current.film —— 仅当本次 items[].name
 *         里不含它（判据 manifest_contains_item_name()，守住「不删除自己刚提交的文件」）。
 *         随后经 service_file_display_for_now_async() 触发上屏，显示哪一张由当前时段
 *         决定，而非最后下载的那一张。
 *      b) item_count == 0（后端只给了 film_url）：走单张路径，落盘 current.film 后
 *         service_file_display_received()。
 *      两条路径都逐块流式写入，整份 209120 字节绝不驻留内存/PSRAM（Req 12.3），内存
 *      占用与张数无关；都强制 Content-Length == 209120。
 *
 * Every exit path releases the HTTP client and, on failure, aborts the file
 * transaction (keeping the current screen) and records a non-secret
 * last_sync_error. The device token / Authorization header are never logged.
 * Callable directly from a task context (it may block for connect/SNTP/download).
 *
 * @return ESP_OK when a check completed (up-to-date OR downloaded+applied);
 *         an error code on any gate/network/validation failure (BLE fallback).
 */
extern esp_err_t service_wifi_sync_once(void);

/**
 * @brief Notify Wi-Fi sync that a committed film finished refreshing on the EPD.
 *
 * Download paths keep the manifest version pending in RAM. The film task calls
 * this only after hal_epd_display_film() returns; matching a pending filename is
 * what finally persists last_applied_version. A reboot or display failure leaves
 * the old version in NVS, so the next sync downloads the release again.
 */
extern void service_wifi_display_completed(const char *filename);

/** 清除匹配的 pending 画面但不推进版本，使下一轮同步重新下载并显示。 */
extern void service_wifi_display_failed(const char *filename);

/**
 * [service_wifi_report_display_result 把本次唤醒的上屏结果回报给后端]
 *
 * 向 `POST {manifest_url 同源的 .../display/applied}` 发 `{"version":N,"ok":bool}`。
 *
 * 必须在同一次唤醒里报：manifest 请求头 X-Device-Applied-Version 只能在下一次唤醒
 * 带上去，而定时唤醒之间隔着几小时。那段时间后端只知道文件被取走，无法区分「已上屏」
 * 与「落盘后刷新失败、屏幕还是旧的」，后者表现为冰箱贴着昨天的菜单而云端显示正常。
 *
 * 必须在 Wi-Fi 仍连着时调用（同步返回后、深睡前）。失败无害：下一次唤醒的请求头仍会
 * 带上结果，本函数只是让后端早几小时知道。
 *
 * @param wait_ms 等待电子纸刷新完成的上限。没有待上屏的版本时立即返回
 *                ESP_ERR_NOT_FOUND，不白等（干等等于让射频多醒这么久）。
 * @return ESP_OK 已回报；ESP_ERR_NOT_FOUND 本次无需回报；其余为超时或发送失败。
 */
extern esp_err_t service_wifi_report_display_result(uint32_t wait_ms);

/**
 * [service_wifi_take_pending_firmware 取出本次同步 manifest 带来的固件升级描述]
 *
 * 取出即清零：同一份描述不该被两次唤醒各刷一遍；下次唤醒若仍需升级，后端会再给一次。
 *
 * 由低功耗管理器在上屏完成之后调用。不在同步流程里就地刷固件，是因为画面刷新是异步
 * 排队的：在那里重启会让刚下载的这一版画面永远画不上去 —— 升级成功，但冰箱上还是
 * 昨天的菜单。
 *
 * @param out 必须非 NULL；无论返回值如何都会被写入（无升级时是全零）。
 * @return true 表示 out->has_update 为真，可以交给 service_wifi_ota_perform()。
 */
extern bool service_wifi_take_pending_firmware(manifest_firmware_t *out);

/**
 * [service_wifi_request_refresh 请求后端立即重新生成并发布菜单画面]
 *
 * 向 `POST {manifest_url 同源的 .../display/request-refresh}` 发空体 HTTPS 请求（与
 * manifest 拉取同一套凭据与校验）。URL 由 manifest_url 推导：把结尾的 "manifest" 换成
 * "request-refresh"；不以该后缀结尾时直接返回错误并记一条非机密日志，不猜地址。
 *
 * 后端立即返回 202 并在后台生成（一次 AI 生成约 25 秒），故 ESP_OK 只表示请求已受理，
 * 不代表新版本已就绪 —— 调用方需稍后再同步一次才能拿到新画面。
 *
 * 会阻塞（连接退避 + TLS 握手 + 一次往返），必须在任务上下文调用，不可在按键或定时器
 * 回调里直接调用。绝不记录 token / SSID / password。
 *
 * @return ESP_OK 已受理（HTTP 200/202）；ESP_ERR_NOT_FINISHED 被后端限流（429）；
 *         其它错误码表示门控/网络/状态码异常。
 */
extern esp_err_t service_wifi_request_refresh(void);

/*
 * [service_wifi_sync_is_busy 是否有同步正在进行]
 * 供休眠决策的 Wi-Fi 忙探测与状态查询使用。只读、无副作用，可从任意任务调用。
 */
extern bool service_wifi_sync_is_busy(void);

/* 立即触发一次一次性 Wi-Fi 同步检查（connect→SNTP→manifest），供 BLE SYNC_NOW
 * 命令调用（Req 14.1）。轮询仍为默认后台同步行为（Req 14.2）；不使用 MQTT 长连接。 */
extern void service_wifi_sync_now(void);

/*
 * [service_wifi_link_watch_tick 醒着期间的 Wi-Fi 链路自愈巡检]
 *
 * 由 service_monitor 的节拍周期调用（数秒一次）。补的空档是：断连事件刻意不重连（避免
 * Req 11 的 infinite fast reconnect），重连只编排在 service_wifi_sync_prepare() 里，
 * 于是「设备醒着且同步已结束」时无人负责链路。休眠关闭的调试模式由纯模块
 * wifi_link_watch.{h,c} 判定并按需发起一次非阻塞 service_wifi_connect()；低功耗模式
 * 不维持常驻链路，交给下一次同步重新连接。
 */
extern void service_wifi_link_watch_tick(void);


#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_WIFI_H__ */
