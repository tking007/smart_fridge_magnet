/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/film_pin.c
 * Description: 「置顶自定义画面」纯决策逻辑实现。判定规则与每条取舍的理由见 film_pin.h。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stddef.h>   /* NULL   */
#include <string.h>   /* memchr */

#include "film_pin.h"

/*********************************************************************
 * LOCAL FUNCTIONS
 */

/* pinned 文件名里**禁止出现**的餐段关键字。
 *
 * 必须与 service_film.c 的 film_meal_keyword() 与 FILM_OVERVIEW_KEYWORD 保持一致：
 * 那边用 strstr 做包含匹配来选菜单图，这边若允许同名子串，一个文件就会同时是
 * 「pinned 照片」和「某一餐的详情页」，两条选图路径抢同一个文件。
 * 两处常量分居两个文件是有意的 —— service_film.c 依赖 ESP-IDF，不能被主机测试编译；
 * 一旦那边加了新关键字（比如加个下午茶），这里必须同步，测试里也断言了这份清单。 */
static const char *const k_reserved_keywords[] = {
    "today", "breakfast", "lunch", "dinner",
};

/* 有界的 strlen：绝不越过 max 个字节去找 NUL。 */
static size_t pin_bounded_len(const char *s, size_t max)
{
    const void *nul = memchr(s, '\0', max);
    return (nul != NULL) ? (size_t)((const char *)nul - s) : max;
}

/* [start, end) 的归属判定，支持跨零点。
 *
 * 不用「先归一化再比较」的通用写法，而是显式分两种情形，因为这两种情形对读代码的人
 * 意义完全不同（同日区间 / 跨午夜区间），而合并成模运算之后反而看不出跨零点被处理了。
 * start == end 落到第一个分支且恒 false —— 空窗口，见 film_pin.h。 */
static bool pin_in_window(uint32_t minute, uint32_t start, uint32_t end)
{
    if (start <= end) {
        /* 同一天内的区间（含 start == end 的空区间）。 */
        return (minute >= start) && (minute < end);
    }
    /* 跨午夜：22:00–06:00 等价于 [22:00, 24:00) ∪ [00:00, 06:00)。 */
    return (minute >= start) || (minute < end);
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool film_pin_should_show(const film_pin_cfg_t *cfg, int minute_of_day,
                          bool time_valid, bool in_meal_window)
{
    if (cfg == NULL) {
        return false;   /* 未配置 */
    }

    /* 横切所有模式：没有文件名就没有可显示的东西（见 film_pin.h）。
     * 放在 mode 判断之前，是为了让「配了模式但名字空」这种半份配置在每种模式下
     * 表现一致，而不是某些模式返回 true、某些返回 false。 */
    if (pin_bounded_len(cfg->filename, FILM_PIN_FILENAME_MAX) == 0u) {
        return false;
    }

    switch (cfg->mode) {
    case FILM_PIN_ALWAYS:
        /* 恒 true，不看时间 —— 没配网也能一直显示照片。 */
        return true;

    case FILM_PIN_WINDOW: {
        if (!time_valid) {
            /* 时间不可信时绝不猜时段（与 0x47 / 唤醒时间表同源的既有约定）。 */
            return false;
        }
        if (minute_of_day < 0) {
            /* 负的分钟数不是「越界」而是「坏值」，无法用取模给出合理含义。 */
            return false;
        }
        uint32_t minute = (uint32_t)minute_of_day % FILM_PIN_MINUTES_PER_DAY;
        return pin_in_window(minute,
                             (uint32_t)cfg->window_start_min % FILM_PIN_MINUTES_PER_DAY,
                             (uint32_t)cfg->window_end_min % FILM_PIN_MINUTES_PER_DAY);
    }

    case FILM_PIN_OUTSIDE_MEAL:
        /* 做饭窗口内让位给菜单，窗口外显示 pinned。
         * time_valid == false 时 in_meal_window 只能是 false，于是显示 pinned ——
         * 刻意的取舍，理由见 film_pin.h（宁可显示用户的照片，也不显示一张可能过期的
         * 「今日总览」）。 */
        return !in_meal_window;

    case FILM_PIN_OFF:
    default:
        /* 未知 mode 与 OFF 同处：非法配置绝不改变现有显示行为。 */
        return false;
    }
}

bool film_pin_cfg_is_valid(const film_pin_cfg_t *cfg)
{
    if (cfg == NULL) {
        return false;
    }

    if (cfg->mode > FILM_PIN_MODE_MAX) {
        return false;
    }

    /* 即便当前 mode 不用窗口也校验：记录会被 0x4D 回读、被 App 原样再发回来，
     * 让非法值在记录里过夜，迟早会在某次改 mode 之后突然生效。 */
    if ((uint32_t)cfg->window_start_min >= FILM_PIN_MINUTES_PER_DAY ||
        (uint32_t)cfg->window_end_min >= FILM_PIN_MINUTES_PER_DAY) {
        return false;
    }

    /* --- 文件名：非空、有界 NUL 结尾 --- */
    size_t n = pin_bounded_len(cfg->filename, FILM_PIN_FILENAME_MAX);
    if (n == 0u) {
        return false;   /* 空名 */
    }
    if (n >= FILM_PIN_FILENAME_MAX) {
        return false;   /* 缓冲区内找不到 NUL：超长/未终止，绝不当成字符串用 */
    }

    /* --- 文件名：安全基名（白名单字符集）---
     * 用白名单而不是「排除 / 与 \」：白名单天然挡掉空格、通配符、控制字符、非 ASCII，
     * 而黑名单每次都要想「还漏了什么」。 */
    for (size_t i = 0; i < n; i++) {
        char c = cfg->filename[i];
        bool ok = (c >= '0' && c <= '9') ||
                  (c >= 'A' && c <= 'Z') ||
                  (c >= 'a' && c <= 'z') ||
                  (c == '.') || (c == '_') || (c == '-');
        if (!ok) {
            return false;
        }
    }

    /* "." / ".." 本身，以及任何位置的 ".." —— 目录穿越的入口。 */
    if ((n == 1u && cfg->filename[0] == '.') ||
        (n == 2u && cfg->filename[0] == '.' && cfg->filename[1] == '.')) {
        return false;
    }
    for (size_t i = 0; i + 1u < n; i++) {
        if (cfg->filename[i] == '.' && cfg->filename[i + 1u] == '.') {
            return false;
        }
    }

    /* --- 文件名：必须以 ".film" 结尾 --- */
    size_t suffix_len = pin_bounded_len(FILM_PIN_REQUIRED_SUFFIX, FILM_PIN_FILENAME_MAX);
    if (n <= suffix_len) {
        /* 恰等于后缀长度也拒绝：".film" 没有基名部分，不是一个文件名。 */
        return false;
    }
    if (memcmp(&cfg->filename[n - suffix_len], FILM_PIN_REQUIRED_SUFFIX, suffix_len) != 0) {
        return false;
    }

    /* --- 文件名：不得包含餐段/总览关键字 ---
     * 这一条是本校验存在的主要原因，见 film_pin.h 的详细说明。 */
    for (size_t k = 0; k < sizeof(k_reserved_keywords) / sizeof(k_reserved_keywords[0]); k++) {
        if (strstr(cfg->filename, k_reserved_keywords[k]) != NULL) {
            return false;
        }
    }

    return true;
}

bool film_pin_name_is_menu_slot(const char *name)
{
    if (name == NULL) {
        return false;
    }
    /* 有界求长后再 strstr：与 film_pin_cfg_is_valid() 同样的理由 —— 调用方给的可能是一段
     * 定长缓冲里的名字，先确认 NUL 在界内，strstr 才不会读过界。
     * 这里的上界比 pinned 名字的 32 宽一档：菜单图的名字由后端决定，不受 pin 那条
     * 「≤31 字符」的约束（它约束的是用户能 pin 什么，不是设备上能有什么）。 */
    size_t n = pin_bounded_len(name, FILM_PIN_MENU_NAME_SCAN_MAX);
    if (n == 0 || n >= FILM_PIN_MENU_NAME_SCAN_MAX) {
        /* 空名字，或在扫描上界内找不到 NUL（不是一个可安全比较的 C 字符串）：
         * 一律判为「不属于菜单命名空间」。判 false 的后果是不删它 —— 保守方向正确，
         * 误删一张用户的图比留一张过期菜单严重得多。 */
        return false;
    }
    for (size_t k = 0; k < sizeof(k_reserved_keywords) / sizeof(k_reserved_keywords[0]); k++) {
        if (strstr(name, k_reserved_keywords[k]) != NULL) {
            return true;
        }
    }
    return false;
}
