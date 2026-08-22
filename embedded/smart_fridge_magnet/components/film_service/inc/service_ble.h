#ifndef __SERVICE_BLE_H__
#define __SERVICE_BLE_H__

#ifdef __cplusplus
extern "C"{
#endif

/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>
#include <stdint.h>

/*********************************************************************
 * MACROS
 */
#define SYS_OS_PRI_BLE_TASK            (8)
#define SYS_OS_SIZE_BLE_TASK           (4096)
#define SYS_OS_NAME_BLE_TASK           "ble_task"

#define BLE_CMD_HEAD                   (0x55)
#define BLE_CMD_LEN_MIN                (4)

// 通道定义
// FILM文件传输 传输逻辑 START->FILENAME->FILELEN->FILEDATA-> STOP->END
#define BLE_FILM_TRANS_CH_FILE_NAME                    (0x00)
#define BLE_FILM_TRANS_CH_FILE_LEN                     (0x01)
#define BLE_FILM_TRANS_CH_FILE_DATA                    (0x02)
#define BLE_FILM_TRANS_CH_FILE_START                   (0x03)
#define BLE_FILM_TRANS_CH_FILE_STOP                    (0x04)
// FILM文件管理               
#define BLE_FILM_TRANS_CH_FILE_DELETE                  (0x05) // 删除id对应的文件
#define BLE_FILM_TRANS_CH_FILE_LIST                    (0x06) // 查询文件列表
#define BLE_FILM_TRANS_CH_FILE_DISPLAY                 (0x07) // 显示id对应的文件
#define BLE_FILM_TRANS_CH_FILE_DISPLAY_GET             (0x08) // 查询当前显示的文件
// OTA               
#define BLE_FILM_TRANS_CH_OTA_LEN                      (0x10)
#define BLE_FILM_TRANS_CH_OTA_DATA                     (0x11)
#define BLE_FILM_TRANS_CH_OTA_START                    (0x12)
#define BLE_FILM_TRANS_CH_OTA_STOP                     (0x13)
// FILM控制               
#define BLE_FILM_TRANS_CH_CTRL_MODE                    (0x20) // Film模式切换 （0：手动，1：自动）
#define BLE_FILM_TRANS_CH_CTRL_MODE_GET                (0x21) // Film模式查询 （0：手动，1：自动）
#define BLE_FILM_TRANS_CH_CTRL_RESET                   (0x22) // 重置设备到出厂
#define BLE_FILM_TRANS_CH_CTRL_PWRREAD                 (0x23) // 获取电量
#define BLE_FILM_TRANS_CH_CTRL_REBOOT                  (0x24) // 重启设备
#define BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF              (0x25) // 休眠模式开关设置
#define BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF_GET          (0x26) // 休眠模式开关查询
#define BLE_FILM_TRANS_CH_CTRL_SLEEPMODE               (0x27) // 休眠模式 定时唤醒开关设置
#define BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_GET           (0x28) // 休眠模式 定时唤醒开关查询
#define BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_TIME          (0x29) // 定时唤醒开关时间设置（单位分钟）
#define BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_TIME_GET      (0x2A) // 定时唤醒开关时间查询（单位分钟）
#define BLE_FILM_TRANS_CH_CTRL_SDRESET                 (0x2B) // 兼容保留；远程格式化已禁用
// 网络控制
#define BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE             (0x30) // WiFi开关设置
#define BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE_GET         (0x31) // WiFi开关查询
#define BLE_FILM_TRANS_CH_CTRL_WIFI_SSID               (0x32) // WiFi SSID设置
#define BLE_FILM_TRANS_CH_CTRL_WIFI_SSID_GET           (0x33) // WiFi SSID查询
#define BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD           (0x34) // WiFi 密码设置
#define BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD_GET       (0x35) // WiFi 密码查询
#define BLE_FILM_TRANS_CH_CTRL_FILM_API_URL            (0x36) // HTTP下载film文件的API地址设置
#define BLE_FILM_TRANS_CH_CTRL_FILM_API_URL_GET        (0x37) // HTTP下载film文件的API地址查询
#define BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT            (0x38) // 连接WiFi
#define BLE_FILM_TRANS_CH_CTRL_WIFI_DISCONNECT         (0x39) // 断开WiFi连接
#define BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT_GET        (0x3A) // 查询WiFi连接状态 0：未连接 1：已连接
#define BLE_FILM_TRANS_CH_CTRL_WIFI_CLEAR              (0x3B) // 清除网络配置信息
#define BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD           (0x3C) // 开始下载film文件
#define BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD_STATE     (0x3D) // 查询下载状态
// Wi-Fi 配网与状态查询（Req 9.6/9.7/15.1/15.3）
// 规范上游 0x30+ 网络控制块：补齐 manifest_url/device_id/device_token 的配网 set 命令，
// 新增一个合并状态查询命令；追加的 opcode 从 0x3E 起，既有 0x30–0x3D 语义/值保持不变。
#define BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL            (0x3E) // 设置 manifest_url（Wi-Fi 同步清单地址，非机密）
#define BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL_GET        (0x3F) // 查询 manifest_url（非机密）
#define BLE_FILM_TRANS_CH_CTRL_DEVICE_ID               (0x40) // 设置 device_id（设备标识，非机密）
#define BLE_FILM_TRANS_CH_CTRL_DEVICE_ID_GET           (0x41) // 查询 device_id（非机密）
#define BLE_FILM_TRANS_CH_CTRL_DEVICE_TOKEN            (0x42) // 设置 device_token（明文写入→落盘加密；仅写不可读回）
#define BLE_FILM_TRANS_CH_CTRL_STATUS_QUERY            (0x43) // 查询 Wi-Fi状态/最近版本/最近错误/busy/电量（不含任何机密明文）
// BLE SYNC_NOW 立即同步命令（Req 14.1/14.4）
// 设计/任务文本记为「(0x32)」仅为标称值：0x32 在上游基线已是 WIFI_SSID 设置命令
// （见上方 BLE_FILM_TRANS_CH_CTRL_WIFI_SSID），不可复用。此处选取 0x43 之后的下一个
// 空闲 opcode 0x44 作为专用、无冲突的 SYNC_NOW；既有 0x30–0x43 语义/取值保持不变。
#define BLE_FILM_TRANS_CH_CTRL_SYNC_NOW                (0x44) // 立即触发一次 Wi-Fi manifest 同步检查（一次性；轮询仍为默认后台行为）
// 清空全部 .film（0x44 之后的下一个空闲 opcode）。用于「只保留当天菜单」：
// 发送方在下发新一套画面之前调用一次，避免历史版本图片留在设备上被按键轮播翻出来。
// 清空不清屏；传输进行中会被拒绝。Ack 1 字节 = 实际删除数（失败 0xFF）。
#define BLE_FILM_TRANS_CH_FILE_CLEAR_ALL               (0x45) // 清空存储中的全部 .film
// 静默接收开关（1 字节 0/1）。开启后接收完成只保存不上屏，用于连发多张时避免逐张刷屏
// （电子纸刷一次约 20s，逐张刷会让对端 GATT 超时断连）。不持久化，重启回到 0。
#define BLE_FILM_TRANS_CH_FILE_SILENT_RECV             (0x46) // 设置静默接收模式
// 按当前时间显示对应那一餐（中午显示午餐详情）。整套传完后由发送方触发一次。
// 时间不可信（未配网/未 SNTP）时回落显示今日总览，绝不猜时段。响应 1 字节：
// 命中的文件索引，失败 0xFF。
#define BLE_FILM_TRANS_CH_FILE_DISPLAY_NOW             (0x47) // 按当前时段显示
// 休眠/唤醒策略配置（F3，0x47 之后的连续空闲取值 0x48–0x4B）。
// App 把每日更新时间写进设备：出厂为单槽位 06:30，另有 24 小时兜底用于自愈。
// 0x4A/0x4B 提供轮询周期的设备侧读写通道，便于现场调续航。
#define BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE           (0x48) // 设置唤醒时间表（每日时间 + 兜底周期）
#define BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE_GET       (0x49) // 查询唤醒时间表
#define BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL           (0x4A) // 设置轮询周期（秒，写入前钳制）
#define BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL_GET       (0x4B) // 查询轮询周期（秒）
// 置顶自定义画面（pinned film，0x4B 之后的下两个空闲取值 0x4C/0x4D）。
// 让用户手绘的涂鸦 / 相册里的照片能【持续】显示在冰箱贴上：自定义图靠既有传输序列
// （0x03/0x00/0x01/0x02/0x04）就能传进来并自动上屏，但撑不过几小时 —— 设备默认 10 分钟
// 深睡一次，唤醒后「上次自动切换到哪个时段」的内存状态归零，时间一同步就把自定义图换成
// 菜单。所以必须有一个【持久化的】pin：一个文件名 + 一个模式（+ 可选时间窗口）。
// 三种模式：1=一直显示、2=只在 [start,end) 显示（支持跨零点）、3=做饭窗口外显示；0=取消。
// pin 配置存在**独立的 NVS key** 下，不占 PARAMS blob；恢复出厂时仍会一并擦除。
#define BLE_FILM_TRANS_CH_CTRL_PIN_FILM                (0x4C) // 设置置顶自定义画面（模式+窗口+文件名）
#define BLE_FILM_TRANS_CH_CTRL_PIN_FILM_GET            (0x4D) // 查询置顶自定义画面

/*********************************************************************
* TYPEDEFS
*/
typedef struct
{
    uint8_t ID;
    uint8_t subID;
    uint8_t len;
    uint8_t *pdata;
} ble_msg_t;

// 数据包构成 1byte 头 1byte 通道 1byte 数据长度 nbyte 数据(数据长度) 1byte 校验和(和校验)
typedef struct
{
    uint8_t ch;          //通道
    uint8_t len;         //数据长度
    uint8_t sum;         //校验和
    uint8_t *pdata;
} ble_cmd_t;

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
extern void service_ble_init(void);
extern void service_ble_msg_send(void *p_msg, bool in_isr);
extern void service_ble_msg_gatts_cmd_send( uint8_t const *p_data, uint16_t len );
extern void service_ble_msg_gatts_data_send( uint8_t const *p_data, uint16_t len, uint8_t ch);


#ifdef __cplusplus
extern "C"}
#endif

#endif /* __SERVICE_BLE_H__ */
