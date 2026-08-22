/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/src/service_film.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/22
 * Description: Film service function
 *   显示前校验当前有效文件头，仅校验通过才送屏；校验或加载失败时保留当前画面，
 *   绝不清屏（Req 8.3、7.7）。
 *
 *********************************************************************/


/*********************************************************************
 * INCLUDES
 */
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"   /* pin 配置缓存的互斥量（见 m_pin_cfg_mux） */
#include "freertos/task.h"
#include "freertos/timers.h"
#include "esp_timer.h"

#include "sys_log.h"
#include "hal_epd.h"
#include "hal_key.h"
#include "service_file.h"
#include "service_param.h"
#include "service_film.h"
#include "service_wifi.h"
#include "film_validate.h"   /* 显示前文件头校验：复用 film_validate（Req 8.3、7.7） */
#include "wifi_sync_gate.h"  /* 复用时间可信度纯谓词，避免冷启动 1970 被当成有效时间 */
#include "film_pin.h"        /* 置顶自定义画面的纯决策逻辑（三种模式 + 跨零点窗口） */

/* 做饭时间窗口（分钟，含起、不含止）。
 *
 * 语义是「**做饭时间点的大致范围**内显示那一餐的详细做法，其余时间显示今日总览」，
 * 而不是把一天三等分。因为详细做法（食材/步骤）只在准备这一餐时才有用；平时冰箱贴上
 * 更该是今日总览。窗口略宽于实际做饭时刻，留出提前看菜谱备料的时间。 */
#define FILM_BREAKFAST_START_MIN   (6 * 60)          /* 06:00 */
#define FILM_BREAKFAST_END_MIN     (9 * 60)          /* 09:00 */
#define FILM_LUNCH_START_MIN       (11 * 60)         /* 11:00 */
#define FILM_LUNCH_END_MIN         (13 * 60 + 30)    /* 13:30 */
#define FILM_DINNER_START_MIN      (17 * 60)         /* 17:00 */
#define FILM_DINNER_END_MIN        (19 * 60 + 30)    /* 19:30 */

/* 今日总览的文件名关键字。做饭窗口之外、时间不可信、或找不到当前餐段时都回落显示它。 */
#define FILM_OVERVIEW_KEYWORD      "today"

/* 手动操作后的自动切换保护期（分钟）。
 * 用户用侧边按键翻到某一张，说明他此刻正想看这张；若定时检查立刻把画面切回去，
 * 既打断用户又白刷一次 20 秒的屏。保护期内不做自动切换。 */
#define FILM_MANUAL_HOLD_MIN       (30)

/*********************************************************************
 * MACROS
 */
#define FILM_MSG_QUEUE_LENGTH       30
#define FILM_MSG_QUEUE_ITEM_SIZE    sizeof( film_msg_t )

#define SYS_OS_PRI_FILM_TASK        (5)
#define SYS_OS_SIZE_FILM_TASK       (6144)
#define SYS_OS_NAME_FILM_TASK       "film_task"

/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */

/*********************************************************************
 * LOCAL VARIABLES
 */
static TaskHandle_t m_film_task_hdl = NULL;
static QueueHandle_t m_film_msg_hdl = NULL;

/* 按时间自动切换画面的状态。
 *  - m_auto_slot        : 上一次自动切换所依据的时段，用于「只在跨时段时切换一次」。
 *                         初值 UNKNOWN，使首次判定必然触发一次对齐。
 *  - m_manual_display_ts: 最近一次用户手动翻页的时间戳（0 = 从未），用于保护期。
 *
 * 两者都跨任务访问：note_manual_display() 由按键回调（hal_key 任务）调用，
 * auto_display_tick() 由低功耗管理器（monitor 任务）调用，故需 volatile 保证可见性。
 * 时间戳用 uint32_t 秒而非 int64_t：32 位 MCU 上 64 位读写非原子，可能读到撕裂值；
 * 秒级 uint32 单次访问即原子，且量程到 2106 年，足够。 */
static volatile film_meal_slot_t m_auto_slot = FILM_MEAL_UNKNOWN;
static volatile uint32_t m_manual_display_ts = 0;

/* 上一次自动切换时「是否在显示 pinned 图」。
 *
 * 单独用一个布尔而不给 film_meal_slot_t 扩一个 FILM_MEAL_PINNED 取值：自动切换的状态本
 * 就是二维的（上次对齐到哪个餐段 × 当时是否在显示 pinned）。WINDOW 模式在午餐窗口中途
 * 结束时要从 pinned 切回午餐菜单，两维表达即 pinned 由 true 变 false 而 slot 仍是 LUNCH；
 * 一维枚举则 pinned 会覆盖掉 slot，切回来时不知道该切回哪一餐。此外 film_meal_slot_t 是
 * 对外导出的领域枚举，塞进一个非时间段的值会让每处 switch 多一个异类分支。
 *
 * 与 m_auto_slot 同为跨任务访问，故 volatile；两者由 monitor、hal_key（
 * service_film_note_manual_display）与 BLE（service_film_pin_invalidate）任务分别写入，
 * 且不是原子地一起更新。
 *
 * 不上锁的理由是后果有界：这两个值只用于「目标变了没有」这个判定，读到半新半旧的组合最多
 * 让本轮多切或少切一次图。少切的会在下一分钟的 tick 补上（tick 是周期调用、非边沿触发），
 * 多切的代价是一次 20 秒刷屏。都不会显示错内容 —— 显示什么由 display_for_now() 当场重新
 * 判定，不依赖这两个值。 */
static volatile bool m_auto_pinned = false;

/* pin 配置的进程内缓存。
 *
 * 不在每次选图时读 NVS：auto_display_tick() 由低功耗管理器按分钟调用，每分钟一次
 * flash 读虽不贵，但它换来的只是「配置可能在两秒前刚被改过」这点新鲜度 —— 而唯一的
 * 修改入口（BLE 0x4C）会显式调 service_film_pin_invalidate()，缓存不会过期。
 * 深睡唤醒是一次冷启动，缓存自然失效并重新从 NVS 载入，这正是本功能能「撑过深睡」
 * 的关键：状态在 NVS 里，不在 RAM 里。 */
static film_pin_cfg_t m_pin_cfg;
static bool m_pin_cfg_loaded = false;
/* 缓存**必须加锁**。本缓存被 monitor 任务（auto_display_tick）、hal_key 任务
 * （note_manual_display / FN 短按）、file 任务与 BLE 任务（0x4C 写入后 invalidate）
 * 四处访问，而不加锁存在一个真实的丢更新窗口：
 *
 *   1) BLE 任务处理 0x4C：写 NVS 成功，invalidate 置 loaded=false；
 *   2) monitor 任务此刻正在 service_param_pinned_load() 内部 —— 它在第 1 步之前就进了
 *      载入分支，读到的是**旧**记录；
 *   3) monitor 返回，把旧配置写回 m_pin_cfg 并置 loaded=true。
 *
 * 新配置于是在缓存里静默消失，0x4D 回读也读这份旧缓存。用户看到的是「命令发了、
 * 回读还是旧值、显示也没变」，与固件根本没收到无法区分。
 *
 * 早先这里的注释把最坏后果写成「多刷一次屏」，那个判断是错的（它以为两个任务只会各写
 * 一份相同内容），据此得出的「不值得上锁」结论也就不成立。
 *
 * 用互斥量而不是临界区：载入路径里有 NVS 读（会阻塞、会让出 CPU），在临界区里做这种事
 * 会把调度停掉几毫秒。 */
static SemaphoreHandle_t m_pin_cfg_mux = NULL;

/*********************************************************************
 * GLOBAL VARIABLES
 */

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void film_task_handle(void *pvParameters);
static void film_msg_send(void *p_msg, bool in_isr);

static void film_display_event(uint32_t file_id);
static void film_next_event(void);
static void film_prev_event(void);
static void film_init_event(void);
static void film_clear_event(void);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

void service_film_init(void)
{
    /* pin 缓存的互斥量。必须在任何 film_pin_cfg() 之前建好 —— 建不出来时那个函数会按
     * 「未设置」返回，pin 功能整体退化为关闭，而不是无锁裸跑。 */
    if(m_pin_cfg_mux == NULL)
    {
        m_pin_cfg_mux = xSemaphoreCreateMutex();
        if(m_pin_cfg_mux == NULL)
        {
            sys_loge(FILM_TAG, "pin cfg mutex create failed, pin feature disabled");
        }
    }

    /* 消息队列在建任务之前创建：本函数返回即代表 film_msg_send() 可用，
     * 调用方（含唤醒时序的侧键翻页）不会把消息投进空队列而被静默丢弃。 */
    if(m_film_msg_hdl == NULL)
    {
        m_film_msg_hdl = xQueueCreate(FILM_MSG_QUEUE_LENGTH, FILM_MSG_QUEUE_ITEM_SIZE);
        if(m_film_msg_hdl == NULL)
        {
            sys_loge(FILM_TAG, "film msg queue create error!");
            return;
        }
    }

    if(m_film_task_hdl == NULL)
    {
        if ( pdPASS != xTaskCreate( film_task_handle, SYS_OS_NAME_FILM_TASK, SYS_OS_SIZE_FILM_TASK, NULL, SYS_OS_PRI_FILM_TASK, NULL ))
        {
            sys_loge(FILM_TAG, "film task create error!");
        }
    }
}

static void film_task_handle(void *pvParameters)
{
    // 等待文件完成加载
    vTaskDelay(2000 / portTICK_PERIOD_MS);

    // 发送初始化消息
    film_msg_t msg;
    msg.ID = MSG_FILM_INIT;
    film_msg_send(&msg, 0);

    /* 注册按键回调（三独立按键，取代原编码器语义）
     *
     * 侧边并排两键专用于画面轮播——这正是「安卓一次下发多张（今日总览 + 早/中/晚详情），
     * 用户按键翻页查看」的交互基础。轮播本身复用既有的 service_film_prev/next
     * （按文件列表 (current±1) % file_count 循环），无需改动 BLE 协议。
     *
     * 右侧单键（KEY_ID_FN）的短按与长按都归低功耗管理器做蓝牙控制（见
     * service_monitor.c：短按开 60s、长按开 180s 广播窗口）。
     *
     * 两处刻意不再绑定：
     *   - 清屏 service_film_clear：误触即整屏变白且无法撤销，风险远大于收益；
     *     需要清屏时通过 BLE 命令执行。
     *   - Wi-Fi 手动同步：由 App 侧 SYNC_NOW(0x44) 或 PREV 长按触发（见 service_monitor.c），
     *     不占用本文件的短按槽位。
     */
    hal_key_register_cb(KEY_ID_PREV, KEY_EVENT_SHORT, service_film_prev);
    hal_key_register_cb(KEY_ID_NEXT, KEY_EVENT_SHORT, service_film_next);

    for(;;)
    {
        film_msg_t msg;
        if(xQueueReceive( m_film_msg_hdl, (void *const)&msg, portMAX_DELAY ) == pdPASS)
        {
            switch(msg.ID)
            {
            case MSG_FILM_DISPLAY:
                film_display_event(msg.file_id);
                break;
            case MSG_FILM_NEXT:
                film_next_event();
                break;
            case MSG_FILM_PREV:
                film_prev_event();
                break;
            case MSG_FILM_INIT:
                film_init_event();
                break;
            case MSG_FILM_CLEAR:
                film_clear_event();
                break;
            default:
                break;
            }
        }
    }
}

static void film_msg_send(void *p_msg, bool in_isr)
{
    if(m_film_msg_hdl != NULL)
    {
        if(in_isr == 0)
        {
            if(xQueueSend(m_film_msg_hdl, p_msg, portMAX_DELAY) != pdPASS)
            {
                sys_loge(FILM_TAG, "film msg send error!");
            }
        }
        else
        {
            BaseType_t xHigherPriorityTaskWoken;
            xHigherPriorityTaskWoken = pdFALSE;
            xQueueSendFromISR( m_film_msg_hdl, p_msg, &xHigherPriorityTaskWoken );
            portYIELD_FROM_ISR( xHigherPriorityTaskWoken );
        }
    }
}

static void film_init_event(void)
{
    sys_logi(FILM_TAG, "Film init event complete %d play mode %d", g_service_param.film.load_complete, g_service_param.film.play_mode);

    /* 自动联网只由 monitor 编排。屏幕在深睡期间保持像素，已有有效画面无需开机重刷。 */
    if(!g_service_param.film.load_complete && service_file_get_count() > 0)
    {
        sys_logi(FILM_TAG, "No applied screen state, displaying the first local film");
        film_display_event(g_service_param.film.current_file_id);
    }
}

/* 独立的「电子纸刷新进行中」标志：与 load_complete（"已加载有效图"语义）解耦。
 * 仅在 display/clear 真正送屏期间为 1，供 service_monitor 休眠互斥判据使用，避免
 * 无图/清屏后 load_complete==0 被误判为"永远在刷新"而永不深睡（review H1）。 */
static volatile uint8_t m_film_refreshing = 0;

uint8_t service_film_is_refreshing(void)
{
    return m_film_refreshing;
}

static void film_clear_event(void)
{
    sys_logi(FILM_TAG, "Film clear event");

    m_film_refreshing = 1;
    esp_err_t err = hal_epd_display_init();
    if(err == ESP_OK) err = hal_epd_display_white();
    esp_err_t shutdown_err = hal_epd_pwroff();
    if(err == ESP_OK) err = shutdown_err;
    m_film_refreshing = 0;

    if(err != ESP_OK)
    {
        sys_loge(FILM_TAG, "Clear failed at EPD stage: %s", esp_err_to_name(err));
        return;
    }

    g_service_param.film.load_complete = 0;
    g_service_param.film.current_file_id = 0;
    g_service_param.film.play_mode = 0;
    service_param_save();
}

static void film_display_event(uint32_t file_id)
{
    char display_name[256] = {0};
    sys_logi(FILM_TAG, "Displaying file: %d", file_id);

    // 检查文件ID是否有效
    uint32_t file_count = service_file_get_count();
    sys_logi(FILM_TAG, "Total count: %d", file_count);
    if(file_count == 0)
    {
        sys_logw(FILM_TAG, "No files available");
        return;
    }

    if(file_id >= file_count)
    {
        sys_logw(FILM_TAG, "Invalid file ID: %d, using 0", file_id);
        file_id = 0;
    }

    const char *selected_name = service_file_get_name(file_id);
    if(selected_name != NULL)
    {
        (void)snprintf(display_name, sizeof(display_name), "%s", selected_name);
    }

    // 加载文件
    if(service_file_load(file_id) != 0)
    {
        sys_loge(FILM_TAG, "Failed to load file: %d", file_id);
        service_wifi_display_failed(display_name[0] ? display_name : NULL);
        return;
    }
    else
    {
        sys_logi(FILM_TAG, "File name: %s", service_file_get_name(file_id));
    }

    // 等待文件加载完成（最多等待3秒）
    uint32_t wait_count = 0;
    while((service_file_get_load_complete() != FILE_LOAD_STATE_DONE) && wait_count < 300)
    {
        vTaskDelay(10 / portTICK_PERIOD_MS);
        wait_count++;
    }
    
    uint8_t* buffer = NULL;
    if((buffer = service_file_get_buffer()) == NULL)
    {
        // 加载失败：保留当前画面，不清屏（Req 8.3、7.7）
        sys_loge(FILM_TAG, "Failed to get file buffer");
        service_wifi_display_failed(display_name[0] ? display_name : NULL);
        return;
    }

    // 显示前校验当前有效文件的文件头，仅在校验通过后送屏（Req 8.3）。
    // 复用 film_validate（像素长度/宽高/颜色数等头字段校验，避免重复实现校验逻辑）；
    // 缓冲区已完整加载整份 .film（见 service_file 的 file_load_event），故对整份缓冲校验。
    // 校验失败时直接返回，不调用任何 EPD 接口，因而保留当前有效画面、绝不清屏（Req 7.7）。
    film_err_t film_err = film_validate(buffer, service_file_get_buffer_size());
    if(film_err != FILM_OK)
    {
        sys_loge(FILM_TAG, "Film header invalid (%s), keep current screen", film_err_str(film_err));
        service_wifi_display_failed(display_name[0] ? display_name : NULL);
        return;
    }

    // 调用EPD显示接口（仅在文件头校验通过后执行）
    int64_t refresh_started = esp_timer_get_time();

    /* 刷屏前把 load_complete 落盘为 0：刷屏中途掉电后屏上是半张图，重启必须能看出
     * 「屏幕内容未知」并重画。current_file_id 仍留到成功后才提交。 */
    if(g_service_param.film.load_complete)
    {
        g_service_param.film.load_complete = 0;
        service_param_save();
    }

    m_film_refreshing = 1;   /* 刷新进行中：与 load_complete 解耦，供休眠互斥判据（H1） */
    esp_err_t epd_err = hal_epd_display_init();
    if(epd_err == ESP_OK) epd_err = hal_epd_display_film(buffer);
    esp_err_t shutdown_err = hal_epd_pwroff();
    if(epd_err == ESP_OK) epd_err = shutdown_err;
    m_film_refreshing = 0;

    if(epd_err != ESP_OK)
    {
        sys_loge(FILM_TAG, "EPD refresh failed: %s; screen content unknown",
                 esp_err_to_name(epd_err));
        service_wifi_display_failed(display_name[0] ? display_name : NULL);
        return;
    }

    // 只有初始化、双 pass 和关机都成功后才提交显示状态与 applied ACK。
    g_service_param.film.current_file_id = file_id;
    g_service_param.film.load_complete = 1;
    service_param_save();

    /* Wi-Fi versions advance only after the actual EPD refresh completes. Other
     * display sources are harmless: service_wifi checks the pending filename. */
    service_wifi_display_completed(display_name);

    sys_logi(FILM_TAG, "Refresh event completed, timing epd_total=%lldms",
             (esp_timer_get_time() - refresh_started) / 1000);
}

static void film_next_event(void)
{
    // 获取文件总数
    uint32_t file_count = service_file_get_count();
    if(file_count == 0)
    {
        sys_logw(FILM_TAG, "No files available");
        return;
    }

    // 计算下一张图片ID
    uint32_t current_id = g_service_param.film.current_file_id;
    uint32_t next_id = (current_id + 1) % file_count;

    // 显示下一张图片
    if(g_service_param.film.load_complete)
    {
        // 加载完成，直接显示下一张图片
        film_display_event(next_id);
    }
    else
    {
        // 未加载完成，更新无效
        sys_logi(FILM_TAG, "Load not complete, updating invalid");
    }
}

static void film_prev_event(void)
{
    // 获取文件总数
    uint32_t file_count = service_file_get_count();
    if(file_count == 0)
    {
        sys_logw(FILM_TAG, "No files available");
        return;
    }

    // 计算上一张图片ID
    uint32_t current_id = g_service_param.film.current_file_id;
    uint32_t prev_id = (current_id == 0) ? (file_count - 1) : (current_id - 1);

    // 显示上一张图片
    if(g_service_param.film.load_complete)
    {
        // 加载完成，直接显示上一张图片
        film_display_event(prev_id);
    }
    else
    {
        // 未加载完成，更新无效
        sys_logi(FILM_TAG, "Load not complete, updating invalid");
    }
}



/* [film_meal_slot_now 由本地时间判断当前餐段]
 *
 * 时段划分见本文件顶部的 FILM_*_START/END_MIN 常量（早餐 06:00–09:00、午餐 11:00–13:30、
 * 晚餐 17:00–19:30）。三个窗口之外返回 FILM_MEAL_NONE，显示今日总览。
 *
 * 返回 FILM_MEAL_UNKNOWN 表示【时间不可信】，调用方必须放弃按时段选片、退回原有行为。
 * 注意 NONE 与 UNKNOWN 最终都回落到总览，但成因完全不同，日志须分开报（见
 * film_resolve_display_target）。
 * 判据用与 Wi-Fi 同步同一个纯谓词 wifi_sync_gate_time_synced()：冷启动时系统时间是
 * 1970，若不做这个门控，设备一开机就会被算成「凌晨=晚餐」而显示错误的一屏。
 * 时间只有在配网并 SNTP 成功后才可信（见 service_wifi_sntp_sync）。
 */
/* [film_local_minute_now 取当前当地时间的分钟数，并给出时间是否可信]
 *
 * pin 判定（film_pin_should_show）需要的是「分钟数 + 可信度」这一对原始事实，而不是已经
 * 归约成餐段的结论。单独抽出来是为了让「什么算可信时间」只有一个定义，否则两处各写一份
 * time()/localtime_r() 迟早漂移成一处认 1970 可信、另一处不认。
 *
 * @param out_minutes 输出当地分钟数 0..1439（可为 NULL）
 * @return true 时间可信（SNTP 已成功）；false 时 *out_minutes 未定义
 */
static bool film_local_minute_now(int *out_minutes)
{
    time_t now = 0;
    time(&now);

    if(!wifi_sync_gate_time_synced((int64_t)now))
    {
        return false;
    }

    struct tm tm_local;
    if(localtime_r(&now, &tm_local) == NULL)
    {
        return false;
    }

    if(out_minutes != NULL)
    {
        *out_minutes = tm_local.tm_hour * 60 + tm_local.tm_min;
    }
    return true;
}

/* [film_meal_slot_from_minute 由已知的当地分钟数判断餐段]
 *
 * 与 film_meal_slot_now() 拆开，是为了让 pin 判定能用**同一次**时间读取同时得到
 * 「分钟数」与「餐段」。若各自再读一次 time()，恰好跨分钟时两者会互相矛盾
 * （一个说 10:59、一个说已进午餐窗口），后果是多刷一次屏 —— 不严重，但没必要。
 *
 * 入参必须是可信时间派生的分钟数；「时间不可信」由调用方在此之前处理成
 * FILM_MEAL_UNKNOWN。 */
static film_meal_slot_t film_meal_slot_from_minute(int minutes)
{
    if(minutes >= FILM_BREAKFAST_START_MIN && minutes < FILM_BREAKFAST_END_MIN)
    {
        return FILM_MEAL_BREAKFAST;
    }
    if(minutes >= FILM_LUNCH_START_MIN && minutes < FILM_LUNCH_END_MIN)
    {
        return FILM_MEAL_LUNCH;
    }
    if(minutes >= FILM_DINNER_START_MIN && minutes < FILM_DINNER_END_MIN)
    {
        return FILM_MEAL_DINNER;
    }

    /* 时间有效但不在任何做饭窗口内 -> 该显示今日总览。
     * 与 FILM_MEAL_UNKNOWN（时间不可信）区分开：两者最终都显示总览，但前者是**确定的
     * 业务决定**、可以参与自动切换；后者是「不知道现在几点」，不应触发任何定时切换。 */
    return FILM_MEAL_NONE;
}

static film_meal_slot_t film_meal_slot_now(void)
{
    int minutes = 0;
    if(!film_local_minute_now(&minutes))
    {
        return FILM_MEAL_UNKNOWN;
    }
    return film_meal_slot_from_minute(minutes);
}

static const char* film_meal_keyword(film_meal_slot_t slot)
{
    switch(slot)
    {
    case FILM_MEAL_BREAKFAST: return "breakfast";
    case FILM_MEAL_LUNCH:     return "lunch";
    case FILM_MEAL_DINNER:    return "dinner";
    default:                  return NULL;   /* NONE / UNKNOWN 都没有专属餐段文件 */
    }
}

const char* service_film_meal_slot_str(film_meal_slot_t slot)
{
    switch(slot)
    {
    case FILM_MEAL_BREAKFAST: return "breakfast";
    case FILM_MEAL_LUNCH:     return "lunch";
    case FILM_MEAL_DINNER:    return "dinner";
    case FILM_MEAL_NONE:      return "overview";
    default:                  return "unknown";
    }
}

film_meal_slot_t service_film_get_meal_slot_now(void)
{
    return film_meal_slot_now();
}

/* 在文件列表里找**文件名包含 keyword** 的第一项。
 * 用「包含」而不是「相等」：发送方会加序号前缀（2_breakfast.film）以固定轮播次序，
 * 前缀不应参与匹配。 */
static int film_find_file_by_keyword(const char *keyword)
{
    if(keyword == NULL)
    {
        return -1;
    }
    uint32_t count = service_file_get_count();
    for(uint32_t i = 0; i < count; i++)
    {
        const char *name = service_file_get_filename(i);
        if(name != NULL && strstr(name, keyword) != NULL)
        {
            return (int)i;
        }
    }
    return -1;
}

/* 在文件列表里找**文件名完全相等**的那一项（用于 pinned 图）。
 *
 * 与 film_find_file_by_keyword() 的「包含」匹配不同，这里要求全等：pinned 名字是
 * App 通过 0x4C 明确指定的完整基名，与经 FILE_NAME 传上来的名字逐字节相同。
 * 用包含匹配会让 `photo_1.film` 意外命中 `photo_12.film`，而用户根本不知道自己 pin
 * 的是哪一张。 */
static int film_find_file_by_name(const char *name)
{
    if(name == NULL || name[0] == '\0')
    {
        return -1;
    }
    uint32_t count = service_file_get_count();
    for(uint32_t i = 0; i < count; i++)
    {
        const char *cur = service_file_get_filename(i);
        if(cur != NULL && strcmp(cur, name) == 0)
        {
            return (int)i;
        }
    }
    return -1;
}

/* [film_pin_cfg 取 pin 配置的一份**拷贝**（首次调用时从独立 NVS key 载入）]
 *
 * 恒填出确定值：载入失败/未设置时是一份 mode==OFF 的全零配置，
 * film_pin_should_show() 对它恒返回 false，选图行为与没有本功能时完全一致。
 *
 * **返回拷贝而不是指针**：调用方拿到指针后往往要多次解引用（先判 mode 再取 filename），
 * 而这两次之间缓存可能已被另一个任务换掉 —— 那就会出现「按新配置判定为生效、却去找旧
 * 文件名」这种两份配置混用的结果。拷贝一份 40 字节的结构体在这条路径上不值得省。 */
static void film_pin_cfg(film_pin_cfg_t *out)
{
    if(out == NULL)
    {
        return;
    }
    if(m_pin_cfg_mux == NULL)
    {
        /* 互斥量在 service_film_init() 里创建。真走到这里说明有调用方早于 init ——
         * 那时不可能有并发，直接按未设置返回，不去在这里补建（补建本身也需要互斥）。 */
        memset(out, 0, sizeof(*out));
        return;
    }

    xSemaphoreTake(m_pin_cfg_mux, portMAX_DELAY);
    if(!m_pin_cfg_loaded)
    {
        /* 失败时 load 也会把 out 填成确定的 OFF 配置，故不必判返回值。 */
        (void)service_param_pinned_load(&m_pin_cfg);
        m_pin_cfg_loaded = true;
        sys_logi(FILM_TAG, "pin cfg loaded: mode=%u window=[%u,%u) name=%s",
                 (unsigned)m_pin_cfg.mode,
                 (unsigned)m_pin_cfg.window_start_min,
                 (unsigned)m_pin_cfg.window_end_min,
                 m_pin_cfg.filename);
    }
    *out = m_pin_cfg;
    xSemaphoreGive(m_pin_cfg_mux);
}

void service_film_pin_invalidate(void)
{
    if(m_pin_cfg_mux != NULL)
    {
        /* 在锁内置脏：与「载入并置 loaded=true」互斥，杜绝上面注释里那个丢更新窗口。 */
        xSemaphoreTake(m_pin_cfg_mux, portMAX_DELAY);
        m_pin_cfg_loaded = false;
        xSemaphoreGive(m_pin_cfg_mux);
    }
    else
    {
        m_pin_cfg_loaded = false;
    }
    /* 同时清掉自动切换的「上次目标」记忆：pin 配置刚变，上一次的目标已无参考价值，
     * 让下一次 tick 必然重新对齐一次（否则改完 pin 可能要等到下次跨时段才生效）。 */
    m_auto_slot = FILM_MEAL_UNKNOWN;
    m_auto_pinned = false;
}

bool service_film_pin_get(film_pin_cfg_t *out)
{
    if(out == NULL)
    {
        return false;
    }
    film_pin_cfg(out);
    return (out->mode != (uint8_t)FILM_PIN_OFF);
}

/* [film_pin_active_now 此刻是否该显示 pinned 图]
 *
 * 把「当前时间事实」翻译成 film_pin_should_show() 的三个入参。时间不可信时
 * in_meal_window 只能是 false —— 不知道现在几点，就无法声称自己在某个做饭窗口里。 */
static bool film_pin_active_now(void)
{
    int minute = 0;
    bool time_valid = film_local_minute_now(&minute);

    /* 用同一次时间读取派生餐段，避免跨分钟时「分钟数」与「餐段」互相矛盾。 */
    bool in_meal = false;
    if(time_valid)
    {
        film_meal_slot_t slot = film_meal_slot_from_minute(minute);
        in_meal = (slot == FILM_MEAL_BREAKFAST ||
                   slot == FILM_MEAL_LUNCH ||
                   slot == FILM_MEAL_DINNER);
    }

    film_pin_cfg_t pin;
    film_pin_cfg(&pin);
    return film_pin_should_show(&pin, minute, time_valid, in_meal);
}

/* [film_pin_target_now 自动切换意义上「此刻的目标是否为 pinned」]
 *
 * 比 film_pin_active_now() 多一条：那张图**真的在设备上**。
 *
 * 为什么把文件存在性纳入目标：auto_display_tick() 的核心判定是「目标变了才刷屏」。只看
 * 配置的话，ALWAYS 模式下目标恒为 pinned、永远与上次相同，于是用户删掉那张照片之后
 * 屏幕会停在删除前的画面 —— 回落逻辑只写在 display_for_now() 里，而它仅由
 * 0x47 / FN 短按 / 下载完成触发，定时自检根本进不去。
 *
 * 纳入之后三种模式统一：文件被删 → 目标从 pinned 变回菜单 → 下一次 tick 自然触发一次
 * 回落；文件又被传回来 → 目标再变回 pinned → 自然切回去。不需要为 ALWAYS 写特例。 */
static bool film_pin_target_now(void)
{
    if(!film_pin_active_now())
    {
        return false;
    }
    film_pin_cfg_t pin;
    film_pin_cfg(&pin);
    return (film_find_file_by_name(pin.filename) >= 0);
}

/* [film_resolve_display_target 只回答「现在该显示哪一张」，不刷屏]
 *
 * 从 service_film_display_for_now() 里拆出来的选图链。拆的理由是有两类调用者，
 * 对「同一张要不要重刷」的要求正好相反：
 *
 *   - 整套下载成功后：必须无条件刷。新版本的文件名与索引都没变，变的是字节，按索引
 *     去重会让屏幕停在旧图上。
 *   - 同步失败后的降级：只在该换页时才刷。电子纸刷一次约 20 秒、耗电、且刷新次数有限，
 *     Wi-Fi 断一天就白刷三次同样的内容。
 *
 * @return 目标文件索引；没有任何文件时返回 -2。
 */
static int film_resolve_display_target(void)
{
    if(service_file_get_count() == 0)
    {
        return -2;
    }

    /* 0) pin 闸门。这一处同时覆盖了 BLE 0x47、FN 短按、以及 Wi-Fi 整套下载成功后的
     * 选图 —— 三条路径都汇到本函数，所以闸门只需要加在这里一处。
     *
     * 刻意**不**加在 film_display_event() / service_film_display(file_id)（BLE 0x07
     * 指定显示）与按键翻页上：pin 的语义是「自动选图时优先它」，不是「锁死屏幕」。
     * 在那些路径上加闸门，用户传了新图看不到、也不能临时翻页看菜单。 */
    if(film_pin_active_now())
    {
        film_pin_cfg_t pin;
        film_pin_cfg(&pin);
        int pidx = film_find_file_by_name(pin.filename);
        if(pidx >= 0)
        {
            sys_logi(FILM_TAG, "pin display: mode=%u -> [%d] %s",
                     (unsigned)pin.mode, pidx, pin.filename);
            return pidx;
        }
        /* 文件不在了（用户删了 / 被 0x45 清空过 / 名字改了）。**继续走原有菜单逻辑**，
         * 绝不空屏：一张过期菜单也比一片空白有用，而空屏还会让人以为设备坏了。
         * 这条 warning 是现场唯一能解释「pin 配了却不生效」的线索，故用 warn 级别。 */
        sys_logw(FILM_TAG, "pin display: pinned file '%s' not found, fall back to menu",
                 pin.filename);
    }

    film_meal_slot_t slot = film_meal_slot_now();
    const char *kw = film_meal_keyword(slot);

    /* 1) 时间可信且能找到对应餐段的图 -> 显示它（中午就显示午餐详情）。 */
    if(kw != NULL)
    {
        int idx = film_find_file_by_keyword(kw);
        if(idx >= 0)
        {
            sys_logi(FILM_TAG, "meal display: slot=%s -> [%d] %s",
                     kw, idx, service_file_get_filename((uint32_t)idx));
            return idx;
        }
        sys_logi(FILM_TAG, "meal display: no file matches slot=%s, fall back", kw);
    }
    else if(slot == FILM_MEAL_NONE)
    {
        /* 时间可信，只是当前不在任何做饭窗口内 —— 这是确定的业务决定，显示今日总览即可。
         * 必须与下面的 UNKNOWN 分开报：film_meal_keyword() 对两者都返回 NULL，早先这里
         * 合并成一条 "time not synced"，于是任何非饭点时刻都被误报成时钟没同步，
         * 排查「设备为什么不显示当餐」时会把人引向 SNTP 而不是时段配置。 */
        sys_logi(FILM_TAG, "meal display: outside meal windows, show overview");
    }
    else
    {
        /* 时间不可信（未配网 / 未 SNTP / 冷启动仍是 1970）：绝不猜时段，走回落。 */
        sys_logi(FILM_TAG, "meal display: time not synced, fall back to overview");
    }

    /* 2) 回落到今日总览。这样即使设备没配网（无时间源），整套下发后也有确定的默认画面。 */
    int idx = film_find_file_by_keyword(FILM_OVERVIEW_KEYWORD);
    if(idx >= 0)
    {
        sys_logi(FILM_TAG, "meal display: overview -> [%d] %s",
                 idx, service_file_get_filename((uint32_t)idx));
        return idx;
    }

    /* 3) 连总览也没有（文件名未按约定命名）：显示列表第一张，保证有画面而不是空等。 */
    sys_logi(FILM_TAG, "meal display: no overview either, show first file");
    return 0;
}

int service_film_display_for_now(void)
{
    int idx = film_resolve_display_target();
    if(idx < 0)
    {
        sys_logw(FILM_TAG, "meal display: no files");
        return idx;
    }
    service_film_display((uint32_t)idx);
    return idx;
}

int service_film_display_for_now_if_changed(void)
{
    int idx = film_resolve_display_target();
    if(idx < 0)
    {
        sys_logw(FILM_TAG, "meal display: no files");
        return idx;
    }

    if((uint32_t)idx == g_service_param.film.current_file_id)
    {
        sys_logi(FILM_TAG, "meal display: [%d] already on screen, skip refresh", idx);
        return -1;
    }

    service_film_display((uint32_t)idx);
    return idx;
}

void service_film_note_manual_display(void)
{
    time_t now = 0;
    time(&now);
    m_manual_display_ts = (uint32_t)now;
    /* 手动切换后把自动目标同步为「当前时段」，这样保护期结束时若时段没变就不会
     * 突然把用户翻的那张顶掉；只有真的跨了时段才切换。
     * pinned 维度一并同步，否则「手动翻页 -> 保护期结束 -> pinned 维度看起来变了」
     * 会白刷一次屏。 */
    m_auto_slot = film_meal_slot_now();
    m_auto_pinned = film_pin_target_now();
}

void service_film_auto_display_tick(void)
{
    /* ALWAYS 模式：既然自定义图要一直显示，就不该有任何按时间的自动切换 ——
     * 一天连一次刷屏都不该有（电子纸刷一次约 20 秒且耗电）。
     * 判定放在时间判定之前，因为 ALWAYS 本就不依赖时间。
     *
     * **但只在那张图还在的时候早退。** 早退如果无条件，就把「pinned 文件消失后回落菜单」
     * 这条路一起关掉了 —— 那个回落只写在 service_film_display_for_now() 里，而它仅由
     * 0x47 / FN 短按 / 下载完成触发，定时自检根本进不去。于是用户删掉那张照片之后，
     * 屏幕会停在删除前的画面直到下一次有人按键，看起来像设备卡死。 */
    if(film_pin_target_now())
    {
        film_pin_cfg_t pin;
        film_pin_cfg(&pin);
        if(pin.mode == (uint8_t)FILM_PIN_ALWAYS)
        {
            return;
        }
    }

    film_meal_slot_t slot = film_meal_slot_now();

    /* 时间不可信：不做任何自动切换（否则冷启动会被当成某个时段而乱刷屏）。
     *
     * 注意这也意味着 OUTSIDE_MEAL 在时间不可信时【不会主动切到 pinned】——
     * 但一旦有任何一条选图路径被触发（0x47、FN 短按、下载完成），
     * service_film_display_for_now() 里的闸门就会显示 pinned。这个不对称是有意的：
     * 「不主动刷屏」比「按不可信的时间刷屏」更重要。 */
    if(slot == FILM_MEAL_UNKNOWN)
    {
        return;
    }

    /* 没有文件、或正在刷屏时不触发，避免叠加刷新。 */
    if(service_file_get_count() == 0 || m_film_refreshing)
    {
        return;
    }

    /* WINDOW / OUTSIDE_MEAL 需要**双向切换**（进窗口切到 pinned、出窗口切回菜单），
     * 所以「目标没变就什么都不做」的比较必须把 pinned 也表达进去：目标 = (时段, 是否 pinned)。
     * 只比时段的话，pinned 窗口在同一餐段中途开始/结束时目标看起来没变，切换不会发生。 */
    bool want_pinned = film_pin_target_now();

    /* 目标没变就什么都不做。电子纸刷一次约 20 秒且耗电，只在目标真的变化时切换。 */
    if(slot == m_auto_slot && want_pinned == m_auto_pinned)
    {
        return;
    }

    /* 用户刚手动翻过页：保护期内不打断他。保护期结束后若时段仍与记录不同才会切换。 */
    time_t now = 0;
    time(&now);
    if(m_manual_display_ts > 0 &&
       ((int64_t)now - (int64_t)m_manual_display_ts) < (int64_t)FILM_MANUAL_HOLD_MIN * 60)
    {
        return;
    }

    sys_logi(FILM_TAG, "auto display: %s%s -> %s%s",
             service_film_meal_slot_str(m_auto_slot), m_auto_pinned ? "+pinned" : "",
             service_film_meal_slot_str(slot), want_pinned ? "+pinned" : "");

    m_auto_slot = slot;
    m_auto_pinned = want_pinned;
    service_film_display_for_now();
}

void service_film_display(uint32_t file_id)
{
    film_msg_t msg;
    msg.ID = MSG_FILM_DISPLAY;
    msg.file_id = file_id;
    film_msg_send(&msg, 0);
}

void service_film_next(void)
{
    if(g_service_param.film.load_complete)
    {
        /* 按键手动翻页：记录一次手动操作，抑制随后的按时间自动切换，
         * 免得用户刚翻到某张就被定时检查顶回去（还白刷一次 20 秒的屏）。 */
        service_film_note_manual_display();

        // 加载完成，显示下一张图片
        film_msg_t msg;
        msg.ID = MSG_FILM_NEXT;
        film_msg_send(&msg, 0);
    }
    else
    {
        sys_logi(FILM_TAG, "Load not complete, next event invalid");
    }
}

void service_film_prev(void)
{
    if(g_service_param.film.load_complete)
    {
        /* 按键手动翻页：记录一次手动操作，抑制随后的按时间自动切换，
         * 免得用户刚翻到某张就被定时检查顶回去（还白刷一次 20 秒的屏）。 */
        service_film_note_manual_display();

        // 加载完成，显示上一张图片
        film_msg_t msg;
        msg.ID = MSG_FILM_PREV;
        film_msg_send(&msg, 0);
    }
    else
    {
        sys_logi(FILM_TAG, "Load not complete, prev event invalid");
    }
}

void service_film_clear(void)
{
    if(g_service_param.film.load_complete)
    {
        // 加载完成，清除显示
        film_msg_t msg;
        msg.ID = MSG_FILM_CLEAR;
        film_msg_send(&msg, 0);
    }
    else
    {
        sys_logi(FILM_TAG, "Load not complete, clear event invalid");
    }
}

void service_film_set_play_mode(uint8_t mode)
{
    g_service_param.film.play_mode = mode;
    service_param_save();
    
    sys_logi(FILM_TAG, "Set play mode: %d", mode);
}

uint32_t service_film_get_current_id(void)
{
    return g_service_param.film.current_file_id;
}

uint8_t service_film_get_load_complete(void)
{
    return g_service_param.film.load_complete;
}
