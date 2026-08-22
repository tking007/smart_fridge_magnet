/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/sfm_crypto.h
 * Description: App-layer encryption for sensitive provisioning fields (Wi-Fi
 *   password / device token) before they are persisted to NVS (Req 15.1).
 *
 *   SCHEME: AES-128-CTR under a key derived from the reused SFM_BLE_KEY
 *   (sys_cfg.h SYS_BLE_DEFAULT_KEY). Blobs are self-describing —
 *   out = [nonce 12B][ciphertext len B], out_len = len + 12. CTR is a stream
 *   mode, so no padding and decrypt is the same keystream XOR as encrypt. The
 *   prepended random nonce makes the blob never byte-equal to the plaintext and
 *   makes repeated encryption of one field yield different blobs.
 *
 *   THREAT MODEL — read before trusting this. It only raises the bar against
 *   PASSIVE exposure (BLE sniffing of the provisioning write, casual inspection
 *   of NVS dumps or serial logs). The key is baked into every firmware image, so
 *   anyone who can read the binary or device flash recovers it and can decrypt.
 *   There is no authentication tag (not AEAD): values are hidden, tampering is
 *   not detected. This is not a substitute for TLS on the wire (Req 10/11 keep
 *   manifest/.film traffic on HTTPS) nor for a secure element at rest.
 *
 *   PORTABILITY: depends only on plain C plus two backend seams (AES-128 block
 *   encrypt, random bytes). On target they resolve to ESP-IDF mbedtls and
 *   esp_fill_random; with -DSFM_CRYPTO_HOST_TEST they resolve to host-supplied
 *   functions (test/host/sfm_crypto_backend_host.h), so the identical mode and
 *   key-derivation logic round-trips off-target.
 *
 *********************************************************************/
#ifndef __SFM_CRYPTO_H__
#define __SFM_CRYPTO_H__

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
/* Random nonce prepended to every ciphertext blob (12 bytes). Combined with a
 * 4-byte big-endian block counter it forms the 16-byte AES-CTR counter block. */
#define SFM_CRYPTO_NONCE_LEN   (12u)

/* Fixed per-blob overhead = the prepended nonce. */
#define SFM_CRYPTO_OVERHEAD    (SFM_CRYPTO_NONCE_LEN)

/* AES-128 primitive sizes. */
#define SFM_CRYPTO_KEY_LEN     (16u)
#define SFM_CRYPTO_BLOCK_LEN   (16u)

/* Ciphertext blob length produced for a plaintext of @p plain_len bytes. */
#define SFM_CRYPTO_CIPHERTEXT_LEN(plain_len) ((size_t)(plain_len) + SFM_CRYPTO_OVERHEAD)

/*********************************************************************
 * TYPEDEFS
 */
/* Result codes. Self-contained (no dependency on esp_err.h) so the module and
 * its property test build identically on host and on target. */
typedef enum {
    SFM_CRYPTO_OK = 0,                    /* success                                    */
    SFM_CRYPTO_ERR_INVAL = -1,            /* NULL args or malformed length              */
    SFM_CRYPTO_ERR_BUFFER_TOO_SMALL = -2, /* out_cap smaller than required output       */
    SFM_CRYPTO_ERR_BACKEND = -3,          /* AES or RNG backend reported a failure       */
} sfm_crypto_err_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */
/**
 * @brief 使用复用的 SFM_BLE_KEY 加密敏感字段（AES-128-CTR，密文含随机 nonce）
 *
 * Writes SFM_CRYPTO_CIPHERTEXT_LEN(len) bytes to @p out: a fresh random 12-byte
 * nonce followed by the keystream-XOR ciphertext. This blob is what the
 * parameter service persists to NVS; it is always 12 bytes longer than the
 * plaintext and therefore never byte-equal to it.
 *
 * @param plain   plaintext bytes (may be NULL only when @p len == 0)
 * @param len     plaintext length in bytes (0 is allowed, e.g. open network)
 * @param out     output buffer for the ciphertext blob
 * @param out_cap capacity of @p out in bytes; must be >= len + SFM_CRYPTO_OVERHEAD
 * @param out_len [out] receives the number of bytes written (len + overhead)
 * @return SFM_CRYPTO_OK on success, otherwise an sfm_crypto_err_t error code
 */
sfm_crypto_err_t sfm_field_encrypt(const uint8_t *plain, size_t len,
                                   uint8_t *out, size_t out_cap, size_t *out_len);

/**
 * @brief 解密由 sfm_field_encrypt 产生的密文，还原敏感字段明文
 *
 * Reads the leading 12-byte nonce from @p cipher, regenerates the CTR keystream
 * and writes the recovered plaintext (@p len - SFM_CRYPTO_OVERHEAD bytes) to
 * @p out.
 *
 * @param cipher  ciphertext blob (nonce || ciphertext); must be >= overhead
 * @param len     ciphertext blob length in bytes (must be >= SFM_CRYPTO_OVERHEAD)
 * @param out     output buffer for the recovered plaintext (may be NULL only
 *                when the recovered length is 0)
 * @param out_cap capacity of @p out in bytes; must be >= len - SFM_CRYPTO_OVERHEAD
 * @param out_len [out] receives the recovered plaintext length
 * @return SFM_CRYPTO_OK on success, otherwise an sfm_crypto_err_t error code
 */
sfm_crypto_err_t sfm_field_decrypt(const uint8_t *cipher, size_t len,
                                   uint8_t *out, size_t out_cap, size_t *out_len);

/**
 * @brief 返回 sfm_crypto_err_t 的可读字符串（日志/测试诊断用，永不为 NULL）
 */
const char *sfm_crypto_err_str(sfm_crypto_err_t err);

#ifdef __cplusplus
}
#endif

#endif /* __SFM_CRYPTO_H__ */
