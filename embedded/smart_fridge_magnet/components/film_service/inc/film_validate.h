/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/film_validate.h
 * Description: Structure validator for `.film` files (source-agnostic,
 *   order-independent outcome). Req 6.2–6.5.
 *
 *   Mirrors the real upstream FrameFilm header layout (`FilmHeader` in
 *   film_hal/src/hal_epd.c) rather than inventing a conflicting format. Two
 *   non-obvious properties of that layout: there is NO magic field, and
 *   offset 0x00 stores the PIXEL-DATA length, not the total file length.
 *   Fixed geometry constants below; the cross-end contract is in
 *   docs/contracts/README.md.
 *
 *   Depends only on plain C (<stdint.h>/<stddef.h>), not on the ESP-IDF
 *   runtime, so it can be compiled and property-tested on a host machine.
 *
 *********************************************************************/
#ifndef __FILM_VALIDATE_H__
#define __FILM_VALIDATE_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stddef.h>

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* Fixed geometry of the SFM six-color 792x528 `.film` (Req 6.1, 6.2). */
#define FILM_HEADER_SIZE_BYTES     (32u)                                   /* 32B header              */
#define FILM_COLOR_TABLE_ENTRIES   (16u)                                   /* palette slots in header */
#define FILM_PIXEL_BYTES           (209088u)                               /* 792*528 px @ 4bit/px    */
#define FILM_FILE_SIZE_BYTES       (FILM_HEADER_SIZE_BYTES + FILM_PIXEL_BYTES) /* 209120 total         */
#define FILM_EXPECTED_WIDTH        (792u)                                  /* canvas width  (Req 6.3) */
#define FILM_EXPECTED_HEIGHT       (528u)                                  /* canvas height (Req 6.3) */
#define FILM_EXPECTED_COLOR_COUNT  (6u)                                    /* six colors    (Req 6.3) */

/* Header field byte offsets — mirror upstream FrameFilm FilmHeader (hal_epd.c). */
#define FILM_OFF_PIXEL_BYTES       (0x00)  /* uint32 LE: declared pixel-data length */
#define FILM_OFF_WIDTH             (0x04)  /* uint16 LE: canvas width               */
#define FILM_OFF_HEIGHT            (0x06)  /* uint16 LE: canvas height              */
#define FILM_OFF_COLOR_COUNT       (0x08)  /* uint8    : palette color count        */
#define FILM_OFF_RESERVED          (0x09)  /* 7 reserved bytes                      */
#define FILM_OFF_COLOR_TABLE       (0x10)  /* 16-byte palette table                 */

/*********************************************************************
 * TYPEDEFS
 */
/*
 * Parsed `.film` header — packed, 32 bytes, matching the upstream FrameFilm
 * `FilmHeader` (film_hal/src/hal_epd.c) exactly.
 *
 * `pixel_bytes` is upstream `fileSize` at offset 0, which holds the pixel-data
 * byte count (209088), NOT the total file length (209120). There is no stored
 * bpp field: 4bit/px is enforced implicitly by the fixed length relation.
 */
typedef struct {
    uint32_t pixel_bytes;                            /* 0x00: must == FILM_PIXEL_BYTES (209088) */
    uint16_t width;                                  /* 0x04: must == FILM_EXPECTED_WIDTH  (792) */
    uint16_t height;                                 /* 0x06: must == FILM_EXPECTED_HEIGHT (528) */
    uint8_t  color_count;                            /* 0x08: expect FILM_EXPECTED_COLOR_COUNT (6) */
    uint8_t  reserved[7];                            /* 0x09..0x0F */
    uint8_t  color_table[FILM_COLOR_TABLE_ENTRIES];  /* 0x10..0x1F: palette */
} film_header_t;

/*
 * Validation result. Distinct reasons let tests assert the specific
 * first-failure. FILM_OK (== 0) means every structural check passed.
 */
typedef enum {
    FILM_OK = 0,             /* all checks passed                                     */
    FILM_ERR_NULL,           /* buffer pointer was NULL                               */
    FILM_ERR_TOTAL_LEN,      /* actual total length != 209120 (Req 6.2)               */
    FILM_ERR_PIXEL_LEN,      /* header-declared pixel length != 209088 (Req 6.2)      */
    FILM_ERR_DIMS,           /* width != 792 or height != 528 (Req 6.3)               */
    FILM_ERR_COLOR_COUNT,    /* color count != expected six (Req 6.3)                 */
    FILM_ERR_PALETTE_INDEX,  /* a pixel palette index >= color_count (Req 6.4)        */
    FILM_ERR_COLOR_TABLE,    /* colorTable 槽含非固件可识别的设备色值（以固件 color_lut 为准）*/
} film_err_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */
/**
 * @brief 解析 `.film` 32 字节文件头（小端，与主机字节序无关）
 *
 * Explicit little-endian decoding, so results are identical on the target and
 * on any host. The caller MUST guarantee @p buf holds at least
 * FILM_HEADER_SIZE_BYTES bytes.
 *
 * @param buf 原始 `.film` 字节缓冲（至少 32 字节）
 * @param out 解析结果输出
 */
void film_parse_header(const uint8_t *buf, film_header_t *out);

/**
 * @brief 结构校验 `.film` 文件（顺序无关，任一违规立即拒绝）
 *
 * Returns FILM_OK if and only if ALL of the following hold (Req 6.2–6.5):
 *   - total_len == FILM_FILE_SIZE_BYTES
 *   - header pixel length == FILM_PIXEL_BYTES
 *   - width == 792 and height == 528
 *   - color count == expected six
 *   - every 4-bit pixel palette index is < color_count
 *
 * Otherwise the first failing reason is returned. The accept/reject outcome is
 * independent of check order, since acceptance requires every check to pass.
 * Total length is checked first purely for memory safety: it gates all
 * subsequent buffer reads.
 *
 * @param buf       原始 `.film` 字节缓冲
 * @param total_len 缓冲实际总长度
 * @return film_err_t FILM_OK 或第一个失败原因
 */
film_err_t film_validate(const uint8_t *buf, size_t total_len);

/**
 * @brief 返回 film_err_t 的可读字符串（用于日志/测试诊断）
 *
 * @param err 校验结果
 * @return const char* 静态字符串，永不为 NULL
 */
const char *film_err_str(film_err_t err);

#ifdef __cplusplus
}
#endif

#endif /* __FILM_VALIDATE_H__ */
