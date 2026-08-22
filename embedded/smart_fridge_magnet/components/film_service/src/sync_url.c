/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/sync_url.c
 * Description: 见 sync_url.h。只依赖标准 C 头，可在宿主机直接编译测试。
 *
 *********************************************************************/
#include "sync_url.h"

#include <string.h>

sync_url_result_t sync_url_build_sibling(char *out, size_t out_sz,
                                         const char *src, const char *suffix)
{
    static const char SUFFIX_MANIFEST[] = "manifest";
    const size_t manifest_len = sizeof(SUFFIX_MANIFEST) - 1u;

    if(out == NULL || out_sz == 0u)
    {
        return SYNC_URL_ERR_ARG;
    }
    out[0] = '\0';

    if(src == NULL || suffix == NULL)
    {
        return SYNC_URL_ERR_ARG;
    }

    size_t src_len = strlen(src);
    /* 必须严格长于后缀本身：源地址恰好等于 "manifest" 时前缀为空，
     * 推出来的是一个没有主机名的相对路径，发不出去也不该被当成成功。 */
    if(src_len <= manifest_len ||
       strcmp(src + (src_len - manifest_len), SUFFIX_MANIFEST) != 0)
    {
        return SYNC_URL_ERR_ARG;
    }

    size_t base_len = src_len - manifest_len;   /* 含结尾 '/' 的前缀 */
    size_t suffix_len = strlen(suffix);
    if((base_len + suffix_len + 1u) > out_sz)
    {
        return SYNC_URL_ERR_TOO_LONG;
    }

    memcpy(out, src, base_len);
    memcpy(out + base_len, suffix, suffix_len + 1u);   /* 含 '\0' */
    return SYNC_URL_OK;
}
