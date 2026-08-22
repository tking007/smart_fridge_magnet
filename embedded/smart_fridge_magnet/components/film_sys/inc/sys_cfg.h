/*********************************************************************
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 * Change: brand/device identifiers renamed to this project's naming, and
 *   SYS_UPSTREAM_BASELINE added for the startup identification banner.
 * Preserved unchanged: .film file format, film_* symbols, BLE service/
 *   characteristic UUIDs, and the FILE command sequence.
 * Upstream: FrameFilm (GPL-3.0-or-later) — see ../../../NOTICE.md and LICENSE.
 *********************************************************************/
#ifndef __SYS_CFG_H__
#define __SYS_CFG_H__

#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * INCLUDES
 */


/*********************************************************************
 * MACROS
 */
// SYS CONFIG
#define SYS_DEVICE_NAME                "智能冰箱贴"
#define SYS_WIFI_HOSTNAME              "smart-fridge-magnet"
#define SYS_MANUFACTURER_NAME          "SMART_FRIDGE_MAGNET"
#define SYS_MODEL_NUMBER               "SFM-DEV-1"
#define SYS_SERIAL_NUMBER_PREFIX       "SFM"                    // SN 由设备 MAC 后 4 字节派生
/* 本地时区（POSIX 写法：UTC+8 记作 -8，无夏令时）。SNTP 得到的是 UTC，
 * 不设时区则 localtime() 偏 8 小时，按时段选餐会在错误的时间切换。 */
#define SFM_LOCAL_TZ                   "CST-8"
#define SYS_HAREWARE_VERSION           "H1.0"                   //硬件版本号
#define SYS_FIRMWARE_VERSION           "0.19.3"                 //固件版本号
#define SYS_SYSTEM_ID                  "smart_fridge_magnet"

// 上游基线提交标识（GPLv3 署名与可诊断启动横幅，详见 ../../../NOTICE.md）
#define SYS_UPSTREAM_BASELINE          "FrameFilm 562f330"      //上游基线提交号

#define SYS_BLE_DEFAULT_KEY            "SFM_BLE_KEY"

#define SYS_M_NVS_NAMESPACE            "SFM_NVS"
#define SYS_M_NVS_KEY_NAME             "PARAMS"
/* 置顶自定义画面（pinned film）的独立 blob key，与 PARAMS 同 namespace。
 * 单独一个 key 而不是加进 ServiceParam_Def_t：后者会改变 PARAM_SERIALIZE_LEN，
 * 而 nvs_init() 见到长度不符即整份 set_default()，已烧录设备会丢配网。 */
#define SYS_M_NVS_PINNED_KEY_NAME      "PINNED"

// spiffs
#define BACE_PATH                      "/spiffs"


/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */


/*********************************************************************
 * GLOBAL FUNCTIONS
 */


#ifdef __cplusplus
extern "C"
}
#endif

#endif /* __SYS_CFG_H__ */
