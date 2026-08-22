/*********************************************************************
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 * Upstream: FrameFilm (GPL-3.0-or-later) — see ../../NOTICE.md and LICENSE.
 *   Requirements: 15.1, 15.2, 15.4, 19.1.
 *********************************************************************/
#ifndef __SERVICE_PARAM_H__
#define __SERVICE_PARAM_H__


/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>  /* sfm_param_apply_default_wake_slots() 用 memset */

/* wake_schedule.h 只依赖 <stdint.h>/<stdbool.h>，包含它不会破坏本头文件
 * 「宿主机可直接 include」的性质。直接复用 WAKE_SCHEDULE_MAX_SLOTS 而不在这里另抄
 * 一份上限宏：两份常量必然漂移，而漂移的表现是序列化长度与结构体不一致——
 * 那会让 NVS 记录被判为异物并整份重置，症状离成因极远。 */
#include "wake_schedule.h"

/* film_pin.h 同样只依赖 <stdint.h>/<stdbool.h>/<stddef.h>，包含它不破坏本头文件
 * 「宿主机可直接 include」的性质。pin 配置**刻意不进 ServiceParam_Def_t**（见下方
 * pin 相关函数的说明），这里包含它只是为了声明那三个读写函数的签名。 */
#include "film_pin.h"

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* Explicit persisted parameter-blob version. Field evolution is migrated by
 * version (see param_serialize.c).
 *
 * v3：ServiceSleep_Def_t 新增唤醒时间表字段（wake_slot_count / wake_slots[6] /
 * wake_fallback_min）。param_deserialize() 同时支持 v2 布局，故升级时旧配网与同步状态
 * 会原样迁移。
 * v4：布局不变，只把旧出厂唤醒策略迁成每日一次。
 * v5：布局不变，出厂唤醒策略从「每日一次」改成「三餐前各一次 + 每日兜底」。 */
#define SFM_PARAM_CONFIG_VERSION      (6u)

/* 兜底唤醒周期的出厂默认值（分钟）。
 * 24 小时：三餐槽位是主路径，兜底只负责错过计划时自愈。 */
#define SFM_PARAM_DEFAULT_WAKE_FALLBACK_MIN (1440u)

/* 出厂唤醒时间表：三餐前各一次。
 *
 * 取值 = 后端出厂餐次（07:30 / 12:00 / 18:30）− 发布提前量（30 分钟）+ 5 分钟余量。
 * 加余量而不是踩着发布时刻醒：两边各自按自己的时钟走，正好同一分钟醒来会赶在发布落库
 * 之前取图，拿到的是上一餐那张。5 分钟足够覆盖时钟漂移与一次模型调用的尾延迟。
 *
 * 这只是**出厂值**：App 一旦下发真实作息就会整表覆盖（BLE 0x48）。 */
#define SFM_PARAM_DEFAULT_WAKE_SLOT_COUNT   (3u)
#define SFM_PARAM_DEFAULT_WAKE_SLOT_1_MIN   (425u)  /* 07:05，早餐前 */
#define SFM_PARAM_DEFAULT_WAKE_SLOT_2_MIN   (695u)  /* 11:35，午餐前 */
#define SFM_PARAM_DEFAULT_WAKE_SLOT_3_MIN   (1085u) /* 18:05，晚餐前 */

#define SFM_PARAM_LEGACY_WAKE_FALLBACK_MIN  (240u)  /* v3 旧出厂值 */
#define SFM_PARAM_LEGACY_WAKE_SLOT_MIN      (390u)  /* v4 旧出厂值 06:30 */
#define SFM_PARAM_LEGACY_POLL_INTERVAL_SEC  (60u)   /* v5 旧出厂值 */

/* Field capacities. The first three preserve the upstream/baseline sizes that
 * service_ble.c bounds-checks against via sizeof() — DO NOT shrink them. */
#define SFM_WIFI_SSID_MAX_BYTES       (32u)   /* IEEE 802.11 SSID byte limit          */
#define SFM_WIFI_SSID_MAX             (64u)   /* persisted SSID slot; keep schema size */
#define SFM_WIFI_PASSWORD_MAX         (64u)   /* Wi-Fi password plaintext (<=63)     */
#define SFM_FILM_API_URL_MAX          (128u)  /* legacy upstream HTTP film API URL   */
#define SFM_MANIFEST_URL_MAX          (128u)  /* Wi-Fi sync manifest URL             */
#define SFM_DEVICE_ID_MAX             (48u)   /* device identifier                   */
#define SFM_DEVICE_TOKEN_MAX          (128u)  /* device token plaintext (<=127)      */
#define SFM_SYNC_ERR_MAX              (96u)   /* last sync error (non-secret text)   */

/* Per-secret ciphertext overhead of the sfm_crypto blob (prepended nonce).
 * Declared locally so this PUBLIC header stays independent of sfm_crypto.h and
 * remains trivially host-includable; service_param.c verifies at compile time
 * (_Static_assert) that this equals sfm_crypto.h's SFM_CRYPTO_OVERHEAD. */
#define SFM_SECRET_CIPHER_OVERHEAD    (12u)

/* At-rest ciphertext buffer sizes = plaintext capacity + nonce overhead. */
#define SFM_WIFI_PASSWORD_ENC_MAX     (SFM_WIFI_PASSWORD_MAX + SFM_SECRET_CIPHER_OVERHEAD)
#define SFM_DEVICE_TOKEN_ENC_MAX      (SFM_DEVICE_TOKEN_MAX + SFM_SECRET_CIPHER_OVERHEAD)

/*********************************************************************
* TYPEDEFS
*/
typedef struct
{
    uint32_t current_file_id;  // 当前显示的文件ID
    uint8_t load_complete;     // 加载完成标志
    uint8_t play_mode;         // FILM模式（0：手动，1：本地切换 2.网络拉取）
} ServiceFilm_Def_t;

/* 改动本结构体（或本文件任何 *_MAX 容量宏）会改变持久化布局。新增字段时必须在
 * param_serialize.h/.c 保留旧版本的精确长度与读取分支，否则已烧录设备的配网记录会被
 * 当作损坏数据清空。 */
typedef struct
{
    uint8_t sleep_mode;        // 休眠模式开关 0：关闭 1：开启
    uint8_t sleep_auto;        // 定时唤醒开关 0：关闭 1：开启
    uint16_t sleep_time;       // 定时唤醒时间（单位分钟 10min - 48h(48*60min)）
    /* —— 按计划唤醒（F3）——
     * wake_slots 是【当地时间的分钟数】列表（0..1439），设备在每个时刻之前醒来一次
     * 去拉 manifest；wake_fallback_min 是兜底周期，保证非饭点时段也能拿到手动发布的
     * 新画面。两者的合成规则见 wake_schedule.h（取较小值）。
     *
     * 出厂默认三餐前各一个槽位（见 SFM_PARAM_DEFAULT_WAKE_SLOT_*）；App 下发真实作息后
     * 通过 BLE 0x48 SET_WAKE_SCHEDULE 整表覆盖。 */
    uint8_t  wake_slot_count;                        // 有效唤醒点个数 0..WAKE_SCHEDULE_MAX_SLOTS
    uint16_t wake_slots[WAKE_SCHEDULE_MAX_SLOTS];    // 唤醒点：当地时间分钟数 0..1439
    uint16_t wake_fallback_min;                      // 兜底唤醒周期（分钟），默认 1440（24h）
} ServiceSleep_Def_t;

/**
 * @brief 把出厂唤醒时间表写进 sleep 参数（三餐前各一次 + 每日兜底）。
 *
 * 恢复出厂与两条配置迁移分支都要写同一套值，各写一遍必然有一处漏改，
 * 而漏改的那条路径只在特定旧版本升级时才走到，测不出来。
 */
static inline void sfm_param_apply_default_wake_slots(ServiceSleep_Def_t *sleep)
{
    if (sleep == NULL) {
        return;
    }
    memset(sleep->wake_slots, 0, sizeof(sleep->wake_slots));
    sleep->wake_slot_count = SFM_PARAM_DEFAULT_WAKE_SLOT_COUNT;
    sleep->wake_slots[0] = (uint16_t)SFM_PARAM_DEFAULT_WAKE_SLOT_1_MIN;
    sleep->wake_slots[1] = (uint16_t)SFM_PARAM_DEFAULT_WAKE_SLOT_2_MIN;
    sleep->wake_slots[2] = (uint16_t)SFM_PARAM_DEFAULT_WAKE_SLOT_3_MIN;
    sleep->wake_fallback_min = (uint16_t)SFM_PARAM_DEFAULT_WAKE_FALLBACK_MIN;
}

typedef struct
{
    uint8_t wifi_enable;                        // WiFi开关 0：关闭 1：开启
    char wifi_ssid[SFM_WIFI_SSID_MAX];          // WiFi SSID（协议最大32字节；槽位保持64）
    char wifi_password[SFM_WIFI_PASSWORD_MAX];  // WiFi 密码【运行时明文】；持久化时加密存于 secrets
    char film_api_url[SFM_FILM_API_URL_MAX];    // 兼容上游：HTTP下载film文件的API地址
    char manifest_url[SFM_MANIFEST_URL_MAX];    // Wi-Fi 同步 manifest 地址
    char device_id[SFM_DEVICE_ID_MAX];          // 设备标识（非机密）
    char device_token[SFM_DEVICE_TOKEN_MAX];    // 设备令牌【运行时明文】；持久化时加密存于 secrets
} ServiceNetwork_Def_t;

typedef struct
{
    uint32_t poll_interval_seconds;             // 轮询周期（秒），默认86400，钳制 [30,86400]
    uint32_t last_applied_version;              // 最近成功应用的 film 版本
    char     last_sync_error[SFM_SYNC_ERR_MAX]; // 最近一次同步错误（非机密文本）
} ServiceSync_Def_t;

/*
 * At-rest ciphertext shadow of the two sensitive fields. The parameter service
 * keeps the PLAINTEXT of wifi_password / device_token in `network` for runtime
 * use, and stores their SFM_BLE_KEY-encrypted ciphertext here in the persisted
 * NVS blob (the corresponding plaintext bytes are zeroed in the blob so no
 * plaintext is ever written to flash). A length of 0 means "no secret stored".
 */
typedef struct
{
    uint16_t wifi_password_len;                     // 密文长度(含nonce)，0=无
    uint16_t device_token_len;                      // 密文长度(含nonce)，0=无
    uint8_t  wifi_password_enc[SFM_WIFI_PASSWORD_ENC_MAX];
    uint8_t  device_token_enc[SFM_DEVICE_TOKEN_ENC_MAX];
} ServiceSecretStore_Def_t;


#pragma pack(4)
typedef struct
{
    uint8_t factory_flag;
    uint32_t config_version;      // 显式参数版本（Req 19.1；迁移逻辑见任务7.7）
    ServiceFilm_Def_t film;
    ServiceSleep_Def_t sleep;
    ServiceNetwork_Def_t network;
    ServiceSync_Def_t sync;        // Wi-Fi 同步状态参数
    ServiceSecretStore_Def_t secrets; // 敏感字段至持久化密文（明文见 network）
} ServiceParam_Def_t; /*服务参数*/
#pragma pack()

/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */


/*********************************************************************
 * GLOBAL VARIABLES
 */
extern ServiceParam_Def_t g_service_param;

/*********************************************************************
 * LOCAL FUNCTIONS
 */


/*********************************************************************
 * GLOBAL FUNCTIONS
 */
extern void service_param_init(void);
extern void service_param_save(void);
extern void service_param_reset(void);

/*
 * Setters for the Wi-Fi sync provisioning fields (used by the BLE provisioning
 * path). Each performs a bounded, NUL-terminated copy into
 * g_service_param and does NOT persist by itself — the caller invokes
 * service_param_save() to persist (encrypting the sensitive fields at rest).
 * The token/password setters take PLAINTEXT; encryption at rest is handled by
 * the parameter service on save.
 */
extern void service_param_set_manifest_url(const char *url);
extern void service_param_set_device_id(const char *id);
extern void service_param_set_device_token(const char *token_plain);
extern void service_param_set_wifi(const char *ssid, const char *password_plain);
extern void service_param_set_poll_interval(uint32_t seconds);
extern void service_param_set_last_applied_version(uint32_t version);
extern void service_param_set_last_sync_error(const char *err);

/*
 * 写入唤醒时间表（F3）。全有或全无：任一 slot 非法（>= 1440）或 count 超上限时
 * 整条拒绝并返回 false，**不做部分写入** —— 半张时间表会让设备在一部分饭点醒、
 * 另一部分不醒，而 App 那边显示的是完整配置，两边不一致却没有任何报错。
 *
 * 与其它 setter 一致：本函数不落盘，调用方自行 service_param_save()。
 *
 * @param slots            唤醒点数组（当地时间分钟数）；count==0 时可为 NULL
 * @param count            有效个数，必须 <= WAKE_SCHEDULE_MAX_SLOTS
 * @param fallback_minutes 兜底周期（分钟），0 表示不启用兜底
 * @return true 已写入；false 参数非法、g_service_param 未改动
 */
extern bool service_param_set_wake_schedule(const uint16_t *slots, uint8_t count,
                                            uint16_t fallback_minutes);

/*
 * —— 置顶自定义画面（pinned film）的持久化 ——
 *
 * ⚠️ 这三个函数刻意【不】走 g_service_param / PARAMS blob，而是在**同一个 NVS
 * namespace（SFM_NVS）下用一个独立的 blob key（SYS_M_NVS_PINNED_KEY_NAME =
 * "PINNED"）**自成一条记录。理由是本文件开头 ServiceParam_Def_t 上方那段警告的直接
 * 把显示偏好留在独立记录可避免为无关字段再次扩展 PARAMS schema，也让该配置损坏时
 * 只回落为“未置顶”，不影响配网。
 *
 * 独立 key 的代价是两条记录不在同一次 commit 里（理论上可能一条写成功另一条没写）。
 * 这个代价可以接受：两者语义正交（pin 是显示偏好，PARAMS 是配网/休眠），任一缺失都
 * 有确定的回落行为（pin 缺失 = 未设置 = 行为与没有本功能时一致）。
 *
 * 记录自带 magic + version + body_len 头。**记录不存在、长度不符、magic 不匹配都
 * 一律视为「未设置」**（load 返回 false 并把 out 置成 OFF），绝不因此重置别的东西 ——
 * 这正是独立 key 的全部意义。
 */

/**
 * @brief 读取 pin 配置。
 *
 * @param out 输出（NULL 时直接返回 false）。**无论成功与否都会被填成一个确定的值**：
 *            失败时是 mode == FILM_PIN_OFF 的全零配置，调用方可以不判返回值直接用。
 * @return true 读到一条合法记录；false 记录不存在 / 布局不符 / 内容非法（= 未设置）
 */
extern bool service_param_pinned_load(film_pin_cfg_t *out);

/**
 * @brief 写入 pin 配置（立即落盘，与其它 setter 不同：它不属于 PARAMS blob，
 *        service_param_save() 不会捎带它）。
 *
 * 全有或全无：先经 film_pin_cfg_is_valid() 校验，非法则**一个字节都不写**并返回
 * false（与 0x48 唤醒时间表同一约定 —— 半份配置会让设备在一部分时间显示对、另一部分
 * 显示错，而 App 显示的是完整配置，两边不一致却没有任何一处报错）。
 *
 * @param cfg 待写入的配置
 * @return true 已落盘；false 配置非法或 NVS 写入失败（记录保持原样）
 */
extern bool service_param_pinned_save(const film_pin_cfg_t *cfg);

/**
 * @brief 擦除 pin 记录（= 取消 pin）。
 *
 * 「取消 pin」的落地方式是**删掉整条记录**而不是写一条 mode=OFF 的记录：少一种
 * 需要解释的中间状态，且恢复出厂时能真正做到「什么都不剩」。记录本来就不存在时是
 * no-op。恢复出厂路径（BLE 0x22 与 service_param_reset()）都会调用它 —— 否则恢复
 * 出厂后自定义图还挂着，而用户以为清干净了。
 */
extern void service_param_pinned_erase(void);


#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_PARAM_H__ */
