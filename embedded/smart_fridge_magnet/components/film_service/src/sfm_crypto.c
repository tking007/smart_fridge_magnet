/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/sfm_crypto.c
 * Description: Application-layer AES-128-CTR encryption of sensitive
 *   provisioning fields, keyed from the reused SFM_BLE_KEY (Req 15.1).
 *   Scheme, threat model and backend-seam rationale live in sfm_crypto.h.
 *
 *   Blob layout [nonce(12)][ciphertext(len)]. The 16-byte AES-CTR counter block
 *   is nonce(12) || counter(4, big-endian), counter starting at 0.
 *
 *   Backend seam: on target AES-128-CTR comes from ESP-IDF PSA Crypto and random
 *   bytes from esp_fill_random; with -DSFM_CRYPTO_HOST_TEST both resolve to
 *   test/host/sfm_crypto_backend_host.h so this exact file round-trips on host.
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "sfm_crypto.h"

#include <string.h>

#include "sys_cfg.h"          /* SYS_BLE_DEFAULT_KEY ("SFM_BLE_KEY") */

#if defined(SFM_CRYPTO_HOST_TEST)
#  include "sfm_crypto_backend_host.h"   /* test-only AES + RNG seams */
#else
   /* AES-128-CTR 经 PSA Crypto 提供：mbedtls 4.x 无传统 mbedtls_aes_* 低层 API。 */
#  include "psa/crypto.h"
#  include "esp_random.h"
#endif

/*********************************************************************
 * LOCAL FUNCTIONS — backend seams
 */
/*
 * AES-128 single-block ECB encrypt（CTR 密钥流原语），仅宿主测试路径使用。
 * 目标端走 PSA 的 AES-128-CTR：PSA 的 ECB 可能默认禁用，而 CTR 已确认启用
 * （CONFIG_MBEDTLS_CIPHER_MODE_CTR=1）。
 * Returns 0 on success, non-zero on backend failure.
 */
#if defined(SFM_CRYPTO_HOST_TEST)
static int sfm_aes128_block_encrypt(const uint8_t key[SFM_CRYPTO_KEY_LEN],
                                    const uint8_t in[SFM_CRYPTO_BLOCK_LEN],
                                    uint8_t out[SFM_CRYPTO_BLOCK_LEN])
{
    return sfm_crypto_host_aes128_ecb_encrypt(key, in, out);
}
#endif

/*
 * Fill @p buf with @p len cryptographically-intended random bytes.
 * Returns 0 on success, non-zero on backend failure.
 */
static int sfm_fill_random(uint8_t *buf, size_t len)
{
#if defined(SFM_CRYPTO_HOST_TEST)
    return sfm_crypto_host_fill_random(buf, len);
#else
    esp_fill_random(buf, len);   /* void-returning; treated as infallible */
    return 0;
#endif
}

/*********************************************************************
 * LOCAL FUNCTIONS — helpers
 */
/*
 * Best-effort secret wipe that the optimizer may not elide (volatile).
 */
static void sfm_secure_zero(void *p, size_t n)
{
    volatile uint8_t *v = (volatile uint8_t *)p;
    while (n--) {
        *v++ = 0u;
    }
}

/*
 * Derive the 16-byte AES-128 key from the reused SFM_BLE_KEY ASCII string.
 *
 * Deliberately a simple deterministic stretch rather than a password-hardening
 * KDF: the key string is compiled in, so anyone holding the firmware image
 * recovers it regardless of stretching (see sfm_crypto.h threat model). Each
 * output byte folds the cycled key string with a fixed domain-separation salt
 * and the byte position, so the result is not merely the raw ASCII bytes.
 */
static void sfm_derive_key(uint8_t key[SFM_CRYPTO_KEY_LEN])
{
    /* Fixed domain-separation salt (not secret; documents the derivation). */
    static const uint8_t salt[SFM_CRYPTO_KEY_LEN] = {
        0x53u, 0x46u, 0x4Du, 0x2Du, 0x43u, 0x52u, 0x59u, 0x50u,  /* "SFM-CRYP" */
        0x54u, 0x4Fu, 0x2Du, 0x4Bu, 0x44u, 0x46u, 0x31u, 0x00u   /* "TO-KDF1\0" */
    };
    static const char *src = SYS_BLE_DEFAULT_KEY;
    size_t n = strlen(src);

    for (size_t i = 0u; i < SFM_CRYPTO_KEY_LEN; ++i) {
        uint8_t k = (n != 0u) ? (uint8_t)src[i % n] : 0u;
        key[i] = (uint8_t)(k ^ salt[i] ^ (uint8_t)(i * 0x1Bu));
    }
}

/*
 * AES-128-CTR keystream XOR. Reads the 12-byte @p nonce, then for each 16-byte
 * block encrypts nonce||counter and XORs the keystream over @p in -> @p out.
 * Used for BOTH directions (CTR is symmetric). @p in and @p out may alias only
 * if identical (not used here). Returns an sfm_crypto_err_t.
 */
static sfm_crypto_err_t sfm_ctr_xor(const uint8_t key[SFM_CRYPTO_KEY_LEN],
                                    const uint8_t nonce[SFM_CRYPTO_NONCE_LEN],
                                    const uint8_t *in, uint8_t *out, size_t len)
{
#if defined(SFM_CRYPTO_HOST_TEST)
    /* 宿主测试：手写逐块 CTR，密钥流 = AES-ECB(nonce||counter)，counter 从 0 起、
     * 尾部 4 字节大端自增。 */
    uint8_t counter[SFM_CRYPTO_BLOCK_LEN];
    uint8_t keystream[SFM_CRYPTO_BLOCK_LEN];
    uint32_t block_index = 0u;
    size_t pos = 0u;
    sfm_crypto_err_t result = SFM_CRYPTO_OK;

    memcpy(counter, nonce, SFM_CRYPTO_NONCE_LEN);

    while (pos < len) {
        size_t chunk = len - pos;
        if (chunk > SFM_CRYPTO_BLOCK_LEN) {
            chunk = SFM_CRYPTO_BLOCK_LEN;
        }

        /* Big-endian 32-bit block counter in the trailing 4 bytes. */
        counter[12] = (uint8_t)(block_index >> 24);
        counter[13] = (uint8_t)(block_index >> 16);
        counter[14] = (uint8_t)(block_index >> 8);
        counter[15] = (uint8_t)(block_index);

        if (sfm_aes128_block_encrypt(key, counter, keystream) != 0) {
            result = SFM_CRYPTO_ERR_BACKEND;
            break;
        }

        for (size_t i = 0u; i < chunk; ++i) {
            out[pos + i] = (uint8_t)(in[pos + i] ^ keystream[i]);
        }

        pos += chunk;
        block_index++;
    }

    sfm_secure_zero(keystream, sizeof keystream);
    sfm_secure_zero(counter, sizeof counter);
    return result;
#else
    /* 目标端：PSA AES-128-CTR。IV = nonce(12) || 0x00000000（大端计数器初值 0）。
     * PSA 把 16 字节 IV 视为初始计数块并整体大端自增；数据远小于 2^32 块，故与
     * (nonce||counter) 的逐块方案等价。CTR 加解密同为密钥流异或，两个方向都用
     * encrypt_setup + 显式 set_iv。 */
    psa_status_t st;
    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;
    psa_key_id_t kid = 0;
    psa_cipher_operation_t op = PSA_CIPHER_OPERATION_INIT;
    uint8_t iv[SFM_CRYPTO_BLOCK_LEN];
    size_t produced = 0u;
    size_t fin = 0u;
    sfm_crypto_err_t result = SFM_CRYPTO_OK;

    if (psa_crypto_init() != PSA_SUCCESS) {
        return SFM_CRYPTO_ERR_BACKEND;
    }

    memcpy(iv, nonce, SFM_CRYPTO_NONCE_LEN);
    iv[12] = 0u;
    iv[13] = 0u;
    iv[14] = 0u;
    iv[15] = 0u;

    psa_set_key_type(&attr, PSA_KEY_TYPE_AES);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_ENCRYPT);
    psa_set_key_algorithm(&attr, PSA_ALG_CTR);
    psa_set_key_bits(&attr, 128u);
    if (psa_import_key(&attr, key, SFM_CRYPTO_KEY_LEN, &kid) != PSA_SUCCESS) {
        sfm_secure_zero(iv, sizeof iv);
        return SFM_CRYPTO_ERR_BACKEND;
    }

    if (psa_cipher_encrypt_setup(&op, kid, PSA_ALG_CTR) != PSA_SUCCESS) {
        result = SFM_CRYPTO_ERR_BACKEND;
    } else if (psa_cipher_set_iv(&op, iv, sizeof iv) != PSA_SUCCESS) {
        result = SFM_CRYPTO_ERR_BACKEND;
    } else {
        if (len > 0u) {
            st = psa_cipher_update(&op, in, len, out, len, &produced);
            if (st != PSA_SUCCESS || produced > len) {
                result = SFM_CRYPTO_ERR_BACKEND;
            }
        }
        if (result == SFM_CRYPTO_OK) {
            st = psa_cipher_finish(&op, out + produced, len - produced, &fin);
            if (st != PSA_SUCCESS || (produced + fin) != len) {
                result = SFM_CRYPTO_ERR_BACKEND;
            }
        }
    }

    psa_cipher_abort(&op);
    psa_destroy_key(kid);
    sfm_secure_zero(iv, sizeof iv);
    return result;
#endif
}

/*********************************************************************
 * GLOBAL FUNCTIONS
 */
sfm_crypto_err_t sfm_field_encrypt(const uint8_t *plain, size_t len,
                                   uint8_t *out, size_t out_cap, size_t *out_len)
{
    uint8_t key[SFM_CRYPTO_KEY_LEN];
    size_t need;
    sfm_crypto_err_t rc;

    if (out == NULL || out_len == NULL) {
        return SFM_CRYPTO_ERR_INVAL;
    }
    if (plain == NULL && len != 0u) {
        return SFM_CRYPTO_ERR_INVAL;
    }

    need = len + SFM_CRYPTO_OVERHEAD;
    if (need < len) {                 /* size_t overflow guard */
        return SFM_CRYPTO_ERR_INVAL;
    }
    if (out_cap < need) {
        return SFM_CRYPTO_ERR_BUFFER_TOO_SMALL;
    }

    /* Fresh random nonce written directly into the output prefix. */
    if (sfm_fill_random(out, SFM_CRYPTO_NONCE_LEN) != 0) {
        return SFM_CRYPTO_ERR_BACKEND;
    }

    sfm_derive_key(key);
    rc = sfm_ctr_xor(key, out /* nonce */, plain, out + SFM_CRYPTO_NONCE_LEN, len);
    sfm_secure_zero(key, sizeof key);

    if (rc != SFM_CRYPTO_OK) {
        return rc;
    }

    *out_len = need;
    return SFM_CRYPTO_OK;
}

sfm_crypto_err_t sfm_field_decrypt(const uint8_t *cipher, size_t len,
                                   uint8_t *out, size_t out_cap, size_t *out_len)
{
    uint8_t key[SFM_CRYPTO_KEY_LEN];
    size_t plain_len;
    sfm_crypto_err_t rc;

    if (cipher == NULL || out_len == NULL) {
        return SFM_CRYPTO_ERR_INVAL;
    }
    if (len < SFM_CRYPTO_OVERHEAD) {  /* must at least contain the nonce */
        return SFM_CRYPTO_ERR_INVAL;
    }

    plain_len = len - SFM_CRYPTO_OVERHEAD;
    if (out == NULL && plain_len != 0u) {
        return SFM_CRYPTO_ERR_INVAL;
    }
    if (out_cap < plain_len) {
        return SFM_CRYPTO_ERR_BUFFER_TOO_SMALL;
    }

    sfm_derive_key(key);
    rc = sfm_ctr_xor(key, cipher /* nonce */, cipher + SFM_CRYPTO_NONCE_LEN,
                     out, plain_len);
    sfm_secure_zero(key, sizeof key);

    if (rc != SFM_CRYPTO_OK) {
        return rc;
    }

    *out_len = plain_len;
    return SFM_CRYPTO_OK;
}

const char *sfm_crypto_err_str(sfm_crypto_err_t err)
{
    switch (err) {
        case SFM_CRYPTO_OK:                  return "SFM_CRYPTO_OK";
        case SFM_CRYPTO_ERR_INVAL:           return "SFM_CRYPTO_ERR_INVAL";
        case SFM_CRYPTO_ERR_BUFFER_TOO_SMALL:return "SFM_CRYPTO_ERR_BUFFER_TOO_SMALL";
        case SFM_CRYPTO_ERR_BACKEND:         return "SFM_CRYPTO_ERR_BACKEND";
        default:                             return "SFM_CRYPTO_ERR_UNKNOWN";
    }
}
