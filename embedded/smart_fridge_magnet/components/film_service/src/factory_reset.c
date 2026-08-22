/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/src/factory_reset.c
 * Description: 恢复出厂设置纯决策逻辑实现（clear scope + 格式化确认门控）。
 *   （另满足 Req 15 的机密彻底抹除）。语义、清除范围与门控约定见 factory_reset.h。
 *
 *   本 TU 仅依赖标准 C（<string.h>）、service_param.h（纯结构定义 + 容量宏 +
 *   SFM_PARAM_CONFIG_VERSION）与 poll_interval.h（默认周期常量），不触碰
 *   ESP-IDF/NVS/FreeRTOS/文件系统，可在宿主机直接编译并做属性/单元测试
 *   （Property 17、。持久化与用户存储格式化等副作用由
 *   ESP-IDF 侧的参数服务/存储 HAL 在后续集成步骤中承担，不在本模块内。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <string.h>

#include "factory_reset.h"
#include "poll_interval.h"   /* MONITOR_POLL_INTERVAL_DEFAULT_SEC（复用默认常量，不硬编码） */

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

void factory_reset_apply_params(ServiceParam_Def_t *p)
{
    if (p == NULL)
    {
        return;
    }

    /* ---- 保留（PRESERVED）---------------------------------------------------
     * factory_flag：设备仍是已初始化的 SFM 设备，仅退回未配网状态；
     * film.* / sleep.*：显示选择与休眠偏好保持不变（用户存储 .film 本就不动）。
     * 这三处刻意【不】写，从而被保留。 */

    /* ---- 参数版本戳规整为当前版本 ----------------------------------------- */
    p->config_version = SFM_PARAM_CONFIG_VERSION;

    /* ---- 清除（WIPED）：整个 network 配网块（含明文机密）------------------- */
    p->network.wifi_enable = 0;
    memset(p->network.wifi_ssid, 0, sizeof(p->network.wifi_ssid));
    memset(p->network.wifi_password, 0, sizeof(p->network.wifi_password));   /* 明文密码 */
    memset(p->network.film_api_url, 0, sizeof(p->network.film_api_url));
    memset(p->network.manifest_url, 0, sizeof(p->network.manifest_url));
    memset(p->network.device_id, 0, sizeof(p->network.device_id));
    memset(p->network.device_token, 0, sizeof(p->network.device_token));     /* 明文令牌 */

    /* ---- 清除（WIPED）：secrets 落盘密文 + 长度（Req 15 彻底抹除机密）------ */
    p->secrets.wifi_password_len = 0;
    p->secrets.device_token_len = 0;
    memset(p->secrets.wifi_password_enc, 0, sizeof(p->secrets.wifi_password_enc));
    memset(p->secrets.device_token_enc, 0, sizeof(p->secrets.device_token_enc));

    /* ---- 清除同步状态；轮询周期恢复默认值 ------------------------------ */
    p->sync.last_applied_version = 0;
    memset(p->sync.last_sync_error, 0, sizeof(p->sync.last_sync_error));
    p->sync.poll_interval_seconds = MONITOR_POLL_INTERVAL_DEFAULT_SEC;
}

bool factory_reset_format_confirmed(uint32_t confirm_token)
{
    return confirm_token == FACTORY_RESET_FORMAT_CONFIRM_MAGIC;
}

factory_reset_scope_t factory_reset_decide_scope(uint32_t confirm_token)
{
    return factory_reset_format_confirmed(confirm_token)
               ? FACTORY_RESET_SCOPE_PARAMS_AND_STORAGE
               : FACTORY_RESET_SCOPE_PARAMS_ONLY;
}
