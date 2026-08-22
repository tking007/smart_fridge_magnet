/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/src/service_file.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/21
 * Description: 文件服务。在既有保存/列表/加载之上提供「双通道唯一文件事务」
 *   （service_file_receive_*），承载 FreeRTOS 互斥与文件系统副作用；纯决策逻辑在
 *   file_txn.c（宿主机可测）。
 *
 *   事务写入 current.film.tmp，并在 write 时对实际落盘字节做增量 SHA-256
 *   （psa_hash_*），结束阶段不重新读取整文件。finish 在 m_rx_lock 下按序校验：
 *   状态为 FT_RECEIVING → 累计长度 == expected_size → 临时文件 32 字节头有效 →
 *   Wi-Fi 路径比对期望 SHA-256（BLE 路径不带期望值，按设计跳过）。全部通过才经
 *   fs_commit_temp_to_final()（fsync → rename）原子替换 current.film。
 *
 *   关键不变式（Req 7.7）：current.film 在长度、文件头、（Wi-Fi）SHA-256 全部通过
 *   之前绝不被触碰；begin 打开失败、write 超长/物理失败、显式 abort、BLE 断连等所有
 *   终止路径都经 rx_abort_and_cleanup() 只删除 current.film.tmp，并释放摘要上下文。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stdio.h>
#include <string.h>
#include <dirent.h>
#include <unistd.h>
#include <sys/stat.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include "freertos/semphr.h"

#include "esp_heap_caps.h"
#include "esp_err.h"

/* 增量 SHA-256 流式计算（Req 12.1/12.2）。
 * 使用 PSA Crypto API（psa/crypto.h），ESP-IDF 5.x/6.x 均可用；mbedtls 4.x 已移除
 * 传统 mbedtls_sha256_* 低层 API。 */
#include "psa/crypto.h"

#include "sys_log.h"
#include "hal_sd.h"
#include "service_file.h"
#include "service_film.h"
#include "service_wifi.h"
#include "film_validate.h"   /* film_parse_header + 头字段常量：启动清理时校验当前有效文件头 */
#include "film_set_txn.h"

/*********************************************************************
 * MACROS
 */
#define FILE_MSG_QUEUE_LENGTH       30
#define FILE_MSG_QUEUE_ITEM_SIZE    sizeof( file_msg_t )

#define SYS_OS_PRI_FILE_TASK        (6)
#define SYS_OS_SIZE_FILE_TASK       (4096)
#define SYS_OS_NAME_FILE_TASK       "file_task"

#define FILE_TIMER_BASE_INTERVAL_MS (1000)
#define FILE_SD_CHECK_INTERVAL_MS   (5000)
#define FILE_SD_CHECK_TICK_COUNT    (FILE_SD_CHECK_INTERVAL_MS / FILE_TIMER_BASE_INTERVAL_MS)

#define FILE_EPD_IMGAGE_WIDTH       (792)
#define FILE_EPD_IMGAGE_HEIGHT      (528)
#define FILM_HEADER_SIZE            (32)
#define FILM_PIXEL_DATA_SIZE        ((FILE_EPD_IMGAGE_WIDTH * FILE_EPD_IMGAGE_HEIGHT) / 2)
#define FILE_EPD_IMGAGE_SIZE        (FILM_HEADER_SIZE + FILM_PIXEL_DATA_SIZE)

/* 事务接口的固定长度常量（FILM_FILE_TOTAL_SIZE，见 file_txn.h）必须与既有保存
 * 路径使用的 FILE_EPD_IMGAGE_SIZE 保持一致，避免两条路径尺寸校验分歧。 */
_Static_assert(FILE_EPD_IMGAGE_SIZE == FILM_FILE_TOTAL_SIZE,
               "FILE_EPD_IMGAGE_SIZE must equal FILM_FILE_TOTAL_SIZE (209120)");

/* 文件事务临时文件（Req 8.1）。单写者保证同一时刻至多一个活动事务，
 * 因此固定单一临时名即可；write 只写此临时文件，finish 校验通过后再原子替换
 * (fsync→rename) 为目标文件，任一失败/中止都删除该临时文件。 */
#define FILM_TMP_EXT                ".tmp"
#define FILM_TMP_NAME               "current.film" FILM_TMP_EXT
#define FILM_TMP_PATH               FILM_DIR "/" FILM_TMP_NAME

/* 多图集合事务的磁盘工件。staging/backup 后缀都不是 `.film`，普通文件列表不会扫描；
 * prepare/done 两个小标记把掉电恢复分成“回滚旧套”和“保留新套”两个确定阶段。 */
#define FILM_SET_STAGE_SUFFIX       ".sfmstage"
#define FILM_SET_BACKUP_SUFFIX      ".sfmbak"
#define FILM_SET_PREP_PATH          FILM_DIR "/.sfm-set.prepare"
#define FILM_SET_DONE_PATH          FILM_DIR "/.sfm-set.done"
#define FILM_SET_MARKER_MAGIC       "SFMSET1"

/*********************************************************************
* TYPEDEFS
*/
typedef struct {
    file_item_t* file_list;  // 文件列表
    uint32_t file_count;     // 文件数量
    uint32_t current_file_id; // 当前加载的文件ID
    uint8_t* psram_buffer;   // PSRAM缓冲区
    uint8_t load_complete;   // 文件加载完成状态
    /* 静默接收：接收完成后只保存不上屏（连发多张时避免逐张刷屏）。
     * volatile：由 BLE 任务写入（0x46 命令），由文件任务读取（display_received 路径），
     * 双核下需保证跨任务可见性，不能被编译器缓存进寄存器。 */
    volatile uint8_t silent_receive;
    uint32_t buffer_size;    // 缓冲区大小
    uint8_t sd_mounted;      // SD卡挂载状态
    FILE* save_file_handle;  // 文件保存句柄
    uint32_t save_file_size; // 要保存的文件大小
    uint32_t save_written;   // 已写入的字节数
    char save_filename[256]; // 当前保存的文件名
} file_service_state_t;

/*********************************************************************
 * CONSTANTS
 */

/*********************************************************************
 * LOCAL VARIABLES
 */
static TaskHandle_t m_file_task_hdl = NULL;
static QueueHandle_t m_file_msg_hdl = NULL;
static TimerHandle_t m_file_timer = NULL;
static file_service_state_t m_file_state;

/* 双通道文件事务的单写者状态与互斥锁（Smart Fridge Magnet 新增）。
 * m_rx_lock 保证 begin/write/finish/abort 的所有权判定与状态转移原子发生，
 * 从而对 BLE/Wi-Fi 交错调用维持“任意时刻至多一个写所有者”（Req 7.2、18）。 */
static file_receive_state_t m_rx_state;
static SemaphoreHandle_t m_rx_lock = NULL;

/* M3：事务最后活动（begin 授权 / write 成功）的 tick 时间戳。用于在新 begin 时回收
 * 长时间无写入进展的卡死 RECEIVING 事务（对端连着却不发数据/不 STOP），避免其永久
 * 占用单写者锁阻塞后续传输。正常传输每块 write 都会刷新它，不会被误回收。 */
static volatile uint32_t m_rx_last_activity_tick = 0;
#define FILE_RX_IDLE_TIMEOUT_MS   (120000u)   /* 120s，远大于正常 BLE/WiFi 传输耗时 */

/* 事务的文件系统侧句柄。均由 m_rx_lock 保护，生命周期与 m_rx_state
 * 严格对应：begin 授予所有权时打开 m_rx_tmp_fp 并记录目标路径 m_rx_final_path，
 * finish 成功提交或 abort/失败时清零。单写者保证同一时刻只有一个事务持有它们。 */
static FILE *m_rx_tmp_fp = NULL;        /* 临时文件写句柄；无活动事务时为 NULL */
static char  m_rx_final_path[512];      /* 提交目标路径 FILM_DIR/<filename> */

/* 增量 SHA-256 摘要上下文（Req 12.1/12.2；设计 D9）。同样由 m_rx_lock 保护，
 * 生命周期与 m_rx_tmp_fp 对齐：begin 打开临时文件成功后 setup，write 对实际写入的字节
 * update，finish经 fs_rx_sha256_finish 取终值比对，任一终止/提交路径经
 * fs_rx_sha256_reset 做 psa_hash_abort。m_rx_sha_active 标记上下文是否已 setup（即是否
 * 需要 abort），避免对未初始化或已释放的上下文重复 abort 或误 update。 */
static psa_hash_operation_t m_rx_sha_ctx;
static bool m_rx_sha_active = false;

/*********************************************************************
 * GLOBAL VARIABLES
 */

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void file_task_handle(void *pvParameters);
static void file_msg_send(void *p_msg, bool in_isr);
static void file_timer_callback(TimerHandle_t xTimer);

static void file_list_refresh_event(void);
static void file_load_event(uint32_t file_id);
static void file_load_next_event(void);
static void file_sd_check_event(void);
static void file_free_buffer(void);

/* 文件事务的文件系统副作用助手（均要求调用方已持有 m_rx_lock，除
 * 启动清理外——它在 init 单线程阶段运行）。 */
static void fs_rx_cleanup_temp(void);               /* 关闭临时句柄并删除临时文件 */
static void rx_abort_and_cleanup(void);             /* 统一终止：清理+释放所有权+复位IDLE */
/* 增量 SHA-256 生命周期助手。均要求调用方已持有 m_rx_lock。
 * fs_rx_sha256_reset 释放（free）并停用摘要上下文，用于所有终止/提交路径的收口；
 * fs_rx_sha256_finish 计算并输出 32 字节终值，供 finish 直接比对（不重新缓冲整文件）。 */
static void fs_rx_sha256_reset(void);
static esp_err_t fs_rx_sha256_finish(uint8_t out[32]);
/* fsync(tmp)→rename(tmp,final) 原子替换原语。由 finish 在长度/文件头/
 * (Wi-Fi)SHA-256 全部校验通过后调用（接入），是唯一改变 current.film 的动作。 */
static esp_err_t fs_commit_temp_to_final(void);
static void fs_cleanup_stale_temp_on_boot(void);    /* 启动清理遗留临时文件（Req 8.2） */
/* finish 阶段临时文件 .film 文件头校验助手。fflush+fsync 落盘后只读打开
 * 临时文件校验头字段（复用 fs_film_header_valid）；只读临时文件，绝不触碰 current.film。
 * 调用方须已持有 m_rx_lock。 */
static bool fs_rx_temp_header_valid(void);
static void fs_recover_set_on_boot(void);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

void service_file_init(void)
{
    memset(&m_file_state, 0, sizeof(file_service_state_t));
    m_file_state.file_list = NULL;
    m_file_state.file_count = 0;
    m_file_state.current_file_id = 0;
    m_file_state.psram_buffer = NULL;
    m_file_state.buffer_size = 0;
    m_file_state.sd_mounted = 0;
    m_file_state.load_complete = FILE_LOAD_STATE_NONE;
    m_file_state.save_file_handle = NULL;
    m_file_state.save_file_size = 0;
    m_file_state.save_written = 0;

    // 初始化文件事务状态与单写者互斥锁
    file_txn_reset(&m_rx_state);
    // 复位事务的文件系统侧句柄：冷启动无活动事务
    m_rx_tmp_fp = NULL;
    m_rx_final_path[0] = '\0';
    // 复位增量 SHA-256 上下文标记：冷启动无活动摘要，不需 free。
    m_rx_sha_active = false;
    if(m_rx_lock == NULL)
    {
        m_rx_lock = xSemaphoreCreateMutex();
        if(m_rx_lock == NULL)
        {
            sys_loge(FILE_TAG, "file rx lock create error!");
        }
    }

    if(m_file_task_hdl == NULL)
    {
        if ( pdPASS != xTaskCreate( file_task_handle, SYS_OS_NAME_FILE_TASK, SYS_OS_SIZE_FILE_TASK, NULL, SYS_OS_PRI_FILE_TASK, NULL ))
        {
            sys_loge(FILE_TAG, "file task create error!");
        }
    }

    if(m_file_timer == NULL)
    {
        m_file_timer = xTimerCreate( "file_timer", pdMS_TO_TICKS(FILE_TIMER_BASE_INTERVAL_MS), pdTRUE, NULL, file_timer_callback );
        if(m_file_timer == NULL)
        {
            sys_loge(FILE_TAG, "file timer create error!");
        }
    }
    xTimerStart(m_file_timer, 0);

    // 检查SD卡状态
    file_sd_check_event();

    /* 集合提交恢复必须先于普通临时文件清理和列表刷新：prepare 阶段掉电回滚旧套，
     * done 阶段掉电保留新套并清掉备份。 */
    fs_recover_set_on_boot();

    // 启动清理：在当前有效文件头有效的前提下删除遗留临时文件（Req 8.2）。
    // 此时 hal_sd_init() 已在 film_hal_init 阶段同步挂载 SD，故可直接做文件系统操作。
    fs_cleanup_stale_temp_on_boot();
}

static void file_task_handle(void *pvParameters)
{
    m_file_msg_hdl = xQueueCreate( FILE_MSG_QUEUE_LENGTH, FILE_MSG_QUEUE_ITEM_SIZE );
    if(m_file_msg_hdl == NULL)
    {
        sys_loge(FILE_TAG, "file msg queue create error!");
        vTaskDelete(NULL);
        return;
    }

    for(;;)
    {
        file_msg_t msg;
        if(xQueueReceive( m_file_msg_hdl, (void *const)&msg, portMAX_DELAY ) == pdPASS)
        {
            switch(msg.ID)
            {
            case MSG_FILE_LIST_REFRESH:
                file_list_refresh_event();
                break;
            case MSG_FILE_LOAD:
                file_load_event(msg.file_id);
                break;
            case MSG_FILE_LOAD_NEXT:
                file_load_next_event();
                break;
            case MSG_SD_MOUNTED:
                file_list_refresh_event();
                break;
            case MSG_SD_UNMOUNTED:
                file_free_buffer();
                if(m_file_state.file_list)
                {
                    free(m_file_state.file_list);
                    m_file_state.file_list = NULL;
                }
                m_file_state.file_count = 0;
                m_file_state.current_file_id = 0;
                break;
            case MSG_FILE_SAVE_START:
                if(msg.file_size == FILE_EPD_IMGAGE_SIZE)
                {
                    snprintf(m_file_state.save_filename, sizeof(m_file_state.save_filename), "%s", msg.pdata);
                    char filepath[512];
                    snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, m_file_state.save_filename);

                    m_file_state.save_file_handle = fopen(filepath, "wb");
                    if(m_file_state.save_file_handle == NULL)
                    {
                        sys_loge(FILE_TAG, "Open file for save failed: %s", filepath);
                    }
                    else
                    {
                        m_file_state.save_file_size = msg.file_size;
                        m_file_state.save_written = 0;
                        sys_logi(FILE_TAG, "Start save file: %s, size: %d", filepath, msg.file_size);
                    }
                }
                else
                {
                    sys_loge(FILE_TAG, "Invalid file size: %d, expected: %d", msg.file_size, FILE_EPD_IMGAGE_SIZE);
                }
                if(msg.pdata)
                {
                    free(msg.pdata);
                }
                break;
            case MSG_FILE_SAVE_DATA:
                if(m_file_state.save_file_handle && msg.pdata)
                {
                    size_t written = fwrite(msg.pdata, 1, msg.data_len, m_file_state.save_file_handle);
                    m_file_state.save_written += written;
                }
                if(msg.pdata)
                {
                    free(msg.pdata);
                }
                break;
            case MSG_FILE_SAVE_STOP:
                if(m_file_state.save_file_handle)
                {
                    fclose(m_file_state.save_file_handle);
                    m_file_state.save_file_handle = NULL;
                    sys_logi(FILE_TAG, "Save file complete: %s, written: %d", m_file_state.save_filename, m_file_state.save_written);
                    if(m_file_state.save_written != m_file_state.save_file_size)
                    {
                        sys_loge(FILE_TAG, "File size mismatch: written=%d, expected=%d", m_file_state.save_written, m_file_state.save_file_size);
                    }
                    else
                    {
                        sys_logi(FILE_TAG, "Refreshing file list and loading new photo...");
                        // 刷新文件列表
                        file_list_refresh_event();
                        
                        // 查找新文件并加载
                        for(uint32_t i = 0; i < m_file_state.file_count; i++)
                        {
                            if(strcmp(m_file_state.file_list[i].filename, m_file_state.save_filename) == 0)
                            {
                                sys_logi(FILE_TAG, "Found new file at index %d, loading...", i);
                                m_file_state.current_file_id = i;
                                service_film_display(i);
                                break;
                            }
                        }
                    }
                }
                m_file_state.save_file_size = 0;
                m_file_state.save_written = 0;
                memset(m_file_state.save_filename, 0, sizeof(m_file_state.save_filename));
                break;
            case MSG_FILE_CLEAR_ALL:
                /* 在文件任务内执行清空：opendir/readdir/remove 都是 FATFS + SD 卡 IO，
                 * 删若干个 209120 字节的文件耗时可观。若在 BLE 任务里同步做，会阻塞协议栈
                 * 导致对端 GATT 超时断连（实测 0x45 必然断连）。这里与其他文件操作一致，
                 * 统一由文件任务串行处理，也天然避免与接收事务并发。 */
                {
                    int rc = service_file_clear_all();
                    sys_logi(FILE_TAG, "clear all (async) done, rc=%d", rc);
                }
                break;

            case MSG_FILE_DISPLAY_RECEIVED:
                /* 传输适配器（BLE）经统一文件事务提交成功后触发显示（Req 5.3）。
                 * 与 MSG_FILE_SAVE_STOP 成功分支相同：在文件任务内同步刷新文件列表，再按
                 * 文件名定位其索引并调用 service_film_display(index)，复用既有显示路径
                 * （→ hal_epd_display_film）。先刷新后定位可避免异步刷新竞态。 */
                if(msg.pdata)
                {
                    sys_logi(FILE_TAG, "Display received file: %s (refresh + display)", (char*)msg.pdata);
                    file_list_refresh_event();

                    bool found = false;
                    for(uint32_t i = 0; i < m_file_state.file_count; i++)
                    {
                        if(strcmp(m_file_state.file_list[i].filename, (const char*)msg.pdata) == 0)
                        {
                            sys_logi(FILE_TAG, "Found received file at index %d, displaying...", i);
                            m_file_state.current_file_id = i;
                            service_film_display(i);
                            found = true;
                            break;
                        }
                    }
                    if(!found)
                    {
                        sys_logw(FILE_TAG, "Received file not found in list after commit: %s", (char*)msg.pdata);
                        service_wifi_display_failed((const char*)msg.pdata);
                    }
                    free(msg.pdata);
                }
                break;

            case MSG_FILE_DISPLAY_FOR_NOW:
                /* Wi-Fi「一套多张」全部提交成功后触发。
                 *
                 * 必须在文件任务内先刷新列表再选图：receive_finish() 只做提交、不刷新内存
                 * 里的文件列表，而 service_file_refresh_list() 是异步的（只投一条消息）。
                 * 调用方在自己任务里先 refresh 再立刻选图会读到刷新前的旧列表，刚下载的
                 * 四张可能一张都不在里面，于是回落到「列表第一张」。 */
                sys_logi(FILE_TAG, "display for now: refresh list then pick by meal slot");
                file_list_refresh_event();
                {
                    int idx = service_film_display_for_now();
                    if(idx >= 0)
                    {
                        m_file_state.current_file_id = (uint32_t)idx;
                    }
                    else
                    {
                        sys_logw(FILE_TAG, "display for now: no displayable file (rc=%d)", idx);
                        service_wifi_display_failed(NULL);
                    }
                }
                break;
            case MSG_FILE_DISPLAY_IF_STALE:
                /* 同步失败后的降级。与上面一样必须先刷新列表再选图（同一个竞态），
                 * 差别只在 _if_changed 会跳过「该显示的正是当前这张」的情形。 */
                sys_logi(FILE_TAG, "display if stale: refresh list then pick by meal slot");
                file_list_refresh_event();
                {
                    int idx = service_film_display_for_now_if_changed();
                    if(idx >= 0)
                    {
                        m_file_state.current_file_id = (uint32_t)idx;
                    }
                    /* idx == -1 表示「已经是该显示的那张」，不是错误，不必报给 Wi-Fi 层；
                     * idx == -2 表示一张文件都没有，那种情形调用方本来也无从降级。 */
                }
                break;
            default:
                break;
            }
        }
    }
}

static void file_msg_send(void *p_msg, bool in_isr)
{
    if(m_file_msg_hdl != NULL)
    {
        if(in_isr == 0)
        {
            if(xQueueSend(m_file_msg_hdl, p_msg, portMAX_DELAY) != pdPASS)
            {
                sys_loge(FILE_TAG, "file msg send error!");
            }
        }
        else
        {
            BaseType_t xHigherPriorityTaskWoken;
            xHigherPriorityTaskWoken = pdFALSE;
            xQueueSendFromISR( m_file_msg_hdl, p_msg, &xHigherPriorityTaskWoken );
            portYIELD_FROM_ISR( xHigherPriorityTaskWoken );
        }
    }
}

static void file_timer_callback(TimerHandle_t xTimer)
{
    static uint32_t tick_counter = 0;
    tick_counter++;

    if((tick_counter % FILE_SD_CHECK_TICK_COUNT) == 0)
    {
        file_sd_check_event();
    }
}

static void file_sd_check_event(void)
{
    int sd_status = hal_sd_get_status();
    if(sd_status == SD_MOUNT)
    {
        if(m_file_state.sd_mounted == 0)
        {
            m_file_state.sd_mounted = 1;
            file_msg_t msg;
            msg.ID = MSG_SD_MOUNTED;
            file_msg_send(&msg, 0);
        }
    }
    else
    {
        if(m_file_state.sd_mounted == 1)
        {
            m_file_state.sd_mounted = 0;
            file_msg_t msg;
            msg.ID = MSG_SD_UNMOUNTED;
            file_msg_send(&msg, 0);
        }
    }
}

static void file_list_refresh_event(void)
{
    if(!m_file_state.sd_mounted)
    {
        sys_logw(FILE_TAG, "SD card not mounted");
        return;
    }

    // 释放旧的文件列表
    if(m_file_state.file_list)
    {
        free(m_file_state.file_list);
        m_file_state.file_list = NULL;
    }
    m_file_state.file_count = 0;

    // 检查目录是否存在，不存在则创建
    DIR* dir = opendir(FILM_DIR);
    if(dir == NULL)
    {
        sys_logi(FILE_TAG, "Film directory not found, creating...");
        // 创建目录
        if(mkdir(FILM_DIR, 0777) != 0)
        {
            sys_loge(FILE_TAG, "Create film directory failed");
            return;
        }
        sys_logi(FILE_TAG, "Film directory created successfully");
        // 重新打开目录
        dir = opendir(FILM_DIR);
        if(dir == NULL)
        {
            sys_logw(FILE_TAG, "Open film directory failed");
            return;
        }
    }

    // 先统计文件数量
    struct dirent* entry;
    while((entry = readdir(dir)) != NULL)
    {
        if(entry->d_type == DT_REG)
        {
            sys_logi(FILE_TAG, "Found file: %s", entry->d_name);
            char* ext = strrchr(entry->d_name, '.');
            
            if(ext && strcmp(ext, FILM_FILE_EXT) == 0)
            {
                // 检查文件大小是否符合要求
                char filepath[512];
                snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, entry->d_name);
                struct stat st;
                if(stat(filepath, &st) == 0)
                {
                    if(st.st_size == FILE_EPD_IMGAGE_SIZE)
                    {
                        m_file_state.file_count++;
                    }
                    else
                    {
                        sys_logw(FILE_TAG, "File size not match: %s, expected: %d, actual: %d", entry->d_name, FILE_EPD_IMGAGE_SIZE, st.st_size);
                    }
                }
                else
                {
                    sys_logw(FILE_TAG, "Get file size failed: %s", entry->d_name);
                }
            }
        }
    }
    closedir(dir);

    // 分配文件列表内存
    if(m_file_state.file_count > 0)
    {
        m_file_state.file_list = (file_item_t*)malloc(sizeof(file_item_t) * m_file_state.file_count);
        if(m_file_state.file_list == NULL)
        {
            sys_loge(FILE_TAG, "Allocate file list memory failed");
            m_file_state.file_count = 0;
            return;
        }

        // 重新扫描并填充文件列表
        dir = opendir(FILM_DIR);
        if(dir == NULL)
        {
            sys_logw(FILE_TAG, "Open film directory failed");
            free(m_file_state.file_list);
            m_file_state.file_list = NULL;
            m_file_state.file_count = 0;
            return;
        }

        uint32_t index = 0;
        while((entry = readdir(dir)) != NULL)
        {
            if(entry->d_type == DT_REG)
            {
                char* ext = strrchr(entry->d_name, '.');
                if(ext && strcmp(ext, FILM_FILE_EXT) == 0)
                {
                    // 检查文件大小
                    char filepath[512];
                    snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, entry->d_name);
                    struct stat st;
                    if(stat(filepath, &st) == 0)
                    {
                        if(st.st_size == FILE_EPD_IMGAGE_SIZE)
                        {
                            /* 防越界：两趟 readdir 之间目录若被并发改动，本趟命中数可能超过
                             * 第一趟据以 malloc 的 file_count，超出即停止填充，避免堆越界写。 */
                            if(index >= m_file_state.file_count)
                            {
                                sys_logw(FILE_TAG, "file list grew during refresh, stop at %u", (unsigned)index);
                                break;
                            }
                            strncpy(m_file_state.file_list[index].filename, entry->d_name, sizeof(m_file_state.file_list[index].filename) - 1);
                            m_file_state.file_list[index].filename[sizeof(m_file_state.file_list[index].filename) - 1] = '\0';
                            m_file_state.file_list[index].file_size = st.st_size;
                            index++;
                        }
                        else
                        {
                            sys_logw(FILE_TAG, "Skip file size not match: %s, expected: %d, actual: %d", entry->d_name, FILE_EPD_IMGAGE_SIZE, st.st_size);
                        }
                    }
                    else
                    {
                        sys_logw(FILE_TAG, "Skip file size get failed: %s", entry->d_name);
                    }
                }
            }
        }
        closedir(dir);

        /* 实际填充数可能小于第一趟统计数（目录被并发改动时会提前 break），
         * 以真实填充数为准，避免列表尾部残留未初始化项被当成有效文件。 */
        m_file_state.file_count = index;

        /* 按文件名升序排序，让按键轮播次序确定：readdir 的返回顺序由文件系统决定、不保证
         * 稳定，不排序则「上一张/下一张」无法预期。排序后配合发送方用序号前缀命名
         * （1_today.film / 2_breakfast.film / ...），即可得到确定的翻页顺序。
         * 文件数量为个位数级别，插入排序足够且不额外占内存。 */
        for(uint32_t i = 1; i < m_file_state.file_count; i++)
        {
            file_item_t key = m_file_state.file_list[i];
            int32_t j = (int32_t)i - 1;
            while(j >= 0 && strcmp(m_file_state.file_list[j].filename, key.filename) > 0)
            {
                m_file_state.file_list[j + 1] = m_file_state.file_list[j];
                j--;
            }
            m_file_state.file_list[j + 1] = key;
        }

        sys_logi(FILE_TAG, "Found %d film files (sorted by name)", m_file_state.file_count);

        // 处理当前文件ID
        if(m_file_state.current_file_id >= m_file_state.file_count)
        {
            m_file_state.current_file_id = 0;
        }
    }
    else
    {
        sys_logi(FILE_TAG, "No film files found");
        m_file_state.current_file_id = 0;
    }
}

static void file_free_buffer(void)
{
    if(m_file_state.psram_buffer)
    {
        heap_caps_free(m_file_state.psram_buffer);
        m_file_state.psram_buffer = NULL;
        m_file_state.buffer_size = 0;
    }
}

static void file_load_event(uint32_t file_id)
{
    if(!m_file_state.sd_mounted)
    {
        sys_logw(FILE_TAG, "SD card not mounted");
        return;
    }

    // 进入加载状态
    m_file_state.load_complete = FILE_LOAD_STATE_LOADING;

    if(file_id >= m_file_state.file_count)
    {
        sys_logw(FILE_TAG, "Invalid file ID: %d", file_id);
        m_file_state.load_complete = FILE_LOAD_STATE_NONE;
        return;
    }

    // 释放现有缓冲区
    file_free_buffer();

    // 构建文件路径
    char filepath[512];
    snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, m_file_state.file_list[file_id].filename);

    // 打开文件
    FILE* file = fopen(filepath, "rb");
    if(file == NULL)
    {
        sys_loge(FILE_TAG, "Open file failed: %s", filepath);
        m_file_state.load_complete = FILE_LOAD_STATE_NONE;
        return;
    }

    // 获取文件大小
    fseek(file, 0, SEEK_END);
    uint32_t file_size = ftell(file);
    fseek(file, 0, SEEK_SET);

    // 分配PSRAM缓冲区
    m_file_state.psram_buffer = (uint8_t*)heap_caps_malloc(file_size, MALLOC_CAP_SPIRAM);
    if(m_file_state.psram_buffer == NULL)
    {
        sys_loge(FILE_TAG, "Allocate PSRAM buffer failed");
        fclose(file);
        m_file_state.load_complete = FILE_LOAD_STATE_NONE;
        return;
    }

    // 读取文件数据
    size_t read_size = fread(m_file_state.psram_buffer, 1, file_size, file);
    if(read_size != file_size)
    {
        sys_loge(FILE_TAG, "Read file failed");
        file_free_buffer();
        fclose(file);
        m_file_state.load_complete = FILE_LOAD_STATE_NONE;
        return;
    }

    fclose(file);

    m_file_state.buffer_size = file_size;
    m_file_state.current_file_id = file_id;

    sys_logi(FILE_TAG, "Loaded file: %s, size: %d bytes", m_file_state.file_list[file_id].filename, file_size);

    // 加载完成
    m_file_state.load_complete = FILE_LOAD_STATE_DONE;
}

static void file_load_next_event(void)
{
    if(m_file_state.file_count == 0)
    {
        sys_logw(FILE_TAG, "No files to load");
        return;
    }

    // 计算下一个文件ID（循环）
    uint32_t next_file_id = (m_file_state.current_file_id + 1) % m_file_state.file_count;
    file_load_event(next_file_id);
}

void service_file_refresh_list(void)
{
    file_msg_t msg;
    msg.ID = MSG_FILE_LIST_REFRESH;
    file_msg_send(&msg, 0);
}

int service_file_load(uint32_t file_id)
{
    if(file_id >= m_file_state.file_count)
    {
        sys_logw(FILE_TAG, "Invalid file ID: %d, total files: %d", file_id, m_file_state.file_count);
        return -1;
    }
    
    // 清空加载状态
    m_file_state.load_complete = FILE_LOAD_STATE_NONE;

    file_msg_t msg;
    msg.ID = MSG_FILE_LOAD;
    msg.file_id = file_id;
    file_msg_send(&msg, 0);
    return 0;
}

void service_file_load_next(void)
{
    // 清空加载状态
    m_file_state.load_complete = FILE_LOAD_STATE_NONE;

    file_msg_t msg;
    msg.ID = MSG_FILE_LOAD_NEXT;
    file_msg_send(&msg, 0);
}

int service_file_save_data(const char *pfilename, uint32_t file_size, uint8_t *pdata, uint32_t data_len)
{
    if(!m_file_state.sd_mounted)
    {
        sys_logw(FILE_TAG, "SD card not mounted");
        return -1;
    }

    file_msg_t msg = {0};
    msg.ID = MSG_FILE_SAVE_DATA;
    msg.file_size = file_size;
    msg.pdata = pdata;
    msg.data_len = data_len;
    file_msg_send(&msg, 0);
    return 0;
}

int service_file_save_start(const char *pfilename, uint32_t file_size)
{
    if(!m_file_state.sd_mounted)
    {
        sys_logw(FILE_TAG, "SD card not mounted");
        return -1;
    }

    if(file_size != FILE_EPD_IMGAGE_SIZE)
    {
        sys_logw(FILE_TAG, "Invalid file size: %d, expected: %d", file_size, FILE_EPD_IMGAGE_SIZE);
        return -1;
    }

    file_msg_t msg = {0};
    msg.ID = MSG_FILE_SAVE_START;
    msg.file_size = file_size;
    msg.pdata = (uint8_t*)pvPortMalloc(strlen(pfilename) + 1);
    if(msg.pdata)
    {
        memcpy(msg.pdata, pfilename, strlen(pfilename) + 1);
    }
    file_msg_send(&msg, 0);
    return 0;
}

void service_file_save_stop(void)
{
    file_msg_t msg = {0};
    msg.ID = MSG_FILE_SAVE_STOP;
    file_msg_send(&msg, 0);
}

uint32_t service_file_get_count(void)
{
    return m_file_state.file_count;
}

const char* service_file_get_name(uint32_t file_id)
{
    if(file_id >= m_file_state.file_count)
    {
        return NULL;
    }
    return m_file_state.file_list[file_id].filename;
}

uint8_t* service_file_get_buffer(void)
{
    return m_file_state.psram_buffer;
}

uint32_t service_file_get_buffer_size(void)
{
    return m_file_state.buffer_size;
}

uint32_t service_file_get_current_id(void)
{
    return m_file_state.current_file_id;
}

uint8_t service_file_get_load_complete(void)
{
    return m_file_state.load_complete;
}

uint32_t service_file_get_size(uint32_t file_id)
{
    if(file_id >= m_file_state.file_count)
    {
        return 0;
    }
    return m_file_state.file_list[file_id].file_size;
}

const char* service_file_get_filename(uint32_t file_id)
{
    if(file_id >= m_file_state.file_count)
    {
        return NULL;
    }
    return m_file_state.file_list[file_id].filename;
}

/* clear_all 的循环与缓冲上限。
 * 刻意用小缓冲：本函数在 BLE 任务上下文运行，栈仅 4096 字节，且 FATFS 调用链自身也吃栈。
 * 轮数上限兼作死循环护栏（每轮必须成功删掉一个，否则立即跳出）。 */
#define CLEAR_MAX_ROUNDS   (64)
#define FILM_NAME_MAX      (64)
#define FILM_PATH_MAX      (192)

int service_file_clear_all_async(void)
{
    if(m_file_msg_hdl == NULL)
    {
        sys_logw(FILE_TAG, "clear all async: file task not ready");
        return -1;
    }
    file_msg_t msg = {0};
    msg.ID = MSG_FILE_CLEAR_ALL;
    file_msg_send(&msg, 0);
    return 0;
}

int service_file_clear_all(void)
{
    if(!m_file_state.sd_mounted)
    {
        sys_logw(FILE_TAG, "clear all: SD not mounted");
        return -1;
    }

    /* 传输进行中拒绝清空：正在写入的临时文件与单写者事务不能被这里的批量 remove 干扰，
     * 否则会出现「事务以为自己还持有文件、文件却已被删」的不一致。发送方应在开始
     * 下发前清空，而不是边传边清。 */
    file_receive_state_t rx = service_file_get_receive_state();
    if(rx.state == FT_RECEIVING || rx.state == FT_VALIDATING)
    {
        sys_logw(FILE_TAG, "clear all rejected: transfer in progress (state=%d)", (int)rx.state);
        return -2;
    }

    DIR* dir = opendir(FILM_DIR);
    if(dir == NULL)
    {
        sys_logi(FILE_TAG, "clear all: film dir not found, nothing to do");
        return 0;
    }

    closedir(dir);

    /* 边 readdir 边 remove 在部分 FATFS 实现上行为未定义，故每轮「只取一个名字 → 关目录
     * → 删除」，再重新打开目录继续，直到没有 .film 可删。
     *
     * 不一次性收集全部名字：本函数运行在 BLE 任务上下文，栈只有 4096 字节
     * （SYS_OS_SIZE_BLE_TASK），而 FATFS 的 opendir/readdir/remove 调用链本身就要占不少
     * 栈；用 char[32][64] 一次收集需要 2KB，占掉一半栈空间，栈溢出风险很高。每轮只用一个
     * 文件名缓冲的代价是多几次 opendir，而文件数是个位数级别，可以接受。 */
    int removed = 0;
    for(int round = 0; round < CLEAR_MAX_ROUNDS; round++)
    {
        char name[FILM_NAME_MAX] = {0};
        bool found = false;

        dir = opendir(FILM_DIR);
        if(dir == NULL)
        {
            break;
        }
        struct dirent* entry;
        while((entry = readdir(dir)) != NULL)
        {
            if(entry->d_type != DT_REG)
            {
                continue;
            }
            char* ext = strrchr(entry->d_name, '.');
            if(ext && strcmp(ext, FILM_FILE_EXT) == 0)
            {
                strncpy(name, entry->d_name, sizeof(name) - 1);
                name[sizeof(name) - 1] = '\0';
                found = true;
                break;
            }
        }
        closedir(dir);

        if(!found)
        {
            break;   /* 已无 .film，正常结束 */
        }

        char filepath[FILM_PATH_MAX];
        snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, name);
        if(remove(filepath) == 0)
        {
            removed++;
        }
        else
        {
            /* 删不掉就必须跳出，否则下一轮又会取到同一个文件导致死循环 */
            sys_logw(FILE_TAG, "clear all: delete failed %s, stop", filepath);
            break;
        }
    }

    /* 刷新列表并复位游标。注意【不清屏】：当前画面继续保留，直到下一张图上屏，
     * 避免清空瞬间出现白屏。 */
    service_file_refresh_list();
    m_file_state.current_file_id = 0;

    sys_logi(FILE_TAG, "clear all: removed %d film files", removed);
    return removed;
}

/* 按名删除单个 .film。安全基名判据与语义见 service_file.h。
 *
 * 为什么不复用 file_txn_filename_is_safe()：那个谓词服务于「接收进来的文件名」，
 * 允许到 128 字符且不要求后缀。删除是不可逆操作，判据应当更窄 —— 这里额外要求
 * 以 .film 结尾，把「误删非画面文件」这条路直接堵死。 */
int service_file_remove(const char *name)
{
    if(name == NULL || name[0] == '\0')
    {
        sys_logw(FILE_TAG, "remove: empty name");
        return -1;
    }

    /* 安全基名：无路径分隔符、无穿越片段、无控制字符、长度有界。
     * 本函数拼的是落盘路径，放宽一点就是一次任意路径删除。 */
    size_t len = strnlen(name, FILM_NAME_MAX);
    if(len == 0 || len >= FILM_NAME_MAX || name[len] != '\0')
    {
        sys_logw(FILE_TAG, "remove rejected: name too long");
        return -1;
    }
    for(size_t i = 0; i < len; i++)
    {
        unsigned char ch = (unsigned char)name[i];
        if(ch == '/' || ch == '\\' || ch < 0x20)
        {
            sys_logw(FILE_TAG, "remove rejected: unsafe name");
            return -1;
        }
    }
    if(strstr(name, "..") != NULL)
    {
        sys_logw(FILE_TAG, "remove rejected: path traversal in name");
        return -1;
    }
    const char *ext = strrchr(name, '.');
    if(ext == NULL || strcmp(ext, FILM_FILE_EXT) != 0)
    {
        sys_logw(FILE_TAG, "remove rejected: not a %s file", FILM_FILE_EXT);
        return -1;
    }

    if(!m_file_state.sd_mounted)
    {
        sys_logw(FILE_TAG, "remove: SD not mounted");
        return -1;
    }

    /* 传输进行中拒绝：正在写入的临时文件与单写者事务不能被这里的 remove 干扰，
     * 否则会出现「事务以为自己还持有文件、文件却已被删」的不一致（与 clear_all 同理）。 */
    file_receive_state_t rx = service_file_get_receive_state();
    if(rx.state == FT_RECEIVING || rx.state == FT_VALIDATING)
    {
        sys_logw(FILE_TAG, "remove rejected: transfer in progress (state=%d)", (int)rx.state);
        return -2;
    }

    char filepath[FILM_PATH_MAX];
    snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, name);
    if(remove(filepath) != 0)
    {
        /* 文件本来就不存在是常见且无害的情形（例如设备从未走过单张路径），
         * 故只记 info 级，不当作错误上报。 */
        sys_logi(FILE_TAG, "remove: %s not removed (absent or failed)", name);
        return -1;
    }

    /* 刷新列表让轮播看不到已删文件。不复位游标：删的是遗留文件，不该打断用户
     * 当前的翻页位置。也【不清屏】，当前画面保留到下一张图上屏。 */
    service_file_refresh_list();
    sys_logi(FILE_TAG, "removed film file: %s", name);
    return 0;
}

int service_file_delete(uint32_t file_id)
{
    if(file_id >= m_file_state.file_count)
    {
        sys_logw(FILE_TAG, "Invalid file id: %d", file_id);
        return -1;
    }

    char filepath[512];
    snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, m_file_state.file_list[file_id].filename);

    if(remove(filepath) == 0)
    {
        sys_logi(FILE_TAG, "Deleted file: %s", filepath);
        service_file_refresh_list();
        return 0;
    }
    else
    {
        sys_logw(FILE_TAG, "Failed to delete file: %s", filepath);
        return -1;
    }
}


/*********************************************************************
 * 双通道唯一文件事务接口（Smart Fridge Magnet 新增）
 *
 * 纯决策逻辑（参数/文件名/累计长度/单写者状态转移）位于 file_txn.c，可在宿主机
 * 做属性测试；此处仅负责：互斥保护、esp_err_t 映射、以及后续任务的文件系统副作用
 * 接入点（临时文件、fsync、rename、增量 SHA-256、finish 校验）。
 *********************************************************************/

/* 将纯决策返回码映射为 esp_err_t（busy 采用 ESP_ERR_INVALID_STATE，见任务约束）。 */
static esp_err_t file_txn_rc_to_esp(file_txn_rc_t rc)
{
    switch(rc)
    {
    case FILE_TXN_OK:            return ESP_OK;
    case FILE_TXN_ERR_ARG:       return ESP_ERR_INVALID_ARG;
    case FILE_TXN_ERR_BUSY:      return ESP_ERR_INVALID_STATE;  /* busy */
    case FILE_TXN_ERR_STATE:     return ESP_ERR_INVALID_STATE;
    case FILE_TXN_ERR_OVERFLOW:  return ESP_ERR_INVALID_SIZE;
    default:                     return ESP_FAIL;
    }
}

/* 获取事务锁；未初始化（NULL）时退化为无锁最佳努力，避免早期调用崩溃。 */
static inline void rx_lock(void)
{
    if(m_rx_lock != NULL)
    {
        xSemaphoreTake(m_rx_lock, portMAX_DELAY);
    }
}

static inline void rx_unlock(void)
{
    if(m_rx_lock != NULL)
    {
        xSemaphoreGive(m_rx_lock);
    }
}

/* 关闭临时文件句柄并删除临时文件（Req 7.6）。
 * 用于 abort/失败路径：当前有效画面保持不变。调用方须已持有 m_rx_lock（或处于
 * init 单线程阶段）。remove 对不存在的临时文件返回失败可忽略。 */
static void fs_rx_cleanup_temp(void)
{
    if(m_rx_tmp_fp != NULL)
    {
        fclose(m_rx_tmp_fp);
        m_rx_tmp_fp = NULL;
    }
    /* 无论句柄是否存在都尝试删除，避免遗留半成品临时文件。 */
    remove(FILM_TMP_PATH);
    m_rx_final_path[0] = '\0';
    /* 增量 SHA-256 上下文与临时句柄同生命周期：作为所有终止路径的统一收口，一并释放
     * 摘要上下文。fs_rx_sha256_reset 内部据 m_rx_sha_active 幂等处理，
     * 未初始化时为 no-op，故 begin 打开临时文件失败（尚未 init 摘要）也安全。 */
    fs_rx_sha256_reset();
}

/* 释放并停用增量 SHA-256 上下文。据 m_rx_sha_active 幂等：仅在上下文已
 * setup 后才 psa_hash_abort，之后清标记，可安全重复调用。调用方须已持有 m_rx_lock
 * （或处于 init 单线程阶段）。psa_hash_abort 对“已置 INIT 未 setup/已 finish”的上下文亦安全。 */
static void fs_rx_sha256_reset(void)
{
    if(m_rx_sha_active)
    {
        psa_hash_abort(&m_rx_sha_ctx);
        m_rx_sha_active = false;
    }
}

/* 取增量 SHA-256 终值（Req 12.2）。直接对 write 期间增量累积的上下文
 * psa_hash_finish 输出 32 字节摘要，绝不在此重新读取/缓冲整个临时文件——这正是
 * D9 “写入时增量、结束时取终值”的落点。finish 校验分支取到终值后与期望摘要比对。
 * 调用方须已持有 m_rx_lock。
 *
 * @return ESP_OK 且 out 填入摘要；ESP_ERR_INVALID_STATE 无活动摘要；ESP_FAIL 计算失败。 */
static esp_err_t fs_rx_sha256_finish(uint8_t out[32])
{
    if(out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    if(!m_rx_sha_active)
    {
        return ESP_ERR_INVALID_STATE;
    }
    size_t sha_out_len = 0;
    if(psa_hash_finish(&m_rx_sha_ctx, out, 32, &sha_out_len) != PSA_SUCCESS
       || sha_out_len != 32)
    {
        sys_loge(FILE_TAG, "rx sha256 finish failed");
        return ESP_FAIL;
    }
    return ESP_OK;
}

/* 统一的事务终止清理（Req 7.4/7.6）。
 *
 * 所有“失败 / 超长 / 断连 / 显式 abort”终止路径的唯一收口，保证每条路径都完整地：
 *   1) 关闭临时文件句柄并删除临时文件 current.film.tmp（fs_rx_cleanup_temp）；
 *   2) 释放写所有权（file_txn_abort 将 owner 置 FILE_SRC_NONE）；
 *   3) 完成 ABORTED->IDLE 清理转移，复位为干净空闲态（file_txn_reset）。
 *
 * 语义要点：
 *   - 当前有效画面（current.film）在任何终止路径都保持不变——本函数只删除 *.tmp，
 *     从不触碰最终文件；提交(rename)仅发生在 finish 全部校验通过后。
 *   - 复位为 FT_IDLE 使 write 失败/超长与显式 abort 的终态一致，便于状态查询与
 *     休眠互斥判定，也让下一次 begin 从干净空闲态重新授予所有权（Req 7.6）。
 *
 * 调用方须已持有 m_rx_lock（begin/write/abort 均在锁内调用本函数）。 */
static void rx_abort_and_cleanup(void)
{
    file_txn_abort(&m_rx_state);   /* 释放写所有权（owner=NONE），标记 FT_ABORTED */
    fs_rx_cleanup_temp();          /* 关句柄 + 删临时文件 + 清目标路径（不动 current.film） */
    file_txn_reset(&m_rx_state);   /* 完成 ABORTED->IDLE，回到干净空闲态 */
}

/* 原子替换原语（Req 8.1）：fsync(tmp) → rename(tmp, final)。
 *
 * 只由 finish 在长度/文件头/SHA-256 全部校验通过后调用：校验通过前绝不覆盖当前有效
 * 文件（Req 7.7）。
 *
 * ESP-IDF/FATFS 说明：
 *  - stdio 的 fflush() 仅把用户态缓冲刷入 VFS，需再 fsync(fileno) 触发 FATFS f_sync
 *    将数据落到 TF/SDnand（Req 8.1 的“执行 fsync”）。
 *  - FATFS 的 rename 在目标已存在时会失败（FR_EXIST），故先 remove(final) 再 rename；
 *    这一步使替换在掉电语义上非严格原子，正是 B5 采用“临时文件 + fsync + rename +
 *    启动清理 + 显示前校验文件头”组合来获得工程上可接受的断电安全的原因。 */
static esp_err_t fs_commit_temp_to_final(void)
{
    if(m_rx_tmp_fp == NULL || m_rx_final_path[0] == '\0')
    {
        return ESP_ERR_INVALID_STATE;
    }

    /* 1) 用户态缓冲落入 VFS。 */
    if(fflush(m_rx_tmp_fp) != 0)
    {
        sys_loge(FILE_TAG, "commit: fflush failed");
        fs_rx_cleanup_temp();
        return ESP_FAIL;
    }

    /* 2) 强制 FATFS 将数据同步到存储介质（Req 8.1）。 */
    int fd = fileno(m_rx_tmp_fp);
    if(fd >= 0)
    {
        if(fsync(fd) != 0)
        {
            sys_loge(FILE_TAG, "commit: fsync failed");
            fs_rx_cleanup_temp();
            return ESP_FAIL;
        }
    }

    /* 3) 关闭句柄后再重命名（FATFS 要求重命名的文件未被打开）。 */
    fclose(m_rx_tmp_fp);
    m_rx_tmp_fp = NULL;

    /* 4) 原子替换：FATFS rename 不覆盖已存在目标，先删除旧目标再重命名。 */
    remove(m_rx_final_path);
    if(rename(FILM_TMP_PATH, m_rx_final_path) != 0)
    {
        sys_loge(FILE_TAG, "commit: rename %s -> %s failed", FILM_TMP_PATH, m_rx_final_path);
        remove(FILM_TMP_PATH);   /* 重命名失败则删除临时文件，避免遗留 */
        m_rx_final_path[0] = '\0';
        return ESP_FAIL;
    }

    sys_logi(FILE_TAG, "commit: atomic replace -> %s", m_rx_final_path);
    m_rx_final_path[0] = '\0';
    /* 提交成功路径同样释放增量 SHA-256 上下文，避免此路径泄漏。finish 在调用本原语前
     * 已经经 fs_rx_sha256_finish 取得终值比对，故此处 free 不影响比对。 */
    fs_rx_sha256_reset();
    return ESP_OK;
}

/* 校验某 .film 文件的 32 字节文件头是否有效（仅读头，不读像素，Req 8.2 只要求
 * “文件头有效”）。有效条件对齐 film_validate 的头字段检查。 */
static bool fs_film_header_valid(const char *path)
{
    FILE *fp = fopen(path, "rb");
    if(fp == NULL)
    {
        return false;
    }

    uint8_t hdr[FILM_HEADER_SIZE_BYTES];
    size_t n = fread(hdr, 1, sizeof(hdr), fp);
    fclose(fp);
    if(n != sizeof(hdr))
    {
        return false;
    }

    film_header_t h;
    film_parse_header(hdr, &h);
    return (h.pixel_bytes == FILM_PIXEL_BYTES &&
            h.width       == FILM_EXPECTED_WIDTH &&
            h.height      == FILM_EXPECTED_HEIGHT &&
            h.color_count == FILM_EXPECTED_COLOR_COUNT);
}

/* 判断 FILM_DIR 下是否存在“当前有效文件”（尺寸正确且文件头有效的 .film）。
 * 用作启动清理的前置条件（Req 8.2）。 */
static bool fs_dir_has_valid_current_film(void)
{
    DIR *dir = opendir(FILM_DIR);
    if(dir == NULL)
    {
        return false;   /* 目录不存在 => 无当前有效文件 */
    }

    bool found = false;
    struct dirent *entry;
    while((entry = readdir(dir)) != NULL)
    {
        if(entry->d_type != DT_REG)
        {
            continue;
        }
        const char *ext = strrchr(entry->d_name, '.');
        if(ext == NULL || strcmp(ext, FILM_FILE_EXT) != 0)
        {
            continue;   /* 只看 .film（跳过 .tmp 等） */
        }

        char filepath[512];
        snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, entry->d_name);
        struct stat st;
        if(stat(filepath, &st) != 0 || st.st_size != FILE_EPD_IMGAGE_SIZE)
        {
            continue;
        }
        if(fs_film_header_valid(filepath))
        {
            found = true;
            break;
        }
    }
    closedir(dir);
    return found;
}

/* 启动清理遗留临时文件（Req 8.2）。
 *
 * 语义：仅“在当前有效文件头有效的前提下”删除 FILM_DIR 下的一切 *.tmp。若当前不存在
 * 任何有效 .film（前置条件不满足），保守保留临时文件、不删除，避免误删可能仍可诊断的
 * 中间产物或在无有效画面时改变现场。由于提交是 fsync→rename，遗留 *.tmp 必为一次被
 * 中断（未提交）的写入，删除它既不会丢失已提交画面，也不会破坏当前有效文件。 */
static void fs_cleanup_stale_temp_on_boot(void)
{
    if(hal_sd_get_status() != SD_MOUNT)
    {
        sys_logw(FILE_TAG, "boot temp cleanup skipped: SD not mounted");
        return;
    }

    if(!fs_dir_has_valid_current_film())
    {
        sys_logw(FILE_TAG, "boot temp cleanup skipped: no valid current .film, keep leftover temp(s)");
        return;
    }

    DIR *dir = opendir(FILM_DIR);
    if(dir == NULL)
    {
        return;   /* 无目录则无遗留可清理 */
    }

    int removed = 0;
    struct dirent *entry;
    while((entry = readdir(dir)) != NULL)
    {
        if(entry->d_type != DT_REG)
        {
            continue;
        }
        const char *ext = strrchr(entry->d_name, '.');
        if(ext == NULL || strcmp(ext, FILM_TMP_EXT) != 0)
        {
            continue;   /* 只清理 *.tmp */
        }

        char filepath[512];
        snprintf(filepath, sizeof(filepath), "%s/%s", FILM_DIR, entry->d_name);
        if(remove(filepath) == 0)
        {
            removed++;
            sys_logi(FILE_TAG, "boot: removed leftover temp %s", entry->d_name);
        }
        else
        {
            sys_logw(FILE_TAG, "boot: failed to remove leftover temp %s", entry->d_name);
        }
    }
    closedir(dir);
    sys_logi(FILE_TAG, "boot temp cleanup done: removed %d leftover temp file(s)", removed);
}

typedef struct {
    size_t count;
    bool had_original[FILM_SET_MAX_ITEMS];
    char names[FILM_SET_MAX_ITEMS][FILM_SET_NAME_MAX + 1u];
} film_set_marker_t;

static bool fs_set_artifact_path(char *out, size_t out_size,
                                 const char *name, const char *suffix)
{
    if(out == NULL || out_size == 0 || !film_set_name_is_safe(name) || suffix == NULL)
    {
        return false;
    }
    int n = snprintf(out, out_size, "%s/%s%s", FILM_DIR, name, suffix);
    return n > 0 && (size_t)n < out_size;
}

static bool fs_set_path_exists(const char *path)
{
    struct stat st;
    return path != NULL && stat(path, &st) == 0;
}

static bool fs_set_remove_if_exists(const char *path)
{
    if(!fs_set_path_exists(path))
    {
        return true;
    }
    return remove(path) == 0;
}

static esp_err_t fs_set_write_prepare(const film_set_marker_t *marker)
{
    FILE *fp = fopen(FILM_SET_PREP_PATH, "wb");
    if(fp == NULL)
    {
        return ESP_FAIL;
    }

    bool ok = fprintf(fp, "%s %u\n", FILM_SET_MARKER_MAGIC,
                      (unsigned)marker->count) > 0;
    for(size_t i = 0; ok && i < marker->count; i++)
    {
        ok = fprintf(fp, "%u %s\n", marker->had_original[i] ? 1u : 0u,
                     marker->names[i]) > 0;
    }
    if(ok)
    {
        ok = fflush(fp) == 0;
    }
    if(ok)
    {
        int fd = fileno(fp);
        ok = fd >= 0 && fsync(fd) == 0;
    }
    if(fclose(fp) != 0)
    {
        ok = false;
    }
    if(!ok)
    {
        remove(FILM_SET_PREP_PATH);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static bool fs_set_read_prepare(film_set_marker_t *marker)
{
    if(marker == NULL)
    {
        return false;
    }
    memset(marker, 0, sizeof(*marker));

    FILE *fp = fopen(FILM_SET_PREP_PATH, "rb");
    if(fp == NULL)
    {
        return false;
    }

    char magic[sizeof(FILM_SET_MARKER_MAGIC)] = {0};
    unsigned count = 0;
    bool ok = fscanf(fp, "%7s %u", magic, &count) == 2 &&
              strcmp(magic, FILM_SET_MARKER_MAGIC) == 0 &&
              count > 0u && count <= FILM_SET_MAX_ITEMS;
    marker->count = ok ? (size_t)count : 0u;

    for(size_t i = 0; ok && i < marker->count; i++)
    {
        unsigned had = 0;
        ok = fscanf(fp, "%u %31s", &had, marker->names[i]) == 2 &&
             had <= 1u && film_set_name_is_safe(marker->names[i]);
        marker->had_original[i] = had == 1u;
    }
    fclose(fp);
    return ok;
}

static esp_err_t fs_set_write_done(void)
{
    FILE *fp = fopen(FILM_SET_DONE_PATH, "wb");
    if(fp == NULL)
    {
        return ESP_FAIL;
    }
    bool ok = fwrite(FILM_SET_MARKER_MAGIC, 1,
                     sizeof(FILM_SET_MARKER_MAGIC) - 1u, fp) ==
              sizeof(FILM_SET_MARKER_MAGIC) - 1u;
    if(ok)
    {
        ok = fflush(fp) == 0;
    }
    if(ok)
    {
        int fd = fileno(fp);
        ok = fd >= 0 && fsync(fd) == 0;
    }
    if(fclose(fp) != 0)
    {
        ok = false;
    }
    if(!ok)
    {
        remove(FILM_SET_DONE_PATH);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static bool fs_set_done_valid(void)
{
    FILE *fp = fopen(FILM_SET_DONE_PATH, "rb");
    if(fp == NULL)
    {
        return false;
    }
    char magic[sizeof(FILM_SET_MARKER_MAGIC)] = {0};
    size_t n = fread(magic, 1, sizeof(FILM_SET_MARKER_MAGIC) - 1u, fp);
    fclose(fp);
    return n == sizeof(FILM_SET_MARKER_MAGIC) - 1u &&
           strcmp(magic, FILM_SET_MARKER_MAGIC) == 0;
}

static bool fs_set_cleanup_artifacts(const film_set_marker_t *marker)
{
    bool ok = true;
    for(size_t i = 0; i < marker->count; i++)
    {
        char path[512];
        if(fs_set_artifact_path(path, sizeof(path), marker->names[i], FILM_SET_STAGE_SUFFIX))
        {
            ok = fs_set_remove_if_exists(path) && ok;
        }
        if(fs_set_artifact_path(path, sizeof(path), marker->names[i], FILM_SET_BACKUP_SUFFIX))
        {
            ok = fs_set_remove_if_exists(path) && ok;
        }
    }
    return ok;
}

static bool fs_set_rollback(const film_set_marker_t *marker)
{
    bool ok = true;
    for(size_t i = 0; i < marker->count; i++)
    {
        char target[512];
        char backup[512];
        char stage[512];
        (void)snprintf(target, sizeof(target), "%s/%s", FILM_DIR, marker->names[i]);
        (void)fs_set_artifact_path(backup, sizeof(backup), marker->names[i],
                                   FILM_SET_BACKUP_SUFFIX);
        (void)fs_set_artifact_path(stage, sizeof(stage), marker->names[i],
                                   FILM_SET_STAGE_SUFFIX);

        bool backup_exists = fs_set_path_exists(backup);
        switch(film_set_recovery_action(false, marker->had_original[i], backup_exists))
        {
        case FILM_SET_RECOVERY_RESTORE_BACKUP:
            ok = fs_set_remove_if_exists(target) && ok;
            if(rename(backup, target) != 0)
            {
                ok = false;
            }
            break;
        case FILM_SET_RECOVERY_REMOVE_TARGET:
            ok = fs_set_remove_if_exists(target) && ok;
            break;
        case FILM_SET_RECOVERY_KEEP:
        default:
            break;
        }
        ok = fs_set_remove_if_exists(stage) && ok;
    }

    if(ok)
    {
        (void)fs_set_cleanup_artifacts(marker);
        remove(FILM_SET_DONE_PATH);
        remove(FILM_SET_PREP_PATH);
    }
    return ok;
}

static bool fs_name_has_suffix(const char *name, const char *suffix)
{
    size_t name_len = strlen(name);
    size_t suffix_len = strlen(suffix);
    return name_len > suffix_len &&
           strcmp(name + name_len - suffix_len, suffix) == 0;
}

static void fs_set_cleanup_orphans(void)
{
    /* 每轮只删一个后重新打开目录，避免 FATFS 上边 readdir 边 remove。 */
    for(unsigned round = 0; round < 64u; round++)
    {
        DIR *dir = opendir(FILM_DIR);
        if(dir == NULL)
        {
            return;
        }
        char found[128] = {0};
        struct dirent *entry;
        while((entry = readdir(dir)) != NULL)
        {
            if(entry->d_type == DT_REG &&
               (fs_name_has_suffix(entry->d_name, FILM_SET_STAGE_SUFFIX) ||
                fs_name_has_suffix(entry->d_name, FILM_SET_BACKUP_SUFFIX)))
            {
                /* 显式跳过装不下的名字，而不是让 snprintf 静默截断。
                 *
                 * `entry->d_name` 是 256 字节数组，IDF 的 -Werror=format-truncation 会
                 * 因此拒绝编译 `snprintf(found, 128, "%s", d_name)`。把缓冲扩到 256 只是
                 * 让编译器闭嘴 —— 真正的问题是**截断后的名字指向另一个文件**：拿它去
                 * remove() 要么失败（好情况），要么删掉一个刚好同前缀的别的文件。
                 *
                 * 这里的中间文件名由本模块自己生成（`<基名>.film` + 固定后缀，见
                 * FILM_SET_STAGE_SUFFIX / FILM_SET_BACKUP_SUFFIX），远短于 128；长到装不
                 * 下的只可能是外来文件，那本就不该由这个清理函数处置。 */
                size_t nlen = strlen(entry->d_name);
                if(nlen >= sizeof(found))
                {
                    continue;
                }
                memcpy(found, entry->d_name, nlen + 1u);
                break;
            }
        }
        closedir(dir);
        if(found[0] == '\0')
        {
            return;
        }
        char path[512];
        (void)snprintf(path, sizeof(path), "%s/%s", FILM_DIR, found);
        if(remove(path) != 0)
        {
            return;
        }
    }
}

static void fs_recover_set_on_boot(void)
{
    if(hal_sd_get_status() != SD_MOUNT)
    {
        return;
    }

    bool prepared = fs_set_path_exists(FILM_SET_PREP_PATH);
    bool committed = fs_set_done_valid();
    if(prepared)
    {
        film_set_marker_t marker;
        if(!fs_set_read_prepare(&marker))
        {
            sys_loge(FILE_TAG, "film set recovery: invalid prepare marker, artifacts retained");
            return;
        }

        if(committed)
        {
            /* done 已 fsync：新套是提交结果，只需完成幂等清理。 */
            if(fs_set_cleanup_artifacts(&marker))
            {
                remove(FILM_SET_PREP_PATH);
                remove(FILM_SET_DONE_PATH);
            }
            sys_logi(FILE_TAG, "film set recovery: completed committed set cleanup");
        }
        else
        {
            bool ok = fs_set_rollback(&marker);
            sys_logi(FILE_TAG, "film set recovery: rollback %s", ok ? "complete" : "deferred");
        }
        return;
    }

    /* staging 可能在“下载完几张但还没开始集合提交”时掉电；没有 prepare 就没有任何正式
     * 文件被改动，可以直接清理。孤立 done 也只是已完成提交的清理尾巴。 */
    remove(FILM_SET_DONE_PATH);
    fs_set_cleanup_orphans();
}

esp_err_t service_file_receive_begin_staged(const char *filename,
                                            uint32_t expected_size)
{
    if(!film_set_name_is_safe(filename))
    {
        return ESP_ERR_INVALID_ARG;
    }
    char stage_name[FILM_SET_NAME_MAX + sizeof(FILM_SET_STAGE_SUFFIX)];
    int n = snprintf(stage_name, sizeof(stage_name), "%s%s",
                     filename, FILM_SET_STAGE_SUFFIX);
    if(n <= 0 || (size_t)n >= sizeof(stage_name))
    {
        return ESP_ERR_INVALID_SIZE;
    }
    return service_file_receive_begin(FILE_SRC_WIFI, stage_name, expected_size);
}

void service_file_discard_staged_set(const char *const names[], size_t count)
{
    if(names == NULL)
    {
        return;
    }
    rx_lock();
    for(size_t i = 0; i < count && i < FILM_SET_MAX_ITEMS; i++)
    {
        char stage[512];
        if(fs_set_artifact_path(stage, sizeof(stage), names[i], FILM_SET_STAGE_SUFFIX))
        {
            (void)fs_set_remove_if_exists(stage);
        }
    }
    rx_unlock();
}

esp_err_t service_file_commit_staged_set(const char *const names[], size_t count)
{
    if(names == NULL || count == 0u || count > FILM_SET_MAX_ITEMS)
    {
        return ESP_ERR_INVALID_ARG;
    }

    rx_lock();
    if(m_rx_state.state == FT_RECEIVING || m_rx_state.state == FT_VALIDATING)
    {
        rx_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    if(!m_file_state.sd_mounted)
    {
        rx_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    /* 上一轮 marker 仍在说明清理或回滚没有完整结束。覆盖 marker 会让启动恢复失去
     * 依据；旧 done 也可能把本轮中途掉电误判为已提交，因此直接 fail-closed。 */
    if(fs_set_path_exists(FILM_SET_PREP_PATH) || fs_set_path_exists(FILM_SET_DONE_PATH))
    {
        rx_unlock();
        return ESP_ERR_INVALID_STATE;
    }

    film_set_marker_t marker = {0};
    marker.count = count;
    for(size_t i = 0; i < count; i++)
    {
        if(!film_set_name_is_safe(names[i]))
        {
            rx_unlock();
            return ESP_ERR_INVALID_ARG;
        }
        for(size_t j = 0; j < i; j++)
        {
            if(strcmp(names[i], names[j]) == 0)
            {
                rx_unlock();
                return ESP_ERR_INVALID_ARG;
            }
        }
        snprintf(marker.names[i], sizeof(marker.names[i]), "%s", names[i]);

        char target[512];
        char stage[512];
        char backup[512];
        (void)snprintf(target, sizeof(target), "%s/%s", FILM_DIR, names[i]);
        (void)fs_set_artifact_path(stage, sizeof(stage), names[i], FILM_SET_STAGE_SUFFIX);
        (void)fs_set_artifact_path(backup, sizeof(backup), names[i], FILM_SET_BACKUP_SUFFIX);
        struct stat st;
        if(stat(stage, &st) != 0 || st.st_size != FILM_FILE_TOTAL_SIZE ||
           !fs_film_header_valid(stage))
        {
            rx_unlock();
            return ESP_ERR_INVALID_SIZE;
        }
        /* backup 必须在 prepare 之前清干净。若留到正式文件已经开始改名后才发现删不掉，
         * 回滚可能把上一次事务的陈旧 backup 当成本轮旧文件恢复。 */
        if(!fs_set_remove_if_exists(backup))
        {
            rx_unlock();
            return ESP_FAIL;
        }
        marker.had_original[i] = fs_set_path_exists(target);
    }

    esp_err_t err = fs_set_write_prepare(&marker);
    if(err != ESP_OK)
    {
        rx_unlock();
        return err;
    }

    for(size_t i = 0; i < count; i++)
    {
        char target[512];
        char stage[512];
        char backup[512];
        (void)snprintf(target, sizeof(target), "%s/%s", FILM_DIR, names[i]);
        (void)fs_set_artifact_path(stage, sizeof(stage), names[i], FILM_SET_STAGE_SUFFIX);
        (void)fs_set_artifact_path(backup, sizeof(backup), names[i], FILM_SET_BACKUP_SUFFIX);

        if((marker.had_original[i] && rename(target, backup) != 0) ||
           rename(stage, target) != 0)
        {
            sys_loge(FILE_TAG, "film set commit failed at %s, rolling back", names[i]);
            (void)fs_set_rollback(&marker);
            rx_unlock();
            return ESP_FAIL;
        }
    }

    err = fs_set_write_done();
    if(err != ESP_OK)
    {
        (void)fs_set_rollback(&marker);
        rx_unlock();
        return err;
    }

    if(fs_set_cleanup_artifacts(&marker))
    {
        remove(FILM_SET_PREP_PATH);
        remove(FILM_SET_DONE_PATH);
    }
    rx_unlock();
    sys_logi(FILE_TAG, "film set committed: %u item(s)", (unsigned)count);
    return ESP_OK;
}

esp_err_t service_file_receive_begin(file_source_t source,
                                     const char *filename,
                                     uint32_t expected_size)
{
    rx_lock();

    /* M3 卡死事务超时自愈：若已有 RECEIVING 事务长时间无写入进展（对端连着却不发
     * 数据/不 STOP），先超时回收它，避免其永久占用单写者锁、阻塞新传输。正常传输
     * 每块 write 都刷新活动时间，故不会被误回收。tick 差用无符号运算，天然处理回绕。 */
    if(m_rx_state.state == FT_RECEIVING &&
       (uint32_t)(xTaskGetTickCount() - m_rx_last_activity_tick) > pdMS_TO_TICKS(FILE_RX_IDLE_TIMEOUT_MS))
    {
        sys_logw(FILE_TAG, "rx begin: stale RECEIVING txn idle-timeout, reclaiming");
        rx_abort_and_cleanup();
    }

    file_txn_rc_t rc = file_txn_begin(&m_rx_state, source, filename, expected_size);
    if(rc == FILE_TXN_OK)
    {
        m_rx_last_activity_tick = xTaskGetTickCount();   /* 记录活动时间（M3） */
        /* 授予所有权后打开临时文件 current.film.tmp（Req 8.1）。
         * 记录提交目标路径 FILM_DIR/<filename>，供 finish 通过校验后原子替换。
         * filename 已由 file_txn_begin 校验为安全基础名（Req 7.3）。 */
        snprintf(m_rx_final_path, sizeof(m_rx_final_path), "%s/%s", FILM_DIR, filename);
        m_rx_tmp_fp = fopen(FILM_TMP_PATH, "wb");
        if(m_rx_tmp_fp == NULL)
        {
            /* 打开失败：统一终止清理，释放所有权、删除可能遗留的临时文件并复位为
             * IDLE，当前有效画面保持不变（Req 7.6）。 */
            rx_abort_and_cleanup();
            rx_unlock();
            sys_loge(FILE_TAG, "rx begin: open temp %s failed", FILM_TMP_PATH);
            return ESP_FAIL;
        }
        /* 初始化增量 SHA-256 摘要上下文（Req 12.1）。置 INIT 后即标记 active，
         * 使后续任一失败路径都能经 fs_rx_sha256_reset 正确 abort（abort 在置 INIT 之后、
         * 无论 setup 是否成功都安全）。psa_crypto_init 幂等，PSA 使用前需调用。 */
        (void)psa_crypto_init();
        m_rx_sha_ctx = (psa_hash_operation_t)PSA_HASH_OPERATION_INIT;
        m_rx_sha_active = true;
        if(psa_hash_setup(&m_rx_sha_ctx, PSA_ALG_SHA_256) != PSA_SUCCESS)
        {
            /* 摘要初始化失败：统一终止清理（含 psa_hash_abort），释放所有权、删除
             * 临时文件并复位 IDLE，当前有效画面不变（Req 7.6）。 */
            rx_abort_and_cleanup();
            rx_unlock();
            sys_loge(FILE_TAG, "rx begin: sha256 starts failed");
            return ESP_FAIL;
        }
    }
    rx_unlock();

    if(rc == FILE_TXN_OK)
    {
        sys_logi(FILE_TAG, "rx begin: src=%d name=%s size=%u -> owner granted, temp opened",
                 (int)source, filename ? filename : "(null)", (unsigned)expected_size);
    }
    else if(rc == FILE_TXN_ERR_BUSY)
    {
        sys_logw(FILE_TAG, "rx begin busy: src=%d rejected (owner=%d)",
                 (int)source, (int)m_rx_state.owner);
    }
    else
    {
        sys_logw(FILE_TAG, "rx begin invalid arg: src=%d size=%u",
                 (int)source, (unsigned)expected_size);
    }
    return file_txn_rc_to_esp(rc);
}

esp_err_t service_file_receive_write(const uint8_t *data, size_t length)
{
    /* 空指针防御；纯累计长度判定仍由 file_txn_write_advance 承担（Req 7.4）。 */
    if(data == NULL && length != 0)
    {
        return ESP_ERR_INVALID_ARG;
    }

    rx_lock();
    file_txn_rc_t rc = file_txn_write_advance(&m_rx_state, length);
    if(rc == FILE_TXN_OK)
    {
        /* 累计长度合法：将本块数据写入临时文件（Req 8.1）。 */
        if(length > 0)
        {
            if(m_rx_tmp_fp == NULL)
            {
                /* 事务处于 RECEIVING 却无临时句柄属不一致状态：统一终止清理
                 * （关句柄+删临时+释放所有权+复位IDLE，Req 7.6）。 */
                rx_abort_and_cleanup();
                rx_unlock();
                sys_loge(FILE_TAG, "rx write: temp handle missing, aborted");
                return ESP_FAIL;
            }
            size_t w = fwrite(data, 1, length, m_rx_tmp_fp);
            if(w != length)
            {
                /* 物理写失败：统一终止清理，关句柄、删临时文件、释放写所有权并复位
                 * IDLE（Req 7.6），当前有效画面不变。 */
                rx_abort_and_cleanup();
                rx_unlock();
                sys_loge(FILE_TAG, "rx write: fwrite %u/%u failed, aborted",
                         (unsigned)w, (unsigned)length);
                return ESP_FAIL;
            }
            /* 增量更新 SHA-256：仅对“本次实际写入的字节”update（Req 12.1）。
             * 位于 fwrite 成功之后，保证摘要覆盖的字节集与已落盘临时文件严格一致；累计
             * 超长的写入已在上游 file_txn_write_advance 被拒（不落盘、不到此处），因此摘要
             * 永不包含被拒字节。全程增量、绝不在结束阶段重新缓冲整文件（设计 D9）。 */
            if(psa_hash_update(&m_rx_sha_ctx, data, length) != PSA_SUCCESS)
            {
                /* 摘要更新失败：与物理写失败一致地统一终止清理（含 psa_hash_abort）。 */
                rx_abort_and_cleanup();
                rx_unlock();
                sys_loge(FILE_TAG, "rx write: sha256 update failed, aborted");
                return ESP_FAIL;
            }
        }
        m_rx_last_activity_tick = xTaskGetTickCount();   /* 刷新活动时间（M3 卡死回收） */
    }
    else if(rc == FILE_TXN_ERR_OVERFLOW)
    {
        /* 累计超长：file_txn_write_advance 已拒绝该写入（未落盘、written 不变），
         * 此处立即中止事务并统一清理——关句柄、删临时文件、释放写所有权并复位
         * IDLE（Req 7.4 拒绝该写入并中止事务；Req 7.6 清理），当前画面不变。 */
        rx_abort_and_cleanup();
    }
    rx_unlock();

    if(rc == FILE_TXN_ERR_OVERFLOW)
    {
        sys_logw(FILE_TAG, "rx write overflow: len=%u exceeds declared size, aborted",
                 (unsigned)length);
    }

    return file_txn_rc_to_esp(rc);
}

/* finish 阶段校验临时文件的 .film 文件头（Req 7.5）。
 *
 * 先 fflush + fsync 把写句柄的用户态缓冲与 FATFS 目录项/数据同步落盘，随后以只读方式
 * 打开同一临时文件读取其 32 字节文件头并做像素长度/宽高/颜色数校验（复用
 * fs_film_header_valid，与 film_validate 头字段检查一致）。工程配置
 * CONFIG_FATFS_FS_LOCK=0，允许同一文件被再次打开；fsync 已更新目录项与数据，故只读
 * 复核可见完整文件头。仅读文件头、不读像素，也绝不触碰 current.film。
 *
 * 语义：任何“句柄缺失 / 同步失败 / 头非法”都返回 false，由调用方走统一终止清理。
 * 调用方须已持有 m_rx_lock。
 *
 * @return true 头有效；false 同步失败或头非法。 */
static bool fs_rx_temp_header_valid(void)
{
    if(m_rx_tmp_fp == NULL)
    {
        sys_loge(FILE_TAG, "rx finish: temp handle missing for header check");
        return false;
    }

    /* 1) 用户态缓冲落入 VFS。 */
    if(fflush(m_rx_tmp_fp) != 0)
    {
        sys_loge(FILE_TAG, "rx finish: fflush temp failed");
        return false;
    }

    /* 2) 目录项与数据同步到介质，保证只读再打开可见完整文件头。 */
    int fd = fileno(m_rx_tmp_fp);
    if(fd < 0 || fsync(fd) != 0)
    {
        sys_loge(FILE_TAG, "rx finish: fsync temp failed");
        return false;
    }

    /* 3) 只读复核临时文件头字段（不动 current.film）。 */
    return fs_film_header_valid(FILM_TMP_PATH);
}

esp_err_t service_file_receive_finish(const uint8_t expected_sha256[32])
{
    rx_lock();

    /* (1) 仅允许在 FT_RECEIVING 下结束事务；其余状态一律拒绝且不做清理（既非事务终止
     * 失败，也不改变当前所有者/临时文件）。 */
    if(m_rx_state.state != FT_RECEIVING)
    {
        file_txn_state_t st = m_rx_state.state;
        rx_unlock();
        sys_logw(FILE_TAG, "rx finish: not in RECEIVING (state=%d) -> reject", (int)st);
        return ESP_ERR_INVALID_STATE;
    }

    /* 进入校验阶段：RECEIVING->VALIDATING（design 状态机；FT_VALIDATING 语义即
     * “finish 中正在校验”）。该状态同样被低功耗休眠互斥视为活动，校验/提交全程禁睡
     * （Req 16.2）。整个 finish 在 m_rx_lock 内原子完成——校验、状态转移与原子替换之间
     * 不释放锁，故 BLE/Wi-Fi 交错调用维持单写者语义。当前有效画面 current.film 在本阶段
     * 绝不被触碰，只有全部校验通过后的原子替换才会改变它（Req 7.7）。 */
    file_source_t owner = m_rx_state.owner;
    m_rx_state.state = FT_VALIDATING;

    /* (2) 长度校验：累计已写字节必须恰好等于声明长度（Req 7.5、6.2）。
     * file_txn_write_advance 已保证 written 不会“超过” expected_size；此处捕获收尾时
     * “不足/过短”的情况。任一失败即统一终止清理，current.film 不变。 */
    if(m_rx_state.written != m_rx_state.expected_size)
    {
        sys_loge(FILE_TAG, "rx finish: length mismatch written=%u expected=%u -> reject",
                 (unsigned)m_rx_state.written, (unsigned)m_rx_state.expected_size);
        rx_abort_and_cleanup();
        rx_unlock();
        return ESP_ERR_INVALID_SIZE;
    }

    /* (3) 文件头校验：把临时文件同步落盘后只读复核其 32 字节文件头（像素长度/宽高/
     * 颜色数），与 film_validate 头字段检查一致（Req 6.2/6.3、7.5）。 */
    if(!fs_rx_temp_header_valid())
    {
        sys_loge(FILE_TAG, "rx finish: temp .film header invalid -> reject");
        rx_abort_and_cleanup();
        rx_unlock();
        return ESP_ERR_INVALID_SIZE;
    }

    /* (4) SHA-256 校验：仅 Wi-Fi 路径（expected_sha256 != NULL）比对增量摘要终值；BLE
     * 路径（NULL）按设计跳过（finish 校验长度+文件头，Wi-Fi 额外校验 SHA-256，Req 7.5、
     * 12.2）。fs_rx_sha256_finish 直接取 write 期间增量累积的终值，绝不重新缓冲整文件
     * （设计 D9）。BLE 路径不 finalize 摘要，其上下文由后续提交/清理路径统一 free，不泄漏。 */
    if(expected_sha256 != NULL)
    {
        uint8_t actual[32];
        esp_err_t se = fs_rx_sha256_finish(actual);
        if(se != ESP_OK)
        {
            sys_loge(FILE_TAG, "rx finish: sha256 finalize failed (%d) -> reject", (int)se);
            rx_abort_and_cleanup();
            rx_unlock();
            return se;
        }
        if(memcmp(actual, expected_sha256, sizeof(actual)) != 0)
        {
            sys_loge(FILE_TAG, "rx finish: sha256 mismatch -> reject (wifi)");
            rx_abort_and_cleanup();
            rx_unlock();
            return ESP_ERR_INVALID_CRC;
        }
    }

    /* (5) 全部校验通过：原子替换（fsync(tmp)→rename(tmp,current)）。这是唯一改变
     * current.film 的时刻，且严格发生在长度/文件头/(Wi-Fi)SHA-256 全部通过之后
     * （Req 7.7、8.1）。提交失败改走统一终止清理，绝不遗留 VALIDATING 僵态或临时文件。 */
    esp_err_t ce = fs_commit_temp_to_final();
    if(ce != ESP_OK)
    {
        sys_loge(FILE_TAG, "rx finish: commit failed (%d) -> abort", (int)ce);
        rx_abort_and_cleanup();
        rx_unlock();
        return ce;
    }

    /* 提交成功：VALIDATING->COMMITTED->IDLE，释放写所有权，事务回到干净空闲态。临时
     * 句柄/目标路径/增量 SHA-256 上下文已在 fs_commit_temp_to_final 内清理。 */
    m_rx_state.state = FT_COMMITTED;
    file_txn_reset(&m_rx_state);
    rx_unlock();

    sys_logi(FILE_TAG, "rx finish: committed (src=%d, %s)",
             (int)owner, expected_sha256 ? "wifi/sha-verified" : "ble/no-sha");
    return ESP_OK;
}

esp_err_t service_file_receive_abort(void)
{
    rx_lock();
    /* 统一终止清理（Req 7.6）：关闭临时文件句柄、删除 current.film.tmp、
     * 释放写所有权并复位为 IDLE。显式 abort 与 BLE 断连（经此接口，Req 9.5）、
     * 写失败/超长走同一条干净终止路径；当前有效画面保持不变。 */
    rx_abort_and_cleanup();
    rx_unlock();

    sys_logi(FILE_TAG, "rx abort: ownership released, temp cleaned");
    return ESP_OK;
}

file_receive_state_t service_file_get_receive_state(void)
{
    file_receive_state_t snapshot;
    rx_lock();
    snapshot = m_rx_state;
    rx_unlock();
    return snapshot;
}

void service_file_set_silent_receive(uint8_t enable)
{
    m_file_state.silent_receive = enable ? 1u : 0u;
    sys_logi(FILE_TAG, "silent receive: %s", m_file_state.silent_receive ? "ON" : "OFF");
}

uint8_t service_file_get_silent_receive(void)
{
    return m_file_state.silent_receive;
}

void service_file_display_received(const char *filename)
{
    /* 传输适配器（BLE）在 service_file_receive_finish() 成功提交后调用（* Req 5.3）。把文件名投递到文件任务，由 MSG_FILE_DISPLAY_RECEIVED 处理器在文件
     * 任务上下文内“刷新文件列表 → 按名定位索引 → service_film_display(index)”，复用
     * 既有显示路径（→ hal_epd_display_film）。本接口不自实现保存/校验/上屏。 */
    if(filename == NULL || filename[0] == '\0')
    {
        sys_logw(FILE_TAG, "display received: empty filename ignored");
        return;
    }

    /* 静默接收模式：只保存并刷新文件列表，不上屏。
     *
     * 电子纸刷一次约 20 秒，期间设备忙于送屏。连发多张时若每张 FILE_STOP 都触发一次
     * 刷屏，前 N-1 次刷屏纯属浪费，而且刷屏期间对端极易因 GATT 超时而断连
     * （实测「连发整套」必然失败，单张却正常，正是这个原因）。
     * 发送方在传前面几张时开启静默、传最后一张前关闭，即可做到「只刷最后一张」。 */
    if(m_file_state.silent_receive)
    {
        sys_logi(FILE_TAG, "silent receive: saved %s, skip display", filename);
        service_file_refresh_list();
        return;
    }

    file_msg_t msg = {0};
    msg.ID = MSG_FILE_DISPLAY_RECEIVED;
    size_t name_sz = strlen(filename) + 1;
    msg.pdata = (uint8_t*)pvPortMalloc(name_sz);
    if(msg.pdata == NULL)
    {
        sys_loge(FILE_TAG, "display received: alloc failed for %s", filename);
        return;
    }
    memcpy(msg.pdata, filename, name_sz);
    file_msg_send(&msg, 0);
}

int service_file_display_if_stale_async(void)
{
    file_msg_t msg = {0};
    msg.ID = MSG_FILE_DISPLAY_IF_STALE;
    file_msg_send(&msg, 0);
    return 0;
}

int service_file_display_for_now_async(void)
{
    /* 只投消息，真正的「刷新列表 + 按时段选图」在文件任务内串行完成
     * （见 MSG_FILE_DISPLAY_FOR_NOW 处理器里关于竞态的说明）。
     * 静默接收模式对本接口【不适用】：它的语义是「整套已经下完，现在该上屏了」，
     * 正是静默期结束的那一下。 */
    file_msg_t msg = {0};
    msg.ID = MSG_FILE_DISPLAY_FOR_NOW;
    file_msg_send(&msg, 0);
    return 0;
}
