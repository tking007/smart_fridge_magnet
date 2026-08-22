/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 *
 * FileName : /film_service/inc/service_ble_parser.h
 * Description: Pure (host-testable) BLE command parsing & FILE-sequence
 *              ordering validation, extracted from service_ble.c for
 *              "validate-before-access" hardening.
 *
 *
 * This translation unit is intentionally free of any ESP-IDF / FreeRTOS /
 * GATT dependency: it only uses the C standard library so it compiles and
 * runs on a plain host (see test/host/). The GATT write path in
 * service_ble.c calls into these functions instead of touching raw bytes.
 *
 * The frame layout is preserved from the FrameFilm upstream baseline and is
 * NOT redesigned here — the public BLE UUIDs, opcodes and the
 * FILE_START->FILE_NAME->FILE_LEN->FILE_DATA->FILE_STOP command sequence are
 * unchanged. This is hardening only.
 *********************************************************************/
#ifndef __SERVICE_BLE_PARSER_H__
#define __SERVICE_BLE_PARSER_H__

#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * INCLUDES
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*********************************************************************
 * MACROS
 *
 * Frame layout (unchanged from upstream):
 *   [0] HEAD (0x55) | [1] CH/opcode | [2] LEN (data length) |
 *   [3 .. 3+LEN-1] DATA | [3+LEN] CHECKSUM (sum of all preceding bytes)
 *   => total frame length == LEN + 4.
 */
#define BLE_PARSER_CMD_HEAD      (0x55u)  /* frame head byte                     */
#define BLE_PARSER_CMD_OVERHEAD  (4u)     /* HEAD + CH + LEN + CHECKSUM           */
#define BLE_PARSER_CMD_MIN_LEN   (4u)     /* smallest valid frame (0 data bytes)  */
#define BLE_PARSER_DATA_MAX      (255u)   /* LEN is a single byte                 */

/* FILE-transfer command channels (opcodes). MUST match service_ble.h. */
#define BLE_PARSER_CH_FILE_NAME  (0x00u)
#define BLE_PARSER_CH_FILE_LEN   (0x01u)
#define BLE_PARSER_CH_FILE_DATA  (0x02u)
#define BLE_PARSER_CH_FILE_START (0x03u)
#define BLE_PARSER_CH_FILE_STOP  (0x04u)

/* Largest FILE_NAME payload we accept: the receive buffer is 256 bytes and we
 * append a NUL terminator at index [len], so len must leave room for it. */
#define BLE_PARSER_FILENAME_MAX  (128u)  /* 对齐文件事务上限 FILE_TXN_FILENAME_MAX(128)：
                                          * 避免 129..254 的名字在 FILE_NAME 阶段被接受、
                                          * 却在后续 begin 阶段才因超限失败（口径统一） */

/*********************************************************************
 * TYPEDEFS
 */

/* Frame parse result codes. */
typedef enum {
    BLE_PARSE_OK = 0,
    BLE_PARSE_ERR_NULL,          /* buf == NULL || out == NULL           */
    BLE_PARSE_ERR_TOO_SHORT,     /* buf_len < BLE_PARSER_CMD_MIN_LEN      */
    BLE_PARSE_ERR_BAD_HEAD,      /* buf[0] != HEAD                        */
    BLE_PARSE_ERR_LEN_MISMATCH,  /* LEN field + overhead != buf_len       */
    BLE_PARSE_ERR_CHECKSUM,      /* trailing checksum mismatch            */
} ble_parse_err_t;

/* A validated view into the caller's buffer. `data` points into the original
 * buffer and always addresses `len` in-bounds bytes (data == &buf[3]); it is
 * never NULL when the parse returns BLE_PARSE_OK. */
typedef struct {
    uint8_t        ch;    /* channel / opcode  (buf[1])          */
    uint8_t        len;   /* declared data length (buf[2])       */
    const uint8_t *data;  /* payload start (&buf[3])             */
} ble_parsed_cmd_t;

/* FILE-transfer ordering states. Values MUST match the transfer state machine
 * in service_ble.c. */
typedef enum {
    BLE_FILM_TRANS_IDLE      = 0,
    BLE_FILM_TRANS_STARTED   = 1,
    BLE_FILM_TRANS_RECV_NAME = 2,
    BLE_FILM_TRANS_RECV_LEN  = 3,
    BLE_FILM_TRANS_RECV_DATA = 4,
    BLE_FILM_TRANS_STOPPED   = 5,
} ble_film_trans_state_t;

/* Result of validating one FILE-sequence step. */
typedef enum {
    BLE_SEQ_ACCEPT = 0,  /* valid next step; *next_state updated               */
    BLE_SEQ_REJECT,      /* out-of-order or bad data length; keep state as-is  */
    BLE_SEQ_NOT_FILE,    /* ch is not a FILE-sequence opcode                   */
} ble_seq_result_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/* Protocol checksum: 8-bit sum of the first `len` bytes of `buf`.
 * Returns 0 for a NULL buffer. */
uint8_t ble_cmd_checksum(const uint8_t *buf, size_t len);

/* Bounds-checked frame parse. Validates, and BEFORE the command handler is
 * allowed to access the head/channel/length fields:
 *   1. non-NULL buffer / out
 *   2. minimum frame length (gates every indexed read)
 *   3. head byte
 *   4. protocol length-field consistency (LEN + overhead == buf_len)
 *   5. trailing checksum
 * On BLE_PARSE_OK, *out is filled with a view guaranteed to be in-bounds.
 * This function never reads outside [buf, buf + buf_len). */
ble_parse_err_t ble_cmd_parse(const uint8_t *buf, size_t buf_len,
                              ble_parsed_cmd_t *out);

/* Pure per-command allowed data-length policy. Returns true when `data_len`
 * is acceptable for opcode `ch`. FILE_NAME requires >= 1, FILE_LEN requires
 * exactly 4, FILE_DATA requires >= 1; FILE_START/FILE_STOP and all non-FILE
 * opcodes are permissive (their length, if any, is checked by their own
 * handler). */
bool ble_cmd_data_len_ok(uint8_t ch, uint8_t data_len);

/* Pure filename safety check for FILE_NAME payloads. Rejects empty names,
 * over-long names, absolute/rooted paths, any path separator, parent-dir
 * traversal ("..") and non-printable bytes. `data` need not be
 * NUL-terminated; exactly `len` bytes are inspected. */
bool ble_filename_is_safe(const uint8_t *data, uint8_t len);

/* Pure FILE-sequence transition validator. Enforces BOTH ordering
 * (FILE_START -> FILE_NAME -> FILE_LEN -> FILE_DATA -> FILE_STOP) and the
 * per-command data-length policy. On BLE_SEQ_ACCEPT it writes the next state
 * to *next_state (may be NULL). On BLE_SEQ_REJECT the caller MUST leave the
 * transfer state unchanged. BLE_SEQ_NOT_FILE means `ch` is outside the FILE
 * sequence and ordering does not apply. */
ble_seq_result_t ble_film_trans_step(ble_film_trans_state_t cur,
                                     uint8_t ch, uint8_t data_len,
                                     ble_film_trans_state_t *next_state);

/* Pure outbound response-frame builder (Req 9.4 / Property 8).
 *
 * Serializes exactly ONE notify frame into out_buf using the same on-wire
 * layout the parser validates:
 *   [0] HEAD (0x55) | [1] ch | [2] data_len | [3 .. 3+data_len-1] data |
 *   [3+data_len] CHECKSUM (8-bit sum of every preceding byte)
 *
 * Returns the EXACT frame length written == data_len + BLE_PARSER_CMD_OVERHEAD.
 * The caller MUST notify exactly this many bytes, so the transmitted length
 * always equals the real payload and no trailing / uninitialized bytes are
 * ever sent. Returns 0 (writes nothing) when:
 *   - out_buf == NULL,
 *   - data == NULL while data_len > 0, or
 *   - out_cap is too small to hold the whole frame.
 * Pure: no ESP-IDF / FreeRTOS / GATT dependency, host-testable. */
size_t ble_cmd_build_frame(uint8_t ch,
                           const uint8_t *data, uint8_t data_len,
                           uint8_t *out_buf, size_t out_cap);

/* Pure secret read-back responder (Req 9.7 / 15.3, Property 10).
 *
 * Builds the ONLY reply the device is permitted to emit when a BLE client
 * asks to read a SECRET config field (wifi_password / device_token): a single
 * 1-byte "set / unset" STATUS frame that reveals ONLY whether the field is
 * configured, NEVER its plaintext value.
 *
 *   status = (secret_value != NULL && secret_value[0] != '\0') ? 1 : 0
 *   frame  = [HEAD 0x55][opcode][LEN 1][status][CHECKSUM]   (== 5 bytes)
 *
 * The emitted frame is a pure function of (opcode, is-the-field-configured):
 * the secret's CONTENT never influences a single output byte, so no amount of
 * secret value can leak through this path. `opcode` is echoed into the frame
 * so the reply matches the request. Returns the exact frame length written
 * (== 1 + BLE_PARSER_CMD_OVERHEAD) via ble_cmd_build_frame(), or 0 (writing
 * nothing) when out_buf == NULL or out_cap is too small. `secret_value` may be
 * NULL (treated as unset) and is only inspected for emptiness — its bytes are
 * never copied into the frame. Pure: no ESP-IDF / GATT dependency,
 * host-testable. */
size_t ble_build_secret_status_response(uint8_t opcode,
                                        const char *secret_value,
                                        uint8_t *out_buf, size_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_BLE_PARSER_H__ */
