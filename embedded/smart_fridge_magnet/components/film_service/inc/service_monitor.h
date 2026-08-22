#ifndef __SERVICE_MONITOR_H__
#define __SERVICE_MONITOR_H__


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
// LED管理参数
#define MONITOR_LED_UPDATE_INTERVAL_MS      (500)    // LED状态更新间隔 500ms
#define MONITOR_LED_BLINK_ON_TICKS          (1)      // LED闪烁点亮tick数 (1 * 500ms = 500ms亮)
#define MONITOR_LED_BLINK_OFF_TICKS         (3)      // LED闪烁熄灭tick数 (3 * 500ms = 1500ms灭)

// 电池管理参数
#define MONITOR_BAT_CHECK_INTERVAL_MS       (30000)  // 电池检测间隔 30s
#define MONITOR_BAT_LOW_THRESHOLD           (10)     // 低电量阈值 10%
#define MONITOR_BAT_CRITICAL_THRESHOLD      (5)      // 极低电量阈值 5%

// 自动休眠管理参数
#define MONITOR_SLEEP_CHECK_INTERVAL_MS     (200)    // 休眠检测间隔 200ms
#define MONITOR_AUTO_SLEEP_TIMEOUT_SEC      (60)     // 自动休眠超时时间(手动唤醒) 1min (60s)
#define MONITOR_AUTO_SLEEP_TIMEOUT_SEC_LOW  (20)     // 自动休眠超时时间(自动唤醒) 20s (20s)

/*********************************************************************
* TYPEDEFS
*/
enum
{
    MSG_LED_MANAGER = 0x01,
    MSG_BATTERY_MANAGER,
    MSG_AUTO_SLEEP_MANAGER,
    /* Wi-Fi 链路自愈巡检。走消息而不是在软定时器回调里直接做：回调跑在 FreeRTOS
     * 的 Timer 服务任务上，那里栈很浅、且一旦阻塞会拖住所有软定时器；而巡检要读
     * 文件事务状态（可能加锁）并可能发起连接。 */
    MSG_WIFI_LINK_WATCH,
    MSG_MANUAL_SYNC_OK,
};

typedef struct
{
    uint8_t ID;
} monitor_msg_t;

/*
 * 唤醒原因（低功耗管理器唤醒/休眠状态机的入口分类；design.md「唤醒/休眠状态机」，
 * Req 16.1、17.1）。冷启动与定时唤醒共用「定时/冷启动同步时序」分支；
 * 按键唤醒走「BLE 广播窗口」分支。由 esp_sleep_get_wakeup_causes() 位图映射，
 * 按键优先（按键与定时器同时触发时用户正在等反馈）：
 *   ESP_SLEEP_WAKEUP_GPIO/EXT0/EXT1           -> MONITOR_WAKE_BUTTON
 *   ESP_SLEEP_WAKEUP_TIMER                    -> MONITOR_WAKE_TIMER
 *   无唤醒源                                   -> MONITOR_WAKE_COLD_BOOT
 * 放在公共头以便 的按键分支复用同一分类。
 */
typedef enum
{
    MONITOR_WAKE_COLD_BOOT = 0,   /* 上电冷启动（非深睡唤醒） */
    MONITOR_WAKE_TIMER,           /* 定时器唤醒（轮询周期到期） */
    MONITOR_WAKE_BUTTON,          /* 按键/GPIO 唤醒（BLE 窗口） */
} monitor_wake_reason_t;

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
extern void service_monitor_init(void);

/**
 * @brief 启动一次性唤醒时序分派任务
 *
 * 唤醒时序会向 film 服务投递消息，必须在所有 service init 完成之后调用。
 */
extern void service_monitor_start_wake_dispatch(void);

/**
 * @brief 通知 monitor 任务清除手动同步成功前遗留的每日重试状态。
 *
 * 只清失败重试，不设置 synced_today；可从普通任务上下文调用。
 */
extern void service_monitor_notify_manual_sync_ok(void);

#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_MONITOR_H__ */
