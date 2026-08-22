/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/manifest_parse.c
 * Description: 云端 manifest 解析器实现 —— 依赖精简（仅标准 C）的严格 JSON
 *              读取器，只识别本契约所需结构并强制大小/长度限制。
 *              见 manifest_parse.h 顶部「JSON 后端选择」说明。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "manifest_parse.h"

#include <string.h>

/*********************************************************************
 * MACROS
 */
/* 已知字段的 seen 位掩码（用于缺失/重复检测）。 */
#define MP_F_VERSION   (1u << 0)
#define MP_F_FILM_URL  (1u << 1)
#define MP_F_SIZE      (1u << 2)
#define MP_F_SHA256    (1u << 3)
#define MP_F_FORCE     (1u << 4)
/* items 是【可选】字段，故不进 MP_F_ALL 的齐备性掩码 —— 只用来检测重复出现。
 * 缺失时 item_count 保持 0，调用方走单张兼容路径（未升级的后端必须继续可用）。 */
#define MP_F_ITEMS     (1u << 5)
/* firmware 同为【可选】字段，理由同上：后端可能还没有固件托管能力。 */
#define MP_F_FIRMWARE  (1u << 6)
#define MP_F_ALL       (MP_F_VERSION | MP_F_FILM_URL | MP_F_SIZE | MP_F_SHA256 | MP_F_FORCE)

/* 键缓冲：容纳所有已知键名（最长 "film_url" = 8）+ NUL，并给出富余。
 * 键更长者必非已知键 → 归为未知键并跳过其值。 */
#define MP_KEY_BUFSZ   (24u)

/*********************************************************************
 * TYPEDEFS
 */
/* 单遍解析游标：[p, end) 为剩余输入；err 记录首个错误原因。 */
typedef struct {
    const char    *p;
    const char    *end;
    manifest_err_t err;
} mp_cursor_t;

/* 解析上下文：累积字段值 + 已见掩码 + 字符串长度/截断标志。 */
typedef struct {
    manifest_t m;             /* 累积字段值（成功后拷贝给调用方）        */
    uint32_t   seen_mask;     /* 已见的已知字段位掩码                    */
    size_t     url_len;       /* film_url 解码后完整长度                 */
    size_t     sha_len;       /* sha256 解码后完整长度                   */
    bool       url_truncated; /* film_url 解码长度 > 255                 */
    bool       sha_truncated; /* sha256 解码长度 > 64                    */
} mp_ctx_t;

/*********************************************************************
 * LOCAL FUNCTIONS
 */

/* 返回当前字符（0..255），到达末尾返回 -1。 */
static int mp_peek(const mp_cursor_t *c)
{
    return (c->p < c->end) ? (int)(unsigned char)*c->p : -1;
}

/* 跳过 JSON 空白（空格/制表/换行/回车）。 */
static void mp_skip_ws(mp_cursor_t *c)
{
    while (c->p < c->end) {
        char ch = *c->p;
        if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') {
            c->p++;
        } else {
            break;
        }
    }
}

/* 十六进制字符 → 值；非法返回 -1。 */
static int mp_hexval(char ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

/* 精确匹配一个字面量（如 "true"）并推进游标；不足或不匹配返回 false（不设 err）。 */
static bool mp_match(mp_cursor_t *c, const char *lit)
{
    size_t n = strlen(lit);
    if ((size_t)(c->end - c->p) < n) {
        return false;
    }
    if (memcmp(c->p, lit, n) != 0) {
        return false;
    }
    c->p += n;
    return true;
}

/* 向输出缓冲追加一个解码字节：越界只计数不写，最终由调用方据完整计数判断截断。 */
static void mp_emit(char *out, size_t out_cap, size_t *idx, char ch)
{
    if (out != NULL && out_cap > 0 && *idx < out_cap - 1u) {
        out[*idx] = ch;
    }
    (*idx)++;
}

/*
 * 解析一个 JSON 字符串（游标须停在开引号 '"' 上）。解码标准转义
 * (\" \\ \/ \b \f \n \r \t \uXXXX) 到 @p out（可为 NULL 表示仅跳过/计数）。
 * *out_len 返回解码后完整长度（可能 > out_cap-1，用于判定超长）。
 * 非法转义/未终止/裸控制字符 → 设 err 并返回 false。
 */
static bool mp_parse_string(mp_cursor_t *c, char *out, size_t out_cap, size_t *out_len)
{
    if (mp_peek(c) != '"') {
        c->err = MANIFEST_ERR_TYPE;
        return false;
    }
    c->p++; /* 消费开引号 */

    size_t idx = 0;
    for (;;) {
        if (c->p >= c->end) {
            c->err = MANIFEST_ERR_JSON; /* 未终止字符串 */
            return false;
        }
        unsigned char ch = (unsigned char)*c->p++;
        if (ch == '"') {
            break; /* 闭引号 */
        }
        if (ch == '\\') {
            if (c->p >= c->end) {
                c->err = MANIFEST_ERR_JSON;
                return false;
            }
            unsigned char e = (unsigned char)*c->p++;
            switch (e) {
                case '"':  mp_emit(out, out_cap, &idx, '"');  break;
                case '\\': mp_emit(out, out_cap, &idx, '\\'); break;
                case '/':  mp_emit(out, out_cap, &idx, '/');  break;
                case 'b':  mp_emit(out, out_cap, &idx, '\b'); break;
                case 'f':  mp_emit(out, out_cap, &idx, '\f'); break;
                case 'n':  mp_emit(out, out_cap, &idx, '\n'); break;
                case 'r':  mp_emit(out, out_cap, &idx, '\r'); break;
                case 't':  mp_emit(out, out_cap, &idx, '\t'); break;
                case 'u': {
                    if ((c->end - c->p) < 4) {
                        c->err = MANIFEST_ERR_JSON;
                        return false;
                    }
                    uint32_t cp = 0;
                    for (int k = 0; k < 4; k++) {
                        int hv = mp_hexval(c->p[k]);
                        if (hv < 0) {
                            c->err = MANIFEST_ERR_JSON;
                            return false;
                        }
                        cp = (cp << 4) | (uint32_t)hv;
                    }
                    c->p += 4;
                    if (cp >= 0xD800u && cp <= 0xDFFFu) {
                        /* 代理对不支持（本契约中不应出现），视为非法。 */
                        c->err = MANIFEST_ERR_JSON;
                        return false;
                    }
                    /* 编码为 UTF-8（BMP 内）。 */
                    if (cp < 0x80u) {
                        mp_emit(out, out_cap, &idx, (char)cp);
                    } else if (cp < 0x800u) {
                        mp_emit(out, out_cap, &idx, (char)(0xC0u | (cp >> 6)));
                        mp_emit(out, out_cap, &idx, (char)(0x80u | (cp & 0x3Fu)));
                    } else {
                        mp_emit(out, out_cap, &idx, (char)(0xE0u | (cp >> 12)));
                        mp_emit(out, out_cap, &idx, (char)(0x80u | ((cp >> 6) & 0x3Fu)));
                        mp_emit(out, out_cap, &idx, (char)(0x80u | (cp & 0x3Fu)));
                    }
                    break;
                }
                default:
                    c->err = MANIFEST_ERR_JSON; /* 未知转义 */
                    return false;
            }
        } else if (ch < 0x20u) {
            c->err = MANIFEST_ERR_JSON; /* JSON 字符串中不允许裸控制字符 */
            return false;
        } else {
            mp_emit(out, out_cap, &idx, (char)ch);
        }
    }

    if (out != NULL && out_cap > 0) {
        size_t term = (idx < out_cap - 1u) ? idx : (out_cap - 1u);
        out[term] = '\0';
    }
    if (out_len != NULL) {
        *out_len = idx;
    }
    return true;
}

/*
 * 扫描（消费）一个 JSON 数字，输出整数部分数字区间与符号/整数标志。
 * 游标须停在 '-' 或数字上。malformed（如 '-' 后无数字、小数点后无数字）→ err。
 */
static bool mp_scan_number(mp_cursor_t *c,
                           const char **digits_start,
                           const char **digits_end,
                           bool *negative,
                           bool *is_integer)
{
    *negative   = false;
    *is_integer = true;

    if (mp_peek(c) == '-') {
        *negative = true;
        c->p++;
    }

    const char *ds = c->p;
    if (!(mp_peek(c) >= '0' && mp_peek(c) <= '9')) {
        c->err = MANIFEST_ERR_JSON; /* '-' 后无数字 */
        return false;
    }
    while (mp_peek(c) >= '0' && mp_peek(c) <= '9') {
        c->p++;
    }
    const char *de = c->p;

    if (mp_peek(c) == '.') {
        *is_integer = false;
        c->p++;
        if (!(mp_peek(c) >= '0' && mp_peek(c) <= '9')) {
            c->err = MANIFEST_ERR_JSON;
            return false;
        }
        while (mp_peek(c) >= '0' && mp_peek(c) <= '9') {
            c->p++;
        }
    }

    if (mp_peek(c) == 'e' || mp_peek(c) == 'E') {
        *is_integer = false;
        c->p++;
        if (mp_peek(c) == '+' || mp_peek(c) == '-') {
            c->p++;
        }
        if (!(mp_peek(c) >= '0' && mp_peek(c) <= '9')) {
            c->err = MANIFEST_ERR_JSON;
            return false;
        }
        while (mp_peek(c) >= '0' && mp_peek(c) <= '9') {
            c->p++;
        }
    }

    *digits_start = ds;
    *digits_end   = de;
    return true;
}

/*
 * 解析一个 uint32 字段值（version/size）。类型不符（非数字起始）→ MANIFEST_ERR_TYPE；
 * 负数/非整数/溢出 uint32 → @p range_err。
 */
static bool mp_parse_number_u32(mp_cursor_t *c, uint32_t *out, manifest_err_t range_err)
{
    int first = mp_peek(c);
    if (first != '-' && !(first >= '0' && first <= '9')) {
        c->err = MANIFEST_ERR_TYPE;
        return false;
    }

    const char *ds = NULL;
    const char *de = NULL;
    bool negative = false;
    bool is_integer = false;
    if (!mp_scan_number(c, &ds, &de, &negative, &is_integer)) {
        return false; /* err 已设 */
    }
    if (negative || !is_integer) {
        c->err = range_err;
        return false;
    }

    uint32_t v = 0;
    for (const char *q = ds; q < de; q++) {
        uint32_t d = (uint32_t)(*q - '0');
        if (v > (UINT32_MAX - d) / 10u) {
            c->err = range_err; /* 溢出 uint32 */
            return false;
        }
        v = v * 10u + d;
    }
    *out = v;
    return true;
}

/* 解析布尔字段值：true/false。类型不符 → MANIFEST_ERR_TYPE；坏字面量 → MANIFEST_ERR_JSON。 */
static bool mp_parse_bool(mp_cursor_t *c, bool *out)
{
    int ch = mp_peek(c);
    if (ch == 't') {
        if (mp_match(c, "true")) { *out = true; return true; }
        c->err = MANIFEST_ERR_JSON;
        return false;
    }
    if (ch == 'f') {
        if (mp_match(c, "false")) { *out = false; return true; }
        c->err = MANIFEST_ERR_JSON;
        return false;
    }
    c->err = MANIFEST_ERR_TYPE;
    return false;
}

/*
 * 安全地跳过一个任意 JSON 值（用于未知键，保持前向兼容）。支持
 * 字符串/数字/true/false/null/对象/数组；对象与数组按 @p depth 递归下界跳过。
 * 越界/深度耗尽/语法错误 → 设 err 返回 false。
 */
static bool mp_skip_value(mp_cursor_t *c, unsigned depth)
{
    mp_skip_ws(c);
    int ch = mp_peek(c);
    if (ch < 0) {
        c->err = MANIFEST_ERR_JSON;
        return false;
    }

    if (ch == '"') {
        return mp_parse_string(c, NULL, 0, NULL);
    }

    if (ch == '{' || ch == '[') {
        if (depth == 0) {
            c->err = MANIFEST_ERR_JSON; /* 嵌套过深 */
            return false;
        }
        char open  = (char)ch;
        char close = (open == '{') ? '}' : ']';
        c->p++; /* 消费开括号 */
        mp_skip_ws(c);
        if (mp_peek(c) == close) {
            c->p++;
            return true; /* 空对象/数组 */
        }
        for (;;) {
            if (open == '{') {
                mp_skip_ws(c);
                if (mp_peek(c) != '"') {
                    c->err = MANIFEST_ERR_JSON;
                    return false;
                }
                if (!mp_parse_string(c, NULL, 0, NULL)) {
                    return false;
                }
                mp_skip_ws(c);
                if (mp_peek(c) != ':') {
                    c->err = MANIFEST_ERR_JSON;
                    return false;
                }
                c->p++;
            }
            if (!mp_skip_value(c, depth - 1u)) {
                return false;
            }
            mp_skip_ws(c);
            int d = mp_peek(c);
            if (d == ',') {
                c->p++;
                continue;
            }
            if (d == close) {
                c->p++;
                return true;
            }
            c->err = MANIFEST_ERR_JSON;
            return false;
        }
    }

    if (ch == '-' || (ch >= '0' && ch <= '9')) {
        const char *ds = NULL;
        const char *de = NULL;
        bool neg = false;
        bool isint = false;
        return mp_scan_number(c, &ds, &de, &neg, &isint);
    }

    if (ch == 't') {
        if (mp_match(c, "true")) return true;
        c->err = MANIFEST_ERR_JSON;
        return false;
    }
    if (ch == 'f') {
        if (mp_match(c, "false")) return true;
        c->err = MANIFEST_ERR_JSON;
        return false;
    }
    if (ch == 'n') {
        if (mp_match(c, "null")) return true;
        c->err = MANIFEST_ERR_JSON;
        return false;
    }

    c->err = MANIFEST_ERR_JSON;
    return false;
}

/* —— F4「一套多张」items[] 的解析 ——
 *
 * 后端产出的条目形如
 *   {"slot":"today","name":"1_today.film","url":"https://...","size":209120,"sha256":"<64hex>"}
 * 其中 "slot" 是后端自用的语义标签，固件不需要（落盘名与显示时段都由 name 决定），
 * 所以按未知键有界跳过 —— 这样后端将来再加字段也不会让固件拒收整份 manifest。
 */
#define MP_I_NAME    (1u << 0)
#define MP_I_URL     (1u << 1)
#define MP_I_SIZE    (1u << 2)
#define MP_I_SHA256  (1u << 3)
#define MP_I_ALL     (MP_I_NAME | MP_I_URL | MP_I_SIZE | MP_I_SHA256)

/*
 * 解析 items[] 中的一个对象。游标须停在 '{' 上。
 * 成功时写入 *out 并返回 true；失败设 c->err 并返回 false（*out 内容视为无效）。
 *
 * 校验强度与顶层的 film_url/size/sha256 完全一致，另加 name 的安全基名规则。
 * 每一项都用独立的错误码，因为「哪一类字段错了」直接决定该改后端哪一段代码，
 * 而这个信息最终要经 last_sync_error 回到 App 和串口日志。
 */
static bool mp_parse_item(mp_cursor_t *c, manifest_item_t *out)
{
    if (mp_peek(c) != '{') {
        c->err = MANIFEST_ERR_ITEMS_TYPE;
        return false;
    }
    c->p++;   /* 消费 '{' */

    memset(out, 0, sizeof(*out));
    uint32_t seen = 0u;
    size_t name_len = 0;
    size_t url_len = 0;
    size_t sha_len = 0;
    bool name_trunc = false;
    bool url_trunc = false;
    bool sha_trunc = false;

    mp_skip_ws(c);
    if (mp_peek(c) == '}') {
        c->p++;   /* 空对象 → 下面的齐备性检查会拒绝 */
    } else {
        for (;;) {
            mp_skip_ws(c);
            if (mp_peek(c) != '"') {
                c->err = MANIFEST_ERR_ITEM_FIELD;
                return false;
            }

            char   key[MP_KEY_BUFSZ];
            size_t klen = 0;
            if (!mp_parse_string(c, key, sizeof(key), &klen)) {
                return false;   /* err 已设（JSON 语法层） */
            }

            mp_skip_ws(c);
            if (mp_peek(c) != ':') {
                c->err = MANIFEST_ERR_ITEM_FIELD;
                return false;
            }
            c->p++;
            mp_skip_ws(c);

            bool known_fit = (klen < sizeof(key));

            if (known_fit && strcmp(key, "name") == 0) {
                if (seen & MP_I_NAME) { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (mp_peek(c) != '"') { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (!mp_parse_string(c, out->name, sizeof(out->name), &name_len)) return false;
                name_trunc = (name_len > MANIFEST_ITEM_NAME_MAXLEN);
                seen |= MP_I_NAME;
            } else if (known_fit && strcmp(key, "url") == 0) {
                if (seen & MP_I_URL) { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (mp_peek(c) != '"') { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (!mp_parse_string(c, out->url, sizeof(out->url), &url_len)) return false;
                url_trunc = (url_len > MANIFEST_FILM_URL_MAXLEN);
                seen |= MP_I_URL;
            } else if (known_fit && strcmp(key, "size") == 0) {
                if (seen & MP_I_SIZE) { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (!mp_parse_number_u32(c, &out->size, MANIFEST_ERR_ITEM_SIZE)) {
                    /* 类型不符时 mp_parse_number_u32 设的是 MANIFEST_ERR_TYPE；
                     * 归一到条目级错误码，让调用方一眼看出是 items[] 里的问题。 */
                    if (c->err == MANIFEST_ERR_TYPE) {
                        c->err = MANIFEST_ERR_ITEM_SIZE;
                    }
                    return false;
                }
                seen |= MP_I_SIZE;
            } else if (known_fit && strcmp(key, "sha256") == 0) {
                if (seen & MP_I_SHA256) { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (mp_peek(c) != '"') { c->err = MANIFEST_ERR_ITEM_FIELD; return false; }
                if (!mp_parse_string(c, out->sha256_hex, sizeof(out->sha256_hex), &sha_len)) return false;
                sha_trunc = (sha_len > MANIFEST_SHA256_HEXLEN);
                seen |= MP_I_SHA256;
            } else {
                /* 未知键（如后端的 "slot"）：安全地有界跳过其值，保持前向兼容。 */
                if (!mp_skip_value(c, MANIFEST_MAX_JSON_DEPTH)) return false;
            }

            mp_skip_ws(c);
            int d = mp_peek(c);
            if (d == ',') {
                c->p++;
                continue;
            }
            if (d == '}') {
                c->p++;
                break;
            }
            c->err = MANIFEST_ERR_ITEM_FIELD;
            return false;
        }
    }

    if ((seen & MP_I_ALL) != MP_I_ALL) {
        c->err = MANIFEST_ERR_ITEM_FIELD;   /* 四个必需字段有缺失 */
        return false;
    }

    /* 固定顺序的语义校验，返回首个失败的可区分原因。 */
    if (name_trunc || !manifest_item_name_is_safe(out->name)) {
        c->err = MANIFEST_ERR_ITEM_NAME;
        return false;
    }
    if (url_trunc || !manifest_url_is_https(out->url)) {
        c->err = MANIFEST_ERR_ITEM_URL;
        return false;
    }
    if (sha_trunc || !manifest_is_lower_hex64(out->sha256_hex)) {
        c->err = MANIFEST_ERR_ITEM_SHA256;
        return false;
    }
    if (!manifest_size_is_standard(out->size)) {
        c->err = MANIFEST_ERR_ITEM_SIZE;
        return false;
    }
    return true;
}

/* —— 可选的 firmware 对象解析（Wi-Fi OTA）——
 *
 * 校验强度对齐 items[] 里的条目：url 必须 https、sha256 必须恰 64 位小写十六进制。
 * size 的判据与画面不同 —— 画面恒为 209120，固件是变长的，只能查「非 0 且不超过
 * app 分区容量」。超容量的固件下载到最后一刻才会失败，白耗一次射频与电量。
 */
#define MP_FW_VERSION (1u << 0)
#define MP_FW_URL     (1u << 1)
#define MP_FW_SIZE    (1u << 2)
#define MP_FW_SHA256  (1u << 3)
#define MP_FW_ALL     (MP_FW_VERSION | MP_FW_URL | MP_FW_SIZE | MP_FW_SHA256)

static bool mp_parse_firmware(mp_cursor_t *c, manifest_firmware_t *out)
{
    if (mp_peek(c) != '{') {
        c->err = MANIFEST_ERR_FW_TYPE;
        return false;
    }
    c->p++;   /* 消费 '{' */

    memset(out, 0, sizeof(*out));
    uint32_t seen = 0u;
    size_t ver_len = 0, url_len = 0, sha_len = 0;
    bool ver_trunc = false, url_trunc = false, sha_trunc = false;

    mp_skip_ws(c);
    if (mp_peek(c) == '}') {
        c->p++;   /* 空对象 → 下面的齐备性检查会拒绝 */
    } else {
        for (;;) {
            mp_skip_ws(c);
            if (mp_peek(c) != '"') {
                c->err = MANIFEST_ERR_FW_FIELD;
                return false;
            }

            char   key[MP_KEY_BUFSZ];
            size_t klen = 0;
            if (!mp_parse_string(c, key, sizeof(key), &klen)) {
                return false;
            }

            mp_skip_ws(c);
            if (mp_peek(c) != ':') {
                c->err = MANIFEST_ERR_FW_FIELD;
                return false;
            }
            c->p++;
            mp_skip_ws(c);

            bool known_fit = (klen < sizeof(key));

            if (known_fit && strcmp(key, "version") == 0) {
                if (seen & MP_FW_VERSION) { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (mp_peek(c) != '"') { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (!mp_parse_string(c, out->version, sizeof(out->version), &ver_len)) return false;
                ver_trunc = (ver_len > MANIFEST_FW_VERSION_MAXLEN);
                seen |= MP_FW_VERSION;
            } else if (known_fit && strcmp(key, "url") == 0) {
                if (seen & MP_FW_URL) { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (mp_peek(c) != '"') { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (!mp_parse_string(c, out->url, sizeof(out->url), &url_len)) return false;
                url_trunc = (url_len > MANIFEST_FILM_URL_MAXLEN);
                seen |= MP_FW_URL;
            } else if (known_fit && strcmp(key, "size") == 0) {
                if (seen & MP_FW_SIZE) { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (!mp_parse_number_u32(c, &out->size, MANIFEST_ERR_FW_SIZE)) {
                    if (c->err == MANIFEST_ERR_TYPE) {
                        c->err = MANIFEST_ERR_FW_SIZE;
                    }
                    return false;
                }
                seen |= MP_FW_SIZE;
            } else if (known_fit && strcmp(key, "sha256") == 0) {
                if (seen & MP_FW_SHA256) { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (mp_peek(c) != '"') { c->err = MANIFEST_ERR_FW_FIELD; return false; }
                if (!mp_parse_string(c, out->sha256_hex, sizeof(out->sha256_hex), &sha_len)) return false;
                sha_trunc = (sha_len > MANIFEST_SHA256_HEXLEN);
                seen |= MP_FW_SHA256;
            } else {
                if (!mp_skip_value(c, MANIFEST_MAX_JSON_DEPTH)) return false;
            }

            mp_skip_ws(c);
            int d = mp_peek(c);
            if (d == ',') {
                c->p++;
                continue;
            }
            if (d == '}') {
                c->p++;
                break;
            }
            c->err = MANIFEST_ERR_FW_FIELD;
            return false;
        }
    }

    if ((seen & MP_FW_ALL) != MP_FW_ALL) {
        c->err = MANIFEST_ERR_FW_FIELD;
        return false;
    }

    if (ver_trunc || out->version[0] == '\0') {
        c->err = MANIFEST_ERR_FW_VERSION;
        return false;
    }
    if (url_trunc || !manifest_url_is_https(out->url)) {
        c->err = MANIFEST_ERR_FW_URL;
        return false;
    }
    if (sha_trunc || !manifest_is_lower_hex64(out->sha256_hex)) {
        c->err = MANIFEST_ERR_FW_SHA256;
        return false;
    }
    if (out->size == 0u || out->size > MANIFEST_FW_MAX_BYTES) {
        c->err = MANIFEST_ERR_FW_SIZE;
        return false;
    }

    out->has_update = true;
    return true;
}

/*
 * 解析 items[] 数组。游标须停在 '[' 上。
 * 成功时写满 out_items[0..*out_count-1]；失败设 c->err 返回 false。
 *
 * 超过 MANIFEST_MAX_ITEMS 条即拒绝【整份】manifest 而不是截断到前 4 条：截断会静默
 * 丢掉后端认为必须存在的画面，设备于是长期显示一套不完整的图而无人察觉。
 */
static bool mp_parse_items(mp_cursor_t *c, manifest_item_t *out_items, uint8_t *out_count)
{
    if (mp_peek(c) != '[') {
        c->err = MANIFEST_ERR_ITEMS_TYPE;
        return false;
    }
    c->p++;   /* 消费 '[' */

    uint8_t n = 0;
    mp_skip_ws(c);
    if (mp_peek(c) == ']') {
        c->p++;
        *out_count = 0;   /* 空数组 == 后端没给多张，等价于走单张兼容路径 */
        return true;
    }

    for (;;) {
        mp_skip_ws(c);
        if (n >= (uint8_t)MANIFEST_MAX_ITEMS) {
            c->err = MANIFEST_ERR_ITEMS_TOO_MANY;
            return false;
        }
        if (!mp_parse_item(c, &out_items[n])) {
            return false;
        }
        n++;

        mp_skip_ws(c);
        int d = mp_peek(c);
        if (d == ',') {
            c->p++;
            continue;
        }
        if (d == ']') {
            c->p++;
            break;
        }
        c->err = MANIFEST_ERR_ITEMS_TYPE;
        return false;
    }

    *out_count = n;
    return true;
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool manifest_is_lower_hex64(const char *s)
{
    if (s == NULL) {
        return false;
    }
    for (int i = 0; i < (int)MANIFEST_SHA256_HEXLEN; i++) {
        char ch = s[i];
        bool ok = (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
        if (!ok) {
            return false;
        }
    }
    return s[MANIFEST_SHA256_HEXLEN] == '\0';
}

bool manifest_url_is_https(const char *s)
{
    if (s == NULL) {
        return false;
    }
    static const char pfx[] = "https://";
    const size_t n = sizeof(pfx) - 1u; /* 8 */
    for (size_t i = 0; i < n; i++) {
        char a = s[i];
        if (a == '\0') {
            return false;
        }
        char lo = (a >= 'A' && a <= 'Z') ? (char)(a + 32) : a;
        if (lo != pfx[i]) {
            return false;
        }
    }
    /* 方案之后至少要有一个主机字符。 */
    return s[n] != '\0';
}

bool manifest_size_is_standard(uint32_t size)
{
    return size == MANIFEST_STANDARD_FILM_SIZE;
}

bool manifest_contains_item_name(const manifest_t *m, const char *name)
{
    if (m == NULL || name == NULL) {
        return false;
    }

    /* 防御性上界：解析器已保证 item_count <= MANIFEST_MAX_ITEMS，但本函数也会被
     * 调用方用手工构造的 manifest_t 驱动（含 host 测试），越界读不能靠约定避免。 */
    uint8_t count = m->item_count;
    if (count > (uint8_t)MANIFEST_MAX_ITEMS) {
        count = (uint8_t)MANIFEST_MAX_ITEMS;
    }

    for (uint8_t i = 0; i < count; i++) {
        /* items[].name 由解析器写入且必定 NUL 结尾（mp_parse_string 保证），
         * 故 strcmp 是有界的。大小写敏感 —— 见头文件说明。 */
        if (strcmp(m->items[i].name, name) == 0) {
            return true;
        }
    }
    return false;
}

bool manifest_item_name_is_safe(const char *s)
{
    if (s == NULL) {
        return false;
    }

    /* 白名单字符集 + 长度上界。用白名单而非黑名单：路径穿越的变体太多（'/'、'\\'、
     * URL 编码、UTF-8 同形字），逐一拉黑必定漏一种。 */
    size_t n = 0;
    for (; s[n] != '\0'; n++) {
        if (n >= (size_t)MANIFEST_ITEM_NAME_MAXLEN) {
            return false;   /* 超长（也覆盖了「没有结尾符」的畸形输入） */
        }
        char ch = s[n];
        bool ok = (ch >= '0' && ch <= '9') ||
                  (ch >= 'A' && ch <= 'Z') ||
                  (ch >= 'a' && ch <= 'z') ||
                  ch == '.' || ch == '_' || ch == '-';
        if (!ok) {
            return false;
        }
    }
    if (n == 0) {
        return false;   /* 空名 */
    }

    /* "." / ".." 能通过上面的字符集，但语义上是目录，必须单独排除。 */
    if (strcmp(s, ".") == 0 || strcmp(s, "..") == 0) {
        return false;
    }

    /* 必须以 ".film" 结尾。落盘目录里只应存在 .film —— 别的后缀要么是后端出错，
     * 要么是有人在试探写入路径。 */
    static const char suffix[] = ".film";
    const size_t slen = sizeof(suffix) - 1u;   /* 5 */
    if (n < slen) {
        return false;
    }
    return memcmp(s + (n - slen), suffix, slen) == 0;
}

manifest_err_t manifest_parse(const char *json, size_t json_len, manifest_t *out)
{
    if (json == NULL || out == NULL) {
        return MANIFEST_ERR_NULL;
    }
    if (json_len == 0) {
        return MANIFEST_ERR_EMPTY;
    }
    if (json_len > MANIFEST_MAX_RESPONSE_BYTES) {
        return MANIFEST_ERR_TOO_LARGE; /* Req 10.1：拒绝超大响应，先于任何解析 */
    }

    mp_cursor_t c;
    c.p   = json;
    c.end = json + json_len;
    c.err = MANIFEST_OK;

    mp_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    mp_skip_ws(&c);
    if (mp_peek(&c) != '{') {
        return MANIFEST_ERR_JSON; /* 顶层必须是对象 */
    }
    c.p++;

    mp_skip_ws(&c);
    if (mp_peek(&c) == '}') {
        c.p++; /* 空对象 → 后续缺失字段检测会拒绝 */
    } else {
        for (;;) {
            mp_skip_ws(&c);
            if (mp_peek(&c) != '"') {
                return MANIFEST_ERR_JSON;
            }

            char   key[MP_KEY_BUFSZ];
            size_t klen = 0;
            if (!mp_parse_string(&c, key, sizeof(key), &klen)) {
                return c.err;
            }

            mp_skip_ws(&c);
            if (mp_peek(&c) != ':') {
                return MANIFEST_ERR_JSON;
            }
            c.p++;
            mp_skip_ws(&c);

            bool known_fit = (klen < sizeof(key)); /* 键完整装入缓冲才可能是已知键 */

            if (known_fit && strcmp(key, "version") == 0) {
                if (ctx.seen_mask & MP_F_VERSION) return MANIFEST_ERR_DUP_FIELD;
                if (!mp_parse_number_u32(&c, &ctx.m.version, MANIFEST_ERR_VERSION_RANGE)) return c.err;
                ctx.seen_mask |= MP_F_VERSION;
            } else if (known_fit && strcmp(key, "film_url") == 0) {
                if (ctx.seen_mask & MP_F_FILM_URL) return MANIFEST_ERR_DUP_FIELD;
                if (mp_peek(&c) != '"') return MANIFEST_ERR_TYPE;
                if (!mp_parse_string(&c, ctx.m.film_url, sizeof(ctx.m.film_url), &ctx.url_len)) return c.err;
                ctx.url_truncated = (ctx.url_len > MANIFEST_FILM_URL_MAXLEN);
                ctx.seen_mask |= MP_F_FILM_URL;
            } else if (known_fit && strcmp(key, "size") == 0) {
                if (ctx.seen_mask & MP_F_SIZE) return MANIFEST_ERR_DUP_FIELD;
                if (!mp_parse_number_u32(&c, &ctx.m.size, MANIFEST_ERR_SIZE_RANGE)) return c.err;
                ctx.seen_mask |= MP_F_SIZE;
            } else if (known_fit && strcmp(key, "sha256") == 0) {
                if (ctx.seen_mask & MP_F_SHA256) return MANIFEST_ERR_DUP_FIELD;
                if (mp_peek(&c) != '"') return MANIFEST_ERR_TYPE;
                if (!mp_parse_string(&c, ctx.m.sha256_hex, sizeof(ctx.m.sha256_hex), &ctx.sha_len)) return c.err;
                ctx.sha_truncated = (ctx.sha_len > MANIFEST_SHA256_HEXLEN);
                ctx.seen_mask |= MP_F_SHA256;
            } else if (known_fit && strcmp(key, "force") == 0) {
                if (ctx.seen_mask & MP_F_FORCE) return MANIFEST_ERR_DUP_FIELD;
                if (!mp_parse_bool(&c, &ctx.m.force)) return c.err;
                ctx.seen_mask |= MP_F_FORCE;
            } else if (known_fit && strcmp(key, "firmware") == 0) {
                /* Wi-Fi OTA 的可选字段。与 items[] 同一条兼容规则：不出现时
                 * has_update 保持 false；出现了就必须完全合法，否则拒绝整份 manifest
                 * —— 一个字段可疑的固件描述不该被「尽力解析」，那会把设备刷成砖。 */
                if (ctx.seen_mask & MP_F_FIRMWARE) return MANIFEST_ERR_DUP_FIELD;
                if (!mp_parse_firmware(&c, &ctx.m.firmware)) return c.err;
                ctx.seen_mask |= MP_F_FIRMWARE;
            } else if (known_fit && strcmp(key, "items") == 0) {
                /* F4「一套多张」。可选字段：不出现时 item_count 保持 0。
                 * 出现了就必须完全合法 —— 任一条目有问题即拒绝整份 manifest。 */
                if (ctx.seen_mask & MP_F_ITEMS) return MANIFEST_ERR_DUP_FIELD;
                if (!mp_parse_items(&c, ctx.m.items, &ctx.m.item_count)) return c.err;
                ctx.seen_mask |= MP_F_ITEMS;
            } else {
                /* 未知键：安全地有界跳过其值，保持前向兼容。 */
                if (!mp_skip_value(&c, MANIFEST_MAX_JSON_DEPTH)) return c.err;
            }

            mp_skip_ws(&c);
            int d = mp_peek(&c);
            if (d == ',') {
                c.p++;
                continue;
            }
            if (d == '}') {
                c.p++;
                break;
            }
            return MANIFEST_ERR_JSON;
        }
    }

    /* 顶层对象之后只允许空白，拒绝尾部多余内容。 */
    mp_skip_ws(&c);
    if (c.p != c.end) {
        return MANIFEST_ERR_JSON;
    }

    /* 必需字段齐备性。 */
    if ((ctx.seen_mask & MP_F_ALL) != MP_F_ALL) {
        return MANIFEST_ERR_MISSING_FIELD;
    }

    /* 语义/限制校验（固定顺序，返回首个失败的可区分原因）。 */
    if (ctx.url_truncated) {
        return MANIFEST_ERR_URL_TOO_LONG;                 /* Req 10.1 字符串长度限制 */
    }
    if (!manifest_url_is_https(ctx.m.film_url)) {
        return MANIFEST_ERR_URL_SCHEME;
    }
    if (ctx.sha_truncated || !manifest_is_lower_hex64(ctx.m.sha256_hex)) {
        return MANIFEST_ERR_SHA256_FORMAT;
    }
    if (!manifest_size_is_standard(ctx.m.size)) {
        return MANIFEST_ERR_SIZE_MISMATCH;                /* Req 10.2 标准版 size 必须 ==209120 */
    }

    *out = ctx.m;
    return MANIFEST_OK;
}

const char *manifest_err_str(manifest_err_t err)
{
    switch (err) {
        case MANIFEST_OK:                return "MANIFEST_OK";
        case MANIFEST_ERR_NULL:          return "MANIFEST_ERR_NULL";
        case MANIFEST_ERR_EMPTY:         return "MANIFEST_ERR_EMPTY";
        case MANIFEST_ERR_TOO_LARGE:     return "MANIFEST_ERR_TOO_LARGE";
        case MANIFEST_ERR_JSON:          return "MANIFEST_ERR_JSON";
        case MANIFEST_ERR_MISSING_FIELD: return "MANIFEST_ERR_MISSING_FIELD";
        case MANIFEST_ERR_TYPE:          return "MANIFEST_ERR_TYPE";
        case MANIFEST_ERR_DUP_FIELD:     return "MANIFEST_ERR_DUP_FIELD";
        case MANIFEST_ERR_VERSION_RANGE: return "MANIFEST_ERR_VERSION_RANGE";
        case MANIFEST_ERR_SIZE_RANGE:    return "MANIFEST_ERR_SIZE_RANGE";
        case MANIFEST_ERR_SIZE_MISMATCH: return "MANIFEST_ERR_SIZE_MISMATCH";
        case MANIFEST_ERR_URL_TOO_LONG:  return "MANIFEST_ERR_URL_TOO_LONG";
        case MANIFEST_ERR_URL_SCHEME:    return "MANIFEST_ERR_URL_SCHEME";
        case MANIFEST_ERR_SHA256_FORMAT: return "MANIFEST_ERR_SHA256_FORMAT";
        case MANIFEST_ERR_ITEMS_TYPE:    return "MANIFEST_ERR_ITEMS_TYPE";
        case MANIFEST_ERR_ITEMS_TOO_MANY:return "MANIFEST_ERR_ITEMS_TOO_MANY";
        case MANIFEST_ERR_ITEM_FIELD:    return "MANIFEST_ERR_ITEM_FIELD";
        case MANIFEST_ERR_ITEM_NAME:     return "MANIFEST_ERR_ITEM_NAME";
        case MANIFEST_ERR_ITEM_URL:      return "MANIFEST_ERR_ITEM_URL";
        case MANIFEST_ERR_ITEM_SIZE:     return "MANIFEST_ERR_ITEM_SIZE";
        case MANIFEST_ERR_ITEM_SHA256:   return "MANIFEST_ERR_ITEM_SHA256";
        case MANIFEST_ERR_FW_TYPE:       return "MANIFEST_ERR_FW_TYPE";
        case MANIFEST_ERR_FW_FIELD:      return "MANIFEST_ERR_FW_FIELD";
        case MANIFEST_ERR_FW_VERSION:    return "MANIFEST_ERR_FW_VERSION";
        case MANIFEST_ERR_FW_URL:        return "MANIFEST_ERR_FW_URL";
        case MANIFEST_ERR_FW_SIZE:       return "MANIFEST_ERR_FW_SIZE";
        case MANIFEST_ERR_FW_SHA256:     return "MANIFEST_ERR_FW_SHA256";
        default:                         return "MANIFEST_ERR_UNKNOWN";
    }
}
