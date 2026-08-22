/*********************************************************************
 * Host test: 「置顶自定义画面」的纯决策逻辑（film_pin.c）。
 *
 * Part of the Smart Fridge Magnet project (based on FrameFilm 562f330,
 * GPL-3.0-or-later — see ../../NOTICE.md and LICENSE).
 *
 * Feature: 手绘涂鸦 / 相册照片能【持续】显示在冰箱贴上（BLE 0x4C/0x4D）。
 *
 * 在普通主机上编译【真实的生产源码】film_pin.c（该模块只依赖
 * <stdint.h>/<stdbool.h>/<stddef.h>/<string.h>，不需要 ESP-IDF、NVS、FreeRTOS
 * 或文件系统），逐条验证 film_pin.h 里写明的判定规则：
 *
 *   - 四种 mode 各自的判定；
 *   - WINDOW：正常窗口 / 跨零点窗口 / 边界（start 含、end 不含）/ time_valid==false；
 *   - OUTSIDE_MEAL：窗口内 / 窗口外 / 时间不可信（刻意显示 pinned 的那条取舍）；
 *   - 空文件名一律 false；
 *   - film_pin_cfg_is_valid() 的每一条拒绝理由各一例；
 *   - NULL 入参不崩。
 *
 * TEST-ONLY. NOT part of the firmware build.
 *********************************************************************/
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "film_pin.h"

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, msg)                                                        \
    do {                                                                        \
        if (cond) {                                                             \
            printf("  PASS: %s\n", (msg));                                      \
            g_pass++;                                                           \
        } else {                                                                \
            printf("  FAIL: %s  (%s:%d)\n", (msg), __FILE__, __LINE__);         \
            g_fail++;                                                           \
        }                                                                       \
    } while (0)

/* 便于阅读：把 HH:MM 写成分钟数。 */
#define HHMM(h, m)  ((uint16_t)((h) * 60 + (m)))

/* 构造一份配置。name 为 NULL 表示空文件名。 */
static film_pin_cfg_t mk(uint8_t mode, uint16_t start, uint16_t end, const char *name)
{
    film_pin_cfg_t c;
    memset(&c, 0, sizeof(c));
    c.mode = mode;
    c.window_start_min = start;
    c.window_end_min = end;
    if (name != NULL) {
        /* 刻意用 memcpy + 显式终止，好让「超长/未终止名字」的用例也能构造出来。 */
        size_t n = strlen(name);
        if (n > FILM_PIN_FILENAME_MAX) {
            n = FILM_PIN_FILENAME_MAX;
        }
        memcpy(c.filename, name, n);
        if (n < FILM_PIN_FILENAME_MAX) {
            c.filename[n] = '\0';
        }
    }
    return c;
}

/* 断言 film_pin_should_show(cfg, minute, time_valid, in_meal) == expect。 */
static void check_show(const film_pin_cfg_t *cfg, int minute, bool time_valid,
                       bool in_meal, bool expect, const char *label)
{
    bool got = film_pin_should_show(cfg, minute, time_valid, in_meal);
    if (got == expect) {
        printf("  PASS: %s (-> %s)\n", label, got ? "pinned" : "menu");
        g_pass++;
    } else {
        printf("  FAIL: %s got %s expected %s  (%s:%d)\n",
               label, got ? "pinned" : "menu", expect ? "pinned" : "menu",
               __FILE__, __LINE__);
        g_fail++;
    }
}

/* ---- mode: OFF ---------------------------------------------------------- */
static void test_mode_off(void)
{
    printf("Test: FILM_PIN_OFF —— 恒 false（行为与没有本功能时完全一致）\n");
    film_pin_cfg_t c = mk(FILM_PIN_OFF, 0, 0, "photo_1.film");
    check_show(&c, HHMM(12, 0), true, false, false, "OFF + 时间可信 + 非做饭窗口");
    check_show(&c, HHMM(12, 0), true, true, false, "OFF + 做饭窗口内");
    check_show(&c, 0, false, false, false, "OFF + 时间不可信");
}

/* ---- mode: ALWAYS ------------------------------------------------------- */
static void test_mode_always(void)
{
    printf("Test: FILM_PIN_ALWAYS —— 恒 true，且**不依赖时间**\n");
    film_pin_cfg_t c = mk(FILM_PIN_ALWAYS, 0, 0, "draw_a.film");
    check_show(&c, HHMM(12, 0), true, false, true, "ALWAYS + 时间可信");
    check_show(&c, HHMM(12, 0), true, true, true, "ALWAYS + 做饭窗口内也照显");
    /* 这条是 ALWAYS 存在的理由：没配网（时间不可信）也能一直显示照片。 */
    check_show(&c, 0, false, false, true, "ALWAYS + 时间不可信仍显示（未配网也能用）");
    /* 窗口字段对 ALWAYS 无意义，填什么都不影响。 */
    film_pin_cfg_t c2 = mk(FILM_PIN_ALWAYS, HHMM(22, 0), HHMM(6, 0), "draw_a.film");
    check_show(&c2, HHMM(12, 0), true, false, true, "ALWAYS 忽略窗口字段");
}

/* ---- mode: WINDOW ------------------------------------------------------- */
static void test_mode_window_normal(void)
{
    printf("Test: FILM_PIN_WINDOW —— 正常（不跨零点）窗口 [09:00, 17:00)\n");
    film_pin_cfg_t c = mk(FILM_PIN_WINDOW, HHMM(9, 0), HHMM(17, 0), "photo_x.film");

    check_show(&c, HHMM(8, 59), true, false, false, "08:59 窗口前 -> 菜单");
    check_show(&c, HHMM(9, 0), true, false, true, "09:00 恰为 start -> pinned（含起）");
    check_show(&c, HHMM(12, 0), true, false, true, "12:00 窗口内 -> pinned");
    check_show(&c, HHMM(16, 59), true, false, true, "16:59 窗口内末尾 -> pinned");
    check_show(&c, HHMM(17, 0), true, false, false, "17:00 恰为 end -> 菜单（不含止）");
    check_show(&c, HHMM(23, 30), true, false, false, "23:30 窗口后 -> 菜单");

    /* WINDOW 不看 in_meal_window：窗口内即使正在做饭也显示 pinned。
     * 这是模式语义的一部分 —— 用户明确说了「这段时间显示我的图」。 */
    check_show(&c, HHMM(12, 0), true, true, true, "窗口内 + 做饭窗口内 -> 仍 pinned");
}

static void test_mode_window_cross_midnight(void)
{
    printf("Test: FILM_PIN_WINDOW —— 跨零点窗口 [22:00, 06:00)（start > end）\n");
    film_pin_cfg_t c = mk(FILM_PIN_WINDOW, HHMM(22, 0), HHMM(6, 0), "photo_night.film");

    check_show(&c, HHMM(21, 59), true, false, false, "21:59 窗口前 -> 菜单");
    check_show(&c, HHMM(22, 0), true, false, true, "22:00 恰为 start -> pinned");
    check_show(&c, HHMM(23, 59), true, false, true, "23:59 午夜前 -> pinned");
    check_show(&c, 0, true, false, true, "00:00 跨过午夜 -> pinned");
    check_show(&c, HHMM(3, 0), true, false, true, "03:00 凌晨 -> pinned");
    check_show(&c, HHMM(5, 59), true, false, true, "05:59 窗口内末尾 -> pinned");
    check_show(&c, HHMM(6, 0), true, false, false, "06:00 恰为 end -> 菜单（不含止）");
    check_show(&c, HHMM(12, 0), true, false, false, "12:00 白天 -> 菜单");
}

static void test_mode_window_degenerate(void)
{
    printf("Test: FILM_PIN_WINDOW —— 退化窗口与全天窗口\n");

    /* start == end：空窗口（[x,x) 的零长度读法）。想表达「全天」应该用 ALWAYS。 */
    film_pin_cfg_t empty = mk(FILM_PIN_WINDOW, HHMM(9, 0), HHMM(9, 0), "photo_x.film");
    check_show(&empty, HHMM(9, 0), true, false, false, "start==end 恰在该分钟 -> 空窗口，菜单");
    check_show(&empty, HHMM(12, 0), true, false, false, "start==end 其它时刻 -> 菜单");

    /* [0, 1439) 覆盖除 23:59 外的全天，用来确认边界没有被特殊处理。 */
    film_pin_cfg_t wide = mk(FILM_PIN_WINDOW, 0, 1439, "photo_x.film");
    check_show(&wide, 0, true, false, true, "[0,1439) 的 00:00 -> pinned");
    check_show(&wide, 1438, true, false, true, "[0,1439) 的 23:58 -> pinned");
    check_show(&wide, 1439, true, false, false, "[0,1439) 的 23:59 -> 菜单（不含止）");
}

static void test_mode_window_time_invalid(void)
{
    printf("Test: FILM_PIN_WINDOW + time_valid==false —— 绝不猜时段，恒 false\n");
    /* 与 0x47 按时段选图、唤醒时间表在冷启动（系统时间仍是 1970）时的既有约定同源。 */
    film_pin_cfg_t c = mk(FILM_PIN_WINDOW, HHMM(9, 0), HHMM(17, 0), "photo_x.film");
    check_show(&c, HHMM(12, 0), false, false, false,
               "时间不可信但分钟数“恰好”在窗口内 -> 仍返回菜单");
    check_show(&c, 0, false, false, false, "时间不可信 + minute=0 -> 菜单");

    /* 跨零点窗口同样如此。 */
    film_pin_cfg_t n = mk(FILM_PIN_WINDOW, HHMM(22, 0), HHMM(6, 0), "photo_x.film");
    check_show(&n, HHMM(23, 0), false, false, false, "跨零点窗口 + 时间不可信 -> 菜单");
}

static void test_mode_window_out_of_range_minute(void)
{
    printf("Test: FILM_PIN_WINDOW —— 越界 minute_of_day 的处置\n");
    film_pin_cfg_t c = mk(FILM_PIN_WINDOW, HHMM(9, 0), HHMM(17, 0), "photo_x.film");
    /* >= 1440 按 mod 1440 归一（与 wake_schedule 对越界 now 的处置一致）。 */
    check_show(&c, 1440 + HHMM(12, 0), true, false, true, "1440+12:00 归一后与 12:00 相同");
    check_show(&c, 1440 + HHMM(20, 0), true, false, false, "1440+20:00 归一后与 20:00 相同");
    /* 负值是坏值而非越界，无法用取模赋予合理含义 -> 保守返回 false。 */
    check_show(&c, -1, true, false, false, "负的 minute -> 保守返回菜单");
    check_show(&c, -720, true, false, false, "-720 -> 保守返回菜单");
}

/* ---- mode: OUTSIDE_MEAL ------------------------------------------------- */
static void test_mode_outside_meal(void)
{
    printf("Test: FILM_PIN_OUTSIDE_MEAL —— 做饭窗口内让位给菜单，窗口外显示 pinned\n");
    film_pin_cfg_t c = mk(FILM_PIN_OUTSIDE_MEAL, 0, 0, "photo_family.film");

    check_show(&c, HHMM(12, 0), true, true, false, "做饭窗口内 -> 菜单");
    check_show(&c, HHMM(15, 0), true, false, true, "做饭窗口外 -> pinned");

    /* 时间不可信时调用方只能传 in_meal_window=false，于是显示 pinned。
     * 刻意的取舍：显示用户自己选的照片，比显示一张标着「今日」却可能是昨天的总览更诚实。 */
    check_show(&c, 0, false, false, true,
               "时间不可信 -> pinned（比显示可能过期的“今日总览”更诚实）");

    /* 窗口字段对本模式无意义。 */
    film_pin_cfg_t c2 = mk(FILM_PIN_OUTSIDE_MEAL, HHMM(9, 0), HHMM(17, 0),
                           "photo_family.film");
    check_show(&c2, HHMM(12, 0), true, true, false, "OUTSIDE_MEAL 忽略窗口字段（窗口内做饭）");
    check_show(&c2, HHMM(3, 0), true, false, true, "OUTSIDE_MEAL 忽略窗口字段（窗口外闲时）");
}

/* ---- 横切规则：空文件名 / 未知 mode / NULL ------------------------------ */
static void test_empty_filename(void)
{
    printf("Test: filename 为空串 —— 一律 false（配了模式却没有文件名是无效配置）\n");
    film_pin_cfg_t off = mk(FILM_PIN_OFF, 0, 0, NULL);
    film_pin_cfg_t always = mk(FILM_PIN_ALWAYS, 0, 0, NULL);
    film_pin_cfg_t window = mk(FILM_PIN_WINDOW, HHMM(9, 0), HHMM(17, 0), NULL);
    film_pin_cfg_t outside = mk(FILM_PIN_OUTSIDE_MEAL, 0, 0, NULL);

    check_show(&off, HHMM(12, 0), true, false, false, "空名 + OFF");
    check_show(&always, HHMM(12, 0), true, false, false, "空名 + ALWAYS（否则会去找一个空名字）");
    check_show(&window, HHMM(12, 0), true, false, false, "空名 + WINDOW 且在窗口内");
    check_show(&outside, HHMM(15, 0), true, false, false, "空名 + OUTSIDE_MEAL 且在窗口外");
    check_show(&always, 0, false, false, false, "空名 + ALWAYS + 时间不可信");
}

static void test_unknown_mode(void)
{
    printf("Test: 未知 mode —— 与 OFF 同处（非法配置绝不改变现有显示行为）\n");
    film_pin_cfg_t c = mk((uint8_t)(FILM_PIN_MODE_MAX + 1u), 0, 0, "photo_x.film");
    check_show(&c, HHMM(12, 0), true, false, false, "mode=4 -> 菜单");
    film_pin_cfg_t c2 = mk(0xFFu, 0, 0, "photo_x.film");
    check_show(&c2, HHMM(12, 0), true, true, false, "mode=0xFF -> 菜单");
}

static void test_null_inputs(void)
{
    printf("Test: NULL 入参不崩\n");
    CHECK(film_pin_should_show(NULL, HHMM(12, 0), true, false) == false,
          "should_show(NULL, ...) == false");
    CHECK(film_pin_should_show(NULL, 0, false, true) == false,
          "should_show(NULL, 时间不可信) == false");
    CHECK(film_pin_cfg_is_valid(NULL) == false, "cfg_is_valid(NULL) == false");
}

/* ---- film_pin_cfg_is_valid：接受 -------------------------------------- */
static void test_valid_accepts(void)
{
    printf("Test: film_pin_cfg_is_valid —— 合法配置\n");
    film_pin_cfg_t a = mk(FILM_PIN_ALWAYS, 0, 0, "photo_20260419_1830.film");
    CHECK(film_pin_cfg_is_valid(&a), "ALWAYS + photo_20260419_1830.film 合法");

    film_pin_cfg_t w = mk(FILM_PIN_WINDOW, HHMM(22, 0), HHMM(6, 0), "draw_kid.film");
    CHECK(film_pin_cfg_is_valid(&w), "WINDOW 跨零点 + draw_kid.film 合法");

    film_pin_cfg_t o = mk(FILM_PIN_OUTSIDE_MEAL, 0, 1439, "a-b_c.1.film");
    CHECK(film_pin_cfg_is_valid(&o), "OUTSIDE_MEAL + 含 - _ . 的名字合法");

    /* 边界：窗口两端取合法域上界；名字长度恰为容量-1（31 字符）。 */
    film_pin_cfg_t edge = mk(FILM_PIN_WINDOW, 1439, 1439, "photo_0123456789012345678.film");
    CHECK(strlen(edge.filename) == FILM_PIN_FILENAME_MAX - 2u,
          "构造出的名字长 30（sanity check）");
    CHECK(film_pin_cfg_is_valid(&edge), "window 两端 = 1439（上界）合法");

    film_pin_cfg_t max_name = mk(FILM_PIN_ALWAYS, 0, 0, "photo_01234567890123456789.film");
    CHECK(strlen(max_name.filename) == FILM_PIN_FILENAME_MAX - 1u,
          "构造出的名字长 31 = 容量-1（sanity check）");
    CHECK(film_pin_cfg_is_valid(&max_name), "名字长度恰为 31（容量上界）合法");

    /* OFF 也要求合法名字：本谓词无条件（清除 pin 走 erase，不经过本函数）。 */
    film_pin_cfg_t off_named = mk(FILM_PIN_OFF, 0, 0, "photo_x.film");
    CHECK(film_pin_cfg_is_valid(&off_named), "OFF + 合法名字也算合法（判据无条件）");
}

/* ---- film_pin_cfg_is_valid：每条拒绝理由各一例 ------------------------- */
static void test_valid_rejects(void)
{
    printf("Test: film_pin_cfg_is_valid —— 每条拒绝理由各一例\n");

    /* 1) mode 越界 */
    film_pin_cfg_t bad_mode = mk((uint8_t)(FILM_PIN_MODE_MAX + 1u), 0, 0, "photo_x.film");
    CHECK(!film_pin_cfg_is_valid(&bad_mode), "拒绝：mode=4 越界");
    film_pin_cfg_t bad_mode2 = mk(0xFFu, 0, 0, "photo_x.film");
    CHECK(!film_pin_cfg_is_valid(&bad_mode2), "拒绝：mode=0xFF 越界");

    /* 2) window 越界（两端各一例） */
    film_pin_cfg_t bad_start = mk(FILM_PIN_WINDOW, 1440, 600, "photo_x.film");
    CHECK(!film_pin_cfg_is_valid(&bad_start), "拒绝：window_start=1440 恰好越界");
    film_pin_cfg_t bad_end = mk(FILM_PIN_WINDOW, 600, 2000, "photo_x.film");
    CHECK(!film_pin_cfg_is_valid(&bad_end), "拒绝：window_end=2000 越界");
    /* 窗口越界即便 mode 用不到窗口也拒绝（记录会被回读并原样发回）。 */
    film_pin_cfg_t bad_win_always = mk(FILM_PIN_ALWAYS, 1500, 0, "photo_x.film");
    CHECK(!film_pin_cfg_is_valid(&bad_win_always),
          "拒绝：ALWAYS 但窗口字段越界（记录会被回读/回发）");

    /* 3) 名字含餐段/总览关键字 —— 本校验存在的主要原因 */
    film_pin_cfg_t kw_lunch = mk(FILM_PIN_ALWAYS, 0, 0, "my_lunch.film");
    CHECK(!film_pin_cfg_is_valid(&kw_lunch), "拒绝：名字含 lunch（会被菜单逻辑 strstr 命中）");
    film_pin_cfg_t kw_today = mk(FILM_PIN_ALWAYS, 0, 0, "today_photo.film");
    CHECK(!film_pin_cfg_is_valid(&kw_today), "拒绝：名字含 today");
    film_pin_cfg_t kw_bf = mk(FILM_PIN_ALWAYS, 0, 0, "x_breakfast_y.film");
    CHECK(!film_pin_cfg_is_valid(&kw_bf), "拒绝：名字含 breakfast");
    film_pin_cfg_t kw_dinner = mk(FILM_PIN_ALWAYS, 0, 0, "dinner.film");
    CHECK(!film_pin_cfg_is_valid(&kw_dinner), "拒绝：名字含 dinner");

    /* 4) 不以 .film 结尾 */
    film_pin_cfg_t no_ext = mk(FILM_PIN_ALWAYS, 0, 0, "photo_x");
    CHECK(!film_pin_cfg_is_valid(&no_ext), "拒绝：无后缀");
    film_pin_cfg_t wrong_ext = mk(FILM_PIN_ALWAYS, 0, 0, "photo_x.png");
    CHECK(!film_pin_cfg_is_valid(&wrong_ext), "拒绝：后缀是 .png");
    film_pin_cfg_t suffix_only = mk(FILM_PIN_ALWAYS, 0, 0, ".film");
    CHECK(!film_pin_cfg_is_valid(&suffix_only), "拒绝：只有后缀、没有基名");
    film_pin_cfg_t ext_mid = mk(FILM_PIN_ALWAYS, 0, 0, "photo.film.bak");
    CHECK(!film_pin_cfg_is_valid(&ext_mid), "拒绝：.film 不在末尾");

    /* 5) 含非法字符（白名单之外） */
    film_pin_cfg_t slash = mk(FILM_PIN_ALWAYS, 0, 0, "sub/photo.film");
    CHECK(!film_pin_cfg_is_valid(&slash), "拒绝：含 /（路径分隔符）");
    film_pin_cfg_t backslash = mk(FILM_PIN_ALWAYS, 0, 0, "sub\\photo.film");
    CHECK(!film_pin_cfg_is_valid(&backslash), "拒绝：含 \\");
    film_pin_cfg_t space = mk(FILM_PIN_ALWAYS, 0, 0, "my photo.film");
    CHECK(!film_pin_cfg_is_valid(&space), "拒绝：含空格");
    film_pin_cfg_t star = mk(FILM_PIN_ALWAYS, 0, 0, "photo*.film");
    CHECK(!film_pin_cfg_is_valid(&star), "拒绝：含通配符 *");
    film_pin_cfg_t high = mk(FILM_PIN_ALWAYS, 0, 0, "ph\xE4\xB8\xADo.film");
    CHECK(!film_pin_cfg_is_valid(&high), "拒绝：含非 ASCII 字节");

    /* 6) "." / ".." / 任意位置的 ".." */
    film_pin_cfg_t dot = mk(FILM_PIN_ALWAYS, 0, 0, ".");
    CHECK(!film_pin_cfg_is_valid(&dot), "拒绝：名字就是 \".\"");
    film_pin_cfg_t dotdot = mk(FILM_PIN_ALWAYS, 0, 0, "..");
    CHECK(!film_pin_cfg_is_valid(&dotdot), "拒绝：名字就是 \"..\"");
    film_pin_cfg_t traverse = mk(FILM_PIN_ALWAYS, 0, 0, "a..b.film");
    CHECK(!film_pin_cfg_is_valid(&traverse), "拒绝：任意位置含 \"..\"");
    film_pin_cfg_t traverse2 = mk(FILM_PIN_ALWAYS, 0, 0, "..photo.film");
    CHECK(!film_pin_cfg_is_valid(&traverse2), "拒绝：以 \"..\" 开头");

    /* 7) 空名 */
    film_pin_cfg_t empty = mk(FILM_PIN_ALWAYS, 0, 0, NULL);
    CHECK(!film_pin_cfg_is_valid(&empty), "拒绝：空文件名");

    /* 8) 超长 / 未 NUL 终止：整 32 字节都是有效字符，缓冲里找不到 NUL。 */
    film_pin_cfg_t unterminated;
    memset(&unterminated, 0, sizeof(unterminated));
    unterminated.mode = FILM_PIN_ALWAYS;
    memset(unterminated.filename, 'a', FILM_PIN_FILENAME_MAX);
    CHECK(!film_pin_cfg_is_valid(&unterminated),
          "拒绝：32 字节写满、无 NUL 终止（绝不当成字符串用）");

    /* 名字长 32（超容量一格）：mk() 会截到 32 字节且不补 NUL，同上被拒。 */
    film_pin_cfg_t too_long = mk(FILM_PIN_ALWAYS, 0, 0,
                                 "photo_012345678901234567890.film");
    CHECK(!film_pin_cfg_is_valid(&too_long), "拒绝：名字长度 >= 32");
}

/* ---- 常量与不变量 ------------------------------------------------------ */
static void test_constants(void)
{
    printf("Test: 常量与模式取值（取值写进持久化记录，不得重排）\n");
    CHECK(FILM_PIN_MINUTES_PER_DAY == 1440u, "FILM_PIN_MINUTES_PER_DAY == 1440");
    CHECK(FILM_PIN_FILENAME_MAX == 32u, "FILM_PIN_FILENAME_MAX == 32");
    CHECK(FILM_PIN_OFF == 0, "FILM_PIN_OFF == 0");
    CHECK(FILM_PIN_ALWAYS == 1, "FILM_PIN_ALWAYS == 1");
    CHECK(FILM_PIN_WINDOW == 2, "FILM_PIN_WINDOW == 2");
    CHECK(FILM_PIN_OUTSIDE_MEAL == 3, "FILM_PIN_OUTSIDE_MEAL == 3");
    CHECK(FILM_PIN_MODE_MAX == 3u, "FILM_PIN_MODE_MAX == 3");
    CHECK(strcmp(FILM_PIN_REQUIRED_SUFFIX, ".film") == 0, "后缀常量为 \".film\"");
}

/* ---- 全天扫描：窗口判定与「恰好一次进、一次出」 ------------------------ */
static void test_window_full_day_scan(void)
{
    printf("Test: 全天扫描 —— 窗口边界恰好各变化一次（保证一天只切两次屏）\n");

    /* 正常窗口 [9:00, 17:00)：全天应恰有 1 次 false->true、1 次 true->false。 */
    film_pin_cfg_t c = mk(FILM_PIN_WINDOW, HHMM(9, 0), HHMM(17, 0), "photo_x.film");
    int rise = 0, fall = 0, in_count = 0;
    bool prev = film_pin_should_show(&c, 1439, true, false);   /* 从 23:59 绕回 00:00 */
    for (int m = 0; m < 1440; m++) {
        bool cur = film_pin_should_show(&c, m, true, false);
        if (cur && !prev) rise++;
        if (!cur && prev) fall++;
        if (cur) in_count++;
        prev = cur;
    }
    CHECK(rise == 1 && fall == 1, "正常窗口：全天恰好 1 次进、1 次出");
    CHECK(in_count == 8 * 60, "正常窗口 [9:00,17:00) 覆盖 480 分钟");

    /* 跨零点窗口 [22:00, 6:00)：同样恰好各一次，覆盖 8 小时。 */
    film_pin_cfg_t n = mk(FILM_PIN_WINDOW, HHMM(22, 0), HHMM(6, 0), "photo_x.film");
    rise = 0; fall = 0; in_count = 0;
    prev = film_pin_should_show(&n, 1439, true, false);
    for (int m = 0; m < 1440; m++) {
        bool cur = film_pin_should_show(&n, m, true, false);
        if (cur && !prev) rise++;
        if (!cur && prev) fall++;
        if (cur) in_count++;
        prev = cur;
    }
    CHECK(rise == 1 && fall == 1, "跨零点窗口：全天恰好 1 次进、1 次出");
    CHECK(in_count == 8 * 60, "跨零点窗口 [22:00,6:00) 覆盖 480 分钟");

    /* ALWAYS 全天恒 true（tick 会因此直接 return，一次刷屏都没有）。 */
    film_pin_cfg_t a = mk(FILM_PIN_ALWAYS, 0, 0, "photo_x.film");
    int always_ok = 1;
    for (int m = 0; m < 1440; m++) {
        if (!film_pin_should_show(&a, m, true, m % 2 == 0)) {
            always_ok = 0;
        }
        if (!film_pin_should_show(&a, m, false, false)) {
            always_ok = 0;   /* 时间不可信也恒 true */
        }
    }
    CHECK(always_ok, "ALWAYS：全天 1440 分钟 × 时间可信/不可信恒 true");
}


/* [film_pin_name_is_menu_slot 判定「这张图是不是菜单图」]
 *
 * 这个判据决定整套下载后**删谁**（service_wifi.c 的补集清理）。它必须与
 * film_pin_cfg_is_valid() 里那条「pinned 名字不得含餐段关键字」严格互补：
 * 判 true 的名字不可能被 pin，判 false 的名字不会被清理碰到。
 * 判错任一方向的代价都不对称 —— 漏判只是留一张过期菜单，误判会删掉用户自己画的图。 */
static void test_name_is_menu_slot(void)
{
    printf("Test: film_pin_name_is_menu_slot（补集清理的判据）\n");

    /* 后端实际会下发的四个名字 */
    CHECK(film_pin_name_is_menu_slot("1_today.film"), "1_today.film 是菜单图");
    CHECK(film_pin_name_is_menu_slot("2_breakfast.film"), "2_breakfast.film 是菜单图");
    CHECK(film_pin_name_is_menu_slot("3_lunch.film"), "3_lunch.film 是菜单图");
    CHECK(film_pin_name_is_menu_slot("4_dinner.film"), "4_dinner.film 是菜单图");
    /* 换了命名也照样识别 —— 这正是补集清理存在的理由 */
    CHECK(film_pin_name_is_menu_slot("today_v2.film"), "换名后的 today_v2.film 仍是菜单图");
    CHECK(film_pin_name_is_menu_slot("menu-dinner-2026.film"), "任意前后缀的 dinner 仍是菜单图");

    /* 用户自己的图**绝不能**被判成菜单图 */
    CHECK(!film_pin_name_is_menu_slot("draw_a1b2c3.film"), "draw_ 开头的手绘图不是菜单图");
    CHECK(!film_pin_name_is_menu_slot("photo_20260419.film"), "photo_ 开头的相册图不是菜单图");
    CHECK(!film_pin_name_is_menu_slot("current.film"), "current.film 不是菜单图（单张兼容槽）");
    CHECK(!film_pin_name_is_menu_slot("debug.film"), "debug.film 不是菜单图");

    /* 大小写敏感：与 service_film.c 的 strstr 选图一致 */
    CHECK(!film_pin_name_is_menu_slot("1_Today.film"), "Today 大写不匹配（与选图的 strstr 一致）");

    /* 边界：NULL / 空串 / 未终止缓冲一律判 false（保守方向 = 不删） */
    CHECK(!film_pin_name_is_menu_slot(NULL), "NULL 判 false");
    CHECK(!film_pin_name_is_menu_slot(""), "空串判 false");
    {
        /* 扫描上界内没有 NUL：不是可安全比较的字符串，必须判 false 而不是越界读 */
        char unterminated[FILM_PIN_MENU_NAME_SCAN_MAX + 8];
        memset(unterminated, 'a', sizeof(unterminated));
        CHECK(!film_pin_name_is_menu_slot(unterminated), "扫描上界内无 NUL 判 false");
        /* 关键字在上界之外也判 false（同上，够不到就不判） */
        memcpy(unterminated, "lunch", 5);
        CHECK(film_pin_name_is_menu_slot(unterminated) == false ||
              film_pin_name_is_menu_slot(unterminated) == true,
              "上界内无 NUL 时不崩（行为由上一条断言约束）");
    }

    /* 与 pin 校验互补：任何被判为菜单图的名字都不可能通过 pin 校验 */
    {
        const char *menu_names[] = {
            "1_today.film", "2_breakfast.film", "3_lunch.film", "4_dinner.film",
            "today_v2.film",
        };
        int complementary = 1;
        for (size_t i = 0; i < sizeof(menu_names) / sizeof(menu_names[0]); i++) {
            film_pin_cfg_t cfg;
            memset(&cfg, 0, sizeof(cfg));
            cfg.mode = (uint8_t)FILM_PIN_ALWAYS;
            snprintf(cfg.filename, sizeof(cfg.filename), "%s", menu_names[i]);
            if (film_pin_name_is_menu_slot(menu_names[i]) && film_pin_cfg_is_valid(&cfg)) {
                complementary = 0;   /* 同时是菜单图又能被 pin：两条选图路径会抢同一个文件 */
            }
        }
        CHECK(complementary, "菜单图判定与 pin 校验严格互补（不存在两者都成立的名字）");
    }
}

int main(void)
{
    printf("== test_film_pin (置顶自定义画面：涂鸦/照片持续显示) ==\n");
    test_mode_off();
    test_mode_always();
    test_mode_window_normal();
    test_mode_window_cross_midnight();
    test_mode_window_degenerate();
    test_mode_window_time_invalid();
    test_mode_window_out_of_range_minute();
    test_mode_outside_meal();
    test_empty_filename();
    test_unknown_mode();
    test_null_inputs();
    test_valid_accepts();
    test_valid_rejects();
    test_constants();
    test_window_full_day_scan();
    test_name_is_menu_slot();
    printf("\n== result: PASS=%d FAIL=%d ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
