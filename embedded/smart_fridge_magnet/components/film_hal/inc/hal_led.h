#ifndef __HAL_LED_H__
#define __HAL_LED_H__


/*********************************************************************
 * INCLUDES
 */
#include "led_strip.h"


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
// 定义常用颜色
#define LED_COLOR_RED           (0xFF0000)    // 红色
#define LED_COLOR_GREEN         (0x00FF00)    // 绿色
#define LED_COLOR_BLUE          (0x0000FF)    // 蓝色
#define LED_COLOR_WHITE         (0xFFFFFF)    // 白色
#define LED_COLOR_BLACK         (0x000000)    // 黑色
#define LED_COLOR_LIGHT_BLUE    (0x87CEEB)    // 浅蓝色
#define LED_COLOR_LIGHT_RED     (0xFFB6C1)    // 浅红色
#define LED_COLOR_EMERALD       (0x00FF7F)    // 翠绿色
#define LED_COLOR_YELLOW        (0xFFFF00)    // 黄色
#define LED_COLOR_PURPLE        (0x800080)    // 紫色
#define LED_COLOR_ORANGE        (0xFFA500)    // 橙色


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
/**
 * @brief 初始化 LED（创建 RMT 通道并点亮为默认色）。使用其它 LED 接口前必须先调用。
 */
extern void hal_led_init(void);

/**
 * @brief 获取当前亮度（0-100 百分比）。
 */
extern uint32_t hal_led_get_brightness(void);

/**
 * @brief 设置亮度并按当前颜色刷新整串。
 *
 * @param brightness 亮度百分比（0-100；>100 会导致输出全黑）
 */
extern void hal_led_set_brightness(uint32_t brightness);

/**
 * @brief 设置整串颜色（两颗灯同色）。
 *
 * @param color 0xRRGGBB
 */
extern void hal_led_set_color(uint32_t color);

/**
 * @brief 获取当前颜色（0xRRGGBB）。
 */
extern uint32_t hal_led_get_color(void);

/**
 * @brief 一次性设置两颗灯珠各自的颜色与亮度
 *
 * 板上是一条 WS2812 上的 2 颗灯珠，共用一根数据线；set_color()/set_brightness()
 * 对所有像素写同一个 r/g/b，两颗灯只能同色。
 *
 * 刻意不提供「只设一颗」的接口：WS2812 是整串刷新，拆成两次调用要么多刷一次串，
 * 要么在两次调用之间留下「只改了一半」的中间态（肉眼可见的闪烁）。本接口强制调用方
 * 每次都同时给出两颗的目标状态，一次调用内写两个像素、只 refresh 一次。
 *
 * 亮度沿用本模块的 0-100 百分比语义，>100 钳到 100（而非 set_color() 那样输出全黑）。
 *
 * @param color0      灯珠 0 的颜色（0xRRGGBB）
 * @param brightness0 灯珠 0 的亮度（0-100，0 为灭）
 * @param color1      灯珠 1 的颜色（0xRRGGBB）
 * @param brightness1 灯珠 1 的亮度（0-100，0 为灭）
 *
 * @note index 0 与 1 分别对应物理上的哪一颗（左/右）未经真机确认。若装反，
 *       只需把 service_monitor.c 里那唯一一处调用的两组参数互换即可 ——
 *       这也是刻意只留一处调用点的原因。
 */
extern void hal_led_set_pixels(uint32_t color0, uint32_t brightness0,
                               uint32_t color1, uint32_t brightness1);

/**
 * @brief 熄灯、释放 RMT 设备并把数据引脚置为低功耗输入。深睡前调用。
 */
extern void hal_led_deinit(void);

#ifdef __cplusplus
}
#endif

#endif /* __HAL_LED_H__ */
