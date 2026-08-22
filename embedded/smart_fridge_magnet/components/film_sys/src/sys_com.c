/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_sys/src/sys_com.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/30
 * Description: 系统级公共动作（当前仅重启）。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#include "sys_com.h"
#include "sys_log.h"

/*********************************************************************
 * MACROS
 */


/*********************************************************************
* TYPEDEFS
*/
#define COMMON_TAG                    "common"

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


void sys_reboot(void)
{
    sys_logw(COMMON_TAG, "sys restart!");
    esp_restart();
    vTaskDelay(100);
    esp_restart();
}
