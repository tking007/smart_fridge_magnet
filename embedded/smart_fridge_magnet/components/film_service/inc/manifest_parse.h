/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/manifest_parse.h
 * Description: 云端版本清单（manifest）解析器：解析字段并强制大小/长度限制
 *   （Req 10.1、10.2）。
 *
 *   顶层为扁平 JSON 对象，必含 version / film_url / size / sha256 / force 五个标量
 *   字段，可选 items[]（一套多张）与 firmware（OTA 信息）。
 *
 *   本模块只做解析与字段级校验；版本门控与 force 强制应用决策在 sync_decision.h。
 *
 *   不依赖 cJSON，内置一个仅需标准 C 的严格 JSON 读取器，只识别本契约所需结构。
 *   这样可与其它纯逻辑模块一样在宿主机直接编译测试，且能在任何分配/拷贝之前就对
 *   总响应大小与各字符串长度施加精确上界（DOM 式解析必须先建整棵树）。目标端与
 *   宿主端共用同一份 manifest_parse.c，无条件编译分叉。
 *
 *********************************************************************/
#ifndef __MANIFEST_PARSE_H__
#define __MANIFEST_PARSE_H__

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
/* 单个 items[] 条目的最坏合法字节数（含键名、引号、逗号与后端可能多带的未知键）。
 * 依据解析器自己允许的上界：name<=31 + url<=255 + size 6 位 + sha256=64，
 * 加键名与标点约 30，再给后端的 "slot":"breakfast" 这类会被跳过但仍占字节的键留余量。 */
#define MANIFEST_ITEM_MAX_BYTES       (512u)

/* 顶层五字段的最坏合法字节数：film_url<=255 + sha256=64 + version/size/force + 键名标点。 */
#define MANIFEST_TOP_MAX_BYTES        (1024u)

/* 允许的 manifest 响应总字节上界（Req 10.1）。超过即在任何解析/拷贝前直接拒绝。
 * 由 MANIFEST_MAX_ITEMS 推导而不写死：写死的上界一旦低于解析器能接受的最坏合法
 * 输入，就会先于解析器拒掉一份合法 manifest，表现为 `manifest too large` 使同步
 * 永久失败，而后端与固件各自看都没错。调整 MANIFEST_MAX_ITEMS 时本上界自动跟随。 */
#define MANIFEST_MAX_RESPONSE_BYTES   (MANIFEST_TOP_MAX_BYTES + \
                                       MANIFEST_MAX_ITEMS * MANIFEST_ITEM_MAX_BYTES)

/* 跳过未知键的任意 JSON 值时允许的最大对象/数组嵌套深度（防御性递归上界）。 */
#define MANIFEST_MAX_JSON_DEPTH       (8u)

/* film_url 允许的最大字符数（不含结尾 '\0'）—— Req 10.1「限制字符串长度」。 */
#define MANIFEST_FILM_URL_MAXLEN      (255u)
#define MANIFEST_URL_BUFSZ            (MANIFEST_FILM_URL_MAXLEN + 1u) /* 256 */

/* sha256 必须恰为 64 位小写十六进制（design.md：64 位小写 hex）。 */
#define MANIFEST_SHA256_HEXLEN        (64u)
#define MANIFEST_SHA256_BUFSZ         (MANIFEST_SHA256_HEXLEN + 1u)   /* 65 */

/* 智能冰箱贴 .film 固定总长度。与 file_txn.h::FILM_FILE_TOTAL_SIZE 及
 * film_validate.h::FILM_FILE_SIZE_BYTES 同值，契约见 docs/contracts/README.md。
 * 此处独立定义以保持本模块 host 自包含（仅需 -I inc 即可单独编译）。 */
#define MANIFEST_STANDARD_FILM_SIZE   (209120u)

/* 一个版本最多携带的画面张数。4 = today/breakfast/lunch/dinner 一套四张，正是
 * service_film.c 按时段选图所需的全集。刻意不留余量：每多一张就是一次额外的整份
 * 下载，而按时段显示的类别本来就只有这四种。 */
#define MANIFEST_MAX_ITEMS            (4u)

/* 条目落盘文件名的最大字符数（不含结尾 '\0'）。31 足够容纳约定的 "1_today.film" /
 * "2_breakfast.film" 等名字，同时把每个条目的结构体开销压在 32 字节。 */
#define MANIFEST_ITEM_NAME_MAXLEN     (31u)
#define MANIFEST_ITEM_NAME_BUFSZ      (MANIFEST_ITEM_NAME_MAXLEN + 1u)  /* 32 */

/* 固件版本串上限。语义化版本加预发布后缀 31 字节足够；超长即拒绝而不截断 ——
 * 截断后的版本串会在日志里冒充另一个版本。 */
#define MANIFEST_FW_VERSION_MAXLEN    (31u)
#define MANIFEST_FW_VERSION_BUFSZ     (MANIFEST_FW_VERSION_MAXLEN + 1u)  /* 32 */

/* 固件大小上限 = app 分区容量（partitions.csv 里 ota_0/ota_1 各 1920K）。
 * 与后端 firmware.MAX_FIRMWARE_BYTES 必须一致：不一致时后端认为可下发、设备下载完
 * 才拒绝，等于每次唤醒白耗一次 1.9MB 的射频。 */
#define MANIFEST_FW_MAX_BYTES         (1920u * 1024u)

/*********************************************************************
 * TYPEDEFS
 */
/*
 * items[] 中的一个条目（一套多张）。
 *
 * 与顶层的 film_url/size/sha256 同强度校验，并额外要求 name 是安全基名：name 会被
 * 直接拼成落盘路径，一个含 '/' 或 ".." 的名字就是一次任意路径写入。
 */
typedef struct {
    char     name[MANIFEST_ITEM_NAME_BUFSZ];    /* 落盘文件名，如 1_today.film；安全基名 + .film 后缀 */
    char     url[MANIFEST_URL_BUFSZ];           /* HTTPS 地址，长度 <= 255，NUL 结尾            */
    uint32_t size;                              /* 必须 == 209120                              */
    char     sha256_hex[MANIFEST_SHA256_BUFSZ]; /* 恰 64 位小写十六进制，NUL 结尾               */
} manifest_item_t;

/*
 * 可选的固件升级信息（Wi-Fi OTA）。随 manifest 一并下发，设备在同一次唤醒里顺手
 * 判断是否升级，不额外拉一个接口：设备电池供电、平时深睡，每多一次往返都要多醒
 * 一段射频。
 *
 * has_update == false 表示后端这次没给（已是最新，或后端没有固件）。
 * 与 items[] 同一条兼容规则：字段缺失走原路径，出现了就必须完全合法。
 */
typedef struct {
    bool     has_update;                       /* false 时其余字段无意义                        */
    char     version[MANIFEST_FW_VERSION_BUFSZ]; /* 如 "1.2.0"，仅日志用，门控在后端做          */
    char     url[MANIFEST_URL_BUFSZ];          /* HTTPS 地址                                   */
    uint32_t size;                             /* 字节数，用于容量与合理性检查                  */
    char     sha256_hex[MANIFEST_SHA256_BUFSZ];/* 恰 64 位小写十六进制                          */
} manifest_firmware_t;

/*
 * 解析成功后的 manifest。仅在 manifest_parse() 返回 MANIFEST_OK 时 *out 才有效。
 *
 * items[] 是可选字段：缺失时 item_count == 0，五个既有字段仍按原规则校验并生效，
 * 走单张兼容路径。这条兼容性不能破 —— 后端可能还没升级，破了就是新固件连不上旧后端。
 */
typedef struct {
    uint32_t version;                          /* 版本号（任意 uint32；语义门控见 sync_decision）*/
    char     film_url[MANIFEST_URL_BUFSZ];     /* HTTPS 地址，长度 <= 255，NUL 结尾            */
    uint32_t size;                             /* 标准版必须 == 209120（MANIFEST_STANDARD_...）*/
    char     sha256_hex[MANIFEST_SHA256_BUFSZ];/* 恰 64 位小写十六进制，NUL 结尾               */
    bool     force;                            /* true=强制应用当前版本                        */
    uint8_t  item_count;                       /* 0 = 后端没给 items[]，走单张兼容路径         */
    manifest_item_t items[MANIFEST_MAX_ITEMS]; /* 一套多张；仅前 item_count 项有效             */
    manifest_firmware_t firmware;              /* 可选的固件升级信息；has_update=false 即没有   */
} manifest_t;

/*
 * 解析/校验结果。各失败原因彼此可区分，便于定位首个失败并记录 last_sync_error。
 */
typedef enum {
    MANIFEST_OK = 0,               /* 五字段齐备且全部合法                                   */
    MANIFEST_ERR_NULL,             /* json 或 out 指针为 NULL                                */
    MANIFEST_ERR_EMPTY,            /* json_len == 0                                          */
    MANIFEST_ERR_TOO_LARGE,        /* json_len > MANIFEST_MAX_RESPONSE_BYTES（Req 10.1）      */
    MANIFEST_ERR_JSON,             /* JSON 语法非法 / 非对象 / 未终止 / 尾部有多余内容        */
    MANIFEST_ERR_MISSING_FIELD,    /* 五个必需字段中有缺失                                   */
    MANIFEST_ERR_TYPE,             /* 某字段 JSON 类型错误（如 version 是字符串）             */
    MANIFEST_ERR_DUP_FIELD,        /* 某已知键重复出现                                       */
    MANIFEST_ERR_VERSION_RANGE,    /* version 非非负整数 / 溢出 uint32                        */
    MANIFEST_ERR_SIZE_RANGE,       /* size 非非负整数 / 溢出 uint32                           */
    MANIFEST_ERR_SIZE_MISMATCH,    /* size 合法但 != 209120（标准版校验失败，Req 10.2）       */
    MANIFEST_ERR_URL_TOO_LONG,     /* film_url 超过 255 字符（Req 10.1）                      */
    MANIFEST_ERR_URL_SCHEME,       /* film_url 不是 https:// 或缺少主机部分                   */
    MANIFEST_ERR_SHA256_FORMAT,    /* sha256 不是恰好 64 位小写十六进制                       */
    /* items[] 相关错误码。任一条目不合法即拒绝整份 manifest，而不静默丢掉那一条：
     * 半套图比没有更糟，设备会在某个时段显示过期详情页而人看不出。 */
    MANIFEST_ERR_ITEMS_TYPE,       /* items 存在但不是数组 / 元素不是对象                     */
    MANIFEST_ERR_ITEMS_TOO_MANY,   /* items 条目数超过 MANIFEST_MAX_ITEMS                     */
    MANIFEST_ERR_ITEM_FIELD,       /* 某条目缺字段 / 字段类型错 / 键重复                      */
    MANIFEST_ERR_ITEM_NAME,        /* 某条目 name 不是安全基名或不以 .film 结尾               */
    MANIFEST_ERR_ITEM_URL,         /* 某条目 url 超长或非 https                               */
    MANIFEST_ERR_ITEM_SIZE,        /* 某条目 size 非法 / != 209120                            */
    MANIFEST_ERR_ITEM_SHA256,      /* 某条目 sha256 不是恰 64 位小写十六进制                   */
    MANIFEST_ERR_FW_TYPE,          /* firmware 存在但不是对象                                 */
    MANIFEST_ERR_FW_FIELD,         /* firmware 缺字段 / 字段类型错 / 键重复                    */
    MANIFEST_ERR_FW_VERSION,       /* firmware.version 缺失或超长                             */
    MANIFEST_ERR_FW_URL,           /* firmware.url 超长或非 https                             */
    MANIFEST_ERR_FW_SIZE,          /* firmware.size 为 0 或超过 app 分区容量                  */
    MANIFEST_ERR_FW_SHA256,        /* firmware.sha256 不是恰 64 位小写十六进制                 */
} manifest_err_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 解析并校验云端 manifest JSON（字段校验 + 大小/长度限制）。
 *
 * @p json 指向长度为 @p json_len 的字节缓冲（不要求 NUL 结尾）。解析器：
 *   1) 在任何拷贝前拒绝空响应与超过 MANIFEST_MAX_RESPONSE_BYTES 的响应（Req 10.1）；
 *   2) 要求顶层为 JSON 对象且含 version/film_url/size/sha256/force 五个键；未知键被
 *      安全地有界跳过，重复的已知键被拒绝；
 *   3) 逐字段校验：version/size 为可容纳于 uint32 的非负整数；film_url 为长度
 *      <= 255 的 https:// 地址；sha256 恰 64 位小写十六进制；force 为布尔；
 *      size 语义上必须 == 209120，否则 MANIFEST_ERR_SIZE_MISMATCH（Req 10.2）；
 *   4) 可选 items[]：出现时必须是对象数组，条目数 <= MANIFEST_MAX_ITEMS，每条须含
 *      name/url/size/sha256 并通过与顶层同强度的校验（name 另加安全基名 + .film
 *      后缀规则），条目内未知键被安全跳过。任一条目不合法即拒绝整份 manifest。
 *      items 缺失或为空数组时 out->item_count == 0，调用方走单张兼容路径。
 *
 * 仅当返回 MANIFEST_OK 时才写入 *out；任何失败都不改动 *out（除非本身为 NULL）。
 * 解析严格按 [json, json+json_len) 边界读取，绝不越界。
 *
 * @param json     manifest 字节缓冲（可不含结尾 '\0'）
 * @param json_len 缓冲字节长度
 * @param out      解析结果输出（成功时写入；不可为 NULL）
 * @return manifest_err_t MANIFEST_OK 或第一个失败原因
 */
manifest_err_t manifest_parse(const char *json, size_t json_len, manifest_t *out);

/**
 * @brief 返回 manifest_err_t 的可读字符串（用于日志/测试诊断）。
 * @param err 解析结果
 * @return const char* 静态字符串，永不为 NULL
 */
const char *manifest_err_str(manifest_err_t err);

/* 以下为字段级校验谓词，均无副作用、无全局状态，可被 host 测试单独驱动。 */

/**
 * @brief 判断以 '\0' 结尾的字符串是否为恰 64 位小写十六进制（[0-9a-f]）。
 * @param s NUL 结尾字符串（可为 NULL → false）
 * @return true 当且仅当 s 恰含 64 个小写十六进制字符且随后为 '\0'
 */
bool manifest_is_lower_hex64(const char *s);

/**
 * @brief 判断以 '\0' 结尾的 URL 是否为受支持的 HTTPS 地址。
 *
 * 规则：以大小写不敏感的 "https://" 方案起始，且方案后至少有一个主机字符。
 *
 * @param s NUL 结尾字符串（可为 NULL → false）
 * @return true 表示合法 HTTPS URL
 */
bool manifest_url_is_https(const char *s);

/**
 * @brief 判断 size 是否为标准版固定长度（== 209120）。
 * @param size 待判定的字节数
 * @return true 当且仅当 size == MANIFEST_STANDARD_FILM_SIZE
 */
bool manifest_size_is_standard(uint32_t size);

/**
 * @brief 本次 manifest 的 items[] 里是否存在名为 @p name 的条目。
 *
 * 用于守住一条不变量：永远不要删除自己刚刚下载并提交的文件。整套下载路径
 * （service_wifi.c 的 wifi_download_film_set）收尾时会清理单张落盘名 current.film；
 * 若某次发布把单张也表达成 items[] 的一条（name 恰为 "current.film"），那次清理就会
 * 删掉本次刚提交的图 —— 版本号已推进、图却没了，下一轮判「已是最新」不再下载，
 * 该次发布永久丢失而两端日志都显示成功。把不变量放在固件侧，就不必依赖对端行为。
 *
 * 比较是大小写敏感的 strcmp：FAT 上的大小写行为不该由这个查询函数去猜。
 *
 * @param m    已解析的 manifest（NULL → false）
 * @param name NUL 结尾的文件名（NULL → false）
 * @return true 当且仅当 items[0..item_count-1] 中存在 name 完全相同的条目
 */
bool manifest_contains_item_name(const manifest_t *m, const char *name);

/**
 * @brief 判断 items[] 条目的 name 是否为可安全落盘的 .film 基名。
 *
 * name 会被直接拼进落盘路径（FILM_DIR/<name>），故用白名单而非黑名单：
 *   - 长度 1..MANIFEST_ITEM_NAME_MAXLEN（31）；
 *   - 只允许 [0-9A-Za-z._-]，从而一并排除 '/'、'\\'、控制字符、空格与所有多字节
 *     字符。路径穿越的变体太多（'/'、'\\'、URL 编码、UTF-8 同形字），逐一拉黑必定漏；
 *   - 不是 "." 也不是 ".."（它们通过上面的字符集，但语义上是目录）；
 *   - 必须以 ".film" 结尾：落盘目录里只应存在 .film，别的后缀要么是后端出错，
 *     要么是有人在试探写入路径。
 *
 * @param s NUL 结尾字符串（可为 NULL → false）
 * @return true 表示可安全用作落盘基名
 */
bool manifest_item_name_is_safe(const char *s);

#ifdef __cplusplus
}
#endif

#endif /* __MANIFEST_PARSE_H__ */
