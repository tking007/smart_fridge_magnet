/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_hal/src/hal_led.c
 * Author: Kiritro  Version: v0.1  Date: 2026/3/31
 * Description: WS2812 状态灯驱动（GPIO17 上一条 2 颗灯珠，RMT 后端）。
 *   亮度语义为 0-100 百分比，与颜色相乘后整串刷新。接口说明见 hal_led.h。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "led_strip.h"
#include "driver/gpio.h"

#include "hal_led.h"
#include "sys_log.h"


/*********************************************************************
 * MACROS
 */
#define LED_TAG                        "HAL_LED"

#define RGB_LED_WS2812_PIN             (GPIO_NUM_17)
#define RGB_LED_NUMBERS                (2)
#define RGB_LED_RMT_RES_HZ             (10 * 1000 * 1000)

#define LED_STRIP_USE_DMA              (0)
#if LED_STRIP_USE_DMA
#define LED_STRIP_MEMORY_BLOCK_WORDS   (1024)
#else
#define LED_STRIP_MEMORY_BLOCK_WORDS   (0)
#endif

/*********************************************************************
* TYPEDEFS
*/
typedef struct
{
    uint32_t brightness;  // 亮度百分比 0-100
    uint32_t color;       // 0xRRGGBB
} led_t;

/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */
static led_strip_handle_t m_rgb;
static led_t m_led =
{
    .brightness = 10,
    .color = LED_COLOR_WHITE
};

/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */
static led_strip_handle_t configure_led(void);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */



void hal_led_init(void)
{
    m_rgb = configure_led();

    hal_led_set_color(m_led.color);
    hal_led_set_brightness(m_led.brightness);
}

uint32_t hal_led_get_brightness(void)
{
    return m_led.brightness;
}

void hal_led_set_brightness(uint32_t brightness)
{
    m_led.brightness = brightness;
    hal_led_set_color(m_led.color);
}

void hal_led_set_color(uint32_t color)
{
    uint32_t r = 0, g = 0, b = 0;

    m_led.color = color;
    if (m_led.brightness > 0 && m_led.brightness <= 100)
    {
        r = (((color >> 16) & 0xff) * m_led.brightness / 100);
        g = (((color >> 8) & 0xff) * m_led.brightness / 100);
        b = ((color & 0xff) * m_led.brightness / 100);
    }

    for(int i = 0; i < RGB_LED_NUMBERS; i++)
    {
        led_strip_set_pixel(m_rgb, i, r, g, b);
    }
    led_strip_refresh(m_rgb);
}

uint32_t hal_led_get_color(void)
{
    return m_led.color;
}

/**
 * @brief 一次性设置两颗灯珠各自的颜色与亮度（接口说明见 hal_led.h）
 *
 * 两个像素在同一次调用里写完，只 led_strip_refresh() 一次：WS2812 整串刷新，
 * 分两次刷会多一次串刷新，且中间那一帧是「一颗新一颗旧」的混合态。
 *
 * 亮度按 0-100 百分比缩放，>100 钳到 100（而非 hal_led_set_color() 那样输出全黑）。
 *
 * 不更新 m_led.color/brightness：那两个字段属于「整串同色」那组接口的状态，
 * getter 也读它们；本接口两颗灯各有独立状态，写进去会让 getter 返回无意义的值。
 * 调用方（service_monitor）自己持有两路状态。
 */
void hal_led_set_pixels(uint32_t color0, uint32_t brightness0,
                        uint32_t color1, uint32_t brightness1)
{
    /* 签名把「两颗」写死在参数里，下面的初值列表也按 2 个元素写。
     * 灯珠数量变化时必须同步改签名，而不是让初值列表静默截断/补零。 */
    _Static_assert(RGB_LED_NUMBERS == 2, "hal_led_set_pixels assumes exactly 2 LEDs");

    uint32_t colors[RGB_LED_NUMBERS]     = { color0, color1 };
    uint32_t brights[RGB_LED_NUMBERS]    = { brightness0, brightness1 };

    if (m_rgb == NULL)
    {
        return;
    }

    for (int i = 0; i < RGB_LED_NUMBERS; i++)
    {
        uint32_t r = 0, g = 0, b = 0;
        uint32_t brightness = brights[i];

        if (brightness > 100)
        {
            brightness = 100;
        }

        if (brightness > 0)
        {
            r = (((colors[i] >> 16) & 0xff) * brightness / 100);
            g = (((colors[i] >> 8) & 0xff) * brightness / 100);
            b = ((colors[i] & 0xff) * brightness / 100);
        }

        led_strip_set_pixel(m_rgb, i, r, g, b);
    }

    led_strip_refresh(m_rgb);
}

void hal_led_deinit(void)
{
    hal_led_set_color(LED_COLOR_BLACK);

    if (m_rgb != NULL)
    {
        led_strip_del(m_rgb);
        m_rgb = NULL;
    }

    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << RGB_LED_WS2812_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf);

    sys_logi(LED_TAG, "LED deinitialized");
}

led_strip_handle_t configure_led(void)
{
    // LED strip general initialization, according to your led board design
    led_strip_config_t strip_config =
    {
        .strip_gpio_num = RGB_LED_WS2812_PIN, // The GPIO that connected to the LED strip's data line
        .max_leds = RGB_LED_NUMBERS,      // The number of LEDs in the strip,
        .led_model = LED_MODEL_WS2812,        // LED strip model
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB, // The color order of the strip: GRB
        .flags = {
            .invert_out = false, // don't invert the output signal
        }
    };

    // LED strip backend configuration: RMT
    led_strip_rmt_config_t rmt_config =
    {
        .clk_src = RMT_CLK_SRC_DEFAULT,        // different clock source can lead to different power consumption
        .resolution_hz = RGB_LED_RMT_RES_HZ, // RMT counter clock frequency
        .mem_block_symbols = LED_STRIP_MEMORY_BLOCK_WORDS, // the memory block size used by the RMT channel
        .flags = {
            .with_dma = LED_STRIP_USE_DMA,     // Using DMA can improve performance when driving more LEDs
        }
    };

    // LED Strip object handle
    led_strip_handle_t led_strip;
    SYS_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &led_strip));
    sys_logi(LED_TAG, "Created LED strip object with RMT backend");
    return led_strip;
}

