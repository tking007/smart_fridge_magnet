#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "param_serialize.h"
#include "poll_interval.h"   /* MONITOR_POLL_INTERVAL_DEFAULT_SEC */

static void put_u16_le(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value & 0xffu);
    dst[1] = (uint8_t)((value >> 8) & 0xffu);
}

static void make_v2_record(const ServiceParam_Def_t *param,
                           uint8_t out[PARAM_SERIALIZE_LEN_V2])
{
    uint8_t current[PARAM_SERIALIZE_LEN];
    size_t current_len = 0;
    assert(param_serialize(param, current, sizeof(current), &current_len) == PARAM_SER_OK);
    assert(current_len == PARAM_SERIALIZE_LEN);

    const size_t wake_offset = PARAM_SERIALIZE_HEADER_LEN
        + 1u + 4u                    /* factory + config version */
        + 4u + 1u + 1u              /* film */
        + 1u + 1u + 2u;             /* v2 sleep fields */

    memcpy(out, current, wake_offset);
    memcpy(out + wake_offset,
           current + wake_offset + PARAM_SERIALIZE_WAKE_FIELDS_LEN,
           PARAM_SERIALIZE_LEN - wake_offset - PARAM_SERIALIZE_WAKE_FIELDS_LEN);
    put_u16_le(out + 6u, (uint16_t)PARAM_SERIALIZE_BODY_LEN_V2);
}

int main(void)
{
    ServiceParam_Def_t old = {0};
    old.factory_flag = 0x22u;
    old.config_version = 2u;
    old.film.current_file_id = 17u;
    old.film.load_complete = 1u;
    old.film.play_mode = 2u;
    old.sleep.sleep_mode = 1u;
    old.sleep.sleep_auto = 1u;
    old.sleep.sleep_time = 45u;
    old.network.wifi_enable = 1u;
    snprintf(old.network.wifi_ssid, sizeof(old.network.wifi_ssid), "migration-wifi");
    snprintf(old.network.film_api_url, sizeof(old.network.film_api_url),
             "https://api.example/film");
    snprintf(old.network.manifest_url, sizeof(old.network.manifest_url),
             "https://api.example/devices/kitchen/display/manifest");
    snprintf(old.network.device_id, sizeof(old.network.device_id), "kitchen");
    old.sync.poll_interval_seconds = 900u;
    old.sync.last_applied_version = 42u;
    snprintf(old.sync.last_sync_error, sizeof(old.sync.last_sync_error), "previous timeout");
    old.secrets.wifi_password_len = 23u;
    old.secrets.device_token_len = 47u;
    for (size_t i = 0; i < sizeof(old.secrets.wifi_password_enc); i++) {
        old.secrets.wifi_password_enc[i] = (uint8_t)(0x20u + i);
    }
    for (size_t i = 0; i < sizeof(old.secrets.device_token_enc); i++) {
        old.secrets.device_token_enc[i] = (uint8_t)(0x80u + i);
    }

    uint8_t record[PARAM_SERIALIZE_LEN_V2];
    make_v2_record(&old, record);

    ServiceParam_Def_t restored;
    assert(param_deserialize(record, sizeof(record), &restored) == PARAM_SER_OK);
    assert(restored.config_version == 2u);
    assert(strcmp(restored.network.wifi_ssid, old.network.wifi_ssid) == 0);
    assert(strcmp(restored.network.manifest_url, old.network.manifest_url) == 0);
    assert(strcmp(restored.network.device_id, old.network.device_id) == 0);
    assert(restored.network.wifi_password[0] == '\0');
    assert(restored.network.device_token[0] == '\0');
    assert(restored.sync.poll_interval_seconds == old.sync.poll_interval_seconds);
    assert(restored.sync.last_applied_version == old.sync.last_applied_version);
    assert(restored.secrets.wifi_password_len == old.secrets.wifi_password_len);
    assert(restored.secrets.device_token_len == old.secrets.device_token_len);
    assert(memcmp(restored.secrets.wifi_password_enc,
                  old.secrets.wifi_password_enc,
                  sizeof(old.secrets.wifi_password_enc)) == 0);
    assert(memcmp(restored.secrets.device_token_enc,
                  old.secrets.device_token_enc,
                  sizeof(old.secrets.device_token_enc)) == 0);

    assert(param_migrate(&restored));
    assert(restored.config_version == SFM_PARAM_CONFIG_VERSION);
    /* v5 起出厂唤醒策略是「三餐前各一次」，v2 记录没有唤醒字段，直接补当前出厂值。 */
    assert(restored.sleep.wake_slot_count == SFM_PARAM_DEFAULT_WAKE_SLOT_COUNT);
    assert(restored.sleep.wake_slots[0] == SFM_PARAM_DEFAULT_WAKE_SLOT_1_MIN);
    assert(restored.sleep.wake_slots[1] == SFM_PARAM_DEFAULT_WAKE_SLOT_2_MIN);
    assert(restored.sleep.wake_slots[2] == SFM_PARAM_DEFAULT_WAKE_SLOT_3_MIN);
    assert(restored.sleep.wake_fallback_min == SFM_PARAM_DEFAULT_WAKE_FALLBACK_MIN);
    assert(strcmp(restored.network.wifi_ssid, old.network.wifi_ssid) == 0);
    assert(strcmp(restored.network.manifest_url, old.network.manifest_url) == 0);
    assert(restored.sync.last_applied_version == old.sync.last_applied_version);
    assert(memcmp(restored.secrets.device_token_enc,
                  old.secrets.device_token_enc,
                  sizeof(old.secrets.device_token_enc)) == 0);

    ServiceParam_Def_t legacy_v3 = {0};
    legacy_v3.config_version = 3u;
    legacy_v3.sleep.wake_fallback_min = SFM_PARAM_LEGACY_WAKE_FALLBACK_MIN;
    assert(param_migrate(&legacy_v3));
    assert(legacy_v3.sleep.wake_slot_count == SFM_PARAM_DEFAULT_WAKE_SLOT_COUNT);
    assert(legacy_v3.sleep.wake_slots[0] == SFM_PARAM_DEFAULT_WAKE_SLOT_1_MIN);
    assert(legacy_v3.sleep.wake_fallback_min == SFM_PARAM_DEFAULT_WAKE_FALLBACK_MIN);

    ServiceParam_Def_t customized_v3 = {0};
    customized_v3.config_version = 3u;
    customized_v3.sleep.wake_fallback_min = 0u;
    assert(param_migrate(&customized_v3));
    assert(customized_v3.sleep.wake_slot_count == 0u);
    assert(customized_v3.sleep.wake_fallback_min == 0u);

    /* v4 的出厂组合（单槽 06:30）应迁到三餐槽：留在旧默认上会让设备每天只在 06:30
     * 取一次图，与后端三餐发布时刻脱节，而脱节表现成「菜单是昨天的」，日志里看不出。 */
    ServiceParam_Def_t factory_v4 = {0};
    factory_v4.config_version = 4u;
    factory_v4.sleep.wake_slot_count = 1u;
    factory_v4.sleep.wake_slots[0] = SFM_PARAM_LEGACY_WAKE_SLOT_MIN;
    factory_v4.sleep.wake_fallback_min = SFM_PARAM_DEFAULT_WAKE_FALLBACK_MIN;
    assert(param_migrate(&factory_v4));
    assert(factory_v4.config_version == SFM_PARAM_CONFIG_VERSION);
    assert(factory_v4.sleep.wake_slot_count == SFM_PARAM_DEFAULT_WAKE_SLOT_COUNT);
    assert(factory_v4.sleep.wake_slots[0] == SFM_PARAM_DEFAULT_WAKE_SLOT_1_MIN);
    assert(factory_v4.sleep.wake_slots[2] == SFM_PARAM_DEFAULT_WAKE_SLOT_3_MIN);

    /* 用户自己排过唤醒时间的 v4 记录必须原样保留：迁移只认得出的出厂组合，
     * 覆盖用户排班等于替他改了作息，而他不会知道是升级动的。 */
    ServiceParam_Def_t customized_v4 = {0};
    customized_v4.config_version = 4u;
    customized_v4.sleep.wake_slot_count = 2u;
    customized_v4.sleep.wake_slots[0] = 500u;
    customized_v4.sleep.wake_slots[1] = 900u;
    customized_v4.sleep.wake_fallback_min = 720u;
    assert(param_migrate(&customized_v4));
    assert(customized_v4.config_version == SFM_PARAM_CONFIG_VERSION);
    assert(customized_v4.sleep.wake_slot_count == 2u);
    assert(customized_v4.sleep.wake_slots[0] == 500u);
    assert(customized_v4.sleep.wake_slots[1] == 900u);
    assert(customized_v4.sleep.wake_fallback_min == 720u);

    /* v5 的出厂轮询周期（60 秒）应迁到 24 小时：留在旧值上会让升级后的设备继续按
     * 分钟级轮询，而这一版的续航模型是一天只成功联网一次。 */
    ServiceParam_Def_t factory_v5 = {0};
    factory_v5.config_version = 5u;
    factory_v5.sync.poll_interval_seconds = SFM_PARAM_LEGACY_POLL_INTERVAL_SEC;
    sfm_param_apply_default_wake_slots(&factory_v5.sleep);
    assert(param_migrate(&factory_v5));
    assert(factory_v5.config_version == SFM_PARAM_CONFIG_VERSION);
    assert(factory_v5.sync.poll_interval_seconds == MONITOR_POLL_INTERVAL_DEFAULT_SEC);

    /* 用户自己设过轮询周期的 v5 记录必须原样保留。 */
    ServiceParam_Def_t customized_v5 = {0};
    customized_v5.config_version = 5u;
    customized_v5.sync.poll_interval_seconds = 1800u;
    sfm_param_apply_default_wake_slots(&customized_v5.sleep);
    assert(param_migrate(&customized_v5));
    assert(customized_v5.config_version == SFM_PARAM_CONFIG_VERSION);
    assert(customized_v5.sync.poll_interval_seconds == 1800u);

    puts("param v2/v3/v4/v5 -> v6 migration tests passed");
    return 0;
}
