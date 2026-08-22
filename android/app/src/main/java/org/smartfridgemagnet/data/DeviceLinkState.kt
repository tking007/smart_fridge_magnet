package org.smartfridgemagnet.data

/**
 * 设备联网状态的判据：BLE 回读优先，后端证据兜底。
 *
 * 只从 BLE（`0x43`）取会导致没连蓝牙时永远显示「未知」，而设备平时深睡、蓝牙常态连不上。
 * 设备能向后端发出 HTTPS 请求只能证明它在那个时刻联网过。低功耗设备请求完成后立即关闭
 * Wi-Fi，只有 BLE 回读才足以表示当前链路状态。
 *
 * 「多久以前」由后端算好（`device_last_contact_minutes`）。App 不自己解析时间串：库里 UTC 的
 * `Y-m-d H:M:S` 与本地 ISO 并存，用单一 pattern 必然在其中一种上错 8 小时。
 */
sealed interface WifiLinkState {
    /** 确定联网：BLE 明确回读已连接。 */
    object Connected : WifiLinkState

    /** 确定没联网：只有 BLE 明确回读 false 才算。 */
    object Disconnected : WifiLinkState

    /**
     * 只有后端历史证据。[minutes] 是距今分钟数。
     *
     * 单独一档而不是并入 [Connected]：设备通常每天定时唤醒一次，「半小时前来过」不足以断言
     * 「现在连着」，但它远比「未知」有用 —— 用户由此知道配网成功过。
     */
    data class SeenAgo(val minutes: Int) : WifiLinkState

    /** 两边都没有证据。 */
    object Unknown : WifiLinkState
}

/**
 * 综合 BLE 回读与后端证据，得出该显示哪一种 Wi-Fi 状态。
 *
 * @param bleWifiConnected BLE `0x43` 回读的 Wi-Fi 状态；null = 没连蓝牙 / 没读到
 * @param lastContactMinutes 后端算出的「设备最近一次联系后端距今多少分钟」；null = 从未
 */
fun wifiLinkStateOf(bleWifiConnected: Boolean?, lastContactMinutes: Int?): WifiLinkState = when {
    // BLE 读到了就用它：那是设备此刻对自己链路的判断，时效性最好。
    bleWifiConnected == true -> WifiLinkState.Connected
    bleWifiConnected == false -> WifiLinkState.Disconnected
    lastContactMinutes == null -> WifiLinkState.Unknown
    else -> WifiLinkState.SeenAgo(lastContactMinutes)
}

/** 把分钟数说成人话（用于「N 前联网」）。 */
fun describeMinutesAgo(minutes: Int): String = when {
    minutes < 1 -> "刚刚"
    minutes < 60 -> "$minutes 分钟前"
    minutes < 60 * 24 -> "${minutes / 60} 小时前"
    else -> "${minutes / (60 * 24)} 天前"
}
