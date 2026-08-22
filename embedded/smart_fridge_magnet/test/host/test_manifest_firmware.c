/* test_manifest_firmware.c — manifest 里可选 firmware 对象的解析与校验。
 *
 * 两条必须守住的性质：
 *   1) 字段缺失时整个 OTA 路径不启动（旧后端必须继续可用）；
 *   2) 字段出现但任一处可疑时**拒绝整份 manifest**，而不是「尽力解析」——
 *      一个描述可疑的固件被刷进去就是砖，而画面刷错只是难看。
 *
 * TEST-ONLY. NOT part of the firmware build.
 */
#include <stdio.h>
#include <string.h>

#include "manifest_parse.h"

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, msg)                                                        \
    do {                                                                        \
        if (cond) { printf("  PASS: %s\n", (msg)); g_pass++; }                  \
        else { printf("  FAIL: %s  (%s:%d)\n", (msg), __FILE__, __LINE__); g_fail++; } \
    } while (0)

#define SHA64 "0000000000000000000000000000000000000000000000000000000000000001"

/* 顶层五个必需字段的合法骨架，后面拼接 firmware 片段。 */
#define BASE "\"version\":7,\"film_url\":\"https://h/f.film\",\"size\":209120," \
             "\"sha256\":\"" SHA64 "\",\"force\":false"

static void check_err(const char *json, manifest_err_t expect, const char *label)
{
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));
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

static void test_firmware_absent_is_compatible(void)
{
    printf("Test: 没有 firmware 字段时照常解析，has_update=false\n");
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));
    const char *json = "{" BASE "}";

    CHECK(manifest_parse(json, strlen(json), &m) == MANIFEST_OK, "解析成功");
    CHECK(m.firmware.has_update == false, "has_update 为 false（不启动 OTA）");
    CHECK(m.version == 7u, "顶层字段不受影响");
}

static void test_firmware_parsed(void)
{
    printf("Test: 合法 firmware 被完整解析\n");
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));
    const char *json = "{" BASE ",\"firmware\":{"
                       "\"version\":\"1.2.0\",\"url\":\"https://h/sfm-1.2.0.bin\","
                       "\"size\":1048576,\"sha256\":\"" SHA64 "\"}}";

    CHECK(manifest_parse(json, strlen(json), &m) == MANIFEST_OK, "解析成功");
    CHECK(m.firmware.has_update == true, "has_update 为 true");
    CHECK(strcmp(m.firmware.version, "1.2.0") == 0, "version 正确");
    CHECK(strcmp(m.firmware.url, "https://h/sfm-1.2.0.bin") == 0, "url 正确");
    CHECK(m.firmware.size == 1048576u, "size 正确");
    CHECK(strcmp(m.firmware.sha256_hex, SHA64) == 0, "sha256 正确");
}

static void test_firmware_unknown_key_is_skipped(void)
{
    printf("Test: firmware 里的未知键被安全跳过（后端可以加字段）\n");
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));
    const char *json = "{" BASE ",\"firmware\":{"
                       "\"version\":\"1.2.0\",\"url\":\"https://h/a.bin\","
                       "\"size\":1024,\"sha256\":\"" SHA64 "\","
                       "\"notes\":\"whatever\",\"channel\":{\"a\":[1,2]}}}";

    CHECK(manifest_parse(json, strlen(json), &m) == MANIFEST_OK, "解析成功");
    CHECK(m.firmware.has_update == true, "已知字段仍生效");
}

static void test_url_violations(void)
{
    printf("Test: url 违规 -> MANIFEST_ERR_FW_URL\n");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"http://h/a.bin\",\"size\":1024,\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_FW_URL, "http 被拒（固件只接受 https）");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"\",\"size\":1024,\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_FW_URL, "空 url 被拒");
}

static void test_size_violations(void)
{
    printf("Test: size 违规 -> MANIFEST_ERR_FW_SIZE\n");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":0,\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_FW_SIZE, "size 为 0 被拒");

    /* 超过 app 分区容量：下载到最后一刻才失败，白耗一次射频与电量。 */
    char json[512];
    snprintf(json, sizeof(json),
             "{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
             "\"url\":\"https://h/a.bin\",\"size\":%u,\"sha256\":\"" SHA64 "\"}}",
             (unsigned)(MANIFEST_FW_MAX_BYTES + 1u));
    check_err(json, MANIFEST_ERR_FW_SIZE, "超过 app 分区容量被拒");

    snprintf(json, sizeof(json),
             "{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
             "\"url\":\"https://h/a.bin\",\"size\":%u,\"sha256\":\"" SHA64 "\"}}",
             (unsigned)MANIFEST_FW_MAX_BYTES);
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));
    CHECK(manifest_parse(json, strlen(json), &m) == MANIFEST_OK,
          "恰好等于容量上限时接受（边界含等号）");

    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":\"1024\",\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_FW_SIZE, "size 是字符串被拒（类型错归一到 FW_SIZE）");
}

static void test_sha_violations(void)
{
    printf("Test: sha256 违规 -> MANIFEST_ERR_FW_SHA256\n");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":1024,\"sha256\":\"abc\"}}",
              MANIFEST_ERR_FW_SHA256, "长度不足 64 被拒");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":1024,"
              "\"sha256\":\"000000000000000000000000000000000000000000000000000000000000000A\"}}",
              MANIFEST_ERR_FW_SHA256, "大写十六进制被拒");
}

static void test_version_violations(void)
{
    printf("Test: version 违规 -> MANIFEST_ERR_FW_VERSION\n");
    check_err("{" BASE ",\"firmware\":{\"version\":\"\","
              "\"url\":\"https://h/a.bin\",\"size\":1024,\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_FW_VERSION, "空版本串被拒");

    /* 超长版本串不截断：截断后的版本串会在日志里冒充另一个版本。 */
    char longver[MANIFEST_FW_VERSION_MAXLEN + 8];
    memset(longver, '1', sizeof(longver) - 1);
    longver[sizeof(longver) - 1] = '\0';
    char json[640];
    snprintf(json, sizeof(json),
             "{" BASE ",\"firmware\":{\"version\":\"%s\","
             "\"url\":\"https://h/a.bin\",\"size\":1024,\"sha256\":\"" SHA64 "\"}}",
             longver);
    check_err(json, MANIFEST_ERR_FW_VERSION, "超长版本串被拒（不截断）");
}

static void test_missing_and_duplicate_fields(void)
{
    printf("Test: 缺字段 / 重复字段 / 类型错\n");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":1024}}",
              MANIFEST_ERR_FW_FIELD, "缺 sha256 被拒");
    check_err("{" BASE ",\"firmware\":{}}",
              MANIFEST_ERR_FW_FIELD, "空对象被拒");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\",\"version\":\"2.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":1024,\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_FW_FIELD, "重复 version 被拒");
    check_err("{" BASE ",\"firmware\":[1,2,3]}",
              MANIFEST_ERR_FW_TYPE, "firmware 不是对象被拒");
    check_err("{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
              "\"url\":\"https://h/a.bin\",\"size\":1024,\"sha256\":\"" SHA64 "\"},"
              "\"firmware\":{\"version\":\"2.0.0\","
              "\"url\":\"https://h/b.bin\",\"size\":1024,\"sha256\":\"" SHA64 "\"}}",
              MANIFEST_ERR_DUP_FIELD, "顶层重复 firmware 被拒");
}

static void test_bad_firmware_rejects_whole_manifest(void)
{
    printf("Test: firmware 可疑时拒绝整份 manifest（不退化成只用画面）\n");
    manifest_t m;
    memset(&m, 0xAA, sizeof(m));
    /* 画面部分完全合法，只有 firmware.url 是 http。 */
    const char *json = "{" BASE ",\"firmware\":{\"version\":\"1.0.0\","
                       "\"url\":\"http://h/a.bin\",\"size\":1024,"
                       "\"sha256\":\"" SHA64 "\"}}";

    CHECK(manifest_parse(json, strlen(json), &m) == MANIFEST_ERR_FW_URL,
          "整份被拒而不是忽略 firmware 继续用画面");
}

int main(void)
{
    printf("== test_manifest_firmware (Wi-Fi OTA: manifest.firmware) ==\n");
    test_firmware_absent_is_compatible();
    test_firmware_parsed();
    test_firmware_unknown_key_is_skipped();
    test_url_violations();
    test_size_violations();
    test_sha_violations();
    test_version_violations();
    test_missing_and_duplicate_fields();
    test_bad_firmware_rejects_whole_manifest();
    printf("\n== result: PASS=%d FAIL=%d ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
