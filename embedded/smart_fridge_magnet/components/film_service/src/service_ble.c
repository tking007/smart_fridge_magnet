/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/src/service_ble.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/5
 * Description: BLE GATT 服务。
 *   仅作传输适配层：入站命令经 ble_cmd_parse() 校验长度与校验和后分发，
 *   文件传输统一走 service_file 的单写者事务，不自行保存/校验/上屏。
 *   UUID、opcode 与 FILE_* 时序沿用上游 FrameFilm，协议见 docs/ble_protocol.md。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stdio.h>
#include <inttypes.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_system.h"

#include "sys_log.h"

#include "service_ble_gatts.h"
#include "service_ble.h"
#include "service_ble_parser.h"
#include "service_file.h"
#include "service_film.h"
#include "service_ota.h"
#include "service_param.h"
#include "factory_reset.h"
#include "service_wifi.h"
#include "poll_interval.h"     /* F3 0x4A: monitor_clamp_poll_interval —— 写入前钳制 */
#include "wake_schedule.h"     /* F3 0x48/0x49: 唤醒点合法性判据（与装载路径共用） */
#include "film_pin.h"          /* 0x4C/0x4D: 置顶自定义画面的配置结构与合法性判据 */

#include "hal_bat.h"
#include "hal_sd.h"

/*********************************************************************
 * MACROS
 */


/*********************************************************************
 * TYPEDEFS
 */
#define BEL_SERVICE_TAG            "ble_service"

#define BLE_MSG_QUEUE_LENGTH       50
#define BLE_MSG_QUEUE_ITEM_SIZE    sizeof( ble_msg_t )

/* FILE-transfer states (BLE_FILM_TRANS_IDLE .. BLE_FILM_TRANS_STOPPED) are
 * defined as ble_film_trans_state_t in service_ble_parser.h so the pure
 * ordering validator and this file share one source of truth. */

#define BLE_OTA_TRANS_IDLE        0
#define BLE_OTA_TRANS_STARTED     1
#define BLE_OTA_TRANS_RECV_LEN    2
#define BLE_OTA_TRANS_RECV_DATA   3
#define BLE_OTA_TRANS_STOPPED     4

/* FILE_STOP transfer result codes carried in the Notify payload (Req 9.3). */
#define BLE_FILE_STOP_RESULT_OK    (0x00)  /* declared length == received length */
#define BLE_FILE_STOP_RESULT_FAIL  (0x01)  /* size mismatch / incomplete transfer */

/* Scratch buffer for small standard responses. The largest standard response
 * frame built here carries 2 data bytes (=> 6-byte frame); 8 leaves headroom.
 * ble_cmd_build_frame() enforces the real bound, so this is only a size hint. */
#define BLE_RESP_SMALL_BUF        (8)


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */
static TaskHandle_t m_ble_task_hdl = NULL;
static QueueHandle_t m_ble_msg_hdl = NULL;
static ble_film_trans_state_t m_film_trans_state = BLE_FILM_TRANS_IDLE;
static uint8_t m_film_trans_filename[256];
static uint32_t m_film_trans_file_size = 0;
static uint32_t m_film_trans_received = 0;
static uint8_t m_ota_trans_state = BLE_OTA_TRANS_IDLE;
static uint32_t m_ota_trans_file_size = 0;
static uint32_t m_ota_trans_received = 0;


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void ble_task_handle(void *pvParameters);
static uint8_t ble_checksum(uint8_t arr[], int len);
static void ble_cmd_process(ble_cmd_t *cmd);
static void ble_send_std_response(uint8_t ch, const uint8_t *data, uint8_t data_len);
static void ble_send_str_response(uint8_t ch, const char *str);
static bool ble_copy_cstr(char *dst, size_t dst_cap, const uint8_t *data, uint8_t len);
static void ble_secure_zero(void *p, size_t n);
static void ble_send_status_query(void);
static void ble_file_notify_result(bool ok);
static void ble_film_trans_reset(void);
static void ble_film_trans_abort_on_disconnect(void);
static void service_ble_on_gatts_disconnect(void);

/*********************************************************************
 * GLOBAL FUNCTIONS
 */



/**
 * [service_ble_init 初始化ble服务]
 */
void service_ble_init(void)
{
    // 初始化ble服务
    service_ble_gatt_server_init();
    service_ble_gatts_cmd_register_cb(service_ble_msg_gatts_cmd_send);
    /* Abort any in-progress BLE file transfer when the peer disconnects
     * (Req 9.5). The GATTS callback only enqueues a message; the abort itself
     * runs in the BLE task so it cannot race with ble_cmd_process(). */
    service_ble_gatts_disconnect_register_cb(service_ble_on_gatts_disconnect);

    /* 按需广播只在省电模式下启用：
     *  - sleep_mode=1（省电）：断连后不自动恢复广播，由可连接窗口决定何时可被扫描，
     *    平时静默既省射频也缩小暴露面；
     *  - sleep_mode=0（调试）：设备本就常驻不睡，保持上游「断连即恢复广播」的行为，
     *    随时可连对调试更重要。
     * 运行时用 BLE 0x25 改休眠开关后，本策略在下次启动生效（避免在连接中途改变可见性）。 */
    service_ble_gatts_set_adv_auto_restart(g_service_param.sleep.sleep_mode ? false : true);

    if(m_ble_task_hdl == NULL)
    {
        if ( pdPASS != xTaskCreate( ble_task_handle, SYS_OS_NAME_BLE_TASK, SYS_OS_SIZE_BLE_TASK, NULL, SYS_OS_PRI_BLE_TASK, &m_ble_task_hdl ))
        {
            sys_loge(BEL_SERVICE_TAG, "ble task create error!");
        }
    }
}

/**
 * [service_ble_msg_send ble事件msg发送]
 * @param p_msg  [msg]
 * @param in_isr [is in interrupt]
 */
void service_ble_msg_send(void *p_msg, bool in_isr)
{
    /* The queue could not be created. */
    if(m_ble_msg_hdl != NULL)
    {
        if(in_isr == 0)
        {
            SYS_ERROR_CHECK((xQueueSend(m_ble_msg_hdl, p_msg, portMAX_DELAY) != pdPASS));
        }
        else /* Is In interrupt.*/
        {
            BaseType_t xHigherPriorityTaskWoken;
            /* No tasks have yet been unblocked. */
            xHigherPriorityTaskWoken = pdFALSE;

            /* Write the byte to the queue. xHigherPriorityTaskWoken will get set to
            pdTRUE if writing to the queue causes a task to leave the Blocked state,
            and the task leaving the Blocked state has a priority higher than the
            currently executing task (the task that was interrupted). */
            xQueueSendFromISR( m_ble_msg_hdl, p_msg, &xHigherPriorityTaskWoken );
            /* Now the buffer is empty, and the interrupt source has been cleared, a context
            switch should be performed if xHigherPriorityTaskWoken is equal to pdTRUE.
            NOTE: The syntax required to perform a context switch from an ISR varies from
            port to port, and from compiler to compiler. Check the web documentation and
            examples for the port being used to find the syntax required for your
            application. */
            portYIELD_FROM_ISR( xHigherPriorityTaskWoken );
        }
    }
}

/**
 * [ble_msg_copy_enqueue GATTS 回调侧「拷贝一份并入队」的公共实现]
 *
 * 所有入队都收口到这里，因为连接间隔为 7.5ms 时两类失败都会被显著放大：
 *  1) 队列（50 项）满导致 xQueueSendFromISR 失败。若不检查返回值，pdata 已经
 *     pvPortMalloc 过，既泄漏内存又静默丢帧；丢一帧 FILE_DATA 的后果是 film 数据缺口、
 *     校验失败、屏幕保持旧画面。
 *  2) pvPortMalloc 失败。若仍然入队（pdata=NULL 而 len 非 0），下游 ble_cmd_parse() 会
 *     挡住 NULL 不至于崩溃，但同样是一次无人知晓的丢帧。
 *
 * 两种失败都不入队、都释放已分配内存、并累计到 m_ble_msg_drop_cnt。计数由 BLE 任务在
 * 任务上下文打印（回调里打日志在 ISR 语境下不安全）。真机串口出现 "ble msg dropped"
 * 即说明速率已超出下游写盘能力，应回调连接间隔。
 *
 * @param id     [消息 ID]
 * @param p_data [数据 buf]
 * @param len    [长度]
 */
static volatile uint32_t m_ble_msg_drop_cnt = 0;

static void ble_msg_copy_enqueue( uint8_t id, uint8_t const *p_data, uint16_t len )
{
    BaseType_t pxHigherPriorityTaskWoken = pdFALSE;
    ble_msg_t  msg = {0};

    msg.ID    = id;
    msg.pdata = pvPortMalloc(len);

    if(msg.pdata == NULL)
    {
        /* 分配失败：直接计一次丢弃，不入队（避免下游拿到 pdata=NULL 的空消息）。 */
        m_ble_msg_drop_cnt++;
        return;
    }

    memcpy(msg.pdata, p_data, len);
    msg.len = len;

    if(xQueueSendFromISR( m_ble_msg_hdl, &msg, &pxHigherPriorityTaskWoken ) != pdPASS)
    {
        /* 队列满：必须自己释放，否则这块 buf 再无人回收。 */
        vPortFree(msg.pdata);
        m_ble_msg_drop_cnt++;
        return;
    }

    portYIELD_FROM_ISR( pxHigherPriorityTaskWoken );
}

/**
 * [service_ble_msg_gatts_cmd_send 发送命令获取msg]
 * @param p_data [命令buf]
 * @param len    [长度]
 */
void service_ble_msg_gatts_cmd_send( uint8_t const *p_data, uint16_t len )
{
    /* The queue could not be created. */
    if(m_ble_msg_hdl != NULL)
    {
        ble_msg_copy_enqueue(MSG_BLE_CH1_IN_CMD, p_data, len);
    }
}

/**
 * [service_ble_msg_gatts_data_send 发送数据msg]
 * @param p_data [命令buf]
 * @param len    [长度]
 * @param ch     [val MSG_BLE_CH1_OUT_DATA MSG_BLE_CH2_OUT_DATA MSG_BLE_CH3_OUT_DATA]
 */
void service_ble_msg_gatts_data_send( uint8_t const *p_data, uint16_t len, uint8_t ch)
{
    /* The queue could not be created. */
    if(m_ble_msg_hdl != NULL)
    {
        ble_msg_copy_enqueue(ch, p_data, len);
    }
}

static void ble_task_handle(void *pvParameters)
{
    m_ble_msg_hdl = xQueueCreate( BLE_MSG_QUEUE_LENGTH, BLE_MSG_QUEUE_ITEM_SIZE );
    SYS_ERROR_CHECK(m_ble_msg_hdl == NULL);

    /* 已上报过的丢帧数。只在数值变化时打一行，避免连续丢帧时日志把串口淹掉
     * （日志本身也会占用时间，反而加剧丢帧）。 */
    uint32_t reported_drop = 0;

    for(;;)
    {
        ble_msg_t msg;
        SYS_ERROR_CHECK( xQueueReceive( m_ble_msg_hdl, (void *const)&msg, portMAX_DELAY ) != pdPASS );

        /* 在任务上下文（而非 GATTS 回调里）上报丢帧，日志调用才是安全的。
         * 串口一旦出现这行，说明 BLE 到达速率已超过下游写盘能力，需要把
         * BLE_DEFAULT_MIN_INT/MAX_INT 调回更保守的值。 */
        if(m_ble_msg_drop_cnt != reported_drop)
        {
            reported_drop = m_ble_msg_drop_cnt;
            sys_loge(BEL_SERVICE_TAG, "ble msg dropped (queue full or oom), total = %" PRIu32, reported_drop);
        }

        switch(msg.ID)
        {
        case MSG_BLE_CH1_IN_CMD :
        {
            if( msg.len )
            {
                /* Validate-before-access (Req 9.1): min length, protocol
                 * length field and checksum are all checked inside
                 * ble_cmd_parse() before any header/channel/length field is
                 * used. Never reads out of bounds, never panics on a short or
                 * malformed frame. */
                ble_parsed_cmd_t parsed = {0};
                ble_parse_err_t  perr = ble_cmd_parse(msg.pdata, msg.len, &parsed);

                if(perr == BLE_PARSE_OK)
                {
                    ble_cmd_t cmd = {0};
                    cmd.ch    = parsed.ch;
                    cmd.len   = parsed.len;
                    cmd.pdata = (uint8_t *)parsed.data; /* in-bounds view */
                    ble_cmd_process(&cmd);
                }
                else
                {
                    sys_logw(BEL_SERVICE_TAG,
                             "Rejected malformed BLE cmd: err=%d, len=%u",
                             (int)perr, (unsigned)msg.len);
                }
                vPortFree(msg.pdata);
            }
            break;
        }
        case MSG_BLE_CH1_OUT_DATA :
        {
            if( msg.len )
            {
                service_ble_send_notify_data(BLE_NOTIFY_SEND_CH1, msg.pdata, msg.len);
                vPortFree(msg.pdata);
            }
            break;
        }
        case MSG_BLE_CH2_OUT_DATA :
        {
            if( msg.len )
            {
                service_ble_send_notify_data(BLE_NOTIFY_SEND_CH2, msg.pdata, msg.len);
                vPortFree(msg.pdata);
            }
            break;
        }
        case MSG_BLE_CH3_OUT_DATA :
        {
            if( msg.len )
            {
                service_ble_send_notify_data(BLE_NOTIFY_SEND_CH3, msg.pdata, msg.len);
                vPortFree(msg.pdata);
            }
            break;
        }
        case MSG_BLE_GAP_DISCONNECT:
            service_ble_gatts_dev_disconnect();
            break;
        case MSG_BLE_GAP_PEER_DISCONNECTED:
            /* Peer link dropped: abort any unfinished file transfer so temp
             * files / write ownership are released and the transfer state
             * machine is reset (Req 9.5). Current picture is preserved. */
            ble_film_trans_abort_on_disconnect();
            break;
        default :
        {
            if( msg.len )
            {
                vPortFree(msg.pdata);
            }
            break;
        }
        }
    }
}

static void ble_cmd_process(ble_cmd_t *cmd)
{
    if(cmd == NULL)
    {
        sys_logw(BEL_SERVICE_TAG, "cmd is NULL");
        return;
    }

    switch(cmd->ch)
    {
        case BLE_FILM_TRANS_CH_FILE_START :
        {
            ble_film_trans_state_t next;
            if(ble_film_trans_step(m_film_trans_state, cmd->ch, cmd->len, &next) == BLE_SEQ_ACCEPT)
            {
                m_film_trans_state = next;
                m_film_trans_received = 0;
                memset(m_film_trans_filename, 0, sizeof(m_film_trans_filename));
                m_film_trans_file_size = 0;
                sys_logi(BEL_SERVICE_TAG, "Film transfer started");
            }
            else
            {
                /* Out-of-order FILE_START: reject, keep transfer state (Req 9.2). */
                sys_logw(BEL_SERVICE_TAG, "Rejected FILE_START (out-of-order), state=%d", m_film_trans_state);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_NAME :
        {
            ble_film_trans_state_t next;
            /* Ordering + data-length checked by ble_film_trans_step; the
             * filename bytes are additionally validated for path-traversal /
             * separators / non-printables before they are ever copied. */
            if(ble_film_trans_step(m_film_trans_state, cmd->ch, cmd->len, &next) == BLE_SEQ_ACCEPT
               && ble_filename_is_safe(cmd->pdata, cmd->len))
            {
                memcpy(m_film_trans_filename, cmd->pdata, cmd->len);
                m_film_trans_filename[cmd->len] = '\0';
                m_film_trans_state = next;
                sys_logi(BEL_SERVICE_TAG, "Received filename: %s", m_film_trans_filename);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected FILE_NAME (out-of-order/unsafe): state=%d, len=%d", m_film_trans_state, cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_LEN :
        {
            ble_film_trans_state_t next;
            /* ACCEPT guarantees cmd->len == 4, so pdata[0..3] are in bounds. */
            if(ble_film_trans_step(m_film_trans_state, cmd->ch, cmd->len, &next) == BLE_SEQ_ACCEPT)
            {
                m_film_trans_file_size = (cmd->pdata[0] << 24) | (cmd->pdata[1] << 16) | (cmd->pdata[2] << 8) | cmd->pdata[3];
                sys_logi(BEL_SERVICE_TAG, "Received file size: %d", m_film_trans_file_size);

                /* BLE 作为传输适配器进入统一文件事务（Req 5.4）。filename 已在
                 * FILE_NAME 阶段经 ble_filename_is_safe 校验；此处以声明长度 begin。事务内部
                 * 再强制单写者、安全基础名与固定长度(209120)校验，BLE 不自实现保存/校验。 */
                esp_err_t rerr = service_file_receive_begin(FILE_SRC_BLE,
                                                            (const char*)m_film_trans_filename,
                                                            m_film_trans_file_size);
                if(rerr == ESP_OK)
                {
                    m_film_trans_received = 0;
                    m_film_trans_state = next;
                }
                else
                {
                    /* busy（另一来源持有事务）或参数非法（长度!=209120/文件名不安全）：
                     * Notify 传输失败并复位 BLE 传图状态。当前画面不变。 */
                    sys_logw(BEL_SERVICE_TAG,
                             "FILE_LEN begin rejected (err=0x%x, size=%u) -> notify fail",
                             (unsigned)rerr, (unsigned)m_film_trans_file_size);
                    ble_file_notify_result(false);
                    ble_film_trans_reset();
                }
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected FILE_LEN (out-of-order): state=%d, len=%d", m_film_trans_state, cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_DATA :
        {
            ble_film_trans_state_t next;
            if(ble_film_trans_step(m_film_trans_state, cmd->ch, cmd->len, &next) == BLE_SEQ_ACCEPT)
            {
                /* 直接写入统一文件事务（Req 5.4）。cmd->pdata 是解析器给出的
                 * 越界安全视图，且 service_file_receive_write 为同步落盘（内部拷贝），
                 * 无需再 malloc 副本，BLE 不自实现保存。 */
                esp_err_t rerr = service_file_receive_write(cmd->pdata, cmd->len);
                if(rerr == ESP_OK)
                {
                    m_film_trans_received += cmd->len;
                    m_film_trans_state = next;
                }
                else
                {
                    /* 超长/写失败：事务已在内部自我中止（清临时+释放所有权）。BLE 侧再显式
                     * abort（对空闲事务为安全 no-op）、Notify 失败并复位传图状态（* Req 5.4）。当前有效画面保持不变。 */
                    sys_logw(BEL_SERVICE_TAG,
                             "FILE_DATA write rejected (err=0x%x, len=%d) -> abort + notify fail",
                             (unsigned)rerr, cmd->len);
                    service_file_receive_abort();
                    ble_file_notify_result(false);
                    ble_film_trans_reset();
                }
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected FILE_DATA (out-of-order): state=%d, len=%d", m_film_trans_state, cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_STOP :
        {
            ble_film_trans_state_t next;
            if(ble_film_trans_step(m_film_trans_state, cmd->ch, cmd->len, &next) == BLE_SEQ_ACCEPT)
            {
                /* 结束统一文件事务。BLE 路径按设计传 NULL SHA：finish 只校验
                 * 累计长度与 .film 文件头，SHA-256 比对仅用于 Wi-Fi 路径。finish
                 * 内部在全部校验通过后 fsync→rename 原子替换目标文件；任一失败则内部已自我
                 * 中止清理，current.film 绝不被覆盖（Req 5.4、7.7）。 */
                esp_err_t ferr = service_file_receive_finish(NULL);
                bool ok = (ferr == ESP_OK);

                sys_logi(BEL_SERVICE_TAG,
                         "Film transfer stopped, received: %u/%u bytes, finish=0x%x -> %s",
                         (unsigned)m_film_trans_received,
                         (unsigned)m_film_trans_file_size,
                         (unsigned)ferr,
                         ok ? "OK" : "FAIL");

                if(ok)
                {
                    /* 提交成功：复用既有显示路径（刷新文件列表 → service_film_display →
                     * hal_epd_display_film）显示新接收的图片，不在此重实现上屏（Req 5.3）。 */
                    service_file_display_received((const char*)m_film_trans_filename);
                }

                /* FILE_STOP 结果 Notify（Req 9.3）：保留 的成功/失败通知行为，
                 * 现由统一事务 finish 结果驱动。 */
                ble_file_notify_result(ok);

                m_film_trans_state = next;
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected FILE_STOP (out-of-order), state=%d", m_film_trans_state);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_DELETE : // 删除文件
        {
            if(cmd->len == 1)
            {
                uint8_t file_id = cmd->pdata[0];
                sys_logi(BEL_SERVICE_TAG, "Delete file id: %d", file_id);
                service_file_delete(file_id);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_LIST : // 查询文件列表
        {
            uint32_t file_count = service_file_get_count();
            sys_logi(BEL_SERVICE_TAG, "File list count: %d", file_count);

            uint8_t *cmd_buf = pvPortMalloc(150);
            if(cmd_buf == NULL) break;

            for(uint32_t i = 0; i < file_count; i++)
            {
                memset(cmd_buf, 0, 150);

                const char *filename = service_file_get_filename(i);
                if(filename == NULL)
                {
                    continue;
                }
                uint8_t name_len = strlen(filename) + 1;

                /* Bounds guard (Req 9.4): the frame is
                 * HEAD+CH+LEN + (id + name_len prefix + name) + CHECKSUM ==
                 * 6 + name_len bytes. Skip pathologically long names instead of
                 * overrunning the 150-byte buffer or emitting a truncated frame
                 * whose declared length would not match the payload. */
                if((uint32_t)name_len + 6u > 150u)
                {
                    sys_logw(BEL_SERVICE_TAG, "Skip over-long filename in list (name_len=%u)", (unsigned)name_len);
                    continue;
                }

                cmd_buf[0] = BLE_CMD_HEAD;
                cmd_buf[1] = BLE_FILM_TRANS_CH_FILE_LIST;
                cmd_buf[2] = 2 + name_len;
                cmd_buf[3] = i & 0xff;
                cmd_buf[4] = name_len;

                memcpy(&cmd_buf[5], filename, name_len);

                uint8_t checksum = ble_checksum(cmd_buf, 5 + name_len);
                cmd_buf[5 + name_len] = checksum;

                /* Notify EXACTLY the payload length (6 + name_len). */
                service_ble_send_notify_data(BLE_NOTIFY_SEND_CH1, cmd_buf, 6 + name_len);

                vTaskDelay(50 / portTICK_PERIOD_MS);
            }

            /* 显式结束帧：[0xFF][0x00]。0xFF 不可能是有效文件 id，name_len=0 也不可能
             * 是有效条目；App 因而能区分“空列表”与“设备完全没有响应”。 */
            memset(cmd_buf, 0, 150);
            cmd_buf[0] = BLE_CMD_HEAD;
            cmd_buf[1] = BLE_FILM_TRANS_CH_FILE_LIST;
            cmd_buf[2] = 2;
            cmd_buf[3] = 0xFF;
            cmd_buf[4] = 0;
            cmd_buf[5] = ble_checksum(cmd_buf, 5);
            service_ble_send_notify_data(BLE_NOTIFY_SEND_CH1, cmd_buf, 6);

            vPortFree(cmd_buf);
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_DISPLAY : // 显示id对应的文件
        {
            if(cmd->len == 1)
            {
                uint8_t file_id = cmd->pdata[0];
                sys_logi(BEL_SERVICE_TAG, "Display file id: %d", file_id);
                service_film_display(file_id);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_DISPLAY_GET : // 查询当前显示的文件id
        {
            uint32_t current_id = service_film_get_current_id();
            sys_logi(BEL_SERVICE_TAG, "Current display file id: %d", current_id);

            uint8_t data = (uint8_t)(current_id & 0xFF);
            ble_send_std_response(BLE_FILM_TRANS_CH_FILE_DISPLAY_GET, &data, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_OTA_LEN :
        {
            if(m_ota_trans_state == BLE_OTA_TRANS_IDLE && cmd->len == 4)
            {
                m_ota_trans_file_size = (cmd->pdata[0] << 24) | (cmd->pdata[1] << 16) | (cmd->pdata[2] << 8) | cmd->pdata[3];
                m_ota_trans_received = 0;
                m_ota_trans_state = BLE_OTA_TRANS_RECV_LEN;
                sys_logi(BEL_SERVICE_TAG, "OTA file size: %d bytes", m_ota_trans_file_size);
                service_ota_start();
                service_set_length(m_ota_trans_file_size);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Invalid state or length for OTA_LEN: state=%d, len=%d", m_ota_trans_state, cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_OTA_DATA :
        {
            if((m_ota_trans_state == BLE_OTA_TRANS_RECV_LEN || m_ota_trans_state == BLE_OTA_TRANS_RECV_DATA) && cmd->len > 0)
            {
                service_ota_write(cmd->pdata, cmd->len);
                m_ota_trans_received += cmd->len;
                m_ota_trans_state = BLE_OTA_TRANS_RECV_DATA;
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Invalid state or length for OTA_DATA: state=%d, len=%d", m_ota_trans_state, cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_OTA_START :
        {
            if(m_ota_trans_state == BLE_OTA_TRANS_IDLE)
            {
                m_ota_trans_state = BLE_OTA_TRANS_STARTED;
                m_ota_trans_received = 0;
                service_ota_start();
                sys_logi(BEL_SERVICE_TAG, "OTA started (without length)");
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Invalid state for OTA_START: %d", m_ota_trans_state);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_OTA_STOP :
        {
            if(m_ota_trans_state == BLE_OTA_TRANS_RECV_DATA || m_ota_trans_state == BLE_OTA_TRANS_RECV_LEN)
            {
                service_ota_stop();
                sys_logi(BEL_SERVICE_TAG, "OTA stopped, received: %d/%d bytes", m_ota_trans_received, m_ota_trans_file_size);
                m_ota_trans_state = BLE_OTA_TRANS_STOPPED;
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Invalid state for OTA_STOP: %d", m_ota_trans_state);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_MODE : // Film模式切换
        {
            if(cmd->len == 1 && (cmd->pdata[0] == 0 || cmd->pdata[0] == 1 || cmd->pdata[0] == 2))
            {
                uint8_t mode = cmd->pdata[0];
                sys_logi(BEL_SERVICE_TAG, "Set play mode: %d", mode);
                g_service_param.film.play_mode = mode;
                service_param_save();
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_MODE_GET : // Film模式查询
        {
            sys_logi(BEL_SERVICE_TAG, "Current play mode: %d", g_service_param.film.play_mode);
            uint8_t data = (uint8_t)(g_service_param.film.play_mode & 0xFF);
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_MODE_GET, &data, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_RESET : // 重置（恢复出厂：仅清参数）
        {
            /* Factory reset = PARAMS-ONLY clear (Req 19.3): wipe the network
             * provisioning block, at-rest secret ciphertext and sync state,
             * reset poll_interval to the default and restamp config_version,
             * while PRESERVING factory_flag / film.* / sleep.* (display choice
             * and sleep preference are kept; the user storage .film files are
             * never touched). This replaces the previous service_param_reset()
             * which defaulted EVERYTHING (dropping film/sleep preferences too).
             *
             * NOTE (Req 19.4, 结论 3 — deferred / out-of-scope for this
             * version): the "additionally FORMAT user storage after a SEPARATE
             * explicit confirmation" escalation
             * (factory_reset_decide_scope() / factory_reset_format_confirmed(),
             * FACTORY_RESET_SCOPE_PARAMS_AND_STORAGE) is intentionally NOT wired
             * to any BLE opcode here. The safe default — clear parameters only,
             * never format user storage — is the ONLY reachable behavior over
             * BLE, so no destructive user-storage wipe can be triggered
             * remotely. (The pre-existing upstream SDRESET opcode formats the
             * SD card and is a separate, unchanged behavior.) */
            sys_logi(BEL_SERVICE_TAG, "Factory reset (params only)...");
            factory_reset_apply_params(&g_service_param);
            service_param_save();
            /* 置顶自定义画面（pin）存在**独立的 NVS key** 下，factory_reset_apply_params()
             * 是纯函数、只动内存里的参数结构，service_param_save() 也只写 PARAMS blob，
             * 两者都碰不到它 —— 必须显式擦除。否则恢复出厂后设备还挂着上一位用户的照片，
             * 而用户以为已经清干净了。用户存储上的 .film 文件仍然不动（Req 19.3 的既有
             * 语义不变），擦掉的只是「优先显示哪一张」这个偏好。 */
            service_param_pinned_erase();
            service_ble_gatts_clear_bonds();
            vTaskDelay(100 / portTICK_PERIOD_MS);
            sys_reboot();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_PWRREAD : // 读取电池电压
        {
            uint8_t percent = hal_bat_get_percent();
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_PWRREAD, &percent, 1);
            sys_logi(BEL_SERVICE_TAG, "Battery level: %d%%", percent);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_REBOOT : // 重启
        {
            sys_logi(BEL_SERVICE_TAG, "Rebooting...");
            sys_reboot();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF : // 休眠模式开关
        {
            if(cmd->len == 1 && (cmd->pdata[0] == 0 || cmd->pdata[0] == 1))
            {
                uint8_t mode = cmd->pdata[0];
                sys_logi(BEL_SERVICE_TAG, "Set sleep mode: %s", mode ? "ON" : "OFF");
                g_service_param.sleep.sleep_mode = mode;
                service_param_save();
            }
            break;
        }         
        case BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF_GET : // 休眠模式开关查询
        {
            sys_logi(BEL_SERVICE_TAG, "Sleep mode: %s", g_service_param.sleep.sleep_mode ? "ON" : "OFF");
            uint8_t data = (uint8_t)(g_service_param.sleep.sleep_mode & 0xFF);
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF_GET, &data, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_SLEEPMODE : // 定时唤醒开关
        {
            if(cmd->len == 1 && (cmd->pdata[0] == 0 || cmd->pdata[0] == 1))
            {
                uint8_t auto_wake = cmd->pdata[0];
                sys_logi(BEL_SERVICE_TAG, "Set auto wake: %s", auto_wake ? "ON" : "OFF");
                g_service_param.sleep.sleep_auto = auto_wake;
                service_param_save();
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_GET : // 定时唤醒开关查询
        {
            sys_logi(BEL_SERVICE_TAG, "Auto wake: %s", g_service_param.sleep.sleep_auto ? "ON" : "OFF");
            uint8_t data = (uint8_t)(g_service_param.sleep.sleep_auto & 0xFF);
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_GET, &data, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_TIME : // 定时唤醒时间（单位分钟）
        {
            if(cmd->len == 2)
            {
                uint16_t time_min = (cmd->pdata[0] << 8) | cmd->pdata[1];
                if(time_min >= 10 && time_min <= 2880)
                {
                    sys_logi(BEL_SERVICE_TAG, "Set sleep wake time: %d min", time_min);
                    g_service_param.sleep.sleep_time = time_min;
                    service_param_save();
                }
            }
            break;
        }   
        case BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_TIME_GET : // 定时唤醒时间查询（单位分钟）
        {
            sys_logi(BEL_SERVICE_TAG, "Sleep wake time: %d min", g_service_param.sleep.sleep_time);
            uint8_t data[2];
            data[0] = (uint8_t)((g_service_param.sleep.sleep_time >> 8) & 0xFF);
            data[1] = (uint8_t)(g_service_param.sleep.sleep_time & 0xFF);
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_SLEEPMODE_TIME_GET, data, 2);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_SDRESET : // SD卡格式化
        {
            /* Formatting is destructive and the BLE link is not a sufficient
             * confirmation. Keep the legacy opcode for compatibility, but make
             * it a safe no-op; local maintenance tools may format the card. */
            sys_logw(BEL_SERVICE_TAG, "SD card format rejected: local maintenance required");
            uint8_t data = 1;
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_SDRESET, &data, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE : // WiFi开关设置
        {
            if(cmd->len == 1 && (cmd->pdata[0] == 0 || cmd->pdata[0] == 1))
            {
                uint8_t enable = cmd->pdata[0];
                sys_logi(BEL_SERVICE_TAG, "Set WiFi enable: %s", enable ? "ON" : "OFF");
                g_service_param.network.wifi_enable = enable;
                service_param_save();
                if(enable)
                {
                    service_wifi_init();
                }
                else
                {
                    service_wifi_disconnect();
                    service_wifi_deinit();
                }
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE_GET : // WiFi开关查询
        {
            sys_logi(BEL_SERVICE_TAG, "WiFi enable: %s", g_service_param.network.wifi_enable ? "ON" : "OFF");
            uint8_t data = (uint8_t)(g_service_param.network.wifi_enable & 0xFF);
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE_GET, &data, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_SSID : // WiFi SSID设置（配网）
        {
            /* 规范化：经参数服务 setter 持久化（Req 15.1）。SSID 为非机密，可记日志。
             * 载荷非 NUL 结尾，先落成有界 C 字符串再交 setter（避免 strncpy 越读）。 */
            char ssid[SFM_WIFI_SSID_MAX];
            if(cmd->len <= SFM_WIFI_SSID_MAX_BYTES &&
               ble_copy_cstr(ssid, sizeof(ssid), cmd->pdata, cmd->len))
            {
                service_param_set_wifi(ssid, NULL);
                service_param_save();
                sys_logi(BEL_SERVICE_TAG, "Set WiFi SSID: %s", ssid);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected WIFI_SSID set (len=%d)", cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_SSID_GET : // WiFi SSID查询
        {
            uint8_t ssid_len = strlen(g_service_param.network.wifi_ssid);
            sys_logi(BEL_SERVICE_TAG, "WiFi SSID: %s", g_service_param.network.wifi_ssid);
            uint8_t resp_ssid_len = 4 + ssid_len; // HEAD + CH + LEN + DATA + CHECKSUM
            uint8_t *resp_buf = pvPortMalloc(resp_ssid_len);
            if(resp_buf)
            {
                size_t frame_len = ble_cmd_build_frame(BLE_FILM_TRANS_CH_CTRL_WIFI_SSID_GET,
                                                       (const uint8_t *)g_service_param.network.wifi_ssid,
                                                       ssid_len, resp_buf, resp_ssid_len);
                if(frame_len > 0)
                {
                    service_ble_msg_gatts_data_send(resp_buf, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
                }
                vPortFree(resp_buf);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD : // WiFi 密码设置（配网）
        {
            /* 密码为机密：明文写入参数服务，落盘时由参数服务经 SFM_BLE_KEY 加密
             *。非空载荷不带 NUL，先落成有界本地明文串再交 setter；空载荷
             * 表示开放网络并清除旧密码。明文副本用后抹除且绝不写日志（Req 15.1/15.4）。 */
            char pwd[SFM_WIFI_PASSWORD_MAX] = {0};
            bool valid = cmd->len == 0u ||
                         ble_copy_cstr(pwd, sizeof(pwd), cmd->pdata, cmd->len);
            if(valid)
            {
                service_param_set_wifi(NULL, pwd);
                service_param_save();
                sys_logi(BEL_SERVICE_TAG, "WiFi password updated (value redacted)");
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected WIFI_PASSWORD set (len=%d)", cmd->len);
            }
            ble_secure_zero(pwd, sizeof(pwd));
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD_GET : // WiFi 密码查询 -> 拒绝读回明文（Req 9.7/15.3）
        {
            /* 机密禁止读回（Req 9.7/15.3，Property 10）：绝不返回密码明文。保留 opcode 与
             * “有响应”契约以兼容既有 App，但仅回 1 字节 set/unset 状态位（非机密），
             * 让 App 能判断“是否已配置密码”而无法获取其值。
             *
             * 响应构造收敛到纯函数 ble_build_secret_status_response()（* 宿主可测），它只依据字段“是否已配置”产出状态位，绝不将密码内容写入帧。*/
            uint8_t frame[BLE_RESP_SMALL_BUF];
            size_t  frame_len = ble_build_secret_status_response(
                                    BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD_GET,
                                    g_service_param.network.wifi_password,
                                    frame, sizeof(frame));
            sys_logi(BEL_SERVICE_TAG,
                     "WiFi password query -> plaintext read-back refused, status-only (%s)",
                     (g_service_param.network.wifi_password[0] != '\0') ? "set" : "unset");
            if(frame_len > 0)
            {
                service_ble_msg_gatts_data_send(frame, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_FILM_API_URL : // 已停用：原「HTTP 下载 film 的 API 地址」
        {
            /* 不再接受写入。这个字段是上游遗留下载路径唯一的输入口，而那条路径
             * 不强制 HTTPS、不带鉴权、也不校验 SHA-256（已删除，见 service_wifi.c）。
             * 继续允许写入等于保留一个「把设备指向任意明文地址」的开关，而它已经
             * 没有任何读取方，写进去只会造成「配了却不生效」的困惑。
             *
             * 取图地址请改用 manifest_url（0x3E），它有 https 强制校验并配合
             * device_token 鉴权。字段本身保留不删：它在 param_serialize.c 占 128 字节
             * 定长槽位，删除会改变 NVS blob 布局并需要 config_version 迁移。 */
            sys_logw(BEL_SERVICE_TAG,
                     "FILM_API_URL (0x36) is retired and ignored; use MANIFEST_URL (0x3E) instead");
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_FILM_API_URL_GET : // 已停用：原「HTTP 下载 film 的 API 地址」查询
        {
            /* 与 0x36 一并停用。继续返回该字段只会误导：值可能是停用前残留的旧地址，
             * 而它对取图行为已完全没有影响 —— 上位机看到一个「配着的地址」却发现设备
             * 从别处取图，是最费解的那类不一致。回空串明确表达「本项已废弃」。
             * 取图地址请查 manifest_url（0x3F）。 */
            sys_logw(BEL_SERVICE_TAG,
                     "FILM_API_URL_GET (0x37) is retired; query MANIFEST_URL (0x3F) instead");
            ble_send_str_response(BLE_FILM_TRANS_CH_CTRL_FILM_API_URL_GET, "");
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT : // 连接WiFi
        {
            sys_logi(BEL_SERVICE_TAG, "WiFi connect requested");
            service_wifi_connect();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_DISCONNECT : // 断开WiFi连接
        {
            sys_logi(BEL_SERVICE_TAG, "WiFi disconnect requested");
            service_wifi_disconnect();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT_GET : // 查询WiFi连接状态
        {
            uint8_t status = service_wifi_get_connect_status();
            sys_logi(BEL_SERVICE_TAG, "WiFi connect status: %s", status ? "Connected" : "Disconnected");
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT_GET, &status, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WIFI_CLEAR : // 清除网络配置信息
        {
            sys_logi(BEL_SERVICE_TAG, "WiFi config clear requested");
            service_wifi_clear_config();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD : // 立即取图（保留为 0x44 SYNC_NOW 的兼容别名）
        {
            /* 转发到 service_wifi_sync_now()，与 0x44 完全等价；保留本 opcode 只为不
             * 破坏已有上位机。
             *
             * 必须用 sync_now()（内部另起任务）而非 sync_once()：在 BLE 任务里同步跑
             * TLS 握手加整份下载会阻塞协议栈，被对端判定 GATT 超时而断连。 */
            sys_logi(BEL_SERVICE_TAG, "Film download requested -> manifest sync (alias of 0x44)");
            service_wifi_sync_now();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD_STATE : // 查询下载状态
        {
            wifi_download_state_t state = service_wifi_download_get_state();
            uint8_t progress = service_wifi_download_get_progress();
            sys_logi(BEL_SERVICE_TAG, "Download state: %d, progress: %d%%", state, progress);
            uint8_t data[2];
            data[0] = (uint8_t)state;
            data[1] = progress;
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD_STATE, data, 2);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL : // 设置 manifest_url（配网）
        {
            /* manifest_url 非机密，经参数服务 setter 持久化（Req 15.1）。 */
            char url[SFM_MANIFEST_URL_MAX];
            if(ble_copy_cstr(url, sizeof(url), cmd->pdata, cmd->len))
            {
                service_param_set_manifest_url(url);
                service_param_save();
                sys_logi(BEL_SERVICE_TAG, "Set manifest_url: %s", url);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected MANIFEST_URL set (len=%d)", cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL_GET : // 查询 manifest_url（非机密）
        {
            sys_logi(BEL_SERVICE_TAG, "manifest_url: %s", g_service_param.network.manifest_url);
            ble_send_str_response(BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL_GET,
                                  g_service_param.network.manifest_url);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_DEVICE_ID : // 设置 device_id（配网）
        {
            /* device_id 非机密，经参数服务 setter 持久化（Req 15.1）。 */
            char id[SFM_DEVICE_ID_MAX];
            if(ble_copy_cstr(id, sizeof(id), cmd->pdata, cmd->len))
            {
                service_param_set_device_id(id);
                service_param_save();
                sys_logi(BEL_SERVICE_TAG, "Set device_id: %s", id);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected DEVICE_ID set (len=%d)", cmd->len);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_DEVICE_ID_GET : // 查询 device_id（非机密）
        {
            sys_logi(BEL_SERVICE_TAG, "device_id: %s", g_service_param.network.device_id);
            ble_send_str_response(BLE_FILM_TRANS_CH_CTRL_DEVICE_ID_GET,
                                  g_service_param.network.device_id);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_DEVICE_TOKEN : // 设置 device_token（配网）
        {
            /* device_token 为机密：明文写入参数服务，落盘时由参数服务经 SFM_BLE_KEY
             * 加密。载荷非 NUL 结尾 → 落成有界本地明文串再交 setter，用后抹除；
             * 绝不记录令牌明文，且不提供任何读回命令（Req 15.1/15.3/15.4）。 */
            char token[SFM_DEVICE_TOKEN_MAX];
            if(ble_copy_cstr(token, sizeof(token), cmd->pdata, cmd->len))
            {
                service_param_set_device_token(token);
                service_param_save();
                sys_logi(BEL_SERVICE_TAG, "device_token updated (value redacted)");
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected DEVICE_TOKEN set (len=%d)", cmd->len);
            }
            ble_secure_zero(token, sizeof(token));
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_STATUS_QUERY : // 查询 Wi-Fi状态/版本/错误/busy/电量（Req 9.6，不含机密）
        {
            ble_send_status_query();
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_SYNC_NOW : // SYNC_NOW 立即同步（Req 14.1/14.4）
        {
            /* SYNC_NOW（A3，Req 14.1）：收到即立即触发一次「一次性」Wi-Fi manifest
             * 检查，演示时无需等待低功耗管理器的轮询周期。轮询仍为默认后台同步
             * 行为（Req 14.2），本命令仅为附加的即时触发；WiFi同步服务不使用 MQTT
             * 长连接实现触发（Req 14.4）。触发逻辑收敛在 service_wifi_sync_now()，
             * BLE 只做转发，不自实现同步流程。 */
            sys_logi(BEL_SERVICE_TAG, "SYNC_NOW received -> trigger immediate one-shot Wi-Fi sync");
            service_wifi_sync_now();

            /* Ack：复用既有 notify 帧构造器（ble_cmd_build_frame，经 ble_send_std_response）
             * 回 1 字节受理状态位，发送长度恒等于真实载荷（Req 9.4）。 */
            uint8_t ack = 1u; /* 1 = 已受理并触发一次即时同步 */
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_SYNC_NOW, &ack, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_CLEAR_ALL : // 清空全部 .film（只保留当天菜单）
        {
            /* 发送方在下发新一套画面【之前】调用一次，避免历史版本图片留在设备上被
             * 按键轮播翻出来。清空不清屏：当前画面保留到下一张图上屏，避免瞬间白屏。
             * 传输进行中会被 service_file_clear_all() 拒绝（返回 -2），不做任何删除。 */
            /* 必须【异步】投给文件任务，不能在 BLE 任务里同步删。
             * opendir/readdir/remove 都是 FATFS + SD 卡 IO，删若干个 209120 字节的文件耗时
             * 足以阻塞 BLE 任务，使协议栈无法及时响应而被对端判定 GATT 超时并断开连接
             * （实测：0x45 之后必然出现 "GATT operation failed" 且随即断连）。 */
            int rc = service_file_clear_all_async();
            sys_logi(BEL_SERVICE_TAG, "FILE_CLEAR_ALL accepted -> rc=%d", rc);

            /* Ack 语义是「已受理」而非「已删完」：真实删除在文件任务里进行。
             * 1 = 已受理，0xFF = 无法受理（文件任务未就绪）。 */
            uint8_t ack = (rc == 0) ? 1u : 0xFFu;
            ble_send_std_response(BLE_FILM_TRANS_CH_FILE_CLEAR_ALL, &ack, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_SILENT_RECV : // 静默接收开关（连发多张时避免逐张刷屏）
        {
            /* 载荷 1 字节：非 0 开启、0 关闭。开启期间 FILE_STOP 只保存不上屏。
             * 缺载荷时按「开启」处理（发送方意图显然是要静默），避免因少一字节而
             * 退化成逐张刷屏、进而 GATT 超时断连。 */
            uint8_t enable = 1u;
            if(cmd->len >= 1u && cmd->pdata != NULL)
            {
                enable = (cmd->pdata[0] != 0u) ? 1u : 0u;
            }
            service_file_set_silent_receive(enable);
            sys_logi(BEL_SERVICE_TAG, "SILENT_RECV set to %u", (unsigned)enable);

            uint8_t ack = service_file_get_silent_receive();
            ble_send_std_response(BLE_FILM_TRANS_CH_FILE_SILENT_RECV, &ack, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_FILE_DISPLAY_NOW : // 按当前时段显示对应那一餐
        {
            /* 整套传完后由发送方触发一次：设备按本地时间选择 breakfast/lunch/dinner
             * 中对应的那张显示（中午就显示午餐详情）。时间不可信时回落显示今日总览，
             * 绝不猜时段。这条命令同时也是「整套接收结束」的收尾信号。 */
            int idx = service_film_display_for_now();
            sys_logi(BEL_SERVICE_TAG, "DISPLAY_NOW -> idx=%d", idx);

            uint8_t ack = (idx >= 0 && idx <= 254) ? (uint8_t)idx : 0xFFu;
            ble_send_std_response(BLE_FILM_TRANS_CH_FILE_DISPLAY_NOW, &ack, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE : // 设置唤醒时间表（F3）
        {
            /* 载荷：[count][slot0_hi][slot0_lo]...[fallback_hi][fallback_lo]
             * 各 slot 与 fallback 均为大端 uint16，与既有 0x29（定时唤醒时间）的字节序一致。
             *
             * 校验失败【整条拒绝，不做部分写入】：半张时间表会让设备在一部分饭点醒、
             * 另一部分不醒，而 App 那边显示的是完整配置 —— 两边不一致却没有任何报错。
             * 真正的写入由 service_param_set_wake_schedule() 做（它自己也会再校验一遍，
             * 因为它不止 BLE 一个调用方）。 */
            bool ok = false;
            uint8_t count = 0;
            uint16_t slots[WAKE_SCHEDULE_MAX_SLOTS] = {0};
            uint16_t fallback = 0;

            /* 最短合法载荷 = count(1) + fallback(2) = 3（count==0，只配兜底）。 */
            if(cmd->len >= 3u && cmd->pdata != NULL)
            {
                count = cmd->pdata[0];
                /* 长度必须与 count 严格自洽：多一字节少一字节都说明发送方与固件对
                 * 布局的理解不一致，此时按任一方猜测解析都可能把 fallback 当成 slot。 */
                if(count <= WAKE_SCHEDULE_MAX_SLOTS &&
                   cmd->len == (uint8_t)(1u + 2u * (unsigned)count + 2u))
                {
                    ok = true;
                    for(uint8_t i = 0; i < count; i++)
                    {
                        slots[i] = (uint16_t)((cmd->pdata[1u + 2u * i] << 8) |
                                               cmd->pdata[2u + 2u * i]);
                        if(!wake_schedule_slot_valid(slots[i]))
                        {
                            ok = false;
                            break;
                        }
                    }
                    if(ok)
                    {
                        fallback = (uint16_t)((cmd->pdata[1u + 2u * (unsigned)count] << 8) |
                                               cmd->pdata[2u + 2u * (unsigned)count]);
                        ok = service_param_set_wake_schedule(slots, count, fallback);
                    }
                }
            }

            if(ok)
            {
                service_param_save();
                sys_logi(BEL_SERVICE_TAG, "SET_WAKE_SCHEDULE: %u slot(s), fallback=%u min",
                         (unsigned)count, (unsigned)fallback);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected SET_WAKE_SCHEDULE (len=%u)", (unsigned)cmd->len);
            }

            uint8_t ack = ok ? 1u : 0xFFu;
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE, &ack, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE_GET : // 查询唤醒时间表（F3）
        {
            /* 回读布局与 0x48 完全相同，App 可以把回读结果原样再发回来。
             * 响应载荷最长 1 + 2*6 + 2 = 15 字节，超过 ble_send_std_response 的 8 字节
             * 小缓冲，故这里自行开一个足够的栈缓冲构帧。 */
            uint8_t payload[1u + 2u * WAKE_SCHEDULE_MAX_SLOTS + 2u];
            uint8_t count = g_service_param.sleep.wake_slot_count;
            if(count > WAKE_SCHEDULE_MAX_SLOTS)
            {
                count = (uint8_t)WAKE_SCHEDULE_MAX_SLOTS;   /* 防御：NVS 值异常时不越界读 */
            }

            uint8_t n = 0;
            payload[n++] = count;
            for(uint8_t i = 0; i < count; i++)
            {
                payload[n++] = (uint8_t)((g_service_param.sleep.wake_slots[i] >> 8) & 0xFFu);
                payload[n++] = (uint8_t)(g_service_param.sleep.wake_slots[i] & 0xFFu);
            }
            payload[n++] = (uint8_t)((g_service_param.sleep.wake_fallback_min >> 8) & 0xFFu);
            payload[n++] = (uint8_t)(g_service_param.sleep.wake_fallback_min & 0xFFu);

            uint8_t frame[sizeof(payload) + BLE_PARSER_CMD_OVERHEAD];
            size_t frame_len = ble_cmd_build_frame(BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE_GET,
                                                  payload, n, frame, sizeof(frame));
            if(frame_len > 0)
            {
                service_ble_msg_gatts_data_send(frame, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
            }
            sys_logi(BEL_SERVICE_TAG, "GET_WAKE_SCHEDULE: %u slot(s), fallback=%u min",
                     (unsigned)count, (unsigned)g_service_param.sleep.wake_fallback_min);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL : // 设置轮询周期（秒，F3）
        {
            /* 载荷 4 字节大端秒数。写入前经 monitor_clamp_poll_interval() 钳到 [30,86400]，
             * 并把【钳制后】的值回给 App —— 否则 App 显示 5 秒、设备实际 30 秒，
             * 现场调续航时会得出错误结论。 */
            if(cmd->len == 4u && cmd->pdata != NULL)
            {
                uint32_t req = ((uint32_t)cmd->pdata[0] << 24) |
                               ((uint32_t)cmd->pdata[1] << 16) |
                               ((uint32_t)cmd->pdata[2] << 8) |
                                (uint32_t)cmd->pdata[3];
                service_param_set_poll_interval(req);   /* setter 内部完成钳制 */
                service_param_save();

                uint32_t eff = g_service_param.sync.poll_interval_seconds;
                sys_logi(BEL_SERVICE_TAG, "SET_POLL_INTERVAL: requested %u -> effective %u sec",
                         (unsigned)req, (unsigned)eff);

                uint8_t data[4];
                data[0] = (uint8_t)((eff >> 24) & 0xFFu);
                data[1] = (uint8_t)((eff >> 16) & 0xFFu);
                data[2] = (uint8_t)((eff >> 8) & 0xFFu);
                data[3] = (uint8_t)(eff & 0xFFu);
                ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL, data, 4);
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected SET_POLL_INTERVAL (len=%u)", (unsigned)cmd->len);
                uint8_t ack = 0xFFu;
                ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL, &ack, 1);
            }
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL_GET : // 查询轮询周期（秒，F3）
        {
            uint32_t secs = g_service_param.sync.poll_interval_seconds;
            uint8_t data[4];
            data[0] = (uint8_t)((secs >> 24) & 0xFFu);
            data[1] = (uint8_t)((secs >> 16) & 0xFFu);
            data[2] = (uint8_t)((secs >> 8) & 0xFFu);
            data[3] = (uint8_t)(secs & 0xFFu);
            sys_logi(BEL_SERVICE_TAG, "GET_POLL_INTERVAL: %u sec", (unsigned)secs);
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL_GET, data, 4);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_PIN_FILM : // 设置置顶自定义画面
        {
            /* 载荷：[mode][start_hi][start_lo][end_hi][end_lo][name_len][name...]
             * 各 uint16 为大端，与 0x48/0x29 的字节序一致。name 不带结尾 NUL，由固件补
             * （与既有配网 set 命令同一处置，见 ble_copy_cstr 的说明）。
             *
             * mode == FILM_PIN_OFF 时允许只发 1 字节：取消 pin 不需要窗口和文件名，
             * 要求 App 凑齐 6 字节只是让「清除」这件事更容易发错。
             *
             * 校验失败【整条拒绝，绝不部分写入】（与 0x48 同一约定）：半份配置会让设备
             * 在一部分时间显示对、另一部分显示错，而 App 显示的是完整配置，两边不一致
             * 却没有任何一处报错。 */
            bool ok = false;

            if(cmd->len >= 1u && cmd->pdata != NULL)
            {
                uint8_t mode = cmd->pdata[0];

                if(mode == (uint8_t)FILM_PIN_OFF)
                {
                    /* 取消 pin = 擦掉整条记录，而不是写一条 mode=OFF 的记录：
                     * 少一种需要解释的中间状态。载荷更长时也一样处理（App 可能把
                     * 0x4D 的回读原样发回来，那就是 6 字节且 mode=OFF）。 */
                    service_param_pinned_erase();
                    service_film_pin_invalidate();
                    sys_logi(BEL_SERVICE_TAG, "PIN_FILM cleared");
                    ok = true;
                }
                else if(cmd->len >= 6u)
                {
                    uint8_t name_len = cmd->pdata[5];
                    /* 长度必须与 name_len 严格自洽：多一字节少一字节都说明发送方与固件
                     * 对布局的理解不一致，此时按任一方猜测解析都可能把别的字段当成名字。 */
                    if((unsigned)cmd->len == 6u + (unsigned)name_len)
                    {
                        film_pin_cfg_t cfg;
                        memset(&cfg, 0, sizeof(cfg));
                        cfg.mode = mode;
                        cfg.window_start_min = (uint16_t)(((uint16_t)cmd->pdata[1] << 8) |
                                                          cmd->pdata[2]);
                        cfg.window_end_min = (uint16_t)(((uint16_t)cmd->pdata[3] << 8) |
                                                        cmd->pdata[4]);

                        /* 有界拷贝并补 NUL；name_len==0 或 >= 容量时返回 false（拒绝）。 */
                        if(ble_copy_cstr(cfg.filename, sizeof(cfg.filename),
                                         &cmd->pdata[6], name_len))
                        {
                            /* 先用纯谓词判一次，好让日志能区分「配置非法」与「NVS 写失败」；
                             * service_param_pinned_save() 内部还会再校验一遍（它不止
                             * BLE 一个调用方）。 */
                            if(film_pin_cfg_is_valid(&cfg))
                            {
                                ok = service_param_pinned_save(&cfg);
                                if(ok)
                                {
                                    service_film_pin_invalidate();
                                    sys_logi(BEL_SERVICE_TAG,
                                             "PIN_FILM set: mode=%u window=[%u,%u) name=%s",
                                             (unsigned)cfg.mode,
                                             (unsigned)cfg.window_start_min,
                                             (unsigned)cfg.window_end_min,
                                             cfg.filename);
                                }
                                else
                                {
                                    sys_loge(BEL_SERVICE_TAG, "PIN_FILM save failed (NVS)");
                                }
                            }
                            else
                            {
                                /* 最常见的拒绝原因是文件名含 today/breakfast/lunch/dinner
                                 * 关键字，或不以 .film 结尾 —— 把名字打出来，否则现场
                                 * 只能看到一个 0xFF 而无从下手。 */
                                sys_logw(BEL_SERVICE_TAG,
                                         "Rejected PIN_FILM (invalid cfg): mode=%u name=%s",
                                         (unsigned)cfg.mode, cfg.filename);
                            }
                        }
                        else
                        {
                            sys_logw(BEL_SERVICE_TAG,
                                     "Rejected PIN_FILM (name_len=%u out of range)",
                                     (unsigned)name_len);
                        }
                    }
                    else
                    {
                        sys_logw(BEL_SERVICE_TAG,
                                 "Rejected PIN_FILM (len=%u inconsistent with name_len=%u)",
                                 (unsigned)cmd->len, (unsigned)name_len);
                    }
                }
                else
                {
                    sys_logw(BEL_SERVICE_TAG, "Rejected PIN_FILM (len=%u too short)",
                             (unsigned)cmd->len);
                }
            }
            else
            {
                sys_logw(BEL_SERVICE_TAG, "Rejected PIN_FILM (empty payload)");
            }

            uint8_t ack = ok ? 1u : 0xFFu;
            ble_send_std_response(BLE_FILM_TRANS_CH_CTRL_PIN_FILM, &ack, 1);
            break;
        }
        case BLE_FILM_TRANS_CH_CTRL_PIN_FILM_GET : // 查询置顶自定义画面
        {
            /* 响应布局与 0x4C 载荷**完全相同**，App 可以把回读结果原样再发回来。
             * 最长 6 + 31 = 37 字节，超过 ble_send_std_response 的 8 字节小缓冲，
             * 故这里自行开栈缓冲构帧（与 0x49 同一处置）。 */
            film_pin_cfg_t cfg;
            (void)service_film_pin_get(&cfg);   /* 未设置时也会填成确定的 OFF 配置 */

            uint8_t payload[6u + FILM_PIN_FILENAME_MAX];
            /* 有界求长（避免非标准的 strnlen）：cfg.filename 已由载入/校验路径保证在
             * 容量内 NUL 结尾，这里仍取有界长度作为纵深防御。 */
            const void *nul = memchr(cfg.filename, '\0', sizeof(cfg.filename));
            uint8_t name_len = (nul != NULL)
                                 ? (uint8_t)((const char *)nul - cfg.filename)
                                 : (uint8_t)(FILM_PIN_FILENAME_MAX - 1u);

            payload[0] = cfg.mode;
            payload[1] = (uint8_t)((cfg.window_start_min >> 8) & 0xFFu);
            payload[2] = (uint8_t)(cfg.window_start_min & 0xFFu);
            payload[3] = (uint8_t)((cfg.window_end_min >> 8) & 0xFFu);
            payload[4] = (uint8_t)(cfg.window_end_min & 0xFFu);
            payload[5] = name_len;
            if(name_len > 0u)
            {
                memcpy(&payload[6], cfg.filename, name_len);
            }

            uint8_t frame[sizeof(payload) + BLE_PARSER_CMD_OVERHEAD];
            size_t frame_len = ble_cmd_build_frame(BLE_FILM_TRANS_CH_CTRL_PIN_FILM_GET,
                                                  payload, (uint8_t)(6u + name_len),
                                                  frame, sizeof(frame));
            if(frame_len > 0)
            {
                service_ble_msg_gatts_data_send(frame, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
            }
            sys_logi(BEL_SERVICE_TAG, "GET_PIN_FILM: mode=%u window=[%u,%u) name=%s",
                     (unsigned)cfg.mode, (unsigned)cfg.window_start_min,
                     (unsigned)cfg.window_end_min, cfg.filename);
            break;
        }
        default :
        {
            break;
        }
    }
}

/**
 * [ble_checksum 和校验]
 * @param  arr [校验函数]
 * @param  len [校验长度]
 * @return     [校验值]
 */
static uint8_t ble_checksum(uint8_t arr[], int len)
{
    uint8_t sum = 0;
    for (int i = 0; i < len; i++)
    {
        sum += arr[i];
    }
    return sum;
}

/**
 * [ble_send_std_response 构造并发送一帧标准响应]
 *
 * 通过纯函数 ble_cmd_build_frame() 序列化响应帧，并**仅**发送其返回的精确帧长。
 * 这样发送长度恒等于真实载荷长度，绝不外发未初始化的填充字节（Req 9.4）。
 *
 * @param ch        通道/操作码（与既有 opcode 保持不变）
 * @param data      载荷指针（data_len==0 时可为 NULL）
 * @param data_len  载荷字节数
 */
static void ble_send_std_response(uint8_t ch, const uint8_t *data, uint8_t data_len)
{
    uint8_t frame[BLE_RESP_SMALL_BUF];
    size_t frame_len = ble_cmd_build_frame(ch, data, data_len, frame, sizeof(frame));
    if(frame_len > 0)
    {
        service_ble_msg_gatts_data_send(frame, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
    }
    else
    {
        sys_logw(BEL_SERVICE_TAG, "resp build failed: ch=0x%02x data_len=%u", ch, (unsigned)data_len);
    }
}

/**
 * [ble_send_str_response 以 opcode ch 回一帧“非机密字符串”响应]
 *
 * 用于 manifest_url / device_id 等**非机密**字段的读回。字符串来自
 * 已 NUL 结尾的参数缓冲，长度 <= 127（字段容量所限），恒 <= 一字节 LEN 上界。
 * 经纯函数 ble_cmd_build_frame() 序列化，发送长度恒等于真实载荷（Req 9.4）。
 *
 * 注意：本函数**不得**用于任何机密字段（wifi_password / device_token）——机密
 * 禁止读回（Req 9.7/15.3），密码查询仅回 set/unset 状态位，令牌无读回命令。
 */
static void ble_send_str_response(uint8_t ch, const char *str)
{
    uint8_t str_len = (str != NULL) ? (uint8_t)strlen(str) : 0u;
    uint8_t resp_len = BLE_PARSER_CMD_OVERHEAD + str_len;
    uint8_t *resp_buf = pvPortMalloc(resp_len);
    if(resp_buf == NULL)
    {
        return;
    }
    size_t frame_len = ble_cmd_build_frame(ch, (const uint8_t *)str, str_len,
                                           resp_buf, resp_len);
    if(frame_len > 0)
    {
        service_ble_msg_gatts_data_send(resp_buf, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
    }
    vPortFree(resp_buf);
}

/**
 * [ble_copy_cstr 将 BLE 载荷（非 NUL 结尾）安全拷入 NUL 结尾的本地缓冲]
 *
 * BLE 命令的 cmd->pdata 是解析器给出的越界安全视图，但**不保证** NUL 结尾；而
 * service_param 的 setter 内部用 strncpy(src) 会一直读到 NUL 或上限，直接把
 * cmd->pdata 传进去会越读相邻内存。故所有配网 set 命令先经此函数落成有界、
 * NUL 结尾的 C 字符串再交给 setter。
 *
 * @return true 拷贝成功（1 <= len <= dst_cap-1）；false 空载荷或超长（应拒绝）
 */
static bool ble_copy_cstr(char *dst, size_t dst_cap, const uint8_t *data, uint8_t len)
{
    if(dst == NULL || data == NULL || len == 0u || (size_t)len >= dst_cap)
    {
        return false;
    }
    memcpy(dst, data, len);
    dst[len] = '\0';
    return true;
}

/**
 * [ble_secure_zero 抹除内存（volatile，避免被优化省略）——清理栈上机密明文副本]
 */
static void ble_secure_zero(void *p, size_t n)
{
    volatile uint8_t *v = (volatile uint8_t *)p;
    while(n--)
    {
        *v++ = 0u;
    }
}

/**
 * [ble_send_status_query 回一帧合并状态查询响应（Req 9.6）]
 *
 * 载荷布局（大端 version；全部为**非机密**字段，绝不含 wifi_password/device_token）：
 *   [0]      wifi_status     Wi-Fi 连接状态 0/1（service_wifi_get_connect_status）
 *   [1..4]   version         最近成功应用版本 last_applied_version（大端 uint32）
 *   [5]      busy            文件事务忙 0/1（FT_RECEIVING / FT_VALIDATING）
 *   [6]      battery         电量百分比 0..100（hal_bat_get_percent，钳制）
 *   [7]      err_len         尾随错误串长度（0=无）
 *   [8..]    last_sync_error 最近同步错误（非机密文本，err_len 字节，无 NUL）
 *   [8+err_len] sleep_mode   休眠模式 0/1
 * data_len = 9 + err_len（旧 App 会忽略追加字节）。
 */
static void ble_send_status_query(void)
{
    uint8_t  wifi_status = service_wifi_get_connect_status() ? 1u : 0u;
    uint32_t version     = g_service_param.sync.last_applied_version;

    file_receive_state_t ft = service_file_get_receive_state();
    uint8_t  busy = (ft.state == FT_RECEIVING || ft.state == FT_VALIDATING) ? 1u : 0u;

    int bat = hal_bat_get_percent();
    if(bat < 0)   bat = 0;
    if(bat > 100) bat = 100;
    uint8_t battery = (uint8_t)bat;

    /* last_sync_error 是非机密文本（由 service_param_set_last_sync_error 存入、
     * 机密由日志脱敏/加密路径处理），可安全读回。 */
    const char *err = g_service_param.sync.last_sync_error;
    uint8_t err_len = (uint8_t)strlen(err);
    uint8_t data_len = 9u + err_len;

    uint8_t *data = pvPortMalloc(data_len);
    if(data == NULL)
    {
        return;
    }
    data[0] = wifi_status;
    data[1] = (uint8_t)((version >> 24) & 0xFF);
    data[2] = (uint8_t)((version >> 16) & 0xFF);
    data[3] = (uint8_t)((version >> 8) & 0xFF);
    data[4] = (uint8_t)(version & 0xFF);
    data[5] = busy;
    data[6] = battery;
    data[7] = err_len;
    if(err_len)
    {
        memcpy(&data[8], err, err_len);
    }
    data[8u + err_len] = g_service_param.sleep.sleep_mode ? 1u : 0u;

    uint8_t resp_len = BLE_PARSER_CMD_OVERHEAD + data_len;
    uint8_t *resp_buf = pvPortMalloc(resp_len);
    if(resp_buf)
    {
        size_t frame_len = ble_cmd_build_frame(BLE_FILM_TRANS_CH_CTRL_STATUS_QUERY,
                                               data, data_len, resp_buf, resp_len);
        if(frame_len > 0)
        {
            service_ble_msg_gatts_data_send(resp_buf, (uint16_t)frame_len, MSG_BLE_CH1_OUT_DATA);
        }
        vPortFree(resp_buf);
    }
    vPortFree(data);

    sys_logi(BEL_SERVICE_TAG,
             "Status query -> wifi=%u ver=%u busy=%u bat=%u%% err_len=%u",
             (unsigned)wifi_status, (unsigned)version, (unsigned)busy,
             (unsigned)battery, (unsigned)err_len);
}

/**
 * [ble_file_notify_result 通过 FILE_STOP 结果通道 Notify 传输成功/失败]
 *
 * BLE 传图接入统一文件事务后，FILE_LEN(begin)/FILE_DATA(write)/
 * FILE_STOP(finish) 任一失败以及 FILE_STOP 成功，都经此单一“传输结果”通道以 1 字节
 * 结果码 Notify，复用 BLE_FILM_TRANS_CH_FILE_STOP 的结果语义，不新增第二套数据格式、
 * 不改变既有 opcode/命令序列（Req 2、9.3）。
 *
 * @param ok true=成功（BLE_FILE_STOP_RESULT_OK）；false=失败（BLE_FILE_STOP_RESULT_FAIL）
 */
static void ble_file_notify_result(bool ok)
{
    uint8_t result = ok ? BLE_FILE_STOP_RESULT_OK : BLE_FILE_STOP_RESULT_FAIL;
    ble_send_std_response(BLE_FILM_TRANS_CH_FILE_STOP, &result, 1);
}

/**
 * [ble_film_trans_reset 复位 BLE 传图状态机与计数]
 */
static void ble_film_trans_reset(void)
{
    m_film_trans_state = BLE_FILM_TRANS_IDLE;
    m_film_trans_received = 0;
    m_film_trans_file_size = 0;
    memset(m_film_trans_filename, 0, sizeof(m_film_trans_filename));
}

/**
 * [ble_film_trans_abort_on_disconnect 断连中止未完成的文件事务]
 *
 * 在 BLE 任务上下文运行（由 MSG_BLE_GAP_PEER_DISCONNECTED 驱动），因此不会与
 * ble_cmd_process() 竞争传图状态。若断连时存在进行中的传输：
 *   - 调用 service_file_receive_abort() 释放统一文件事务的写所有权、关闭临时文件
 *     句柄并删除临时文件（对空闲事务为安全 no-op）。
 * BLE 传图统一走文件事务、不打开旧保存句柄，故此处无需 service_file_save_stop()。
 * 当前有效画面保持不变（Req 7.6、9.5）。
 */
static void ble_film_trans_abort_on_disconnect(void)
{
    if(m_film_trans_state != BLE_FILM_TRANS_IDLE)
    {
        sys_logw(BEL_SERVICE_TAG,
                 "BLE disconnected mid-transfer (state=%d, %u/%u bytes) -> abort",
                 (int)m_film_trans_state,
                 (unsigned)m_film_trans_received,
                 (unsigned)m_film_trans_file_size);

        /* Release unified transaction ownership + temp file if BLE owns one
         * (safe no-op when idle). Current picture is preserved (Req 7.6, 9.5). */
        service_file_receive_abort();
    }

    /* 断连必须复位静默接收：否则「开启静默 -> 传输中途断开」会让设备此后收到任何图都
     * 不再上屏，且没有任何界面提示用户，只能靠重启恢复。静默只是一次连发会话内的临时
     * 状态，随会话结束失效。 */
    if(service_file_get_silent_receive())
    {
        sys_logw(BEL_SERVICE_TAG, "disconnect -> reset silent receive");
        service_file_set_silent_receive(0);
    }

    ble_film_trans_reset();
}

/**
 * [service_ble_on_gatts_disconnect GATTS 断连回调]
 *
 * 运行在 bluedroid GATTS 回调上下文（非本 BLE 任务），只做入队，把真正的中止
 * 动作交给 BLE 任务串行处理，避免跨线程改动传图状态（Req 9.5）。
 */
static void service_ble_on_gatts_disconnect(void)
{
    if(m_ble_msg_hdl != NULL)
    {
        ble_msg_t msg = {0};
        msg.ID = MSG_BLE_GAP_PEER_DISCONNECTED;
        msg.pdata = NULL;
        msg.len = 0;
        /* GATTS callback is a task context (not an ISR): a non-blocking send is
         * sufficient. Never block the bluedroid stack task here. */
        if(xQueueSend(m_ble_msg_hdl, &msg, 0) != pdPASS)
        {
            sys_logw(BEL_SERVICE_TAG, "disconnect abort msg enqueue failed");
        }
    }
}
