/*********************************************************************
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Copyright (c) 2026 Smart Fridge Magnet project
 *   (based on FrameFilm 562f330 — see ../../NOTICE.md and LICENSE)
 *
 * FileName : /film_service/inc/sync_decision.h
 * Description: Wi-Fi 同步「版本门控 + force 强制应用」纯决策逻辑（Req 10.3、10.4）。
 *   无副作用、独立于网络/文件系统/ESP-IDF，可宿主机测试。
 *
 *   should_download_and_apply == (force || version > last_applied_version)：
 *   force 为真则强制下载并应用 manifest 声明的当前版本，且不重置本地
 *   last_applied_version、也不需要专用 BLE 复位命令；否则仅当版本更新才应用，
 *   版本未更新即结束本轮同步，不下载、不刷新电子纸。
 *
 *   本模块只产出决策；SNTP/HTTPS/流式落盘/原子替换/刷新以及刷新成功后保存
 *   last_applied_version 都由 service_wifi 完成，故本模块不触碰持久化。
 *
 *********************************************************************/
#ifndef __SYNC_DECISION_H__
#define __SYNC_DECISION_H__

/*********************************************************************
 * INCLUDES
 */
#include <stdint.h>
#include <stdbool.h>

/*********************************************************************
 * CPPMIX
 */
#ifdef __cplusplus
extern "C" {
#endif

/*********************************************************************
 * TYPEDEFS
 */
/*
 * 一次 Wi-Fi 同步在解析出 manifest 之后、下载之前的门控决策结果。
 */
typedef struct {
    /* true  => 下载并应用 manifest 声明的当前版本；
     * false => 结束本次同步，不下载、不刷新（Req 10.3）。 */
    bool should_download_and_apply;

    /* 是否要求重置本地 last_applied_version。恒为 false：force 强制应用当前版本
     * 但不重置 last_applied（Req 10.4），后者只在刷新成功后由 service_wifi 前向
     * 更新。保留该字段是为了让这条不变式可被属性测试直接断言。 */
    bool resets_last_applied_version;
} sync_decision_t;

/*********************************************************************
 * GLOBAL FUNCTIONS
 */

/**
 * @brief 计算 Wi-Fi 同步的下载/应用门控决策（版本门控 + force 强制应用）。
 *
 * 纯函数：should_download_and_apply == (force || version > last_applied_version)；
 * resets_last_applied_version 恒为 false（Req 10.4）。
 *
 * @param version               manifest 声明的版本号（任意 uint32）
 * @param last_applied_version  最近一次成功上屏的版本号（任意 uint32）
 * @param force                 manifest 的 force 字段（true=强制应用当前版本）
 * @return sync_decision_t       门控决策
 */
sync_decision_t sync_decide_download(uint32_t version,
                                     uint32_t last_applied_version,
                                     bool force);

/**
 * @brief 便捷谓词：本次同步是否应下载并应用当前版本。
 *
 * 等价于 sync_decide_download(...).should_download_and_apply，供只关心「是否
 * 下载」的调用方避免展开结构体。
 *
 * @param version               manifest 声明的版本号
 * @param last_applied_version  最近一次成功上屏的版本号
 * @param force                 manifest 的 force 字段
 * @return true 当且仅当 force 为真，或 version > last_applied_version
 */
bool sync_should_download(uint32_t version,
                          uint32_t last_applied_version,
                          bool force);

#ifdef __cplusplus
}
#endif

#endif /* __SYNC_DECISION_H__ */
