/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/inc/file_txn.h
 * Description: 文件事务纯决策逻辑（来源/文件名/长度校验、单写者状态机转移）。
 *              仅依赖标准 C 头文件，不依赖 ESP-IDF/FreeRTOS/文件系统，可在宿主机
 *              直接编译与做属性测试。文件系统/临时文件/SHA-256 等副作用由
 *              service_file.c 承载。
 *
 *********************************************************************/
#ifndef __FILE_TXN_H__
#define __FILE_TXN_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* 智能冰箱贴 .film 文件固定总长度（32 字节头 + 209088 字节像素）。
 * 跨端契约见 docs/contracts/README.md。service_file.c 的保存路径以其派生值
 * FILE_EPD_IMGAGE_SIZE 做尺寸校验，两者必须一致（见 service_file.c 静态断言）。 */
#define FILM_FILE_TOTAL_SIZE        (209120u)

/* 事务文件名（安全基础名）允许的最大字符数（不含结尾 '\0'）。
 * 取值小于 file_item_t::filename 缓冲区（256），并给路径拼接留出余量。 */
#define FILE_TXN_FILENAME_MAX       (128u)

/*********************************************************************
 * TYPEDEFS
 */

/* 传输来源。写事务同一时刻至多归属其中一个来源（单写者）。 */
typedef enum {
    FILE_SRC_NONE = 0,  /* 无来源/未占用 */
    FILE_SRC_BLE,       /* BLE 分片直传 */
    FILE_SRC_WIFI,      /* Wi-Fi HTTPS 流式下载 */
} file_source_t;

/* 事务状态（供状态查询与低功耗休眠互斥使用）。 */
typedef enum {
    FT_IDLE = 0,      /* 空闲，可接受 begin */
    FT_RECEIVING,     /* 已开始，正在写临时文件 */
    FT_VALIDATING,    /* finish 中，正在校验 */
    FT_COMMITTED,     /* 已原子替换成功 */
    FT_ABORTED,       /* 已中止并清理 */
} file_txn_state_t;

/* 事务运行时快照。 */
typedef struct {
    file_txn_state_t state;         /* 当前状态 */
    file_source_t    owner;         /* 当前写所有者 */
    uint32_t         expected_size; /* 声明总长度，必须 == FILM_FILE_TOTAL_SIZE */
    uint32_t         written;       /* 已写累计字节 */
} file_receive_state_t;

/* 纯决策返回码（与 ESP-IDF 无关，由 service_file.c 映射到 esp_err_t）。 */
typedef enum {
    FILE_TXN_OK = 0,        /* 决策通过 */
    FILE_TXN_ERR_ARG,       /* 来源/文件名/声明长度非法 */
    FILE_TXN_ERR_BUSY,      /* 已有进行中的写事务被其他/本来源占用 */
    FILE_TXN_ERR_STATE,     /* 当前状态不允许该操作 */
    FILE_TXN_ERR_OVERFLOW,  /* 该写入将使累计长度超过声明长度 */
} file_txn_rc_t;

/*********************************************************************
 * GLOBAL FUNCTIONS（均为纯函数：无副作用、无全局状态、可宿主机测试）
 */

/**
 * @brief 判断文件名是否为安全的基础文件名（Req 7.3）。
 *
 * 规则：非空、长度不超过 FILE_TXN_FILENAME_MAX、不含路径分隔符（'/'、'\\'）、
 * 不以 '/' 起始、不含 ".." 穿越片段、不含控制字符（< 0x20）。
 *
 * @param filename 待校验的以 '\0' 结尾的字符串（可为 NULL）
 * @return true 表示安全基础名；false 表示应拒绝
 */
bool file_txn_filename_is_safe(const char *filename);

/**
 * @brief 判断某来源+声明长度是否为一组合法的 begin 参数（不含并发/状态判定）。
 *
 * @param source        传输来源
 * @param filename       文件名
 * @param expected_size  声明总长度
 * @return true 当且仅当 source∈{BLE,WIFI} 且 filename 为安全基础名
 *         且 expected_size == FILM_FILE_TOTAL_SIZE
 */
bool file_txn_begin_args_valid(file_source_t source,
                               const char *filename,
                               uint32_t expected_size);

/**
 * @brief begin 决策 + 单写者状态机转移（纯函数，就地更新 *st）。
 *
 * 语义（Req 7.2/7.3）：
 *  - 若已有进行中的写事务（state 为 FT_RECEIVING/FT_VALIDATING），返回
 *    FILE_TXN_ERR_BUSY，*st 不变（保护当前所有者，第二来源恒 busy）。
 *  - 否则若三项参数任一非法，返回 FILE_TXN_ERR_ARG，*st 不变。
 *  - 否则授予所有权：state=FT_RECEIVING、owner=source、expected_size=入参、
 *    written=0，返回 FILE_TXN_OK。
 *
 * @param st            事务状态（in/out，不可为 NULL）
 * @param source        传输来源
 * @param filename       文件名
 * @param expected_size  声明总长度
 * @return 决策返回码
 */
file_txn_rc_t file_txn_begin(file_receive_state_t *st,
                             file_source_t source,
                             const char *filename,
                             uint32_t expected_size);

/**
 * @brief write 的累计长度不变式决策 + 状态推进（纯函数，就地更新 *st）。
 *
 * 语义（Req 7.4）：
 *  - 仅在 FT_RECEIVING 下允许，否则返回 FILE_TXN_ERR_STATE，*st 不变。
 *  - 若 written + length 会超过 expected_size，返回 FILE_TXN_ERR_OVERFLOW 且
 *    *st 不变（调用方据此中止事务）。
 *  - 否则 written += length，返回 FILE_TXN_OK。
 *
 * 只推进「已接受字节计数」，真实落盘与 SHA-256 更新由 service_file.c 负责。
 *
 * @param st      事务状态（in/out，不可为 NULL）
 * @param length  本次写入字节数
 * @return 决策返回码
 */
file_txn_rc_t file_txn_write_advance(file_receive_state_t *st, size_t length);

/**
 * @brief 中止事务的状态转移（纯函数）：标记 FT_ABORTED 并释放写所有权。
 *
 * 仅处理状态/所有权语义；关闭句柄、删除临时文件等副作用由 service_file.c 承担。
 *
 * @param st 事务状态（in/out，NULL 时为 no-op）
 */
void file_txn_abort(file_receive_state_t *st);

/**
 * @brief 将事务状态复位为 FT_IDLE 且释放所有权（纯函数）。
 *
 * @param st 事务状态（in/out，NULL 时为 no-op）
 */
void file_txn_reset(file_receive_state_t *st);

#ifdef __cplusplus
}
#endif

#endif /* __FILE_TXN_H__ */
