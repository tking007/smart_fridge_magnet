/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 kiritro
 *
 * FileName : /film_hal/src/hal_init.c
 * Author: Kiritro  Version: v0.1  Date: 2026/4/1
 * Description: HAL 层统一初始化入口。
 *   顺序固定：电源（先给外设供电）-> 电池 -> LED -> SD -> 按键 -> EPD。
 *   EPD 失败只记日志不中断，其余模块仍可用于提示与联网。
 *
 *********************************************************************/

/*********************************************************************
 * INCLUDES
 */
#include "hal_sd.h"
#include "hal_bat.h"
#include "hal_led.h"
#include "hal_pwr.h"
#include "hal_epd.h"
#include "hal_key.h"
#include "hal_init.h"

#include "sys_log.h"

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


void film_hal_init(void)
{
    hal_pwr_init();
    hal_bat_init();
    hal_led_init();
    hal_sd_init();
    hal_key_init();

    esp_err_t epd_err = hal_epd_init();
    if(epd_err != ESP_OK)
    {
        sys_loge("HAL_INIT", "EPD init failed: %s", esp_err_to_name(epd_err));
    }
}
