/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_service/src/service_monitor.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/16
 * Description: 监控服务
 *
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 * Upstream: FrameFilm (GPL-3.0-or-later) — see ../../NOTICE.md and LICENSE.
 *   Requirements: 16.1, 17.1, 17.2, 17.4 (16.2/16.3/16.4/14.3/17.3/18.1 wired via
 *   reused modules).
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <string.h>
#include <time.h>              /* 每日计划唤醒需要当地时间（time / localtime_r） */

#include "esp_sleep.h"
#include "esp_attr.h"
#include "esp_system.h"
#include "esp_timer.h"         /* 本次清醒时长（深睡唤醒等价重启，从 0 起算） */
/* FreeRTOS.h 必须留在 queue.h/task.h/timers.h 之前：那几个头文件开头就是
 * #error "include FreeRTOS.h must appear in source files before ..."。
 * clangd 的 unused-includes 会把它误判为未使用（符号可经 task.h 间接可见），勿删。 */
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "freertos/timers.h"   /* 一次性广播窗口软定时器（xTimerGetExpiryTime/
                                * xTimerIsTimerActive/xTimerChangePeriod）。基线已用软定时器，
                                * 此处显式引入以使用窗口计时相关 API。 */

#include "sys_log.h"

#include "hal_api.h"
#include "hal_led.h"
#include "hal_bat.h"
#include "hal_key.h"
#include "hal_epd.h"
#include "hal_pwr.h"
#include "service_ble_gatts.h"
#include "service_monitor.h"
#include "service_param.h"

/* 定时/冷启动唤醒同步时序所需的服务接口与纯决策模块（均只读使用）。
 *  - service_wifi.h      : service_wifi_sync_once()（Phase③ 公有同步入口，内部含单写者
 *                          让路门控）与 service_wifi_download_get_state()（Wi-Fi 忙探测）。
 *  - service_file.h      : service_file_get_receive_state()（文件事务快照）+ file_txn.h
 *                          的 file_source_t/file_txn_state_t 枚举。
 *  - service_film.h      : service_film_get_load_complete()（显示/刷新进行中代理信号）。
 *  - poll_interval.h     : monitor_clamp_poll_interval()（轮询周期钳制）。
 *  - sleep_decision.h    : monitor_can_deep_sleep()/monitor_activity_t（休眠互斥）。 */
#include "service_wifi.h"
#include "service_wifi_ota.h"   /* Wi-Fi OTA：确认镜像 + 执行升级 */
#include "service_file.h"
#include "service_film.h"
#include "poll_interval.h"
#include "sleep_decision.h"
#include "wake_schedule.h"     /* 每日计划唤醒 + 兜底周期的纯决策逻辑 */

/*********************************************************************
 * MACROS
 */
#define MONITOR_TAG                              "MONITOR"

#define MONITOR_MSG_QUEUE_LENGTH                 30
#define MONITOR_MSG_QUEUE_ITEM_SIZE              sizeof( monitor_msg_t )

#define SYS_OS_PRI_MONITOR_TASK                  (7)
#define SYS_OS_SIZE_MONITOR_TASK                 (4096)
#define SYS_OS_NAME_MONITOR_TASK                 "monitor_task"

/* one-shot wake-reason dispatch task. It runs the timer/cold-boot
 * Wi-Fi sync sequence (blocking connect/SNTP/HTTPS/download inside
 * service_wifi_sync_once), so it needs a TLS-capable stack comparable to the
 * Wi-Fi sync task (see SFM_WIFI_SYNC_TASK_STACK in service_wifi.c). Runs below
 * the periodic monitor task so LED/battery/sleep ticks stay responsive. */
#define SYS_OS_PRI_WAKE_SYNC_TASK                (5)
#define SYS_OS_SIZE_WAKE_SYNC_TASK               (10240)
#define SYS_OS_NAME_WAKE_SYNC_TASK               "mon_wake_sync"

/* Settle delay before the wake-sync sequence touches other services: film/file
 * tasks create their queues on entry (the film task itself waits ~2s before its
 * boot display), so wait a bit so a committed .film's display refresh can be
 * enqueued. The blocking connect backoff dwarfs this anyway. */
#define MONITOR_WAKE_SYNC_START_DELAY_MS         (2500)

/* NEXT 长按「立即请求重算菜单」的一次性任务：POST → 等待 → 同步。
 * 栈需求与 wake-sync 任务同级（同样要跑 TLS 握手 + esp_http_client + 流式下载），
 * 优先级同样低于周期监控任务，免得阻塞 LED/电量/休眠节拍。 */
#define SYS_OS_PRI_REFRESH_TASK                  (5)
#define SYS_OS_SIZE_REFRESH_TASK                 (10240)
#define SYS_OS_NAME_REFRESH_TASK                 "mon_refresh"

/* 请求重算后等多久再去同步（毫秒）。
 *
 * 30 秒的依据：后端收到请求后立即返回 202，真正的生成在它的后台线程里做，其中一次
 * AI 生成实测约 25 秒。等太短必然拉到旧版本 —— 而版本门控会判定「已是最新」直接结束，
 * 用户看到画面没变，会以为这个按键没用。留 5 秒余量覆盖渲染与落盘。
 *
 * 若 30 秒后仍未更新（模型偶发更慢、或后端限流拒了本次请求），可再用 PREV 长按手动
 * 同步一次；本动作不做内部重试——重试会把等待时间叠加成分钟级，按键动作不该那么久。 */
#define MONITOR_REFRESH_WAIT_MS                  (30000)

/* 按键唤醒 BLE 广播+可连接窗口时长（秒）。
 *  - MONITOR_BLE_ADV_WINDOW_SEC : Req 17.1 规定的默认广播窗口 60s，给手机连接、推送
 *    .film 或重新配网留出时间；窗口期 ble_adv_window_active=true 经休眠互斥禁睡（Req 17.3）。
 *  - MONITOR_BLE_SHORT_WINDOW_SEC: Req 17.2 规定「本次唤醒的 Wi-Fi 同步失败则进入休眠前
 *    保留一段短暂 BLE 可连接窗口」的兜底时长；取一个明显短于主窗口、又足够手机降级 BLE
 *    直传建立连接的值（30s）。用作「至少保留」的下限，不会缩短仍较长的主窗口剩余。 */
#define MONITOR_BLE_ADV_WINDOW_SEC               (60)
#define MONITOR_BLE_SHORT_WINDOW_SEC             (30)
/* 右侧单键长按开启的长广播窗口（秒）。60s 够配网但传一整张 .film（209120 字节）可能不够，
 * 窗口到期会在传输中途允许深睡；3 分钟给整图直传留足余量。窗口期禁睡，故不设为无限。 */
#define MONITOR_BLE_LONG_WINDOW_SEC              (180)
#define MONITOR_DAILY_SYNC_MAGIC                 (0x53464D19u)

/* 等电子纸刷完再回报上屏结果的上限（毫秒）。
 * 一整屏 792x528 六色刷新实测十几秒，25s 留出余量；超时不算失败，结果会由下一次唤醒
 * 的请求头带上去。取值不宜再大：这段时间 Wi-Fi 一直连着，等待成本直接记在电池上。 */
#define MONITOR_APPLIED_REPORT_WAIT_MS           (25000)

#define MONITOR_TIMER_BASE_INTERVAL_MS           (100)
#define MONITOR_MAX_AWAKE_SECONDS                (900u)
#define MONITOR_SHUTDOWN_GRACE_SECONDS           (60u)
#define MONITOR_DROP_LOG_INTERVAL_US             (60LL * 1000000LL)
/* 做饭时段检查节拍：每 60s 一次。时段边界是分钟粒度，且真正的画面切换只在跨窗口那一刻
 * 发生一次（service_film 内部按「类别变化」去重），因此这个频率既够用又不会反复刷屏。 */
#define MONITOR_MEAL_CHECK_TICK_COUNT            (60000 / MONITOR_TIMER_BASE_INTERVAL_MS)
/* Wi-Fi 链路自愈巡检节拍：每 5s 一次。真正的重连节奏由 wifi_link_watch 的退避决定
 * （5s 起、60s 上限），这里只需要巡检频率不低于最小退避间隔，否则退避到点后还要多等
 * 一整拍才发起重连。 */
#define MONITOR_LINK_WATCH_TICK_COUNT            (5000 / MONITOR_TIMER_BASE_INTERVAL_MS)

#define MONITOR_LED_TICK_COUNT                   (MONITOR_LED_UPDATE_INTERVAL_MS / MONITOR_TIMER_BASE_INTERVAL_MS)
#define MONITOR_BAT_TICK_COUNT                   (MONITOR_BAT_CHECK_INTERVAL_MS / MONITOR_TIMER_BASE_INTERVAL_MS)
#define MONITOR_SLEEP_TICK_COUNT                 (MONITOR_SLEEP_CHECK_INTERVAL_MS / MONITOR_TIMER_BASE_INTERVAL_MS)
#define MONITOR_AUTO_SLEEP_TICK_COUNT            (MONITOR_AUTO_SLEEP_TIMEOUT_SEC * 1000 / MONITOR_SLEEP_CHECK_INTERVAL_MS)
#define MONITOR_AUTO_SLEEP_TICK_COUNT_LOW        (MONITOR_AUTO_SLEEP_TIMEOUT_SEC_LOW * 1000 / MONITOR_SLEEP_CHECK_INTERVAL_MS)

/* LED 指示节奏参数。以 MONITOR_LED_UPDATE_INTERVAL_MS(500ms) 为一 tick：
 *   慢闪 = 亮 500ms / 灭 1500ms（沿用既有 BLINK_ON/OFF_TICKS）
 *   快闪 = 亮 500ms / 灭 500ms（用于告警与进行中的操作）
 *   常亮 = 全周期点亮 */
#define MONITOR_LED_FAST_ON_TICKS                (1)
#define MONITOR_LED_FAST_OFF_TICKS               (1)
#define MONITOR_LED_SOLID_ON_TICKS               (1)
#define MONITOR_LED_BRIGHTNESS_IDLE              (5)    /* 空闲/背景提示，沿用原亮度 */
#define MONITOR_LED_BRIGHTNESS_ACTIVE            (10)   /* 进行中的操作，稍亮以便看清 */
#define MONITOR_LED_BRIGHTNESS_ALERT             (20)   /* 电量告警，必须显眼 */
/* 呼吸近似：500ms 一格，共 8 格约 4s 一个来回 */
#define MONITOR_LED_BREATHE_STEPS                (8)
#define MONITOR_LED_BREATHE_GAIN                 (4)

/* 充电状态推断参数。硬件未把充电 IC 的 STAT 引脚引到 GPIO，只能由电压趋势推断，
 * 故阈值取得比 ADC 噪声明显大（30s 采样间隔内充电通常可上升数十 mV）。 */
#define MONITOR_CHARGE_RISE_MV                   (30)
#define MONITOR_BAT_FULL_MV                      (4130)

/*********************************************************************
* TYPEDEFS
*/
/* 充电状态（仅用于 LED 指示与日志，不参与控制决策） */
typedef enum
{
    MONITOR_CHARGE_UNKNOWN = 0,
    MONITOR_CHARGE_DISCHARGING,
    MONITOR_CHARGE_CHARGING,
    MONITOR_CHARGE_FULL,
} monitor_charge_state_t;

/* 单路 LED 指示样式：颜色 + 闪烁节奏 + 亮度 + 是否呼吸 */
typedef struct
{
    uint32_t color;
    uint32_t on_ticks;
    uint32_t off_ticks;
    uint32_t brightness;
    bool     breathe;
} monitor_led_channel_t;

/* 两颗灯珠的分工：灯 0 只讲「设备本身怎么样」，灯 1 只讲「连接/传输怎么样」。
 * 两路各自解析、各自计闪烁相位，渲染时一次写下去（见 monitor_led_manage_event）。 */
typedef struct
{
    monitor_led_channel_t sys;    /* 灯0：系统/电源（电量、充电、活着） */
    monitor_led_channel_t link;   /* 灯1：连接/传输（BLE、Wi-Fi、刷屏） */
} monitor_led_pattern_t;

typedef struct
{
    uint8_t ble_connected;
    uint8_t bat_level;
    /* 两路各自独立的闪烁周期计数：合用一个计数器会让两路节奏被绑在一起
     * （例如系统灯的慢闪周期会把连接灯的快闪相位一起拖走），失去分工的意义。 */
    uint8_t led_state_sys;                    /* 灯0 闪烁周期计数 */
    uint8_t led_state_link;                   /* 灯1 闪烁周期计数 */
    uint8_t led_breathe_phase;                /* 呼吸相位计数（只有灯0 的充电呼吸用得到） */
    uint16_t last_bat_voltage;                /* 上次采样电压（mV），用于充电趋势推断 */
    monitor_charge_state_t charge_state;      /* 充电状态推断结果 */
    uint32_t sleep_counter;
    uint8_t last_encoder_state;
    uint32_t tick_counter;
    uint32_t wakeup_ticks;

    /* 唤醒原因分派与 Wi-Fi 活动计数 */
    monitor_wake_reason_t wake_reason;   /* 本次唤醒原因（init 时锁定） */
    /* 本管理器自身的 Wi-Fi 活动进行中：作为 wifi_busy 喂入休眠互斥，覆盖 file 事务尚未
     * 打开的连接/SNTP/manifest 阶段（Req 16.2）。
     *
     * **是计数器而不是布尔**：有三条互不知情的路径要禁睡 —— 定时唤醒同步、按键唤醒同步、
     * NEXT 长按的 refresh 等待。前两条跑在同一个一次性任务上互不重叠，但长按本身就是唤醒
     * 动作：按住 NEXT 唤醒设备后，按键唤醒同步很可能还在跑（连接退避 + TLS + manifest +
     * 可能四张下载，几十秒量级），refresh 任务同时也置了标志。用布尔则谁先结束谁把它清零，
     * 另一方的禁睡承诺当场失效，而 refresh 那段 30 秒纯等待最容易被空闲计数追上。
     *
     * 用 int32_t 而不是无符号：配对失衡时（将来有人漏了一次 leave）负值在日志里一眼可见，
     * 而无符号会回绕成一个巨大的正数、变成「永不休眠」这种更难发现的形态。 */
    volatile int32_t wifi_activity_refs;
    volatile bool ble_adv_window_active; /* 按键唤醒 BLE 广播+可连接窗口活动标志（驱动）：
                                          * 开窗时置真、由一次性软定时器到期回调清零，经
                                          * monitor_collect_activity() 喂入休眠互斥（Req 17.3/17.4）。 */
    bool shutdown_requested;
    uint32_t shutdown_deadline_seconds;
} monitor_state_t;

typedef struct
{
    uint32_t magic;
    int32_t day_key;
    uint8_t synced_today;
    uint8_t failure_count;
    uint16_t retry_delay_minutes;
    int64_t retry_at_epoch;
} monitor_daily_sync_t;

/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */
static TaskHandle_t m_monitor_task_hdl = NULL;
static TaskHandle_t m_monitor_wake_sync_task_hdl = NULL;   /* 唤醒同步一次性任务 */
/* NEXT 长按触发的「请求重算 → 等待 → 同步」一次性任务句柄。非 NULL 即表示上一次请求
 * 还在等待窗口内，用于拒绝连按重复投递（连按会向后端连打请求，而后端也有限流）。 */
static TaskHandle_t m_monitor_refresh_task_hdl = NULL;
static QueueHandle_t m_monitor_msg_hdl = NULL;
static TimerHandle_t m_monitor_timer = NULL;
static TimerHandle_t m_ble_adv_window_timer = NULL;        /* 一次性广播窗口计时 */
static monitor_state_t m_monitor_state;
RTC_DATA_ATTR static monitor_daily_sync_t m_daily_sync;
static portMUX_TYPE m_monitor_drop_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t m_monitor_dropped_ticks;
static int64_t m_monitor_last_drop_log_us;

/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void monitor_task_handle(void *pvParameters);
static void monitor_msg_send(void *p_msg, bool in_isr);
static void monitor_timer_callback(TimerHandle_t xTimer);

static void monitor_led_manage_event(void);
static void monitor_battery_manage_event(void);
static void monitor_auto_sleep_manage_event(void);
static void monitor_enter_low_power(void);
static void monitor_request_shutdown(const char *reason, const monitor_activity_t *act);
static uint32_t monitor_awake_seconds(void);
static void monitor_log_dropped_ticks(void);
static void monitor_key_fn_short_cb(void);
static void monitor_key_fn_long_cb(void);
static void monitor_key_prev_long_cb(void);
static void monitor_key_next_long_cb(void);
static void monitor_refresh_request_task(void *pvParameters);
static void monitor_resolve_led_pattern(monitor_led_pattern_t *out);
static void monitor_resolve_sys_channel(monitor_led_channel_t *out);
static void monitor_resolve_link_channel(monitor_led_channel_t *out,
                                         const monitor_activity_t *act);
static void monitor_led_channel_render(const monitor_led_channel_t *ch,
                                       uint8_t *state,
                                       uint32_t *out_color,
                                       uint32_t *out_brightness);
static void monitor_update_charge_state(void);
static const char* monitor_charge_state_str(monitor_charge_state_t state);
static void monitor_encoder_activity_cb(void);

/* wake-reason dispatch + timer/cold-boot Wi-Fi sync sequence +
 * deep-sleep re-entry decision (all reuse the pure decision modules). */
static monitor_wake_reason_t monitor_determine_wake_reason(void);
static bool monitor_is_provisioned(void);
static void monitor_collect_activity(monitor_activity_t *act);
static void monitor_wifi_activity_enter(void);
static void monitor_wifi_activity_leave(void);
static uint32_t monitor_poll_wakeup_minutes(void);
static void monitor_arm_deep_sleep_wakeup(void);
static void monitor_timer_wake_sequence(void);
static bool monitor_local_day_key(int32_t *out);
static void monitor_daily_sync_reset(void);
static void monitor_daily_sync_refresh_day(void);
static void monitor_daily_sync_succeeded(void);
static void monitor_daily_sync_failed(void);
static void monitor_daily_retry_clear(void);
static uint32_t monitor_daily_retry_seconds(void);
static void monitor_display_local_if_stale(void);
static void monitor_try_firmware_update(void);
static void monitor_wake_sync_task(void *pvParameters);

/* 按键唤醒 BLE 广播+可连接窗口（打开/短窗兜底/到期回调）与按键唤醒时序。 */
static void monitor_ble_adv_window_timer_cb(TimerHandle_t xTimer);
static void monitor_open_ble_adv_window(uint32_t seconds);
static void monitor_ble_adv_window_ensure_min(uint32_t seconds);
static void monitor_button_wake_sequence(void);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */


void service_monitor_init(void)
{
    memset(&m_monitor_state, 0, sizeof(monitor_state_t));
    m_monitor_state.bat_level = 100;
    m_monitor_state.led_state_sys = 0;
    m_monitor_state.led_state_link = 0;
    m_monitor_state.tick_counter = 0;

    /* classify the wake reason ONCE at init and share it with the
     * wake dispatch (timer/cold-boot vs button). Timer wakes use the shorter
     * idle timeout before re-entering deep sleep; manual/cold use the longer
     * window so an operator can interact. */
    m_monitor_state.wake_reason = monitor_determine_wake_reason();
    if(m_monitor_state.wake_reason == MONITOR_WAKE_COLD_BOOT ||
       m_daily_sync.magic != MONITOR_DAILY_SYNC_MAGIC)
    {
        monitor_daily_sync_reset();
    }
    monitor_daily_sync_refresh_day();
    m_monitor_state.wifi_activity_refs = 0;
    m_monitor_state.ble_adv_window_active = false;   /* 由按键唤醒的广播窗口驱动 */

    if(m_monitor_state.wake_reason == MONITOR_WAKE_TIMER) // 定时唤醒
    {
        m_monitor_state.wakeup_ticks = MONITOR_AUTO_SLEEP_TICK_COUNT_LOW;
    }
    else // 冷启动 / 按键唤醒
    {
        m_monitor_state.wakeup_ticks = MONITOR_AUTO_SLEEP_TICK_COUNT;
    }

    if(m_monitor_task_hdl == NULL)
    {
        if ( pdPASS != xTaskCreate( monitor_task_handle, SYS_OS_NAME_MONITOR_TASK, SYS_OS_SIZE_MONITOR_TASK, NULL, SYS_OS_PRI_MONITOR_TASK, NULL ))
        {
            sys_loge(MONITOR_TAG, "monitor task create error!");
        }
    }

    if(m_monitor_timer == NULL)
    {
        m_monitor_timer = xTimerCreate( "monitor_timer", pdMS_TO_TICKS(MONITOR_TIMER_BASE_INTERVAL_MS), pdTRUE, NULL, monitor_timer_callback );
        SYS_ERROR_CHECK(m_monitor_timer == NULL);
    }
    xTimerStart(m_monitor_timer, 0);

    /* 任意按键只重置休眠倒计时；PREV/NEXT 不打开 BLE、不联网。 */
    for(int key = 0; key < KEY_ID_MAX; key++)
    {
        hal_key_register_cb((key_id_t)key, KEY_EVENT_SHORT, monitor_encoder_activity_cb);
        hal_key_register_cb((key_id_t)key, KEY_EVENT_LONG, monitor_encoder_activity_cb);
    }

    hal_key_register_cb(KEY_ID_FN, KEY_EVENT_SHORT, monitor_key_fn_short_cb);

    /* 右侧单键专责「蓝牙控制」：长按开一个更长的广播窗口，用于配网或传输整张 .film
     * （209120 字节在 60s 内可能传不完，短窗口会中途断开）。 */
    hal_key_register_cb(KEY_ID_FN, KEY_EVENT_LONG, monitor_key_fn_long_cb);

    /* 侧边两键长按提供两个设备侧入口，免得用户为此掏手机：
     *   PREV 长按 -> 立即 Wi-Fi 同步（等价 BLE 0x44）
     *   NEXT 长按 -> 请求后端重算菜单，等约 30 秒后再同步一次拿新版本 */
    hal_key_register_cb(KEY_ID_PREV, KEY_EVENT_LONG, monitor_key_prev_long_cb);
    hal_key_register_cb(KEY_ID_NEXT, KEY_EVENT_LONG, monitor_key_next_long_cb);
}

void service_monitor_notify_manual_sync_ok(void)
{
    monitor_msg_t msg = {.ID = MSG_MANUAL_SYNC_OK};
    if(m_monitor_msg_hdl == NULL)
    {
        sys_logw(MONITOR_TAG, "manual sync result before monitor queue init");
        return;
    }
    (void)xQueueSend(m_monitor_msg_hdl, &msg, portMAX_DELAY);
}

/* 唤醒时序里的侧键翻页会投递 film 消息，而 film 队列在 service_film_init() 才建好，
 * 投递到空队列会被静默丢弃。故本函数必须在所有 service init 完成之后调用。 */
void service_monitor_start_wake_dispatch(void)
{
    if(m_monitor_wake_sync_task_hdl != NULL) return;

    if(pdPASS != xTaskCreate(monitor_wake_sync_task, SYS_OS_NAME_WAKE_SYNC_TASK,
                             SYS_OS_SIZE_WAKE_SYNC_TASK, NULL, SYS_OS_PRI_WAKE_SYNC_TASK,
                             &m_monitor_wake_sync_task_hdl))
    {
        sys_loge(MONITOR_TAG, "wake sync task create error!");
    }
}

static void monitor_task_handle(void *pvParameters)
{
    m_monitor_msg_hdl = xQueueCreate( MONITOR_MSG_QUEUE_LENGTH, MONITOR_MSG_QUEUE_ITEM_SIZE );
    SYS_ERROR_CHECK(m_monitor_msg_hdl == NULL);

    for(;;)
    {
        monitor_msg_t msg;
        SYS_ERROR_CHECK( xQueueReceive( m_monitor_msg_hdl, (void *const)&msg, portMAX_DELAY ) != pdPASS );
        monitor_log_dropped_ticks();

        switch(msg.ID)
        {
        case MSG_LED_MANAGER :
            monitor_led_manage_event();
            break;
        case MSG_BATTERY_MANAGER :
            monitor_battery_manage_event();
            break;
        case MSG_AUTO_SLEEP_MANAGER :
            monitor_auto_sleep_manage_event();
            break;
        case MSG_WIFI_LINK_WATCH :
            service_wifi_link_watch_tick();
            break;
        case MSG_MANUAL_SYNC_OK :
            monitor_daily_retry_clear();
            sys_logi(MONITOR_TAG, "manual sync ok -> daily retry cleared");
            break;
        default :
            break;
        }
    }
}

static void monitor_msg_send(void *p_msg, bool in_isr)
{
    if(m_monitor_msg_hdl != NULL)
    {
        if(in_isr == 0)
        {
            /* 不能阻塞等待：本分支的全部调用方都在 monitor_timer_callback 里，跑在
             * FreeRTOS 定时器服务任务上。monitor 任务处理变慢时队列填满，portMAX_DELAY
             * 会把定时器服务任务永久挂住 —— 于是 100ms 节拍停摆（不再产生休眠判定）、
             * BLE 广播窗口的一次性定时器永不到期（ble_adv_window_active 恒真），设备
             * 永久不睡。这些都是周期性节拍消息，丢一拍下一拍还会来，远优于挂死。 */
            if(xQueueSend(m_monitor_msg_hdl, p_msg, 0) != pdPASS)
            {
                portENTER_CRITICAL(&m_monitor_drop_mux);
                m_monitor_dropped_ticks++;
                portEXIT_CRITICAL(&m_monitor_drop_mux);
            }
        }
        else
        {
            BaseType_t xHigherPriorityTaskWoken;
            xHigherPriorityTaskWoken = pdFALSE;
            xQueueSendFromISR( m_monitor_msg_hdl, p_msg, &xHigherPriorityTaskWoken );
            portYIELD_FROM_ISR( xHigherPriorityTaskWoken );
        }
    }
}

static void monitor_log_dropped_ticks(void)
{
    int64_t now = esp_timer_get_time();
    if(now - m_monitor_last_drop_log_us < MONITOR_DROP_LOG_INTERVAL_US) return;

    portENTER_CRITICAL(&m_monitor_drop_mux);
    uint32_t dropped = m_monitor_dropped_ticks;
    m_monitor_dropped_ticks = 0;
    portEXIT_CRITICAL(&m_monitor_drop_mux);

    if(dropped > 0)
    {
        m_monitor_last_drop_log_us = now;
        sys_logw(MONITOR_TAG, "monitor queue dropped %u periodic messages",
                 (unsigned)dropped);
    }
}

static void monitor_timer_callback(TimerHandle_t xTimer)
{
    monitor_msg_t msg;

    m_monitor_state.tick_counter++;

    if((m_monitor_state.tick_counter % MONITOR_LED_TICK_COUNT) == 0)
    {
        msg.ID = MSG_LED_MANAGER;
        monitor_msg_send(&msg, 0);
    }

    if((m_monitor_state.tick_counter % MONITOR_BAT_TICK_COUNT) == 0)
    {
        msg.ID = MSG_BATTERY_MANAGER;
        monitor_msg_send(&msg, 0);
    }

    if((m_monitor_state.tick_counter % MONITOR_SLEEP_TICK_COUNT) == 0)
    {
        msg.ID = MSG_AUTO_SLEEP_MANAGER;
        monitor_msg_send(&msg, 0);
    }

    /* 按做饭时间窗口自动切换画面（中午显示午餐详情，窗口外显示今日总览）。
     * 每分钟检查一次足够：时段边界是分钟粒度，切换只在跨窗口那一刻发生一次。
     * 判定与保护期逻辑都在 service_film 内，这里只负责提供节拍。 */
    if((m_monitor_state.tick_counter % MONITOR_MEAL_CHECK_TICK_COUNT) == 0)
    {
        service_film_auto_display_tick();
    }
    /* Wi-Fi 链路自愈：断连事件刻意不重连（避免 Req 11 的 infinite fast reconnect），
     * 而重连只编排在 sync_prepare 里 —— 于是「醒着且同步已结束」这段时间没人负责链路，
     * 关掉路由器再打开设备不会自己连回去。这里只投消息（判定与退避在 wifi_link_watch
     * 纯模块里，由 monitor 任务执行）：本回调跑在 FreeRTOS Timer 服务任务上，栈浅且
     * 阻塞会拖住所有软定时器，不适合读锁保护的状态或发起连接。 */
    if((m_monitor_state.tick_counter % MONITOR_LINK_WATCH_TICK_COUNT) == 0)
    {
        msg.ID = MSG_WIFI_LINK_WATCH;
        monitor_msg_send(&msg, 0);
    }
}

static const char* monitor_charge_state_str(monitor_charge_state_t state)
{
    switch(state)
    {
    case MONITOR_CHARGE_DISCHARGING: return "discharging";
    case MONITOR_CHARGE_CHARGING:    return "charging";
    case MONITOR_CHARGE_FULL:        return "full";
    default:                         return "unknown";
    }
}

/* [monitor_resolve_sys_channel 灯0（系统/电源）应显示什么]
 *
 * 只讲「设备本身怎么样」：电量与充电。优先级 first-match：
 *   1) 电量 <5%（MONITOR_BAT_CRITICAL_THRESHOLD）：红快闪、亮度 20。设备即将因低电
 *      自动深睡（见 monitor_battery_manage_event），必须给出最强提示。
 *   2) 电量 <10%（MONITOR_BAT_LOW_THRESHOLD）：红慢闪、亮度 20。
 *   3) 已充满：绿色弱常亮、亮度 5。
 *   4) 充电中：橙色呼吸、亮度 10（呼吸基准，实际亮度由渲染时的相位计算）。
 *   5) 其余（正常放电、醒着）：极弱白慢闪、亮度 5——「活着但没什么事」。
 *
 * 充电状态是电压趋势推断出来的（见 monitor_update_charge_state），可能误判；误判只会
 * 让这颗灯的颜色不准，不影响任何控制决策。 */
static void monitor_resolve_sys_channel(monitor_led_channel_t *out)
{
    /* 默认：醒着但空闲——极弱白慢闪 */
    out->color      = LED_COLOR_WHITE;
    out->on_ticks   = MONITOR_LED_BLINK_ON_TICKS;
    out->off_ticks  = MONITOR_LED_BLINK_OFF_TICKS;
    out->brightness = MONITOR_LED_BRIGHTNESS_IDLE;
    out->breathe    = false;

    if(m_monitor_state.bat_level < MONITOR_BAT_CRITICAL_THRESHOLD)
    {
        out->color      = LED_COLOR_RED;
        out->on_ticks   = MONITOR_LED_FAST_ON_TICKS;
        out->off_ticks  = MONITOR_LED_FAST_OFF_TICKS;
        out->brightness = MONITOR_LED_BRIGHTNESS_ALERT;
        return;
    }

    if(m_monitor_state.bat_level < MONITOR_BAT_LOW_THRESHOLD)
    {
        out->color      = LED_COLOR_RED;
        out->brightness = MONITOR_LED_BRIGHTNESS_ALERT;
        return;
    }

    if(m_monitor_state.charge_state == MONITOR_CHARGE_FULL)
    {
        out->color      = LED_COLOR_GREEN;
        out->on_ticks   = MONITOR_LED_SOLID_ON_TICKS;
        out->off_ticks  = 0;
        out->brightness = MONITOR_LED_BRIGHTNESS_IDLE;
        return;
    }

    if(m_monitor_state.charge_state == MONITOR_CHARGE_CHARGING)
    {
        out->color      = LED_COLOR_ORANGE;
        out->on_ticks   = MONITOR_LED_SOLID_ON_TICKS;
        out->off_ticks  = 0;
        out->brightness = MONITOR_LED_BRIGHTNESS_ACTIVE;
        out->breathe    = true;
        return;
    }
}

/* [monitor_resolve_link_channel 灯1（连接/传输）应显示什么]
 *
 * 只讲「跟外面的往来怎么样」，判据全部取自 monitor_collect_activity() 汇总的显式活动
 * 标志。优先级 first-match（越靠前越「正在发生」）：
 *   1) 正在刷电子纸：白常亮、亮度 10。刷屏时画面会来回闪变，常亮白灯说明「属正常」。
 *   2) BLE 接收文件中：蓝快闪、亮度 10。
 *   3) Wi-Fi 同步中（连接/SNTP/manifest/下载）：青（LIGHT_BLUE）快闪、亮度 10。
 *   4) BLE 已连接（未在传）：蓝常亮、亮度 10——「可以开始推送了」。
 *   5) BLE 广播窗口开着：蓝慢闪、亮度 5——「现在可以连我」。
 *   6) 其余：**灭**（BLACK + 亮度 0）。
 *
 * 最后那条「空闲且无连接就灭」是刻意的：一来省电（这颗灯在无事发生时完全不亮），
 * 二来正因为它平时是灭的，一旦亮起就明确意味着「有事在发生」——常亮着一个背景色
 * 反而会让真正的事件淹没在噪声里。设备是否醒着由灯0 表达，不需要这颗灯重复。 */
static void monitor_resolve_link_channel(monitor_led_channel_t *out,
                                         const monitor_activity_t *act)
{
    /* 默认：灭 */
    out->color      = LED_COLOR_BLACK;
    out->on_ticks   = MONITOR_LED_SOLID_ON_TICKS;
    out->off_ticks  = 0;
    out->brightness = 0;
    out->breathe    = false;

    if(act->epd_refreshing)
    {
        out->color      = LED_COLOR_WHITE;
        out->brightness = MONITOR_LED_BRIGHTNESS_ACTIVE;
        return;
    }

    if(act->ble_receiving)
    {
        out->color      = LED_COLOR_BLUE;
        out->on_ticks   = MONITOR_LED_FAST_ON_TICKS;
        out->off_ticks  = MONITOR_LED_FAST_OFF_TICKS;
        out->brightness = MONITOR_LED_BRIGHTNESS_ACTIVE;
        return;
    }

    if(act->wifi_busy)
    {
        out->color      = LED_COLOR_LIGHT_BLUE;
        out->on_ticks   = MONITOR_LED_FAST_ON_TICKS;
        out->off_ticks  = MONITOR_LED_FAST_OFF_TICKS;
        out->brightness = MONITOR_LED_BRIGHTNESS_ACTIVE;
        return;
    }

    if(act->ble_connected)
    {
        out->color      = LED_COLOR_BLUE;
        out->brightness = MONITOR_LED_BRIGHTNESS_ACTIVE;
        return;
    }

    if(act->ble_adv_window_active)
    {
        out->color      = LED_COLOR_BLUE;
        out->on_ticks   = MONITOR_LED_BLINK_ON_TICKS;
        out->off_ticks  = MONITOR_LED_BLINK_OFF_TICKS;
        out->brightness = MONITOR_LED_BRIGHTNESS_IDLE;
        return;
    }
}

/* [monitor_resolve_led_pattern 由当前设备状态解析出两颗灯各自的指示样式]
 *
 * 两颗灯分工：灯0 只管设备本身（电量/充电），灯1 只管对外往来（BLE/Wi-Fi/刷屏），
 * 各自维护独立的优先级表与闪烁相位。若把所有状态挤进一张优先级表 first-match 取一个，
 * 电量告警与传输状态必有一方被掩盖 —— 低电时传图只见红闪，用户会以为设备坏了。
 *
 * hal_led_set_color() 对所有像素写同一个 r/g/b，故渲染时算出两路各自的
 * color+brightness，用 hal_led_set_pixels() 一次写下去（WS2812 整串刷新，一次写完才不会
 * 出现只改了一半的中间帧）。
 *
 * 所有判据都复用既有状态，不新增探测，也不参与任何控制决策（仅驱动指示灯）。
 */
static void monitor_resolve_led_pattern(monitor_led_pattern_t *out)
{
    monitor_activity_t act;

    if(out == NULL)
    {
        return;
    }

    monitor_collect_activity(&act);
    m_monitor_state.ble_connected = act.ble_connected ? 1 : 0;

    monitor_resolve_sys_channel(&out->sys);
    monitor_resolve_link_channel(&out->link, &act);
}

/* [monitor_led_channel_render 把一路样式在当前 tick 上折算成实际的 color+brightness]
 *
 * 闪烁靠调用方持有的周期计数器（*state）实现：每 tick（MONITOR_LED_UPDATE_INTERVAL_MS
 * = 500ms）前进一格，落在 on 段就点亮、落在 off 段就输出黑色。两路各自传入自己的
 * 计数器，故节奏互不干扰。
 *
 * 呼吸只有灯0 的充电态用得到，因此呼吸相位仍是全局唯一一份（m_monitor_state.
 * led_breathe_phase）——为一个用不到的通道也配一份相位只是徒增状态。 */
static void monitor_led_channel_render(const monitor_led_channel_t *ch,
                                       uint8_t *state,
                                       uint32_t *out_color,
                                       uint32_t *out_brightness)
{
    uint32_t blink_cycle = ch->on_ticks + ch->off_ticks;

    if(blink_cycle == 0)
    {
        blink_cycle = 1;
    }

    *state = (uint8_t)((*state + 1) % blink_cycle);

    if(*state >= ch->on_ticks)
    {
        /* 熄灭段 */
        *out_color      = LED_COLOR_BLACK;
        *out_brightness = 0;
        return;
    }

    *out_color      = ch->color;
    *out_brightness = ch->brightness;

    /* 呼吸效果：LED 更新周期为 500ms，做不了平滑渐变，故用几级亮度循环近似
     * （充电状态属于长时间背景提示，粗粒度渐变已足够表达「正在充」）。 */
    if(ch->breathe)
    {
        uint32_t phase = m_monitor_state.led_breathe_phase % MONITOR_LED_BREATHE_STEPS;
        uint32_t up    = (phase < (MONITOR_LED_BREATHE_STEPS / 2))
                         ? phase
                         : (MONITOR_LED_BREATHE_STEPS - phase);
        *out_brightness = MONITOR_LED_BRIGHTNESS_IDLE +
                          (up * MONITOR_LED_BREATHE_GAIN);
        m_monitor_state.led_breathe_phase++;
    }
}

static void monitor_led_manage_event(void)
{
    monitor_led_pattern_t pattern;
    uint32_t sys_color = LED_COLOR_BLACK, sys_bright = 0;
    uint32_t link_color = LED_COLOR_BLACK, link_bright = 0;

    monitor_resolve_led_pattern(&pattern);

    monitor_led_channel_render(&pattern.sys, &m_monitor_state.led_state_sys,
                               &sys_color, &sys_bright);
    monitor_led_channel_render(&pattern.link, &m_monitor_state.led_state_link,
                               &link_color, &link_bright);

    /* 一次写两颗、只刷一次串。灯0=系统/电源，灯1=连接/传输；若真机上左右装反，
     * 只需在这里把两组参数互换（这是全工程唯一的调用点）。 */
    hal_led_set_pixels(sys_color, sys_bright, link_color, link_bright);
}

/* [monitor_update_charge_state 依据电压趋势推断充电状态]
 *
 * 本硬件没有充电 IC 的 STAT 引脚接到 GPIO，固件无法直接读到「是否在充电」——这正是
 * 之前「插着线整夜仍被耗尽、固件毫不知情」的原因之一。此处用电压趋势做推断：
 * 每次电池检测（30s）与上次采样电压比较，上升超过阈值判为充电、下降超过阈值判为放电，
 * 变化在阈值内则维持原状态（滤除 ADC 噪声）。
 *
 * 局限（务必知悉）：这是推断而非测量，充满后涓流阶段电压几乎不动，可能仍显示为放电；
 * 温度与负载突变也会造成误判。因此该状态【仅用于 LED 指示】，不参与休眠、关机或任何
 * 控制决策，误判只会让灯色不准，不会影响功能。若后续硬件把充电 STAT 引脚引出到 GPIO，
 * 应改为直接读引脚并删除本推断。
 */
static void monitor_update_charge_state(void)
{
    int voltage = hal_bat_get_voltage();

    if(voltage <= 0)
    {
        return;
    }

    /* 首次采样只记录基准，不做判断 */
    if(m_monitor_state.last_bat_voltage == 0)
    {
        m_monitor_state.last_bat_voltage = (uint16_t)voltage;
        return;
    }

    /* 与【判定基准】比较，而不是与上一次采样比较。
     * 真机日志显示慢充时 30s 内只上升几 mV（3846 -> 3852），若每次采样都刷新参照点，
     * 变化量永远攒不到阈值，状态会一直停在 unknown。改为只在做出判定时才更新基准，
     * 让缓慢但持续的变化能够累积到阈值。 */
    int delta = voltage - (int)m_monitor_state.last_bat_voltage;

    if(delta >= MONITOR_CHARGE_RISE_MV)
    {
        m_monitor_state.charge_state = MONITOR_CHARGE_CHARGING;
        m_monitor_state.last_bat_voltage = (uint16_t)voltage;
    }
    else if(delta <= -MONITOR_CHARGE_RISE_MV)
    {
        m_monitor_state.charge_state = MONITOR_CHARGE_DISCHARGING;
        m_monitor_state.last_bat_voltage = (uint16_t)voltage;
    }

    /* 接近满压且当前判定为充电 -> 视为已充满 */
    if(m_monitor_state.charge_state == MONITOR_CHARGE_CHARGING &&
       voltage >= MONITOR_BAT_FULL_MV)
    {
        m_monitor_state.charge_state = MONITOR_CHARGE_FULL;
    }
}

static void monitor_battery_manage_event(void)
{
    m_monitor_state.bat_level = (uint8_t)hal_bat_get_level();

    monitor_update_charge_state();

    /* 把电压与充电状态一并打出来：只有百分比时，无法判断设备是在充电还是在跌向欠压，
     * 这对排查「设备为什么突然扫不到」很关键。 */
    sys_logi(MONITOR_TAG, "battery level: %d%%, voltage: %d mV, charge: %s",
             m_monitor_state.bat_level,
             hal_bat_get_voltage(),
             monitor_charge_state_str(m_monitor_state.charge_state));

    if(m_monitor_state.bat_level < MONITOR_BAT_CRITICAL_THRESHOLD)
    {
        /* 注意：这条深睡路径不受 sleep_mode 开关约束，是电量保护的最后一道闸。
         * 充电中不应触发，否则插着电反而会睡死、且醒不过来（无法自恢复）。 */
        if(m_monitor_state.charge_state == MONITOR_CHARGE_CHARGING ||
           m_monitor_state.charge_state == MONITOR_CHARGE_FULL)
        {
            sys_logw(MONITOR_TAG,
                     "battery critical low but charging, stay awake to recover");
            return;
        }
        monitor_activity_t act;
        monitor_collect_activity(&act);
        monitor_request_shutdown("battery critical", &act);
    }
}

static void monitor_auto_sleep_manage_event(void)
{
    if(!g_service_param.sleep.sleep_mode && !m_monitor_state.shutdown_requested)
    {
        m_monitor_state.sleep_counter = 0;
        return;
    }

    /* deep-sleep RE-ENTRY decision (Req 16.2/16.4/17.3, Property 13).
     * Gate the idle countdown on the pure休眠互斥谓词 monitor_can_deep_sleep()
     * over the FULL activity set, not just BLE-connected: any in-progress BLE
     * connection/receive, Wi-Fi sync (this manager's own wake-sync or a legacy
     * download), file transaction (RECEIVING/VALIDATING), e-paper refresh, or
     * BLE adv window keeps the device awake and resets the countdown. This is
     * the single place the runtime honors monitor_can_deep_sleep() and thus
     * "依据显式完成结果判断任务结束，不依赖固定延迟" (Req 16.4). */
    monitor_activity_t act;
    monitor_collect_activity(&act);
    m_monitor_state.ble_connected = act.ble_connected ? 1 : 0;   /* keep for LED */
    uint32_t awake_seconds = monitor_awake_seconds();

    if(!m_monitor_state.shutdown_requested &&
       awake_seconds >= MONITOR_MAX_AWAKE_SECONDS)
    {
        monitor_request_shutdown("awake limit", &act);
        monitor_collect_activity(&act);
    }

    if(m_monitor_state.shutdown_requested)
    {
        if(!monitor_can_deep_sleep(&act))
        {
            if(awake_seconds >= m_monitor_state.shutdown_deadline_seconds)
            {
                sys_loge(MONITOR_TAG, "shutdown grace expired with active work, restarting");
                esp_restart();
            }
            return;
        }

        sys_logi(MONITOR_TAG, "shutdown work quiesced -> deep sleep");
        monitor_arm_deep_sleep_wakeup();
        monitor_enter_low_power();
        return;
    }

    if(!monitor_can_deep_sleep(&act))
    {
        /* 窗口到期会把计数推进到阈值，作为“任务结束即睡”的锁存值；不能被仍在收尾的
         * Wi-Fi/文件/刷屏活动清零。其它活动仍重启普通空闲计时。 */
        if(m_monitor_state.sleep_counter < m_monitor_state.wakeup_ticks)
        {
            m_monitor_state.sleep_counter = 0;
        }
        return;
    }

    /* No pending activity: count down the idle window, then re-enter deep sleep
     * after arming the timer wake period (Req 16.1). */
    m_monitor_state.sleep_counter++;
    if(m_monitor_state.sleep_counter >= m_monitor_state.wakeup_ticks)
    {
        sys_logi(MONITOR_TAG, "idle & no active work -> deep sleep");
        monitor_arm_deep_sleep_wakeup();
        monitor_enter_low_power();
    }
}

static uint32_t monitor_awake_seconds(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000000LL);
}

static void monitor_request_shutdown(const char *reason, const monitor_activity_t *act)
{
    if(m_monitor_state.shutdown_requested) return;

    uint32_t awake_seconds = monitor_awake_seconds();
    m_monitor_state.shutdown_requested = true;
    m_monitor_state.shutdown_deadline_seconds = awake_seconds + MONITOR_SHUTDOWN_GRACE_SECONDS;
    m_monitor_state.sleep_counter = m_monitor_state.wakeup_ticks;

    sys_logw(MONITOR_TAG,
             "%s at %us, request shutdown "
             "(ble_conn=%d ble_rx=%d wifi_busy=%d wifi_refs=%d txn=%d epd=%d adv=%d)",
             reason, (unsigned)awake_seconds,
             (int)act->ble_connected, (int)act->ble_receiving, (int)act->wifi_busy,
             (int)m_monitor_state.wifi_activity_refs, (int)act->file_txn_state,
             (int)act->epd_refreshing, (int)act->ble_adv_window_active);

    if(m_ble_adv_window_timer != NULL) (void)xTimerStop(m_ble_adv_window_timer, 0);
    m_monitor_state.ble_adv_window_active = false;
    service_ble_gatt_server_uninit();
    (void)service_file_receive_abort();
}

static void monitor_enter_low_power(void)
{
    xTimerStop(m_monitor_timer, 0);

    service_ble_gatt_server_uninit();

    /* 深睡前显式关射频。esp_deep_sleep_start() 自己会强关，但只要有一条路径最终没睡成
     * （唤醒源注册失败改走 esp_restart，或别处把休眠判定卡住），Wi-Fi 就会整段时间保持
     * 关联，功耗是深睡的几百倍。幂等：未初始化时函数内部直接返回。
     * 原先只有 monitor_wifi_activity_leave() 在引用计数归零时才关，漏掉了这条必经路径。 */
    service_wifi_deinit();

    hal_led_deinit();
    hal_bat_deinit();
    hal_sd_deinit();

    /* 面板先进 DSLP 再拆 SPI —— DSLP 必须在 SPI 还可用时发出。
     * 之前只有 service_film 的刷屏收尾会发 DSLP，而多数唤醒（manifest 判定已是最新）
     * 根本不刷屏，面板就整段深睡期间停在非 DSLP 状态。
     * 与 hal_pwr_enter_sleep() 的断电是两道互补措施：DSLP 让面板自己收敛内部升压电路，
     * 断电才能覆盖 WS2812、SDnand 这类没有 sleep 命令、只能靠断电归零的器件。 */
    (void)hal_epd_pwroff();
    hal_epd_deinit();

    hal_key_deinit();

    // 关闭外设供电并进入休眠
    hal_pwr_enter_sleep();
}

/* 任意按键活动只重置休眠倒计时。 */
static void monitor_encoder_activity_cb(void)
{
    m_monitor_state.sleep_counter = 0;
    sys_logi(MONITOR_TAG, "key activity -> reset idle timer");
}

static void monitor_key_fn_short_cb(void)
{
    m_monitor_state.sleep_counter = 0;
    monitor_ble_adv_window_ensure_min(MONITOR_BLE_ADV_WINDOW_SEC);
    sys_logi(MONITOR_TAG, "FN short press -> BLE window %d s",
             MONITOR_BLE_ADV_WINDOW_SEC);
}

/* [monitor_key_fn_long_cb 右侧单键长按：开启长广播窗口]
 *
 * 60s 的默认窗口足够手机连上并下发配网命令，但不足以稳定传完一整张 .film
 * （209120 字节），窗口到期后设备可能在传输中途进入深睡。长按给出一个明显更长的
 * 窗口用于配网与整图直传。注意窗口期禁睡，因此不宜设为无限长。 */
static void monitor_key_fn_long_cb(void)
{
    m_monitor_state.sleep_counter = 0;
    monitor_ble_adv_window_ensure_min(MONITOR_BLE_LONG_WINDOW_SEC);
    sys_logi(MONITOR_TAG, "FN long press -> BLE window %d s",
             MONITOR_BLE_LONG_WINDOW_SEC);
}

/* [monitor_key_prev_long_cb 侧边上键长按：立即做一次 Wi-Fi 同步]
 *
 * 与 BLE 0x44（SYNC_NOW）等价，提供一个不必掏手机的设备侧入口。
 *
 * 未配网时不要空跑：service_wifi_sync_now() 会去连一个不存在的 SSID，走完整套有界退避
 * 才失败（十几秒），期间还禁睡 —— 用户按了键只看到什么都没发生。这里先判一次
 * monitor_is_provisioned()，未配网就只记一行日志说明原因。
 *
 * 注册在本文件而不是 service_film.c：Wi-Fi 同步与唤醒/休眠时序是同一件事的两面，
 * 本文件已经在处理「唤醒后要不要同步」，把手动同步也放这里，同步相关的判据只有一处。 */
static void monitor_key_prev_long_cb(void)
{
    m_monitor_state.sleep_counter = 0;

    if(!monitor_is_provisioned())
    {
        sys_logw(MONITOR_TAG, "PREV long press: 未配网，忽略手动同步请求");
        return;
    }

    sys_logi(MONITOR_TAG, "PREV long press -> immediate Wi-Fi sync");
    /* service_wifi_sync_now() 内部自建一次性任务，不会在本回调（hal_key 轮询任务）里阻塞。 */
    service_wifi_sync_now();
}

/* [monitor_refresh_request_task 「请求重算菜单 → 等待生成 → 再同步」一次性任务]
 *
 * 为什么必须放到独立任务里：整个动作要等约 30 秒，而按键回调跑在 hal_key 的轮询任务上
 * —— 在那里睡 30 秒会让消抖与长按判定全部停摆（这段时间内所有按键都失灵），用户会以为
 * 设备死机。按键回调只负责投递意图，实际执行放这里，与既有的 monitor_wake_sync_task
 * 同构。
 *
 * 全程持有一份 wifi_activity_refs：它经 monitor_collect_activity() 作为 wifi_busy 喂入
 * 休眠互斥。等待期间必须禁睡，否则设备会在「等新版本」的过程中睡过去，醒来时早已错过
 * 这次生成结果，按键等于白按。 */
static void monitor_refresh_request_task(void *pvParameters)
{
    (void)pvParameters;

    /* 请求与等待、以及随后的同步都算「Wi-Fi 忙」，全程禁睡。 */
    monitor_wifi_activity_enter();

    esp_err_t rerr = service_wifi_request_refresh();
    if(rerr != ESP_OK)
    {
        /* 已由 service_wifi_request_refresh() 记录了具体原因（含 429 限流那条）；
         * 这里不重复，只表明本次动作到此结束、不再等待与同步。 */
        sys_logw(MONITOR_TAG, "refresh request not accepted (rc=%d), skip wait+sync",
                 (int)rerr);
        monitor_wifi_activity_leave();
        m_monitor_refresh_task_hdl = NULL;
        vTaskDelete(NULL);
        return;
    }

    sys_logi(MONITOR_TAG, "refresh accepted, wait %d ms then sync",
             MONITOR_REFRESH_WAIT_MS);
    vTaskDelay(pdMS_TO_TICKS(MONITOR_REFRESH_WAIT_MS));

    esp_err_t serr = service_wifi_sync_once();
    if(serr == ESP_OK)
    {
        service_monitor_notify_manual_sync_ok();
        sys_logi(MONITOR_TAG, "post-refresh sync done (up-to-date or applied)");
    }
    else
    {
        /* 仍是旧版本或同步失败：保留当前画面，仅记非机密返回码。
         * 用户可再用 PREV 长按手动同步一次（后端可能生成得比 30 秒更慢）。 */
        sys_logw(MONITOR_TAG,
                 "post-refresh sync ended without apply (rc=%d), retry with PREV long press",
                 (int)serr);
    }

    monitor_wifi_activity_leave();
    m_monitor_refresh_task_hdl = NULL;
    vTaskDelete(NULL);
}

/* [monitor_key_next_long_cb 侧边下键长按：请求后端重算菜单，稍后拉新版本]
 *
 * 语义与「同步」不同：同步只是去看后端有没有新版本；本动作是让后端**重新生成**一份
 * （库存变了、家庭成员变了之后，用户希望立刻拿到新菜单，而不是等下一个每日窗口）。
 *
 * 回调只做三件轻量的事：重置休眠倒计时、判配网、投递一次性任务。绝不在此阻塞。 */
static void monitor_key_next_long_cb(void)
{
    m_monitor_state.sleep_counter = 0;

    if(!monitor_is_provisioned())
    {
        sys_logw(MONITOR_TAG, "NEXT long press: 未配网，忽略重算菜单请求");
        return;
    }

    if(m_monitor_refresh_task_hdl != NULL)
    {
        /* 上一次请求还在等待窗口里。连按不该变成向后端连打请求（后端也有限流）。 */
        sys_logw(MONITOR_TAG, "NEXT long press: refresh already in progress, ignored");
        return;
    }

    sys_logi(MONITOR_TAG, "NEXT long press -> request menu refresh, then sync");

    if(pdPASS != xTaskCreate(monitor_refresh_request_task, SYS_OS_NAME_REFRESH_TASK,
                             SYS_OS_SIZE_REFRESH_TASK, NULL, SYS_OS_PRI_REFRESH_TASK,
                             &m_monitor_refresh_task_hdl))
    {
        m_monitor_refresh_task_hdl = NULL;
        sys_loge(MONITOR_TAG, "refresh task create error!");
    }
}

/*====================================================================
 * 定时/冷启动唤醒同步时序（Req 16.1）
 *
 * 唤醒/休眠状态机（design.md「唤醒/休眠状态机」）在低功耗管理器中的目标机侧落地：
 *   DeepSleep --定时器唤醒--> InitTimer --已配网--> WifiSync --新版本--> Refresh
 *             --冷启动---->                                    |
 *                                                              v
 *                                                          DecideSleep --可深睡--> DeepSleep
 *
 * 本节落地 timer/cold-boot 分支、共享的唤醒原因分派与深睡再进入决策；按键唤醒
 * （InitButton -> BleWindow，Req 17）见 monitor_wake_sync_task 的 MONITOR_WAKE_BUTTON
 * 分支与文件末尾「按键唤醒 BLE 广播窗口」小节。所有决策复用纯模块（poll_interval.h /
 * sleep_decision.h / file_txn.h）与 service_wifi_sync_once() 公有入口。
 *===================================================================*/

/* [monitor_determine_wake_reason 由 esp_sleep 唤醒原因映射到共享分类]
 * GPIO/EXT0/EXT1（按键）-> BUTTON；定时器 -> TIMER；无唤醒源 -> COLD_BOOT。
 * 用 causes 位图并让按键优先：按键与定时器同时触发时用户正在等反馈。 */
static monitor_wake_reason_t monitor_determine_wake_reason(void)
{
    uint32_t causes = esp_sleep_get_wakeup_causes();
    if(causes & (BIT(ESP_SLEEP_WAKEUP_GPIO) | BIT(ESP_SLEEP_WAKEUP_EXT0) |
                 BIT(ESP_SLEEP_WAKEUP_EXT1)))
    {
        return MONITOR_WAKE_BUTTON;
    }
    if(causes & BIT(ESP_SLEEP_WAKEUP_TIMER))
    {
        return MONITOR_WAKE_TIMER;
    }
    return MONITOR_WAKE_COLD_BOOT;
}

/* [monitor_is_provisioned 是否已配网：Wi-Fi 开启且 SSID 非空]
 * 仅据此决定 timer/cold-boot 唤醒是否发起一次 Wi-Fi 同步（Req 16.1）。未配网直接跳过
 * 同步、保留当前画面，交由周期休眠管理器在空闲后按需再进入深睡。 */
static bool monitor_is_provisioned(void)
{
    return (g_service_param.network.wifi_enable != 0) &&
           (g_service_param.network.wifi_ssid[0] != '\0');
}

static bool monitor_local_day_key(int32_t *out)
{
    if(out == NULL || !service_wifi_time_is_synced()) return false;
    time_t now = 0;
    struct tm local;
    time(&now);
    if(localtime_r(&now, &local) == NULL) return false;
    *out = (int32_t)(local.tm_year + 1900) * 1000 + local.tm_yday;
    return true;
}

static void monitor_daily_sync_reset(void)
{
    memset(&m_daily_sync, 0, sizeof(m_daily_sync));
    m_daily_sync.magic = MONITOR_DAILY_SYNC_MAGIC;
    m_daily_sync.day_key = -1;
}

static void monitor_daily_sync_refresh_day(void)
{
    int32_t day = -1;
    if(!monitor_local_day_key(&day)) return;
    if(m_daily_sync.day_key != day)
    {
        m_daily_sync.day_key = day;
        m_daily_sync.synced_today = 0;
        m_daily_sync.failure_count = 0;
        m_daily_sync.retry_delay_minutes = 0;
        m_daily_sync.retry_at_epoch = 0;
        sys_logi(MONITOR_TAG, "daily sync state reset for day %ld", (long)day);
    }
}

static void monitor_daily_sync_succeeded(void)
{
    monitor_daily_sync_refresh_day();
    m_daily_sync.synced_today = 1;
    m_daily_sync.failure_count = 0;
    m_daily_sync.retry_delay_minutes = 0;
    m_daily_sync.retry_at_epoch = 0;
}

/* [monitor_daily_retry_clear 只清失败重试记账，不动 synced_today]
 *
 * 供手动同步入口使用。刻意不复用 monitor_daily_sync_succeeded()：那个会置
 * synced_today=1，而该标志会让当天后续的**定时**唤醒跳过联网、只切本地缓存图
 * （见 monitor_timer_wake_sequence）。手动同步的意图是「我现在就要最新的」，
 * 不该顺带把当天剩下的自动同步也关掉 —— 用户 10:00 手动同步过，后端 10:30 又更新了
 * 午餐菜单，11:35 那次饭点唤醒仍应该去拉。
 *
 * 于是手动成功只做一件事：把上一次自动同步失败留下的重试周期清零，让唤醒装载回到
 * 饭点时间表。「今天是否已同步」仍只由自动路径认定。 */
static void monitor_daily_retry_clear(void)
{
    monitor_daily_sync_refresh_day();
    m_daily_sync.failure_count = 0;
    m_daily_sync.retry_delay_minutes = 0;
    m_daily_sync.retry_at_epoch = 0;
}

static void monitor_daily_sync_failed(void)
{
    monitor_daily_sync_refresh_day();
    if(m_daily_sync.failure_count < 4u) m_daily_sync.failure_count++;
    uint16_t retry = wake_schedule_retry_minutes(m_daily_sync.failure_count);
    m_daily_sync.retry_delay_minutes = retry;
    m_daily_sync.retry_at_epoch = 0;
    if(retry > 0u && service_wifi_time_is_synced())
    {
        time_t now = 0;
        time(&now);
        m_daily_sync.retry_at_epoch = (int64_t)now + (int64_t)retry * 60;
    }
    sys_logw(MONITOR_TAG, "daily sync failure #%u, retry in %u min",
             (unsigned)m_daily_sync.failure_count, (unsigned)retry);
}

static uint32_t monitor_daily_retry_seconds(void)
{
    if(m_daily_sync.retry_delay_minutes == 0u) return 0u;
    if(m_daily_sync.retry_at_epoch > 0 && service_wifi_time_is_synced())
    {
        time_t now = 0;
        time(&now);
        int64_t delta = m_daily_sync.retry_at_epoch - (int64_t)now;
        return delta > (int64_t)WAKE_SCHEDULE_MIN_SECONDS
            ? (uint32_t)delta : WAKE_SCHEDULE_MIN_SECONDS;
    }
    return (uint32_t)m_daily_sync.retry_delay_minutes * 60u;
}

/* [monitor_wifi_activity_enter/leave 声明「我这条路径正在用 Wi-Fi，别睡」]
 *
 * 成对调用，中间那段经 monitor_collect_activity() 作为 wifi_busy 喂入休眠互斥。
 *
 * 为什么是引用计数而不是一个布尔：三条路径会重叠 —— 定时唤醒同步、按键唤醒同步、
 * NEXT 长按后的 refresh 等待。前两条跑在同一个一次性任务上互不重叠，但**长按本身就是
 * 唤醒动作**，所以「按键唤醒同步」与「refresh 等待」可以同时在跑。布尔的话谁先结束谁把
 * 它清零，另一方的禁睡承诺当场失效，而 refresh 那段 30 秒纯等待正是最容易被空闲计数
 * 追上的一段。
 *
 * 用临界区而不是原子内建：这里的争用极低（每次唤醒最多三次进出），而临界区的语义在
 * 任何 IDF 版本上都一致，不必依赖 __atomic_* 在 Xtensa 上的具体实现。 */
static portMUX_TYPE m_wifi_activity_mux = portMUX_INITIALIZER_UNLOCKED;

static void monitor_wifi_activity_enter(void)
{
    portENTER_CRITICAL(&m_wifi_activity_mux);
    m_monitor_state.wifi_activity_refs++;
    portEXIT_CRITICAL(&m_wifi_activity_mux);
}

static void monitor_wifi_activity_leave(void)
{
    bool wifi_idle = false;
    portENTER_CRITICAL(&m_wifi_activity_mux);
    if(m_monitor_state.wifi_activity_refs > 0)
    {
        m_monitor_state.wifi_activity_refs--;
        wifi_idle = (m_monitor_state.wifi_activity_refs == 0);
    }
    else
    {
        /* 配对失衡：多了一次 leave。夹到 0 而不是让它变负 —— 负值会让
         * `refs > 0` 恒假，把「禁睡」静默变成「随时可睡」，而这正是这个计数器要防的事。
         * 打 warning 是因为失衡本身一定是代码 bug，不该静默吞掉。 */
        sys_logw(MONITOR_TAG, "wifi activity refs underflow (unbalanced leave)");
    }
    portEXIT_CRITICAL(&m_wifi_activity_mux);

    /* 低功耗模式只在同步事务期间开启 Wi-Fi；最后一个网络任务结束后立即关射频，
     * BLE 窗口和休眠倒计时继续运行也不会让路由器看到设备反复上线。 */
    if(wifi_idle && g_service_param.sleep.sleep_mode)
    {
        service_wifi_deinit();
    }
}

/* [monitor_collect_activity 采集当前【显式】活动状态，喂入纯休眠互斥决策]
 *
 * 采集 design.md「5. 低功耗管理器」列举的活动标志（Req 16.2/16.4/17.3）：
 *   - ble_connected : service_ble_gatts_get_connect()（BLE 已连接）。
 *   - ble_receiving : 文件事务由 BLE 持有且处于进行中态（RECEIVING/VALIDATING）。
 *   - wifi_busy     : 本管理器自身的 wake-sync 进行中（覆盖 file 事务打开【之前】的
 *                     连接/SNTP/manifest 阶段——此时 file_txn 仍为 IDLE，仅靠 file 状态
 *                     无法判定 Wi-Fi 忙），或旧版固定 URL 下载路径处于 DOWNLOADING。
 *   - file_txn_state: service_file_get_receive_state().state（覆盖 Wi-Fi 流式下载与
 *                     finish 校验，及 BLE 直传）。
 *   - epd_refreshing: service_film_is_refreshing() 的专用刷新标志，仅在 EPD 送屏期间为真。
 *   - ble_adv_window_active: 由 的按键唤醒 BLE 广播窗口驱动（开窗置真、
 *                     窗口到期回调清零），窗口期禁睡（Req 17.3/17.4）。
 *
 * 全部为【显式状态】，无任何固定延迟/超时推断（Req 16.4）。 */
static void monitor_collect_activity(monitor_activity_t *act)
{
    if(act == NULL)
    {
        return;
    }

    file_receive_state_t rx = service_file_get_receive_state();

    act->ble_connected = (service_ble_gatts_get_connect() != 0);
    act->ble_receiving = (rx.owner == FILE_SRC_BLE) &&
                         (rx.state == FT_RECEIVING || rx.state == FT_VALIDATING);
    act->wifi_busy = (m_monitor_state.wifi_activity_refs > 0) ||
                     (service_wifi_download_get_state() == WIFI_DOWNLOAD_DOWNLOADING);
    act->file_txn_state = rx.state;
    act->epd_refreshing = (service_film_is_refreshing() != 0);
    act->ble_adv_window_active = m_monitor_state.ble_adv_window_active;

}

/* [monitor_poll_wakeup_minutes 由持久化轮询周期派生定时唤醒周期（分钟）]
 *
 * 轮询周期以【秒】持久化于 g_service_param.sync.poll_interval_seconds，先经纯函数
 * monitor_clamp_poll_interval() 钳制到 [30, 86400]（poll_interval.h，Req 16.3/14.3）。
 * hal_pwr_set_timer_wakeup() 仅接受【整分钟】粒度，故此处向上取整到分钟并至少 1 分钟，
 * 保证实际定时唤醒周期不短于所配置的轮询周期（30s 演示下限受硬件分钟粒度映射为 1 分钟）。 */
static uint32_t monitor_poll_wakeup_minutes(void)
{
    uint32_t secs = monitor_clamp_poll_interval(g_service_param.sync.poll_interval_seconds);
    uint32_t mins = (secs + 59u) / 60u;   /* ceil to whole minutes */
    if(mins == 0u)
    {
        mins = 1u;
    }
    return mins;
}

/* [monitor_arm_deep_sleep_wakeup 深睡前装载定时唤醒周期]
 *
 * 优先级（v4「每日更新时间 + 24 小时兜底」）：
 *  1) 已配网 且 时间可信 且 配了唤醒时间表 -> 按时间表装【秒级】唤醒；
 *  2) 已配网 但 时间不可信或没有时间表     -> 有兜底周期就用它，否则沿用轮询周期；
 *  3) 未配网                              -> 旧版 sleep_auto/sleep_time（分支不动）。
 *
 * 为什么第 1 条必须同时要求「时间可信」：唤醒时间表是【当地时钟时刻】，算的是
 * 「现在几点 → 距下一个每日更新时间还有多久」。冷启动时系统时间是 1970-01-01 00:00，
 * 由它算出的「距 18:00 还有 18 小时」是彻底错的：设备会在一个毫无意义的时刻醒来，
 * 却错过真正的更新时间。时间可信度判据复用与 Wi-Fi 同步、按时段选图同一个来源
 * （service_wifi_time_is_synced -> wifi_sync_gate_time_synced），避免三处各写一套。
 *
 * 为什么第 2 条在没有时间表时也优先用兜底：兜底以分钟为配置语义，并允许用户显式关闭；
 * 轮询周期只在兜底关闭（=0）时作为最后回落。两者出厂值都为 24 小时。 */
static void monitor_arm_deep_sleep_wakeup(void)
{
    if(monitor_is_provisioned())
    {
        /* 1) 时间可信 + 有时间表 -> 秒级饭点唤醒 */
        if(g_service_param.sleep.wake_slot_count > 0 && service_wifi_time_is_synced())
        {
            time_t now = 0;
            struct tm tm_local;
            time(&now);
            if(localtime_r(&now, &tm_local) != NULL)
            {
                uint32_t now_min = (uint32_t)tm_local.tm_hour * 60u + (uint32_t)tm_local.tm_min;
                uint32_t scheduled = wake_schedule_next_seconds(
                    now_min, g_service_param.sleep.wake_slots,
                    g_service_param.sleep.wake_slot_count,
                    g_service_param.sleep.wake_fallback_min);
                uint32_t retry = monitor_daily_retry_seconds();
                uint32_t secs = wake_schedule_earliest_seconds(scheduled, retry);
                if(secs > 0u)
                {
                    /* 顺手把「醒来时大约几点」算出来打进日志：排查「设备没在饭点醒」
                     * 时，光看秒数很难判断是时间表配错了还是时钟偏了。 */
                    uint32_t wake_min = (now_min + (secs + 59u) / 60u) % 1440u;
                    hal_pwr_set_timer_wakeup_seconds(secs);
                    sys_logi(MONITOR_TAG,
                             "arm next wake: %u sec (~%02u:%02u, slots=%u, fallback=%u min, retry=%u sec)",
                             (unsigned)secs, (unsigned)(wake_min / 60u), (unsigned)(wake_min % 60u),
                             (unsigned)g_service_param.sleep.wake_slot_count,
                             (unsigned)g_service_param.sleep.wake_fallback_min,
                             (unsigned)retry);
                    return;
                }
            }
        }

        /* 2) 时间不可信 / 没有时间表：兜底周期优先，轮询周期作为最后回落 */
        if(g_service_param.sleep.wake_fallback_min > 0u)
        {
            uint32_t fallback = (uint32_t)g_service_param.sleep.wake_fallback_min * 60u;
            uint32_t secs = wake_schedule_earliest_seconds(
                fallback, monitor_daily_retry_seconds());
            hal_pwr_set_timer_wakeup_seconds(secs);
            sys_logi(MONITOR_TAG, "arm timer wake from fallback/retry: %u sec",
                     (unsigned)secs);
            return;
        }

        uint32_t mins = monitor_poll_wakeup_minutes();
        uint32_t secs = wake_schedule_earliest_seconds(
            mins * 60u, monitor_daily_retry_seconds());
        hal_pwr_set_timer_wakeup_seconds(secs);
        sys_logi(MONITOR_TAG, "arm timer wake from poll/retry: %u sec", (unsigned)secs);
    }
    else if(g_service_param.sleep.sleep_auto && g_service_param.sleep.sleep_time > 0)
    {
        hal_pwr_set_timer_wakeup(g_service_param.sleep.sleep_time);
        sys_logi(MONITOR_TAG, "arm timer wake (legacy): %u min",
                 (unsigned)g_service_param.sleep.sleep_time);
    }
}

/* [monitor_display_local_if_stale 按当前餐次对齐本地缓存画面]
 *
 * 拿不到新内容时，至少保证屏幕显示的是**当前时段**该看的那一页。SD 卡上本来存着一套
 * 四张，早上那次唤醒可能把早餐页留在屏上，而现在是晚饭前。
 *
 * 只在该换页时才刷：电子纸刷一次约 20 秒、耗电、刷新次数有限，Wi-Fi 断一整天会
 * 白刷三次同样的内容。
 *
 * 刻意**不**在屏幕上写「同步失败」：那会把用户唯一想看的菜单顶掉，而失败原因已经在
 * last_sync_error（BLE 0x43 可读）与串口日志里。屏幕的职责是显示菜单，不是显示状态。
 */
static void monitor_display_local_if_stale(void)
{
    (void)service_file_display_if_stale_async();
}

static void monitor_try_firmware_update(void)
{
    manifest_firmware_t fw;

    if(!service_wifi_take_pending_firmware(&fw))
    {
        return;   /* 本次 manifest 没带升级 */
    }

    /* 直接读 HAL 而不是用 m_monitor_state.bat_level：那个字段由周期任务每
     * MONITOR_BAT_CHECK_INTERVAL_MS 更新一次，冷启动后可能还是初始值 100，
     * 而 OTA 是本设备最耗电的单次操作，不能拿一个可能过期的读数去放行。 */
    int battery = hal_bat_get_level();

    /* 成功时本函数不返回（内部 esp_restart）。返回即表示跳过或失败，
     * 两种情况都继续走正常的休眠流程 —— 升级不成功不该影响今天的菜单。 */
    (void)service_wifi_ota_perform(&fw, battery);
}

/* [monitor_timer_wake_sequence 定时/冷启动唤醒 -> 一次 Wi-Fi 同步（Req 16.1）]
 *
 * 硬件与参数已由 film_service_init()/app_main 在本任务运行前初始化。此处：
 *   已配网 -> 执行【一次】Wi-Fi 同步 service_wifi_sync_once()（Phase③ 公有入口，只读使用）。
 *   该入口内部：先经 service_wifi_sync_prepare() 的单写者让路门控——若已有进行中的 BLE
 *   文件事务则跳过本轮、不启动写事务（wifi_sync_should_yield_to_ble，Req 18.1）；否则
 *   connect(有界退避)->SNTP->带 Bearer 的 manifest->版本门控->有新版本则流式下载入统一
 *   文件事务->校验/原子替换/刷新->保存 last_applied_version（Req 10.5）。本函数只读其
 *   【非机密】返回码用于日志，绝不记录 token/SSID/Authorization。
 *
 * 同步全程持有一份 wifi_activity_refs 作为 wifi_busy 喂入休眠互斥，确保连接/SNTP/
 * manifest（file 事务尚未打开）阶段也禁睡；返回后清零。深睡再进入由周期休眠管理器
 * monitor_auto_sleep_manage_event() 依据 monitor_can_deep_sleep() 在空闲后决定，并由
 * monitor_arm_deep_sleep_wakeup() 以轮询周期装载下次唤醒。 */
static void monitor_timer_wake_sequence(void)
{
    /* 按需广播下，这两种唤醒对「是否需要被连接」的要求完全不同：
     *  - 冷启动（用户上电/拨开关）：很可能就是要配网或推图，开一个 60s 可连接窗口；
     *  - 定时唤醒（后台 Wi-Fi 同步）：无人交互，不需要被连接。GATT 初始化时会自动拉起
     *    广播，这里主动关掉，省去整段同步期间的广播射频开销，也避免设备在无人看管时
     *    长期可被扫描。同步失败仍会经 monitor_ble_adv_window_ensure_min() 留一段兜底窗口。
     * 休眠关闭（调试模式）时不做这些收敛，保持随时可连。 */
    if(g_service_param.sleep.sleep_mode)
    {
        if(m_monitor_state.wake_reason == MONITOR_WAKE_TIMER)
        {
            sys_logi(MONITOR_TAG, "wake=timer: background sync, adv off");
            service_ble_gatts_adv_stop();
        }
        else
        {
            sys_logi(MONITOR_TAG, "wake=cold: open BLE window for config/transfer");
            monitor_open_ble_adv_window(MONITOR_BLE_ADV_WINDOW_SEC);
        }
    }

    if(!monitor_is_provisioned())
    {
        /* 未配网：不发起同步、保留当前画面（Req 13.1/13.3 精神）；后续按需再深睡。 */
        sys_logi(MONITOR_TAG, "wake=timer/cold: not provisioned, skip Wi-Fi sync");
        return;
    }

    monitor_daily_sync_refresh_day();
    if(m_monitor_state.wake_reason == MONITOR_WAKE_TIMER && m_daily_sync.synced_today)
    {
        sys_logi(MONITOR_TAG, "wake=timer: already synced today, switch local meal film only");
        monitor_display_local_if_stale();
        return;
    }

    sys_logi(MONITOR_TAG, "wake=timer/cold: first unsynced wake -> single Wi-Fi sync");

    monitor_wifi_activity_enter();
    esp_err_t r = service_wifi_sync_once();

    if(r == ESP_OK)
    {
        monitor_daily_sync_succeeded();
        /* Wi-Fi 通了、manifest 也取到了，这就证明本固件还能接收下一次 OTA ——
         * 正是取消回滚该等的那个证据。放在这里而不是 app_main：无条件确认会让
         * 任何「能开机」的镜像都算通过，回滚保护就废了。 */
        service_wifi_ota_confirm_running_image();

        /* 单次唤醒事务：等电子纸刷完再把结果报回后端，全程在同一次唤醒里完成。
         * Wi-Fi 此时仍由 activity 引用持有，回报后才在 leave 中关闭，所以不需要重连。
         * 回报失败无害 —— 下次唤醒的 X-Device-Applied-Version 仍会带上去。 */
        esp_err_t report_rc = service_wifi_report_display_result(MONITOR_APPLIED_REPORT_WAIT_MS);
        /* manifest 已是最新时没有待上屏结果；仍需把本地缓存切到当前餐次。若有刷新在途，
         * report_rc 为成功/超时，不能再排一条相同显示任务。 */
        if(report_rc == ESP_ERR_NOT_FOUND)
        {
            monitor_display_local_if_stale();
        }

        /* 固件升级放在最后：上屏已经完成，此时重启不会丢掉这一版画面。 */
        monitor_try_firmware_update();
    }
    monitor_wifi_activity_leave();

    if(r == ESP_OK)
    {
        sys_logi(MONITOR_TAG, "wake sync done (up-to-date or applied)");
    }
    else
    {
        monitor_daily_sync_failed();
        /* 任一门控/网络/校验失败：保留当前画面、降级 BLE 兜底；仅记录非机密返回码。 */
        sys_logw(MONITOR_TAG, "wake sync ended without apply (rc=%d), keep screen", (int)r);
        monitor_display_local_if_stale();
    }
}

/* [monitor_wake_sync_task 一次性唤醒分派任务：跑完唤醒时序即自删除]
 *
 * 共享的唤醒原因分派（Req 16.1 / 17.1）：
 *   TIMER / COLD_BOOT -> monitor_timer_wake_sequence()。
 *   BUTTON            -> monitor_button_wake_sequence()：打开 BLE 广播+可连接
 *                        窗口、失败后保留短窗（Req 17.1/17.2/17.4），窗口活动经休眠互斥禁睡。 */
static void monitor_wake_sync_task(void *pvParameters)
{
    (void)pvParameters;

    /* 让 file/film 等服务任务先建好队列（film 任务自身启动后约 2s 才做开机显示），
     * 以便本轮同步提交后的显示刷新能被正常入队；阻塞式连接退避远长于此延时。 */
    vTaskDelay(pdMS_TO_TICKS(MONITOR_WAKE_SYNC_START_DELAY_MS));

    switch(m_monitor_state.wake_reason)
    {
    case MONITOR_WAKE_TIMER:
    case MONITOR_WAKE_COLD_BOOT:
        monitor_timer_wake_sequence();
        break;
    case MONITOR_WAKE_BUTTON:
        /* 按键唤醒 BLE 广播窗口 + 休眠让路（Req 17.1/17.2/17.4；窗口活动经
         * m_monitor_state.ble_adv_window_active 喂入 16.2 休眠互斥）。 */
        monitor_button_wake_sequence();
        break;
    default:
        break;
    }

    m_monitor_wake_sync_task_hdl = NULL;
    vTaskDelete(NULL);
}

/*====================================================================
 * 按键唤醒 BLE 广播窗口与休眠让路（Req 17.1/17.2/17.4）
 *
 * design.md「唤醒/休眠状态机」按键分支在低功耗管理器目标机侧的落地：
 *   DeepSleep --按键唤醒--> InitButton --> BleWindow(默认60s, A2)
 *                                            |-- 可选触发同步 --> WifiSync
 *                                            |                     |-- 失败 --> BleShortWindow
 *                                            v                     v
 *                                        DecideSleep <-------------+
 *   DecideSleep --can_deep_sleep()==true--> DeepSleep
 *
 * 窗口计时用一次性 FreeRTOS 软定时器实现，仅驱动 m_monitor_state.ble_adv_window_active
 * 这一显式活动标志；真正的「能否深睡」判定仍收敛在 的纯谓词 monitor_can_deep_sleep()
 * （由周期休眠管理器 monitor_auto_sleep_manage_event() 经 monitor_collect_activity() 调用）。
 * 本节不触碰 BLE 射频初始化（启动时已由 service_ble_init() 拉起广播、断连自动重开，
 * 仅在深睡路径 monitor_enter_low_power() 里经 service_ble_gatt_server_uninit() 关闭），
 * 也不重复「连接/接收/文件事务进行中禁睡」的既有逻辑（由其它活动标志覆盖）。
 *===================================================================*/

/* [monitor_ble_adv_window_timer_cb 广播窗口一次性定时器到期回调]
 * 窗口到期即清除 ble_adv_window_active，交还深睡决策权（Req 17.4）。若此刻仍有连接/
 * 接收/文件事务在进行，休眠互斥由 monitor_collect_activity() 采集的其它显式活动标志
 * （ble_connected/ble_receiving/file_txn_state）继续禁睡（Req 16.2/17.3），本回调不重复
 * 该逻辑、也绝不强制入睡。运行于 FreeRTOS 定时器服务任务上下文，仅置一个 volatile
 * 布尔量，非阻塞。 */
static void monitor_ble_adv_window_timer_cb(TimerHandle_t xTimer)
{
    (void)xTimer;
    m_monitor_state.ble_adv_window_active = false;

    /* 按需广播：窗口到期即停止广播，设备不再被扫描到，直到下次按键开窗（或深睡唤醒开窗）。
     *
     * 若只清标志不停广播，广播要等进深睡时才随 GATT 反初始化一起关闭，设备醒着就一直可被
     * 扫描，既有持续射频开销，也让任意设备都能随时尝试连接。
     *
     * 两条例外，都是有意为之：
     *  1) 已连接时不停广播——正在传图/配网，停广播对已建连接无影响但没必要冒风险；
     *  2) 休眠关闭时（sleep_mode=0，调试模式）保持广播常开——此时设备本就常驻不睡，
     *     省这点射频没意义，而随时可连对调试很重要。省电模式（sleep_mode=1）才按需广播。 */
    if(!g_service_param.sleep.sleep_mode)
    {
        sys_logi(MONITOR_TAG,
                 "BLE adv window expired (sleep disabled) -> keep adv for debugging");
        return;
    }

    /* 即使连接或其它任务仍忙，也保留“任务结束即睡”的意图。休眠互斥会等显式活动结束，
     * 上面的计数锁存保证结束后不再额外累计 60 秒。 */
    m_monitor_state.sleep_counter = m_monitor_state.wakeup_ticks;
    if(service_ble_gatts_get_connect())
    {
        sys_logi(MONITOR_TAG, "BLE adv window expired but connected -> sleep after disconnect");
        return;
    }

    service_ble_gatts_adv_stop();
    sys_logi(MONITOR_TAG,
             "BLE adv window expired -> adv off, evaluate deep sleep now "
             "(press any key to become connectable again)");
}

/* [monitor_open_ble_adv_window 打开按键唤醒 BLE 广播+可连接窗口（Req 17.1）]
 *
 * 广播本身已由启动时 service_ble_init() -> service_ble_gatt_server_init() 拉起，并在
 * 断连后自动重开（见 service_ble_gatts.c 的 ESP_GATTS_DISCONNECT_EVT），且仅在深睡路径
 * monitor_enter_low_power() 里经 service_ble_gatt_server_uninit() 关闭。故此处不重复
 * 初始化射频，只做「开窗」这一唤醒期承诺：
 *   1) 置 ble_adv_window_active=true——窗口期内经 monitor_collect_activity() 喂入
 *      monitor_can_deep_sleep() 恒禁睡（Req 17.3），给手机留出连接、推送 .film 或
 *      重新配网的时间；
 *   2) 用一次性软定时器计时 seconds 秒，到期回调清除该标志（Req 17.4）。
 * seconds==0 回退到默认窗口时长（Req 17.1，60s）。 */
static void monitor_open_ble_adv_window(uint32_t seconds)
{
    if(seconds == 0u)
    {
        seconds = MONITOR_BLE_ADV_WINDOW_SEC;
    }

    TickType_t period = pdMS_TO_TICKS(seconds * 1000u);

    /* 先置活动标志再启动计时，避免（理论上的）计时先于置位造成的窗口空档。 */
    m_monitor_state.ble_adv_window_active = true;

    /* 开窗即开广播（按需广播，理由见 monitor_ble_adv_window_timer_cb 的说明）。
     * 反复调用是安全的：底层只是重发一次 start_advertising。 */
    service_ble_gatts_adv_start();

    if(m_ble_adv_window_timer == NULL)
    {
        m_ble_adv_window_timer = xTimerCreate("ble_adv_win", period, pdFALSE /* one-shot */,
                                              NULL, monitor_ble_adv_window_timer_cb);
        SYS_ERROR_CHECK(m_ble_adv_window_timer == NULL);
        xTimerStart(m_ble_adv_window_timer, 0);
    }
    else
    {
        /* 复用既有定时器：改周期即（重）启动，从满窗重新计时。 */
        xTimerChangePeriod(m_ble_adv_window_timer, period, 0);
    }

    sys_logi(MONITOR_TAG, "wake=button: open BLE adv window %us", (unsigned)seconds);
}

/* [monitor_ble_adv_window_ensure_min 确保窗口至少保留一段短兜底时长（Req 17.2）]
 *
 * 本次按键唤醒的 Wi-Fi 同步失败后，进入休眠前应保留一段短暂 BLE 可连接窗口，以便手机
 * 降级到 BLE 直传兜底。语义为「至少保留 seconds 秒」：
 *   - 主窗口（Req 17.1）剩余 ≥ seconds：维持不变（不缩短仍较长的主窗口）；
 *   - 剩余 < seconds 或窗口已到期/未开：把窗口（重新）设为 seconds 并置活动标志。
 * 这样即便同步耗时吃掉大部分主窗口，也能保证失败后仍有兜底连接机会。
 * 剩余时长用 xTimerGetExpiryTime()-xTaskGetTickCount() 计算（TickType_t 无符号相减在
 * tick 回绕下对小窗口仍成立）。 */
static void monitor_ble_adv_window_ensure_min(uint32_t seconds)
{
    if(seconds == 0u)
    {
        return;
    }

    TickType_t min_ticks = pdMS_TO_TICKS(seconds * 1000u);

    /* 窗口从未创建（异常路径）：直接开一个短窗。 */
    if(m_ble_adv_window_timer == NULL)
    {
        monitor_open_ble_adv_window(seconds);
        return;
    }

    bool active = (xTimerIsTimerActive(m_ble_adv_window_timer) != pdFALSE);
    TickType_t remaining = 0;
    if(active)
    {
        remaining = xTimerGetExpiryTime(m_ble_adv_window_timer) - xTaskGetTickCount();
    }

    if(!active || remaining < min_ticks)
    {
        m_monitor_state.ble_adv_window_active = true;
        /* 与 open 路径一致地开广播：按需广播下窗口外是不广播的，这里是按键唤醒后
         * 「让手机能重新扫到设备」的关键一步。 */
        service_ble_gatts_adv_start();
        xTimerChangePeriod(m_ble_adv_window_timer, min_ticks, 0);
        /* 本函数有多个调用方（同步失败兜底、按键活动、FN 长按），不能在这里断言原因，
         * 否则会像早期版本那样把按键开窗也打成 "sync failed" 而误导排障。
         * 具体原因由各调用方自行记录。 */
        sys_logi(MONITOR_TAG, "BLE window ensured: %us", (unsigned)seconds);
    }
}

/* [monitor_button_wake_sequence 按键唤醒时序：BLE 广播窗口(+可选同步)（Req 17）]
 *
 * design.md「唤醒/休眠状态机」的 InitButton -> BleWindow(-> 可选 WifiSync -> 失败
 * BleShortWindow) -> DecideSleep 分支在目标机侧的落地：
 *   1) 打开默认 60s BLE 广播+可连接窗口（Req 17.1）。窗口期 ble_adv_window_active=true
 *      经休眠互斥禁睡（Req 17.3），给手机连接、推送 .film 或重新配网留时间。
 *   2) 已配网则借本次唤醒顺带触发一次 Wi-Fi 同步（design.md「BleWindow -> 可选触发同步」；
 *      复用 Phase③ 公有入口 service_wifi_sync_once()，其内部已含单写者让路门控 Req 18.1；
 *      同步期持有 wifi_activity_refs 亦禁睡，见 monitor_collect_activity()）。
 *      同步失败 -> monitor_ble_adv_window_ensure_min() 保证进入休眠前仍保留一段短暂
 *      BLE 可连接窗口以便降级 BLE 兜底（Req 17.2）。仅记录非机密返回码，绝不记录
 *      token/SSID/Authorization。
 *   3) 窗口到期回调清除 ble_adv_window_active（Req 17.4）；此后周期休眠管理器依
 *      monitor_can_deep_sleep() 在空闲计满后深睡。若到期时仍有连接/接收/文件事务在进行，
 *      则由其它显式活动标志继续禁睡（Req 16.2/17.3），本序列不重复该逻辑、不提前强制入睡。 */
static void monitor_button_wake_sequence(void)
{
    uint64_t keys = hal_pwr_get_wakeup_key_mask();
    bool fn = (keys & HAL_PWR_WAKE_FN_MASK) != 0;
    bool prev = (keys & HAL_PWR_WAKE_PREV_MASK) != 0;
    bool next = (keys & HAL_PWR_WAKE_NEXT_MASK) != 0;

    /* 当前硬件下 HAL_PWR_WAKE_KEY_MASK 只含 FN，因此 prev/next 恒为假、下面的 !fn 分支
     * 不可达（理由见 hal_pwr.h 掩码定义处：PREV/NEXT 的上拉挂在受控电源 3V3_C 上，
     * 断电后变成下拉，作 ANY_LOW 唤醒源会让设备进深睡的同一刻自唤醒）。
     * 保留该分支是为了硬件把 R20/R21 改接常电后，只改掩码一处即可恢复三键唤醒，
     * 不必再回来重写这段时序。 */
    if(!fn)
    {
        /* 侧键从深睡首次唤醒只执行一次本地短按语义，不开 BLE、不联网。 */
        m_monitor_state.wakeup_ticks = MONITOR_AUTO_SLEEP_TICK_COUNT_LOW;
        m_monitor_state.sleep_counter = 0;
        if(prev && next)
        {
            sys_logw(MONITOR_TAG, "wake=keys: PREV+NEXT ambiguous, no navigation");
        }
        else if(prev)
        {
            sys_logi(MONITOR_TAG, "wake=PREV: queue one local previous-film action");
            service_film_prev();
        }
        else if(next)
        {
            sys_logi(MONITOR_TAG, "wake=NEXT: queue one local next-film action");
            service_film_next();
        }
        else
        {
            sys_logw(MONITOR_TAG, "wake=keys: EXT1 mask empty, no action");
        }
        return;
    }

    if(prev || next)
    {
        sys_logw(MONITOR_TAG, "wake=FN+side key: FN control intent wins (mask=0x%llx)",
                 (unsigned long long)keys);
    }

    /* Req 17.1：打开默认 60s 广播+可连接窗口（广播已在启动时拉起，见
     * monitor_open_ble_adv_window 注释；此处开窗=窗口期保持唤醒的承诺）。 */
    monitor_open_ble_adv_window(MONITOR_BLE_ADV_WINDOW_SEC);

    if(monitor_is_provisioned())
    {
        /* design.md「BleWindow -> 可选触发同步」：已配网则借本次唤醒同步一次。 */
        sys_logi(MONITOR_TAG, "wake=button: provisioned -> single Wi-Fi sync in window");

        monitor_wifi_activity_enter();
        esp_err_t r = service_wifi_sync_once();
        if(r == ESP_OK)
        {
            monitor_daily_sync_succeeded();
            service_wifi_ota_confirm_running_image();
            /* 与定时唤醒同一处理：在同一次唤醒里把上屏结果报回后端。 */
            esp_err_t report_rc = service_wifi_report_display_result(MONITOR_APPLIED_REPORT_WAIT_MS);
            if(report_rc == ESP_ERR_NOT_FOUND)
            {
                monitor_display_local_if_stale();
            }
            monitor_try_firmware_update();
        }
        monitor_wifi_activity_leave();

        if(r == ESP_OK)
        {
            sys_logi(MONITOR_TAG, "wake=button: sync done (up-to-date or applied)");
        }
        else
        {
            /* Req 17.2：本次同步失败 -> 进入休眠前保留一段短暂 BLE 可连接窗口。 */
            sys_logw(MONITOR_TAG, "wake=button: sync ended without apply (rc=%d)", (int)r);
            monitor_ble_adv_window_ensure_min(MONITOR_BLE_SHORT_WINDOW_SEC);
            /* 按键唤醒尤其需要这一步：用户刚按了键要看新菜单，拿不到新内容时至少要让
             * 屏幕停在当前时段该看的那一页，否则「按了没反应」与「按键坏了」无法区分。 */
            monitor_display_local_if_stale();
        }
    }
    else
    {
        /* 未配网：不发起同步，保留 60s 窗口供 BLE 手动配网/兜底传图（Req 13.2 精神）。 */
        sys_logi(MONITOR_TAG, "wake=button: not provisioned, BLE window for manual config/transfer");
    }
}
