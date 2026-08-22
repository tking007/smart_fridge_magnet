package org.smartfridgemagnet.ui

import org.smartfridgemagnet.network.DeviceReportedStatusDto
import org.smartfridgemagnet.network.SyncEventDto
import org.smartfridgemagnet.network.SyncStatusDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HomeDeviceStatusTest {

    @Test
    fun `固件快照优先于可能过期的事件列表`() {
        val status = SyncStatusDto(
            deviceStatus = DeviceReportedStatusDto(appliedVersion = 19),
            recentEvents = listOf(SyncEventDto(event = "apply", version = 18))
        )

        assertEquals(19, reportedAppliedVersion(status))
    }

    @Test
    fun `旧固件回退到最近应用事件`() {
        val status = SyncStatusDto(
            recentEvents = listOf(SyncEventDto(event = "apply", version = 18))
        )

        assertEquals(18, reportedAppliedVersion(status))
    }

    @Test
    fun `没有快照和事件时版本保持未知`() {
        assertNull(reportedAppliedVersion(SyncStatusDto()))
    }
}
