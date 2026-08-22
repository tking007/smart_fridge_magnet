/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/wifi_link_watch.h
 * Description: 醒着期间的 Wi-Fi 链路自愈判定（纯逻辑）。
 *
 *   WIFI_EVENT_STA_DISCONNECTED 的处理器刻意不调用 esp_wifi_connect()，避免事件
 *   回调里的快速重连风暴（Req 11）；重连编排在 service_wifi_sync_prepare() 的有界
 *   退避里，因而只在一次同步被触发时才发生。结果是「设备醒着且同步已结束」这段时间
 *   无人负责链路：调试模式下由本模块恢复连接；低功耗模式不启用常驻自愈，断线交给
 *   下一次主动同步或计划唤醒处理，避免清醒窗口内反复拉起 Wi-Fi 射频。
 *
 *   退避到上限后继续按固定间隔重试而不彻底放弃：路由器重启或换 AP 的恢复时间从几十秒
 *   到几分钟不等，若停手则 AP 恢复后设备仍长期离线。这与 Req 11 不冲突 —— Req 11 禁
 *   的是毫秒级的 fast reconnect，这里最快也要等 SFM_LINK_WATCH_BACKOFF_BASE_MS。
 *
 *   仅依赖标准 C 头，不触碰 ESP-IDF / FreeRTOS / NVS，可在宿主机上直接编译测试。
 *
 *********************************************************************/
#ifndef __WIFI_LINK_WATCH_H__
#define __WIFI_LINK_WATCH_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>
#include <stddef.h>
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
/* 两次自愈尝试之间的最小间隔（毫秒）。取 5 秒而非 1 秒：链路刚断时 AP 往往正在
 * 重启，立刻重连只是白耗一次射频唤醒；非零下界同时避免退化成 Req 11 禁止的快速
 * 重连风暴。 */
#define SFM_LINK_WATCH_BACKOFF_BASE_MS   (5000u)

/* 退避上限（毫秒）。到达后保持该间隔持续重试，不再增长也不放弃。60 秒是「路由器
 * 恢复后最坏多久能自动连上」的可感知上界。 */
#define SFM_LINK_WATCH_BACKOFF_MAX_MS    (60000u)

/*********************************************************************
 * TYPEDEFS
 */
/* 一次链路巡检的输入快照。全部由调用方（service_monitor 的节拍）填入，本模块不
 * 持有状态，使依赖 ESP-IDF 的状态采集留在调用方、判定逻辑可在宿主机穷举。 */
typedef struct {
    /* 是否启用常驻链路自愈。低功耗模式为 false，调试模式为 true。 */
    bool enabled;
    /* 是否已配网（SSID 非空）。没配过网时重连无意义。 */
    bool provisioned;
    /* 当前链路是否已连上（含拿到 IP）。 */
    bool link_up;
    /* 是否有 BLE 传输或同步流程正在进行。忙时不插手：sync_prepare 自带有界退避
     * 重连，两边同时 connect 只会互相打断。 */
    bool busy;
    /* 当前单调时钟（毫秒）。允许回绕 —— 判定用无符号减法。 */
    uint32_t now_ms;
    /* 上一次尝试（或检测到断链）的时刻。 */
    uint32_t last_attempt_ms;
    /* 自上次连上以来的连续失败次数，只用于计算退避间隔，不用于放弃。 */
    uint32_t attempts;
} wifi_link_watch_input_t;

/* 判定结果。每种「跳过」单独成值而非统一 false，以便日志能说清为什么没重连。 */
typedef enum {
    /* 低功耗模式不维持常驻 Wi-Fi。 */
    WIFI_LINK_WATCH_SKIP_DISABLED = 0,
    /* 没配过网，无从连接。 */
    WIFI_LINK_WATCH_SKIP_NOT_PROVISIONED,
    /* 链路正常，无需动作（调用方应借此机会把 attempts 归零）。 */
    WIFI_LINK_WATCH_SKIP_LINK_UP,
    /* 有传输或同步在进行，让路。 */
    WIFI_LINK_WATCH_SKIP_BUSY,
    /* 距上次尝试还没到退避间隔。 */
    WIFI_LINK_WATCH_SKIP_BACKOFF,
    /* 应当发起一次重连尝试。 */
    WIFI_LINK_WATCH_CONNECT,
} wifi_link_watch_action_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 计算第 attempts 次失败后应等待的间隔（毫秒）。
 *
 * 指数增长并被上限钳制：BASE, 2*BASE, 4*BASE, … 直到 MAX，之后恒为 MAX。
 * 返回值恒落在 [BASE, MAX]，因此不存在「间隔为 0 的忙等」。
 * 对很大的 attempts 做移位饱和保护，避免未定义行为与回绕。
 *
 * @param attempts 连续失败次数（0 表示第一次尝试前的等待）
 * @return uint32_t 落在 [BASE, MAX] 的等待毫秒数
 */
uint32_t wifi_link_watch_backoff_ms(uint32_t attempts);

/**
 * @brief 判定这一拍是否应当发起链路自愈重连。
 *
 * 纯函数。判定顺序即优先级：禁用 > 未配网 > 链路正常 > 忙 > 退避未到。
 * 时间比较用无符号减法 `now - last`，单调时钟回绕（约 49.7 天）时最坏只多等一轮
 * 退避；用有符号差值则会在回绕后长期为负而彻底停摆。
 *
 * @param in 输入快照；NULL 时返回 [WIFI_LINK_WATCH_SKIP_DISABLED]
 * @return wifi_link_watch_action_t 应采取的动作
 */
wifi_link_watch_action_t wifi_link_watch_eval(const wifi_link_watch_input_t *in);

#ifdef __cplusplus
}
#endif

#endif /* __WIFI_LINK_WATCH_H__ */
