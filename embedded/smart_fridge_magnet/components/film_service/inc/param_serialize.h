/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/param_serialize.h
 * Description: NVS 参数的版本化序列化 / 反序列化 / 版本迁移纯逻辑（Req 19.1、19.2）。
 *   无副作用、无全局状态、不依赖 ESP-IDF/NVS/FreeRTOS，可在宿主机直接测试。
 *   本模块不触碰 NVS 与持久化本身，只做「结构体 <-> 字节记录」与值级迁移。
 *
 *   字节记录格式（固定小端）：
 *     header(8B): magic "SFMP"(4) | schema_version:u16 | body_len:u16
 *     body      : 各字段按固定顺序小端打包（顺序见 param_serialize.c）
 *   schema_version 只是便于快速探测的镜像，body 内的 config_version 才是权威值。
 *   序列化恒写当前布局，值级迁移由 param_migrate() 依 config_version 完成，与
 *   二进制布局演进解耦。
 *
 *   机密处理（Req 15.1）：两个明文机密字段（network.wifi_password /
 *   network.device_token）一律写 0，只序列化 secrets 里的落盘密文，因此字节记录
 *   中不含任何机密明文。运行时明文由 service_param.c 载入后另行解密还原。
 *
 *********************************************************************/
#ifndef __PARAM_SERIALIZE_H__
#define __PARAM_SERIALIZE_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "service_param.h"   /* ServiceParam_Def_t + field-capacity macros + SFM_PARAM_CONFIG_VERSION */

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* 序列化记录当前 schema 版本。镜像结构体的 SFM_PARAM_CONFIG_VERSION，两者不漂移。 */
#define PARAM_SCHEMA_VERSION_CURRENT   (SFM_PARAM_CONFIG_VERSION)

/* 记录前缀的 4 字节魔数 'S','F','M','P'。 */
#define PARAM_SERIALIZE_MAGIC0   ((uint8_t)'S')
#define PARAM_SERIALIZE_MAGIC1   ((uint8_t)'F')
#define PARAM_SERIALIZE_MAGIC2   ((uint8_t)'M')
#define PARAM_SERIALIZE_MAGIC3   ((uint8_t)'P')

/* 固定记录头长度：magic(4) + schema_version(u16) + body_len(u16)。 */
#define PARAM_SERIALIZE_HEADER_LEN   (8u)

/* v3 新增的唤醒时间表字段占用 15 字节。单列出来以保留 v2 的精确 body 长度，
 * 否则升级时无法在不清空 SSID、密钥与同步版本的前提下读取旧记录。 */
#define PARAM_SERIALIZE_WAKE_FIELDS_LEN                                  \
    (   1u                             /* sleep.wake_slot_count        */ \
      + (2u * WAKE_SCHEDULE_MAX_SLOTS) /* sleep.wake_slots[]           */ \
      + 2u )                           /* sleep.wake_fallback_min      */

/* 当前字段布局的精确 body 字节数。完全由字段容量宏推导，故容量变更时自动
 * 跟随。两个明文机密字段占位但序列化为 0（明文不落盘）；secrets 密文按容量原样打包。 */
#define PARAM_SERIALIZE_BODY_LEN                                                \
    (   1u                             /* factory_flag                        */ \
      + 4u                             /* config_version                      */ \
      + 4u + 1u + 1u                   /* film: current_file_id/load/mode      */ \
      + 1u + 1u + 2u                   /* sleep: mode/auto/time                */ \
      + PARAM_SERIALIZE_WAKE_FIELDS_LEN /* v3 sleep wake schedule               */ \
      + 1u                             /* network.wifi_enable                  */ \
      + SFM_WIFI_SSID_MAX              /* network.wifi_ssid                    */ \
      + SFM_WIFI_PASSWORD_MAX          /* network.wifi_password -> ZEROS       */ \
      + SFM_FILM_API_URL_MAX           /* network.film_api_url                 */ \
      + SFM_MANIFEST_URL_MAX           /* network.manifest_url                 */ \
      + SFM_DEVICE_ID_MAX              /* network.device_id                    */ \
      + SFM_DEVICE_TOKEN_MAX           /* network.device_token -> ZEROS        */ \
      + 4u + 4u                        /* sync: poll_interval/last_applied     */ \
      + SFM_SYNC_ERR_MAX               /* sync.last_sync_error                 */ \
      + 2u + 2u                        /* secrets: wifi_pw_len/device_tok_len  */ \
      + SFM_WIFI_PASSWORD_ENC_MAX      /* secrets.wifi_password_enc (ciphertext)*/ \
      + SFM_DEVICE_TOKEN_ENC_MAX )     /* secrets.device_token_enc  (ciphertext)*/

/* 序列化记录总长（header + body）。用于给缓冲区定长。 */
#define PARAM_SERIALIZE_LEN   (PARAM_SERIALIZE_HEADER_LEN + PARAM_SERIALIZE_BODY_LEN)

/* v2 与 v3 其余字段顺序完全一致，只少上面的唤醒时间表。 */
#define PARAM_SERIALIZE_BODY_LEN_V2 \
    (PARAM_SERIALIZE_BODY_LEN - PARAM_SERIALIZE_WAKE_FIELDS_LEN)
#define PARAM_SERIALIZE_LEN_V2 \
    (PARAM_SERIALIZE_HEADER_LEN + PARAM_SERIALIZE_BODY_LEN_V2)

/*********************************************************************
 * TYPEDEFS
 */
/* 结果码。自包含（不依赖 esp_err.h），使本模块与其属性测试在宿主机/目标机
 * 编译一致。 */
typedef enum {
    PARAM_SER_OK = 0,                     /* 成功                                    */
    PARAM_SER_ERR_NULL = -1,              /* 空指针参数                              */
    PARAM_SER_ERR_BUFFER_TOO_SMALL = -2,  /* 输出容量 / 输入长度不足                 */
    PARAM_SER_ERR_MAGIC = -3,             /* 魔数不匹配                              */
    PARAM_SER_ERR_BODY_LEN = -4,          /* body 长度字段不是受支持的 v2/v3 布局   */
    PARAM_SER_ERR_TRUNCATED = -5,         /* 缓冲区短于声明的记录长度               */
} param_ser_err_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 返回当前布局下序列化记录的字节长度（== PARAM_SERIALIZE_LEN）。
 *
 * 纯函数。供调用方在运行期给缓冲区定长（无需展开宏）。
 */
size_t param_serialized_len(void);

/**
 * @brief 将参数结构 @p p 序列化为一条版本化字节记录。
 *
 * 纯函数。恒按当前布局写 PARAM_SERIALIZE_LEN 字节。
 * 两个明文机密字段（network.wifi_password / network.device_token）一律写 0，
 * 只序列化 secrets 里的密文与长度，故输出字节中不含机密明文（Req 15.1）。
 *
 * @param p        待序列化的参数结构（不修改）
 * @param out      输出缓冲区
 * @param out_cap  @p out 容量（字节），必须 >= param_serialized_len()
 * @param out_len  [out] 写入的字节数（== param_serialized_len()），可为 NULL
 * @return PARAM_SER_OK 成功；否则相应错误码
 */
param_ser_err_t param_serialize(const ServiceParam_Def_t *p,
                                uint8_t *out, size_t out_cap, size_t *out_len);

/**
 * @brief 将 param_serialize() 产生的字节记录反序列化回参数结构。
 *
 * 纯函数，忠实还原而不迁移：先把 @p out 整体清零，再按 v2 或当前布局回填，
 * 两个明文机密字段回来是 0（调用方随后解密 secrets 还原）；v2 不存在的唤醒字段
 * 保持为 0，交由 param_migrate() 填默认值。校验魔数、受支持的 body 长度与截断。
 * 版本迁移与本函数解耦，使序列化往返对任意结构恒成立，与迁移互不干扰。
 *
 * @param buf   输入字节记录
 * @param len   @p buf 长度（字节）
 * @param out   输出参数结构（进入时会被整体清零）
 * @return PARAM_SER_OK 成功；否则相应错误码
 */
param_ser_err_t param_deserialize(const uint8_t *buf, size_t len,
                                  ServiceParam_Def_t *out);

/**
 * @brief 前向 schema 迁移（内存中，就地，Req 19.2）。
 *
 * 纯函数。保留一切既有字段的语义值，仅对旧版本会留空的「未设置哨兵」字段填入
 * 合理默认值。当前迁移阶梯：
 *   - `< 2`：sync.poll_interval_seconds 为 0 时填 MONITOR_POLL_INTERVAL_DEFAULT_SEC
 *     （0 秒非法），并把 sleep.sleep_mode 归零；
 *   - `< 3`：补当前每日唤醒默认；
 *   - `3`：只把可识别的旧出厂组合（0 槽位 + 240 分钟兜底）迁到每日 06:30，
 *     已由用户改过的组合不动。
 *
 * 后置条件：p->config_version == PARAM_SCHEMA_VERSION_CURRENT。幂等：已是当前
 * 版本则为 no-op。对 config_version 高于当前的记录（固件降级/未知），保留全部
 * 字段值、仅把版本戳规整为当前，以免丢数据。
 *
 * @param p 待迁移的参数结构（就地修改）
 * @return true 若发生了任何改动；false 若无需改动（已是当前版本）或 @p p 为 NULL
 */
bool param_migrate(ServiceParam_Def_t *p);

/**
 * @brief 「持久化表示」等价比较：仅比较 param_serialize() 实际持久化的字段。
 *
 * 比较数值字段、非机密字符串（按内容）与 secrets 密文（含长度）。两个明文机密
 * 字段被刻意忽略（它们从不落盘），因此本比较正是「落盘后能否原样恢复」的语义
 * 等价，用于无歧义地表述往返与迁移不变式。
 *
 * @return true 当且仅当两者的持久化表示等价
 */
bool param_config_equal(const ServiceParam_Def_t *a, const ServiceParam_Def_t *b);

/**
 * @brief 返回 param_ser_err_t 的可读字符串（日志/测试诊断用，永不为 NULL）。
 */
const char *param_ser_err_str(param_ser_err_t err);

#ifdef __cplusplus
}
#endif

#endif /* __PARAM_SERIALIZE_H__ */
