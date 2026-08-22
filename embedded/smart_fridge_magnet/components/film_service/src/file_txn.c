/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/src/file_txn.c
 * Description: 文件事务纯决策逻辑实现（来源/文件名/长度校验、单写者状态机转移）。
 *              本文件为 Smart Fridge Magnet 项目新增，不依赖 ESP-IDF/文件系统，
 *              以便在宿主机做属性测试（Property 3/4/5）。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "file_txn.h"

/*********************************************************************
 * LOCAL FUNCTIONS
 */

/* 判断事务当前是否被某来源占用（进行中）。COMMITTED/ABORTED 为终态，不算占用，
 * 允许在其上直接开启新事务（service_file.c 通常已在这些终态后复位为 IDLE）。 */
static bool file_txn_is_active(const file_receive_state_t *st)
{
    return (st->state == FT_RECEIVING) || (st->state == FT_VALIDATING);
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

bool file_txn_filename_is_safe(const char *filename)
{
    if (filename == NULL) {
        return false;
    }

    /* 绝对路径（以 '/' 起始）直接拒绝。 */
    if (filename[0] == '/') {
        return false;
    }

    /* 扫描并同时做长度上限与非法字符检查；读取上限为 FILE_TXN_FILENAME_MAX+1，
     * 避免对未正确终止的缓冲区无界读取。 */
    size_t len = 0;
    while (filename[len] != '\0') {
        char c = filename[len];
        if (c == '/' || c == '\\') {
            return false;               /* 路径分隔符 */
        }
        if ((unsigned char)c < 0x20) {
            return false;               /* 控制字符 */
        }
        len++;
        if (len > FILE_TXN_FILENAME_MAX) {
            return false;               /* 超长 */
        }
    }

    if (len == 0) {
        return false;                   /* 空文件名 */
    }

    /* 拒绝路径穿越片段 ".."（作为子串出现即拒绝，足够安全且顺序无关）。 */
    for (size_t i = 0; i + 1 < len; i++) {
        if (filename[i] == '.' && filename[i + 1] == '.') {
            return false;
        }
    }

    return true;
}

bool file_txn_begin_args_valid(file_source_t source,
                               const char *filename,
                               uint32_t expected_size)
{
    if (source != FILE_SRC_BLE && source != FILE_SRC_WIFI) {
        return false;
    }
    if (!file_txn_filename_is_safe(filename)) {
        return false;
    }
    if (expected_size != FILM_FILE_TOTAL_SIZE) {
        return false;
    }
    return true;
}

file_txn_rc_t file_txn_begin(file_receive_state_t *st,
                             file_source_t source,
                             const char *filename,
                             uint32_t expected_size)
{
    if (st == NULL) {
        return FILE_TXN_ERR_ARG;
    }

    /* 单写者优先：已存在进行中的写事务时，任何来源的 begin 一律 busy，
     * 且不改动当前所有者的事务状态（Req 7.2 / Property 5）。 */
    if (file_txn_is_active(st)) {
        return FILE_TXN_ERR_BUSY;
    }

    /* 参数安全校验：三项均合法才授予所有权（Req 7.3 / Property 4）。 */
    if (!file_txn_begin_args_valid(source, filename, expected_size)) {
        return FILE_TXN_ERR_ARG;
    }

    st->state         = FT_RECEIVING;
    st->owner         = source;
    st->expected_size = expected_size;
    st->written       = 0;
    return FILE_TXN_OK;
}

file_txn_rc_t file_txn_write_advance(file_receive_state_t *st, size_t length)
{
    if (st == NULL) {
        return FILE_TXN_ERR_ARG;
    }
    if (st->state != FT_RECEIVING) {
        return FILE_TXN_ERR_STATE;
    }

    /* 累计长度不变式：written + length 不得超过声明长度（Req 7.4 / Property 3）。
     * 用减法比较避免 size_t/uint32_t 相加溢出。 */
    uint32_t remaining = st->expected_size - st->written; /* written<=expected 恒成立 */
    if (length > (size_t)remaining) {
        return FILE_TXN_ERR_OVERFLOW;
    }

    st->written += (uint32_t)length;
    return FILE_TXN_OK;
}

void file_txn_abort(file_receive_state_t *st)
{
    if (st == NULL) {
        return;
    }
    st->state = FT_ABORTED;
    st->owner = FILE_SRC_NONE;
    st->written = 0;
    /* expected_size 保留以便诊断；下一次 begin 会重新赋值。 */
}

void file_txn_reset(file_receive_state_t *st)
{
    if (st == NULL) {
        return;
    }
    st->state = FT_IDLE;
    st->owner = FILE_SRC_NONE;
    st->expected_size = 0;
    st->written = 0;
}
