/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/service_wifi_ota.c
 * Description: 见 service_wifi_ota.h。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_https_ota.h"
#include "esp_http_client.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "psa/crypto.h"

#include "sys_log.h"
#include "service_param.h"
#include "service_wifi_ota.h"

/*********************************************************************
 * CONSTANTS
 */
#define OTA_TAG   "wifi_ota"

/* HTTPS 下载超时。整片固件近 2MB，家用上行不稳时单次读取可能停顿数秒，
 * 给 30s 而不是同步流程那种更短的值 —— 超时重启一次 OTA 的代价远高于多等一会。 */
#define SFM_OTA_HTTP_TIMEOUT_MS   (30000)
#define SFM_OTA_HASH_CHUNK_BYTES  (2048u)

/*********************************************************************
 * LOCAL FUNCTIONS
 */

static esp_err_t ota_http_client_init(esp_http_client_handle_t client)
{
    char auth[SFM_DEVICE_TOKEN_MAX + 16];
    int n = snprintf(auth, sizeof(auth), "Bearer %s",
                     g_service_param.network.device_token);
    if(n < 0 || n >= (int)sizeof(auth))
    {
        memset(auth, 0, sizeof(auth));
        return ESP_ERR_INVALID_SIZE;
    }

    esp_err_t err = esp_http_client_set_header(client, "Authorization", auth);
    memset(auth, 0, sizeof(auth));
    return err;
}

static bool ota_sha256_from_hex(const char *hex, uint8_t out[32])
{
    if(hex == NULL || strlen(hex) != 64u)
    {
        return false;
    }

    for(size_t i = 0; i < 32u; ++i)
    {
        char pair[3] = {hex[i * 2u], hex[i * 2u + 1u], '\0'};
        char *end = NULL;
        unsigned long value = strtoul(pair, &end, 16);
        if(end == NULL || *end != '\0' || value > 0xffu)
        {
            return false;
        }
        out[i] = (uint8_t)value;
    }
    return true;
}

static esp_err_t ota_partition_sha256(const esp_partition_t *partition,
                                      size_t image_size, uint8_t out[32])
{
    uint8_t *buffer = malloc(SFM_OTA_HASH_CHUNK_BYTES);
    if(buffer == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    psa_status_t status = psa_crypto_init();
    psa_hash_operation_t operation = PSA_HASH_OPERATION_INIT;
    if(status == PSA_SUCCESS)
    {
        status = psa_hash_setup(&operation, PSA_ALG_SHA_256);
    }

    for(size_t offset = 0; status == PSA_SUCCESS && offset < image_size;)
    {
        size_t chunk = image_size - offset;
        if(chunk > SFM_OTA_HASH_CHUNK_BYTES)
        {
            chunk = SFM_OTA_HASH_CHUNK_BYTES;
        }
        esp_err_t err = esp_partition_read(partition, offset, buffer, chunk);
        if(err != ESP_OK)
        {
            (void)psa_hash_abort(&operation);
            memset(buffer, 0, SFM_OTA_HASH_CHUNK_BYTES);
            free(buffer);
            return err;
        }
        status = psa_hash_update(&operation, buffer, chunk);
        offset += chunk;
    }

    size_t output_length = 0;
    if(status == PSA_SUCCESS)
    {
        status = psa_hash_finish(&operation, out, 32u, &output_length);
        if(status != PSA_SUCCESS)
        {
            (void)psa_hash_abort(&operation);
        }
    }
    else
    {
        (void)psa_hash_abort(&operation);
    }

    memset(buffer, 0, SFM_OTA_HASH_CHUNK_BYTES);
    free(buffer);
    return status == PSA_SUCCESS && output_length == 32u ? ESP_OK : ESP_FAIL;
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

void service_wifi_ota_confirm_running_image(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;

    if(running == NULL || esp_ota_get_state_partition(running, &state) != ESP_OK)
    {
        return;
    }

    if(state != ESP_OTA_IMG_PENDING_VERIFY)
    {
        return;   /* 已确认过，或本来就不是 OTA 刷进来的镜像 */
    }

    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if(err == ESP_OK)
    {
        sys_logi(OTA_TAG, "running image confirmed valid, rollback cancelled (%s)",
                 running->label);
    }
    else
    {
        /* 确认失败不是致命错误：下次启动会回滚到上一个可用镜像，那是安全方向。 */
        sys_logw(OTA_TAG, "mark_app_valid failed (rc=%d), image stays pending verify",
                 (int)err);
    }
}

esp_err_t service_wifi_ota_perform(const manifest_firmware_t *fw, int battery_pct)
{
    if(fw == NULL || !fw->has_update)
    {
        return ESP_ERR_NOT_FOUND;
    }

    /* 上一次 OTA 还没被确认就再刷一次，等于把「已知能跑的那个分区」也覆盖掉，
     * 回滚就没有落脚点了。先让这一版被确认（下一次唤醒同步成功即确认）。 */
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    if(running != NULL && esp_ota_get_state_partition(running, &state) == ESP_OK &&
       state == ESP_OTA_IMG_PENDING_VERIFY)
    {
        sys_logw(OTA_TAG, "current image still pending verify, skip OTA this wake");
        return ESP_ERR_INVALID_STATE;
    }

    /* 后端已按语义化版本做过新旧门控（见 backend/src/firmware.py）。这里只挡
     * 「同版本重刷」：版本串相同却下发，说明两边对版本的理解不一致，重刷 2MB
     * 不会让设备变好，只会每次唤醒都白耗一次。 */
    const esp_app_desc_t *desc = esp_app_get_description();
    if(desc != NULL && strncmp(desc->version, fw->version, sizeof(desc->version)) == 0)
    {
        sys_logi(OTA_TAG, "firmware %s equals running version, skip", fw->version);
        return ESP_ERR_INVALID_VERSION;
    }

    /* 电量读不到时跳过而不是赌一把：OTA 是本设备最耗电的单次操作。 */
    if(battery_pct < 0)
    {
        sys_logw(OTA_TAG, "battery unknown, skip OTA");
        return ESP_ERR_INVALID_STATE;
    }
    if(battery_pct < SFM_OTA_MIN_BATTERY_PCT)
    {
        sys_logi(OTA_TAG, "battery %d%% < %d%%, skip OTA this wake",
                 battery_pct, SFM_OTA_MIN_BATTERY_PCT);
        return ESP_ERR_INVALID_STATE;
    }

    if(strncmp(fw->url, "https://", 8) != 0)
    {
        /* manifest 解析层已经拒过 http，这里是第二道：URL 会被拿去发送 device_token。 */
        sys_loge(OTA_TAG, "firmware url not https -> abort");
        return ESP_ERR_INVALID_ARG;
    }
    if(g_service_param.network.device_token[0] == '\0')
    {
        sys_logw(OTA_TAG, "no device token -> abort OTA");
        return ESP_ERR_INVALID_STATE;
    }

    sys_logi(OTA_TAG, "starting OTA: %s -> %s (%u bytes, battery %d%%)",
             desc ? desc->version : "?", fw->version, (unsigned)fw->size, battery_pct);

    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    if(target == NULL)
    {
        service_param_set_last_sync_error("ota partition missing");
        service_param_save();
        return ESP_ERR_NOT_FOUND;
    }

    esp_http_client_config_t http_cfg = {
        .url               = fw->url,
        .timeout_ms        = SFM_OTA_HTTP_TIMEOUT_MS,
        .transport_type    = HTTP_TRANSPORT_OVER_SSL,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .keep_alive_enable = true,
    };
    esp_https_ota_config_t ota_cfg = {
        .http_config         = &http_cfg,
        .http_client_init_cb = ota_http_client_init,
        .partition.staging   = target,
    };

    esp_https_ota_handle_t handle = NULL;
    esp_err_t err = esp_https_ota_begin(&ota_cfg, &handle);
    if(err != ESP_OK || handle == NULL)
    {
        sys_loge(OTA_TAG, "esp_https_ota_begin failed (rc=%d)", (int)err);
        service_param_set_last_sync_error("ota begin failed");
        service_param_save();
        return err == ESP_OK ? ESP_FAIL : err;
    }

    esp_app_desc_t candidate = {0};
    err = esp_https_ota_get_img_desc(handle, &candidate);
    candidate.version[sizeof(candidate.version) - 1u] = '\0';
    if(err != ESP_OK || strncmp(candidate.version, fw->version,
                                sizeof(candidate.version)) != 0)
    {
        sys_loge(OTA_TAG, "ota image version mismatch: manifest=%s image=%s rc=%d",
                 fw->version, candidate.version, (int)err);
        (void)esp_https_ota_abort(handle);
        service_param_set_last_sync_error("ota version mismatch");
        service_param_save();
        return err == ESP_OK ? ESP_ERR_INVALID_VERSION : err;
    }

    /* 逐块拉取。esp_https_ota_perform 每次返回 IN_PROGRESS 表示还有数据，
     * 中间不打进度日志到每一块 —— 串口刷屏会掩盖真正要看的错误行。 */
    int last_logged_pct = -1;
    do
    {
        err = esp_https_ota_perform(handle);
        if(err != ESP_ERR_HTTPS_OTA_IN_PROGRESS)
        {
            break;
        }
        int written = esp_https_ota_get_image_len_read(handle);
        if(fw->size > 0u)
        {
            int pct = (int)((int64_t)written * 100 / (int64_t)fw->size);
            if(pct >= last_logged_pct + 25)
            {
                sys_logi(OTA_TAG, "ota progress %d%% (%d/%u)",
                         pct, written, (unsigned)fw->size);
                last_logged_pct = pct;
            }
        }
    } while(true);

    if(err != ESP_OK)
    {
        sys_loge(OTA_TAG, "ota download failed (rc=%d)", (int)err);
        (void)esp_https_ota_abort(handle);
        service_param_set_last_sync_error("ota download failed");
        service_param_save();
        return err;
    }

    if(!esp_https_ota_is_complete_data_received(handle))
    {
        /* 长度不足却没报错：宁可放弃也不能把半截镜像设为启动分区。 */
        sys_loge(OTA_TAG, "ota image incomplete -> abort");
        (void)esp_https_ota_abort(handle);
        service_param_set_last_sync_error("ota image incomplete");
        service_param_save();
        return ESP_ERR_INVALID_SIZE;
    }

    int image_size = esp_https_ota_get_image_len_read(handle);
    if(image_size < 0 || (uint32_t)image_size != fw->size)
    {
        sys_loge(OTA_TAG, "ota size mismatch: manifest=%u downloaded=%d",
                 (unsigned)fw->size, image_size);
        (void)esp_https_ota_abort(handle);
        service_param_set_last_sync_error("ota size mismatch");
        service_param_save();
        return ESP_ERR_INVALID_SIZE;
    }

    uint8_t expected_sha256[32] = {0};
    uint8_t actual_sha256[32] = {0};
    if(!ota_sha256_from_hex(fw->sha256_hex, expected_sha256) ||
       ota_partition_sha256(target, (size_t)image_size, actual_sha256) != ESP_OK ||
       memcmp(expected_sha256, actual_sha256, sizeof(expected_sha256)) != 0)
    {
        memset(expected_sha256, 0, sizeof(expected_sha256));
        memset(actual_sha256, 0, sizeof(actual_sha256));
        sys_loge(OTA_TAG, "ota sha256 mismatch -> abort");
        (void)esp_https_ota_abort(handle);
        service_param_set_last_sync_error("ota sha256 mismatch");
        service_param_save();
        return ESP_ERR_INVALID_CRC;
    }
    memset(expected_sha256, 0, sizeof(expected_sha256));
    memset(actual_sha256, 0, sizeof(actual_sha256));

    err = esp_https_ota_finish(handle);
    if(err != ESP_OK)
    {
        /* ESP_ERR_OTA_VALIDATE_FAILED 表示镜像头校验没过，说明下发的不是本芯片的固件。 */
        sys_loge(OTA_TAG, "esp_https_ota_finish failed (rc=%d)", (int)err);
        service_param_set_last_sync_error("ota finish failed");
        service_param_save();
        return err;
    }

    /* 走到这里启动分区已切换。重启后新镜像处于 PENDING_VERIFY，必须在那次运行内
     * 由 service_wifi_ota_confirm_running_image() 确认，否则再下次启动自动回滚。 */
    service_param_set_last_sync_error(NULL);
    service_param_save();
    sys_logi(OTA_TAG, "ota done, rebooting into %s", fw->version);
    esp_restart();

    return ESP_OK;   /* 不会执行到 */
}
