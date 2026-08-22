/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_hal/src/hal_pwr.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/4
 * Description: 电源管理 HAL：外设供电开关、深睡唤醒源装载与唤醒原因判定。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <inttypes.h>

#include "esp_sleep.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "esp_pm.h"
#include "esp_system.h"  /* esp_restart()：唤醒源全部注册失败时的兜底 */

#include "sys_log.h"
#include "hal_pwr.h"

/*********************************************************************
 * MACROS
 */
#define PWR_TAG                 "HAL_PWR"

#define PERI_PWR_PIN            GPIO_NUM_21     //外设供电管脚

/* 外设供电脚必须由 RTC 域控制并在深睡期间 hold。
 *
 * 深睡时数字 IO 域掉电，gpio_config() 设的方向与内部上下拉、gpio_set_level() 写的电平
 * 全部不保留，GPIO21 会浮空、外设是否断电不可控。而挂在这条电源上的 WS2812（写黑仍有
 * 静态电流）与 SDnand 只能靠断电省流，电子纸也要靠断电兜住未进 DSLP 的情况。
 * GPIO21 是 ESP32-S3 的 RTC pad（RTC GPIO 覆盖 GPIO0~21），故改用 rtc_gpio_* 接管。 */
#define PERI_PWR_ON             rtc_gpio_set_level(PERI_PWR_PIN, 1)
#define PERI_PWR_OFF            rtc_gpio_set_level(PERI_PWR_PIN, 0)

/* 上面整套 rtc_gpio_* 只对 RTC pad 成立。ESP32-S3 的 RTC GPIO 是 GPIO0~21，把这个前提
 * 固化到编译期：换引脚时若选了非 RTC pad，此处直接编译失败，而不是等到真机上表现为
 * 「深睡外设不断电、待机一天耗尽电池」这种要靠电流表才能发现的形态。 */
_Static_assert(PERI_PWR_PIN >= 0 && PERI_PWR_PIN <= 21,
               "PERI_PWR_PIN must be an ESP32-S3 RTC-capable pad (GPIO0..21) "
               "for rtc_gpio_hold_en() to keep peripherals powered off in deep sleep");


/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */
static uint64_t m_timer_wakeup_us = 0;
/* RTC 域是否成功接管外设供电脚。false 表示已降级到数字域：设备仍可用，但深睡期间
 * 供电脚会浮空、外设不保证断电（即本次修复要解决的那个功耗问题）。 */
static bool m_rtc_pwr_ready = false;


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */

/* [pwr_fallback_digital RTC 域接管失败时的降级：用数字域把外设供电打开]
 *
 * 取舍很明确：宁可「设备可用但深睡不省电」，也不要「省电逻辑完好但设备是块砖」。
 * RTC 接管失败时若直接返回，外设永远不上电 —— 屏黑、SD 读不到、什么都做不了。
 * 数字域至少能把电供上，代价是深睡期间该脚浮空、外设不保证断电（回到修复前的功耗形态），
 * 而那条 error 日志与 m_rtc_pwr_ready=false 是现场唯一的线索。
 *
 * 实际上不该发生：PERI_PWR_PIN 是编译期常量且有 _Static_assert 锁住 RTC pad 范围。 */
static void pwr_fallback_digital(void)
{
    sys_loge(PWR_TAG, "falling back to digital GPIO: device stays usable, "
                      "but peripherals are NOT guaranteed off in deep sleep");

    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << PERI_PWR_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    (void)gpio_config(&io_conf);
    (void)gpio_set_level(PERI_PWR_PIN, 1);
}


/*********************************************************************
 * GLOBAL FUNCTIONS
 */

void hal_pwr_init(void)
{
    sys_logi(PWR_TAG, "pwr init");

    /* hold_dis 必须在最前且无条件执行：上一次深睡 hold 住的低电平在唤醒后依然生效，
     * 不先解除的话后面的置高被锁死，外设永远不上电（屏黑、SD 读不到），而且这个状态
     * 无法自恢复——只能拔电池。深睡唤醒等价于重启，本函数是每次唤醒的必经路径。 */
    esp_err_t err = rtc_gpio_hold_dis(PERI_PWR_PIN);
    if(err != ESP_OK && err != ESP_ERR_INVALID_STATE)
    {
        sys_logw(PWR_TAG, "release peri power hold failed: %s", esp_err_to_name(err));
    }

    /* 不用 ESP_ERROR_CHECK：这里 abort 会变成上电即 boot loop，比「外设供电异常但
     * 系统还能跑起来打日志」更难排查。失败则降级到数字域控制（见 pwr_fallback_digital）。 */
    err = rtc_gpio_init(PERI_PWR_PIN);
    if(err != ESP_OK)
    {
        sys_loge(PWR_TAG, "rtc_gpio_init(peri power) failed: %s", esp_err_to_name(err));
        pwr_fallback_digital();
        return;
    }
    err = rtc_gpio_set_direction(PERI_PWR_PIN, RTC_GPIO_MODE_OUTPUT_ONLY);
    if(err != ESP_OK)
    {
        sys_loge(PWR_TAG, "rtc_gpio_set_direction(peri power) failed: %s",
                 esp_err_to_name(err));
        pwr_fallback_digital();
        return;
    }
    rtc_gpio_pullup_dis(PERI_PWR_PIN);
    rtc_gpio_pulldown_dis(PERI_PWR_PIN);

    m_rtc_pwr_ready = true;
    PERI_PWR_ON;
}

void hal_pwr_enter_sleep(void)
{
    sys_logi(PWR_TAG, "Entering deep sleep");

    if (m_rtc_pwr_ready)
    {
        /* 断外设供电并把低电平 hold 住。hold 之后该 pad 在深睡（数字域掉电）期间仍保持
         * 输出低，外设才真正断电；只置低不 hold 等于让 pad 在深睡时浮空。
         * 唤醒后由 hal_pwr_init() 的 rtc_gpio_hold_dis() 解除。 */
        PERI_PWR_OFF;
        esp_err_t hold_ret = rtc_gpio_hold_en(PERI_PWR_PIN);
        if (hold_ret != ESP_OK)
        {
            /* hold 不成功时外设可能整个深睡期间带电（实测表现为待机一天耗尽电池），
             * 这条日志是排查功耗时的第一现场，不能静默。 */
            sys_loge(PWR_TAG, "Failed to hold peripheral power off: %s",
                     esp_err_to_name(hold_ret));
        }
    }
    else
    {
        /* 降级模式（RTC 接管失败）：只能用数字域拉低，而深睡时这份配置不保留、脚会浮空，
         * 外设是否断电不可控 —— 正是本次修复要消除的那个形态。用 error 级别记录，
         * 不让功耗退化静默发生。 */
        (void)gpio_set_level(PERI_PWR_PIN, 0);
        sys_loge(PWR_TAG, "peri power in digital fallback: "
                          "deep-sleep power-off NOT guaranteed");
    }

    /* 注册唤醒源。任何一个失败都不能提前返回：走到这里外设已经断电、屏幕与网络也已
     * 反初始化，返回等于让设备停在「醒着但什么都不工作、且没有唤醒源」的状态，
     * 表现为电池被慢慢耗光而串口上只有一行看不见的错误。 */
    uint32_t wake_sources = 0;

    esp_err_t ret = esp_sleep_enable_ext1_wakeup(HAL_PWR_WAKE_KEY_MASK,
                                                  ESP_EXT1_WAKEUP_ANY_LOW);
    if (ret != ESP_OK)
    {
        sys_loge(PWR_TAG, "Failed to enable GPIO wakeup: %s", esp_err_to_name(ret));
    }
    else
    {
        wake_sources++;
        sys_logi(PWR_TAG, "Configured EXT1 ANY_LOW key wake mask: 0x%llx",
                 HAL_PWR_WAKE_KEY_MASK);
    }

    if (m_timer_wakeup_us > 0)
    {
        ret = esp_sleep_enable_timer_wakeup(m_timer_wakeup_us);
        if (ret != ESP_OK)
        {
            sys_loge(PWR_TAG, "Failed to enable timer wakeup: %s", esp_err_to_name(ret));
        }
        else
        {
            wake_sources++;
            sys_logi(PWR_TAG, "Configured timer wakeup: %llu us", m_timer_wakeup_us);
        }
    }

    /* 一个唤醒源都没注册上就不能睡：没有唤醒源的深睡要拔电池才能恢复，
     * 而重启是可恢复的、串口上看得见的。宁可多耗一次启动的电。 */
    if (wake_sources == 0)
    {
        sys_loge(PWR_TAG, "No wakeup source armed, restarting instead of sleeping");
        esp_restart();
    }

    esp_deep_sleep_start();
}

void hal_pwr_set_timer_wakeup(uint32_t minutes)
{
    if (minutes > 0)
    {
        m_timer_wakeup_us = (uint64_t)minutes * 60 * 1000000;
        sys_logi(PWR_TAG, "Timer wakeup set: %d min (%llu us)", minutes, m_timer_wakeup_us);
    }
    else
    {
        m_timer_wakeup_us = 0;
        sys_logi(PWR_TAG, "Timer wakeup disabled");
    }
}

/* 秒级定时唤醒：饭点唤醒的周期是「距下一个唤醒点还有多久」，天然不是整分钟的倍数，
 * 分钟版只能向上取整，而餐前有效窗口只有半小时，取整误差足以把设备推到窗口之外。
 * 底层 esp_sleep_enable_timer_wakeup() 本来就收微秒。
 *
 * 与分钟版共用 m_timer_wakeup_us，后调用者生效；调用方每条分支只用其中一个入口。 */
void hal_pwr_set_timer_wakeup_seconds(uint32_t seconds)
{
    if (seconds > 0)
    {
        m_timer_wakeup_us = (uint64_t)seconds * 1000000;
        sys_logi(PWR_TAG, "Timer wakeup set: %u sec (%llu us)",
                 (unsigned)seconds, m_timer_wakeup_us);
    }
    else
    {
        m_timer_wakeup_us = 0;
        sys_logi(PWR_TAG, "Timer wakeup disabled");
    }
}

bool hal_pwr_check_wakeup(void)
{
    /* 用 causes 位图而不是单值版：EXT1 与定时器可能同时触发，单值版会丢掉其中一个。 */
    uint32_t causes = esp_sleep_get_wakeup_causes();

    if (causes & (BIT(ESP_SLEEP_WAKEUP_EXT1) | BIT(ESP_SLEEP_WAKEUP_TIMER)))
    {
        sys_logi(PWR_TAG, "Wakeup from deep sleep by %s",
                 (causes & BIT(ESP_SLEEP_WAKEUP_TIMER)) ? "Timer" : "EXT1 key");
        return true;
    }
    if (causes == 0)
    {
        sys_logi(PWR_TAG, "Normal boot (not from deep sleep)");
        return false;
    }

    sys_logi(PWR_TAG, "Wakeup from other source: 0x%" PRIx32, causes);
    return false;
}

uint64_t hal_pwr_get_wakeup_key_mask(void)
{
    if((esp_sleep_get_wakeup_causes() & BIT(ESP_SLEEP_WAKEUP_EXT1)) == 0)
    {
        return 0;
    }
    return esp_sleep_get_ext1_wakeup_status() & HAL_PWR_WAKE_KEY_MASK;
}
