/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/param_serialize.c
 * Description: NVS 参数版本化序列化 / 反序列化 / 版本迁移纯逻辑实现（Req 19.1、19.2）。
 *              格式、机密处理与迁移语义见 param_serialize.h 顶部。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <string.h>

#include "param_serialize.h"
#include "poll_interval.h"   /* MONITOR_POLL_INTERVAL_DEFAULT_SEC (macro only) */

/*********************************************************************
 * COMPILE-TIME CHECKS
 */
/* body_len 以 u16 落盘，必须能放下当前布局。 */
_Static_assert(PARAM_SERIALIZE_BODY_LEN <= 0xFFFFu,
               "PARAM_SERIALIZE_BODY_LEN must fit in the u16 body-length field");
_Static_assert(PARAM_SERIALIZE_BODY_LEN_V2 + PARAM_SERIALIZE_WAKE_FIELDS_LEN ==
                   PARAM_SERIALIZE_BODY_LEN,
               "v2 must differ from v3 only by the wake schedule fields");
/* schema 版本与结构体显式版本不得漂移。 */
_Static_assert(PARAM_SCHEMA_VERSION_CURRENT == SFM_PARAM_CONFIG_VERSION,
               "PARAM_SCHEMA_VERSION_CURRENT must mirror SFM_PARAM_CONFIG_VERSION");
_Static_assert(PARAM_SCHEMA_VERSION_CURRENT >= 1u,
               "the current schema version must be >= 1");

/*********************************************************************
 * LOCAL TYPES — bounded little-endian byte cursors
 */
typedef struct {
    uint8_t *buf;
    size_t   cap;
    size_t   off;
    bool     ok;   /* becomes false on the first would-be overrun */
} wcur_t;

typedef struct {
    const uint8_t *buf;
    size_t         len;
    size_t         off;
    bool           ok;   /* becomes false on the first would-be under-run */
} rcur_t;

/*********************************************************************
 * LOCAL FUNCTIONS — writers
 */
static void w_bytes(wcur_t *w, const void *src, size_t n)
{
    if (!w->ok) {
        return;
    }
    if (n > w->cap - w->off) {   /* w->off <= w->cap invariant kept below */
        w->ok = false;
        return;
    }
    memcpy(w->buf + w->off, src, n);
    w->off += n;
}

static void w_zeros(wcur_t *w, size_t n)
{
    if (!w->ok) {
        return;
    }
    if (n > w->cap - w->off) {
        w->ok = false;
        return;
    }
    memset(w->buf + w->off, 0, n);
    w->off += n;
}

static void w_u8(wcur_t *w, uint8_t v)
{
    w_bytes(w, &v, 1u);
}

static void w_u16(wcur_t *w, uint16_t v)
{
    uint8_t b[2];
    b[0] = (uint8_t)(v & 0xFFu);
    b[1] = (uint8_t)((v >> 8) & 0xFFu);
    w_bytes(w, b, sizeof(b));
}

static void w_u32(wcur_t *w, uint32_t v)
{
    uint8_t b[4];
    b[0] = (uint8_t)(v & 0xFFu);
    b[1] = (uint8_t)((v >> 8) & 0xFFu);
    b[2] = (uint8_t)((v >> 16) & 0xFFu);
    b[3] = (uint8_t)((v >> 24) & 0xFFu);
    w_bytes(w, b, sizeof(b));
}

/*********************************************************************
 * LOCAL FUNCTIONS — readers
 */
static void r_bytes(rcur_t *r, void *dst, size_t n)
{
    if (!r->ok) {
        return;
    }
    if (n > r->len - r->off) {   /* r->off <= r->len invariant kept below */
        r->ok = false;
        return;
    }
    memcpy(dst, r->buf + r->off, n);
    r->off += n;
}

static uint8_t r_u8(rcur_t *r)
{
    uint8_t v = 0;
    r_bytes(r, &v, 1u);
    return v;
}

static uint16_t r_u16(rcur_t *r)
{
    uint8_t b[2] = {0, 0};
    r_bytes(r, b, sizeof(b));
    return (uint16_t)((uint16_t)b[0] | ((uint16_t)b[1] << 8));
}

static uint32_t r_u32(rcur_t *r)
{
    uint8_t b[4] = {0, 0, 0, 0};
    r_bytes(r, b, sizeof(b));
    return (uint32_t)b[0]
         | ((uint32_t)b[1] << 8)
         | ((uint32_t)b[2] << 16)
         | ((uint32_t)b[3] << 24);
}

/* Bounded content comparison of two NUL-terminated string fields. */
static bool str_field_eq(const char *a, const char *b, size_t cap)
{
    return strncmp(a, b, cap) == 0;
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

size_t param_serialized_len(void)
{
    return (size_t)PARAM_SERIALIZE_LEN;
}

param_ser_err_t param_serialize(const ServiceParam_Def_t *p,
                                uint8_t *out, size_t out_cap, size_t *out_len)
{
    if (p == NULL || out == NULL) {
        return PARAM_SER_ERR_NULL;
    }
    if (out_cap < (size_t)PARAM_SERIALIZE_LEN) {
        return PARAM_SER_ERR_BUFFER_TOO_SMALL;
    }

    wcur_t w = { out, out_cap, 0u, true };

    /* --- header --- */
    w_u8(&w, PARAM_SERIALIZE_MAGIC0);
    w_u8(&w, PARAM_SERIALIZE_MAGIC1);
    w_u8(&w, PARAM_SERIALIZE_MAGIC2);
    w_u8(&w, PARAM_SERIALIZE_MAGIC3);
    /* schema_version mirrors config_version (low 16 bits) for quick probing;
     * the body's config_version field is authoritative. */
    w_u16(&w, (uint16_t)(p->config_version & 0xFFFFu));
    w_u16(&w, (uint16_t)PARAM_SERIALIZE_BODY_LEN);

    /* --- body --- */
    w_u8(&w, p->factory_flag);
    w_u32(&w, p->config_version);

    /* film */
    w_u32(&w, p->film.current_file_id);
    w_u8(&w, p->film.load_complete);
    w_u8(&w, p->film.play_mode);

    /* sleep */
    w_u8(&w, p->sleep.sleep_mode);
    w_u8(&w, p->sleep.sleep_auto);
    w_u16(&w, p->sleep.sleep_time);
    /* 唤醒时间表（F3，config_version v3 新增）。定长写满 WAKE_SCHEDULE_MAX_SLOTS 个
     * 槽位而不是只写 count 个：变长会让 body_len 随配置变化，而反序列化正是靠固定
     * body_len 判断记录是否属于当前布局的。 */
    w_u8(&w, p->sleep.wake_slot_count);
    for (unsigned i = 0; i < (unsigned)WAKE_SCHEDULE_MAX_SLOTS; i++) {
        w_u16(&w, p->sleep.wake_slots[i]);
    }
    w_u16(&w, p->sleep.wake_fallback_min);

    /* network — the two PLAINTEXT secret fields are written as ZEROS: plaintext
     * is never persisted (Req 15.1/15.4); only the ciphertext in `secrets` is. */
    w_u8(&w, p->network.wifi_enable);
    w_bytes(&w, p->network.wifi_ssid, SFM_WIFI_SSID_MAX);
    w_zeros(&w, SFM_WIFI_PASSWORD_MAX);                              /* wifi_password plaintext -> ZEROS */
    w_bytes(&w, p->network.film_api_url, SFM_FILM_API_URL_MAX);
    w_bytes(&w, p->network.manifest_url, SFM_MANIFEST_URL_MAX);
    w_bytes(&w, p->network.device_id, SFM_DEVICE_ID_MAX);
    w_zeros(&w, SFM_DEVICE_TOKEN_MAX);                              /* device_token plaintext -> ZEROS */

    /* sync */
    w_u32(&w, p->sync.poll_interval_seconds);
    w_u32(&w, p->sync.last_applied_version);
    w_bytes(&w, p->sync.last_sync_error, SFM_SYNC_ERR_MAX);

    /* secrets — ciphertext preserved verbatim (Req 15.1). */
    w_u16(&w, p->secrets.wifi_password_len);
    w_u16(&w, p->secrets.device_token_len);
    w_bytes(&w, p->secrets.wifi_password_enc, SFM_WIFI_PASSWORD_ENC_MAX);
    w_bytes(&w, p->secrets.device_token_enc, SFM_DEVICE_TOKEN_ENC_MAX);

    if (!w.ok) {
        return PARAM_SER_ERR_BUFFER_TOO_SMALL;   /* should not happen given the cap check */
    }
    if (out_len != NULL) {
        *out_len = w.off;
    }
    return PARAM_SER_OK;
}

param_ser_err_t param_deserialize(const uint8_t *buf, size_t len,
                                  ServiceParam_Def_t *out)
{
    if (buf == NULL || out == NULL) {
        return PARAM_SER_ERR_NULL;
    }
    if (len < (size_t)PARAM_SERIALIZE_HEADER_LEN) {
        return PARAM_SER_ERR_TRUNCATED;
    }

    rcur_t r = { buf, len, 0u, true };

    /* --- header --- */
    uint8_t m0 = r_u8(&r);
    uint8_t m1 = r_u8(&r);
    uint8_t m2 = r_u8(&r);
    uint8_t m3 = r_u8(&r);
    if (m0 != PARAM_SERIALIZE_MAGIC0 || m1 != PARAM_SERIALIZE_MAGIC1 ||
        m2 != PARAM_SERIALIZE_MAGIC2 || m3 != PARAM_SERIALIZE_MAGIC3) {
        return PARAM_SER_ERR_MAGIC;
    }
    uint16_t schema_version = r_u16(&r);   /* informational; body is authoritative */
    (void)schema_version;
    uint16_t body_len = r_u16(&r);
    bool is_v2 = body_len == (uint16_t)PARAM_SERIALIZE_BODY_LEN_V2;
    if (!is_v2 && body_len != (uint16_t)PARAM_SERIALIZE_BODY_LEN) {
        return PARAM_SER_ERR_BODY_LEN;
    }
    if (len < (size_t)PARAM_SERIALIZE_HEADER_LEN + (size_t)body_len) {
        return PARAM_SER_ERR_TRUNCATED;
    }

    /* Zero the whole struct first: guarantees padding + the two plaintext secret
     * fields come back as 0 (plaintext is never persisted). */
    memset(out, 0, sizeof(*out));

    /* --- body --- */
    out->factory_flag = r_u8(&r);
    out->config_version = r_u32(&r);

    out->film.current_file_id = r_u32(&r);
    out->film.load_complete = r_u8(&r);
    out->film.play_mode = r_u8(&r);

    out->sleep.sleep_mode = r_u8(&r);
    out->sleep.sleep_auto = r_u8(&r);
    out->sleep.sleep_time = r_u16(&r);
    if (!is_v2) {
        out->sleep.wake_slot_count = r_u8(&r);
        /* 在不可信持久化输入的边界收敛一次，避免各消费者按越界 count 读取 slots。 */
        if (out->sleep.wake_slot_count > (uint8_t)WAKE_SCHEDULE_MAX_SLOTS) {
            out->sleep.wake_slot_count = (uint8_t)WAKE_SCHEDULE_MAX_SLOTS;
        }
        for (unsigned i = 0; i < (unsigned)WAKE_SCHEDULE_MAX_SLOTS; i++) {
            out->sleep.wake_slots[i] = r_u16(&r);
        }
        out->sleep.wake_fallback_min = r_u16(&r);
    }

    out->network.wifi_enable = r_u8(&r);
    r_bytes(&r, out->network.wifi_ssid, SFM_WIFI_SSID_MAX);
    r_bytes(&r, out->network.wifi_password, SFM_WIFI_PASSWORD_MAX);   /* reads the ZEROS written by serialize */
    r_bytes(&r, out->network.film_api_url, SFM_FILM_API_URL_MAX);
    r_bytes(&r, out->network.manifest_url, SFM_MANIFEST_URL_MAX);
    r_bytes(&r, out->network.device_id, SFM_DEVICE_ID_MAX);
    r_bytes(&r, out->network.device_token, SFM_DEVICE_TOKEN_MAX);     /* reads the ZEROS written by serialize */

    out->sync.poll_interval_seconds = r_u32(&r);
    out->sync.last_applied_version = r_u32(&r);
    r_bytes(&r, out->sync.last_sync_error, SFM_SYNC_ERR_MAX);

    out->secrets.wifi_password_len = r_u16(&r);
    out->secrets.device_token_len = r_u16(&r);
    r_bytes(&r, out->secrets.wifi_password_enc, SFM_WIFI_PASSWORD_ENC_MAX);
    r_bytes(&r, out->secrets.device_token_enc, SFM_DEVICE_TOKEN_ENC_MAX);

    if (!r.ok) {
        return PARAM_SER_ERR_TRUNCATED;
    }
    /* Defensive: keep the plaintext secret fields NUL-terminated + zeroed even if
     * a crafted buffer put non-zero bytes in their reserved regions. Plaintext is
     * reconstituted by the parameter service from `secrets` (decrypt), never here. */
    memset(out->network.wifi_password, 0, sizeof(out->network.wifi_password));
    memset(out->network.device_token, 0, sizeof(out->network.device_token));

    return PARAM_SER_OK;
}

bool param_migrate(ServiceParam_Def_t *p)
{
    if (p == NULL) {
        return false;
    }
    if (p->config_version == (uint32_t)PARAM_SCHEMA_VERSION_CURRENT) {
        return false;   /* already current — idempotent no-op */
    }

    if (p->config_version < (uint32_t)PARAM_SCHEMA_VERSION_CURRENT) {
        /* Forward, non-destructive normalization to the current (v1) layout.
         * v1 (relative to the pre-versioning v0 baseline) added the Wi-Fi sync
         * fields. Every one of them has a valid "0/empty" default EXCEPT
         * poll_interval_seconds (0s is not a legal poll period), so fill only
         * that one when unset — preserving all other existing field values.
         * The poll-interval default is reused from poll_interval.h rather than
         * hardcoded. */
        if (p->sync.poll_interval_seconds == 0u) {
            p->sync.poll_interval_seconds = (uint32_t)MONITOR_POLL_INTERVAL_DEFAULT_SEC;
        }
        /* v2：休眠默认改为关闭（调试/演示阶段设备常驻广播、随时可连，同时规避深睡
         * 路径上的已知 spi_bus_free 崩溃）。从 v1 迁移时应用该新策略。 */
        if (p->config_version < 2u) {
            p->sleep.sleep_mode = 0;
        }
        /* v2 没有唤醒字段，直接补当前出厂默认。 */
        if (p->config_version < 3u) {
            sfm_param_apply_default_wake_slots(&p->sleep);
        }
        /* v3 的旧出厂组合（0 槽 + legacy 兜底）→ 补默认。用户自定义过的不动。 */
        else if (p->config_version < 4u && p->sleep.wake_slot_count == 0u &&
                 p->sleep.wake_fallback_min == (uint16_t)SFM_PARAM_LEGACY_WAKE_FALLBACK_MIN) {
            sfm_param_apply_default_wake_slots(&p->sleep);
        }
        /* v4 的旧出厂组合（单槽 06:30）→ 补三餐槽。
         * 只认这一个精确组合：单槽 06:30 是 v4 的出厂值，几乎不会是用户手填的结果。
         * 留在旧默认上的代价是设备每天只在 06:30 取一次图，与后端三餐发布时刻脱节，
         * 而这种脱节在设备上表现成「菜单是昨天的」，从日志里看不出来。 */
        else if (p->config_version < 5u && p->sleep.wake_slot_count == 1u &&
                 p->sleep.wake_slots[0] == (uint16_t)SFM_PARAM_LEGACY_WAKE_SLOT_MIN) {
            sfm_param_apply_default_wake_slots(&p->sleep);
        }
        /* v5 的旧出厂轮询周期（60 秒）→ 补当前默认。只认这一个精确值：60 秒是给联调用的，
         * 用户手填的概率极低，而留在旧值上会让升级后的设备继续按分钟级轮询耗电。 */
        if (p->config_version < 6u &&
            p->sync.poll_interval_seconds == (uint32_t)SFM_PARAM_LEGACY_POLL_INTERVAL_SEC) {
            p->sync.poll_interval_seconds = (uint32_t)MONITOR_POLL_INTERVAL_DEFAULT_SEC;
        }
    }
    /* else: config_version > current (firmware downgrade / unknown record).
     * We cannot interpret newer fields, so preserve ALL existing values and
     * only restamp the version — never destroy user data. */

    p->config_version = (uint32_t)PARAM_SCHEMA_VERSION_CURRENT;
    return true;
}

bool param_config_equal(const ServiceParam_Def_t *a, const ServiceParam_Def_t *b)
{
    if (a == b) {
        return true;
    }
    if (a == NULL || b == NULL) {
        return false;
    }

    if (a->factory_flag != b->factory_flag) {
        return false;
    }
    if (a->config_version != b->config_version) {
        return false;
    }

    if (a->film.current_file_id != b->film.current_file_id ||
        a->film.load_complete != b->film.load_complete ||
        a->film.play_mode != b->film.play_mode) {
        return false;
    }

    if (a->sleep.sleep_mode != b->sleep.sleep_mode ||
        a->sleep.sleep_auto != b->sleep.sleep_auto ||
        a->sleep.sleep_time != b->sleep.sleep_time) {
        return false;
    }
    /* 唤醒时间表：整个定长数组都参与比较（包括 count 之后的槽位）。序列化写满全部
     * 槽位，所以「落盘后能否原样恢复」的语义也必须覆盖全部槽位。 */
    if (a->sleep.wake_slot_count != b->sleep.wake_slot_count ||
        a->sleep.wake_fallback_min != b->sleep.wake_fallback_min) {
        return false;
    }
    for (unsigned i = 0; i < (unsigned)WAKE_SCHEDULE_MAX_SLOTS; i++) {
        if (a->sleep.wake_slots[i] != b->sleep.wake_slots[i]) {
            return false;
        }
    }

    if (a->network.wifi_enable != b->network.wifi_enable) {
        return false;
    }
    if (!str_field_eq(a->network.wifi_ssid, b->network.wifi_ssid, SFM_WIFI_SSID_MAX) ||
        !str_field_eq(a->network.film_api_url, b->network.film_api_url, SFM_FILM_API_URL_MAX) ||
        !str_field_eq(a->network.manifest_url, b->network.manifest_url, SFM_MANIFEST_URL_MAX) ||
        !str_field_eq(a->network.device_id, b->network.device_id, SFM_DEVICE_ID_MAX)) {
        return false;
    }
    /* network.wifi_password / network.device_token (PLAINTEXT) are intentionally
     * NOT compared: they are never persisted (only their ciphertext is). */

    if (a->sync.poll_interval_seconds != b->sync.poll_interval_seconds ||
        a->sync.last_applied_version != b->sync.last_applied_version) {
        return false;
    }
    if (!str_field_eq(a->sync.last_sync_error, b->sync.last_sync_error, SFM_SYNC_ERR_MAX)) {
        return false;
    }

    if (a->secrets.wifi_password_len != b->secrets.wifi_password_len ||
        a->secrets.device_token_len != b->secrets.device_token_len) {
        return false;
    }
    {
        size_t wl = a->secrets.wifi_password_len;
        if (wl > SFM_WIFI_PASSWORD_ENC_MAX) {
            wl = SFM_WIFI_PASSWORD_ENC_MAX;
        }
        if (memcmp(a->secrets.wifi_password_enc, b->secrets.wifi_password_enc, wl) != 0) {
            return false;
        }
        size_t tl = a->secrets.device_token_len;
        if (tl > SFM_DEVICE_TOKEN_ENC_MAX) {
            tl = SFM_DEVICE_TOKEN_ENC_MAX;
        }
        if (memcmp(a->secrets.device_token_enc, b->secrets.device_token_enc, tl) != 0) {
            return false;
        }
    }

    return true;
}

const char *param_ser_err_str(param_ser_err_t err)
{
    switch (err) {
        case PARAM_SER_OK:                   return "ok";
        case PARAM_SER_ERR_NULL:             return "null-argument";
        case PARAM_SER_ERR_BUFFER_TOO_SMALL: return "buffer-too-small";
        case PARAM_SER_ERR_MAGIC:            return "bad-magic";
        case PARAM_SER_ERR_BODY_LEN:         return "bad-body-length";
        case PARAM_SER_ERR_TRUNCATED:        return "truncated";
        default:                             return "unknown";
    }
}
