/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_sys/src/sys_init.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/16
 * Description: 系统初始化入口。
 *   固定顺序：设置本地时区 -> HAL 层 -> 服务层 -> 输出启动标识横幅。
 *
 *********************************************************************/

/*********************************************************************
 * Modified 2026 by Smart Fridge Magnet project (based on FrameFilm 562f330).
 * Change: emit a startup identification banner after core initialization.
 * Upstream: FrameFilm (GPL-3.0-or-later) — see ../../../NOTICE.md and LICENSE.
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include <stdlib.h>
#include <time.h>

#include "sys_log.h"
#include "sys_cfg.h"
#include "sys_init.h"

#include "hal_init.h"
#include "service_init.h"

/*********************************************************************
 * MACROS
 */
// 启动标识可诊断日志的日志 TAG
#define SYS_STARTUP_LOG_TAG            "SFM"


/*********************************************************************
* TYPEDEFS
*/


/*********************************************************************
 * CONSTANTS
 */


/*********************************************************************
 * LOCAL VARIABLES
 */


/*********************************************************************
 * GLOBAL VARIABLES
 */


/*********************************************************************
 * LOCAL FUNCTIONS
 */


/*********************************************************************
 * GLOBAL FUNCTIONS
 */



void film_sys_init(void)
{
    /* 时区必须在启动时无条件设置：深睡唤醒等价于重启，newlib 的 TZ 环境变量会丢回 UTC，
     * 而 RTC 计时在深睡期间照走、系统时间仍然有效。TZ 未设时 localtime() 偏 8 小时，
     * 「按做饭时段展示对应那一餐」就会在错误的时间切换。 */
    setenv("TZ", SFM_LOCAL_TZ, 1);
    tzset();

    film_hal_init();
    film_service_init();

    // 启动标识横幅：项目名、固件版本、硬件版本与上游基线提交号（需求 4.1）
    sys_logi(SYS_STARTUP_LOG_TAG,
             "======== startup banner ========\n"
             "  project  : %s\n"
             "  firmware : %s\n"
             "  hardware : %s\n"
             "  baseline : %s\n"
             "=================================",
             SYS_SYSTEM_ID, SYS_FIRMWARE_VERSION,
             SYS_HAREWARE_VERSION, SYS_UPSTREAM_BASELINE);
}
