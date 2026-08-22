/* test_sync_url.c — manifest_url 推导同源兄弟端点地址的边界测试。
 *
 * 这段逻辑做的是 memcpy + 手算长度写进调用方缓冲，边界算错就是缓冲区溢出，
 * 而它会把设备令牌发到推导出来的地址上 —— 推错地址等于把凭据发给未知端点。
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "sync_url.h"

#define MANIFEST "https://api.example.com/api/devices/dev01/display/manifest"
#define APPLIED  "https://api.example.com/api/devices/dev01/display/applied"
#define REFRESH  "https://api.example.com/api/devices/dev01/display/request-refresh"

static int g_checks = 0;

static void check(const char *name, int cond)
{
    g_checks++;
    printf("  %-4s %s\n", cond ? "PASS" : "FAIL", name);
    assert(cond);
}

static void test_replaces_last_segment(void)
{
    char out[128];

    check("manifest -> applied",
          sync_url_build_sibling(out, sizeof(out), MANIFEST, "applied") == SYNC_URL_OK
          && strcmp(out, APPLIED) == 0);

    check("manifest -> request-refresh（比 manifest 长的后缀）",
          sync_url_build_sibling(out, sizeof(out), MANIFEST, "request-refresh") == SYNC_URL_OK
          && strcmp(out, REFRESH) == 0);

    check("后缀比 manifest 短也正确（不留残余字符）",
          sync_url_build_sibling(out, sizeof(out), MANIFEST, "x") == SYNC_URL_OK
          && strcmp(out, "https://api.example.com/api/devices/dev01/display/x") == 0);
}

static void test_rejects_urls_not_ending_in_manifest(void)
{
    char out[128];

    /* 用户可能配了完全不同的路径。猜一个地址出来就等于把设备令牌发给未知端点。 */
    check("末段不是 manifest 时拒绝",
          sync_url_build_sibling(out, sizeof(out),
                                 "https://api.example.com/foo/bar", "applied")
          == SYNC_URL_ERR_ARG && out[0] == '\0');

    check("只是包含 manifest 但不在末尾也拒绝",
          sync_url_build_sibling(out, sizeof(out),
                                 "https://x/manifest/other", "applied")
          == SYNC_URL_ERR_ARG);

    /* 源地址恰好等于 "manifest"：前缀为空，推出来是个没有主机名的相对路径。 */
    check("源地址恰好等于 manifest 时拒绝（前缀为空）",
          sync_url_build_sibling(out, sizeof(out), "manifest", "applied")
          == SYNC_URL_ERR_ARG);

    check("空源地址拒绝",
          sync_url_build_sibling(out, sizeof(out), "", "applied") == SYNC_URL_ERR_ARG);
}

static void test_capacity_boundary(void)
{
    /* APPLIED 长度 + 1 是刚好够的容量；少一个字节就必须报 TOO_LONG 而不是截断。 */
    size_t need = strlen(APPLIED) + 1u;
    char exact[256];
    char tight[256];

    memset(exact, 0x7f, sizeof(exact));
    check("容量刚好够时成功",
          sync_url_build_sibling(exact, need, MANIFEST, "applied") == SYNC_URL_OK
          && strcmp(exact, APPLIED) == 0);

    memset(tight, 0x7f, sizeof(tight));
    check("容量差一个字节时报 TOO_LONG 且不写出半截地址",
          sync_url_build_sibling(tight, need - 1u, MANIFEST, "applied")
          == SYNC_URL_ERR_TOO_LONG && tight[0] == '\0');

    /* 容量差一字节的那次调用绝不能越界写：检查缓冲尾部的哨兵未被动过。 */
    check("失败路径没有越界写（哨兵完好）",
          (unsigned char)tight[need] == 0x7f);
}

static void test_null_and_zero_args(void)
{
    char out[64];

    check("out 为 NULL 时拒绝",
          sync_url_build_sibling(NULL, sizeof(out), MANIFEST, "applied")
          == SYNC_URL_ERR_ARG);
    check("容量为 0 时拒绝",
          sync_url_build_sibling(out, 0, MANIFEST, "applied") == SYNC_URL_ERR_ARG);
    check("src 为 NULL 时拒绝并清空 out",
          sync_url_build_sibling(out, sizeof(out), NULL, "applied")
          == SYNC_URL_ERR_ARG && out[0] == '\0');
    check("suffix 为 NULL 时拒绝",
          sync_url_build_sibling(out, sizeof(out), MANIFEST, NULL)
          == SYNC_URL_ERR_ARG);
}

int main(void)
{
    printf("test_sync_url: manifest_url 推导同源端点地址\n");
    test_replaces_last_segment();
    test_rejects_urls_not_ending_in_manifest();
    test_capacity_boundary();
    test_null_and_zero_args();
    printf("  -> %d checks passed\n", g_checks);
    return 0;
}
