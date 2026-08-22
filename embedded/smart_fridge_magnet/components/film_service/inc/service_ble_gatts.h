#ifndef __service_ble_gatts_H__
#define __service_ble_gatts_H__

#ifdef __cplusplus
extern "C"{
#endif

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>

/*********************************************************************
 * MACROS
 */
#define GATTS_CH1_NOTIFY_ENABLE_BIT       (uint8_t)(0x01<<0)
#define GATTS_CH2_NOTIFY_ENABLE_BIT       (uint8_t)(0x01<<1)
#define GATTS_CH3_NOTIFY_ENABLE_BIT       (uint8_t)(0x01<<2)

#define BLE_DAFAULT_LATENCY               (0)
/* 连接间隔（单位 1.25ms）。原 0x10/0x20 = 20/40ms 过于保守：本机采用「带响应写」，
 * 每个连接事件只能搬 1 帧（180B 数据），吞吐直接被连接间隔锁死 —— 实测 209KB 的 film
 * 要传 71.6s（2.85 KB/s）。改为 BLE 规范允许的最小区间 7.5/15ms 后，同样的帧大小
 * 理论可达 12~24 KB/s，预计 15~20s 传完。
 * 说明：
 *  - 0x06 是 BLE 规范下限（7.5ms），再小非法；
 *  - 这是「请求值」，由中心设备（手机/浏览器）最终裁决，被拒时沿用原参数、不会失败，
 *    协商结果可在串口 "update connection params ... conn_int = N" 中查看（单位 1.25ms）；
 *  - 传输期功耗上升，但射频开启时长缩短到 1/4，整次传输的总能量反而更低。 */
#define BLE_DEFAULT_MAX_INT               (0x0C)   // max_int = 0x0C*1.25ms = 15ms
#define BLE_DEFAULT_MIN_INT               (0x06)   // min_int = 0x06*1.25ms = 7.5ms（规范下限）
#define BLE_DEFAULT_TIMEOUT               (400)    // timeout = 400*10ms = 4000ms
/* 广播间隔（单位 0.625ms）。原 0x200/0x400 = 320/640ms 偏慢，导致 Web Bluetooth / nRF 等
 * 扫描端要等很久设备才出现。改为 100/200ms 的标准「快速可发现」区间，显著缩短扫描发现时延；
 * 设备为插电/大电池、且仅在可连接窗口广播，功耗代价可接受。 */
#define BLE_ADV_MIN_INT                   (0xA0)   // 最小广播间隔 0xA0*0.625ms = 100ms
#define BLE_ADV_MAX_INT                   (0x140)  // 最大广播间隔 0x140*0.625ms = 200ms

#define GATTC_GAP_SCAN_SECONDS            (3)      //复联扫描时间
#define GATTC_GAP_START_SCAN_SECONDS      (5)      //开启扫描时间

#define ADV_CONGIG_FLAG                   (1 << 0)
#define SCAN_RSP_CONFIG_FLAG              (1 << 1)

#define BLE_NOTIFY_SEND_CH1               (1)
#define BLE_NOTIFY_SEND_CH2               (2)
#define BLE_NOTIFY_SEND_CH3               (3)


/*********************************************************************
* TYPEDEFS
*/
typedef void (*service_ble_gatts_cmd_cb_t)(uint8_t const *p_data, uint16_t len);

/* Invoked from the GATTS layer when the connected peer disconnects, so the
 * BLE service can abort any in-progress file transfer (Req 9.5). Runs in the
 * bluedroid GATTS callback context — implementations must be non-blocking
 * (e.g. just enqueue work for the BLE task). */
typedef void (*service_ble_gatts_disconnect_cb_t)(void);

enum
{
    BLE_GATT_MODE_S = 0,       //GATT SERVER
};

enum
{
    BLE_GATTS_DISCONNECT = 0,  //GATTS DISCONNECT
    BLE_GATTS_CONNECT,         //GATTS CONNECT
};

enum
{
    BLE_GATTS_UNINIT = 0,      //GATTS UNINIT
    BLE_GATTS_INIT,            //GATTS INIT
};

/* Attributes State Machine */
enum
{
    IDX_SVC_DEV,
    IDX_CHAR_1,
    IDX_CHAR_VAL_1,

    IDX_CHAR_2,
    IDX_CHAR_VAL_2,

    IDX_CHAR_3,
    IDX_CHAR_VAL_3,

    IDX_CHAR_4,
    IDX_CHAR_VAL_4,

    IDX_CHAR_5,
    IDX_CHAR_VAL_5,

    IDX_CHAR_6,
    IDX_CHAR_VAL_6,

    IDX_CHAR_7,
    IDX_CHAR_VAL_7,

    DEV_IDX_NB,
};

enum
{
    IDX_M_SVC,
    IDX_M_CHAR_1,
    IDX_M_CHAR_VAL_1,
    IDX_M_CHAR_CFG_1,

    IDX_M_CHAR_2,
    IDX_M_CHAR_VAL_2,
    IDX_M_CHAR_CFG_2,

    IDX_M_CHAR_3,
    IDX_M_CHAR_VAL_3,
    IDX_M_CHAR_CFG_3,

    DEV_M_IDX_NB,
};

enum
{
    MSG_BLE_CH1_IN_CMD = 1,
    MSG_BLE_CH1_OUT_DATA,
    MSG_BLE_CH2_OUT_DATA,
    MSG_BLE_CH3_OUT_DATA,
    MSG_BLE_GAP_DISCONNECT,       /* app requested disconnect of an invalid peer */
    MSG_BLE_GAP_PEER_DISCONNECTED, /* peer link dropped: abort in-progress transfer (Req 9.5) */
};


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
extern void service_ble_gatt_server_init(void);
extern void service_ble_gatt_server_uninit(void);
extern void service_ble_gatt_server_reinit(void);
extern void service_ble_gatts_dev_disconnect(void);
/** 清除所有 BLE bond；恢复出厂时调用，避免旧手机继续持有链路密钥。 */
extern void service_ble_gatts_clear_bonds(void);
extern void service_ble_send_notify_data(uint8_t ch, uint8_t *buf, uint16_t len);
extern void service_ble_gatts_cmd_register_cb(service_ble_gatts_cmd_cb_t cb);
extern void service_ble_gatts_disconnect_register_cb(service_ble_gatts_disconnect_cb_t cb);
extern uint8_t service_ble_gatts_get_connect(void);

/**
 * @brief 开始 BLE 广播（使设备可被扫描/连接）
 *
 * 与 service_ble_gatt_server_init() 区分：本接口只控制广播开关，不重建 GATT 服务，
 * 因此可以按需反复调用（配合可连接窗口使用）。
 */
/**
 * @brief 设置「断连后是否自动恢复广播」
 *
 * 上游行为是断连即无条件重开广播。在按需广播模式下这会架空整个机制——连一次之后设备就
 * 永久可被扫描。省电模式应关闭本开关，改由可连接窗口统一决定何时广播。
 *
 * @param enable true 断连后自动恢复广播（调试友好）；false 保持静默（省电、暴露面更小）
 */
extern void service_ble_gatts_set_adv_auto_restart(bool enable);

extern void service_ble_gatts_adv_start(void);

/**
 * @brief 停止 BLE 广播（设备不再被扫描到，但已建立的连接不受影响）
 *
 * 用于「按需广播」：只在可连接窗口内广播，窗口外静默。除省电外也缩小暴露面——
 * 不广播就不会被任意设备扫描并尝试连接。
 */
extern void service_ble_gatts_adv_stop(void);


#ifdef __cplusplus
extern "C"}
#endif

#endif /* __service_ble_gatts_H__ */
