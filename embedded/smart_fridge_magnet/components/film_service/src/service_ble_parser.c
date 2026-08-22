/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/src/service_ble_parser.c
 * Description: Pure BLE command parse / validate / FILE-sequence ordering logic
 *              (Req 9.1/9.2). Validate-before-access bounds/length/checksum
 *              checks, a per-command data-length policy, filename safety and a
 *              pure FILE-sequence state-transition validator. No ESP-IDF deps,
 *              so it is host-testable.
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "service_ble_parser.h"

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

uint8_t ble_cmd_checksum(const uint8_t *buf, size_t len)
{
    uint8_t sum = 0;

    if (buf == NULL)
    {
        return 0;
    }

    for (size_t i = 0; i < len; i++)
    {
        sum = (uint8_t)(sum + buf[i]);
    }
    return sum;
}

ble_parse_err_t ble_cmd_parse(const uint8_t *buf, size_t buf_len,
                              ble_parsed_cmd_t *out)
{
    /* (1) never dereference a NULL buffer or write through a NULL out. */
    if (buf == NULL || out == NULL)
    {
        return BLE_PARSE_ERR_NULL;
    }

    /* (2) minimum length gates EVERY indexed access below. A frame shorter
     * than HEAD+CH+LEN+CHECKSUM cannot possibly be valid; rejecting here is
     * what prevents the out-of-bounds reads the raw path was prone to. */
    if (buf_len < BLE_PARSER_CMD_MIN_LEN)
    {
        return BLE_PARSE_ERR_TOO_SHORT;
    }

    /* (3) head byte. */
    if (buf[0] != BLE_PARSER_CMD_HEAD)
    {
        return BLE_PARSE_ERR_BAD_HEAD;
    }

    /* (4) protocol length-field consistency: the declared data length plus
     * the 4 fixed overhead bytes must exactly equal the received frame length.
     * Because the LEN field is a single byte (<= 255), this also rejects any
     * frame longer than 259 bytes. */
    {
        uint8_t declared = buf[2];
        if ((size_t)declared + (size_t)BLE_PARSER_CMD_OVERHEAD != buf_len)
        {
            return BLE_PARSE_ERR_LEN_MISMATCH;
        }

        /* (5) trailing checksum over every byte except the checksum itself.
         * buf_len >= 4 here, so buf_len - 1 does not underflow. */
        if (ble_cmd_checksum(buf, buf_len - 1u) != buf[buf_len - 1u])
        {
            return BLE_PARSE_ERR_CHECKSUM;
        }

        out->ch   = buf[1];
        out->len  = declared;
        /* &buf[3] is always in-bounds: the DATA region [3, 3+declared) is a
         * subset of [0, buf_len-1). When declared == 0 this points at the
         * checksum byte and must not be dereferenced by the handler. */
        out->data = &buf[3];
    }

    return BLE_PARSE_OK;
}

bool ble_cmd_data_len_ok(uint8_t ch, uint8_t data_len)
{
    switch (ch)
    {
        case BLE_PARSER_CH_FILE_NAME:
            return data_len >= 1u && data_len <= BLE_PARSER_FILENAME_MAX;
        case BLE_PARSER_CH_FILE_LEN:
            return data_len == 4u;
        case BLE_PARSER_CH_FILE_DATA:
            return data_len >= 1u;
        case BLE_PARSER_CH_FILE_START:
        case BLE_PARSER_CH_FILE_STOP:
        default:
            /* START/STOP carry no data of interest; non-FILE opcodes are
             * length-checked by their own handlers. */
            return true;
    }
}

bool ble_filename_is_safe(const uint8_t *data, uint8_t len)
{
    if (data == NULL || len == 0u)
    {
        return false;
    }

    /* Leave room for the NUL terminator appended at index [len]. */
    if (len > BLE_PARSER_FILENAME_MAX)
    {
        return false;
    }

    /* No absolute / rooted path. */
    if (data[0] == (uint8_t)'/' || data[0] == (uint8_t)'\\')
    {
        return false;
    }

    for (uint8_t i = 0; i < len; i++)
    {
        uint8_t c = data[i];

        /* Printable ASCII only: rejects embedded NUL and control bytes. */
        if (c < 0x20u || c >= 0x7Fu)
        {
            return false;
        }

        /* No path separators anywhere. */
        if (c == (uint8_t)'/' || c == (uint8_t)'\\')
        {
            return false;
        }

        /* No parent-directory traversal ("..") anywhere. */
        if (c == (uint8_t)'.' && (uint16_t)(i + 1u) < (uint16_t)len &&
            data[i + 1u] == (uint8_t)'.')
        {
            return false;
        }
    }

    return true;
}

ble_seq_result_t ble_film_trans_step(ble_film_trans_state_t cur,
                                     uint8_t ch, uint8_t data_len,
                                     ble_film_trans_state_t *next_state)
{
    ble_film_trans_state_t next = cur;
    ble_seq_result_t       result;

    switch (ch)
    {
        case BLE_PARSER_CH_FILE_START:
            if ((cur == BLE_FILM_TRANS_IDLE || cur == BLE_FILM_TRANS_STOPPED) &&
                ble_cmd_data_len_ok(ch, data_len))
            {
                next   = BLE_FILM_TRANS_STARTED;
                result = BLE_SEQ_ACCEPT;
            }
            else
            {
                result = BLE_SEQ_REJECT;
            }
            break;

        case BLE_PARSER_CH_FILE_NAME:
            if (cur == BLE_FILM_TRANS_STARTED && ble_cmd_data_len_ok(ch, data_len))
            {
                next   = BLE_FILM_TRANS_RECV_NAME;
                result = BLE_SEQ_ACCEPT;
            }
            else
            {
                result = BLE_SEQ_REJECT;
            }
            break;

        case BLE_PARSER_CH_FILE_LEN:
            if (cur == BLE_FILM_TRANS_RECV_NAME && ble_cmd_data_len_ok(ch, data_len))
            {
                next   = BLE_FILM_TRANS_RECV_LEN;
                result = BLE_SEQ_ACCEPT;
            }
            else
            {
                result = BLE_SEQ_REJECT;
            }
            break;

        case BLE_PARSER_CH_FILE_DATA:
            if ((cur == BLE_FILM_TRANS_RECV_LEN || cur == BLE_FILM_TRANS_RECV_DATA) &&
                ble_cmd_data_len_ok(ch, data_len))
            {
                next   = BLE_FILM_TRANS_RECV_DATA;
                result = BLE_SEQ_ACCEPT;
            }
            else
            {
                result = BLE_SEQ_REJECT;
            }
            break;

        case BLE_PARSER_CH_FILE_STOP:
            if (cur == BLE_FILM_TRANS_RECV_DATA && ble_cmd_data_len_ok(ch, data_len))
            {
                next   = BLE_FILM_TRANS_STOPPED;
                result = BLE_SEQ_ACCEPT;
            }
            else
            {
                result = BLE_SEQ_REJECT;
            }
            break;

        default:
            /* Not part of the FILE transfer sequence; ordering does not apply. */
            return BLE_SEQ_NOT_FILE;
    }

    if (result == BLE_SEQ_ACCEPT && next_state != NULL)
    {
        *next_state = next;
    }
    return result;
}

size_t ble_cmd_build_frame(uint8_t ch,
                           const uint8_t *data, uint8_t data_len,
                           uint8_t *out_buf, size_t out_cap)
{
    /* Full frame length is fixed by data_len: HEAD+CH+LEN + data + CHECKSUM. */
    size_t frame_len = (size_t)data_len + (size_t)BLE_PARSER_CMD_OVERHEAD;

    if (out_buf == NULL)
    {
        return 0;
    }

    /* Refuse to read a NULL payload; refuse to overrun the caller's buffer.
     * Either way we write nothing and report 0 so the caller sends nothing. */
    if (data == NULL && data_len != 0u)
    {
        return 0;
    }
    if (out_cap < frame_len)
    {
        return 0;
    }

    out_buf[0] = BLE_PARSER_CMD_HEAD;
    out_buf[1] = ch;
    out_buf[2] = data_len;

    for (uint8_t i = 0; i < data_len; i++)
    {
        out_buf[3u + i] = data[i];
    }

    /* Checksum covers HEAD+CH+LEN+DATA, i.e. every byte before the checksum.
     * frame_len >= BLE_PARSER_CMD_OVERHEAD (== 4), so frame_len - 1 is safe. */
    out_buf[frame_len - 1u] = ble_cmd_checksum(out_buf, frame_len - 1u);

    return frame_len;
}

size_t ble_build_secret_status_response(uint8_t opcode,
                                        const char *secret_value,
                                        uint8_t *out_buf, size_t out_cap)
{
    /* Secret read-back refusal (Req 9.7 / 15.3, Property 10): the reply carries
     * ONLY a 1-byte set/unset status derived from whether the field is
     * configured. The secret's CONTENT is never inspected beyond its first byte
     * (emptiness) and is never copied into the frame, so the output leaks no
     * bit of the plaintext value. */
    uint8_t status = (secret_value != NULL && secret_value[0] != '\0') ? 1u : 0u;

    return ble_cmd_build_frame(opcode, &status, 1u, out_buf, out_cap);
}
