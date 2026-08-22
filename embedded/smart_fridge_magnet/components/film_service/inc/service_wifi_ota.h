/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/service_wifi_ota.h
 * Description: Wi-Fi OTA —— 从 manifest.firmware 描述的地址下载并刷入固件。
 *
 *   与既有的 BLE OTA（service_ota.h）是两条独立路径，不共用状态机：
 *   BLE OTA 是手机把字节一块块推过来，本模块是设备自己去 HTTPS 拉，
 *   底层交给 ESP-IDF 的 esp_https_ota 而不是自己 begin/write/end。
 *
 *   ── 回滚保护是前提，不是可选项 ──
 *   sdkconfig 里必须开 CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE。新固件启动后处于
 *   PENDING_VERIFY，要在本次运行内被确认才算数；否则下次启动自动回滚。
 *   设备贴在冰箱上无人值守，一旦刷进「能开机但连不上 Wi-Fi」的固件，没有回滚就
 *   再也收不到任何 OTA —— 只能把设备拆下来插 USB。
 *
 *********************************************************************/
#ifndef __SERVICE_WIFI_OTA_H__
#define __SERVICE_WIFI_OTA_H__

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "manifest_parse.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 允许发起 OTA 的最低电量百分比。
 *
 * 刷写中途断电不会变砖（双分区 + otadata，旧分区仍可启动），但会白耗掉一次
 * 1.9MB 下载与整片 flash 擦写的电量，而设备本来就是因为电量低才该省着用。
 * 60% 是个保守值：一次 OTA 的能耗远高于一次画面同步。 */
#define SFM_OTA_MIN_BATTERY_PCT   (60)

/**
 * [service_wifi_ota_perform 按 manifest.firmware 的描述执行一次 OTA]
 *
 * 必须在 Wi-Fi 已连接时调用（同步流程内）。成功时**本函数不返回** ——
 * 它会设置启动分区并重启。
 *
 * 前置判断（任一不满足即跳过本次，返回非 ESP_OK 且不重启）：
 *   - fw->has_update 为真；
 *   - 版本串与当前运行的固件不同（后端已做过新旧门控，这里只挡「同版本重刷」）；
 *   - 电量 >= SFM_OTA_MIN_BATTERY_PCT；
 *   - 当前不在 PENDING_VERIFY 状态（上一次 OTA 还没被确认，先别叠第二次）。
 *
 * @param fw          manifest 解析出的固件描述。
 * @param battery_pct 当前电量百分比；<0 表示读不到（读不到即跳过，不赌）。
 * @return 跳过或失败的原因码；成功不返回。
 */
esp_err_t service_wifi_ota_perform(const manifest_firmware_t *fw, int battery_pct);

/**
 * [service_wifi_ota_confirm_running_image 确认当前固件可用，取消回滚]
 *
 * 只在「这个固件确实还能接收下一次 OTA」被证明之后调用 —— 本项目取的证据是
 * 「Wi-Fi 连通且 manifest 取到」，因为那正好等价于 OTA 通道可用。
 *
 * 不在 app_main 里无条件调用：那样任何能开机的镜像都会被确认，回滚保护就废了。
 *
 * 幂等；当前镜像不处于 PENDING_VERIFY 时什么都不做。
 */
void service_wifi_ota_confirm_running_image(void);

#ifdef __cplusplus
}
#endif

#endif /* __SERVICE_WIFI_OTA_H__ */
