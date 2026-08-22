/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_service/src/service_wifi.c
 * Author: Kiritro  Version: v0.1  Date: 2026/7/20
 * Description: WiFi服务
 *
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 * Upstream: FrameFilm (GPL-3.0-or-later) — see ../../NOTICE.md and LICENSE.
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"   /* 上屏结果回报的二值信号量 */

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
/* 流式同步只用固定的栈上分块缓冲，不需要 esp_heap_caps.h（Req 12.3）。 */
#include "esp_log.h"
#include "esp_timer.h"       /* esp_timer_get_time()：链路自愈退避用的单调时钟 */
#include "esp_netif_sntp.h"
#include "esp_app_desc.h"

#include "hal_api.h"
#include "sys_cfg.h"
#include "sys_log.h"
#include "service_param.h"
#include "service_file.h"
#include "service_wifi.h"
#include "service_monitor.h"  /* 手动同步成功后回通知每日记账，见该头文件的说明 */
#include "wifi_sync_gate.h"
#include "wifi_sync_yield.h"
#include "wifi_link_watch.h" /* 醒着期间的链路自愈判定（纯逻辑，host 可测） */
#include "manifest_parse.h"
#include "sync_url.h"   /* manifest_url -> 同源兄弟端点地址（纯逻辑，宿主可测） */
#include "film_pin.h"        /* film_pin_name_is_menu_slot()：整套下载后的补集清理判据 */
#include "sync_decision.h"

/*********************************************************************
 * MACROS
 */
#define WIFI_SERVICE_TAG        "wifi_service"

/* Event-group bits driven by wifi_event_handler and waited on by the bounded
 * connect (service_wifi_connect_blocking). CONNECTED = got IP; FAIL = the STA
 * got disconnected while an attempt was pending. */
#define WIFI_CONNECTED_BIT      (1u << 0)
#define WIFI_FAIL_BIT           (1u << 1)

/* --- HTTPS manifest sync + streaming download tunables --- */
/* Per-request HTTPS timeout (connect + I/O), milliseconds. */
#define SFM_WIFI_HTTP_TIMEOUT_MS    (30000)
/* Fixed streaming read chunk. A SMALL bounded buffer — the .film body is piped
 * chunk-by-chunk straight into the file transaction, so the whole 209120-byte
 * file is NEVER held in RAM nor re-expanded in a PSRAM buffer (Req 12.3). Kept
 * small so it does not inflate the sync task's stack peak during the TLS
 * handshake (which shares the same task stack). */
#define SFM_WIFI_DL_CHUNK_BYTES     (1024u)
/* Committed target base name for the Wi-Fi sync path (safe base filename; the
 * transaction writes current.film.tmp then atomically renames to this). */
#define SFM_WIFI_FILM_FILENAME      "current.film"
/* 补集清理时暂存待删文件名的缓冲长度。取 manifest 条目名上限 + 1：待删的正是由某一版
 * manifest 下发的菜单图，其名字必然通过过同一条长度校验。 */
#define SFM_WIFI_STALE_NAME_MAX     (MANIFEST_ITEM_NAME_BUFSZ)
/* One-shot SYNC_NOW task stack: the TLS handshake + esp_http_client + cert
 * bundle verification are stack-hungry (/ 7.5 may tune per context). */
#define SFM_WIFI_SYNC_TASK_STACK    (10240)

/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */
static bool g_wifi_initialized = false;
static bool g_wifi_connected = false;
static bool g_wifi_netif_ready = false;
static esp_netif_t *g_wifi_sta_netif = NULL;
static esp_event_handler_instance_t g_wifi_event_instance = NULL;
static esp_event_handler_instance_t g_ip_event_instance = NULL;

/* Signals STA connect result to the bounded connect waiter. Created in
 * service_wifi_init(). */
static EventGroupHandle_t g_wifi_event_group = NULL;

/* Tracks whether a one-shot SNTP session is currently initialized, so
 * service_wifi_sntp_sync() can deinit a previous session before re-arming and
 * never leaves the SNTP poller running (avoids a background cert-time retry). */
static bool g_sntp_started = false;

/* service_wifi_sync_once() 与 request-refresh 共用同一个原子 try-enter 门控。网络
 * 操作不在临界区内执行，临界区只保护这一位状态。 */
static bool g_sync_in_progress = false;
static portMUX_TYPE g_sync_mux = portMUX_INITIALIZER_UNLOCKED;

/* A version is durable only after one of its committed files reaches the EPD.
 * RAM-only is deliberate: power loss before refresh must cause a re-download. */
static bool g_pending_display_active = false;
static uint32_t g_pending_display_version = 0;
static uint8_t g_pending_display_name_count = 0;
static char g_pending_display_names[MANIFEST_MAX_ITEMS][MANIFEST_ITEM_NAME_BUFSZ];

/* 上屏结果回报（Task 15 单次唤醒事务）。
 *
 * 为什么需要：manifest 请求头 X-Device-Applied-Version 只能在**下一次**唤醒时带上去，
 * 而定时唤醒之间隔着几小时。那段时间里后端只知道文件被取走了，无法区分「已上屏」和
 * 「落盘后刷新失败、屏幕还是旧的」—— 后者的现象是冰箱上贴着昨天的菜单而云端一切正常。
 *
 * 为什么不在钩子里直接发 HTTP：service_wifi_display_completed/failed() 由 film / file
 * 任务调用，那两个任务串行负责 SD 卡 IO 与电子纸刷新，在里面做阻塞的 TLS 握手会把
 * 显示流水线一起卡住。所以钩子只记结果并给信号，由持有网络的唤醒任务取走并发送。 */
static SemaphoreHandle_t g_display_result_sem = NULL;
static uint32_t g_display_result_version = 0;
static bool g_display_result_ok = false;

/* 本次唤醒 manifest 里带来的固件升级描述。
 *
 * 为什么要暂存而不是在同步流程里就地执行 OTA：画面刷新是**异步排队**的
 * （service_file_display_for_now_async），同步函数返回时屏还没画。在那里刷完固件
 * 重启，这一版画面就永远画不上去了 —— 设备升级成功但冰箱上还是昨天的菜单。
 * 所以 OTA 要等上屏完成之后，由低功耗管理器在同一次唤醒的末尾发起。 */
static manifest_firmware_t g_pending_firmware = {0};

/* 对外的取图状态查询由 g_sync_in_progress 派生。 */

/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data);

static bool wifi_build_sta_config(wifi_config_t *out)
{
    size_t ssid_len = strnlen(g_service_param.network.wifi_ssid, SFM_WIFI_SSID_MAX);
    size_t password_len = strnlen(g_service_param.network.wifi_password,
                                  SFM_WIFI_PASSWORD_MAX);

    if(out == NULL || ssid_len == 0u || ssid_len > SFM_WIFI_SSID_MAX_BYTES ||
       ssid_len > sizeof(out->sta.ssid))
    {
        sys_logw(WIFI_SERVICE_TAG, "WiFi SSID length invalid: %u", (unsigned)ssid_len);
        return false;
    }
    if(password_len >= sizeof(out->sta.password))
    {
        sys_logw(WIFI_SERVICE_TAG, "WiFi password length invalid");
        return false;
    }

    memset(out, 0, sizeof(*out));
    memcpy(out->sta.ssid, g_service_param.network.wifi_ssid, ssid_len);
    memcpy(out->sta.password, g_service_param.network.wifi_password, password_len);
    return true;
}

static bool wifi_sync_try_enter(void)
{
    bool entered = false;
    portENTER_CRITICAL(&g_sync_mux);
    entered = wifi_sync_run_try_enter(&g_sync_in_progress, g_pending_display_active);
    portEXIT_CRITICAL(&g_sync_mux);
    return entered;
}

static void wifi_sync_leave(void)
{
    portENTER_CRITICAL(&g_sync_mux);
    wifi_sync_run_leave(&g_sync_in_progress);
    portEXIT_CRITICAL(&g_sync_mux);
}

static void wifi_pending_display_arm(uint32_t version,
                                     const char *const names[], uint8_t count)
{
    if(count > MANIFEST_MAX_ITEMS)
    {
        count = MANIFEST_MAX_ITEMS;
    }

    portENTER_CRITICAL(&g_sync_mux);
    g_pending_display_version = version;
    g_pending_display_name_count = count;
    memset(g_pending_display_names, 0, sizeof(g_pending_display_names));
    for(uint8_t i = 0; i < count; i++)
    {
        if(names[i] != NULL)
        {
            strncpy(g_pending_display_names[i], names[i], MANIFEST_ITEM_NAME_MAXLEN);
        }
    }
    g_pending_display_active = (count > 0u);
    portEXIT_CRITICAL(&g_sync_mux);
}


/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * [service_wifi_init 初始化WiFi]
 */
void service_wifi_init(void)
{
    if(g_wifi_initialized)
    {
        sys_logi(WIFI_SERVICE_TAG, "WiFi already initialized");
        return;
    }

    if(!g_service_param.network.wifi_enable)
    {
        sys_logi(WIFI_SERVICE_TAG, "WiFi disabled, skip init");
        return;
    }

    /* 上屏结果回报用的二值信号量。建在这里而不是首次使用时惰性创建：
     * 给信号的是 film / file 任务，惰性创建会让两个任务竞争同一次 xSemaphoreCreate。 */
    if(g_display_result_sem == NULL)
    {
        g_display_result_sem = xSemaphoreCreateBinary();
        if(g_display_result_sem == NULL)
        {
            sys_logw(WIFI_SERVICE_TAG, "display result sem create failed, "
                                       "applied-report will defer to next wake");
        }
    }

    if(!g_wifi_netif_ready)
    {
        ESP_ERROR_CHECK(esp_netif_init());
        esp_event_loop_create_default();
        g_wifi_sta_netif = esp_netif_create_default_wifi_sta();
        if(g_wifi_sta_netif == NULL)
        {
            sys_loge(WIFI_SERVICE_TAG, "create default WiFi STA netif failed");
            return;
        }
        ESP_ERROR_CHECK(esp_netif_set_hostname(g_wifi_sta_netif, SYS_WIFI_HOSTNAME));
        g_wifi_netif_ready = true;
    }

    if(g_wifi_event_group == NULL)
    {
        g_wifi_event_group = xEventGroupCreate();
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    if(g_wifi_event_instance == NULL)
    {
        ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                            ESP_EVENT_ANY_ID,
                                                            &wifi_event_handler,
                                                            NULL,
                                                            &g_wifi_event_instance));
    }
    if(g_ip_event_instance == NULL)
    {
        ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                            IP_EVENT_STA_GOT_IP,
                                                            &wifi_event_handler,
                                                            NULL,
                                                            &g_ip_event_instance));
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    g_wifi_initialized = true;

    sys_logi(WIFI_SERVICE_TAG, "WiFi initialized");
}

/**
 * [service_wifi_deinit 反初始化WiFi]
 */
void service_wifi_deinit(void)
{
    if(!g_wifi_initialized)
    {
        return;
    }

    /* Ensure the one-shot SNTP poller is not left running across a deinit. */
    if(g_sntp_started)
    {
        esp_netif_sntp_deinit();
        g_sntp_started = false;
    }

    esp_wifi_disconnect();
    esp_wifi_stop();
    esp_wifi_deinit();

    g_wifi_connected = false;
    g_wifi_initialized = false;

    /* Keep g_wifi_event_group allocated (recreated only when NULL in init) to
     * avoid racing the still-registered event handler; just reset its bits. */
    if(g_wifi_event_group)
    {
        xEventGroupClearBits(g_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    }

    sys_logi(WIFI_SERVICE_TAG, "WiFi deinitialized");
}

/**
 * [service_wifi_connect 连接WiFi]
 */
void service_wifi_connect(void)
{
    if(g_wifi_connected)
    {
        sys_logi(WIFI_SERVICE_TAG, "Disconnecting before reconnect");
        esp_wifi_disconnect();
        g_wifi_connected = false;
    }

    if(!g_wifi_initialized)
    {
        service_wifi_init();
    }

    if(!g_wifi_initialized)
    {
        sys_logw(WIFI_SERVICE_TAG, "WiFi init failed");
        return;
    }

    wifi_config_t wifi_config;
    if(!wifi_build_sta_config(&wifi_config))
    {
        return;
    }

    if(g_wifi_event_group)
    {
        xEventGroupClearBits(g_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    }

    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    if(err == ESP_ERR_WIFI_CONN)
    {
        sys_logi(WIFI_SERVICE_TAG, "STA already connecting; keep current attempt");
        return;
    }
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "esp_wifi_set_config failed: %d", err);
        return;
    }

    err = esp_wifi_connect();
    if(err != ESP_OK && err != ESP_ERR_WIFI_CONN)
    {
        sys_logw(WIFI_SERVICE_TAG, "esp_wifi_connect failed: %d", err);
        return;
    }

    /* Do not log the SSID value (avoid leaking network identifiers per task
     * 5.11 logging guidance); the password is never logged. */
    sys_logi(WIFI_SERVICE_TAG, "Connecting to configured SSID");
}

/**
 * [service_wifi_disconnect 断开WiFi]
 */
void service_wifi_disconnect(void)
{
    if(!g_wifi_initialized)
    {
        return;
    }

    esp_wifi_disconnect();
    g_wifi_connected = false;

    sys_logi(WIFI_SERVICE_TAG, "WiFi disconnected");
}

/**
 * [service_wifi_get_connect_status 获取WiFi连接状态]
 * @return 0：未连接 1：已连接
 */
uint8_t service_wifi_get_connect_status(void)
{
    return g_wifi_connected ? 1 : 0;
}

/**
 * [service_wifi_clear_config 清除网络配置信息]
 */
void service_wifi_clear_config(void)
{
    if(g_wifi_initialized)
    {
        service_wifi_disconnect();
        service_wifi_deinit();
    }

    g_service_param.network.wifi_enable = 0;
    memset(g_service_param.network.wifi_ssid, 0, sizeof(g_service_param.network.wifi_ssid));
    memset(g_service_param.network.wifi_password, 0, sizeof(g_service_param.network.wifi_password));
    memset(g_service_param.network.film_api_url, 0, sizeof(g_service_param.network.film_api_url));

    service_param_save();

    sys_logi(WIFI_SERVICE_TAG, "WiFi config cleared");
}

/**
 * [service_wifi_connect_blocking 阻塞式 STA 连接（单次尝试，有界超时）]
 * 见 service_wifi.h。单次尝试，超时/失败即停止本次连接，不在内部重试
 * （重试与退避由 service_wifi_sync_prepare 负责，Req 11）。
 */
esp_err_t service_wifi_connect_blocking(uint32_t timeout_ms)
{
    if(g_wifi_connected)
    {
        return ESP_OK;
    }

    if(!g_wifi_initialized)
    {
        service_wifi_init();
    }

    if(!g_wifi_initialized || g_wifi_event_group == NULL)
    {
        sys_logw(WIFI_SERVICE_TAG, "WiFi not ready for connect");
        return ESP_ERR_INVALID_STATE;
    }

    wifi_config_t wifi_config;
    if(!wifi_build_sta_config(&wifi_config))
    {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    if(err == ESP_ERR_WIFI_CONN)
    {
        /* STA 正处于 connecting，驱动不允许在这个状态下改配置（回 0x3006 = 12294，
         * 日志里那句 "sta is connecting, cannot set config" 就是它）。
         *
         * 若直接返回失败，service_wifi_sync_prepare() 的每次退避都会撞上同一个状态，
         * 耗尽后记 "wifi connect failed"，而设备一次都没真正拿这份配置去连过。
         *
         * 先显式断开让驱动回到 idle，再重试一次。只重试一次：这个状态是确定可解的
         * （disconnect 是同步生效的状态迁移），失败两次说明是别的原因，继续重试只会
         * 把真实错误藏得更深。 */
        sys_logw(WIFI_SERVICE_TAG,
                 "set_config rejected (STA busy connecting), disconnect and retry once");
        (void)esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(100));
        err = esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    }
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "esp_wifi_set_config failed: %d", err);
        return err;
    }

    /* Arm the waiter, then kick off a single connect attempt. */
    xEventGroupClearBits(g_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    err = esp_wifi_connect();
    if(err != ESP_OK && err != ESP_ERR_WIFI_CONN)
    {
        sys_logw(WIFI_SERVICE_TAG, "esp_wifi_connect failed: %d", err);
        return err;
    }

    /* Wait up to timeout_ms for either an IP (success) or a disconnect (fail).
     * design.md step 1: STA connect timeout 10s. */
    EventBits_t bits = xEventGroupWaitBits(g_wifi_event_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(timeout_ms));

    if(bits & WIFI_CONNECTED_BIT)
    {
        return ESP_OK;
    }

    /* Timeout or explicit failure: stop the pending attempt so the driver does
     * not linger and keep fast-retrying on its own. */
    esp_wifi_disconnect();

    if(bits & WIFI_FAIL_BIT)
    {
        sys_logw(WIFI_SERVICE_TAG, "STA connect attempt failed");
        return ESP_FAIL;
    }
    sys_logw(WIFI_SERVICE_TAG, "STA connect attempt timed out");
    return ESP_ERR_TIMEOUT;
}

/**
 * [service_wifi_sntp_sync 一次性 SNTP 时间同步（有界超时）]
 * 见 service_wifi.h。成功判定由纯谓词 wifi_sync_gate_time_synced() 给出。
 * 无论成功与否都在返回前 deinit，绝不留下后台 SNTP 轮询（Req 11.3：不进入证书
 * 时间校验失败重试循环）。
 */
esp_err_t service_wifi_sntp_sync(uint32_t timeout_ms)
{
    if(!g_wifi_connected)
    {
        return ESP_ERR_INVALID_STATE;
    }

    /* Re-arm cleanly if a previous one-shot session was left initialized. */
    if(g_sntp_started)
    {
        esp_netif_sntp_deinit();
        g_sntp_started = false;
    }

    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_err_t err = esp_netif_sntp_init(&config);
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "SNTP init failed: %d", err);
        return err;
    }
    g_sntp_started = true;

    /* ONE-SHOT: wait once for the time to be set, then judge plausibility with
     * the pure gate predicate (cold-boot 1970 must fail — A1). */
    err = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(timeout_ms));

    bool synced = false;
    if(err == ESP_OK)
    {
        time_t now = 0;
        time(&now);
        synced = wifi_sync_gate_time_synced((int64_t)now);
    }

    /* Always stop the poller — do not keep retrying in the background. */
    esp_netif_sntp_deinit();
    g_sntp_started = false;

    if(synced)
    {
        /* 设置时区后 localtime() 才给出本地时间。SNTP 得到的是 UTC，不设时区会偏 8 小时，
         * 「按当前时段展示对应菜单」就会在错误的时间切换。CST-8 是 POSIX 写法
         * （UTC+8 记作 -8），无夏令时。 */
        setenv("TZ", SFM_LOCAL_TZ, 1);
        tzset();
        sys_logi(WIFI_SERVICE_TAG, "SNTP time synced (TZ=%s)", SFM_LOCAL_TZ);
        return ESP_OK;
    }

    sys_logw(WIFI_SERVICE_TAG, "SNTP time sync failed (giving up this cycle)");
    return ESP_ERR_TIMEOUT;
}

/**
 * [service_wifi_time_is_synced 查询墙钟时间是否已同步]
 */
uint8_t service_wifi_time_is_synced(void)
{
    /* 深睡唤醒会重启进程但 RTC 墙钟继续走；TZ 环境不会保留。每次查询都恢复本地时区，
     * 这样当天已同步后，午晚餐只做本地切图时 localtime() 仍按家庭时区计算。 */
    setenv("TZ", SFM_LOCAL_TZ, 1);
    tzset();
    time_t now = 0;
    time(&now);
    return wifi_sync_gate_time_synced((int64_t)now) ? 1 : 0;
}

/* [wifi_log_visible_aps 扫一次并把周围 2.4GHz AP 打进串口（仅用于连接失败后的诊断）]
 *
 * 阻塞约 2 秒（主动扫描全信道）。只在连接彻底失败后调用一次，见调用点的注释。
 * 打印 SSID / 信道 / RSSI / authmode —— 全是空口里本来就明文广播的信息。 */
static void wifi_log_visible_aps(void)
{
    /* 同一次启动内只扫一次。重复扫没有新信息：这份列表在一次启动周期里唯一有意义的变化
     * 是「目标 AP 出现了」，而那时连接会成功，根本走不到这里 —— 而每次重复都要多烧 2 秒
     * 射频（设备靠 USB 长时间醒着调试时，失败会一轮接一轮）。
     *
     * 刻意**不**跨唤醒保持（不用 RTC_DATA_ATTR）：深睡唤醒等于重启，而两次唤醒之间 AP 环境
     * 完全可能变化（用户把热点打开了、换了路由）。这份列表正是「热点到底开没开」的唯一证据 ——
     * 本次真机排障就是靠它确认配的 SSID 不在可见列表里。为省 2 秒把这个证据永久关掉不值得。
     *
     * 无需加锁：这里只从 sync_prepare 那一条链路进来，且竞争的最坏后果是多扫一次。 */
    static bool s_scanned_this_boot = false;
    if(s_scanned_this_boot)
    {
        sys_logw(WIFI_SERVICE_TAG, "ap scan: 本次启动已扫过一次，不再重复（见前面那份列表）");
        return;
    }

    /* 上限 20：一份列表的用途是「我配的那个在不在里面」，20 个足够覆盖住宅与办公环境，
     * 而每条 wifi_ap_record_t 约 80 字节，20 条 1.6KB 放在堆上一次性申请。 */
    const uint16_t max_ap = 20;
    wifi_ap_record_t *records = (wifi_ap_record_t *)malloc(sizeof(wifi_ap_record_t) * max_ap);
    if(records == NULL)
    {
        sys_logw(WIFI_SERVICE_TAG, "ap scan: oom, skip diagnostic scan");
        return;
    }

    wifi_scan_config_t scan_cfg = {0};   /* 全零 = 扫全部信道、不限 SSID、主动扫描 */
    esp_err_t err = esp_wifi_scan_start(&scan_cfg, true);   /* true = 阻塞到扫完 */
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "ap scan failed: %d", (int)err);
        free(records);
        return;
    }

    uint16_t found = max_ap;
    err = esp_wifi_scan_get_ap_records(&found, records);
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "ap scan get records failed: %d", (int)err);
        free(records);
        return;
    }

    /* 扫描确实完成了才记账：oom / scan_start 失败的路径上不设标记，
     * 否则一次偶发失败会把本次启动的诊断能力永久关掉。 */
    s_scanned_this_boot = true;
    sys_logw(WIFI_SERVICE_TAG,
             "===== 诊断：本机能看到的 2.4GHz AP（%u 个）=====", (unsigned)found);
    bool target_seen = false;
    for(uint16_t i = 0; i < found; i++)
    {
        /* SSID 可能不是 NUL 结尾（33 字节数组，32 字符满时无终止符），按定长打印。 */
        char ssid[33];
        memcpy(ssid, records[i].ssid, sizeof(records[i].ssid) > 32 ? 32 : sizeof(records[i].ssid));
        ssid[32] = '\0';
        sys_logw(WIFI_SERVICE_TAG, "  [%2u] ch=%2u rssi=%4d auth=%u  \"%s\"",
                 (unsigned)i, (unsigned)records[i].primary, (int)records[i].rssi,
                 (unsigned)records[i].authmode, ssid);
        if(strcmp(ssid, g_service_param.network.wifi_ssid) == 0)
        {
            target_seen = true;
        }
    }
    if(target_seen)
    {
        /* 能扫到却连不上：那就不是 5GHz、也不是拼写问题，把方向指向密码与加密方式。 */
        sys_logw(WIFI_SERVICE_TAG,
                 "配置的 SSID **在**上面这份列表里 -> 不是 5GHz/拼写问题，"
                 "请检查密码；也看一眼上面那条 reason 码");
    }
    else
    {
        /* 一个都没匹配上：要么 AP 是 5GHz（本机射频看不到），要么名字不一致，
         * 要么不在范围内。这三条正是需要人去现场确认的。 */
        sys_logw(WIFI_SERVICE_TAG,
                 "配置的 SSID **不在**上面这份列表里 -> AP 可能是 5GHz（本芯片仅 2.4GHz）、"
                 "SSID 名字不一致（大小写/空格/中文），或不在覆盖范围内");
    }
    sys_logw(WIFI_SERVICE_TAG, "===== 诊断结束 =====");
    free(records);
}

/**
 * [service_wifi_sync_prepare 连接 + SNTP 门控前置条件]
 * 见 service_wifi.h。连接（有限退避，单次 10s）+ 一次性 SNTP，经纯真值表
 * wifi_sync_gate_eval() 判定是否放行 HTTPS。任何门控失败都记录非机密
 * last_sync_error、返回错误、保画面、降级 BLE，且不进入证书时间重试循环。
 */
esp_err_t service_wifi_sync_prepare(void)
{
    /* 单写者让路（Req 18.1 / 修正项 D10）：在做任何连接/SNTP/下载、
     * 尤其在发起任何文件写事务之前，先检查是否已存在【进行中的 BLE 文件事务】。
     * 若是，则本轮定时 Wi-Fi 同步主动让路——记录非机密的 skip 说明、返回一个可区分
     * 的 busy/skip 结果，且【不启动任何写事务】，从而唤醒期保持单写者、避免两来源
     * 争用文件写事务。让路判定收敛在纯谓词 wifi_sync_should_yield_to_ble()（host 可
     * 测）。注意：即便此处未让路，文件事务侧仍会在真正 begin 时对并发做 busy 兜底
     * （file_txn_begin → FILE_TXN_ERR_BUSY，Req 18.2/18.3）。 */
    file_receive_state_t rx = service_file_get_receive_state();
    if(wifi_sync_should_yield_to_ble(rx.state, rx.owner))
    {
        service_param_set_last_sync_error("skip: BLE file transfer in progress");
        sys_logi(WIFI_SERVICE_TAG,
                 "sync skip: BLE file transaction active -> yield, no write txn (Req 18.1)");
        return ESP_ERR_NOT_FINISHED;
    }

    /* Precondition guards: not provisioned / disabled → abandon, keep screen. */
    if(!g_service_param.network.wifi_enable)
    {
        service_param_set_last_sync_error("wifi disabled");
        sys_logw(WIFI_SERVICE_TAG, "WiFi disabled, skip sync");
        return ESP_ERR_INVALID_STATE;
    }

    if(!g_wifi_initialized)
    {
        service_wifi_init();
    }

    if(!g_wifi_initialized || g_wifi_event_group == NULL)
    {
        service_param_set_last_sync_error("wifi init failed");
        sys_logw(WIFI_SERVICE_TAG, "WiFi init failed, skip sync");
        return ESP_ERR_INVALID_STATE;
    }

    if(strlen(g_service_param.network.wifi_ssid) == 0)
    {
        service_param_set_last_sync_error("not provisioned");
        sys_logw(WIFI_SERVICE_TAG, "WiFi not provisioned, skip sync");
        return ESP_ERR_INVALID_STATE;
    }

    /* Step 1: STA connect with a BOUNDED backoff retry loop (design step 1).
     * Each attempt has a 10s timeout; between attempts we wait an
     * exponential-but-clamped backoff so a flapping AP can never cause an
     * infinite fast-reconnect. */
    bool connected = g_wifi_connected;
    for(uint32_t attempt = 0;
        !connected && wifi_connect_should_retry(attempt, SFM_WIFI_CONNECT_MAX_ATTEMPTS);
        attempt++)
    {
        if(attempt > 0)
        {
            uint32_t backoff_ms = wifi_connect_backoff_ms(attempt - 1);
            sys_logi(WIFI_SERVICE_TAG, "STA reconnect backoff %u ms (attempt %u)",
                     (unsigned)backoff_ms, (unsigned)(attempt + 1));
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        }

        if(service_wifi_connect_blocking(SFM_WIFI_CONNECT_TIMEOUT_MS) == ESP_OK)
        {
            connected = true;
        }
    }

    /* Step 2: connected? If not, fall through the gate as NO_LINK. */
    if(!connected)
    {
        wifi_sync_gate_result_t gate = wifi_sync_gate_eval(false, false);
        service_param_set_last_sync_error(wifi_sync_gate_result_str(gate));
        sys_logw(WIFI_SERVICE_TAG, "sync gate: %s", wifi_sync_gate_result_str(gate));
        /* 连不上时扫一次并列出周围的 2.4GHz AP。
         *
         * 为什么值得花这 2 秒：`reason=201`（NO_AP_FOUND）只说明「扫不到配的那个 SSID」，
         * 而这有三种完全不同的成因 —— AP 只开了 5GHz（芯片没有 5GHz 射频，改密码永远无效）、
         * SSID 拼写/编码不一致、不在覆盖范围内。没有这份列表，现场只能靠反复改密码来试，
         * 而那恰好是三种成因里唯一试不出结果的方向。
         *
         * 只在**连接已经彻底失败**后执行，所以正常路径上零开销；SSID 是非机密（0x33 本身
         * 就可读回本机 SSID），列出周围 AP 名不违反脱敏要求，密码与 token 一个字节都不打。 */
        wifi_log_visible_aps();
        return ESP_ERR_WIFI_NOT_CONNECT;
    }

    /* Step 3: connected → one-shot SNTP BEFORE any manifest HTTPS (Req 11.1).
     * A single bounded attempt; failure does NOT loop on cert-time errors. */
    bool time_synced = (service_wifi_sntp_sync(SFM_WIFI_SNTP_TIMEOUT_MS) == ESP_OK);

    wifi_sync_gate_result_t gate = wifi_sync_gate_eval(true, time_synced);
    if(!wifi_sync_gate_https_allowed(gate))
    {
        /* SNTP failed → abandon this cycle, record non-secret error, degrade to
         * BLE fallback, keep current screen, do NOT enter a cert-time retry
         * loop (Req 11.2, 11.3). */
        service_param_set_last_sync_error(wifi_sync_gate_result_str(gate));
        sys_logw(WIFI_SERVICE_TAG, "sync gate: %s", wifi_sync_gate_result_str(gate));
        return ESP_ERR_INVALID_STATE;
    }

    /* PROCEED: connected AND time synced → manifest HTTPS is allowed. */
    sys_logi(WIFI_SERVICE_TAG, "sync gate: %s (HTTPS allowed)",
             wifi_sync_gate_result_str(gate));
    return ESP_OK;
}

/*====================================================================
 * HTTPS manifest sync + streaming download into the unified
 * file transaction (Req 10.1, 10.5, 12.3).
 *
 * service_wifi_sync_once() is the full "sync once" entry the poll timer
 * and BLE SYNC_NOW call:
 *   1) gate    : service_wifi_sync_prepare() — yield-if-BLE-busy + connect +
 *                SNTP; proceed only on ESP_OK.
 *   2) manifest: GET {manifest_url} over HTTPS with "Authorization: Bearer
 *                {device_token}" (cert bundle), read into a BOUNDED buffer
 *                (<= MANIFEST_MAX_RESPONSE_BYTES) then manifest_parse().
 *                HTTP status must be 200.
 *   3) gate    : sync_should_download(version, last_applied, force);
 *                if not → end cleanly (no download, no refresh).
 *   4) download: GET {film_url} over HTTPS and STREAM the body chunk-by-chunk
 *                straight into service_file_receive_begin/_write — NO whole-file
 *                PSRAM buffering (Req 12.3). Enforce Content-Length == 209120.
 *   5) finish  : service_file_receive_finish(sha) validates length + .film
 *                header + SHA-256 and fsync+rename atomic-replaces current.film;
 *                only on that validated commit is last_applied_version persisted,
 *                then the display refresh is triggered (Req 10.5).
 *
 * Every exit path releases the HTTP client and, on failure, aborts the file
 * transaction (keeping the current screen unchanged) and records a NON-SECRET
 * last_sync_error. The device token / Authorization header are NEVER logged
 * (Req 13.5 / 15.4 / 20.4). HTTPS only (cert bundle).
 *===================================================================*/

/* [wifi_hex_val 单个十六进制字符 → 0..15；非法返回 -1] */
static int wifi_hex_val(char c)
{
    if(c >= '0' && c <= '9') return c - '0';
    if(c >= 'a' && c <= 'f') return (c - 'a') + 10;
    if(c >= 'A' && c <= 'F') return (c - 'A') + 10;   /* tolerate upper-case */
    return -1;
}

/* [wifi_sha256_hex_to_bytes manifest 的 64 位十六进制 sha256 → 32 原始字节]
 * manifest_parse() 已保证 sha256_hex 恰为 64 位小写十六进制；此处仍做防御式解析，
 * 以便把 finish() 需要的 32 字节原始摘要交给统一文件事务比对（Req 10.5）。
 * @return true 当且仅当恰 64 个十六进制字符后紧跟 '\0'。 */
static bool wifi_sha256_hex_to_bytes(const char *hex, uint8_t out[32])
{
    if(hex == NULL)
    {
        return false;
    }
    for(int i = 0; i < 32; i++)
    {
        int hi = wifi_hex_val(hex[(2 * i)]);
        int lo = wifi_hex_val(hex[(2 * i) + 1]);
        if(hi < 0 || lo < 0)
        {
            return false;
        }
        out[i] = (uint8_t)(((uint32_t)hi << 4) | (uint32_t)lo);
    }
    return hex[64] == '\0';   /* exactly 64 hex chars, then terminator */
}

/* [wifi_http_client_release 释放 HTTPS client（幂等、NULL 安全）]
 *
 * 所有【调用方侧】退出路径统一的 client 释放收口（Req 13.4）：关闭连接（释放
 * socket/TLS）再释放句柄（esp_http_client_close + esp_http_client_cleanup），随后把
 * 调用方的句柄指针置 NULL。任一退出路径都可安全调用：
 *   - *client 为 NULL 时为 no-op（begin_get 失败时已把 *out_client 置 NULL，且成功
 *     路径释放后本函数会把它清 NULL），故绝不重复 close/cleanup（避免二次释放）；
 *   - 绝不遗漏 close 直接 cleanup。
 * 注意：wifi_https_begin_get 内部对【尚未 open】的 client 只做 cleanup（此时无连接
 * 可 close），保持其原有释放语义，不经本 helper。 */
static void wifi_http_client_release(esp_http_client_handle_t *client)
{
    if(client == NULL || *client == NULL)
    {
        return;
    }
    esp_http_client_close(*client);
    esp_http_client_cleanup(*client);
    *client = NULL;
}

/* [wifi_https_begin_get 打开一个带 Bearer 鉴权的 HTTPS GET 并校验状态 200]
 *
 * 用 cert bundle（esp_crt_bundle_attach）建立【仅 HTTPS】连接（HTTP_TRANSPORT_
 * OVER_SSL），带上 "Authorization: Bearer {device_token}" 头（Req 10.1），打开连接、
 * fetch_headers，并要求 HTTP 状态码 == 200。成功时经 *out_client 返回【已打开】的
 * client（调用方负责 close+cleanup），并经 *out_content_len 返回声明长度（未知为 -1）；
 * 任一内部失败都在返回前 cleanup，不泄漏 client。绝不记录 device_token 或
 * Authorization 头（Req 13.5 / 15.4 / 20.4）。
 *
 * @return ESP_OK 且 *out_client 为已打开且状态 200 的 client；否则错误码。 */
static esp_err_t wifi_https_begin_get(const char *url,
                                      esp_http_client_handle_t *out_client,
                                      int *out_content_len)
{
    if(out_client)
    {
        *out_client = NULL;
    }
    if(out_content_len)
    {
        *out_content_len = -1;
    }
    if(url == NULL || out_client == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    esp_http_client_config_t config = {
        .url               = url,
        .method            = HTTP_METHOD_GET,
        .timeout_ms        = SFM_WIFI_HTTP_TIMEOUT_MS,
        .transport_type    = HTTP_TRANSPORT_OVER_SSL,   /* HTTPS only            */
        .crt_bundle_attach = esp_crt_bundle_attach,     /* cert bundle (TLS root) */
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if(client == NULL)
    {
        return ESP_FAIL;
    }

    /* Build "Bearer {token}" on the stack, set it as the Authorization header,
     * then scrub the local copy. The header value / token are NEVER logged. */
    {
        char auth[SFM_DEVICE_TOKEN_MAX + 16];
        int n = snprintf(auth, sizeof(auth), "Bearer %s",
                         g_service_param.network.device_token);
        if(n < 0 || n >= (int)sizeof(auth))
        {
            /* token too long to fit / encoding error → never send a truncated
             * credential. */
            memset(auth, 0, sizeof(auth));
            esp_http_client_cleanup(client);
            return ESP_ERR_INVALID_SIZE;
        }
        esp_err_t herr = esp_http_client_set_header(client, "Authorization", auth);
        memset(auth, 0, sizeof(auth));   /* scrub the on-stack credential */
        if(herr != ESP_OK)
        {
            esp_http_client_cleanup(client);
            return herr;
        }
    }

    /* 顺带上报设备状态（电量、固件版本），供后端记录、再由 App 展示。
     *
     * 复用本次 manifest 请求而不新增一条上报接口：设备平时深睡，多一次网络往来就多一次
     * 唤醒与射频开销，而 manifest 检查本来就周期性发生，把状态挂在它的请求头上零成本。
     *
     * 只报非机密信息（电量百分比、固件版本），绝不携带 SSID / 密码 / token 明文。
     * 后端读不到这些头时应照常工作（旧固件即如此），因此这里失败也不阻断同步。 */
    {
        char batbuf[8];
        int bat = hal_bat_get_percent();
        if(bat < 0)   { bat = 0; }
        if(bat > 100) { bat = 100; }
        if(snprintf(batbuf, sizeof(batbuf), "%d", bat) > 0)
        {
            (void)esp_http_client_set_header(client, "X-Device-Battery", batbuf);
        }
        /* 固件版本取自 app descriptor（ESP-IDF 标准来源，与 OTA 使用的是同一份元数据），
         * 不自造版本字符串以免出现两处不一致。 */
        const esp_app_desc_t *desc = esp_app_get_description();
        if(desc != NULL)
        {
            (void)esp_http_client_set_header(client, "X-Device-Firmware", desc->version);
        }
        char appliedbuf[16];
        int applied_len = snprintf(appliedbuf, sizeof(appliedbuf), "%u",
                                   (unsigned)g_service_param.sync.last_applied_version);
        if(applied_len > 0 && applied_len < (int)sizeof(appliedbuf))
        {
            (void)esp_http_client_set_header(client,
                                             "X-Device-Applied-Version",
                                             appliedbuf);
        }

        (void)esp_http_client_set_header(
            client, "X-Device-Sleep-Mode",
            g_service_param.sleep.sleep_mode ? "1" : "0");

        char slotsbuf[40] = "none";
        if(g_service_param.sleep.wake_slot_count > 0u)
        {
            size_t used = 0;
            slotsbuf[0] = '\0';
            for(uint8_t i = 0; i < g_service_param.sleep.wake_slot_count; i++)
            {
                int n = snprintf(slotsbuf + used, sizeof(slotsbuf) - used,
                                 i == 0 ? "%u" : ",%u",
                                 (unsigned)g_service_param.sleep.wake_slots[i]);
                if(n < 0 || (size_t)n >= sizeof(slotsbuf) - used)
                {
                    slotsbuf[0] = '\0';
                    break;
                }
                used += (size_t)n;
            }
        }
        if(slotsbuf[0] != '\0')
        {
            (void)esp_http_client_set_header(client, "X-Device-Wake-Slots", slotsbuf);
        }

        char numberbuf[16];
        int n = snprintf(numberbuf, sizeof(numberbuf), "%u",
                         (unsigned)g_service_param.sleep.wake_fallback_min);
        if(n > 0 && n < (int)sizeof(numberbuf))
        {
            (void)esp_http_client_set_header(
                client, "X-Device-Wake-Fallback-Minutes", numberbuf);
        }
        n = snprintf(numberbuf, sizeof(numberbuf), "%u",
                     (unsigned)g_service_param.sync.poll_interval_seconds);
        if(n > 0 && n < (int)sizeof(numberbuf))
        {
            (void)esp_http_client_set_header(
                client, "X-Device-Poll-Interval-Seconds", numberbuf);
        }
    }

    esp_err_t err = esp_http_client_open(client, 0);
    if(err != ESP_OK)
    {
        esp_http_client_cleanup(client);
        return err;
    }

    int content_len = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if(status != 200)
    {
        /* Status code is a non-secret integer; the URL/header are not logged. */
        sys_logw(WIFI_SERVICE_TAG, "HTTPS GET status %d (expected 200)", status);
        /* 把状态码写进 last_sync_error（App 通过 BLE 0x43 STATUS_QUERY 读回的就是这一条，
         * 见 service_ble.c 的 ble_send_status_query：payload[7]=err_len、payload[8..]=文本）。
         * 调用方原来一律写 "http open/status"，那分不出 401（后端不认令牌）、
         * 404（那个版本不存在）、5xx（后端故障）—— 三者的修法完全不同，而这是 App 上
         * 唯一能看到失败原因的地方。调用方现在会保留这条更精确的文案，见各调用点。 */
        {
            char msg[SFM_SYNC_ERR_MAX];
            snprintf(msg, sizeof(msg), "http %d%s", status,
                     (status == 401 || status == 403) ? " (device_token 被拒)" : "");
            service_param_set_last_sync_error(msg);
        }
        if(status == 401 || status == 403)
        {
            /* 401 只有一个成因：后端不认这台设备的 device_token —— 要么它从未被登记到
             * 后端（换后端、新数据库、卷重建都会这样），要么与本机 NVS 里的不一致。
             *
             * 把结论直接写出来，因为一个裸的「status 401」会把排查方向带到网络、证书、
             * 时间同步上去，而那几条此刻全是好的（TLS 握手已经成功、请求也发出去了）。
             * 修法也不在设备侧：设备上的 token 只写不可读，得在 App 设备页点「登记设备
             * 令牌」把 App 保存的那份 POST 给后端，不必重新配网。 */
            sys_logw(WIFI_SERVICE_TAG,
                     "  -> 后端不认这台设备的 device_token（未登记或与本机不一致）。"
                     "网络与证书都是好的，不用查那两项；"
                     "在 App 设备页点「登记设备令牌」即可恢复，无需重新配网");
        }
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_ERR_INVALID_RESPONSE;
    }

    *out_client = client;
    if(out_content_len)
    {
        *out_content_len = content_len;
    }
    return ESP_OK;
}

/* [wifi_https_post_empty 发一个空体的 HTTPS POST 并取回状态码]
 *
 * 与 wifi_https_begin_get 用完全相同的 client 配置（HTTP_TRANSPORT_OVER_SSL 只允许
 * HTTPS、esp_crt_bundle_attach 做根证书校验、同一个超时预算），只把 method 改成 POST
 * 并显式写 "Content-Length: 0"（不写的话 esp_http_client 在 open(len=0) 时不会生成该头，
 * 某些服务端/代理会一直等 body）。同样带 "Authorization: Bearer {device_token}"，
 * 同样【绝不记录】token 或 Authorization 头。
 *
 * 与 begin_get 的另一个区别：本函数不把 client 交给调用方——这个请求没有需要读取的
 * 响应体（后端立即返回 202，正文只是提示文本），拿到状态码就够了，所以在函数内部
 * 读完头就关掉，退出路径不留资源给调用方管理。
 *
 * @param url        目标 URL（必须 https）
 * @param out_status 输出 HTTP 状态码（打开成功时有效；失败置 0）
 * @return ESP_OK 表示请求发出且成功取到状态码（状态码本身由调用方判定），否则错误码。 */
static esp_err_t wifi_https_post_empty(const char *url, int *out_status)
{
    if(out_status)
    {
        *out_status = 0;
    }
    if(url == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    esp_http_client_config_t config = {
        .url               = url,
        .method            = HTTP_METHOD_POST,
        .timeout_ms        = SFM_WIFI_HTTP_TIMEOUT_MS,
        .transport_type    = HTTP_TRANSPORT_OVER_SSL,   /* HTTPS only            */
        .crt_bundle_attach = esp_crt_bundle_attach,     /* cert bundle (TLS root) */
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if(client == NULL)
    {
        return ESP_FAIL;
    }

    {
        char auth[SFM_DEVICE_TOKEN_MAX + 16];
        int n = snprintf(auth, sizeof(auth), "Bearer %s",
                         g_service_param.network.device_token);
        if(n < 0 || n >= (int)sizeof(auth))
        {
            /* 宁可不发，也不发一个被截断的凭据。 */
            memset(auth, 0, sizeof(auth));
            esp_http_client_cleanup(client);
            return ESP_ERR_INVALID_SIZE;
        }
        esp_err_t herr = esp_http_client_set_header(client, "Authorization", auth);
        memset(auth, 0, sizeof(auth));   /* 擦掉栈上的凭据副本 */
        if(herr != ESP_OK)
        {
            esp_http_client_cleanup(client);
            return herr;
        }
    }

    (void)esp_http_client_set_header(client, "Content-Length", "0");

    esp_err_t err = esp_http_client_open(client, 0);
    if(err != ESP_OK)
    {
        esp_http_client_cleanup(client);
        return err;
    }

    (void)esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if(out_status)
    {
        *out_status = status;
    }

    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ESP_OK;
}

/* [wifi_build_request_refresh_url 由 manifest_url 推导「请求重算菜单」的 URL]
 *
 * manifest_url 形如 https://host/api/devices/{id}/display/manifest；把结尾的 "manifest"
 * 换成 "request-refresh" 即得同一设备的重算入口。刻意从 manifest_url 推导而不是新增一个
 * 持久化字段：新增字段要改 ServiceParam_Def_t 的 NVS 布局，那会触发配置版本迁移并有
 * 重置用户配网的风险；而这两个地址在契约上本就同源同宿，推导不会引入新的失败模式。
 *
 * 不含该后缀（例如用户配了别的路径）就返回错误，由调用方记一条非机密日志后放弃 ——
 * 绝不「猜」一个地址出来打过去。
 *
 * @return ESP_OK 且 out 为 NUL 结尾的 URL；否则错误码。 */
static esp_err_t wifi_build_sibling_url(char *out, size_t out_sz, const char *suffix)
{
    switch(sync_url_build_sibling(out, out_sz,
                                  g_service_param.network.manifest_url, suffix))
    {
    case SYNC_URL_OK:        return ESP_OK;
    case SYNC_URL_ERR_TOO_LONG: return ESP_ERR_INVALID_SIZE;
    default:                 return ESP_ERR_INVALID_ARG;
    }
}

static esp_err_t wifi_build_request_refresh_url(char *out, size_t out_sz)
{
    return wifi_build_sibling_url(out, out_sz, "request-refresh");
}

/* [wifi_read_manifest_body 读取 manifest 响应体（有界）并解析]
 * 读入一个上界为 MANIFEST_MAX_RESPONSE_BYTES 的缓冲（Req 10.1「限制响应总大小」）：
 * 超过上界即拒绝，不做整树 DOM 展开。随后交 manifest_parse() 做 5 字段 + 长度/大小
 * 校验。任一失败都记录【非机密】last_sync_error。调用方负责 client 生命周期。 */
static esp_err_t wifi_read_manifest_body(esp_http_client_handle_t client,
                                         manifest_t *out)
{
    const int cap = (int)MANIFEST_MAX_RESPONSE_BYTES + 1;   /* +1 to detect > limit */
    char *buf = (char *)malloc((size_t)cap);
    if(buf == NULL)
    {
        service_param_set_last_sync_error("manifest oom");
        return ESP_ERR_NO_MEM;
    }

    int total = 0;
    esp_err_t err = ESP_OK;
    while(total < cap)
    {
        int r = esp_http_client_read(client, buf + total, cap - total);
        if(r < 0)
        {
            service_param_set_last_sync_error("manifest read error");
            err = ESP_FAIL;
            break;
        }
        if(r == 0)
        {
            break;   /* end of body */
        }
        total += r;
    }

    if(err == ESP_OK)
    {
        if(total > (int)MANIFEST_MAX_RESPONSE_BYTES)
        {
            service_param_set_last_sync_error("manifest too large");
            sys_logw(WIFI_SERVICE_TAG, "manifest response exceeds %u bytes -> reject",
                     (unsigned)MANIFEST_MAX_RESPONSE_BYTES);
            err = ESP_ERR_INVALID_SIZE;
        }
        else
        {
            manifest_err_t perr = manifest_parse(buf, (size_t)total, out);
            if(perr != MANIFEST_OK)
            {
                /* manifest_err_str() is a stable, non-secret diagnostic string. */
                service_param_set_last_sync_error(manifest_err_str(perr));
                sys_logw(WIFI_SERVICE_TAG, "manifest parse rejected: %s",
                         manifest_err_str(perr));
                err = ESP_FAIL;
            }
        }
    }

    free(buf);
    return err;
}

/* [wifi_download_film_streaming 流式下载 film_url 入统一文件事务并提交刷新]
 * 打开 Wi-Fi 写事务 → HTTPS GET film_url → 逐块流式写入事务（不缓冲整文件，Req 12.3）
 * → hex→bytes 转换 sha256 → finish 校验长度/头/SHA-256 后原子替换 → 登记待应用版本并
 * 触发显示刷新。显示任务成功返回后才保存 last_applied_version。任一失败都中止事务保画面、
 * 释放资源、记录非机密 last_sync_error。 */
static esp_err_t wifi_download_film_streaming(const manifest_t *m)
{
    /* Open the Wi-Fi write transaction FIRST (single-writer; returns busy if a
     * BLE txn holds ownership — the wake-time backstop for Req 18.2 / 18.3). */
    esp_err_t berr = service_file_receive_begin(FILE_SRC_WIFI,
                                                SFM_WIFI_FILM_FILENAME,
                                                FILM_FILE_TOTAL_SIZE);
    if(berr != ESP_OK)
    {
        service_param_set_last_sync_error(
            (berr == ESP_ERR_INVALID_STATE) ? "txn busy" : "txn begin failed");
        return berr;
    }

    esp_http_client_handle_t client = NULL;
    int content_len = -1;
    esp_err_t err = wifi_https_begin_get(m->film_url, &client, &content_len);
    if(err != ESP_OK)
    {
        /* begin_get already released its own client (client stays NULL); the
         * release below is a defensive no-op that keeps "release on every exit"
         * literal. Our Wi-Fi write txn is open -> abort it (keeps current.film). */
        wifi_http_client_release(&client);
        service_file_receive_abort();
        /* ESP_ERR_INVALID_RESPONSE 表示 begin_get 已经按 HTTP 状态码写好了原因
         * （"http 401 (device_token 被拒)" 这类），比这里的通用文案精确得多，不要覆盖它。 */
        if(err != ESP_ERR_INVALID_RESPONSE)
        {
            service_param_set_last_sync_error("film http open/status");
        }
        return err;
    }

    /* Content-Length gate (Req 10.5): when the server declares a length it must
     * equal the fixed .film size. When unknown (-1 / chunked) the finish()
     * length check (written == expected) is the enforcement. */
    if(content_len >= 0 && (uint32_t)content_len != FILM_FILE_TOTAL_SIZE)
    {
        sys_logw(WIFI_SERVICE_TAG, "film content-length %d != %u -> reject",
                 content_len, (unsigned)FILM_FILE_TOTAL_SIZE);
        service_file_receive_abort();
        wifi_http_client_release(&client);
        service_param_set_last_sync_error("film length mismatch");
        return ESP_ERR_INVALID_SIZE;
    }

    /* STREAM the body chunk-by-chunk straight into the transaction. The chunk
     * buffer is a small fixed on-stack buffer — the whole 209120-byte file is
     * NEVER held in RAM nor re-expanded in PSRAM (Req 12.3). The transaction
     * writes each chunk to current.film.tmp and updates the incremental SHA-256. */
    uint8_t chunk[SFM_WIFI_DL_CHUNK_BYTES];
    esp_err_t rc = ESP_OK;
    for(;;)
    {
        int r = esp_http_client_read(client, (char *)chunk, sizeof(chunk));
        if(r < 0)
        {
            service_param_set_last_sync_error("film stream read");
            rc = ESP_FAIL;
            break;
        }
        if(r == 0)
        {
            break;   /* EOF — exact length is enforced by finish() below */
        }
        esp_err_t werr = service_file_receive_write(chunk, (size_t)r);
        if(werr != ESP_OK)
        {
            /* overflow / IO error: the transaction self-aborts internally. */
            service_param_set_last_sync_error("film write rejected");
            rc = werr;
            break;
        }
    }

    /* Release the network resources before validating / committing. */
    wifi_http_client_release(&client);

    if(rc != ESP_OK)
    {
        service_file_receive_abort();   /* safe no-op if already self-aborted */
        return rc;
    }

    /* Convert the manifest's 64-hex sha256 to 32 raw bytes for finish(). */
    uint8_t sha256[32];
    if(!wifi_sha256_hex_to_bytes(m->sha256_hex, sha256))
    {
        service_file_receive_abort();
        service_param_set_last_sync_error("sha256 hex invalid");
        return ESP_ERR_INVALID_ARG;
    }

    /* finish() validates length + .film header + SHA-256 then fsync+rename
     * atomic-replaces current.film. On any failure it aborts + cleans internally
     * and current.film is left untouched (keep current screen, Req 7.7 / 13.1). */
    esp_err_t ferr = service_file_receive_finish(sha256);
    if(ferr != ESP_OK)
    {
        /* finish() already aborts + cleans the txn internally on any validation/
         * commit failure and leaves current.film untouched; abort defensively so
         * EVERY failure exit of this function uniformly aborts-if-open (safe
         * no-op when finish already reset the txn to IDLE). Keep-screen holds:
         * current.film is replaced ONLY after finish() fully validated + atomically
         * committed (Req 7.7 / 13.1 / 13.3). The client is already released above. */
        service_file_receive_abort();
        service_param_set_last_sync_error("finish/validate failed");
        return ferr;
    }

    /* Keep the release version volatile until the matching file has reached the
     * EPD. service_wifi_display_completed() persists it from the film task only
     * after hal_epd_display_film() returns. */
    const char *pending_names[] = {SFM_WIFI_FILM_FILENAME};
    wifi_pending_display_arm(m->version, pending_names, 1u);

    /* Trigger the refresh. The film task persists the version after EPD completion. */
    service_file_display_received(SFM_WIFI_FILM_FILENAME);

    sys_logi(WIFI_SERVICE_TAG,
             "sync pending: version %u committed (validated), awaiting refresh completion",
             (unsigned)m->version);
    return ESP_OK;
}

/* [wifi_download_one_item 流式下载 items[] 中的一个条目到 staging（不覆盖正式文件）]
 *
 * 与 wifi_download_film_streaming() 的差别只有三点：落盘名来自条目的 name（不是硬编码
 * 的 current.film）、成功后【不】保存 last_applied_version、成功后【不】触发上屏。
 * 后两件事属于「整套」的语义，必须等全部条目都提交成功才做一次（见
 * wifi_download_film_set 的注释），否则中途失败会留下一个已推进的版本号，
 * 下一轮同步据此判定「已是最新」，设备就永久停在半套图上。
 *
 * 事务本身保证了 staging 单张完整：写共享临时文件 → finish 校验长度/.film 头/SHA-256
 * → fsync+rename 到不可见的 staging 名。正式文件只由整套提交接口统一替换。
 *
 * @param it 条目（name 已由 manifest 解析器验证为安全基名）
 * @return ESP_OK 该条目已校验并提交；否则错误码（已记录非机密 last_sync_error）
 */
static esp_err_t wifi_download_one_item(const manifest_item_t *it)
{
    /* 先开写事务（单写者）：若 BLE 事务持有所有权会返回 busy，这是 Req 18.2/18.3
     * 在唤醒时刻的兜底 —— 宁可这轮不下载，也不能打断正在进行的 BLE 直传。 */
    esp_err_t berr = service_file_receive_begin_staged(it->name, FILM_FILE_TOTAL_SIZE);
    if(berr != ESP_OK)
    {
        /* last_sync_error 带上文件名：一套四张里「哪一张失败」直接决定去查后端的
         * 哪个槽位，而 name 是非机密的。 */
        char msg[SFM_SYNC_ERR_MAX];
        snprintf(msg, sizeof(msg), "%s: %s", it->name,
                 (berr == ESP_ERR_INVALID_STATE) ? "txn busy" : "txn begin failed");
        service_param_set_last_sync_error(msg);
        return berr;
    }

    esp_http_client_handle_t client = NULL;
    int content_len = -1;
    esp_err_t err = wifi_https_begin_get(it->url, &client, &content_len);
    if(err != ESP_OK)
    {
        wifi_http_client_release(&client);   /* defensive no-op: begin_get already released */
        service_file_receive_abort();
        /* 同上：状态码原因已由 begin_get 写好，只在别的失败上补文件名。 */
        if(err != ESP_ERR_INVALID_RESPONSE)
        {
            char msg[SFM_SYNC_ERR_MAX];
            snprintf(msg, sizeof(msg), "%s: http open/status", it->name);
            service_param_set_last_sync_error(msg);
        }
        return err;
    }

    /* Content-Length 门控：服务器声明了长度就必须等于固定的 .film 长度；
     * 未知（-1 / chunked）时由 finish() 的 written==expected 兜底。 */
    if(content_len >= 0 && (uint32_t)content_len != FILM_FILE_TOTAL_SIZE)
    {
        sys_logw(WIFI_SERVICE_TAG, "item %s content-length %d != %u -> reject",
                 it->name, content_len, (unsigned)FILM_FILE_TOTAL_SIZE);
        service_file_receive_abort();
        wifi_http_client_release(&client);
        char msg[SFM_SYNC_ERR_MAX];
        snprintf(msg, sizeof(msg), "%s: length mismatch", it->name);
        service_param_set_last_sync_error(msg);
        return ESP_ERR_INVALID_SIZE;
    }

    /* 逐块流式写入事务。分片缓冲是一个小的栈上定长缓冲，整份 209120 字节【绝不】
     * 驻留内存，也不在 PSRAM 里展开（Req 12.3）—— 一套四张更是如此：内存占用与
     * 张数无关，因为同一时刻只有一个事务、一个分片缓冲在工作。 */
    uint8_t chunk[SFM_WIFI_DL_CHUNK_BYTES];
    esp_err_t rc = ESP_OK;
    for(;;)
    {
        int r = esp_http_client_read(client, (char *)chunk, sizeof(chunk));
        if(r < 0)
        {
            char msg[SFM_SYNC_ERR_MAX];
            snprintf(msg, sizeof(msg), "%s: stream read", it->name);
            service_param_set_last_sync_error(msg);
            rc = ESP_FAIL;
            break;
        }
        if(r == 0)
        {
            break;   /* EOF —— 精确长度由下面的 finish() 强制 */
        }
        esp_err_t werr = service_file_receive_write(chunk, (size_t)r);
        if(werr != ESP_OK)
        {
            /* 溢出 / IO 错误：事务内部已自行中止。 */
            char msg[SFM_SYNC_ERR_MAX];
            snprintf(msg, sizeof(msg), "%s: write rejected", it->name);
            service_param_set_last_sync_error(msg);
            rc = werr;
            break;
        }
    }

    wifi_http_client_release(&client);   /* 校验/提交之前先放掉网络资源 */

    if(rc != ESP_OK)
    {
        service_file_receive_abort();    /* 已自行中止时为安全的 no-op */
        return rc;
    }

    uint8_t sha256[32];
    if(!wifi_sha256_hex_to_bytes(it->sha256_hex, sha256))
    {
        service_file_receive_abort();
        char msg[SFM_SYNC_ERR_MAX];
        snprintf(msg, sizeof(msg), "%s: sha256 hex invalid", it->name);
        service_param_set_last_sync_error(msg);
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t ferr = service_file_receive_finish(sha256);
    if(ferr != ESP_OK)
    {
        service_file_receive_abort();
        char msg[SFM_SYNC_ERR_MAX];
        snprintf(msg, sizeof(msg), "%s: finish/validate failed", it->name);
        service_param_set_last_sync_error(msg);
        return ferr;
    }

    sys_logi(WIFI_SERVICE_TAG, "item staged (validated): %s", it->name);
    return ESP_OK;
}

/* [wifi_download_film_set 按 manifest.items[] 下载一整套画面并按时段上屏]
 *
 * 逐条 begin → 流式写入 → finish 校验到 staging；全部成功后一次提交整套，再保存
 * last_applied_version 并触发「按时段选图上屏」。
 *
 * 失败语义：任一条目失败即立即中止整套，且
 *   1) 不保存 last_applied_version —— 版本号不推进，下次同步重下整套。若此处推进了
 *      版本号，门控会判「已是最新」，设备将永久停在半套图上，而半套图表现为某个时段
 *      显示过期详情页且人看不出来；
 *   2) 删除本轮 staging，正式文件一张都不改，用户继续看到完整旧套；
 *   3) 记录带失败文件名的非机密 last_sync_error（由 wifi_download_one_item 写入）。
 *
 * 成功后调 display_for_now 而非 display_received：一套四张里该显示哪一张由当前时段
 * 决定，不是最后下载的那一张；该入口在文件任务内先刷新列表再选图，避免读到提交前的
 * 旧列表（详见其头文件注释）。
 *
 * @param m 已解析的 manifest，item_count > 0
 * @return ESP_OK 整套已提交并触发上屏；否则首个失败的错误码
 */
static esp_err_t wifi_download_film_set(const manifest_t *m)
{
    uint8_t count = m->item_count;
    if(count > MANIFEST_MAX_ITEMS)
    {
        count = (uint8_t)MANIFEST_MAX_ITEMS;   /* 防御：解析器已保证，这里不越界读 */
    }

    sys_logi(WIFI_SERVICE_TAG, "downloading film set: version %u, %u item(s)",
             (unsigned)m->version, (unsigned)count);

    const char *names[MANIFEST_MAX_ITEMS] = {0};
    for(uint8_t i = 0; i < count; i++)
    {
        names[i] = m->items[i].name;
    }

    for(uint8_t i = 0; i < count; i++)
    {
        esp_err_t err = wifi_download_one_item(&m->items[i]);
        if(err != ESP_OK)
        {
            service_file_discard_staged_set(names, count);
            sys_logw(WIFI_SERVICE_TAG,
                     "film set aborted at item %u/%u (%s): version %u NOT applied, will retry next sync",
                     (unsigned)(i + 1u), (unsigned)count, m->items[i].name,
                     (unsigned)m->version);
            return err;
        }
    }

    esp_err_t commit_err = service_file_commit_staged_set(names, count);
    if(commit_err != ESP_OK)
    {
        service_file_discard_staged_set(names, count);
        service_param_set_last_sync_error("film set commit failed");
        sys_logw(WIFI_SERVICE_TAG,
                 "film set commit failed: version %u NOT applied, will retry",
                 (unsigned)m->version);
        return commit_err;
    }

    /* The selected item must finish refreshing before this version becomes durable. */
    wifi_pending_display_arm(m->version, names, count);

    /* 一套四张成功后清掉单张落盘名 current.film：它属于单张 Wi-Fi 路径，内容是旧版本的
     * 图，留着会作为第五个文件出现在按键轮播里，用户翻到一张过期菜单且分辨不出来。
     * 只删这一个名字，不用 clear_all —— 刚提交的四张必须原样保留。删除失败只记日志不算
     * 整套失败：四张新图已在卡上并会正常上屏，为一个残留文件让整次同步失败（进而不推进
     * 版本号、下轮重下整套）代价过高。
     *
     * 这里可以同步调用：只删一个文件，而本任务刚往卡上写完四份 209120 字节，一次 remove
     * 的耗时可忽略；此刻 HTTP 客户端已释放，也没有延迟敏感的对端在等（0x45 必须异步是
     * 因为它跑在 BLE 任务上，同步删会触发 GATT 超时断连）。
     *
     * 前置条件：本次 items[] 里不含这个名字。不变量是永远不要删除自己刚刚下载并提交的
     * 文件 —— 若某次 manifest 的 items[] 恰好带着 name == "current.film" 的一条，无条件
     * 删除就会删掉刚校验通过的图：版本号在上面几行已推进并保存 → 图被删 →
     * service_film_display_for_now() 因文件数为 0 直接返回 → 电子纸保留旧像素 → 下一轮
     * 判「已是最新」不再下载，该次发布永久丢失而两端日志都显示成功。判据用纯函数
     * manifest_contains_item_name()，在 host 测试里被单独驱动。 */
    if(manifest_contains_item_name(m, SFM_WIFI_FILM_FILENAME))
    {
        sys_logi(WIFI_SERVICE_TAG,
                 "set contains %s -> skip leftover cleanup (never remove what we just committed)",
                 SFM_WIFI_FILM_FILENAME);
    }
    else
    {
        int rmrc = service_file_remove(SFM_WIFI_FILM_FILENAME);
        if(rmrc == 0)
        {
            sys_logi(WIFI_SERVICE_TAG, "removed leftover single-file slot %s", SFM_WIFI_FILM_FILENAME);
        }
    }

    /* —— 补集清理：删掉不在本次 items[] 里的**旧菜单图** ——
     *
     * 上面那段只处理了一个硬编码的名字（current.film）。真正的缺口是换名：后端把
     * 1_today.film 改成别的命名后，新套四张提交成功，旧套四张一个都不会被删 ——
     * 文件数变成 8，按键轮播里一半是过期菜单，而 display_for_now() 的 strstr 包含匹配
     * 会在两套里都命中，命中哪一张取决于列表排序。症状恰好是上面那段注释自己说要避免的
     * 「翻着翻着看到一张过期菜单」，只是触发条件从「后端多输出一个 items 条目」换成了
     * 「后端改了命名」。
     *
     * 判据用纯函数 film_pin_name_is_menu_slot()（名字含 today/breakfast/lunch/dinner），
     * 它与 pin 那条「pinned 名字不得含这些关键字」的规则互补，因此**绝不会碰到用户自己的
     * draw_*.film / photo_*.film**。这一点很重要：误删用户的画比留一张过期菜单严重得多，
     * 所以判据取的是白名单式的「确定是菜单图才删」，而不是「不在 items 里就删」。
     *
     * 先把待删的名字整份拷出来再删：service_file_get_filename() 读的是内存里那份列表，
     * 而 service_file_receive_finish() 只提交、不刷新列表；边遍历边删会让下标与名字错位。 */
    {
        char stale[MANIFEST_MAX_ITEMS * 2][SFM_WIFI_STALE_NAME_MAX];
        uint32_t stale_n = 0;
        uint32_t total = service_file_get_count();

        for(uint32_t i = 0; i < total && stale_n < (uint32_t)(sizeof(stale) / sizeof(stale[0])); i++)
        {
            const char *fn = service_file_get_filename(i);
            if(fn == NULL || fn[0] == '\0')
            {
                continue;
            }
            if(!film_pin_name_is_menu_slot(fn))
            {
                continue;               /* 用户的自定义图，或 current.film：不属于本次清理范围 */
            }
            if(manifest_contains_item_name(m, fn))
            {
                continue;               /* 正是本次刚下载并提交的那几张 */
            }
            /* 截断而不是跳过：名字长到装不下这个缓冲的菜单图不该存在（后端命名远短于此），
             * 真出现时按截断名去删会失败并记一条 warning，比静默漏掉更容易被发现。 */
            (void)snprintf(stale[stale_n], sizeof(stale[0]), "%s", fn);
            stale_n++;
        }

        for(uint32_t i = 0; i < stale_n; i++)
        {
            int rmrc = service_file_remove(stale[i]);
            if(rmrc == 0)
            {
                sys_logi(WIFI_SERVICE_TAG, "removed stale menu film %s", stale[i]);
            }
            else
            {
                sys_logw(WIFI_SERVICE_TAG, "remove stale menu film %s failed (rc=%d)",
                         stale[i], rmrc);
            }
        }
        if(stale_n > 0)
        {
            sys_logi(WIFI_SERVICE_TAG, "stale menu cleanup: %u removed", (unsigned)stale_n);
        }
    }

    /* 按当前时段选图上屏（不是最后下载的那张）。时间不可信时它自己回落总览。
     *
     * 必须走异步入口而不是直接调 service_film_display_for_now()：
     * service_file_receive_finish() 只做提交、不刷新内存里的文件列表，而
     * service_file_refresh_list() 本身也是异步的。在本任务里直接选图会读到刷新前的旧
     * 列表，刚下的四张可能一张都不在里面，于是回落成「列表第一张」。异步入口把刷新列表
     * 与按时段选图都放进文件任务串行执行，同时避免在同步任务里做 SD 卡 IO。 */
    int dsp = service_file_display_for_now_async();

    sys_logi(WIFI_SERVICE_TAG,
             "sync pending: version %u committed as a set of %u (validated), display_for_now queued rc=%d",
             (unsigned)m->version, (unsigned)count, dsp);
    return ESP_OK;
}

/* [wifi_display_result_publish 记下上屏结果并唤醒等待方]
 *
 * 由 film / file 任务经 service_wifi_display_completed/failed() 调用，只写内存并给信号，
 * 不做任何网络或 NVS 操作 —— 那两个任务负责串行的 SD 卡 IO 与电子纸刷新，阻塞不得。
 * 信号量是二值的：一次唤醒里只可能有一份待回报的结果，多给一次也不会累积。 */
static void wifi_display_result_publish(uint32_t version, bool ok)
{
    if(version == 0u || g_display_result_sem == NULL)
    {
        return;
    }
    g_display_result_version = version;
    g_display_result_ok = ok;
    (void)xSemaphoreGive(g_display_result_sem);
}

/* [wifi_https_post_json 带 JSON 正文的 POST（Bearer 鉴权、HTTPS 强制）]
 *
 * 与 wifi_https_post_empty() 分开而不是加参数：那个函数的注释明确写着「没有正文、
 * 读完头就关」，把正文写入塞进去会让它同时承担两种请求形态，而两者的错误退出路径不同。
 *
 * @param body 必须是 NUL 结尾的 JSON 文本；不含任何机密（只有版本号与布尔）。 */
static esp_err_t wifi_https_post_json(const char *url, const char *body, int *out_status)
{
    if(out_status)
    {
        *out_status = 0;
    }
    if(url == NULL || body == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    esp_http_client_config_t config = {
        .url               = url,
        .method            = HTTP_METHOD_POST,
        .timeout_ms        = SFM_WIFI_HTTP_TIMEOUT_MS,
        .transport_type    = HTTP_TRANSPORT_OVER_SSL,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if(client == NULL)
    {
        return ESP_FAIL;
    }

    {
        char auth[SFM_DEVICE_TOKEN_MAX + 16];
        int n = snprintf(auth, sizeof(auth), "Bearer %s",
                         g_service_param.network.device_token);
        if(n < 0 || n >= (int)sizeof(auth))
        {
            memset(auth, 0, sizeof(auth));
            esp_http_client_cleanup(client);
            return ESP_ERR_INVALID_SIZE;
        }
        esp_err_t herr = esp_http_client_set_header(client, "Authorization", auth);
        memset(auth, 0, sizeof(auth));   /* 擦掉栈上的凭据副本 */
        if(herr != ESP_OK)
        {
            esp_http_client_cleanup(client);
            return herr;
        }
    }

    (void)esp_http_client_set_header(client, "Content-Type", "application/json");

    int body_len = (int)strlen(body);
    esp_err_t err = esp_http_client_open(client, body_len);
    if(err != ESP_OK)
    {
        esp_http_client_cleanup(client);
        return err;
    }

    if(esp_http_client_write(client, body, body_len) != body_len)
    {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    (void)esp_http_client_fetch_headers(client);
    if(out_status)
    {
        *out_status = esp_http_client_get_status_code(client);
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ESP_OK;
}

bool service_wifi_take_pending_firmware(manifest_firmware_t *out)
{
    bool has;

    if(out == NULL)
    {
        return false;
    }

    /* 取出即清零（take 语义）：同一份描述不该被两次唤醒各刷一遍。
     * 下次唤醒若仍需升级，后端会在那次的 manifest 里再给一次。 */
    portENTER_CRITICAL(&g_sync_mux);
    *out = g_pending_firmware;
    has = g_pending_firmware.has_update;
    memset(&g_pending_firmware, 0, sizeof(g_pending_firmware));
    portEXIT_CRITICAL(&g_sync_mux);

    return has;
}

esp_err_t service_wifi_report_display_result(uint32_t wait_ms)
{
    bool pending;

    /* NULL 检查必须在最前：信号量创建失败时（内存紧张）下面的 uxSemaphoreGetCount(NULL)
     * 会直接崩，而这条路径本来是「失败也无害」的可选优化。 */
    if(g_display_result_sem == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    /* 没有待上屏的版本就立刻返回，不白等：同步判定「已是最新、无需下载」时不会有
     * 任何结果送来，在这里干等 wait_ms 等于让射频与 CPU 多醒这么久，而省电正是
     * 这条唤醒路径存在的理由。 */
    portENTER_CRITICAL(&g_sync_mux);
    pending = g_pending_display_active;
    portEXIT_CRITICAL(&g_sync_mux);

    if(!pending && uxSemaphoreGetCount(g_display_result_sem) == 0u)
    {
        return ESP_ERR_NOT_FOUND;
    }

    if(xSemaphoreTake(g_display_result_sem, pdMS_TO_TICKS(wait_ms)) != pdTRUE)
    {
        /* 超时不是错误路径的终点：下一次唤醒的 X-Device-Applied-Version 仍会把结果
         * 带上去。本次回报只是让后端**早几小时**知道，失败了不该影响正常休眠。 */
        sys_logw(WIFI_SERVICE_TAG, "display result not ready within %ums, defer to next wake",
                 (unsigned)wait_ms);
        return ESP_ERR_TIMEOUT;
    }

    uint32_t version = g_display_result_version;
    bool ok = g_display_result_ok;

    char url[SFM_MANIFEST_URL_MAX + 16];
    esp_err_t uerr = wifi_build_sibling_url(url, sizeof(url), "applied");
    if(uerr != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "applied-report: cannot derive url (rc=%d)", (int)uerr);
        return uerr;
    }
    if(strncmp(url, "https://", 8) != 0)
    {
        sys_logw(WIFI_SERVICE_TAG, "applied-report: url not https -> abort");
        return ESP_ERR_INVALID_ARG;
    }
    if(g_service_param.network.device_token[0] == '\0')
    {
        sys_logw(WIFI_SERVICE_TAG, "applied-report: no device token -> abort");
        return ESP_ERR_INVALID_STATE;
    }

    char body[96];
    int n = snprintf(body, sizeof(body), "{\"version\":%u,\"ok\":%s%s}",
                     (unsigned)version, ok ? "true" : "false",
                     ok ? "" : ",\"detail\":\"display refresh failed\"");
    if(n < 0 || n >= (int)sizeof(body))
    {
        return ESP_ERR_INVALID_SIZE;
    }

    int status = 0;
    esp_err_t err = wifi_https_post_json(url, body, &status);
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "applied-report: post failed (rc=%d)", (int)err);
        return err;
    }
    if(status < 200 || status >= 300)
    {
        sys_logw(WIFI_SERVICE_TAG, "applied-report: rejected (status %d)", status);
        return ESP_FAIL;
    }

    sys_logi(WIFI_SERVICE_TAG, "applied-report sent: version %u ok=%d (status %d)",
             (unsigned)version, (int)ok, status);
    return ESP_OK;
}

void service_wifi_display_completed(const char *filename)
{
    uint32_t version = 0;
    bool matched = false;

    if(filename == NULL)
    {
        return;
    }

    portENTER_CRITICAL(&g_sync_mux);
    if(g_pending_display_active)
    {
        for(uint8_t i = 0; i < g_pending_display_name_count; i++)
        {
            if(strcmp(filename, g_pending_display_names[i]) == 0)
            {
                version = g_pending_display_version;
                g_pending_display_active = false;
                g_pending_display_name_count = 0;
                matched = true;
                break;
            }
        }
    }
    portEXIT_CRITICAL(&g_sync_mux);

    if(!matched)
    {
        return;
    }

    service_param_set_last_applied_version(version);
    service_param_set_last_sync_error(NULL);
    service_param_save();
    sys_logi(WIFI_SERVICE_TAG, "sync applied: version %u refreshed as %s",
             (unsigned)version, filename);
    wifi_display_result_publish(version, true);
}

void service_wifi_display_failed(const char *filename)
{
    bool cleared = false;
    uint32_t version = 0;

    portENTER_CRITICAL(&g_sync_mux);
    if(g_pending_display_active)
    {
        bool matched = (filename == NULL);
        for(uint8_t i = 0; !matched && i < g_pending_display_name_count; i++)
        {
            matched = (strcmp(filename, g_pending_display_names[i]) == 0);
        }
        if(matched)
        {
            version = g_pending_display_version;
            g_pending_display_active = false;
            g_pending_display_name_count = 0;
            cleared = true;
        }
    }
    portEXIT_CRITICAL(&g_sync_mux);

    if(cleared)
    {
        service_param_set_last_sync_error("display refresh failed");
        service_param_save();
        sys_logw(WIFI_SERVICE_TAG, "pending display failed%s%s; next sync will retry",
                 filename ? ": " : "", filename ? filename : "");
        /* 失败也要报，否则后端看不见这一类故障。
         * 注意不动 last_applied_version —— 下次唤醒的版本门控还要靠它重试。 */
        wifi_display_result_publish(version, false);
    }
}

/* [wifi_sync_once_run 一次完整同步流程主体（受 g_sync_in_progress 串行保护）] */
static esp_err_t wifi_sync_once_run(void)
{
    /* 1) Connect + SNTP gate (also yields to an in-progress BLE txn).
     * Proceed only when HTTPS is allowed; otherwise prepare already recorded a
     * non-secret last_sync_error and left the current screen untouched. */
    esp_err_t perr = service_wifi_sync_prepare();
    if(perr != ESP_OK)
    {
        return perr;
    }

    /* 2) manifest over HTTPS with Bearer auth. Enforce HTTPS-only for the
     * manifest URL and require a device token (Req 10.1). */
    if(!manifest_url_is_https(g_service_param.network.manifest_url))
    {
        service_param_set_last_sync_error("manifest url not https");
        sys_logw(WIFI_SERVICE_TAG, "manifest url is not https -> abort");
        return ESP_ERR_INVALID_ARG;
    }
    if(g_service_param.network.device_token[0] == '\0')
    {
        service_param_set_last_sync_error("no device token");
        sys_logw(WIFI_SERVICE_TAG, "no device token configured -> abort");
        return ESP_ERR_INVALID_STATE;
    }

    esp_http_client_handle_t client = NULL;
    esp_err_t merr = wifi_https_begin_get(g_service_param.network.manifest_url,
                                          &client, NULL);
    if(merr != ESP_OK)
    {
        /* 同上：401 这类状态码原因已由 begin_get 写好，保留它。
         * manifest 这一跳是最常撞到 401 的地方（它是每次同步的第一个请求）。 */
        if(merr != ESP_ERR_INVALID_RESPONSE)
        {
            service_param_set_last_sync_error("manifest http open/status");
        }
        return merr;
    }

    /* manifest_t 自 F4 起内嵌 items[4]（每条 ~360 字节），整个结构约 1.5KB。
     * 本函数跑在 wifi_sync_once_task 上，栈只有 10240 字节，而同一条调用链上还要做
     * TLS 握手（mbedtls 的握手栈开销本身就是 KB 级）。把 1.5KB 放栈上会把余量吃掉
     * 一大截，栈溢出的表现是同步过程中随机崩溃，极难联想到是一个结构体变大了。
     * 改为堆分配，并保证【每一条退出路径】都 free。 */
    manifest_t *manifest = (manifest_t *)malloc(sizeof(manifest_t));
    if(manifest == NULL)
    {
        wifi_http_client_release(&client);
        service_param_set_last_sync_error("manifest oom");
        return ESP_ERR_NO_MEM;
    }

    esp_err_t rerr = wifi_read_manifest_body(client, manifest);
    wifi_http_client_release(&client);   /* release manifest client on every path */
    if(rerr != ESP_OK)
    {
        free(manifest);
        return rerr;   /* body reader already recorded a non-secret last_sync_error */
    }

    /* 暂存固件升级描述。放在版本门控**之前**：画面已是最新时同步就在下面 return 了，
     * 但固件仍可能需要升级 —— 两者是各自独立的版本线。 */
    portENTER_CRITICAL(&g_sync_mux);
    g_pending_firmware = manifest->firmware;
    portEXIT_CRITICAL(&g_sync_mux);
    if(manifest->firmware.has_update)
    {
        sys_logi(WIFI_SERVICE_TAG, "manifest offers firmware %s (%u bytes)",
                 manifest->firmware.version, (unsigned)manifest->firmware.size);
    }

    /* 3) Version gate + force. If not downloading → clean end: no
     * download, no refresh; resources are already released. */
    if(!sync_should_download(manifest->version,
                             g_service_param.sync.last_applied_version,
                             manifest->force))
    {
        sys_logi(WIFI_SERVICE_TAG,
                 "manifest v%u <= last v%u and !force -> up to date, no download",
                 (unsigned)manifest->version,
                 (unsigned)g_service_param.sync.last_applied_version);
        /* 仅在确有旧错误时写 NVS；轮询默认每分钟一次，已是最新时无条件 save 会造成
         * 毫无意义的 flash 擦写。 */
        if(g_service_param.sync.last_sync_error[0] != '\0')
        {
            service_param_set_last_sync_error(NULL);
            service_param_save();
        }
        free(manifest);
        return ESP_OK;
    }

    /* 4/5) 下载 → 校验 → 原子替换 → 刷新 → 保存版本。
     *
     * item_count > 0 走「一套多张」路径；== 0 时后端没给 items[]（还没升级），
     * 沿用既有单张路径，落盘 current.film 并 display_received —— 那条路径一个字节
     * 都没改，灰度期新固件配旧后端必须仍然可用。 */
    esp_err_t derr;
    if(manifest->item_count > 0)
    {
        derr = wifi_download_film_set(manifest);
    }
    else
    {
        derr = wifi_download_film_streaming(manifest);
    }
    free(manifest);
    return derr;
}

/**
 * [service_wifi_sync_once 执行一次完整的 Wi-Fi 同步（连接→SNTP→manifest→门控→下载→提交）]
 * 见 service_wifi.h。轮询定时器与 BLE SYNC_NOW均调用本入口。
 */
bool service_wifi_sync_is_busy(void)
{
    bool busy;
    portENTER_CRITICAL(&g_sync_mux);
    busy = g_sync_in_progress || g_pending_display_active;
    portEXIT_CRITICAL(&g_sync_mux);
    return busy;
}

/**
 * [service_wifi_link_watch_tick 醒着期间的链路自愈：由 monitor 的节拍周期调用]
 *
 * 补的是这样一个空档：`WIFI_EVENT_STA_DISCONNECTED` 刻意不重连（那正是 Req 11 要
 * 禁止的 infinite fast reconnect），而重连只编排在 service_wifi_sync_prepare() 里 ——
 * 于是「设备醒着、同步流程已经跑完」这段时间没有人负责链路。现场表现是：把路由器关掉
 * 再打开，设备不会自己连回去，必须手动重新下发一次配网。
 *
 * 深睡时不受影响也不需要影响：设备醒来本来就会走 sync_prepare 重连，所以这条路径对
 * 续航没有额外开销 —— 它只在已经醒着的时候评估。
 *
 * 判定全部交给纯函数 wifi_link_watch_eval()（宿主机穷举过退避、优先级、时钟回绕），
 * 这里只负责取当下状态、维护两个静态量、以及在需要时发起一次**非阻塞**的
 * service_wifi_connect()。不用 connect_blocking：本函数跑在 monitor 的消息任务上，
 * 阻塞 10 秒会连带卡住 LED、电量与自动休眠那几路节拍。
 */
void service_wifi_link_watch_tick(void)
{
    /* 静态量而不是全局：除了本函数没有第二个合理的读写者，收在这里能保证退避状态
     * 与判定逻辑同生共死。单线程访问（monitor 消息任务），无需加锁。 */
    static uint32_t s_last_attempt_ms = 0;
    static uint32_t s_attempts = 0;

    file_receive_state_t rx = service_file_get_receive_state();
    const uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);

    wifi_link_watch_input_t in = {
        .enabled         = !g_service_param.sleep.sleep_mode,
        .provisioned     = (g_service_param.network.wifi_ssid[0] != '\0'),
        .link_up         = g_wifi_connected,
        /* 两种忙都要算：同步流程自己带有界退避重连，BLE 传输期间则要守唤醒期单写者
         * 不变量（Req 18.1）。任一成立时插一脚只会互相打断。 */
        .busy            = service_wifi_sync_is_busy() ||
                           wifi_sync_should_yield_to_ble(rx.state, rx.owner),
        .now_ms          = now_ms,
        .last_attempt_ms = s_last_attempt_ms,
        .attempts        = s_attempts,
    };

    switch(wifi_link_watch_eval(&in))
    {
    case WIFI_LINK_WATCH_SKIP_DISABLED:
        s_attempts = 0;
        s_last_attempt_ms = now_ms;
        break;

    case WIFI_LINK_WATCH_SKIP_LINK_UP:
        /* 链路健康：清零失败计数，并把"上次尝试"推到当下。后者是必需的 ——
         * 有了它，链路断开后的第一次重连才会等一个完整的退避间隔（而不是拿一个
         * 很久以前的时间戳算出"早就该重连了"，在 AP 刚掉线时立刻扑上去）。 */
        s_attempts = 0;
        s_last_attempt_ms = now_ms;
        break;

    case WIFI_LINK_WATCH_CONNECT:
        s_last_attempt_ms = now_ms;
        if(s_attempts < 0xFFFFFFFFu)
        {
            s_attempts++;
        }
        /* 日志里带上第几次与下次间隔：链路一直连不上时，这两个数字能立刻区分
         * 「自愈在按退避重试」与「自愈根本没跑」。SSID 与密码不进日志（Req 15.4）。 */
        sys_logi(WIFI_SERVICE_TAG,
                 "link watch: link down while awake -> reconnect attempt #%u "
                 "(next backoff %ums)",
                 (unsigned)s_attempts,
                 (unsigned)wifi_link_watch_backoff_ms(s_attempts));
        service_wifi_connect();
        break;

    case WIFI_LINK_WATCH_SKIP_NOT_PROVISIONED:
    case WIFI_LINK_WATCH_SKIP_BUSY:
    case WIFI_LINK_WATCH_SKIP_BACKOFF:
    default:
        /* 这三种跳过都是常态，每 5 秒打一行会把日志刷满而没有信息量。
         * 真正需要解释的是「为什么连不上」，那由上面 CONNECT 那条与断连事件的
         * reason code 一起给出。 */
        break;
    }
}

/* [wifi_request_refresh_run 请求重算的流程主体（受 g_sync_in_progress 串行保护）]
 *
 * 抽成 static、由下面的公有入口持锁后调用，与 wifi_sync_once_run / service_wifi_sync_once
 * 同构。**必须与 manifest 同步共用同一把锁**：两者都调 service_wifi_sync_prepare()，
 * 而 prepare 里的 SNTP 启停是 check-then-act（g_sntp_started）——并发时一侧的
 * esp_netif_sntp_deinit() 可能正落在另一侧 esp_netif_sntp_sync_wait() 期间。
 * 这条路径挂在 NEXT 长按上，而长按本身就是唤醒动作，与「按键唤醒顺带同步」重叠是常态，
 * 不是边缘情况。 */
static esp_err_t wifi_request_refresh_run(void)
{
    /* manifest_url 上限 128 字节，"manifest"(8) -> "request-refresh"(15) 多 7 字节，
     * 留 16 的余量足够且不必担心截断。 */
    char url[SFM_MANIFEST_URL_MAX + 16];

    if(!manifest_url_is_https(g_service_param.network.manifest_url))
    {
        sys_logw(WIFI_SERVICE_TAG, "request-refresh: manifest url not https -> abort");
        return ESP_ERR_INVALID_ARG;
    }
    if(g_service_param.network.device_token[0] == '\0')
    {
        sys_logw(WIFI_SERVICE_TAG, "request-refresh: no device token -> abort");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t uerr = wifi_build_request_refresh_url(url, sizeof(url));
    if(uerr != ESP_OK)
    {
        /* 非机密日志：只说推导失败，不打印 URL（它含 device_id，虽非机密但没必要）。 */
        sys_logw(WIFI_SERVICE_TAG,
                 "request-refresh: cannot derive url from manifest url (rc=%d)", (int)uerr);
        return uerr;
    }

    /* 连接 + SNTP 门控：POST 也走 HTTPS，冷启动时钟是 1970 会让证书校验必然失败，
     * 所以和 manifest 同步一样必须先过这道门。 */
    esp_err_t perr = service_wifi_sync_prepare();
    if(perr != ESP_OK)
    {
        return perr;
    }

    int status = 0;
    esp_err_t err = wifi_https_post_empty(url, &status);
    if(err != ESP_OK)
    {
        sys_logw(WIFI_SERVICE_TAG, "request-refresh: post failed (rc=%d)", (int)err);
        return err;
    }

    /* 200/202 都算受理：后端当前返回 202（已接收，后台异步生成），200 留作兼容。 */
    if(status == 200 || status == 202)
    {
        sys_logi(WIFI_SERVICE_TAG, "request-refresh accepted (status %d)", status);
        return ESP_OK;
    }

    if(status == 429)
    {
        /* 这个入口挂在物理按键上，连按会被后端限流。单独记一条，避免与网络故障混淆。 */
        sys_logw(WIFI_SERVICE_TAG, "request-refresh: 后端限流，稍后再试 (status 429)");
        service_param_set_last_sync_error("request-refresh: http 429 (按太快了)");
        return ESP_ERR_NOT_FINISHED;
    }

    /* 与 GET 路径同样的道理：把状态码本身写进 last_sync_error，App 才看得见失败原因。
     * 只打串口日志不够 —— 按键重算失败时用户手里只有 App，光看旧文案无从判断这次按键
     * 是否生效。
     *
     * 401/403 在这里与 manifest 那一跳是同一个成因（后端不认这台设备的 device_token），
     * 给出同样的结论，免得同一个问题在两条路径上表现成两种说法。 */
    {
        char msg[SFM_SYNC_ERR_MAX];
        snprintf(msg, sizeof(msg), "request-refresh: http %d%s", status,
                 (status == 401 || status == 403) ? " (device_token 被拒)" : "");
        service_param_set_last_sync_error(msg);
    }
    if(status == 401 || status == 403)
    {
        sys_logw(WIFI_SERVICE_TAG,
                 "  -> 后端不认这台设备的 device_token（未登记或与本机不一致）。"
                 "网络与证书都是好的，不用查那两项；"
                 "在 App 设备页点「登记设备令牌」即可恢复，无需重新配网");
    }

    sys_logw(WIFI_SERVICE_TAG, "request-refresh: unexpected status %d", status);
    return ESP_ERR_INVALID_RESPONSE;
}

/**
 * [service_wifi_request_refresh 请求后端重新生成并发布菜单画面]
 * 见 service_wifi.h。阻塞（连接退避 + TLS + 一次 POST），必须在任务上下文调用。
 */
esp_err_t service_wifi_request_refresh(void)
{
    /* 与 service_wifi_sync_once() 共用同一把让路门控，理由见 wifi_request_refresh_run
     * 的注释。这仍是轻量守卫而非硬并发屏障（与既有 sync 一致）：真正的兜底是文件事务的
     * 单写者检查。 */
    if(!wifi_sync_try_enter())
    {
        sys_logw(WIFI_SERVICE_TAG, "request-refresh skip: another sync in progress");
        return ESP_ERR_NOT_FINISHED;
    }
    esp_err_t err = wifi_request_refresh_run();

    wifi_sync_leave();
    return err;
}

esp_err_t service_wifi_sync_once(void)
{
    /* Prevent overlapping sync runs (poll timer [7.5] + SYNC_NOW). Lightweight
     * guard, not a hard concurrency barrier — the file transaction's
     * single-writer busy check is the real backstop; hardens the
     * exit paths. */
    if(!wifi_sync_try_enter())
    {
        sys_logw(WIFI_SERVICE_TAG, "sync skip: another sync already in progress");
        return ESP_ERR_NOT_FINISHED;
    }
    esp_err_t err = wifi_sync_once_run();

    wifi_sync_leave();
    return err;
}

/*********************************************************************
 * LOCAL FUNCTIONS
 */

/*
 * 本文件只有一条 Wi-Fi 取图链路：service_wifi_sync_now() → manifest 版本门控 →
 * HTTPS + Bearer → 流式写入文件事务 → SHA-256 校验 → fsync+rename 原子替换，
 * 在独立任务里执行（不阻塞调用者所在任务）。
 *
 * network.film_api_url 字段刻意保留但不再被读取：它在 param_serialize.c 里占 128 字节
 * 定长槽位，删除会改变 NVS blob 布局并需要 config_version 迁移，为一个不用的字段去动
 * 持久化布局，风险大于收益。
 */

/**
 * [service_wifi_download_get_progress 获取下载进度]
 *
 * 流式同步路径不跟踪字节级进度：整份 .film 从不驻留内存，逐块直接写入文件事务，而
 * Content-Length 只用于开头的长度门控。进行中报 0（进度未知），空闲报 100；真正的
 * 判据是下面的 state。
 */
uint8_t service_wifi_download_get_progress(void)
{
    return service_wifi_sync_is_busy() ? 0u : 100u;
}

/**
 * [service_wifi_download_get_state 获取下载状态]
 *
 * 由同步任务的忙标志派生，沿用上游枚举与签名，使 service_monitor 的忙探测与
 * BLE 0x3D 查询无需改动。
 *
 * 只有 DOWNLOADING/IDLE 两态：失败原因统一记录在 sync.last_sync_error（非机密文本，
 * 可经 BLE 0x3B 读取），比一个笼统的 ERROR 枚举有用得多。
 */
wifi_download_state_t service_wifi_download_get_state(void)
{
    return service_wifi_sync_is_busy() ? WIFI_DOWNLOAD_DOWNLOADING : WIFI_DOWNLOAD_IDLE;
}

/**
 * [wifi_event_handler WiFi事件处理]
 */
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if(event_base == WIFI_EVENT)
    {
        switch(event_id)
        {
        case WIFI_EVENT_STA_START:
            sys_logi(WIFI_SERVICE_TAG, "WiFi STA started");
            break;
        case WIFI_EVENT_STA_CONNECTED:
            sys_logi(WIFI_SERVICE_TAG, "WiFi STA connected");
            break;
        case WIFI_EVENT_STA_DISCONNECTED:
            g_wifi_connected = false;
            /* Record the failure for the bounded connect waiter, but DO NOT
             * call esp_wifi_connect() here: auto-reconnecting from the event
             * handler is exactly the "infinite fast reconnect" we must avoid
             * (/ Req 11). Retry with bounded backoff is orchestrated
             * by service_wifi_sync_prepare(). */
            if(g_wifi_event_group)
            {
                xEventGroupSetBits(g_wifi_event_group, WIFI_FAIL_BIT);
            }
            /* **必须打出 reason code**：它是区分「找不到这个 SSID」「密码错」「AP 主动踢人」
             * 的唯一线索，而这三种的处置完全不同（换 SSID / 改密码 / 查 AP 设置）。
             * 只打一句 "WiFi STA disconnected" 的话，现场只能靠反复试密码来二分，
             * 而最常见的那个原因（AP 是 5GHz，ESP32-S3 只有 2.4GHz）试密码永远试不出来。
             *
             * reason 是非机密的枚举值，不含 SSID 与密码，可以安全进日志（Req 15.4）。 */
            {
                const wifi_event_sta_disconnected_t *d =
                    (const wifi_event_sta_disconnected_t *)event_data;
                int reason = (d != NULL) ? (int)d->reason : -1;
                const char *hint = "";
                switch(reason)
                {
                case WIFI_REASON_NO_AP_FOUND:
                    /* 扫不到这个 SSID。最常见的成因是 AP 只开了 5GHz —— 芯片没有 5GHz
                     * 射频，怎么改密码都不会连上；其次是拼写错误或不在覆盖范围内。 */
                    hint = " (扫不到该 SSID：AP 是否为 5GHz？ESP32-S3 仅支持 2.4GHz)";
                    break;
                case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
                    hint = " (扫到了但加密强度低于阈值：AP 是 OPEN 或 WEP？)";
                    break;
                case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
                    hint = " (扫到了但信号太弱，靠近路由器再试)";
                    break;
                case WIFI_REASON_AUTH_FAIL:
                case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
                case WIFI_REASON_HANDSHAKE_TIMEOUT:
                    hint = " (认证失败：密码不对，或 AP 的加密方式不受支持)";
                    break;
                case WIFI_REASON_AUTH_EXPIRE:
                    hint = " (认证过期：信号弱或 AP 主动断开)";
                    break;
                case WIFI_REASON_ASSOC_TOOMANY:
                    hint = " (AP 已达最大连接数)";
                    break;
                case WIFI_REASON_ASSOC_FAIL:
                case WIFI_REASON_CONNECTION_FAIL:
                    hint = " (关联/连接失败：AP 拒绝，检查 MAC 白名单)";
                    break;
                default:
                    break;
                }
                sys_logw(WIFI_SERVICE_TAG, "WiFi STA disconnected, reason=%d%s", reason, hint);
            }
            break;
        default:
            break;
        }
    }
    else if(event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP)
    {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        sys_logi(WIFI_SERVICE_TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        g_wifi_connected = true;
        if(g_wifi_event_group)
        {
            xEventGroupClearBits(g_wifi_event_group, WIFI_FAIL_BIT);
            xEventGroupSetBits(g_wifi_event_group, WIFI_CONNECTED_BIT);
        }
    }
}

/**
 * [wifi_sync_once_task 一次性同步任务：在独立任务上下文运行完整同步流程后自删除]
 *
 * SYNC_NOW 可能在 BLE 命令分发上下文中被调用；完整同步（连接退避 + SNTP + HTTPS
 * 下载）会阻塞较久，故放到独立一次性任务里执行，避免阻塞调用方。用完即
 * vTaskDelete(NULL) 释放。的轮询定时器则直接在低功耗管理器上下文调用
 * service_wifi_sync_once()。
 */
/* 一次性同步任务的句柄。非 NULL 表示已有一个在跑（或刚创建、尚未开始）。
 * 用途见 service_wifi_sync_now() 里的重复投递保护。 */
static TaskHandle_t g_sync_once_task_hdl = NULL;

static void wifi_sync_once_task(void *pvParameters)
{
    (void)pvParameters;
    esp_err_t r = service_wifi_sync_once();
    if(r != ESP_OK)
    {
        /* 手动同步（BLE 0x44 / PREV 长按）失败时的即时上屏降级：拿不到新内容，
         * 至少让屏幕停在当前时段该看的那一页。用户刚主动要过新菜单，屏幕毫无变化
         * 与「按键没反应」无法区分。只在该换页时才刷，不为一次失败白耗一次刷屏。 */
        sys_logw(WIFI_SERVICE_TAG, "SYNC_NOW ended without apply (rc=%d), fix screen slot",
                 (int)r);
        (void)service_file_display_if_stale_async();
    }
    else
    {
        /* 手动同步成功也要清掉每日重试记账，否则设备会继续按上一次自动同步失败留下的
         * 15 分钟重试节奏唤醒，而不是回到饭点时间表（详见 service_monitor.h）。 */
        service_monitor_notify_manual_sync_ok();
    }
    /* NOT_FINISHED 表示同步没有取得单写者资格（另一轮同步或上屏仍在进行）。
     * 这时 Wi-Fi 属于别的事务，不能由本任务关闭。 */
    if(g_service_param.sleep.sleep_mode && r != ESP_ERR_NOT_FINISHED)
    {
        service_wifi_deinit();
    }
    /* 先清句柄再自删：反了的话 vTaskDelete 之后这行永远执行不到，句柄会永久非 NULL，
     * 于是「重复投递保护」变成「从此再也不能手动同步」。 */
    g_sync_once_task_hdl = NULL;
    vTaskDelete(NULL);
}

/**
 * [service_wifi_sync_now 立即触发一次一次性 Wi-Fi 同步检查]
 *
 * 由 BLE SYNC_NOW 命令调用（Req 14.1）：请求立即执行一次完整 Wi-Fi
 * 同步（connect → SNTP → 携带 Bearer 的 manifest 拉取 → 版本门控 → 流式下载入
 * 统一文件事务 → 校验/提交/刷新）。演示时无需等待低功耗管理器的轮询周期；轮询仍
 * 为默认后台同步行为（Req 14.2）。**不使用 MQTT 长连接**（Req 14.4）——它只是把
 * 现有的一次性同步流程触发一次，用完即释放。
 *
 * 为避免阻塞调用方（BLE 分发上下文），在一个独立的一次性任务中运行；轮询定时器则在其
 * 自身任务上下文中直接调用 service_wifi_sync_once()。
 */
void service_wifi_sync_now(void)
{
    if(!g_service_param.network.wifi_enable)
    {
        sys_logw(WIFI_SERVICE_TAG, "SYNC_NOW ignored: WiFi disabled");
        return;
    }

    /* 重复投递保护。这个入口挂在 BLE 0x44 与 PREV 长按上，两者都可能被连续触发，
     * 而每次都要建一个 SFM_WIFI_SYNC_TASK_STACK（10 KB）栈的任务 —— 执行体固然会被
     * g_sync_in_progress 挡掉，但堆已经先被占了一份。连按几次就是几十 KB 白占，
     * 而症状会出现在别处（某个后续分配失败）。NEXT 长按那条路径本来就有同样的保护。 */
    if(g_sync_once_task_hdl != NULL)
    {
        sys_logw(WIFI_SERVICE_TAG, "SYNC_NOW ignored: a one-shot sync task is already running");
        return;
    }

    sys_logi(WIFI_SERVICE_TAG, "SYNC_NOW: immediate one-shot Wi-Fi sync requested");

    if(pdPASS != xTaskCreate(wifi_sync_once_task, "wifi_sync",
                             SFM_WIFI_SYNC_TASK_STACK, NULL, 5, &g_sync_once_task_hdl))
    {
        g_sync_once_task_hdl = NULL;
        service_param_set_last_sync_error("sync task create failed");
        sys_loge(WIFI_SERVICE_TAG, "SYNC_NOW: failed to create sync task");
    }
}
