/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/inc/service_file.h
 * Description: 文件服务对外接口：既有的保存/列表/加载，加上 BLE 与 Wi-Fi 共用的
 *              「双通道唯一文件事务」接口（service_file_receive_*）。
 *              传输适配器在 service_file_receive_finish() 成功提交后调用
 *              service_file_display_received() 触发显示，复用既有显示路径
 *              （刷新文件列表 → service_film_display → hal_epd_display_film），
 *              不自实现保存/校验/上屏（Req 5.1–5.4）。
 *
 *********************************************************************/
#ifndef __SERVICE_FILE_H__
#define __SERVICE_FILE_H__


/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stddef.h>

#include "esp_err.h"

#include "file_txn.h"   /* file_source_t / file_txn_state_t / file_receive_state_t
                         * 与纯决策逻辑（宿主机可测） */
#include "film_set_txn.h"


/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
#define FILE_TAG                    "file"
#define FILM_DIR                    "/sdcard/film"
#define FILM_FILE_EXT               ".film"

#define FILE_LOAD_STATE_NONE        (0)
#define FILE_LOAD_STATE_LOADING     (1)
#define FILE_LOAD_STATE_DONE        (2)

/*********************************************************************
* TYPEDEFS
*/
typedef struct {
    char filename[256];  // 文件名
    uint32_t file_size;  // 文件大小
} file_item_t;

typedef enum {
    MSG_FILE_LIST_REFRESH,    // 刷新文件列表
    MSG_FILE_LOAD,            // 加载文件
    MSG_FILE_LOAD_NEXT,       // 加载下一个文件
    MSG_SD_MOUNTED,           // SD卡挂载
    MSG_SD_UNMOUNTED,         // SD卡卸载
    MSG_FILE_SAVE_START,      // 开始保存文件
    MSG_FILE_SAVE_DATA,       // 保存文件数据
    MSG_FILE_SAVE_STOP,       // 停止保存文件
    MSG_FILE_DISPLAY_RECEIVED,// 事务提交成功后：刷新列表并显示新接收的文件
    MSG_FILE_CLEAR_ALL,       // 清空全部 .film（在文件任务内执行，避免阻塞调用方任务）
    MSG_FILE_DISPLAY_FOR_NOW, // 一整套提交成功后：刷新列表并按当前时段选图显示（F4）
    MSG_FILE_DISPLAY_IF_STALE, // 同步失败后的降级：只在该换页时才刷屏（Task 18）
} file_msg_type_t;

typedef struct {
    file_msg_type_t ID;
    uint32_t file_id;
    uint32_t file_size;
    uint32_t data_len;
    uint8_t *pdata;
} file_msg_t;

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
/**
 * @brief 初始化文件服务
 *
 * 此函数用于初始化文件服务，创建任务和消息队列，
 * 初始化状态变量，检查SD卡状态。
 */
extern void service_file_init(void);

/**
 * @brief 刷新文件列表
 *
 * 此函数用于刷新文件列表，扫描SD卡中的.film文件。
 */
extern void service_file_refresh_list(void);

/**
 * @brief 保存文件数据
 *
 * 此函数用于通过BLE接收文件数据并保存到SD卡。
 *
 * @param pfilename 文件名
 * @param file_size 文件大小
 * @param pdata 数据指针
 * @param data_len 数据长度
 * @return int 0:成功, -1:失败
 */
extern int service_file_save_data(const char *pfilename, uint32_t file_size, uint8_t *pdata, uint32_t data_len);

/**
 * @brief 开始保存文件
 *
 * 此函数用于开始BLE文件传输，初始化文件保存。
 *
 * @param pfilename 文件名
 * @param file_size 文件大小
 * @return int 0:成功, -1:失败
 */
extern int service_file_save_start(const char *pfilename, uint32_t file_size);

/**
 * @brief 停止保存文件
 *
 * 此函数用于完成BLE文件传输，关闭文件句柄。
 */
extern void service_file_save_stop(void);

/**
 * @brief 加载指定文件
 *
 * 此函数用于加载指定ID的文件到PSRAM。
 *
 * @param file_id 文件ID
 * @return int 0:成功, -1:失败
 */
extern int service_file_load(uint32_t file_id);

/**
 * @brief 加载下一个文件
 *
 * 此函数用于加载文件列表中的下一个文件，实现循环加载。
 */
extern void service_file_load_next(void);

/**
 * @brief 获取文件数量
 *
 * 此函数用于获取文件列表中的文件数量。
 *
 * @return uint32_t 文件数量
 */
extern uint32_t service_file_get_count(void);

/**
 * @brief 获取文件名
 *
 * 此函数用于获取指定ID的文件名。
 *
 * @param file_id 文件ID
 * @return const char* 文件名
 */
extern const char* service_file_get_name(uint32_t file_id);

/**
 * @brief 获取PSRAM缓冲区指针
 *
 * 此函数用于获取加载文件的PSRAM缓冲区指针。
 *
 * @return uint8_t* PSRAM缓冲区指针
 */
extern uint8_t* service_file_get_buffer(void);

/**
 * @brief 获取缓冲区大小
 *
 * 此函数用于获取PSRAM缓冲区的大小。
 *
 * @return uint32_t 缓冲区大小
 */
extern uint32_t service_file_get_buffer_size(void);

/**
 * @brief 获取当前加载的文件ID
 *
 * 此函数用于获取当前加载的文件ID。
 *
 * @return uint32_t 当前文件ID
 */
extern uint32_t service_file_get_current_id(void);

/**
 * @brief 获取文件加载状态
 *
 * 此函数用于获取当前文件加载状态。
 *
 * @return uint8_t 文件加载状态
 */
extern uint8_t service_file_get_load_complete(void);

/**
 * @brief 获取文件大小
 *
 * 此函数用于获取指定ID的文件大小。
 *
 * @param file_id 文件ID
 * @return uint32_t 文件大小
 */
extern uint32_t service_file_get_size(uint32_t file_id);

/**
 * @brief 获取文件名
 *
 * 此函数用于获取指定ID的文件名。
 *
 * @param file_id 文件ID
 * @return const char* 文件名
 */
extern const char* service_file_get_filename(uint32_t file_id);

/**
 * @brief 删除文件
 *
 * @param file_id 文件ID
 * @return int 0:成功, -1:失败
 */
extern int service_file_delete(uint32_t file_id);

/**
 * @brief 按文件名删除单个 .film
 *
 * 与 service_file_delete(file_id) 并存：0x05 FILE_DELETE 走索引，而按名删除的调用方
 * 手里只有名字，用「先刷列表、再线性找索引」凑一个 id 既绕又受列表刷新时序影响。
 *
 * 行为约定：
 *  - 只接受安全基名（非空、无路径分隔符、不含 ".."、以 .film 结尾）。本函数拼的是
 *    落盘路径，放宽一点就是一次任意路径删除；
 *  - 传输进行中（FT_RECEIVING / FT_VALIDATING）返回 -2 且不做任何删除；
 *  - 删除成功后刷新文件列表，游标不复位（不打断用户的轮播位置）；
 *  - 不清屏：当前画面保留到下一张图上屏。
 *
 * @param name 要删除的文件名（安全基名，如 "current.film"）
 * @return int 0 已删除；-1 参数非法 / SD 未挂载 / 文件不存在或删除失败；-2 传输进行中
 */
extern int service_file_remove(const char *name);

/**
 * @brief 清空存储中的全部 .film 文件
 *
 * 用于「只保留当天菜单」：发送方在下发新一套画面之前调用一次，避免历史版本的图片
 * 继续留在设备上被按键轮播翻出来。
 *
 * 行为约定：
 *  - 传输进行中（FT_RECEIVING / FT_VALIDATING）时拒绝执行并返回 -2，不做任何删除；
 *  - 删除后刷新文件列表并把轮播游标复位到 0；
 *  - 不清屏：当前画面继续保留到下一张图上屏，避免清空瞬间白屏。
 *
 * @return int >=0 实际删除的文件数；-1 SD 未挂载；-2 传输进行中被拒绝
 */
extern int service_file_clear_all(void);

/**
 * @brief 异步清空全部 .film（投递到文件任务执行，立即返回）
 *
 * BLE 等通信任务必须用这个版本：同步版会在调用方任务里做 opendir/readdir/remove
 * （FATFS + SD 卡 IO），删若干个 209120 字节的文件耗时足以阻塞 BLE 任务，使协议栈无法
 * 及时响应而被对端判定 GATT 超时断连。
 *
 * @return int 0 已受理；-1 消息队列不可用
 */
extern int service_file_clear_all_async(void);

/**
 * @brief 设置「静默接收」模式
 *
 * 开启后，接收完成（FILE_STOP 提交成功）只保存文件并刷新列表，**不触发上屏**。
 *
 * 用途：连发多张时避免逐张刷屏。电子纸刷一次约 20 秒，期间设备忙于送屏，对端极易
 * 因 GATT 超时而断连——这正是「单张可传、连发四张必失败」的原因。发送方应在传前面
 * 几张时开启静默、传最后一张前关闭，从而只刷最后一张（它也就成为默认画面）。
 *
 * 该状态不持久化：重启后回到 0（正常上屏），避免异常中断后设备永久不刷新。
 *
 * @param enable 非 0 开启静默，0 关闭（恢复接收后自动上屏）
 */
extern void service_file_set_silent_receive(uint8_t enable);

/**
 * @brief 查询「静默接收」模式当前状态
 * @return uint8_t 1 已开启，0 未开启
 */
extern uint8_t service_file_get_silent_receive(void);


/*====================================================================
 * 双通道唯一文件事务接口（Smart Fridge Magnet 新增）
 *
 * BLE 与 Wi-Fi 两个传输适配器共用此接口写文件，强制单写者、临时文件、
 * 增量校验、原子替换、失败保画面（Req 5.4、6、7、8、12、18）。
 *===================================================================*/

/**
 * @brief 开始一次写事务。
 *
 * 校验来源仅为 BLE/Wi-Fi、文件名为安全基础名（禁 `../`/绝对路径/路径分隔符/超长）、
 * `expected_size` 等于固定长度 FILM_FILE_TOTAL_SIZE(209120)；单写者，已有进行中的
 * 事务时对后到来源返回 busy（ESP_ERR_INVALID_STATE）。（Req 7.1–7.3、18.2、18.3）
 *
 * @param source        传输来源（FILE_SRC_BLE 或 FILE_SRC_WIFI）
 * @param filename       目标文件名（安全基础名）
 * @param expected_size  声明总长度，必须 == FILM_FILE_TOTAL_SIZE
 * @return ESP_OK 授予所有权；ESP_ERR_INVALID_ARG 参数非法；
 *         ESP_ERR_INVALID_STATE 已被占用（busy）
 */
extern esp_err_t service_file_receive_begin(file_source_t source,
                                            const char *filename,
                                            uint32_t expected_size);

/**
 * @brief 开始一个 Wi-Fi 集合条目的 staging 写事务。
 *
 * 数据仍走同一套长度、文件头和 SHA-256 校验，但 finish 后只落到不可见的 staging
 * 文件，不覆盖正式 `.film`。整套全部成功后必须调用 service_file_commit_staged_set()；
 * 任一下载失败则调用 service_file_discard_staged_set()。
 */
extern esp_err_t service_file_receive_begin_staged(const char *filename,
                                                   uint32_t expected_size);

/**
 * @brief 写入一块事务数据。
 *
 * 累计长度不变式：任一写入使累计超过声明长度即拒绝并中止事务（Req 7.4）。
 *
 * @param data    数据指针
 * @param length  数据长度
 * @return ESP_OK 成功；其余为对应错误码
 */
extern esp_err_t service_file_receive_write(const uint8_t *data, size_t length);

/**
 * @brief 结束事务并提交。
 *
 * 在单写者锁保护下按序校验并提交：
 *   (1) 状态必须为 FT_RECEIVING，否则返回 ESP_ERR_INVALID_STATE 且不清理；
 *   (2) 累计已写长度必须等于声明长度（Req 7.5、6.2）；
 *   (3) 临时文件 .film 32 字节文件头有效（像素长度/宽高/颜色数，Req 6.2/6.3）；
 *   (4) Wi-Fi 路径（expected_sha256 != NULL）额外比对增量 SHA-256 终值，
 *       BLE 路径（NULL）按设计跳过 SHA 比对（Req 7.5、12.2）；
 *   (5) 全部通过后 fsync(tmp)→rename(tmp,current) 原子替换（Req 8.1）。
 *
 * 任一校验或提交失败均统一终止清理（关句柄+删临时+释放所有权+复位 IDLE，Req 7.6）。
 * 关键不变式：新文件在完全校验通过前，current.film 绝不被替换（Req 7.7）。
 *
 * @param expected_sha256  期望 SHA-256（Wi-Fi 路径必填；BLE 路径可为 NULL）
 * @return ESP_OK 提交成功；ESP_ERR_INVALID_STATE 非 RECEIVING 态；
 *         ESP_ERR_INVALID_SIZE 长度/文件头校验失败；ESP_ERR_INVALID_CRC SHA-256 不匹配；
 *         其余为提交/摘要计算错误码
 */
extern esp_err_t service_file_receive_finish(const uint8_t expected_sha256[32]);

/**
 * @brief 中止当前事务：关闭句柄、删除临时文件、释放写所有权（Req 7.6）。
 *
 * 当前有效画面保持不变。
 *
 * @return ESP_OK
 */
extern esp_err_t service_file_receive_abort(void);

/**
 * @brief 一次提交整套已校验的 staging 文件。
 *
 * 写入掉电恢复标记后，先把旧正式文件改名为备份，再把 staging 提升为正式名。任一步
 * 失败都会回滚全部已处理条目；启动时也会恢复未完成的准备阶段，避免显示新旧混合版本。
 *
 * @param names 安全 `.film` 基名数组，数量 1..FILM_SET_MAX_ITEMS
 * @param count 数组长度
 */
extern esp_err_t service_file_commit_staged_set(const char *const names[], size_t count);

/** 删除本轮对应的 staging 文件，不改动任何正式 `.film`。 */
extern void service_file_discard_staged_set(const char *const names[], size_t count);

/**
 * @brief 查询当前事务状态快照。
 *
 * 供 BLE 状态查询与低功耗休眠互斥使用（Req 9.6、16.2）。
 *
 * @return 事务状态快照（state/owner/expected_size/written）
 */
extern file_receive_state_t service_file_get_receive_state(void);

/**
 * @brief 事务提交成功后触发显示（复用既有显示路径，Req 5.1–5.3）。
 *
 * 传输适配器（BLE/Wi-Fi）在 service_file_receive_finish() 成功原子替换后调用。把
 * 「刷新文件列表 → 按文件名定位 → service_film_display」放到文件任务上下文中同步执行
 * （先刷新后定位，避免异步刷新与查询之间的竞态），最终经 hal_epd_display_film 上屏。
 *
 * @param filename 已提交到 FILM_DIR 下的目标文件名（安全基础名，以 '\0' 结尾）
 */
extern void service_file_display_received(const char *filename);

/**
 * @brief 一整套画面提交成功后，刷新文件列表并按当前时段选一张上屏（异步）
 *
 * 用于 Wi-Fi「一套多张」下载：一套四张里该显示哪一张由时段决定，不是最后下载的那一张，
 * 所以不能用 service_file_display_received()。
 *
 * 必须走这个异步入口，不要在调用方任务里直接调 service_film_display_for_now()：
 * service_file_receive_finish() 只做提交、不刷新内存里的文件列表，而
 * service_file_refresh_list() 本身是异步的（只投一条消息）。调用方「先 refresh 再立刻
 * 选图」读到的是刷新前的旧列表，刚下载的四张可能一张都不在里面，于是回落到「列表第一
 * 张」，表现为同步成功但屏幕还是旧图而日志一切正常。本接口把刷新与选图都放进文件任务
 * 串行执行，消除该竞态。
 *
 * @return int 0 已受理；-1 消息队列不可用
 */
extern int service_file_display_for_now_async(void);

/**
 * @brief 按当前时段纠正屏幕，但只在该换页时才刷（异步）
 *
 * 用于「用户要了新菜单、但同步失败」的降级：没有新内容可上屏，至少保证屏幕显示的是
 * 当前时段该看的那一页 —— SD 卡上本就存着一套四张，早上那次唤醒可能把早餐页留在屏上，
 * 而用户现在是晚饭前按的键。
 *
 * 与 service_file_display_for_now_async() 的区别只在去重：那个是整套下载成功后调用的，
 * 必须无条件刷（文件名与索引都没变，变的是字节）；本接口按索引去重，因为电子纸刷一次
 * 约 20 秒、耗电且刷新次数有限，Wi-Fi 断一天会白刷三次同样的内容。
 *
 * @return int 0 已受理；-1 消息队列不可用
 */
extern int service_file_display_if_stale_async(void);


#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_FILE_H__ */
