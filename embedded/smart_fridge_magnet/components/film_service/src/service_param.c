/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_service/src/service_param.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/7
 * Description: 服务参数：NVS 持久化与运行时访问（Req 15.1、19.1、19.2）。
 *
 *   落盘用 param_serialize.c 产出的版本化字节记录，而非裸结构体 dump：这样按
 *   config_version 迁移，不会因 sizeof 漂移而静默回退默认值。save 路径先加密
 *   secrets 再序列化（明文机密写 0，密文保留）；load 路径 deserialize → migrate →
 *   param_decrypt_secrets() 还原运行时明文。运行时明文只存在于
 *   g_service_param.network.*，flash 与串口日志都不含明文。
 *
 *   记录长度不符、magic/body 长度错、或 factory_flag 不是 SFM 标志的记录，一律视为
 *   外来/损坏并重新初始化为新设备默认值，绝不迁移（保住 Req 2.4 的首启全新初始化）。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#include "nvs_flash.h"

#include "sys_log.h"
#include "service_param.h"
#include "film_pin.h"          /* pinned film 配置结构与合法性判据（独立 NVS key）      */
#include "sfm_crypto.h"
#include "param_serialize.h"   /* versioned NVS record: serialize / deserialize / migrate */
#include "poll_interval.h"     /* monitor_clamp_poll_interval (Req 16.3, 14.3)             */

/*********************************************************************
 * MACROS
 */
#define SERVICE_FACTORY_DEFAULT_FLAG                            (0x22)

/* 本文件的日志 tag：pin 记录的读写需要可诊断的痕迹。 */
#define PARAM_TAG                                               "param"

/* —— pinned film 记录的字节布局（独立 NVS key，见 service_param.h 的说明）——
 *
 * 自带 magic + version + body_len 头，思路照 param_serialize.h 但独立且极简：这条记录
 * 只有 4 个字段、只有一个写入方，套用 param_serialize 那套（含机密加密、迁移）纯属负担，
 * 而多一份耦合就多一处「改 PARAMS 布局时顺手改坏 pin 布局」的可能。
 *
 *   [0..3] magic 'S''F''P''N'
 *   [4]    version (= 1)
 *   [5]    body_len (= PARAM_PINNED_BODY_LEN)
 *   [6]    mode
 *   [7..8] window_start_min（大端，与 BLE 0x4C 载荷同字节序）
 *   [9..10]window_end_min（大端）
 *   [11..] filename[FILM_PIN_FILENAME_MAX]（定长，尾部补 0）
 *
 * filename 写**定长**而不是变长：变长会让记录长度随名字变化，而记录长度正是判断
 * 「这条记录是否属于当前布局」的依据（与 param_serialize.c 里定长写满
 * WAKE_SCHEDULE_MAX_SLOTS 个槽位同一个理由）。 */
#define PARAM_PINNED_MAGIC0        ((uint8_t)'S')
#define PARAM_PINNED_MAGIC1        ((uint8_t)'F')
#define PARAM_PINNED_MAGIC2        ((uint8_t)'P')
#define PARAM_PINNED_MAGIC3        ((uint8_t)'N')
#define PARAM_PINNED_VERSION       (1u)
#define PARAM_PINNED_HEADER_LEN    (6u)
#define PARAM_PINNED_BODY_LEN      (1u + 2u + 2u + FILM_PIN_FILENAME_MAX)
#define PARAM_PINNED_REC_LEN       (PARAM_PINNED_HEADER_LEN + PARAM_PINNED_BODY_LEN)

/* The public header declares SFM_SECRET_CIPHER_OVERHEAD locally (to stay free
 * of sfm_crypto.h). Verify here that it matches the real crypto overhead so the
 * enc-buffer sizes can never silently under-size the ciphertext. */
_Static_assert(SFM_SECRET_CIPHER_OVERHEAD == SFM_CRYPTO_OVERHEAD,
               "SFM_SECRET_CIPHER_OVERHEAD must equal sfm_crypto SFM_CRYPTO_OVERHEAD");

/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */
static void nvs_init(void);
static void nvs_param_save(void);
static void service_param_set_default(void);

/*********************************************************************
 * GLOBAL VARIABLES
 */
ServiceParam_Def_t g_service_param = {0};

/*********************************************************************
 * LOCAL FUNCTIONS
 */

/**
 * [param_secure_zero 抹除内存（volatile，避免被优化器省略），用于清理栈上明文副本]
 */
static void param_secure_zero(void *p, size_t n)
{
    volatile uint8_t *v = (volatile uint8_t *)p;
    while (n--)
    {
        *v++ = 0u;
    }
}

/**
 * [param_field_len 计算以 NUL 结尾字符串的有界长度（避免非标准的 strnlen）]
 */
static size_t param_field_len(const char *s, size_t max)
{
    const void *nul = memchr(s, '\0', max);
    return (nul != NULL) ? (size_t)((const char *)nul - s) : max;
}

/**
 * [param_encrypt_secrets 将副本 dst 中的敏感字段明文加密进 dst->secrets，并把 dst 明文清零]
 *
 * 仅作用于持久化副本：读取 dst->network.{wifi_password,device_token} 的明文，
 * 经 SFM_BLE_KEY 加密后写入 dst->secrets.*_enc（记录密文长度），随后把 dst 中的
 * 明文字节清零。因此写入 NVS 的 blob 只含密文，绝不含敏感字段明文。空字符串
 * 记为长度 0（不存密文）。加密失败时“失败关闭”：记为长度 0 且不落盘明文。
 */
static void param_encrypt_secrets(ServiceParam_Def_t *dst)
{
    /* --- wifi_password --- */
    size_t pt_len = param_field_len(dst->network.wifi_password, SFM_WIFI_PASSWORD_MAX);
    dst->secrets.wifi_password_len = 0;
    memset(dst->secrets.wifi_password_enc, 0, sizeof(dst->secrets.wifi_password_enc));
    if (pt_len > 0)
    {
        size_t out_len = 0;
        sfm_crypto_err_t rc = sfm_field_encrypt((const uint8_t *)dst->network.wifi_password,
                                                pt_len,
                                                dst->secrets.wifi_password_enc,
                                                sizeof(dst->secrets.wifi_password_enc),
                                                &out_len);
        if (rc == SFM_CRYPTO_OK)
        {
            dst->secrets.wifi_password_len = (uint16_t)out_len;
        }
    }
    /* Never persist the plaintext bytes. */
    memset(dst->network.wifi_password, 0, sizeof(dst->network.wifi_password));

    /* --- device_token --- */
    pt_len = param_field_len(dst->network.device_token, SFM_DEVICE_TOKEN_MAX);
    dst->secrets.device_token_len = 0;
    memset(dst->secrets.device_token_enc, 0, sizeof(dst->secrets.device_token_enc));
    if (pt_len > 0)
    {
        size_t out_len = 0;
        sfm_crypto_err_t rc = sfm_field_encrypt((const uint8_t *)dst->network.device_token,
                                                pt_len,
                                                dst->secrets.device_token_enc,
                                                sizeof(dst->secrets.device_token_enc),
                                                &out_len);
        if (rc == SFM_CRYPTO_OK)
        {
            dst->secrets.device_token_len = (uint16_t)out_len;
        }
    }
    memset(dst->network.device_token, 0, sizeof(dst->network.device_token));
}

/**
 * [param_decrypt_secrets 从 g_service_param.secrets 的密文还原敏感字段明文到 network]
 *
 * 载入 NVS blob 后调用：把 secrets.*_enc 解密回 network.{wifi_password,device_token}
 * 明文供运行时使用（service_wifi/service_ble 读取）。长度为 0 或非法则置空字符串。
 */
static void param_decrypt_secrets(void)
{
    /* --- wifi_password --- */
    memset(g_service_param.network.wifi_password, 0,
           sizeof(g_service_param.network.wifi_password));
    uint16_t clen = g_service_param.secrets.wifi_password_len;
    if (clen >= SFM_CRYPTO_OVERHEAD &&
        clen <= sizeof(g_service_param.secrets.wifi_password_enc))
    {
        uint8_t scratch[SFM_WIFI_PASSWORD_MAX];
        size_t out_len = 0;
        sfm_crypto_err_t rc = sfm_field_decrypt(g_service_param.secrets.wifi_password_enc,
                                                clen, scratch, sizeof(scratch), &out_len);
        if (rc == SFM_CRYPTO_OK &&
            out_len < sizeof(g_service_param.network.wifi_password))
        {
            memcpy(g_service_param.network.wifi_password, scratch, out_len);
            g_service_param.network.wifi_password[out_len] = '\0';
        }
        param_secure_zero(scratch, sizeof(scratch));
    }

    /* --- device_token --- */
    memset(g_service_param.network.device_token, 0,
           sizeof(g_service_param.network.device_token));
    clen = g_service_param.secrets.device_token_len;
    if (clen >= SFM_CRYPTO_OVERHEAD &&
        clen <= sizeof(g_service_param.secrets.device_token_enc))
    {
        uint8_t scratch[SFM_DEVICE_TOKEN_MAX];
        size_t out_len = 0;
        sfm_crypto_err_t rc = sfm_field_decrypt(g_service_param.secrets.device_token_enc,
                                                clen, scratch, sizeof(scratch), &out_len);
        if (rc == SFM_CRYPTO_OK &&
            out_len < sizeof(g_service_param.network.device_token))
        {
            memcpy(g_service_param.network.device_token, scratch, out_len);
            g_service_param.network.device_token[out_len] = '\0';
        }
        param_secure_zero(scratch, sizeof(scratch));
    }
}

/**
 * [param_write_locked 用已打开的 nvs 句柄写入“敏感字段加密 + 版本化序列化”的参数记录]
 *
 * 基于 g_service_param 制作栈上副本，加密其敏感字段（明文清零），再经 param_serialize()
 * 编码为一条版本化字节记录后整体写入并提交。序列化本身还会把两个明文机密字段区域写 0、
 * 仅原样保留 secrets 密文（Req 15.1/15.4），因此落盘记录不含任何机密明文。最后抹除栈上
 * 副本与记录缓冲（副本曾短暂持有明文）。
 */
static esp_err_t param_write_locked(nvs_handle_t handle)
{
    ServiceParam_Def_t blob = g_service_param;   /* copy (holds plaintext) */
    uint8_t rec[PARAM_SERIALIZE_LEN];
    size_t rec_len = 0;
    esp_err_t err;

    param_encrypt_secrets(&blob);                /* -> ciphertext in secrets, plaintext zeroed */

    param_ser_err_t se = param_serialize(&blob, rec, sizeof(rec), &rec_len);
    if (se != PARAM_SER_OK)
    {
        /* Cannot happen: rec is sized to PARAM_SERIALIZE_LEN. Fail closed
         * without persisting anything. */
        param_secure_zero(&blob, sizeof(blob));
        return ESP_ERR_NVS_INVALID_LENGTH;
    }

    err = nvs_set_blob(handle, SYS_M_NVS_KEY_NAME, rec, rec_len);
    if (err == ESP_OK)
    {
        err = nvs_commit(handle);
    }

    param_secure_zero(&blob, sizeof(blob));      /* wipe stack copy that briefly held plaintext */
    param_secure_zero(rec, sizeof(rec));         /* defensive: record held only ciphertext/zeros */
    return err;
}


/*********************************************************************
 * GLOBAL FUNCTIONS
 */


/**
 * [service_param_init 初始化服务参数]
 */
void service_param_init(void)
{
    nvs_init();
}

/**
 * [service_param_save 保存服务参数]
 */
void service_param_save(void)
{
    nvs_param_save();
}

/**
 * [service_param_reset 重置服务参数]
 */
void service_param_reset(void)
{
    service_param_set_default();
    nvs_param_save();
    /* pin 记录在独立的 NVS key 下，不会被 nvs_param_save() 捎带重置 —— 必须显式擦除，
     * 否则恢复出厂后自定义图还挂着，而用户以为清干净了。 */
    service_param_pinned_erase();
}


/**
 * [service_param_set_default 设置服务参数默认值]
 */
static void service_param_set_default(void)
{
    // 设置服务参数默认值
    g_service_param.factory_flag = SERVICE_FACTORY_DEFAULT_FLAG;
    g_service_param.config_version = SFM_PARAM_CONFIG_VERSION;

    // 参数还原
    g_service_param.film.load_complete = 0;
    g_service_param.film.play_mode = 0;
    g_service_param.film.current_file_id = 0;

    /* 休眠模式默认开启：不睡的代价是 CPU + BLE 广播 7x24 全开，实测一夜即把电池耗尽到
     * 欠压反复重启（BROWNOUT_RST boot loop，固件起不来、手机也扫不到设备）。
     *
     * 行为闭环：空闲 -> 深睡；按任意键唤醒并开启 60s BLE 广播+可连接窗口；窗口内可连接
     * 推图；空闲后再次深睡。需要长时间常驻调试时用 BLE 0x25 在运行时关闭休眠，0x26 回读
     * 当前状态（0x20 是 film 播放模式，与休眠无关）。
     *
     * 本函数只在首次启动或恢复出厂设置时生效，已烧录设备需经 BLE 设置或恢复出厂才切换。 */
    g_service_param.sleep.sleep_mode = 1;  // 休眠模式默认开启（省电；深睡崩溃已修）
    g_service_param.sleep.sleep_auto = 0;  // 自动唤醒默认关闭
    g_service_param.sleep.sleep_time = 10; // 默认10分钟

    /* 出厂三槽：早/午/晚各提前一点醒，取当餐那张。App 可用 BLE 0x48 覆盖。 */
    sfm_param_apply_default_wake_slots(&g_service_param.sleep);

    // 网络参数重置
    g_service_param.network.wifi_enable = 0; // WiFi默认关闭
    memset(g_service_param.network.wifi_ssid, 0, sizeof(g_service_param.network.wifi_ssid));
    memset(g_service_param.network.wifi_password, 0, sizeof(g_service_param.network.wifi_password));
    memset(g_service_param.network.film_api_url, 0, sizeof(g_service_param.network.film_api_url));
    memset(g_service_param.network.manifest_url, 0, sizeof(g_service_param.network.manifest_url));
    memset(g_service_param.network.device_id, 0, sizeof(g_service_param.network.device_id));
    memset(g_service_param.network.device_token, 0, sizeof(g_service_param.network.device_token));

    // 同步状态参数重置
    g_service_param.sync.poll_interval_seconds = MONITOR_POLL_INTERVAL_DEFAULT_SEC;
    g_service_param.sync.last_applied_version = 0;
    memset(g_service_param.sync.last_sync_error, 0, sizeof(g_service_param.sync.last_sync_error));

    // 敏感字段密文清空（无存储）
    g_service_param.secrets.wifi_password_len = 0;
    g_service_param.secrets.device_token_len = 0;
    memset(g_service_param.secrets.wifi_password_enc, 0, sizeof(g_service_param.secrets.wifi_password_enc));
    memset(g_service_param.secrets.device_token_enc, 0, sizeof(g_service_param.secrets.device_token_enc));
}

/**
 * [nvs_init 初始化nvs]
 */
static void nvs_init(void)
{
    esp_err_t err;
    nvs_handle_t my_nvs_handle;

    err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        SYS_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    SYS_ERROR_CHECK(err);

    err = nvs_open(SYS_M_NVS_NAMESPACE, NVS_READWRITE, &my_nvs_handle);
    SYS_ERROR_CHECK(err);

    bool need_fresh_init = false;
    uint8_t rec[PARAM_SERIALIZE_LEN];
    size_t stored_len = 0;

    /* Size-query first: a foreign/corrupt blob whose length differs from the
     * versioned record can then be detected and re-initialized instead of
     * faulting on a short read buffer. */
    err = nvs_get_blob(my_nvs_handle, SYS_M_NVS_KEY_NAME, NULL, &stored_len);
    if (err == ESP_ERR_NVS_NOT_FOUND)
    {
        /* Genuine first boot under SFM_NVS/PARAMS: initialize new-device
         * defaults. The legacy FrameFilm blob (different namespace/key) is
         * never consulted, so no upstream blob is migrated (Req 2.4). */
        need_fresh_init = true;
    }
    else if (err != ESP_OK ||
             (stored_len != (size_t)PARAM_SERIALIZE_LEN &&
              stored_len != (size_t)PARAM_SERIALIZE_LEN_V2))
    {
        /* Unexpected NVS error or a record of the wrong length -> treat as
         * foreign/corrupt and re-initialize (never adopt/migrate it). */
        need_fresh_init = true;
    }
    else
    {
        size_t rec_len = stored_len;
        err = nvs_get_blob(my_nvs_handle, SYS_M_NVS_KEY_NAME, rec, &rec_len);
        if (err != ESP_OK)
        {
            need_fresh_init = true;
        }
        else
        {
            param_ser_err_t de = param_deserialize(rec, rec_len, &g_service_param);
            if (de != PARAM_SER_OK ||
                g_service_param.factory_flag != SERVICE_FACTORY_DEFAULT_FLAG)
            {
                /* Bad magic / body length / truncation, or not an SFM
                 * factory-initialized record -> re-initialize defaults. */
                need_fresh_init = true;
            }
            else
            {
                /* Valid SFM record: migrate by config_version (forward,
                 * non-destructive — Req 19.2), then reconstitute the sensitive
                 * fields' plaintext from their at-rest ciphertext. Re-persist
                 * only when migration actually changed the record so a later
                 * boot need not migrate again. */
                bool migrated = param_migrate(&g_service_param);
                param_decrypt_secrets();
                if (migrated)
                {
                    err = param_write_locked(my_nvs_handle);
                    SYS_ERROR_CHECK(err);
                }
            }
        }
    }

    if (need_fresh_init)
    {
        service_param_set_default();
        err = param_write_locked(my_nvs_handle);
        SYS_ERROR_CHECK(err);
    }

    param_secure_zero(rec, sizeof(rec));   /* defensive: record held only ciphertext/zeros */
    nvs_close(my_nvs_handle);
}

/**
 * [nvs_param_save 保存nvs参数]
 */
static void nvs_param_save(void)
{
    esp_err_t err;
    nvs_handle_t my_nvs_handle;

    err = nvs_open(SYS_M_NVS_NAMESPACE, NVS_READWRITE, &my_nvs_handle);
    SYS_ERROR_CHECK(err);

    if(err == ESP_OK)
    {
        err = param_write_locked(my_nvs_handle);
        SYS_ERROR_CHECK(err);
    }

    nvs_close(my_nvs_handle);
}


/*********************************************************************
 * GLOBAL FUNCTIONS — Wi-Fi sync provisioning setters (uses these)
 *
 * Each does a bounded, NUL-terminated copy into g_service_param and does NOT
 * persist by itself; the caller invokes service_param_save() to persist.
 * The password/token setters take PLAINTEXT — at-rest encryption is applied by
 * the parameter service on save. None of these emit the field value to the log.
 */

void service_param_set_manifest_url(const char *url)
{
    if (url == NULL)
    {
        return;
    }
    strncpy(g_service_param.network.manifest_url, url, SFM_MANIFEST_URL_MAX - 1);
    g_service_param.network.manifest_url[SFM_MANIFEST_URL_MAX - 1] = '\0';
}

void service_param_set_device_id(const char *id)
{
    if (id == NULL)
    {
        return;
    }
    strncpy(g_service_param.network.device_id, id, SFM_DEVICE_ID_MAX - 1);
    g_service_param.network.device_id[SFM_DEVICE_ID_MAX - 1] = '\0';
}

void service_param_set_device_token(const char *token_plain)
{
    if (token_plain == NULL)
    {
        return;
    }
    strncpy(g_service_param.network.device_token, token_plain, SFM_DEVICE_TOKEN_MAX - 1);
    g_service_param.network.device_token[SFM_DEVICE_TOKEN_MAX - 1] = '\0';
}

void service_param_set_wifi(const char *ssid, const char *password_plain)
{
    if (ssid != NULL)
    {
        strncpy(g_service_param.network.wifi_ssid, ssid, SFM_WIFI_SSID_MAX - 1);
        g_service_param.network.wifi_ssid[SFM_WIFI_SSID_MAX - 1] = '\0';
    }
    if (password_plain != NULL)
    {
        strncpy(g_service_param.network.wifi_password, password_plain, SFM_WIFI_PASSWORD_MAX - 1);
        g_service_param.network.wifi_password[SFM_WIFI_PASSWORD_MAX - 1] = '\0';
    }
}

void service_param_set_poll_interval(uint32_t seconds)
{
    /* Clamp to the legal poll window [30, 86400] before storing (Req 16.3,
     * 14.3, Property 14) so an out-of-range provisioning value can never be
     * persisted or used for a timed wake. */
    g_service_param.sync.poll_interval_seconds = monitor_clamp_poll_interval(seconds);
}

void service_param_set_last_applied_version(uint32_t version)
{
    g_service_param.sync.last_applied_version = version;
}

void service_param_set_last_sync_error(const char *err)
{
    if (err == NULL)
    {
        g_service_param.sync.last_sync_error[0] = '\0';
        return;
    }
    strncpy(g_service_param.sync.last_sync_error, err, SFM_SYNC_ERR_MAX - 1);
    g_service_param.sync.last_sync_error[SFM_SYNC_ERR_MAX - 1] = '\0';
}

bool service_param_set_wake_schedule(const uint16_t *slots, uint8_t count,
                                     uint16_t fallback_minutes)
{
    /* 全有或全无：先把整条载荷校验完，任何一项不合法都在改动 g_service_param 之前
     * 返回。半张时间表会让设备在一部分饭点醒、另一部分不醒，而 App 那边显示的是
     * 完整配置 —— 两边不一致却没有任何一处报错，是最难排查的一类问题。 */
    if (count > WAKE_SCHEDULE_MAX_SLOTS)
    {
        return false;
    }
    if (count > 0 && slots == NULL)
    {
        return false;
    }
    for (uint8_t i = 0; i < count; i++)
    {
        if (!wake_schedule_slot_valid(slots[i]))
        {
            return false;
        }
    }
    /* 兜底周期上界 7 天：再长就等于「实际上没有兜底」，而设备会显得像坏了
     * （手动发布的画面十几天不上屏）。上界与 BLE 0x48 的校验保持同一口径。 */
    if ((uint32_t)fallback_minutes > (uint32_t)(WAKE_SCHEDULE_MINUTES_PER_DAY * 7u))
    {
        return false;
    }

    /* 校验通过后一次性写入。count 之后的槽位清零，避免旧配置的残留值在
     * 「count 变大但没重写全部槽位」时被当成有效唤醒点。 */
    memset(g_service_param.sleep.wake_slots, 0, sizeof(g_service_param.sleep.wake_slots));
    for (uint8_t i = 0; i < count; i++)
    {
        g_service_param.sleep.wake_slots[i] = slots[i];
    }
    g_service_param.sleep.wake_slot_count = count;
    g_service_param.sleep.wake_fallback_min = fallback_minutes;
    return true;
}


/*********************************************************************
 * GLOBAL FUNCTIONS — 置顶自定义画面（pinned film）的独立 NVS 记录
 *
 * 这三个函数走的是 SFM_NVS 下的独立 key "PINNED"，与 PARAMS blob 完全无关：
 * PARAMS 的长度（PARAM_SERIALIZE_LEN）一个字节都不变，因此**升级不丢配网**。
 * 布局与各条取舍见本文件顶部的 PARAM_PINNED_* 宏说明与 service_param.h。
 */

/**
 * [param_pinned_encode 把配置编码成一条定长记录]
 */
static void param_pinned_encode(const film_pin_cfg_t *cfg, uint8_t *rec)
{
    memset(rec, 0, PARAM_PINNED_REC_LEN);
    rec[0] = PARAM_PINNED_MAGIC0;
    rec[1] = PARAM_PINNED_MAGIC1;
    rec[2] = PARAM_PINNED_MAGIC2;
    rec[3] = PARAM_PINNED_MAGIC3;
    rec[4] = (uint8_t)PARAM_PINNED_VERSION;
    rec[5] = (uint8_t)PARAM_PINNED_BODY_LEN;
    rec[6] = cfg->mode;
    rec[7] = (uint8_t)((cfg->window_start_min >> 8) & 0xFFu);
    rec[8] = (uint8_t)(cfg->window_start_min & 0xFFu);
    rec[9] = (uint8_t)((cfg->window_end_min >> 8) & 0xFFu);
    rec[10] = (uint8_t)(cfg->window_end_min & 0xFFu);
    /* filename 已由 film_pin_cfg_is_valid() 保证在容量内 NUL 结尾；memcpy 定长搬运，
     * 尾部由上面的 memset 补 0。 */
    memcpy(&rec[11], cfg->filename, FILM_PIN_FILENAME_MAX);
}

/**
 * [param_pinned_decode 解析一条记录；任何不符即返回 false（视为“未设置”）]
 */
static bool param_pinned_decode(const uint8_t *rec, size_t len, film_pin_cfg_t *out)
{
    if (rec == NULL || len != (size_t)PARAM_PINNED_REC_LEN)
    {
        return false;
    }
    if (rec[0] != PARAM_PINNED_MAGIC0 || rec[1] != PARAM_PINNED_MAGIC1 ||
        rec[2] != PARAM_PINNED_MAGIC2 || rec[3] != PARAM_PINNED_MAGIC3)
    {
        return false;
    }
    if (rec[4] != (uint8_t)PARAM_PINNED_VERSION ||
        rec[5] != (uint8_t)PARAM_PINNED_BODY_LEN)
    {
        return false;
    }

    memset(out, 0, sizeof(*out));
    out->mode = rec[6];
    out->window_start_min = (uint16_t)(((uint16_t)rec[7] << 8) | rec[8]);
    out->window_end_min = (uint16_t)(((uint16_t)rec[9] << 8) | rec[10]);
    memcpy(out->filename, &rec[11], FILM_PIN_FILENAME_MAX);
    /* 强制终止：记录可能被外部工具写坏成 32 个非 0 字节，那样 filename 不是字符串。 */
    out->filename[FILM_PIN_FILENAME_MAX - 1] = '\0';

    /* 内容也要过同一份判据。一条布局正确但内容非法的记录（例如后来被禁掉的关键字名）
     * 同样按「未设置」处理 —— 宁可退回默认菜单行为，也不按一份自己都判为非法的配置
     * 去选图。 */
    return film_pin_cfg_is_valid(out);
}

bool service_param_pinned_load(film_pin_cfg_t *out)
{
    if (out == NULL)
    {
        return false;
    }
    /* 先给出确定的「未设置」结果，后面任何一步失败都可以直接 return false。 */
    memset(out, 0, sizeof(*out));
    out->mode = (uint8_t)FILM_PIN_OFF;

    nvs_handle_t handle;
    /* NVS_READONLY：namespace 还不存在时会返回 ESP_ERR_NVS_NOT_FOUND，这只说明
     * 「本设备还没写过任何参数」，不是错误，不打 error 日志。 */
    esp_err_t err = nvs_open(SYS_M_NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK)
    {
        return false;
    }

    uint8_t rec[PARAM_PINNED_REC_LEN];
    size_t rec_len = sizeof(rec);
    /* 记录比缓冲长时 nvs_get_blob 返回 ESP_ERR_NVS_INVALID_LENGTH 而不写越界，
     * 正好落进「布局不符 -> 未设置」。 */
    err = nvs_get_blob(handle, SYS_M_NVS_PINNED_KEY_NAME, rec, &rec_len);
    nvs_close(handle);

    if (err != ESP_OK)
    {
        return false;   /* 不存在 / 长度不符 —— 未设置，不重置任何别的东西 */
    }

    if (!param_pinned_decode(rec, rec_len, out))
    {
        sys_logw(PARAM_TAG, "pinned record rejected (bad layout/content) -> treated as unset");
        memset(out, 0, sizeof(*out));
        out->mode = (uint8_t)FILM_PIN_OFF;
        return false;
    }
    return true;
}

bool service_param_pinned_save(const film_pin_cfg_t *cfg)
{
    /* 全有或全无：非法配置一个字节都不写。这里再校验一遍而不是只信 BLE 路径，
     * 是因为本函数不止一个调用方（BLE 0x4C 之外，将来的本地 UI / 测试夹具也可能写）。 */
    if (!film_pin_cfg_is_valid(cfg))
    {
        return false;
    }

    nvs_handle_t handle;
    esp_err_t err = nvs_open(SYS_M_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK)
    {
        return false;
    }

    uint8_t rec[PARAM_PINNED_REC_LEN];
    param_pinned_encode(cfg, rec);

    err = nvs_set_blob(handle, SYS_M_NVS_PINNED_KEY_NAME, rec, sizeof(rec));
    if (err == ESP_OK)
    {
        err = nvs_commit(handle);
    }
    nvs_close(handle);

    if (err != ESP_OK)
    {
        sys_loge(PARAM_TAG, "pinned record save failed: %d", (int)err);
        return false;
    }
    sys_logi(PARAM_TAG, "pinned record saved: mode=%u window=[%u,%u) name=%s",
             (unsigned)cfg->mode, (unsigned)cfg->window_start_min,
             (unsigned)cfg->window_end_min, cfg->filename);
    return true;
}

void service_param_pinned_erase(void)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(SYS_M_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK)
    {
        return;
    }
    err = nvs_erase_key(handle, SYS_M_NVS_PINNED_KEY_NAME);
    if (err == ESP_OK)
    {
        (void)nvs_commit(handle);
        sys_logi(PARAM_TAG, "pinned record erased");
    }
    /* ESP_ERR_NVS_NOT_FOUND 是常态（本来就没 pin 过），不是错误，不打日志。 */
    nvs_close(handle);
}
