#ifndef __HAL_PWR_H__
#define __HAL_PWR_H__


/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>
#include <stdint.h>

/* 三个按键引脚：GPIO6(PREV) / GPIO4(NEXT) / GPIO5(FN)，低电平有效。 */
#define HAL_PWR_WAKE_PREV_MASK   (1ULL << 6)
#define HAL_PWR_WAKE_NEXT_MASK   (1ULL << 4)
#define HAL_PWR_WAKE_FN_MASK     (1ULL << 5)

/* PREV/NEXT 的上拉属于断电的 3V3_C 域，不能作为 ANY_LOW 深睡唤醒源。
 * 硬件将 R20/R21 改接常电 3V3 后可把两位加回。 */
#define HAL_PWR_WAKE_KEY_MASK    (HAL_PWR_WAKE_FN_MASK)


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
 * @brief 初始化电源管理：接管外设供电引脚（GPIO21，RTC 域）并打开供电。
 *
 * 深睡唤醒等价于重启，本函数是每次唤醒的必经路径，会先解除上一次深睡留下的
 * RTC hold 再置高——不解除则供电脚被锁在低电平，外设永远不上电且无法自恢复。
 */
extern void hal_pwr_init(void);

/**
 * @brief 进入深度睡眠
 *
 * 先断外设供电并 rtc_gpio_hold_en() 锁住低电平（深睡时数字 IO 域掉电，不 hold 的 pad
 * 会浮空、外设不保证断电），再注册唤醒源（EXT1 按键掩码 + 可选定时器）后
 * esp_deep_sleep_start()。一个唤醒源都没注册上时不睡而是重启：无唤醒源的深睡要拔电池
 * 才能恢复。
 */
extern void hal_pwr_enter_sleep(void);

/**
 * @brief 设置定时器唤醒时间
 * 
 * @param minutes 唤醒时间（单位：分钟），0表示不使用定时器唤醒
 */
extern void hal_pwr_set_timer_wakeup(uint32_t minutes);

/**
 * @brief 设置定时器唤醒时间（秒级粒度）
 *
 * 饭点唤醒算的是「距下一个唤醒点还有多久」，天然不是整分钟的倍数；分钟版只能向上
 * 取整，而餐前提醒的有效窗口只有半小时左右，取整误差足以让设备醒在窗口之外。
 *
 * 与 hal_pwr_set_timer_wakeup() 共用同一个内部状态，后调用者生效。
 *
 * @param seconds 唤醒时间（单位：秒），0表示不使用定时器唤醒
 */
extern void hal_pwr_set_timer_wakeup_seconds(uint32_t seconds);

/**
 * @brief 检查本次启动是否由深睡唤醒（EXT1 按键或定时器）。
 *
 * @return bool true-从深度睡眠唤醒，false-正常启动
 */
extern bool hal_pwr_check_wakeup(void);

/** @brief 返回本次 EXT1 深睡唤醒时处于有效电平的按键掩码；其它启动返回 0。 */
extern uint64_t hal_pwr_get_wakeup_key_mask(void);


#ifdef __cplusplus
}
#endif

#endif /* __HAL_PWR_H__ */
