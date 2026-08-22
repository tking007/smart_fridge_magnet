#ifndef __SERVICE_FILM_H__
#define __SERVICE_FILM_H__


/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>
#include <stdint.h>

#include "film_pin.h"   /* film_pin_cfg_t：置顶自定义画面的配置（纯逻辑模块） */


/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
#define FILM_TAG                    "film"

#define FILM_PLAY_MODE_MANUAL       (0)  // 手动播放模式
#define FILM_PLAY_MODE_AUTO         (1)  // 本地轮播模式
#define FILM_PLAY_MODE_WIFI         (2)  // WiFi轮播模式

/*********************************************************************
* TYPEDEFS
*/
typedef enum {
    MSG_FILM_DISPLAY,          // 显示指定图片
    MSG_FILM_NEXT,             // 显示下一张图片
    MSG_FILM_PREV,             // 显示上一张图片
    MSG_FILM_INIT,             // 初始化播放
    MSG_FILM_AUTO_PLAY,        // 自动播放
    MSG_FILM_CLEAR,            // EPD维护显示为白色用于长期保存
} film_msg_type_t;

typedef struct {
    film_msg_type_t ID;
    uint32_t file_id;  // 用于MSG_FILM_DISPLAY
} film_msg_t;

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
 * @brief 初始化照片播放服务
 *
 * 此函数用于初始化照片播放服务，创建任务和消息队列，
 * 初始化状态变量，检查服务参数。
 */
extern void service_film_init(void);

/**
 * @brief 显示指定图片
 *
 * 此函数用于显示指定ID的图片。
 *
 * @param file_id 文件ID
 */
extern void service_film_display(uint32_t file_id);

/**
 * @brief 显示下一张图片
 *
 * 此函数用于显示下一张图片，实现循环切换。
 */
extern void service_film_next(void);

/**
 * @brief 显示上一张图片
 *
 * 此函数用于显示上一张图片，实现循环切换。
 */
extern void service_film_prev(void);

/**
 * @brief 清除显示
 *
 * 此函数用于清除当前显示的图片，维护EPD显示为白色。
 */
extern void service_film_clear(void);

/**
 * @brief 设置播放模式
 *
 * 此函数用于设置播放模式。
 *
 * @param mode 播放模式（0：手动，1：自动）
 */
extern void service_film_set_play_mode(uint8_t mode);

/**
 * @brief 获取当前显示的文件ID
 *
 * 此函数用于获取当前显示的文件ID。
 *
 * @return uint32_t 当前文件ID
 */
extern uint32_t service_film_get_current_id(void);

/**
 * @brief 获取加载完成状态
 *
 * 此函数用于获取加载完成状态。
 *
 * @return uint8_t 加载完成状态（0：未完成，1：完成）
 */
extern uint8_t service_film_get_load_complete(void);

/**
 * @brief 获取电子纸「刷新进行中」标志（与 load_complete 解耦，供休眠互斥判据）
 *
 * @return uint8_t 1=正在送屏刷新，0=空闲（无图/清屏后为 0，不会误判为忙）
 */
extern uint8_t service_film_is_refreshing(void);

/**
 * @brief 当前应展示的内容类别（按做饭时间窗口判定）
 *
 * - `FILM_MEAL_UNKNOWN`：**时间不可信**（未配网 / 未 SNTP / 冷启动仍是 1970）。
 *   不得猜测时段，也不得触发定时自动切换。
 * - `FILM_MEAL_NONE`：时间有效但**不在任何做饭窗口内** —— 这是一个确定的业务决定：
 *   显示今日总览。
 * - 其余三项：正处于该餐的做饭窗口内，应显示这一餐的详细做法。
 */
typedef enum
{
    FILM_MEAL_UNKNOWN = 0,
    FILM_MEAL_NONE,
    FILM_MEAL_BREAKFAST,
    FILM_MEAL_LUNCH,
    FILM_MEAL_DINNER,
} film_meal_slot_t;

/**
 * @brief 按本地时间判断当前应展示什么
 *
 * 做饭时间窗口（含起、不含止）：早餐 06:00–09:00、午餐 11:00–13:30、晚餐 17:00–19:30。
 * **窗口之外返回 `FILM_MEAL_NONE`（显示今日总览）**，而不是把一天三等分——详细做法只在
 * 准备这一餐时有用，平时冰箱贴上更该是总览。时间不可信时返回 `FILM_MEAL_UNKNOWN`。
 */
extern film_meal_slot_t service_film_get_meal_slot_now(void);

/** @brief 类别的可读名（用于日志）：breakfast/lunch/dinner/overview/unknown */
extern const char* service_film_meal_slot_str(film_meal_slot_t slot);

/**
 * @brief 按当前时间显示应展示的那一张
 *
 * 做饭窗口内显示对应餐的详细做法；窗口外、时间不可信、或找不到对应文件时回落显示
 * 今日总览（再找不到则显示列表第一张，保证有画面）。
 *
 * 按**文件名包含** breakfast / lunch / dinner / today 匹配，因为发送方会加序号前缀
 * 以固定轮播次序，前缀不参与匹配。
 *
 * **置顶自定义画面（pin）优先**：若当前 pin 配置判定该显示 pinned 图（见 film_pin.h
 * 的三种模式）且该文件仍在设备上，就显示它并直接返回；文件不在了则记一条 warning 后
 * **继续走上述菜单逻辑**，绝不空屏。
 *
 * 不改变按键轮播语义：用户仍可用侧边键翻到任意一张，BLE `0x07` 指定显示同样不受
 * pin 影响 —— pin 的语义是「自动选图时优先它」，不是「锁死屏幕」。
 *
 * @return int >=0 实际显示的文件索引；-2 设备上没有任何文件
 */
extern int service_film_display_for_now(void);

/**
 * @brief 同 service_film_display_for_now()，但目标已在屏上时不刷屏
 *
 * @return >=0 已切到该索引；-1 该显示的正是当前这张（未刷屏）；-2 没有任何文件
 */
extern int service_film_display_for_now_if_changed(void);

/**
 * @brief 定时检查并按时间自动切换画面（由低功耗管理器周期调用）
 *
 * 仅在「应展示的类别发生变化」时才切换，避免反复刷屏（电子纸刷一次约 20 秒且耗电）。
 * 时间不可信时不做任何切换。用户用按键手动翻页后有一段保护期，期间不自动切换，
 * 以免打断用户正在看的那一张。
 *
 * pin 模式的影响：`FILM_PIN_ALWAYS` 直接返回（一天连一次刷屏都不该有）；
 * `FILM_PIN_WINDOW` / `FILM_PIN_OUTSIDE_MEAL` 参与**双向**切换 —— 目标由
 * (餐段, 是否显示 pinned) 两维共同决定，所以窗口在同一餐段中途开始或结束时也能切换。
 */
extern void service_film_auto_display_tick(void);

/**
 * @brief 记录一次「用户手动切换画面」，用于抑制随后的自动切换
 *
 * 由按键翻页路径调用。
 */
extern void service_film_note_manual_display(void);

/**
 * @brief 丢弃 pin 配置的进程内缓存，下次选图时重新从 NVS 载入
 *
 * 由唯一的写入路径（BLE `0x4C`）在写入或擦除 pin 记录之后调用。同时会清掉自动切换的
 * 「上次目标」记忆，使下一次 `service_film_auto_display_tick()` 必然重新对齐一次 ——
 * 否则刚改完 pin 可能要等到下次跨时段才生效，用户会以为命令没生效而反复重发。
 */
extern void service_film_pin_invalidate(void);

/**
 * @brief 读回当前 pin 配置（供 BLE `0x4D` 查询）
 *
 * 读的是进程内缓存（首次调用时从独立 NVS key 载入）。**无论返回值如何 `*out` 都会被
 * 填成一个确定的值**：未设置时是 `mode == FILM_PIN_OFF` 的全零配置，可直接按该布局回发。
 *
 * @param out 输出配置（NULL 时返回 false 且不写）
 * @return true 当前确实设置了 pin（mode != OFF）；false 未设置
 */
extern bool service_film_pin_get(film_pin_cfg_t *out);


#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_FILM_H__ */
