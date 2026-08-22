/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/film_pin.h
 * Description: 「置顶自定义画面」（pinned film）纯决策逻辑：给定一份 pin 配置、
 *   当前当地分钟数、时间可信度与「是否落在做饭窗口内」，算出此刻该不该显示那张
 *   用户自选的图（手绘涂鸦 / 相册照片）。
 *
 *   pin 必须持久化才有意义：service_film.c 里记录「上次自动切换所依据的时段」的状态是
 *   普通 static（本项目没有任何 RTC_DATA_ATTR），深睡唤醒后归零；时间一同步，第一次
 *   service_film_auto_display_tick() 就会把自定义图换成菜单，用户的感受是照片自己没了。
 *   因此 pin 是「文件名 + 模式（+ 可选时间窗口）」存进 NVS，选图路径每次先问一句
 *   「现在该显示 pinned 吗」。
 *
 *   只依赖 <stdint.h>/<stdbool.h>/<stddef.h>，不碰 ESP-IDF / NVS / FreeRTOS /
 *   文件系统 / 时间函数，可在宿主机直接编译测试；读 NVS、在文件列表里找文件名、送屏
 *   等副作用留在 service_param 与 service_film。
 *
 *********************************************************************/
#ifndef __FILM_PIN_H__
#define __FILM_PIN_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* 一天的分钟数。窗口两端的合法域是 [0, 1439]。 */
#define FILM_PIN_MINUTES_PER_DAY    (1440u)

/* pinned 文件名缓冲容量（含结尾 NUL），故有效名长上限 31 字符。
 *
 * 为什么是 32：BLE FILE_NAME 阶段允许的名字更长，但 pin 记录是【定长】的 NVS blob，
 * 名字字段一变长整条记录的长度就变，而记录长度正是判断「这条记录是否属于当前布局」
 * 的依据。32 字节足够容纳约定的命名（photo_20260419_1830.film = 26 字符），
 * 又让整条记录只有几十字节。 */
#define FILM_PIN_FILENAME_MAX       (32u)

/* pinned 文件名必须的后缀。设备上只有 .film 能被送屏，pin 一个别的后缀必然显示不出来。 */
#define FILM_PIN_REQUIRED_SUFFIX    ".film"

/*********************************************************************
 * TYPEDEFS
 */
/**
 * @brief pin 模式。
 *
 * 取值写进持久化记录，**不得重排或复用**（改了会让已写入的记录被解读成另一种模式）。
 */
typedef enum {
    FILM_PIN_OFF = 0,          /* 不 pin，选图行为与没有本模块时完全一致 */
    FILM_PIN_ALWAYS = 1,       /* 一直显示自定义图（取缔菜单）*/
    FILM_PIN_WINDOW = 2,       /* 只在 [start, end) 显示自定义图，其余按菜单逻辑 */
    FILM_PIN_OUTSIDE_MEAL = 3, /* 做饭窗口内显示菜单，窗口外显示自定义图 */
} film_pin_mode_t;

/* 合法 mode 的上界。校验用这个宏而不是硬写 3，新增模式时只改一处。 */
#define FILM_PIN_MODE_MAX           ((uint8_t)FILM_PIN_OUTSIDE_MEAL)

/**
 * @brief 一份 pin 配置。
 *
 * 布局与 BLE 0x4C/0x4D 的载荷、以及 NVS pin 记录的 body 一一对应，三处不各写一遍。
 */
typedef struct {
    uint8_t  mode;                          /* film_pin_mode_t 的取值           */
    uint16_t window_start_min;              /* 窗口起（含），当地分钟数 0..1439  */
    uint16_t window_end_min;                /* 窗口止（不含），当地分钟数 0..1439 */
    char     filename[FILM_PIN_FILENAME_MAX]; /* pinned 文件的安全基名，NUL 结尾  */
} film_pin_cfg_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 当前该不该显示 pinned 图。
 *
 * 纯函数，无副作用、无全局状态。
 *
 * 各模式的判定与理由：
 *
 *  - `FILM_PIN_OFF`：恒 false。
 *
 *  - `FILM_PIN_ALWAYS`：恒 true，完全不依赖时间。没配网（没有可信时间）时也能一直显示
 *    用户的照片；且让 service_film_auto_display_tick() 可以直接 return，一天连一次刷屏
 *    都没有（电子纸刷一次约 20 秒且耗电）。
 *
 *  - `FILM_PIN_WINDOW`：`time_valid == false` 时返回 false —— 时间不可信时绝不猜时段，
 *    由错误时间算出的窗口判定会让设备在毫无意义的时刻切换画面。时间可信时判
 *    `[start, end)`（含起、不含止），支持跨零点（`start > end` 表示跨午夜，如
 *    22:00–06:00）。`start == end` 是空窗口（恒 false），零长度区间是 `[start, end)`
 *    唯一自洽的读法；想表达「全天」应该用 ALWAYS。
 *
 *  - `FILM_PIN_OUTSIDE_MEAL`：返回 `!in_meal_window`。`time_valid == false` 时调用方给出
 *    的 `in_meal_window` 只能是 false，于是本模式会显示 pinned。这是刻意的取舍：时间不
 *    可信时显示用户自选照片，比显示一张标着「今日」却可能是上周的总览更诚实 —— 后者是
 *    会误导人的错误信息。与 WINDOW 取向不同，是因为 WINDOW 里时间是用户明确要求的条件，
 *    而 OUTSIDE_MEAL 里 pinned 才是兜底那一侧。
 *
 * 横切所有模式：`cfg->filename` 为空串时一律 false。配了模式却没有文件名是无效配置，
 * 返回 true 只会让调用方去找一个空名字、找不到、再回落。
 *
 * @param cfg            pin 配置；NULL 视为未配置，返回 false
 * @param minute_of_day  当地时间分钟数；`time_valid == false` 时本参数被忽略。
 *                       负值视为无效（返回 false）；>= 1440 的值按 mod 1440 归一，与
 *                       wake_schedule_next_seconds() 对越界 now 的处置一致。
 * @param time_valid     时间是否可信（SNTP 是否成功；冷启动的 1970 不算可信）
 * @param in_meal_window 当前是否落在某个做饭窗口内（由调用方按 film_meal_slot_now()
 *                       判定；时间不可信时只能传 false）
 * @return true 表示此刻应显示 pinned 图
 */
bool film_pin_should_show(const film_pin_cfg_t *cfg, int minute_of_day,
                          bool time_valid, bool in_meal_window);

/**
 * @brief 校验一份 pin 配置是否合法；非法时调用方不得写入。
 *
 * 纯谓词，单独导出以便 BLE 写入路径（0x4C）、NVS 写入路径与主机测试共用同一份判据。
 *
 * 合法条件（全部满足）：
 *  1) `cfg != NULL`；
 *  2) `mode <= FILM_PIN_MODE_MAX`；
 *  3) `window_start_min` 与 `window_end_min` 都 < 1440（即便当前 mode 不用窗口也校验：
 *     记录会被回读并被 App 原样发回来）；
 *  4) `filename` 非空、在 FILM_PIN_FILENAME_MAX 内 NUL 结尾（有效长度 < 32）；
 *  5) `filename` 是安全基名：只含 `[0-9A-Za-z._-]`，不是 `"."` 或 `".."`，不含 `".."`
 *     子串。比 ble_filename_is_safe() 更严：pin 名会存进 NVS 长期使用，不像一次性的
 *     传输文件名随会话消失；
 *  6) `filename` 以 `".film"` 结尾（设备上只有 .film 能送屏）；
 *  7) `filename` 不含 `today` / `breakfast` / `lunch` / `dinner` 任一关键字。
 *
 * 第 7 条必须由固件拒绝而不能只靠 App 自律：service_film.c 的
 * film_find_file_by_keyword() 用 strstr 做包含匹配（因为发送方会加序号前缀），所以一张
 * 名为 `my_lunch.film` 的照片会同时被当成午餐详情页 —— 中午作为菜单被选中、窗口外作为
 * pinned 被选中，两条路径抢同一个文件，而症状（照片有时在中午自己冒出来）离成因极远。
 *
 * 本谓词不为 `mode == OFF` 开特例，OFF 同样要求合法文件名：取消 pin 的落地方式是擦掉
 * 整条记录（service_param_pinned_erase），清除路径根本不经过本函数。
 *
 * @param cfg 待校验的配置（NULL 返回 false）
 * @return true 表示合法、可以写入
 */
bool film_pin_cfg_is_valid(const film_pin_cfg_t *cfg);

/**
 * @brief 这个文件名是否属于「菜单画面」命名空间。
 *
 * 判据与 service_film.c 选菜单图时一致：名字包含 `today` / `breakfast` / `lunch` /
 * `dinner` 任一关键字（区分大小写）。与 film_pin_cfg_is_valid() 里「pinned 名字不得含
 * 这些关键字」互补：一个文件要么是菜单图、要么可以被 pin，不可能两者都是。
 *
 * 用于整套下载成功后的补集清理：删掉不在本次 `items[]` 里的旧菜单图。有了这个判据，
 * 清理才敢按名字而不是按一个硬编码文件名来做，同时保证绝不碰用户自己的
 * `draw_*.film` / `photo_*.film`。
 *
 * @param name NUL 结尾的文件名；NULL 或空串返回 false。
 * @return 属于菜单命名空间返回 true。
 */
bool film_pin_name_is_menu_slot(const char *name);

/* film_pin_name_is_menu_slot() 扫描文件名时的字节上界。
 * 比 FILM_PIN_FILENAME_MAX（32）宽一档：那个上限约束「用户能 pin 什么」，而本函数判的是
 * 「设备上已有的这张图是不是菜单图」，后者的名字由后端决定，不受前者约束。 */
#define FILM_PIN_MENU_NAME_SCAN_MAX   (64u)

#ifdef __cplusplus
}
#endif

#endif /* __FILM_PIN_H__ */
