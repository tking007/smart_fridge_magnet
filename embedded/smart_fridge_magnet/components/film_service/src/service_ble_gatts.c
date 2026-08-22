/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_service/src/service_ble_gatts.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/5
 * Description: ble gatt server服务
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_bt.h"

#include "esp_gap_ble_api.h"
#include "esp_gatts_api.h"
#include "esp_bt_defs.h"
#include "esp_bt_main.h"
#include "esp_gatt_common_api.h"
#include "sdkconfig.h"

#include "sys_log.h"
#include "service_ble_gatts.h"

/*********************************************************************
 * MACROS
 */
// #define CONFIG_SET_RAW_ADV_DATA

#define GATTS_TAG "GATTS_SERVICE"

#define TEST_MANUFACTURER_DATA_LEN        6

#define PROFILE_NUM                       2
#define PROFILE_A_APP_ID                  0
#define PROFILE_B_APP_ID                  1

/* 属性值长度上限，同时作为 esp_ble_gatt_set_local_mtu() 的本地 MTU 上限。
 *
 * 原值 200 是吞吐的第二个瓶颈：本地 MTU 设成 200，中心即便想协商更大也被卡在 200，
 * 单帧最多只能搬 180 字节 FILE_DATA（200 - 3 ATT 头 - 4 协议帧开销 ≈ 193，取整 180）。
 * 实测 conn_int 已协商到 15ms，而带响应写「一帧占两个连接事件」= 30ms/帧，
 * 于是速率被钉死在 180B/30ms ≈ 5.7 KB/s（209120 字节要 36s）。
 * 放开到 512 后单帧可搬 250 字节，同样的 30ms/帧 → 约 7.9 KB/s、约 25s。
 *
 * 为什么是 512 而不是更大：协议帧的 LEN 字段只有一个字节，数据段上限 255，
 * 整帧上限 4 + 255 = 259 字节，需要 ATT MTU ≥ 262 —— 512 已有充足余量，
 * 再大也用不上（要用满需改帧格式，属跨端破坏性变更）。
 *
 * RAM 代价可忽略：属性表里这些条目都是 length=0 / value=NULL（由 app 响应），
 * 协议栈不为 max_length 预分配缓冲。 */
#define GATTS_CHAR_VAL_LEN_MAX            512
#define CHAR_DECLARATION_SIZE             (sizeof(uint8_t))
#define SFM_CONTROL_PERM                  (ESP_GATT_PERM_READ_ENCRYPTED | \
                                           ESP_GATT_PERM_WRITE_ENCRYPTED)
#define SFM_SERIAL_NUMBER_LEN              (3u + 8u)


/*********************************************************************
 * TYPEDEFS
 */
typedef struct
{
    uint8_t *prepare_buf;
    int prepare_len;
} prepare_type_env_t;


/*********************************************************************
 * CONSTANTS
 */
static const uint16_t character_client_config_uuid     = ESP_GATT_UUID_CHAR_CLIENT_CONFIG;
static const uint16_t primary_service_uuid             = ESP_GATT_UUID_PRI_SERVICE;
static const uint16_t character_declaration_uuid       = ESP_GATT_UUID_CHAR_DECLARE;
static const uint8_t char_prop_read                    = ESP_GATT_CHAR_PROP_BIT_READ;
static const uint8_t char_prop_notify                  = ESP_GATT_CHAR_PROP_BIT_NOTIFY;
static const uint8_t char_prop_write_notify            = ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_NOTIFY | ESP_GATT_CHAR_PROP_BIT_WRITE_NR;

static const uint8_t m_measurement_ccc[2]              = {0x00, 0x00};

static const uint16_t REBOLOR_SERVICE_UUID             = 0x2000;
static const uint16_t REBOLOR_SERVICE_CHAR1_UUID       = 0x2001;
static const uint16_t REBOLOR_SERVICE_CHAR2_UUID       = 0x2002;
static const uint16_t REBOLOR_SERVICE_CHAR3_UUID       = 0x2003;

static const uint16_t DEVICE_INFO_SERVICE_UUID         = 0x180A;
static const uint16_t DEVICE_NAME_CHAR_UUID            = 0x2A00;
static const uint16_t MANUFACTURER_NAME_CHAR_UUID      = 0x2A29;
static const uint16_t MODEL_NUMBER_CHAR_UUID           = 0x2A24;
static const uint16_t SERIAL_NUMBER_CHAR_UUID          = 0x2A25;
static const uint16_t HARDWARE_REVISION_CHAR_UUID      = 0x2A27;
static const uint16_t FIRMWARE_REVISION_CHAR_UUID      = 0x2A26;
static const uint16_t SYSTEM_ID_CHAR_UUID              = 0x2A23;

static char m_serial_number[SFM_SERIAL_NUMBER_LEN + 1u] = SYS_SERIAL_NUMBER_PREFIX "00000000";

static const esp_gatts_attr_db_t m_service_gatt_db[DEV_M_IDX_NB] =
{
    // Service Declaration
    [IDX_M_SVC]     =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &primary_service_uuid, ESP_GATT_PERM_READ,
            sizeof(uint16_t), sizeof(REBOLOR_SERVICE_UUID), (uint8_t *) &REBOLOR_SERVICE_UUID
        }
    },

    /* Characteristic Declaration */
    [IDX_M_CHAR_1]     =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_write_notify
        }
    },

    /* Characteristic Value */
    [IDX_M_CHAR_VAL_1] =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &REBOLOR_SERVICE_CHAR1_UUID, SFM_CONTROL_PERM,
            GATTS_CHAR_VAL_LEN_MAX, 0, NULL
        }
    },

    /* Client Characteristic Configuration Descriptor */
    [IDX_M_CHAR_CFG_1]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_client_config_uuid, SFM_CONTROL_PERM,
            sizeof(uint16_t), sizeof(m_measurement_ccc), (uint8_t *)m_measurement_ccc
        }
    },

    /* Characteristic Declaration */
    [IDX_M_CHAR_2]     =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_notify
        }
    },

    /* Characteristic Value */
    [IDX_M_CHAR_VAL_2] =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &REBOLOR_SERVICE_CHAR2_UUID, SFM_CONTROL_PERM,
            GATTS_CHAR_VAL_LEN_MAX, 0, NULL
        }
    },

    /* Client Characteristic Configuration Descriptor */
    [IDX_M_CHAR_CFG_2]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_client_config_uuid, SFM_CONTROL_PERM,
            sizeof(uint16_t), sizeof(m_measurement_ccc), (uint8_t *)m_measurement_ccc
        }
    },

    /* Characteristic Declaration */
    [IDX_M_CHAR_3]     =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_notify
        }
    },

    /* Characteristic Value */
    [IDX_M_CHAR_VAL_3] =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &REBOLOR_SERVICE_CHAR3_UUID, SFM_CONTROL_PERM,
            GATTS_CHAR_VAL_LEN_MAX, 0, NULL
        }
    },

    /* Client Characteristic Configuration Descriptor */
    [IDX_M_CHAR_CFG_3]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_client_config_uuid, SFM_CONTROL_PERM,
            sizeof(uint16_t), sizeof(m_measurement_ccc), (uint8_t *)m_measurement_ccc
        }
    },

};

static const esp_gatts_attr_db_t dev_info_gatt_db[DEV_IDX_NB] =
{
    // Service Declaration
    [IDX_SVC_DEV]     =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &primary_service_uuid, ESP_GATT_PERM_READ,
            sizeof(uint16_t), sizeof(DEVICE_INFO_SERVICE_UUID), (uint8_t *) &DEVICE_INFO_SERVICE_UUID
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_1]     =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_1] =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &DEVICE_NAME_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(SYS_DEVICE_NAME), (uint8_t *)SYS_DEVICE_NAME
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_2]      =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_2]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &MANUFACTURER_NAME_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(SYS_MANUFACTURER_NAME), (uint8_t *)SYS_MANUFACTURER_NAME
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_3]      =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_3]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &MODEL_NUMBER_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(SYS_MODEL_NUMBER), (uint8_t *)SYS_MODEL_NUMBER
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_4]      =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_4]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &SERIAL_NUMBER_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(m_serial_number), (uint8_t *)m_serial_number
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_5]      =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_5]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &HARDWARE_REVISION_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(SYS_HAREWARE_VERSION), (uint8_t *)SYS_HAREWARE_VERSION
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_6]      =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_6]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &FIRMWARE_REVISION_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(SYS_FIRMWARE_VERSION), (uint8_t *)SYS_FIRMWARE_VERSION
        }
    },

    /* Characteristic Declaration */
    [IDX_CHAR_7]      =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &character_declaration_uuid, ESP_GATT_PERM_READ,
            CHAR_DECLARATION_SIZE, CHAR_DECLARATION_SIZE, (uint8_t *) &char_prop_read
        }
    },

    /* Characteristic Value */
    [IDX_CHAR_VAL_7]  =
    {   {ESP_GATT_AUTO_RSP}, {
            ESP_UUID_LEN_16, (uint8_t *) &SYSTEM_ID_CHAR_UUID, ESP_GATT_PERM_READ,
            GATTS_CHAR_VAL_LEN_MAX, sizeof(SYS_SYSTEM_ID), (uint8_t *)SYS_SYSTEM_ID
        }
    },
};


/*********************************************************************
 * LOCAL VARIABLES
 */
static uint16_t m_service_handle_table[DEV_M_IDX_NB];

static service_ble_gatts_cmd_cb_t m_gatts_cmd_cb = NULL;
static service_ble_gatts_disconnect_cb_t m_gatts_disconnect_cb = NULL;

static esp_ble_conn_update_params_t conn_params = {0};

static uint8_t adv_config_done = 0;

static uint8_t ble_gatts_notify_mask = 0;
static uint8_t ble_gatt_mode = BLE_GATT_MODE_S;
static uint8_t ble_gatts_init = BLE_GATTS_UNINIT;
static uint8_t ble_gatts_connect = BLE_GATTS_DISCONNECT;
/* 断连后是否自动恢复广播。默认 true 保持上游行为（也让休眠关闭时的调试体验不变）；
 * 省电模式下由 service_ble_init 关掉，改由可连接窗口决定何时广播。 */
static bool m_adv_auto_restart = true;
static uint8_t sys_ble_mac[6] = {0};

#ifdef CONFIG_SET_RAW_ADV_DATA
static uint8_t raw_adv_data[] =
{
    0x02, 0x01, 0x06,
    0x02, 0x0a, 0xeb, 0x03, 0x03, 0xab, 0xcd
};
static uint8_t raw_scan_rsp_data[] =
{
    0x0f, 0x09, 0x45, 0x53, 0x50, 0x5f, 0x47, 0x41, 0x54, 0x54, 0x53, 0x5f, 0x44,
    0x45, 0x4d, 0x4f
};
#else

static uint8_t adv_service_uuid128[16] =
{
    /* LSB <--------------------------------------------------------------------------------> MSB */
    //first uuid, 16bit, [12],[13] is the value
    0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00,  /* 16bit 值 = 0x2000，低字节在前 */
};

// The length of adv data must be less than 31 bytes
//adv data
static esp_ble_adv_data_t adv_data =
{
    .set_scan_rsp = false,
    .include_name = true,
    .include_txpower = false,
    .min_interval = BLE_DEFAULT_MIN_INT, //slave connection min interval, Time = min_interval * 1.25 msec (7.5ms)
    .max_interval = BLE_DEFAULT_MAX_INT, //slave connection max interval, Time = max_interval * 1.25 msec (15ms)
    .appearance = 0x00,
    /* 广播只放 flags + 完整设备名：31 字节广播包放不下「128bit service UUID + 厂商MAC + 中文名」，
     * 优先保证中文名完整（UTF-8 每字 3 字节，"智能冰箱贴"=21 字节）。service UUID 移到 scan response。 */
    .manufacturer_len = 0,
    .p_manufacturer_data =  NULL,
    .service_data_len = 0,
    .p_service_data = NULL,
    .service_uuid_len = 0,
    .p_service_uuid = NULL,
    .flag = (ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT),
};
// scan response data
static esp_ble_adv_data_t scan_rsp_data =
{
    .set_scan_rsp = true,
    .include_name = false,   /* 名字已在广播包给全；scan response 放正确的 service UUID(0x2000) + txpower */
    .include_txpower = true,
    //.min_interval = 0x0006,
    //.max_interval = 0x0010,
    .appearance = 0x00,
    .manufacturer_len = 0, //TEST_MANUFACTURER_DATA_LEN,
    .p_manufacturer_data =  NULL, //&test_manufacturer[0],
    .service_data_len = 0,
    .p_service_data = NULL,
    .service_uuid_len = sizeof(adv_service_uuid128),
    .p_service_uuid = adv_service_uuid128,
    .flag = (ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT),
};

#endif /* CONFIG_SET_RAW_ADV_DATA */

static esp_ble_adv_params_t adv_params =
{
    .adv_int_min        = BLE_ADV_MIN_INT,
    .adv_int_max        = BLE_ADV_MAX_INT,
    .adv_type           = ADV_TYPE_IND,
    .own_addr_type      = BLE_ADDR_TYPE_PUBLIC,
    //.peer_addr            =
    //.peer_addr_type       =
    .channel_map        = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

struct gatts_profile_inst
{
    esp_gatts_cb_t gatts_cb;
    uint16_t gatts_if;
    uint16_t app_id;
    uint16_t conn_id;
    uint16_t service_handle;
    esp_gatt_srvc_id_t service_id;
    uint16_t char_handle;
    esp_bt_uuid_t char_uuid;
    esp_gatt_perm_t perm;
    esp_gatt_char_prop_t property;
    uint16_t descr_handle;
    esp_bt_uuid_t descr_uuid;
};

static void esp_gap_cb(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param);
static void gatts_profile_a_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param);
static void gatts_profile_b_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param);
static esp_err_t ble_security_configure(void);

/* One gatt-based profile one app_id and one gatts_if, this array will store the gatts_if returned by ESP_GATTS_REG_EVT */
static struct gatts_profile_inst gl_profile_tab[PROFILE_NUM] =
{
    [PROFILE_A_APP_ID] = {
        .gatts_cb = gatts_profile_a_event_handler,
        .gatts_if = ESP_GATT_IF_NONE,       /* Not get the gatt_if, so initial is ESP_GATT_IF_NONE */
    },
    [PROFILE_B_APP_ID] = {
        .gatts_cb = gatts_profile_b_event_handler,                   /* This demo does not implement, similar as profile A */
        .gatts_if = ESP_GATT_IF_NONE,       /* Not get the gatt_if, so initial is ESP_GATT_IF_NONE */
    },
};

static prepare_type_env_t a_prepare_write_env;


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void dev_exec_write_event_env(prepare_type_env_t *prepare_write_env, esp_ble_gatts_cb_param_t *param);
static void gatts_notift_bit_set(uint16_t ret, uint8_t ch_bit);

static esp_err_t ble_security_configure(void)
{
    esp_ble_auth_req_t auth_req = ESP_LE_AUTH_REQ_SC_BOND;
    esp_ble_io_cap_t io_cap = ESP_IO_CAP_NONE;
    uint8_t key_size = 16;
    uint8_t init_key = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    uint8_t response_key = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
    struct {
        esp_ble_sm_param_t id;
        void *value;
        uint8_t len;
    } params[] = {
        {ESP_BLE_SM_AUTHEN_REQ_MODE, &auth_req, sizeof(auth_req)},
        {ESP_BLE_SM_IOCAP_MODE, &io_cap, sizeof(io_cap)},
        {ESP_BLE_SM_MAX_KEY_SIZE, &key_size, sizeof(key_size)},
        {ESP_BLE_SM_SET_INIT_KEY, &init_key, sizeof(init_key)},
        {ESP_BLE_SM_SET_RSP_KEY, &response_key, sizeof(response_key)},
    };

    for(size_t i = 0; i < sizeof(params) / sizeof(params[0]); ++i)
    {
        esp_err_t ret = esp_ble_gap_set_security_param(params[i].id, params[i].value,
                                                        params[i].len);
        if(ret != ESP_OK)
        {
            return ret;
        }
    }
    return ESP_OK;
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */


static void dev_exec_write_event_env(prepare_type_env_t *prepare_write_env, esp_ble_gatts_cb_param_t *param)
{
    if (param->exec_write.exec_write_flag == ESP_GATT_PREP_WRITE_EXEC)
    {
    }
    else
    {
        sys_logi(GATTS_TAG, "ESP_GATT_PREP_WRITE_CANCEL");
    }
    if (prepare_write_env->prepare_buf)
    {
        free(prepare_write_env->prepare_buf);
        prepare_write_env->prepare_buf = NULL;
    }
    prepare_write_env->prepare_len = 0;
}

static void gatts_notift_bit_set(uint16_t ret, uint8_t ch_bit)
{
    if(ret == 0x0000)
    {
        ble_gatts_notify_mask &= ~(ch_bit);
    }
    else if(ret == 0x0001)
    {
        ble_gatts_notify_mask |= (ch_bit);
    }
}


void service_ble_send_notify_data(uint8_t ch, uint8_t *buf, uint16_t len)
{
    if((ch == BLE_NOTIFY_SEND_CH1 || ch == BLE_NOTIFY_SEND_CH2 || ch == BLE_NOTIFY_SEND_CH3) && len > 0)
    {
        if(ble_gatts_connect == BLE_GATTS_CONNECT && ble_gatts_init == BLE_GATTS_INIT)
        {
            if(len <= GATTS_CHAR_VAL_LEN_MAX)
            {
                if(ch == BLE_NOTIFY_SEND_CH1 && (ble_gatts_notify_mask & GATTS_CH1_NOTIFY_ENABLE_BIT))
                {
                    esp_ble_gatts_send_indicate(gl_profile_tab[PROFILE_A_APP_ID].gatts_if, gl_profile_tab[PROFILE_A_APP_ID].conn_id,  m_service_handle_table[IDX_M_CHAR_VAL_1], len, buf, false);
                }
                else if(ch == BLE_NOTIFY_SEND_CH2 && (ble_gatts_notify_mask & GATTS_CH2_NOTIFY_ENABLE_BIT))
                {
                    esp_ble_gatts_send_indicate(gl_profile_tab[PROFILE_A_APP_ID].gatts_if, gl_profile_tab[PROFILE_A_APP_ID].conn_id,  m_service_handle_table[IDX_M_CHAR_VAL_2], len, buf, false);
                }
                else if(ch == BLE_NOTIFY_SEND_CH3 && (ble_gatts_notify_mask & GATTS_CH3_NOTIFY_ENABLE_BIT))
                {
                    esp_ble_gatts_send_indicate(gl_profile_tab[PROFILE_A_APP_ID].gatts_if, gl_profile_tab[PROFILE_A_APP_ID].conn_id,  m_service_handle_table[IDX_M_CHAR_VAL_3], len, buf, false);
                }
            }
            else
            {
                sys_loge(GATTS_TAG, "notify out of length max %d", GATTS_CHAR_VAL_LEN_MAX);
            }
        }
    }
}

void service_ble_gatts_cmd_register_cb(service_ble_gatts_cmd_cb_t cb)
{
    m_gatts_cmd_cb = cb;
}

void service_ble_gatts_disconnect_register_cb(service_ble_gatts_disconnect_cb_t cb)
{
    m_gatts_disconnect_cb = cb;
}

static void gatts_profile_a_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param)
{
    switch (event)
    {
    case ESP_GATTS_REG_EVT:
        sys_logi(GATTS_TAG, "REGISTER_APP_EVT, status %d, app_id %d", param->reg.status, param->reg.app_id);

        esp_err_t set_dev_name_ret = esp_ble_gap_set_device_name(SYS_DEVICE_NAME);
        if (set_dev_name_ret)
        {
            sys_loge(GATTS_TAG, "set device name failed, error code = %x", set_dev_name_ret);
        }
#ifdef CONFIG_SET_RAW_ADV_DATA
        esp_err_t raw_adv_ret = esp_ble_gap_config_adv_data_raw(raw_adv_data, sizeof(raw_adv_data));
        if (raw_adv_ret)
        {
            sys_loge(GATTS_TAG, "config raw adv data failed, error code = %x ", raw_adv_ret);
        }
        adv_config_done |= ADV_CONGIG_FLAG;
        esp_err_t raw_scan_ret = esp_ble_gap_config_scan_rsp_data_raw(raw_scan_rsp_data, sizeof(raw_scan_rsp_data));
        if (raw_scan_ret)
        {
            sys_loge(GATTS_TAG, "config raw scan rsp data failed, error code = %x", raw_scan_ret);
        }
        adv_config_done |= SCAN_RSP_CONFIG_FLAG;
#else
        //config adv data
        esp_err_t ret = esp_ble_gap_config_adv_data(&adv_data);
        if (ret)
        {
            sys_loge(GATTS_TAG, "config adv data failed, error code = %x", ret);
        }
        adv_config_done |= ADV_CONGIG_FLAG;
        //config scan response data
        ret = esp_ble_gap_config_adv_data(&scan_rsp_data);
        if (ret)
        {
            sys_loge(GATTS_TAG, "config scan response data failed, error code = %x", ret);
        }
        adv_config_done |= SCAN_RSP_CONFIG_FLAG;

#endif
        sys_logi(GATTS_TAG, "REGISTER_APP_EVT_A, status %d, app_id %d, gatts_if %d", param->reg.status, param->reg.app_id, gatts_if);
        esp_err_t create_attr_ret = esp_ble_gatts_create_attr_tab(m_service_gatt_db, gatts_if, DEV_M_IDX_NB, PROFILE_A_APP_ID);
        if (create_attr_ret)
        {
            sys_loge(GATTS_TAG, "create attr a table failed, error code = %x", create_attr_ret);
        }
        break;
    case ESP_GATTS_READ_EVT:
    {
        sys_logi(GATTS_TAG, "GATT_READ_EVT, conn_id %d, trans_id %" PRIu32 ", handle %d", param->read.conn_id, param->read.trans_id, param->read.handle);
        break;
    }
    case ESP_GATTS_WRITE_EVT:
    {
        if (!param->write.is_prep)
        {
            if(param->write.handle == m_service_handle_table[IDX_M_CHAR_CFG_1] && param->write.len == 2)
            {
                uint16_t descr_value = param->write.value[1] << 8 | param->write.value[0];
                gatts_notift_bit_set(descr_value, GATTS_CH1_NOTIFY_ENABLE_BIT);
                if(ble_gatts_notify_mask & GATTS_CH1_NOTIFY_ENABLE_BIT)
                {
                    sys_logi(GATTS_TAG, "notify ch1 enable");
                }
                else
                {
                    sys_logi(GATTS_TAG, "notify ch1 disable");
                }
            }
            else if(param->write.handle == m_service_handle_table[IDX_M_CHAR_CFG_2] && param->write.len == 2)
            {
                uint16_t descr_value = param->write.value[1] << 8 | param->write.value[0];
                gatts_notift_bit_set(descr_value, GATTS_CH2_NOTIFY_ENABLE_BIT);
                if(ble_gatts_notify_mask & GATTS_CH2_NOTIFY_ENABLE_BIT)
                {
                    sys_logi(GATTS_TAG, "notify ch2 enable");
                }
                else
                {
                    sys_logi(GATTS_TAG, "notify ch2 disable");  
                }
            }
            else if(param->write.handle == m_service_handle_table[IDX_M_CHAR_CFG_3] && param->write.len == 2)
            {
                uint16_t descr_value = param->write.value[1] << 8 | param->write.value[0];
                gatts_notift_bit_set(descr_value, GATTS_CH3_NOTIFY_ENABLE_BIT);
                if(ble_gatts_notify_mask & GATTS_CH3_NOTIFY_ENABLE_BIT)
                {
                    sys_logi(GATTS_TAG, "notify ch3 enable");
                }
                else
                {
                    sys_logi(GATTS_TAG, "notify ch3 disable");
                }
            }
            else if(param->write.handle == m_service_handle_table[IDX_M_CHAR_VAL_1])
            {
                if(m_gatts_cmd_cb != NULL)
                {
                    m_gatts_cmd_cb(param->write.value, param->write.len);
                }
            }
            else
            {
                sys_loge(GATTS_TAG, "unknown descr value");
            }
        }
        break;
    }
    case ESP_GATTS_EXEC_WRITE_EVT:
        sys_logi(GATTS_TAG, "ESP_GATTS_EXEC_WRITE_EVT");
        esp_ble_gatts_send_response(gatts_if, param->write.conn_id, param->write.trans_id, ESP_GATT_OK, NULL);
        dev_exec_write_event_env(&a_prepare_write_env, param);
        break;
    case ESP_GATTS_MTU_EVT:

        sys_logi(GATTS_TAG, "ESP_GATTS_MTU_EVT, MTU %d", param->mtu.mtu);
        break;
    case ESP_GATTS_UNREG_EVT:
        break;
    case ESP_GATTS_CREATE_EVT:
        sys_logi(GATTS_TAG, "CREATE_SERVICE_EVT, status %d,  service_handle %d", param->create.status, param->create.service_handle);
        break;
    case ESP_GATTS_ADD_INCL_SRVC_EVT:
        break;
    case ESP_GATTS_ADD_CHAR_EVT:
    {
        break;
    }
    case ESP_GATTS_ADD_CHAR_DESCR_EVT:
        gl_profile_tab[PROFILE_A_APP_ID].descr_handle = param->add_char_descr.attr_handle;
        sys_logi(GATTS_TAG, "ADD_DESCR_EVT, status %d, attr_handle %d, service_handle %d",
                 param->add_char_descr.status, param->add_char_descr.attr_handle, param->add_char_descr.service_handle);
        break;
    case ESP_GATTS_DELETE_EVT:
        break;
    case ESP_GATTS_START_EVT:
        sys_logi(GATTS_TAG, "SERVICE_START_EVT, status %d, service_handle %d",
                 param->start.status, param->start.service_handle);
        break;
    case ESP_GATTS_STOP_EVT:
        break;
    case ESP_GATTS_CONNECT_EVT:
    {
        sys_logi(GATTS_TAG, "link_role:%d", param->connect.link_role);
        if(param->connect.link_role == 1)  //master role = 0  ; slave role = 1
        {
            memcpy(conn_params.bda, param->connect.remote_bda, sizeof(esp_bd_addr_t));
            /* For the IOS system, please reference the apple official documents about the ble connection parameters restrictions. */
            conn_params.latency = BLE_DAFAULT_LATENCY;
            conn_params.max_int = BLE_DEFAULT_MAX_INT;    // max_int = 0x0C*1.25ms = 15ms
            conn_params.min_int = BLE_DEFAULT_MIN_INT;    // min_int = 0x06*1.25ms = 7.5ms
            conn_params.timeout = BLE_DEFAULT_TIMEOUT;    // timeout = 400*10ms = 4000ms
            sys_logi(GATTS_TAG, "ESP_GATTS_CONNECT_EVT, conn_id %d, remote %02x:%02x:%02x:%02x:%02x:%02x:",
                     param->connect.conn_id,
                     param->connect.remote_bda[0], param->connect.remote_bda[1], param->connect.remote_bda[2],
                     param->connect.remote_bda[3], param->connect.remote_bda[4], param->connect.remote_bda[5]);
            gl_profile_tab[PROFILE_A_APP_ID].conn_id = param->connect.conn_id;
            //start sent the update connection parameters to the peer device.
            esp_ble_gap_update_conn_params(&conn_params);
            esp_err_t security_ret = esp_ble_set_encryption(param->connect.remote_bda,
                                                             ESP_BLE_SEC_ENCRYPT);
            if(security_ret != ESP_OK)
            {
                sys_loge(GATTS_TAG, "start BLE encryption failed: %s",
                         esp_err_to_name(security_ret));
            }
            ble_gatts_connect = BLE_GATTS_CONNECT;
            ble_gatts_notify_mask = 0x00;
        }
        break;
    }
    case ESP_GATTS_DISCONNECT_EVT:
    {
        if(memcmp(param->disconnect.remote_bda, conn_params.bda, 6) == 0)
        {
            ble_gatts_connect = BLE_GATTS_DISCONNECT;
            ble_gatts_notify_mask = 0x00;
            sys_logi(GATTS_TAG, "ESP_GATTS_DISCONNECT_EVT, disconnect reason 0x%x", param->disconnect.reason);
            /* Notify the BLE service so it can abort any in-progress file
             * transfer (Req 9.5). Callback must be non-blocking; it only
             * enqueues work for the BLE task. */
            if(m_gatts_disconnect_cb != NULL)
            {
                m_gatts_disconnect_cb();
            }
            /* 断连后是否自动恢复广播，取决于「按需广播」开关。
             *
             * 上游行为是无条件重开广播（断了立刻又可被扫描）。在按需广播模式下这会架空整个
             * 机制：连一次之后设备就永久可见了。故这里改为可配置——省电模式下由可连接窗口
             * 统一决定何时广播（窗口未过期则保持广播，已过期就静默到下次按键唤醒）。 */
            if(m_adv_auto_restart)
            {
                esp_ble_gap_start_advertising(&adv_params);
            }
            else
            {
                sys_logi(GATTS_TAG, "disconnect: adv auto-restart disabled, stay silent");
            }
        }
        break;
    }
    case ESP_GATTS_CONF_EVT:
        if (param->conf.status != ESP_GATT_OK)
        {
        }
        break;
    case ESP_GATTS_CREAT_ATTR_TAB_EVT:
    {
        if (param->add_attr_tab.status != ESP_GATT_OK)
        {
            sys_loge(GATTS_TAG, "create attribute a table failed, error code=0x%x", param->add_attr_tab.status);
        }
        else if (param->add_attr_tab.num_handle != DEV_M_IDX_NB)
        {
            sys_loge(GATTS_TAG, "create attribute a table abnormally, num_handle (%d) doesn't equal to DEV_M_IDX_NB(%d)", param->add_attr_tab.num_handle, DEV_M_IDX_NB);
        }
        else
        {
            sys_logi(GATTS_TAG, "create attribute a table successfully, the number handle = %d", param->add_attr_tab.num_handle);
            memcpy(m_service_handle_table, param->add_attr_tab.handles, sizeof(m_service_handle_table));
            gl_profile_tab[PROFILE_A_APP_ID].service_handle = param->add_attr_tab.handles[0];
            esp_ble_gatts_start_service(m_service_handle_table[0]);
        }
        break;
    }
    case ESP_GATTS_OPEN_EVT:
    case ESP_GATTS_CANCEL_OPEN_EVT:
    case ESP_GATTS_CLOSE_EVT:
    case ESP_GATTS_LISTEN_EVT:
    case ESP_GATTS_CONGEST_EVT:
    default:
        break;
    }
}

static void gatts_profile_b_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param)
{
    switch (event)
    {
    case ESP_GATTS_REG_EVT:
        sys_logi(GATTS_TAG, "REGISTER_APP_EVT, status %d, app_id %d", param->reg.status, param->reg.app_id);
        esp_err_t create_attr_ret = esp_ble_gatts_create_attr_tab(dev_info_gatt_db, gatts_if, DEV_IDX_NB, PROFILE_B_APP_ID);
        if (create_attr_ret)
        {
            sys_loge(GATTS_TAG, "create attr table failed, error code = %x", create_attr_ret);
        }
        break;
    case ESP_GATTS_READ_EVT:
    {
        sys_logi(GATTS_TAG, "GATT_READ_EVT, conn_id %d, trans_id %" PRIu32 ", handle %d", param->read.conn_id, param->read.trans_id, param->read.handle);
        break;
    }
    case ESP_GATTS_WRITE_EVT:
    {
        sys_logi(GATTS_TAG, "GATT_WRITE_EVT, conn_id %d, trans_id %" PRIu32 ", handle %d", param->write.conn_id, param->write.trans_id, param->write.handle);
        break;
    }
    case ESP_GATTS_EXEC_WRITE_EVT:
        break;
    case ESP_GATTS_MTU_EVT:
        break;
    case ESP_GATTS_UNREG_EVT:
        break;
    case ESP_GATTS_CREATE_EVT:
        sys_logi(GATTS_TAG, "CREATE_SERVICE_EVT1, status %d,  service_handle %d", param->create.status, param->create.service_handle);
        break;
    case ESP_GATTS_CREAT_ATTR_TAB_EVT:
    {
        if (param->add_attr_tab.status != ESP_GATT_OK)
        {
            sys_loge(GATTS_TAG, "create attribute table failed, error code=0x%x", param->add_attr_tab.status);
        }
        else if (param->add_attr_tab.num_handle != DEV_IDX_NB)
        {
            sys_loge(GATTS_TAG, "create attribute table abnormally, num_handle (%d) \
doesn't equal to HRS_IDX_NB(%d)", param->add_attr_tab.num_handle, DEV_IDX_NB);
        }
        else
        {
            sys_logi(GATTS_TAG, "create attribute table successfully, the number handle = %d", param->add_attr_tab.num_handle);
            gl_profile_tab[PROFILE_B_APP_ID].service_handle = param->add_attr_tab.handles[0];
            esp_ble_gatts_start_service(gl_profile_tab[PROFILE_B_APP_ID].service_handle);
        }
        break;
    }
    case ESP_GATTS_ADD_INCL_SRVC_EVT:
        break;
    case ESP_GATTS_ADD_CHAR_EVT:
        sys_logi(GATTS_TAG, "ADD_CHAR_EVT, status %d,  attr_handle %d, service_handle %d",
                 param->add_char.status, param->add_char.attr_handle, param->add_char.service_handle);

        gl_profile_tab[PROFILE_B_APP_ID].char_handle = param->add_char.attr_handle;
        esp_ble_gatts_add_char_descr(gl_profile_tab[PROFILE_B_APP_ID].service_handle, &gl_profile_tab[PROFILE_B_APP_ID].descr_uuid,
                                     ESP_GATT_PERM_READ,
                                     NULL, NULL);
        break;
    case ESP_GATTS_ADD_CHAR_DESCR_EVT:
        gl_profile_tab[PROFILE_B_APP_ID].descr_handle = param->add_char_descr.attr_handle;
        sys_logi(GATTS_TAG, "ADD_DESCR_EVT, status %d, attr_handle %d, service_handle %d",
                 param->add_char_descr.status, param->add_char_descr.attr_handle, param->add_char_descr.service_handle);
        break;
    case ESP_GATTS_DELETE_EVT:
        break;
    case ESP_GATTS_START_EVT:
        sys_logi(GATTS_TAG, "SERVICE_START_EVT, status %d, service_handle %d",
                 param->start.status, param->start.service_handle);
        break;
    case ESP_GATTS_STOP_EVT:
        break;
    case ESP_GATTS_CONNECT_EVT:
    {
        if(param->connect.link_role == 1)  //master role = 0  ; slave role = 1
        {
            sys_logi(GATTS_TAG, "CONNECT_EVT, conn_id %d, remote %02x:%02x:%02x:%02x:%02x:%02x:",
                     param->connect.conn_id,
                     param->connect.remote_bda[0], param->connect.remote_bda[1], param->connect.remote_bda[2],
                     param->connect.remote_bda[3], param->connect.remote_bda[4], param->connect.remote_bda[5]);
            gl_profile_tab[PROFILE_B_APP_ID].conn_id = param->connect.conn_id;
        }
        break;
    }
    case ESP_GATTS_CONF_EVT:
        sys_logi(GATTS_TAG, "ESP_GATTS_CONF_EVT status %d attr_handle %d", param->conf.status, param->conf.handle);
        if (param->conf.status != ESP_GATT_OK)
        {
        }
        break;
    case ESP_GATTS_DISCONNECT_EVT:
    case ESP_GATTS_OPEN_EVT:
    case ESP_GATTS_CANCEL_OPEN_EVT:
    case ESP_GATTS_CLOSE_EVT:
    case ESP_GATTS_LISTEN_EVT:
    case ESP_GATTS_CONGEST_EVT:
    default:
        break;
    }
}

static void gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param)
{
    /* If event is register event, store the gatts_if for each profile */
    if (event == ESP_GATTS_REG_EVT)
    {
        if (param->reg.status == ESP_GATT_OK)
        {
            gl_profile_tab[param->reg.app_id].gatts_if = gatts_if;
        }
        else
        {
            sys_logi(GATTS_TAG, "Reg app failed, app_id %04x, status %d",
                     param->reg.app_id,
                     param->reg.status);
            return;
        }
    }

    /* If the gatts_if equal to profile A, call profile A cb handler,
     * so here call each profile's callback */
    do
    {
        int idx;
        for (idx = 0; idx < PROFILE_NUM; idx++)
        {
            if (gatts_if == ESP_GATT_IF_NONE || /* ESP_GATT_IF_NONE, not specify a certain gatt_if, need to call every profile cb function */
                    gatts_if == gl_profile_tab[idx].gatts_if)
            {
                if (gl_profile_tab[idx].gatts_cb)
                {
                    gl_profile_tab[idx].gatts_cb(event, gatts_if, param);
                }
            }
        }
    }
    while (0);
}

void service_ble_gatt_server_init(void)
{
    esp_err_t ret;
    esp_err_t p_ret = 0;

    ble_gatt_mode = BLE_GATT_MODE_S;
    ble_gatts_init = BLE_GATTS_UNINIT;
    ble_gatts_connect = BLE_GATTS_DISCONNECT;
    ble_gatts_notify_mask = 0x00;

    esp_read_mac((uint8_t *)sys_ble_mac, ESP_MAC_BT);
    snprintf(m_serial_number, sizeof(m_serial_number), SYS_SERIAL_NUMBER_PREFIX "%02X%02X%02X%02X",
             sys_ble_mac[2], sys_ble_mac[3], sys_ble_mac[4], sys_ble_mac[5]);
    sys_logi(GATTS_TAG, "ble mac:%02x %02x %02x %02x %02x %02x", sys_ble_mac[0], sys_ble_mac[1],   
             sys_ble_mac[2], sys_ble_mac[3],   
             sys_ble_mac[4], sys_ble_mac[5]);

    SYS_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));

    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ret = esp_bt_controller_init(&bt_cfg);
    if (ret)
    {
        sys_loge(GATTS_TAG, "initialize controller failed: %s", esp_err_to_name(ret));
    }
    p_ret += ret;

    ret = esp_bt_controller_enable(ESP_BT_MODE_BLE);
    if (ret)
    {
        sys_loge(GATTS_TAG, "enable controller failed: %s", esp_err_to_name(ret));
    }
    p_ret += ret;

    ret = esp_bluedroid_init();
    if (ret)
    {
        sys_loge(GATTS_TAG, "init bluetooth failed: %s", esp_err_to_name(ret));
    }
    p_ret += ret;

    ret = esp_bluedroid_enable();
    if (ret)
    {
        sys_loge(GATTS_TAG, "enable bluetooth failed: %s", esp_err_to_name(ret));
    }
    p_ret += ret;

    ret = ble_security_configure();
    if(ret)
    {
        sys_loge(GATTS_TAG, "BLE security config failed: %s", esp_err_to_name(ret));
        return;
    }

    //gap 初始化
    ret = esp_ble_gap_register_callback(esp_gap_cb);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gap register error, error code = %x", ret);
    }
    p_ret += ret;

    //gatts 初始化
    p_ret = 0;
    ret = esp_ble_gatts_register_callback(gatts_event_handler);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts register error, error code = %x", ret);
    }
    p_ret += ret;

    ret = esp_ble_gatts_app_register(PROFILE_A_APP_ID);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts app register error, error code = %x", ret);
    }
    p_ret += ret;

    ret = esp_ble_gatts_app_register(PROFILE_B_APP_ID);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts app register error, error code = %x", ret);
    }
    p_ret += ret;

    esp_err_t local_mtu_ret = esp_ble_gatt_set_local_mtu(GATTS_CHAR_VAL_LEN_MAX);
    if (local_mtu_ret)
    {
        sys_loge(GATTS_TAG, "set local  MTU failed, error code = %x", local_mtu_ret);
    }
    p_ret += ret;

    if (p_ret)
    {
        sys_loge(GATTS_TAG, "gatts init error!");
    }
    else
    {
        ble_gatts_init = BLE_GATTS_INIT;
    }
}


void service_ble_gatts_set_adv_auto_restart(bool enable)
{
    m_adv_auto_restart = enable;
    sys_logi(GATTS_TAG, "adv auto-restart on disconnect: %s", enable ? "ON" : "OFF");
}

void service_ble_gatts_adv_start(void)
{
    /* 只切广播开关，不重建 GATT 服务：可按需反复调用，配合「可连接窗口」使用。 */
    if(ble_gatts_init != BLE_GATTS_INIT)
    {
        sys_logw(GATTS_TAG, "adv start skipped: gatts not initialized");
        return;
    }
    esp_err_t ret = esp_ble_gap_start_advertising(&adv_params);
    if (ret)
    {
        sys_loge(GATTS_TAG, "adv start error, error code = %x", ret);
        return;
    }
    sys_logi(GATTS_TAG, "advertising started");
}

void service_ble_gatts_adv_stop(void)
{
    /* 停广播不影响已建立的连接：正在传输时调用是安全的（本项目只在窗口到期且未连接时调）。 */
    if(ble_gatts_init != BLE_GATTS_INIT)
    {
        return;
    }
    esp_err_t ret = esp_ble_gap_stop_advertising();
    if (ret)
    {
        sys_loge(GATTS_TAG, "adv stop error, error code = %x", ret);
        return;
    }
    sys_logi(GATTS_TAG, "advertising stopped");
}

void service_ble_gatt_server_uninit(void)
{
    esp_err_t ret;

    if(ble_gatts_connect == BLE_GATTS_DISCONNECT)
    {
        ret = esp_ble_gap_stop_advertising();
        if (ret)
        {
            sys_loge(GATTS_TAG, "gatts adv stop error, error code = %x", ret);
        }
    }
    else
    {
        ble_gatts_connect = BLE_GATTS_DISCONNECT;
        ret = esp_ble_gap_disconnect(conn_params.bda);
        if (ret)
        {
            sys_loge(GATTS_TAG, "gatts dev disconnect error, error code = %x", ret);
        }

        ret = esp_ble_gap_stop_advertising();
        if (ret)
        {
            sys_loge(GATTS_TAG, "gatts adv stop error, error code = %x", ret);
        }
    }

    ble_gatt_mode = BLE_GATT_MODE_S;
    ble_gatts_init = BLE_GATTS_UNINIT;
    ble_gatts_connect = BLE_GATTS_DISCONNECT;
    ble_gatts_notify_mask = 0x00;

    ret = esp_ble_gatts_stop_service(gl_profile_tab[PROFILE_A_APP_ID].service_handle);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts a stop error, error code = %x", ret);
    }

    ret = esp_ble_gatts_stop_service(gl_profile_tab[PROFILE_B_APP_ID].service_handle);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts b stop error, error code = %x", ret);
    }

    ret = esp_ble_gatts_delete_service(gl_profile_tab[PROFILE_A_APP_ID].service_handle);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts a delete error, error code = %x", ret);
    }

    ret = esp_ble_gatts_delete_service(gl_profile_tab[PROFILE_B_APP_ID].service_handle);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts b delete error, error code = %x", ret);
    }

    ret = esp_ble_gatts_app_unregister(gl_profile_tab[PROFILE_A_APP_ID].gatts_if);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts a app unregister, error code = %x", ret);
    }

    ret = esp_ble_gatts_app_unregister(gl_profile_tab[PROFILE_B_APP_ID].gatts_if);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts b app unregister, error code = %x", ret);
    }
}


void service_ble_gatt_server_reinit(void)
{
    esp_err_t ret;
    esp_err_t p_ret = 0;

    ble_gatt_mode = BLE_GATT_MODE_S;
    ble_gatts_init = BLE_GATTS_UNINIT;
    ble_gatts_connect = BLE_GATTS_DISCONNECT;
    ble_gatts_notify_mask = 0x00;

    //gatts 初始化
    p_ret = 0;
    ret = esp_ble_gatts_register_callback(gatts_event_handler);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts register error, error code = %x", ret);
    }
    p_ret += ret;

    //gap 初始化
    ret = esp_ble_gap_register_callback(esp_gap_cb);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gap register error, error code = %x", ret);
    }
    p_ret += ret;

    ret = esp_ble_gatts_app_register(PROFILE_A_APP_ID);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts app register error, error code = %x", ret);
    }
    p_ret += ret;

    ret = esp_ble_gatts_app_register(PROFILE_B_APP_ID);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts app register error, error code = %x", ret);
    }
    p_ret += ret;

    esp_err_t local_mtu_ret = esp_ble_gatt_set_local_mtu(GATTS_CHAR_VAL_LEN_MAX);
    if (local_mtu_ret)
    {
        sys_loge(GATTS_TAG, "set local  MTU failed, error code = %x", local_mtu_ret);
    }
    p_ret += ret;

    if (p_ret)
    {
        sys_loge(GATTS_TAG, "gatts init error!");
    }
    else
    {
        ble_gatts_init = BLE_GATTS_INIT;
    }
}

void service_ble_gatts_dev_disconnect(void)
{
    sys_logi(GATTS_TAG, "Invalid device disconnect now!");

    esp_err_t ret = esp_ble_gap_disconnect(conn_params.bda);
    if (ret)
    {
        sys_loge(GATTS_TAG, "gatts dev disconnect error, error code = %x", ret);
    }
}

uint8_t service_ble_gatts_get_connect(void)
{
    return ble_gatts_connect;
}

static void esp_gap_cb(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param)
{
    switch (event)
    {
    case ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT:
        adv_config_done &= (~ADV_CONGIG_FLAG);
        if (adv_config_done == 0)
        {
            esp_ble_gap_start_advertising(&adv_params);
        }
        break;
    case ESP_GAP_BLE_SCAN_RSP_DATA_SET_COMPLETE_EVT:
        adv_config_done &= (~SCAN_RSP_CONFIG_FLAG);
        if (adv_config_done == 0)
        {
            esp_ble_gap_start_advertising(&adv_params);
        }
        break;
    case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
        //advertising start complete event to indicate advertising start successfully or failed
        if (param->adv_start_cmpl.status != ESP_BT_STATUS_SUCCESS)
        {
            sys_loge(GATTS_TAG, "Advertising start failed");
        }
        break;
    case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
        if (param->adv_stop_cmpl.status != ESP_BT_STATUS_SUCCESS)
        {
            sys_loge(GATTS_TAG, "Advertising stop failed");
        }
        else
        {
            sys_logi(GATTS_TAG, "Stop adv successfully");
        }
        break;
    case ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT:
    {
        //the unit of the duration is second
        break;
    }
    case ESP_GAP_BLE_SCAN_START_COMPLETE_EVT:
        //scan start complete event to indicate scan start successfully or failed
        if (param->scan_start_cmpl.status != ESP_BT_STATUS_SUCCESS)
        {
            sys_loge(GATTS_TAG, "scan start failed, error status = %x", param->scan_start_cmpl.status);
            break;
        }
        break;
    case ESP_GAP_BLE_SCAN_RESULT_EVT:
    {
        esp_ble_gap_cb_param_t *scan_result = (esp_ble_gap_cb_param_t *)param;
        switch (scan_result->scan_rst.search_evt)
        {
        case ESP_GAP_SEARCH_INQ_RES_EVT:
            break;
        case ESP_GAP_SEARCH_INQ_CMPL_EVT:
        {
            sys_logi(GATTS_TAG, "ESP_GAP_SEARCH_INQ_CMPL_EVT, scan stop");
            break;
        }
        default:
            break;
        }
        break;
    }
    case ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT:
        if (param->scan_stop_cmpl.status != ESP_BT_STATUS_SUCCESS)
        {
            sys_loge(GATTS_TAG, "scan stop failed, error status = %x", param->scan_stop_cmpl.status);
            break;
        }
        sys_logi(GATTS_TAG, "stop scan successfully");
        break;
    case ESP_GAP_BLE_UPDATE_CONN_PARAMS_EVT:
        sys_logi(GATTS_TAG, "update connection params status = %d, min_int = %d, max_int = %d,conn_int = %d,latency = %d, timeout = %d",
                  param->update_conn_params.status,
                  param->update_conn_params.min_int,
                  param->update_conn_params.max_int,
                  param->update_conn_params.conn_int,
                  param->update_conn_params.latency,
                  param->update_conn_params.timeout);
        break;
    case ESP_GAP_BLE_SEC_REQ_EVT:
        esp_ble_gap_security_rsp(param->ble_security.ble_req.bd_addr, true);
        break;
    case ESP_GAP_BLE_AUTH_CMPL_EVT:
        if(param->ble_security.auth_cmpl.success)
        {
            sys_logi(GATTS_TAG, "BLE Secure Connections pairing complete");
        }
        else
        {
            sys_logw(GATTS_TAG, "BLE pairing failed, reason=0x%x",
                     param->ble_security.auth_cmpl.fail_reason);
        }
        break;
    default:
        break;
    }
}

void service_ble_gatts_clear_bonds(void)
{
    int count = esp_ble_get_bond_device_num();
    if(count <= 0)
    {
        return;
    }
    esp_ble_bond_dev_t *devices = calloc((size_t)count, sizeof(*devices));
    if(devices == NULL)
    {
        sys_loge(GATTS_TAG, "cannot allocate BLE bond list");
        return;
    }
    if(esp_ble_get_bond_device_list(&count, devices) == ESP_OK)
    {
        for(int i = 0; i < count; ++i)
        {
            (void)esp_ble_remove_bond_device(devices[i].bd_addr);
        }
    }
    free(devices);
}
