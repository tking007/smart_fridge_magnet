package org.smartfridgemagnet.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class CameraImageTest {
    @Test
    fun `sample size keeps decoded longest edge within limit`() {
        assertEquals(1, calculateSampleSize(2048, 1536, 2048))
        assertEquals(2, calculateSampleSize(4000, 3000, 2048))
        assertEquals(4, calculateSampleSize(6000, 4000, 2048))
    }
}
