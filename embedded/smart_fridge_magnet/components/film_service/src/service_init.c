/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_service/src/service_init.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/16
 * Description: Function introduction
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "service_init.h"
#include "service_ble.h"
#include "service_param.h"
#include "service_monitor.h"
#include "service_file.h"
#include "service_film.h"


/*********************************************************************
 * MACROS
 */


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



void film_service_init(void)
{
    // 初始化服务参数
    service_param_init();
    // 初始化ble服务
    service_ble_init();
    // 初始化监控服务
    service_monitor_init();
    // 初始化文件服务
    service_file_init();
    // 初始化照片服务
    service_film_init();
    // 唤醒时序会向 film 队列投递消息，必须最后启动
    service_monitor_start_wake_dispatch();
}
