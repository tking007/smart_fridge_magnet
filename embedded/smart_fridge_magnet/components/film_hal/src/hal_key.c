/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_hal/src/hal_key.c
 * Author: Kiritro  Version: v0.1  Date: 2026/7/25
 * Description: 三独立按键驱动（侧边上/下 + 右侧 FN）。
 *
 * 判定策略（轮询而非中断）：
 *   - 每 HAL_KEY_POLL_INTERVAL_MS 采样一次电平，连续稳定达到消抖时长才认定状态变化；
 *     按键属低频交互，轮询开销可忽略。
 *   - 长按：按住累计达到阈值时立即触发 LONG 一次，并置 long_fired，释放时不再补 SHORT。
 *   - 短按：仅在释放时且未触发过 LONG 的情况下触发，避免一次操作产生两个事件。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"

#include "sys_log.h"
#include "hal_key.h"
#include "hal_pwr.h"

/*********************************************************************
 * MACROS
 */
#define KEY_TAG                          "HAL_KEY"

/* 三个按键引脚，同时由 hal_pwr 配成 EXT1 ANY_LOW 深睡唤醒源。 */
#define HAL_KEY_PIN_PREV                 (6)
#define HAL_KEY_PIN_NEXT                 (4)
#define HAL_KEY_PIN_FN                   (5)

/* 按下电平：外部已有上拉，按下接地 -> 低电平有效。若实测某键极性相反，只需改这里。 */
#define HAL_KEY_ACTIVE_LEVEL             (0)

#define HAL_KEY_POLL_INTERVAL_MS         (10)     /* 轮询周期 */
#define HAL_KEY_DEBOUNCE_MS              (40)     /* 消抖稳定时长 */
#define HAL_KEY_LONG_PRESS_MS            (2000)   /* 长按阈值 2s */

#define HAL_KEY_MAX_CALLBACKS            (4)

#define HAL_KEY_TASK_STACK               (3072)
#define HAL_KEY_TASK_PRIO                (5)

/* deinit 等待轮询任务自行退出的上限 */
#define HAL_KEY_EXIT_TIMEOUT_MS          (200)

/*********************************************************************
* TYPEDEFS
*/
typedef struct
{
    int      pin;
    bool     pressed;          /* 已消抖的稳定状态 */
    bool     raw_last;         /* 上次原始采样（true=按下） */
    uint32_t stable_ms;        /* 当前原始状态已持续时长 */
    uint32_t hold_ms;          /* 已按住时长（用于长按） */
    bool     long_fired;       /* 本次按下已触发过长按 */
    bool     suppress_release; /* EXT1 唤醒动作已由 monitor 排队，首次释放不补发 */
} key_slot_t;

typedef struct
{
    bool            initialized;
    volatile bool   running;
    volatile bool   task_exited;   /* 任务已自行退出（deinit 据此避免二次删除） */
    TaskHandle_t    task;
    key_slot_t      keys[KEY_ID_MAX];
    key_callback_t  cbs[KEY_ID_MAX][KEY_EVENT_MAX][HAL_KEY_MAX_CALLBACKS];
    int             cb_counts[KEY_ID_MAX][KEY_EVENT_MAX];
} hal_key_t;

/*********************************************************************
 * LOCAL VARIABLES
 */
static hal_key_t m_key;

static const int m_key_pins[KEY_ID_MAX] = {
    HAL_KEY_PIN_PREV,
    HAL_KEY_PIN_NEXT,
    HAL_KEY_PIN_FN,
};

static const char *m_key_names[KEY_ID_MAX] = {
    "PREV", "NEXT", "FN",
};

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void hal_key_task(void *arg);
static void hal_key_dispatch(key_id_t id, key_event_t evt);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

void hal_key_init(void)
{
    if (m_key.initialized)
    {
        return;
    }

    memset(&m_key, 0, sizeof(m_key));

    uint64_t mask = 0;
    for (int i = 0; i < KEY_ID_MAX; i++)
    {
        m_key.keys[i].pin = m_key_pins[i];
        mask |= (1ULL << m_key_pins[i]);
    }

    /* 外部已有上拉，不开内部上拉，避免改变电气特性。 */
    gpio_config_t io_conf = {
        .pin_bit_mask = mask,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    esp_err_t ret = gpio_config(&io_conf);
    if (ret != ESP_OK)
    {
        sys_loge(KEY_TAG, "gpio_config failed: %s", esp_err_to_name(ret));
        return;
    }

    uint64_t wake_mask = hal_pwr_get_wakeup_key_mask();
    /* 以当前电平作为初始稳定状态；EXT1 唤醒键由 monitor 执行一次短按动作，这里吞掉
     * 首次释放。侧键持续按住不触发联网命令；FN 持续长按仍可把 BLE 窗口延长到 180 秒。 */
    for (int i = 0; i < KEY_ID_MAX; i++)
    {
        bool raw = (gpio_get_level(m_key.keys[i].pin) == HAL_KEY_ACTIVE_LEVEL);
        m_key.keys[i].pressed  = raw;
        m_key.keys[i].raw_last = raw;
        m_key.keys[i].suppress_release = raw && ((wake_mask & (1ULL << m_key_pins[i])) != 0);
    }

    m_key.running = true;
    BaseType_t ok = xTaskCreate(hal_key_task, "hal_key", HAL_KEY_TASK_STACK,
                                NULL, HAL_KEY_TASK_PRIO, &m_key.task);
    if (ok != pdPASS)
    {
        m_key.running = false;
        sys_loge(KEY_TAG, "create key task failed");
        return;
    }

    m_key.initialized = true;
    sys_logi(KEY_TAG, "key initialized (3 independent keys: PREV=%d NEXT=%d FN=%d)",
             HAL_KEY_PIN_PREV, HAL_KEY_PIN_NEXT, HAL_KEY_PIN_FN);
}

void hal_key_deinit(void)
{
    if (!m_key.initialized)
    {
        return;
    }

    /* 让任务自行退出，再收回引脚，避免任务仍在访问已重配的 GPIO。
     *
     * 这里【绝不能】再调用 vTaskDelete(m_key.task)：任务退出时已执行 vTaskDelete(NULL)
     * 自删，句柄随即失效，对失效句柄再删是未定义行为（实测 Guru Meditation）。
     * 本函数在深睡路径 monitor_enter_low_power() 上必经，故改为等待任务自报退出。 */
    m_key.running = false;

    uint32_t waited_ms = 0;
    while (!m_key.task_exited && waited_ms < HAL_KEY_EXIT_TIMEOUT_MS)
    {
        vTaskDelay(HAL_KEY_POLL_INTERVAL_MS / portTICK_PERIOD_MS);
        waited_ms += HAL_KEY_POLL_INTERVAL_MS;
    }
    if (!m_key.task_exited)
    {
        /* 超时未退出：宁可留着任务也不强删——强删正在跑的任务会泄漏它持有的资源，
         * 而这个任务只做 GPIO 轮询，留存的代价远小于崩溃。 */
        sys_logw(KEY_TAG, "key task did not exit in %ums, leave it running", waited_ms);
    }
    m_key.task = NULL;

    /* 置高阻输入降低漏电；深睡唤醒由 hal_pwr_enter_sleep() 单独配置 EXT1。 */
    uint64_t mask = 0;
    for (int i = 0; i < KEY_ID_MAX; i++)
    {
        mask |= (1ULL << m_key_pins[i]);
    }
    gpio_config_t io_conf = {
        .pin_bit_mask = mask,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf);

    m_key.initialized = false;
    sys_logi(KEY_TAG, "key deinitialized");
}

int hal_key_register_cb(key_id_t id, key_event_t evt, key_callback_t cb)
{
    if (cb == NULL) return -1;
    if (id < 0 || id >= KEY_ID_MAX) return -1;
    if (evt < 0 || evt >= KEY_EVENT_MAX) return -1;
    if (m_key.cb_counts[id][evt] >= HAL_KEY_MAX_CALLBACKS) return -2;

    m_key.cbs[id][evt][m_key.cb_counts[id][evt]++] = cb;
    return 0;
}

bool hal_key_is_pressed(key_id_t id)
{
    if (id < 0 || id >= KEY_ID_MAX) return false;
    return m_key.keys[id].pressed;
}

/*********************************************************************
 * LOCAL FUNCTIONS
 */

static void hal_key_dispatch(key_id_t id, key_event_t evt)
{
    sys_logi(KEY_TAG, "key %s %s", m_key_names[id],
             (evt == KEY_EVENT_LONG) ? "LONG" : "SHORT");

    for (int i = 0; i < m_key.cb_counts[id][evt]; i++)
    {
        if (m_key.cbs[id][evt][i])
        {
            m_key.cbs[id][evt][i]();
        }
    }
}

static void hal_key_task(void *arg)
{
    (void)arg;

    while (m_key.running)
    {
        for (int i = 0; i < KEY_ID_MAX; i++)
        {
            key_slot_t *k = &m_key.keys[i];
            bool raw = (gpio_get_level(k->pin) == HAL_KEY_ACTIVE_LEVEL);

            if (raw != k->raw_last)
            {
                k->raw_last  = raw;
                k->stable_ms = 0;
            }
            else
            {
                k->stable_ms += HAL_KEY_POLL_INTERVAL_MS;
            }

            /* 消抖通过后才更新稳定状态 */
            if (k->stable_ms >= HAL_KEY_DEBOUNCE_MS && raw != k->pressed)
            {
                k->pressed = raw;

                if (raw)
                {
                    k->hold_ms    = 0;
                    k->long_fired = false;
                }
                else
                {
                    /* 释放：未触发过长按才算短按，避免一次操作产生两个事件 */
                    if(k->suppress_release)
                    {
                        k->suppress_release = false;
                    }
                    else if (!k->long_fired)
                    {
                        hal_key_dispatch((key_id_t)i, KEY_EVENT_SHORT);
                    }
                    k->hold_ms    = 0;
                    k->long_fired = false;
                }
            }

            /* 保持按下：越过阈值触发一次长按。FN 即便是唤醒键也参与长按计时，
             * 以便唤醒后继续按住把 BLE 窗口延长。 */
            if (k->pressed && (!k->suppress_release || i == KEY_ID_FN))
            {
                k->hold_ms += HAL_KEY_POLL_INTERVAL_MS;
                if (!k->long_fired && k->hold_ms >= HAL_KEY_LONG_PRESS_MS)
                {
                    k->long_fired = true;
                    hal_key_dispatch((key_id_t)i, KEY_EVENT_LONG);
                }
            }
        }

        vTaskDelay(HAL_KEY_POLL_INTERVAL_MS / portTICK_PERIOD_MS);
    }

    /* 自删前先置退出标志，hal_key_deinit() 靠它确认任务已结束、从而不做二次删除。 */
    m_key.task_exited = true;
    vTaskDelete(NULL);
}
