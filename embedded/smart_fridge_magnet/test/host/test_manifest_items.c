/*********************************************************************
 * Host test: manifest items[] 一套多张解析与逐条校验（manifest_parse.c）。
 *
 * Part of the Smart Fridge Magnet project (based on FrameFilm 562f330,
 * GPL-3.0-or-later — see ../../NOTICE.md and LICENSE).
 *
 * Feature: F4「manifest 多张下载、按名落盘、按时段上屏」。
 *
 * 在普通主机上编译【真实的生产源码】manifest_parse.c（只依赖标准 C），断言：
 *
 *   - 4 条正常条目能被完整解析（name / url / size / sha256 各就各位）；
 *   - 缺 items 时 item_count == 0 且五个既有字段仍有效 —— 这条兼容性不能破，
 *     否则新固件连不上还没升级的后端；
 *   - 每一类条目级违规都返回【对应的、可区分的】错误码，而不是静默丢弃那一条：
 *       url 非 https                -> MANIFEST_ERR_ITEM_URL
 *       size != 209120              -> MANIFEST_ERR_ITEM_SIZE
 *       sha256 非 64 位小写 hex      -> MANIFEST_ERR_ITEM_SHA256
 *       name 含 "../"（路径穿越）    -> MANIFEST_ERR_ITEM_NAME
 *       name 不以 .film 结尾         -> MANIFEST_ERR_ITEM_NAME
 *       5 条超过 MANIFEST_MAX_ITEMS  -> MANIFEST_ERR_ITEMS_TOO_MANY
 *     半套图比没有更糟：设备会在某个时段显示一张过期的详情页，人看不出那是旧内容。
 *   - manifest_contains_item_name()：「本次 items 里有没有这个文件名」这一纯查询，
 *     它是 service_wifi.c「不删自己刚下载的文件」那条不变量的全部判断依据
 *     （包含 / 不包含 / NULL / item_count == 0 / 大小写敏感 / item_count 越界）。
 *
 * TEST-ONLY. NOT part of the firmware build.
 *********************************************************************/
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "manifest_parse.h"

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

/* 64 位小写 hex 的合法样例（内容无关，只要格式对）。 */
#define SHA_A "0000000000000000000000000000000000000000000000000000000000000001"
#define SHA_B "00000000000000000000000000000000000000000000000000000000000000b2"
#define SHA_C "00000000000000000000000000000000000000000000000000000000000000c3"
#define SHA_D "00000000000000000000000000000000000000000000000000000000000000d4"

/* 断言 manifest_parse(json) 返回 expect。 */
static void check_err(const char *json, manifest_err_t expect, const char *label)
{
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));   /* 预填异物：失败时不该被写入 */
    manifest_err_t got = manifest_parse(json, strlen(json), &m);
    if (got == expect) {
        printf("  PASS: %s (-> %s)\n", label, manifest_err_str(got));
        g_pass++;
    } else {
        printf("  FAIL: %s got %s expected %s  (%s:%d)\n",
               label, manifest_err_str(got), manifest_err_str(expect),
               __FILE__, __LINE__);
        g_fail++;
    }
}

/* ---- 1) 一套四张正常解析 ---------------------------------------------- */
/* 布局刻意照后端 device_display.get_manifest() 的真实产物：条目里带一个固件不需要的
 * "slot" 键，必须作为未知键被安全跳过（否则后端加字段就会让固件拒收整份 manifest）。 */
static const char JSON_FOUR[] =
    "{\"version\":7,\"force\":false,"
    "\"film_url\":\"https://h.example.com/api/devices/dev001/display/7/1_today.film\","
    "\"size\":209120,\"sha256\":\"" SHA_A "\","
    "\"items\":["
      "{\"slot\":\"today\",\"name\":\"1_today.film\","
       "\"url\":\"https://h.example.com/api/devices/dev001/display/7/1_today.film\","
       "\"size\":209120,\"sha256\":\"" SHA_A "\"},"
      "{\"slot\":\"breakfast\",\"name\":\"2_breakfast.film\","
       "\"url\":\"https://h.example.com/api/devices/dev001/display/7/2_breakfast.film\","
       "\"size\":209120,\"sha256\":\"" SHA_B "\"},"
      "{\"slot\":\"lunch\",\"name\":\"3_lunch.film\","
       "\"url\":\"https://h.example.com/api/devices/dev001/display/7/3_lunch.film\","
       "\"size\":209120,\"sha256\":\"" SHA_C "\"},"
      "{\"slot\":\"dinner\",\"name\":\"4_dinner.film\","
       "\"url\":\"https://h.example.com/api/devices/dev001/display/7/4_dinner.film\","
       "\"size\":209120,\"sha256\":\"" SHA_D "\"}"
    "]}";

static void test_four_items_ok(void)
{
    printf("Test: 一套四张正常解析（含固件不需要的 \"slot\" 键被跳过）\n");
    manifest_t m;
    memset(&m, 0, sizeof(m));
    manifest_err_t err = manifest_parse(JSON_FOUR, strlen(JSON_FOUR), &m);
    CHECK(err == MANIFEST_OK, "MANIFEST_OK");
    if (err != MANIFEST_OK) {
        printf("    (err=%s，跳过后续字段断言)\n", manifest_err_str(err));
        return;
    }

    CHECK(m.version == 7u, "version == 7");
    CHECK(m.force == false, "force == false");
    CHECK(m.size == 209120u, "顶层 size == 209120（旧字段仍生效）");
    CHECK(strcmp(m.sha256_hex, SHA_A) == 0, "顶层 sha256 正确");
    CHECK(m.item_count == 4u, "item_count == 4");

    CHECK(strcmp(m.items[0].name, "1_today.film") == 0,     "items[0].name == 1_today.film");
    CHECK(strcmp(m.items[1].name, "2_breakfast.film") == 0, "items[1].name == 2_breakfast.film");
    CHECK(strcmp(m.items[2].name, "3_lunch.film") == 0,     "items[2].name == 3_lunch.film");
    CHECK(strcmp(m.items[3].name, "4_dinner.film") == 0,    "items[3].name == 4_dinner.film");

    CHECK(strcmp(m.items[0].url,
                 "https://h.example.com/api/devices/dev001/display/7/1_today.film") == 0,
          "items[0].url 完整解析");
    CHECK(strcmp(m.items[3].url,
                 "https://h.example.com/api/devices/dev001/display/7/4_dinner.film") == 0,
          "items[3].url 完整解析");

    CHECK(m.items[0].size == 209120u && m.items[1].size == 209120u &&
          m.items[2].size == 209120u && m.items[3].size == 209120u,
          "四条 size 均为 209120");
    CHECK(strcmp(m.items[0].sha256_hex, SHA_A) == 0 &&
          strcmp(m.items[1].sha256_hex, SHA_B) == 0 &&
          strcmp(m.items[2].sha256_hex, SHA_C) == 0 &&
          strcmp(m.items[3].sha256_hex, SHA_D) == 0,
          "四条 sha256 逐条正确（未串行/未覆盖）");

    /* 顺序必须与 JSON 中一致：下载编排按下标顺序逐条提交，顺序错了会让
     * 「失败中止在第几条」这条日志与实际不符。 */
    CHECK(strcmp(m.items[0].name, "1_today.film") == 0 &&
          strcmp(m.items[2].name, "3_lunch.film") == 0,
          "条目顺序与 JSON 一致");
}

/* ---- 2) 缺 items 的向后兼容 ------------------------------------------- */
static void test_missing_items_compat(void)
{
    printf("Test: 缺 items 时 item_count==0 且五字段仍有效（后端未升级也必须可用）\n");
    static const char json[] =
        "{\"version\":3,\"film_url\":\"https://h.example.com/x.film\","
        "\"size\":209120,\"sha256\":\"" SHA_A "\",\"force\":true}";

    manifest_t m;
    memset(&m, 0, sizeof(m));
    manifest_err_t err = manifest_parse(json, strlen(json), &m);
    CHECK(err == MANIFEST_OK, "无 items 仍返回 MANIFEST_OK");
    CHECK(m.item_count == 0u, "item_count == 0（走单张兼容路径）");
    CHECK(m.version == 3u, "version == 3");
    CHECK(m.force == true, "force == true");
    CHECK(m.size == 209120u, "size == 209120");
    CHECK(strcmp(m.film_url, "https://h.example.com/x.film") == 0, "film_url 正确");
    CHECK(strcmp(m.sha256_hex, SHA_A) == 0, "sha256 正确");

    /* 空数组等价于「没给多张」：也走单张路径，而不是报错。 */
    static const char json_empty[] =
        "{\"version\":3,\"film_url\":\"https://h.example.com/x.film\","
        "\"size\":209120,\"sha256\":\"" SHA_A "\",\"force\":false,\"items\":[]}";
    manifest_t m2;
    memset(&m2, 0, sizeof(m2));
    manifest_err_t e2 = manifest_parse(json_empty, strlen(json_empty), &m2);
    CHECK(e2 == MANIFEST_OK && m2.item_count == 0u,
          "items:[] 视为无多张，item_count == 0");
}

/* ---- 3) 逐类条目级违规 ------------------------------------------------ */

/* 构造一份「顶层合法 + 单条 item」的 manifest，item 的四个字段由参数给出。
 * 这样每个用例只改一处，返回的错误码就唯一对应那一处违规。 */
static void check_one_item(const char *name, const char *url,
                           const char *size_literal, const char *sha,
                           manifest_err_t expect, const char *label)
{
    char json[1024];
    int n = snprintf(json, sizeof(json),
        "{\"version\":1,\"film_url\":\"https://h.example.com/x.film\","
        "\"size\":209120,\"sha256\":\"" SHA_A "\",\"force\":false,"
        "\"items\":[{\"name\":\"%s\",\"url\":\"%s\",\"size\":%s,\"sha256\":\"%s\"}]}",
        name, url, size_literal, sha);
    if (n < 0 || (size_t)n >= sizeof(json)) {
        printf("  FAIL: %s (测试用例自身的 JSON 缓冲不足)  (%s:%d)\n",
               label, __FILE__, __LINE__);
        g_fail++;
        return;
    }
    check_err(json, expect, label);
}

#define GOOD_URL  "https://h.example.com/api/devices/dev001/display/1/1_today.film"

static void test_item_url_violations(void)
{
    printf("Test: 条目 url 违规 -> MANIFEST_ERR_ITEM_URL\n");
    check_one_item("1_today.film", "http://h.example.com/1_today.film", "209120", SHA_A,
                   MANIFEST_ERR_ITEM_URL, "url 是 http（非 https）");
    check_one_item("1_today.film", "ftp://h.example.com/1_today.film", "209120", SHA_A,
                   MANIFEST_ERR_ITEM_URL, "url 是 ftp");
    check_one_item("1_today.film", "https://", "209120", SHA_A,
                   MANIFEST_ERR_ITEM_URL, "url 只有 scheme、无主机");
    check_one_item("1_today.film", "", "209120", SHA_A,
                   MANIFEST_ERR_ITEM_URL, "url 为空串");

    /* 超长 url（> 255）也归为条目 url 错误。填 300 个字符的路径。 */
    char longurl[8 + 8 + 320];
    int off = snprintf(longurl, sizeof(longurl), "https://h.example.com/");
    for (int i = 0; i < 300; i++) {
        longurl[off + i] = 'a';
    }
    longurl[off + 300] = '\0';
    check_one_item("1_today.film", longurl, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_URL, "url 超过 255 字符");
}

static void test_item_size_violations(void)
{
    printf("Test: 条目 size 违规 -> MANIFEST_ERR_ITEM_SIZE\n");
    check_one_item("1_today.film", GOOD_URL, "209119", SHA_A,
                   MANIFEST_ERR_ITEM_SIZE, "size 209119（少一字节）");
    check_one_item("1_today.film", GOOD_URL, "209121", SHA_A,
                   MANIFEST_ERR_ITEM_SIZE, "size 209121（多一字节）");
    check_one_item("1_today.film", GOOD_URL, "0", SHA_A,
                   MANIFEST_ERR_ITEM_SIZE, "size 0");
    check_one_item("1_today.film", GOOD_URL, "-1", SHA_A,
                   MANIFEST_ERR_ITEM_SIZE, "size 为负数");
    check_one_item("1_today.film", GOOD_URL, "4294967296", SHA_A,
                   MANIFEST_ERR_ITEM_SIZE, "size 溢出 uint32");
    check_one_item("1_today.film", GOOD_URL, "\"209120\"", SHA_A,
                   MANIFEST_ERR_ITEM_SIZE, "size 是字符串（类型错）");
}

static void test_item_sha_violations(void)
{
    printf("Test: 条目 sha256 违规 -> MANIFEST_ERR_ITEM_SHA256\n");
    check_one_item("1_today.film", GOOD_URL, "209120",
                   "000000000000000000000000000000000000000000000000000000000000000",
                   MANIFEST_ERR_ITEM_SHA256, "sha256 只有 63 位");
    check_one_item("1_today.film", GOOD_URL, "209120",
                   "00000000000000000000000000000000000000000000000000000000000000012",
                   MANIFEST_ERR_ITEM_SHA256, "sha256 有 65 位");
    check_one_item("1_today.film", GOOD_URL, "209120",
                   "000000000000000000000000000000000000000000000000000000000000000A",
                   MANIFEST_ERR_ITEM_SHA256, "sha256 含大写字母");
    check_one_item("1_today.film", GOOD_URL, "209120",
                   "00000000000000000000000000000000000000000000000000000000000000zz",
                   MANIFEST_ERR_ITEM_SHA256, "sha256 含非 hex 字符");
    check_one_item("1_today.film", GOOD_URL, "209120", "",
                   MANIFEST_ERR_ITEM_SHA256, "sha256 为空串");
}

static void test_item_name_violations(void)
{
    printf("Test: 条目 name 违规 -> MANIFEST_ERR_ITEM_NAME（落盘路径安全）\n");
    /* 路径穿越是最要紧的一类：name 直接被拼成落盘路径。 */
    check_one_item("..\\/..\\/etc\\/passwd.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 含转义的 ../../（路径穿越）");
    check_one_item("sub\\/1_today.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 含目录分隔符 /");
    check_one_item("..", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 是 ..");
    check_one_item(".", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 是 .");
    check_one_item("", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 为空串");

    /* 后缀：落盘目录里只应存在 .film。 */
    check_one_item("1_today.bin", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 不以 .film 结尾（.bin）");
    check_one_item("1_today", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 无后缀");
    check_one_item("1_today.film.bak", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 后缀在中间（.film.bak）");

    /* 字符白名单之外的字符（空格、$、中文）。 */
    check_one_item("1 today.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 含空格");
    check_one_item("1_today$.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 含 $");

    /* 超长（> 31）。"aaaa...".film 共 32 字符即越界。 */
    check_one_item("aaaaaaaaaaaaaaaaaaaaaaaaaaaa.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_ERR_ITEM_NAME, "name 超过 31 字符");

    /* 反面对照：合法 name 必须被接受，证明上面的拒绝不是「一律拒绝」。 */
    check_one_item("1_today.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_OK, "合法 name 1_today.film 被接受（反面对照）");
    check_one_item("A-b_c.9.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_OK, "白名单字符组合 A-b_c.9.film 被接受");
    check_one_item("aaaaaaaaaaaaaaaaaaaaaaaaaa.film", GOOD_URL, "209120", SHA_A,
                   MANIFEST_OK, "恰 31 字符的 name 被接受（上边界）");
}

static void test_item_missing_field(void)
{
    printf("Test: 条目缺字段 / 结构错 -> 对应错误码\n");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"name\":\"1_today.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\"}]}",
              MANIFEST_ERR_ITEM_FIELD, "条目缺 url");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"url\":\"" GOOD_URL "\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\"}]}",
              MANIFEST_ERR_ITEM_FIELD, "条目缺 name");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"name\":\"1_today.film\",\"name\":\"2_x.film\","
              "\"url\":\"" GOOD_URL "\",\"size\":209120,\"sha256\":\"" SHA_A "\"}]}",
              MANIFEST_ERR_ITEM_FIELD, "条目 name 键重复");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,\"items\":{}}",
              MANIFEST_ERR_ITEMS_TYPE, "items 不是数组（是对象）");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,\"items\":[1,2]}",
              MANIFEST_ERR_ITEMS_TYPE, "items 元素不是对象");
}

static void test_items_too_many(void)
{
    printf("Test: 超过 MANIFEST_MAX_ITEMS 条 -> MANIFEST_ERR_ITEMS_TOO_MANY\n");
    /* 5 条全部合法，只是条数超限。必须拒绝整份而不是截断到前 4 条：截断会静默丢掉
     * 后端认为必须存在的画面，设备长期显示一套不完整的图而无人察觉。 */
    char json[2048];
    int off = snprintf(json, sizeof(json),
        "{\"version\":1,\"film_url\":\"https://h.example.com/x.film\","
        "\"size\":209120,\"sha256\":\"" SHA_A "\",\"force\":false,\"items\":[");
    for (int i = 0; i < 5; i++) {
        off += snprintf(json + off, sizeof(json) - (size_t)off,
            "%s{\"name\":\"%d_x.film\",\"url\":\"https://h.example.com/%d.film\","
            "\"size\":209120,\"sha256\":\"" SHA_A "\"}",
            (i == 0) ? "" : ",", i, i);
    }
    off += snprintf(json + off, sizeof(json) - (size_t)off, "]}");
    CHECK(off > 0 && (size_t)off < sizeof(json), "5 条测试 JSON 构造成功");
    check_err(json, MANIFEST_ERR_ITEMS_TOO_MANY, "5 条条目（上限 4）整份拒绝");

    CHECK(MANIFEST_MAX_ITEMS == 4u, "MANIFEST_MAX_ITEMS == 4");
    CHECK(MANIFEST_ITEM_NAME_MAXLEN == 31u, "MANIFEST_ITEM_NAME_MAXLEN == 31");
}

static void test_item_name_predicate(void)
{
    printf("Test: manifest_item_name_is_safe 纯谓词直接驱动\n");
    CHECK(manifest_item_name_is_safe("1_today.film"), "1_today.film 安全");
    CHECK(manifest_item_name_is_safe("4_dinner.film"), "4_dinner.film 安全");
    CHECK(!manifest_item_name_is_safe(NULL), "NULL 不安全");
    CHECK(!manifest_item_name_is_safe(""), "空串不安全");
    CHECK(!manifest_item_name_is_safe(".."), ".. 不安全");
    CHECK(!manifest_item_name_is_safe("../x.film"), "../x.film 不安全");
    CHECK(!manifest_item_name_is_safe("/abs.film"), "/abs.film 不安全（绝对路径）");
    CHECK(!manifest_item_name_is_safe("a\\b.film"), "含反斜杠不安全");
    CHECK(!manifest_item_name_is_safe("x.txt"), "x.txt 不安全（后缀不对）");
}

/* ---- manifest_contains_item_name：「不删自己刚下载的文件」这条不变量的判据 ----
 *
 * service_wifi.c 的 wifi_download_film_set() 在整套成功后要清理旧的单张落盘名
 * current.film。若 items[] 里恰好有一条 name == "current.film"，无条件删除命中的
 * 就是本次刚提交的图：版本号已推进、图没了，下一轮判「已是最新」不再下载，
 * 这次发布永久丢失而两端日志都显示成功。删除条件依赖 service_file_remove()
 * （要 ESP-IDF），主机上只能驱动抽出来的纯查询函数 —— 它是那个条件的全部判断依据。 */
static void test_contains_item_name(void)
{
    printf("Test: manifest_contains_item_name 纯查询（不删自己刚下载的文件）\n");

    manifest_t m;
    memset(&m, 0, sizeof(m));
    manifest_err_t err = manifest_parse(JSON_FOUR, strlen(JSON_FOUR), &m);
    CHECK(err == MANIFEST_OK && m.item_count == 4u, "先解析出一套四张作为被查对象");

    /* 包含：四个名字逐一都能命中，且不受下标顺序影响。 */
    CHECK(manifest_contains_item_name(&m, "1_today.film"),     "命中 items[0] 的名字");
    CHECK(manifest_contains_item_name(&m, "2_breakfast.film"), "命中 items[1] 的名字");
    CHECK(manifest_contains_item_name(&m, "3_lunch.film"),     "命中 items[2] 的名字");
    CHECK(manifest_contains_item_name(&m, "4_dinner.film"),    "命中 items[3] 的名字");

    /* 不包含：一套四张里没有 current.film —— 这正是「可以安全清理遗留文件」的情形。 */
    CHECK(!manifest_contains_item_name(&m, "current.film"),
          "一套四张不含 current.film（可以清理遗留文件）");
    CHECK(!manifest_contains_item_name(&m, "5_supper.film"), "不存在的名字返回 false");
    /* 前缀/子串都不算命中：只有完全相等才是同一个文件。 */
    CHECK(!manifest_contains_item_name(&m, "1_today"), "前缀不算命中（非子串匹配）");
    CHECK(!manifest_contains_item_name(&m, "1_today.film.bak"), "更长的名字不算命中");
    CHECK(!manifest_contains_item_name(&m, ""), "空串不命中");

    /* 大小写敏感：文件名在 FAT 上的大小写行为不该由这个查询函数去猜，它只回答
     * 「这个字符串在不在 items 里」。猜错的方向都有代价 —— 猜「不敏感」会让固件
     * 少删一个真该删的遗留文件（轮播里多一张旧图），猜反了则可能删掉刚下载的图。 */
    CHECK(!manifest_contains_item_name(&m, "1_TODAY.FILM"), "大小写敏感：全大写不命中");
    CHECK(!manifest_contains_item_name(&m, "1_Today.film"), "大小写敏感：首字母大写不命中");

    /* 空指针：调用点在错误路径上也会被走到，不能崩。 */
    CHECK(!manifest_contains_item_name(NULL, "1_today.film"), "m 为 NULL 返回 false");
    CHECK(!manifest_contains_item_name(&m, NULL), "name 为 NULL 返回 false");
    CHECK(!manifest_contains_item_name(NULL, NULL), "两者都为 NULL 返回 false");

    /* item_count == 0（后端没给 items[]，走单张兼容路径）：任何名字都不命中，
     * 于是整套路径根本不会被走到；而万一被走到，也不会误删。
     * 刻意在 items[0] 里留下脏数据：函数必须只看 item_count 而不是扫满数组。 */
    manifest_t empty;
    memset(&empty, 0, sizeof(empty));
    empty.item_count = 0u;
    snprintf(empty.items[0].name, sizeof(empty.items[0].name), "current.film");
    CHECK(!manifest_contains_item_name(&empty, "current.film"),
          "item_count == 0 时不看 items[0] 的残留内容");

    /* 反面对照：只有一条且恰为 current.film —— 这种 manifest 后端不会再产生，
     * 但固件必须自己认得出来。 */
    manifest_t single;
    memset(&single, 0, sizeof(single));
    single.item_count = 1u;
    snprintf(single.items[0].name, sizeof(single.items[0].name), "current.film");
    CHECK(manifest_contains_item_name(&single, "current.film"),
          "单条 current.film 被识别（此时必须跳过遗留清理）");

    /* item_count 越界（手工构造的畸形结构）：不得越界读，只扫前 MANIFEST_MAX_ITEMS 条。 */
    manifest_t overflow;
    memset(&overflow, 0, sizeof(overflow));
    overflow.item_count = 200u;
    snprintf(overflow.items[MANIFEST_MAX_ITEMS - 1u].name,
             sizeof(overflow.items[0].name), "4_dinner.film");
    CHECK(manifest_contains_item_name(&overflow, "4_dinner.film"),
          "item_count 越界时仍只扫前 MANIFEST_MAX_ITEMS 条（不越界读）");
    CHECK(!manifest_contains_item_name(&overflow, "current.film"),
          "item_count 越界且不含目标名时返回 false");
}

/* 一份「整份合法但顶层字段有问题」的对照：确认 items 的加入没有削弱既有校验。 */
static void test_toplevel_still_enforced(void)
{
    printf("Test: 加入 items 后顶层字段的既有校验未被削弱\n");
    check_err("{\"version\":1,\"film_url\":\"http://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"name\":\"1_today.film\",\"url\":\"" GOOD_URL "\","
              "\"size\":209120,\"sha256\":\"" SHA_A "\"}]}",
              MANIFEST_ERR_URL_SCHEME, "顶层 film_url 非 https 仍被拒");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":123,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"name\":\"1_today.film\",\"url\":\"" GOOD_URL "\","
              "\"size\":209120,\"sha256\":\"" SHA_A "\"}]}",
              MANIFEST_ERR_SIZE_MISMATCH, "顶层 size != 209120 仍被拒");
    check_err("{\"version\":1,\"size\":209120,\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"name\":\"1_today.film\",\"url\":\"" GOOD_URL "\","
              "\"size\":209120,\"sha256\":\"" SHA_A "\"}]}",
              MANIFEST_ERR_MISSING_FIELD, "顶层缺 film_url 仍被拒（items 不能替代）");
    check_err("{\"version\":1,\"film_url\":\"https://h/x.film\",\"size\":209120,"
              "\"sha256\":\"" SHA_A "\",\"force\":false,"
              "\"items\":[{\"name\":\"1_today.film\",\"url\":\"" GOOD_URL "\","
              "\"size\":209120,\"sha256\":\"" SHA_A "\"}],"
              "\"items\":[]}",
              MANIFEST_ERR_DUP_FIELD, "items 键重复出现被拒");
}

int main(void)
{
    printf("== test_manifest_items (F4: manifest items[] 一套多张) ==\n");
    test_four_items_ok();
    test_missing_items_compat();
    test_item_url_violations();
    test_item_size_violations();
    test_item_sha_violations();
    test_item_name_violations();
    test_item_missing_field();
    test_items_too_many();
    test_item_name_predicate();
    test_contains_item_name();
    test_toplevel_still_enforced();
    printf("\n== result: PASS=%d FAIL=%d ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
