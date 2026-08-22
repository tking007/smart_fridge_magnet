package org.smartfridgemagnet.data

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Wi-Fi 状态判据的测试。
 *
 * 钉住「BLE 优先、后端证据兜底」这个优先级，以及**不能把几小时前的记录说成现在还连着**。
 */
class DeviceLinkStateTest {

    @Test
    fun `BLE 回读优先于后端证据`() {
        // BLE 说没连，即使后端刚收到过请求也要如实说没连：BLE 读到的是设备此刻对自身
        // 链路的判断，时效性最好。反过来把它盖掉会让"刚刚掉线"这件事被隐藏一段时间。
        assertEquals(
            WifiLinkState.Disconnected,
            wifiLinkStateOf(bleWifiConnected = false, lastContactMinutes = 0)
        )
        assertEquals(
            WifiLinkState.Connected,
            wifiLinkStateOf(bleWifiConnected = true, lastContactMinutes = 9999)
        )
    }

    @Test
    fun `没连蓝牙时后端证据只表示最近联系`() {
        // 低功耗设备请求完 manifest 就会关闭 Wi-Fi；即使后端刚收到请求，也不能据此断言
        // 射频此刻仍连接。
        assertEquals(
            WifiLinkState.SeenAgo(0),
            wifiLinkStateOf(bleWifiConnected = null, lastContactMinutes = 0)
        )
        assertEquals(
            WifiLinkState.SeenAgo(15),
            wifiLinkStateOf(bleWifiConnected = null, lastContactMinutes = 15)
        )
    }

    @Test
    fun `历史联系只说见过、不说在线`() {
        // 设备可能 4 小时才唤醒一次（兜底唤醒默认 240 分钟），半小时前来过不足以断言
        // "现在连着"—— 但它远比"未知"有用：用户由此知道配网这一步是成过的。
        assertEquals(
            WifiLinkState.SeenAgo(16),
            wifiLinkStateOf(bleWifiConnected = null, lastContactMinutes = 16)
        )
        assertEquals(
            WifiLinkState.SeenAgo(600),
            wifiLinkStateOf(bleWifiConnected = null, lastContactMinutes = 600)
        )
    }

    @Test
    fun `两边都没证据才是未知`() {
        assertEquals(
            WifiLinkState.Unknown,
            wifiLinkStateOf(bleWifiConnected = null, lastContactMinutes = null)
        )
    }

    @Test
    fun `相对时间的说法`() {
        assertEquals("刚刚", describeMinutesAgo(0))
        assertEquals("1 分钟前", describeMinutesAgo(1))
        assertEquals("59 分钟前", describeMinutesAgo(59))
        assertEquals("1 小时前", describeMinutesAgo(60))
        assertEquals("23 小时前", describeMinutesAgo(60 * 24 - 1))
        assertEquals("1 天前", describeMinutesAgo(60 * 24))
    }
}
