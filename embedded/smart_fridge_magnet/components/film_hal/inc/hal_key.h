#ifndef __HAL_KEY_H__
#define __HAL_KEY_H__
/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stdbool.h>
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

/**
 * @brief 物理按键标识
 *
 * 本机是三个独立按键，不是旋转编码器：侧边并排两个（上/下），右侧单独一个。
 *
 * 引脚（硬件既有布线）：
 *   KEY_ID_PREV -> GPIO6
 *   KEY_ID_NEXT -> GPIO4
 *   KEY_ID_FN   -> GPIO5
 * 三个引脚均由 hal_pwr 配为 EXT1 ANY_LOW 深睡唤醒源。
 */
typedef enum {
    KEY_ID_PREV = 0,   /* 侧边上键：上一张 */
    KEY_ID_NEXT,       /* 侧边下键：下一张 */
    KEY_ID_FN,         /* 右侧单键：蓝牙控制 / 深睡唤醒 */
    KEY_ID_MAX,
} key_id_t;

/**
 * @brief 按键事件类型
 *
 * 仅区分短按与长按：短按在【释放】时判定（避免与长按重复触发），
 * 长按在按住达到阈值时立即触发一次，随后释放不再产生短按。
 */
typedef enum {
    KEY_EVENT_SHORT = 0,
    KEY_EVENT_LONG,
    KEY_EVENT_MAX,
} key_event_t;

/** 按键事件回调（无参数）。 */
typedef void (*key_callback_t)(void);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 初始化三键驱动
 *
 * 配置三个 GPIO 为输入（外部已有上拉，按下为低电平），并创建轮询任务做消抖与
 * 短按/长按判定。重复调用安全。
 */
extern void hal_key_init(void);

/**
 * @brief 反初始化三键驱动
 *
 * 停止轮询任务并把按键引脚置为高阻输入以降低漏电。深睡前调用。
 * 深睡唤醒由 hal_pwr 用 EXT1 单独配置。
 */
extern void hal_key_deinit(void);

/**
 * @brief 注册按键事件回调
 *
 * 同一个 (按键, 事件) 可注册多个回调，按注册顺序调用。
 *
 * @param id   按键标识
 * @param evt  事件类型
 * @param cb   回调函数
 * @return 0 成功；负值失败（-1 参数非法、-2 该槽位回调已满）
 */
extern int hal_key_register_cb(key_id_t id, key_event_t evt, key_callback_t cb);

/**
 * @brief 查询按键当前是否处于按下状态（已消抖）
 *
 * @param id 按键标识
 * @return true 表示当前按下
 */
extern bool hal_key_is_pressed(key_id_t id);

#ifdef __cplusplus
}
#endif
#endif /* __HAL_KEY_H__ */
