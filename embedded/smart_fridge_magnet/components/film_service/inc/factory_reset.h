/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/factory_reset.h
 * Description: 恢复出厂设置的纯决策逻辑（Req 19.3、19.4）。
 *   两个彼此独立的决策：参数清除范围（哪些字段清、哪些留），以及用户存储
 *   格式化的确认门控。均为无副作用、不依赖 ESP-IDF/NVS/文件系统的纯函数，
 *   可在宿主机直接测试。
 *   本模块只操作内存中的参数结构，既不写 NVS 也不持有任何文件系统句柄，
 *   格式化用户存储由调用方在确认门控通过后自行执行。
 *
 *********************************************************************/
#ifndef __FACTORY_RESET_H__
#define __FACTORY_RESET_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "service_param.h"   /* ServiceParam_Def_t + 字段容量宏 + SFM_PARAM_CONFIG_VERSION */

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * MACROS
 */
/* 用户存储格式化确认魔数（Req 19.4）。ASCII 'F','R','M','T'。
 * 用魔数而非裸布尔：走样/清零/未初始化的实参不会被误判为「已确认」，避免
 * 意外触发不可逆的用户存储格式化。 */
#define FACTORY_RESET_FORMAT_CONFIRM_MAGIC   (0x46524D54u)

/*********************************************************************
 * TYPEDEFS
 */

/* 恢复出厂的执行范围（由确认门控决定）。 */
typedef enum {
    /* 仅清除参数（配网/机密/同步），保留用户存储上的 .film。默认范围。 */
    FACTORY_RESET_SCOPE_PARAMS_ONLY = 0,
    /* 在清除参数之外，还格式化用户存储 —— 仅当用户单独确认时（Req 19.4）。 */
    FACTORY_RESET_SCOPE_PARAMS_AND_STORAGE = 1,
} factory_reset_scope_t;

/*********************************************************************
 * GLOBAL FUNCTIONS（均为纯函数：无副作用、无全局状态、可宿主机测试）
 */

/**
 * @brief 就地把恢复出厂的「清除范围」应用到内存中的参数结构 @p p（Req 19.3）。
 *
 * 纯函数：仅修改 *p，不写 NVS，不触碰用户存储/文件系统。@p p 为 NULL 时为 no-op。
 *
 * 清除：network 配网块全部字段；secrets 两段密文缓冲区清零且长度置 0（Req 15
 * 要求不留密文残余）；sync.last_applied_version → 0、last_sync_error → 空、
 * poll_interval_seconds → MONITOR_POLL_INTERVAL_DEFAULT_SEC（0 秒非法，故回默认）；
 * config_version → SFM_PARAM_CONFIG_VERSION。
 *
 * 保留：factory_flag（设备仍属已初始化，只退回未配网）、film.*（用户存储上的
 * .film 本就不动，保留显示选择即保画面）、sleep.*（属行为偏好而非配网）。
 *
 * @param p 待清除的参数结构（in/out；NULL 时为 no-op）
 */
void factory_reset_apply_params(ServiceParam_Def_t *p);

/**
 * @brief 用户是否单独确认要一并格式化用户存储（Req 19.4 的门控原语）。
 *
 * 纯函数。当且仅当 @p confirm_token == FACTORY_RESET_FORMAT_CONFIRM_MAGIC 时返回
 * true；其余任何值（含 0）均返回 false。
 *
 * @param confirm_token 调用方传入的确认令牌
 * @return true 表示已确认可格式化用户存储；false 表示未确认（默认只清参数）
 */
bool factory_reset_format_confirmed(uint32_t confirm_token);

/**
 * @brief 依据确认令牌把恢复出厂归约为一个明确的执行范围。
 *
 * 纯函数：令牌已确认时返回 FACTORY_RESET_SCOPE_PARAMS_AND_STORAGE，否则返回
 * FACTORY_RESET_SCOPE_PARAMS_ONLY，供调用方一处判断是否格式化用户存储。
 *
 * @param confirm_token 调用方传入的确认令牌
 * @return 应执行的恢复出厂范围
 */
factory_reset_scope_t factory_reset_decide_scope(uint32_t confirm_token);

#ifdef __cplusplus
}
#endif

#endif /* __FACTORY_RESET_H__ */
