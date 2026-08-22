/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/film_validate.c
 * Description: `.film` structure validator (source-agnostic). Implements the
 *   checks required by Requirements 6.2–6.5 of the firmware-migration-refactor
 *   spec: total length, header-declared pixel length, canvas
 *   dimensions, six-color count, and palette-index bounds.
 *
 *   Reuses the REAL upstream `.film` header layout (FrameFilm `FilmHeader`,
 *   see film_hal/src/hal_epd.c): 32-byte header + 120000B 4bit/px pixels.
 *   No conflicting format is invented. Depends only on plain C so it can be
 *   built and property-tested on a host machine (tasks 3.2/3.3).
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "film_validate.h"

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static inline uint16_t rd_u16_le(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static inline uint32_t rd_u32_le(const uint8_t *p)
{
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

/*
 * Palette-index bounds check (Req 6.4). Pixel data is packed 4bit/px: each
 * byte holds two palette indices (high nibble, then low nibble). A valid
 * index references one of the `color_count` populated palette slots, so every
 * nibble must be strictly less than color_count. Because nibbles are 4-bit,
 * this is only meaningful (and only violable) when color_count < 16 — which is
 * always true here (expected six).
 */
static int palette_indices_in_range(const uint8_t *pixels,
                                     uint32_t pixel_len,
                                     uint8_t color_count)
{
    for (uint32_t i = 0; i < pixel_len; ++i) {
        uint8_t hi = (uint8_t)((pixels[i] >> 4) & 0x0F);
        uint8_t lo = (uint8_t)(pixels[i] & 0x0F);
        if (hi >= color_count || lo >= color_count) {
            return 0;
        }
    }
    return 1;
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */
void film_parse_header(const uint8_t *buf, film_header_t *out)
{
    if (buf == NULL || out == NULL) {
        return;
    }

    out->pixel_bytes = rd_u32_le(&buf[FILM_OFF_PIXEL_BYTES]);
    out->width       = rd_u16_le(&buf[FILM_OFF_WIDTH]);
    out->height      = rd_u16_le(&buf[FILM_OFF_HEIGHT]);
    out->color_count = buf[FILM_OFF_COLOR_COUNT];

    for (int i = 0; i < 7; ++i) {
        out->reserved[i] = buf[FILM_OFF_RESERVED + i];
    }
    for (unsigned i = 0; i < FILM_COLOR_TABLE_ENTRIES; ++i) {
        out->color_table[i] = buf[FILM_OFF_COLOR_TABLE + i];
    }
}

film_err_t film_validate(const uint8_t *buf, size_t total_len)
{
    film_header_t hdr;

    /* Null guard. */
    if (buf == NULL) {
        return FILM_ERR_NULL;
    }

    /* Total length (Req 6.2). Checked first so all later reads stay in bounds;
     * this gates — but does not otherwise privilege — the remaining checks. */
    if (total_len != (size_t)FILM_FILE_SIZE_BYTES) {
        return FILM_ERR_TOTAL_LEN;
    }

    /* Header is now guaranteed present (total_len == 209120 >= 32). */
    film_parse_header(buf, &hdr);

    /* Header-declared pixel length (Req 6.2). */
    if (hdr.pixel_bytes != FILM_PIXEL_BYTES) {
        return FILM_ERR_PIXEL_LEN;
    }

    /* Canvas dimensions (Req 6.3). */
    if (hdr.width != FILM_EXPECTED_WIDTH || hdr.height != FILM_EXPECTED_HEIGHT) {
        return FILM_ERR_DIMS;
    }

    /* Color count — expected six (Req 6.3). */
    if (hdr.color_count != FILM_EXPECTED_COLOR_COUNT) {
        return FILM_ERR_COLOR_COUNT;
    }

    /* colorTable 内容校验（以固件为准）：每个调色板槽必须是固件 color_lut 能识别的
     * 真实设备色值字节（黑 0x00 / 白 0xFF / 黄 0xFC / 红 0xE0 / 蓝 0x03 / 绿 0x1C）。
     * 若写成索引 0..5 等非法值，显示时 color_lut[colorTable[idx]] 会落到未映射项(=0)
     * 导致整屏错色，故在此拒绝，避免"结构校验通过却显示全错"（跨端契约以固件为准）。 */
    for (uint8_t ci = 0; ci < hdr.color_count; ++ci) {
        uint8_t v = hdr.color_table[ci];
        if (v != 0x00u && v != 0xFFu && v != 0xFCu &&
            v != 0xE0u && v != 0x03u && v != 0x1Cu) {
            return FILM_ERR_COLOR_TABLE;
        }
    }

    /* Palette indices in range (Req 6.4). */
    if (!palette_indices_in_range(buf + FILM_HEADER_SIZE_BYTES,
                                  FILM_PIXEL_BYTES,
                                  hdr.color_count)) {
        return FILM_ERR_PALETTE_INDEX;
    }

    return FILM_OK;
}

const char *film_err_str(film_err_t err)
{
    switch (err) {
        case FILM_OK:                return "FILM_OK";
        case FILM_ERR_NULL:          return "FILM_ERR_NULL";
        case FILM_ERR_TOTAL_LEN:     return "FILM_ERR_TOTAL_LEN";
        case FILM_ERR_PIXEL_LEN:     return "FILM_ERR_PIXEL_LEN";
        case FILM_ERR_DIMS:          return "FILM_ERR_DIMS";
        case FILM_ERR_COLOR_COUNT:   return "FILM_ERR_COLOR_COUNT";
        case FILM_ERR_PALETTE_INDEX: return "FILM_ERR_PALETTE_INDEX";
        case FILM_ERR_COLOR_TABLE:   return "FILM_ERR_COLOR_TABLE";
        default:                     return "FILM_ERR_UNKNOWN";
    }
}
