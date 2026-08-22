package org.smartfridgemagnet.transfer

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * BLE 帧构造与解析的契约测试。
 *
 * BLE 帧是跨端契约，写错的表现全是**静默失败** —— 固件对不合规的帧直接丢弃、不回任何东西，
 * App 侧只看到「发出去了但设备没反应」，要接串口才查得出来。
 *
 * 只测纯字节逻辑，不碰 GATT：连接过程需要真机，而帧格式不需要。
 */
class BleProtocolTest {

    private fun ByteArray.hex() = joinToString(" ") { "%02X".format(it) }

    @Test
    fun `帧结构为 HEAD CH LEN DATA SUM`() {
        val packet = BleProtocol.buildPacket(0x30, byteArrayOf(1))
        assertEquals("55 30 01 01 87", packet.hex())
        assertEquals(5, packet.size)
    }

    @Test
    fun `校验和是前面所有字节之和的低八位`() {
        val packet = BleProtocol.buildPacket(BleProtocol.CH_FILE_DATA, byteArrayOf(-1, -1, -1))
        val expected = (0x55 + 0x02 + 0x03 + 0xFF * 3) and 0xFF
        assertEquals(expected.toByte(), packet.last())
    }

    @Test
    fun `空载荷命令长度恒为 4`() {
        listOf(
            BleProtocol.buildFileStart(),
            BleProtocol.buildFileStop(),
            BleProtocol.buildSyncNow(),
            BleProtocol.buildStatusGet(),
            BleProtocol.buildFileClearAll(),
            BleProtocol.buildDisplayNow()
        ).forEach { packet ->
            assertEquals("空载荷帧应为 HEAD/CH/LEN/SUM 四字节：${packet.hex()}", 4, packet.size)
            assertEquals(0, packet[2].toInt())
        }
    }

    /**
     * 多发一个结尾 \0，固件的 ble_filename_is_safe() 会因控制字节判定 unsafe 而拒收整帧，状态机停在
     * STARTED，后续 FILE_DATA/FILE_STOP 全被当乱序丢弃，表现是「发送成功但设备白屏」。
     */
    @Test
    fun `文件名载荷不带结尾 NUL`() {
        val packet = BleProtocol.buildFileName("1_today.film")
        val len = packet[2].toInt() and 0xFF
        assertEquals("1_today.film".length, len)
        val payload = packet.copyOfRange(3, 3 + len)
        assertEquals("1_today.film", String(payload, Charsets.US_ASCII))
        assertTrue("载荷里不允许出现 0x00", payload.none { it == 0.toByte() })
    }

    /** 固件按大端读文件长度，写成小端会让它按错误尺寸预分配并拒收。 */
    @Test
    fun `文件长度是大端四字节`() {
        // 209120 = 0x00 03 30 E0（.film 定长，跨端契约）
        val packet = BleProtocol.buildFileLen(209120)
        val payload = packet.copyOfRange(3, 7)
        assertEquals("00 03 30 E0", payload.hex())
    }

    @Test
    fun `manifest url 载荷长度受单字节 LEN 约束`() {
        // 固件侧 manifest_url 上限 127 字节；帧的 LEN 只有一字节，超过 255 会整帧错位。
        val url = "https://fridge.example.com/api/devices/dev001/display/manifest"
        val packet = BleProtocol.buildManifestUrl(url)
        assertEquals(url.length, packet[2].toInt() and 0xFF)
        assertTrue("实际 URL 长度 ${url.length} 已超过固件上限 127", url.length <= 127)
    }

    @Test
    fun `parseResponse 拒绝校验和错误的帧`() {
        val good = BleProtocol.buildPacket(BleProtocol.CH_STATUS_GET, byteArrayOf(7))
        assertNotNull(BleProtocol.parseResponse(good))

        val corrupted = good.copyOf().also { it[it.size - 1] = (it.last() + 1).toByte() }
        assertNull("校验和不符必须判为无效帧", BleProtocol.parseResponse(corrupted))

        val wrongHead = good.copyOf().also { it[0] = 0x56 }
        assertNull("帧头不是 0x55 必须判为无效帧", BleProtocol.parseResponse(wrongHead))

        assertNull("长度不足 4 的帧无法承载任何命令", BleProtocol.parseResponse(byteArrayOf(0x55, 0x01)))
    }

    @Test
    fun `parseResponse 取出通道与载荷`() {
        val packet = BleProtocol.buildWifiSsid("MI-2.4G")
        val parsed = BleProtocol.parseResponse(packet)
        assertNotNull(parsed)
        assertEquals(BleProtocol.CH_WIFI_SSID, parsed!!.channel)
        assertEquals("MI-2.4G", String(parsed.payload, Charsets.US_ASCII))
    }

    /**
     * 0x43 状态响应的字段偏移直接决定设备页显示什么。错位不会报错，
     * 只会把版本号显示成天文数字或把电量当成 busy 标志。
     */
    @Test
    fun `parseStatus 按固件字段偏移解析`() {
        val payload = byteArrayOf(
            1,                          // [0] wifi 已连接
            0, 0, 0, 32,                // [1-4] version = 32（大端）
            0,                          // [5] busy = false
            87,                         // [6] 电量 87%
            5                           // [7] 错误串长度
        ) + "abcde".toByteArray(Charsets.UTF_8) + byteArrayOf(1) // 尾随休眠位

        val status = BleProtocol.parseStatus(payload)
        assertNotNull(status)
        assertTrue(status!!.wifiConnected)
        assertEquals(32, status.lastAppliedVersion)
        assertEquals(false, status.busy)
        assertEquals(87, status.batteryPercent)
        assertEquals("abcde", status.lastSyncError)
        assertEquals(true, status.sleepMode)
    }

    @Test
    fun `parseStatus 兼容没有休眠位的旧固件`() {
        val payload = byteArrayOf(1, 0, 0, 0, 1, 0, 90, 0)
        assertEquals(null, BleProtocol.parseStatus(payload)!!.sleepMode)
    }

    @Test
    fun `parseStatus 在载荷不足时返回 null 而不是给出错值`() {
        assertNull(BleProtocol.parseStatus(byteArrayOf(1, 0, 0, 0)))
    }

    /** 固件的 name_len 含结尾 NUL，不去掉会让文件名带一个不可见字符，后续按名匹配全失败。 */
    @Test
    fun `parseFileListEntry 去掉固件补的结尾 NUL`() {
        val name = "1_today.film"
        val payload = byteArrayOf(3, (name.length + 1).toByte()) +
            name.toByteArray(Charsets.US_ASCII) + byteArrayOf(0)

        val entry = BleProtocol.parseFileListEntry(payload)
        assertNotNull(entry)
        assertEquals(3, entry!!.id)
        assertEquals(name, entry.name)
    }

    @Test
    fun `parseFileListEntry 对残帧返回 null`() {
        assertNull(BleProtocol.parseFileListEntry(byteArrayOf(1)))
        assertNull(BleProtocol.parseFileListEntry(byteArrayOf(1, 10, 65)))   // 声明 10 字节只给了 1
        assertNull(BleProtocol.parseFileListEntry(byteArrayOf(1, 0)))        // 空文件名
    }

    @Test
    fun `文件列表结束帧与普通条目可明确区分`() {
        assertTrue(BleProtocol.isFileListEnd(byteArrayOf(0xFF.toByte(), 0)))
        assertFalse(BleProtocol.isFileListEnd(byteArrayOf(1, 0)))
        assertFalse(BleProtocol.isFileListEnd(byteArrayOf(1, 2, 'a'.code.toByte(), 0)))
    }

    // ==================== 唤醒时间表与轮询周期（0x48–0x4B）====================
    //
    // 这四个 opcode 与前面那些同属跨端字节契约，但故障更隐蔽：写错既不会崩也不会报错，
    // 只是设备在错误的时刻醒来联网（耗电、且屏上那张图没人看）或该醒时不醒。
    // 现场唯一能观察到的现象是"到了饭点画面还是旧的"，看不出是字节序的问题。

    /**
     * 逐字节钉住 0x48 的帧布局。
     *
     * 大端写成小端不会有任何一端报错：固件会把 420（07:00）读成 0xA401 = 41985，
     * 越界后整帧被拒，App 只看到一句"设置失败"。
     */
    @Test
    fun `buildWakeSchedule 逐字节符合固件布局`() {
        // 07:00 / 12:00 / 18:30 三个唤醒点 + 4 小时兜底
        val schedule = BleProtocol.WakeSchedule(
            slotMinutes = listOf(7 * 60, 12 * 60, 18 * 60 + 30),   // 420 / 720 / 1110
            fallbackMinutes = 240
        )
        val packet = BleProtocol.buildWakeSchedule(schedule)

        // count=3 → 载荷 1 + 2*3 + 2 = 9 字节；整帧再加 HEAD/CH/LEN/SUM 四字节
        assertEquals(13, packet.size)
        assertEquals(BleProtocol.CH_WAKE_SCHEDULE, packet[1].toInt() and 0xFF)
        assertEquals(9, packet[2].toInt() and 0xFF)
        assertEquals("55 48 09 03 01 A4 02 D0 04 56 00 F0 6A", packet.hex())
    }

    @Test
    fun `buildWakeSchedule 空时间表也带 count 与兜底`() {
        val packet = BleProtocol.buildWakeSchedule(BleProtocol.WakeSchedule(emptyList(), 0))
        // 载荷 = [count=0][fallback_hi][fallback_lo]，恒有 3 字节
        assertEquals(7, packet.size)
        assertEquals(3, packet[2].toInt() and 0xFF)
        assertEquals("00 00 00", packet.copyOfRange(3, 6).hex())
    }

    /**
     * 非法值必须**抛异常**而不是钳制后发出去。
     *
     * 钳制会让一个算错的时刻变成「看起来能用」的时刻悄悄生效，而固件那侧只回一个 0xFF，
     * 从那个字节看不出是哪一项不合法。
     */
    @Test
    fun `buildWakeSchedule 对越界唤醒点抛异常`() {
        val e = assertThrows(IllegalArgumentException::class.java) {
            // 1440 = 24:00，已越出 [0,1439]
            BleProtocol.buildWakeSchedule(BleProtocol.WakeSchedule(listOf(1440), 240))
        }
        assertTrue("异常信息应指出是第几项越界：${e.message}", e.message!!.contains("1440"))
    }

    @Test
    fun `buildWakeSchedule 对超过六个唤醒点抛异常`() {
        val seven = listOf(0, 60, 120, 180, 240, 300, 360)
        assertEquals(7, seven.size)
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildWakeSchedule(BleProtocol.WakeSchedule(seven, 240))
        }
    }

    @Test
    fun `buildWakeSchedule 对超过七天的兜底周期抛异常`() {
        // 10080 分钟 = 7 天，是上界；再多一分钟就非法
        BleProtocol.buildWakeSchedule(BleProtocol.WakeSchedule(listOf(420), 10080))
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildWakeSchedule(BleProtocol.WakeSchedule(listOf(420), 10081))
        }
    }

    /** 0x49 的响应布局与 0x48 相同，所以构造与解析必须严格互逆。 */
    @Test
    fun `parseWakeSchedule 与 buildWakeSchedule 往返一致`() {
        listOf(
            BleProtocol.WakeSchedule(listOf(420, 690, 1080), 240),
            BleProtocol.WakeSchedule(listOf(0), 0),
            BleProtocol.WakeSchedule(listOf(1439), 10080),
            BleProtocol.WakeSchedule(emptyList(), 120),
            BleProtocol.WakeSchedule(listOf(0, 1, 2, 3, 4, 1439), 480)
        ).forEach { original ->
            val packet = BleProtocol.buildWakeSchedule(original)
            val payload = BleProtocol.parseResponse(packet)!!.payload
            assertEquals("往返后应完全相同：$original", original, BleProtocol.parseWakeSchedule(payload))
        }
    }

    /**
     * 长度不自洽时返回 null 而不是给出"半张时间表"。
     *
     * 半张时间表会让设备在一部分饭点醒、另一部分不醒，而 UI 上显示的是完整配置 ——
     * 两边不一致却没有任何一处报错。
     */
    @Test
    fun `parseWakeSchedule 对长度不自洽的载荷返回 null`() {
        // count 说有 3 个 slot（应为 1+6+2=9 字节），实际只给了 2 个 slot 的字节
        assertNull(
            BleProtocol.parseWakeSchedule(
                byteArrayOf(3, 0x01, 0xA4.toByte(), 0x02, 0xD0.toByte(), 0x00, 0xF0.toByte())
            )
        )
        // 固件拒收时回的单字节 0xFF：count=255 已越界
        assertNull(BleProtocol.parseWakeSchedule(byteArrayOf(0xFF.toByte())))
        assertNull(BleProtocol.parseWakeSchedule(ByteArray(0)))
        // 多一个字节同样算不自洽
        assertNull(BleProtocol.parseWakeSchedule(byteArrayOf(1, 0x01, 0xA4.toByte(), 0x00, 0xF0.toByte(), 0)))
    }

    /**
     * 0x4A 的钳制**刻意**与 0x48 的抛异常不同：这里只是量程问题，UI 滑块的边界与固件
     * 钳制区间本就一致，拖到端点时按端点生效最符合直觉。先钳一次是为了让本地显示的值
     * 与 parsePollIntervalSeconds 读回的值对得上。
     */
    @Test
    fun `buildPollInterval 是四字节大端且超范围钳制`() {
        val packet = BleProtocol.buildPollInterval(900)
        assertEquals(8, packet.size)
        assertEquals(BleProtocol.CH_POLL_INTERVAL, packet[1].toInt() and 0xFF)
        assertEquals(4, packet[2].toInt() and 0xFF)
        assertEquals("00 00 03 84", packet.copyOfRange(3, 7).hex())
        // 5 秒 → 钳到下界 30 秒（0x0000001E）
        assertEquals("00 00 00 1E", BleProtocol.buildPollInterval(5).copyOfRange(3, 7).hex())
        // 999999 秒 → 钳到上界 86400 秒（0x00015180）
        assertEquals("00 01 51 80", BleProtocol.buildPollInterval(999999).copyOfRange(3, 7).hex())
        // 端点本身不被改动
        assertEquals("00 00 00 1E", BleProtocol.buildPollInterval(30).copyOfRange(3, 7).hex())
        assertEquals("00 01 51 80", BleProtocol.buildPollInterval(86400).copyOfRange(3, 7).hex())
    }

    /**
     * 只接受恰好 4 字节。固件在载荷长度非法时回 1 字节 0xFF —— 那是拒绝信号而不是
     * 值 255，按前缀解析会把它读成一个荒谬的周期并原样显示给用户。
     */
    @Test
    fun `parsePollIntervalSeconds 只接受恰好四字节`() {
        assertEquals(900, BleProtocol.parsePollIntervalSeconds(byteArrayOf(0, 0, 0x03, 0x84.toByte())))
        assertEquals(86400, BleProtocol.parsePollIntervalSeconds(byteArrayOf(0, 0x01, 0x51, 0x80.toByte())))

        assertNull("单字节 0xFF 是拒绝信号", BleProtocol.parsePollIntervalSeconds(byteArrayOf(0xFF.toByte())))
        assertNull("五字节不自洽", BleProtocol.parsePollIntervalSeconds(byteArrayOf(0, 0, 0x03, 0x84.toByte(), 0)))
        assertNull(BleProtocol.parsePollIntervalSeconds(ByteArray(0)))
        assertNull("全零解析出 0，只可能来自错帧", BleProtocol.parsePollIntervalSeconds(byteArrayOf(0, 0, 0, 0)))
    }

    /** 三个使用方共用这一个格式化函数（时间表编辑、日志、slotLabels），补零漏了会显示成 "7:5"。 */
    @Test
    fun `formatMinuteOfDay 补零成 HH MM`() {
        assertEquals("00:00", BleProtocol.formatMinuteOfDay(0))
        assertEquals("07:05", BleProtocol.formatMinuteOfDay(7 * 60 + 5))
        assertEquals("12:00", BleProtocol.formatMinuteOfDay(720))
        assertEquals("23:59", BleProtocol.formatMinuteOfDay(1439))
    }

    // ==================== 置顶自定义画面（0x4C / 0x4D）====================
    //
    // 这一组的故障模式和 0x48 一样隐蔽，且多了一条**最不直观**的规则：文件名不得含
    // today/breakfast/lunch/dinner。固件选菜单图用 strstr 做包含匹配，一张 my_lunch.film 会同时被
    // 当成「午餐详情页」和「置顶照片」，所以这条必须在本地就拦下。

    /** 逐字节钉住 ALWAYS（不用窗口时两端仍要发 0，长度恒为 6 + name_len）。 */
    @Test
    fun `buildPinFilm 逐字节符合固件布局 - ALWAYS`() {
        val packet = BleProtocol.buildPinFilm(
            BleProtocol.PinnedFilm(
                mode = BleProtocol.PinMode.ALWAYS,
                fileName = "draw_ab12cd.film"
            )
        )
        // 载荷 = 6 + 16 = 22（0x16）；整帧再加 HEAD/CH/LEN/SUM 四字节
        assertEquals(26, packet.size)
        assertEquals(BleProtocol.CH_PIN_FILM, packet[1].toInt() and 0xFF)
        assertEquals(22, packet[2].toInt() and 0xFF)
        assertEquals(
            "55 4C 16 01 00 00 00 00 10 64 72 61 77 5F 61 62 31 32 63 64 2E 66 69 6C 6D 98",
            packet.hex()
        )
        // 名字不带结尾 NUL（由固件自行补），与 FILE_NAME 同一约定；多发一个 \0 会让
        // 固件的白名单校验判 unsafe 并整条拒收。
        assertTrue(
            "名字字节里不允许出现 0x00",
            packet.copyOfRange(9, 25).none { it == 0.toByte() }
        )
    }

    /** 跨午夜窗口：22:00–06:00 写成 start=1320 > end=360，大端两字节。 */
    @Test
    fun `buildPinFilm 逐字节符合固件布局 - WINDOW 跨午夜`() {
        val packet = BleProtocol.buildPinFilm(
            BleProtocol.PinnedFilm(
                mode = BleProtocol.PinMode.WINDOW,
                windowStartMinutes = 22 * 60,   // 1320 = 0x0528
                windowEndMinutes = 6 * 60,      // 360  = 0x0168
                fileName = "photo_1a2b3c.film"
            )
        )
        assertEquals(27, packet.size)
        assertEquals(23, packet[2].toInt() and 0xFF)
        assertEquals(
            "55 4C 17 02 05 28 01 68 11 70 68 6F 74 6F 5F 31 61 32 62 33 63 2E 66 69 6C 6D 7C",
            packet.hex()
        )
        // 大端写成小端不会有任何一端报错：固件会把 1320 读成 0x2805 = 10245，越界后整帧被拒。
        assertEquals("05 28", packet.copyOfRange(4, 6).hex())
        assertEquals("01 68", packet.copyOfRange(6, 8).hex())
    }

    /**
     * 清除帧只发 1 字节 `mode=0`（协议特许），整帧因此是 5 字节而非空载荷的 4 字节。
     *
     * 固件在 mode==OFF 时**擦掉整条记录**而不是写一条 OFF 记录，所以取消 pin 不需要
     * 窗口和文件名 —— 要求调用方为"取消"先想个文件名是说不通的。
     */
    @Test
    fun `buildPinFilmClear 是单字节 mode 0 的清除帧`() {
        val packet = BleProtocol.buildPinFilmClear()
        assertEquals("HEAD/CH/LEN/1 字节载荷/SUM", 5, packet.size)
        assertEquals(BleProtocol.CH_PIN_FILM, packet[1].toInt() and 0xFF)
        assertEquals(1, packet[2].toInt() and 0xFF)
        assertEquals(0, BleProtocol.PinMode.OFF.raw)
        assertEquals(BleProtocol.PinMode.OFF.raw, packet[3].toInt() and 0xFF)
        assertEquals("55 4C 01 00 A2", packet.hex())
    }

    @Test
    fun `buildPinFilmGet 无载荷`() {
        val packet = BleProtocol.buildPinFilmGet()
        assertEquals(4, packet.size)
        assertEquals("55 4D 00 A2", packet.hex())
    }

    /**
     * 窗口越界必须抛，且**即便当前 mode 用不到窗口也校验**。
     *
     * 理由：这条记录会被 0x4D 回读、被 App 原样发回，非法值留在记录里，等哪天用户把
     * 模式改成 WINDOW 就突然生效了。固件的判定同样不看 mode。
     */
    @Test
    fun `buildPinFilm 对越界窗口抛异常`() {
        val start = assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildPinFilm(
                BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 1440, 360, "photo_x.film")
            )
        }
        assertTrue("异常信息应带上越界的值：${start.message}", start.message!!.contains("1440"))

        val end = assertThrows(IllegalArgumentException::class.java) {
            // mode=ALWAYS 用不到窗口，同样要拦
            BleProtocol.buildPinFilm(
                BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 1440, "photo_x.film")
            )
        }
        assertTrue(end.message!!.contains("1440"))

        // 1439 = 23:59 是上界，合法
        BleProtocol.buildPinFilm(
            BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 1439, 1439, "photo_x.film")
        )
    }

    @Test
    fun `buildPinFilm 对空文件名抛异常`() {
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildPinFilm(BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, ""))
        }
    }

    /** 固件 FILM_PIN_FILENAME_MAX=32 是含结尾 NUL 的容量，能用的只有 31 字符。 */
    @Test
    fun `buildPinFilm 对超长文件名抛异常`() {
        val ok = "a".repeat(26) + ".film"          // 31 字符，上界
        assertEquals(31, ok.length)
        BleProtocol.buildPinFilm(BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, ok))

        val tooLong = "a".repeat(27) + ".film"     // 32 字符
        assertEquals(32, tooLong.length)
        val e = assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildPinFilm(BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, tooLong))
        }
        assertTrue("异常信息应指出长度：${e.message}", e.message!!.contains("31"))
    }

    /** 白名单字符集：空格、路径分隔符、非 ASCII 全部拒绝。 */
    @Test
    fun `buildPinFilm 对非法字符抛异常`() {
        listOf("my photo.film", "sub/photo.film", "photo\\a.film", "涂鸦.film", "photo*.film")
            .forEach { name ->
                assertThrows(
                    "「$name」应被拒",
                    IllegalArgumentException::class.java
                ) {
                    BleProtocol.buildPinFilm(
                        BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, name)
                    )
                }
            }
    }

    /** `.` / `..` 本身以及任意位置的 `..` —— 目录穿越的入口。 */
    @Test
    fun `buildPinFilm 对点号文件名抛异常`() {
        listOf(".", "..", "a..b.film", "..photo.film").forEach { name ->
            assertThrows(
                "「$name」应被拒",
                IllegalArgumentException::class.java
            ) {
                BleProtocol.buildPinFilm(
                    BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, name)
                )
            }
        }
    }

    /** 必须以 `.film` 结尾，且后缀之外还得有基名（`.film` 本身不是文件名）。 */
    @Test
    fun `buildPinFilm 对后缀不合规的文件名抛异常`() {
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildPinFilm(
                BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, "photo.png")
            )
        }
        val bare = assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildPinFilm(
                BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, ".film")
            )
        }
        assertTrue("应说明是缺基名而不是缺后缀：${bare.message}", bare.message!!.contains("基名"))
    }

    /**
     * 这条是本地校验存在的**主要理由**：`my_lunch.film` 看着毫无问题，但固件选菜单图用
     * `strstr` 包含匹配，它会同时被当成午餐详情页 —— 中午作为菜单被选中、窗口外作为
     * pinned 被选中，两条路径抢同一个文件。
     */
    @Test
    fun `buildPinFilm 对含餐段关键字的文件名抛异常`() {
        val e = assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildPinFilm(
                BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, "my_lunch.film")
            )
        }
        assertTrue("异常信息应点出撞上了哪个关键字：${e.message}", e.message!!.contains("lunch"))

        listOf("today_pic.film", "a_breakfast_b.film", "dinner2.film", "photo_today.film")
            .forEach { name ->
                assertThrows(
                    "「$name」应被拒",
                    IllegalArgumentException::class.java
                ) {
                    BleProtocol.buildPinFilm(
                        BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, name)
                    )
                }
            }

        // 关键字比较**区分大小写**，与固件的 strstr 一致 —— 固件那边选菜单图用的也是同一份
        // 小写关键字，my_Lunch.film 真的不会撞名。擅自放宽成忽略大小写会把设备实际接受的
        // 名字判成非法，而本函数也用来判断 0x4D 回读到的名字。
        assertTrue(BleProtocol.isPinFileNameValid("my_Lunch.film"))
    }

    /** 0x4D 的响应布局与 0x4C 载荷相同，所以构造与解析必须严格互逆。 */
    @Test
    fun `parsePinnedFilm 与 buildPinFilm 往返一致`() {
        listOf(
            BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, "draw_ab12cd.film"),
            BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 1320, 360, "photo_1a2b3c.film"),
            BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 0, 0, "p.film"),
            BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 1439, 1439, "p.film"),
            BleProtocol.PinnedFilm(BleProtocol.PinMode.OUTSIDE_MEAL, 0, 0, "a".repeat(26) + ".film")
        ).forEach { original ->
            val payload = BleProtocol.parseResponse(BleProtocol.buildPinFilm(original))!!.payload
            assertEquals("往返后应完全相同：$original", original, BleProtocol.parsePinnedFilm(payload))
        }
    }

    /**
     * 「未设置」用 `PinnedFilm(OFF, …)` 表达而不是 null，null 只留给「解析失败」。
     *
     * 两者在 UI 上必须能分开：前者显示「未置顶」并让用户去设置，后者显示「读取失败请重试」。
     * 固件的 0x4D 在未 pin 时回的正是这样一份 6 字节 OFF 配置，与线上字节一一对应。
     */
    @Test
    fun `parsePinnedFilm 把未设置解析成 OFF 而不是 null`() {
        // 固件未 pin 时的 0x4D 响应：mode=0、窗口 0、name_len=0
        val notPinned = BleProtocol.parsePinnedFilm(byteArrayOf(0, 0, 0, 0, 0, 0))
        assertNotNull("未设置不是解析失败", notPinned)
        assertEquals(BleProtocol.PinMode.OFF, notPinned!!.mode)
        assertEquals("", notPinned.fileName)

        // 单字节 0x00（我们自己发的清除帧被原样回显）语义相同
        val single = BleProtocol.parsePinnedFilm(byteArrayOf(0))
        assertNotNull(single)
        assertEquals(BleProtocol.PinMode.OFF, single!!.mode)

        // 单字节 0xFF 是固件的拒绝信号，不是 mode=255
        assertNull("0xFF 是拒绝信号", BleProtocol.parsePinnedFilm(byteArrayOf(0xFF.toByte())))
    }

    /**
     * 长度不自洽返回 null 而不是给出"半份配置"。
     *
     * 半份配置会让设备在一部分时间显示对、另一部分显示错，而 UI 上显示的是完整配置 ——
     * 两边不一致却没有任何一处报错。
     */
    @Test
    fun `parsePinnedFilm 对长度不自洽的载荷返回 null`() {
        val name = "p.film".toByteArray(Charsets.US_ASCII)
        // name_len 说 6 个字节，实际只给了 5 个
        assertNull(
            BleProtocol.parsePinnedFilm(
                byteArrayOf(1, 0, 0, 0, 0, 6) + name.copyOfRange(0, 5)
            )
        )
        // 多一个字节同样算不自洽
        assertNull(
            BleProtocol.parsePinnedFilm(byteArrayOf(1, 0, 0, 0, 0, 6) + name + byteArrayOf(0))
        )
        assertNull("不足 6 字节的非 OFF 载荷", BleProtocol.parsePinnedFilm(byteArrayOf(1, 0, 0, 0, 0)))
        assertNull(BleProtocol.parsePinnedFilm(ByteArray(0)))
        // 未知 mode（固件只认 0..3）
        assertNull(BleProtocol.parsePinnedFilm(byteArrayOf(4, 0, 0, 0, 0, 0)))
        // 窗口越界只可能来自错帧：固件写入时就拒了 ≥1440，绝不会存进记录再回读出来
        assertNull(
            BleProtocol.parsePinnedFilm(byteArrayOf(2, 0x05, 0xA0.toByte(), 0, 0, 6) + name)
        )
    }

    /**
     * 建议名必须**每一次**都合法：随机后缀只要有一种取值不合法，就是一个概率复现的
     * 「偶尔置顶失败」，而现场只能看到固件回的一个 0xFF。
     */
    @Test
    fun `suggestPinFileName 产出的名字必然合法`() {
        repeat(100) {
            listOf("photo", "draw").forEach { prefix ->
                val name = BleProtocol.suggestPinFileName(prefix)
                assertTrue("生成的名字不合法：$name", BleProtocol.isPinFileNameValid(name))
                assertTrue("应以 ${prefix}_ 开头：$name", name.startsWith("${prefix}_"))
                assertTrue("应以 .film 结尾：$name", name.endsWith(".film"))
                assertEquals("photo_xxxxxx.film 定长", prefix.length + 12, name.length)
                // 建议名必须能直接进帧
                BleProtocol.buildPinFilm(
                    BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, name)
                )
            }
        }
    }

    @Test
    fun `suggestPinFileName 对非法前缀抛异常`() {
        listOf("", "my photo", "a/b", "涂鸦", "my_lunch", "today").forEach { prefix ->
            assertThrows(
                "前缀「$prefix」应被拒",
                IllegalArgumentException::class.java
            ) { BleProtocol.suggestPinFileName(prefix) }
        }
        // 前缀太长：加上 "_xxxxxx.film" 这 12 个字符后会超过 31
        BleProtocol.suggestPinFileName("a".repeat(19))
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.suggestPinFileName("a".repeat(20))
        }
    }

    /**
     * describe() 的两个坑：空窗口与跨午夜。
     *
     * `[x, x)` 只有零长度这一种自洽读法，若显示成 "07:00–07:00 显示" 会被当成
     * "显示一整天"，与实际行为正好相反。
     */
    @Test
    fun `PinnedFilm describe 说清空窗口与跨午夜`() {
        assertEquals(
            "一直显示",
            BleProtocol.PinnedFilm(BleProtocol.PinMode.ALWAYS, 0, 0, "p.film").describe()
        )
        assertEquals(
            "22:00–06:00 显示",
            BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 1320, 360, "p.film").describe()
        )
        assertEquals(
            "空窗口（不会显示）",
            BleProtocol.PinnedFilm(BleProtocol.PinMode.WINDOW, 420, 420, "p.film").describe()
        )
        assertEquals(
            "做饭时段外显示",
            BleProtocol.PinnedFilm(BleProtocol.PinMode.OUTSIDE_MEAL, 0, 0, "p.film").describe()
        )
        assertTrue(
            BleProtocol.PinnedFilm(BleProtocol.PinMode.OFF).describe().contains("未置顶")
        )
    }

    /** UUID 必须与固件 service_ble_gatts.c 一致：写错会让 getService() 返回 null、直接连不上。 */
    @Test
    fun `GATT UUID 与固件定义一致`() {
        assertEquals("00002000-0000-1000-8000-00805f9b34fb", BleProtocol.SERVICE_UUID.toString())
        assertEquals("00002001-0000-1000-8000-00805f9b34fb", BleProtocol.WRITE_CHAR_UUID.toString())
        assertEquals("00002002-0000-1000-8000-00805f9b34fb", BleProtocol.NOTIFY_CHAR2_UUID.toString())
        assertEquals("00002003-0000-1000-8000-00805f9b34fb", BleProtocol.NOTIFY_CHAR3_UUID.toString())
    }

    // ==================== 配网字符串编码 ====================

    /**
     * 中文 SSID 必须按 UTF-8 字节发出去。
     *
     * `Charsets.US_ASCII` 对无法表示的字符**静默替换成 `?`(0x3F)** 且不抛异常，设备拿这串字节
     * 去连永远扫不到那个 AP（reason=201 NO_AP_FOUND），而 App 与固件都不报错。
     */
    @Test
    fun `buildWifiSsid 中文 SSID 按 UTF-8 编码而不是问号`() {
        val ssid = "蛮王拉稀丶"
        val packet = BleProtocol.buildWifiSsid(ssid)
        val payload = packet.copyOfRange(3, packet.size - 1)

        // 5 个中文字 UTF-8 是 15 字节；若退回 US_ASCII 会是 5 字节
        assertEquals("必须是 UTF-8 字节数", 15, payload.size)
        assertArrayEquals(ssid.toByteArray(Charsets.UTF_8), payload)
        // 一个 0x3F 都不该有 —— 那是 US_ASCII 的替换字符
        assertFalse("载荷里不能出现 '?' 替换字符", payload.any { it == 0x3F.toByte() })
        assertEquals("帧内声明的长度要等于实际载荷", payload.size, packet[2].toInt() and 0xFF)
    }

    @Test
    fun `buildWifiPassword 中文密码同样按 UTF-8 编码`() {
        val pwd = "密码123"
        val payload = BleProtocol.buildWifiPassword(pwd).let { it.copyOfRange(3, it.size - 1) }
        assertArrayEquals(pwd.toByteArray(Charsets.UTF_8), payload)
        // 「密」「码」各 3 字节 + "123" 三个 ASCII = 9
        assertEquals(2 * 3 + 3, payload.size)
    }

    @Test
    fun `buildWifiSsid ASCII 与空密码仍正常`() {
        val payload = BleProtocol.buildWifiSsid("fridge24").let { it.copyOfRange(3, it.size - 1) }
        assertArrayEquals("fridge24".toByteArray(Charsets.UTF_8), payload)
        // 开放网络：空密码合法，不该抛
        val empty = BleProtocol.buildWifiPassword("")
        assertEquals(0, empty[2].toInt() and 0xFF)
    }

    /**
     * 超长**抛异常而不是截断**：截断后的字节串是另一个 SSID，设备会去连一个不存在的网络，
     * 而现象与「密码错」无法区分。让调用方当场知道，比让用户在现场猜好得多。
     */
    @Test
    fun `buildWifiSsid 超长按字节判定并抛异常`() {
        // 11 个中文 = 33 字节 > 32，但只有 11 个字符 —— 按字符判会漏过
        val tooLong = "蛮".repeat(11)
        assertEquals(33, tooLong.toByteArray(Charsets.UTF_8).size)
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildWifiSsid(tooLong)
        }
        // 10 个中文 = 30 字节，在界内
        BleProtocol.buildWifiSsid("蛮".repeat(10))
        // 标准允许完整的 32 字节 SSID，不要求给 NUL 留空间
        BleProtocol.buildWifiSsid("a".repeat(32))
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildWifiSsid("a".repeat(33))
        }
        // 空 SSID 无意义，也要拒
        assertThrows(IllegalArgumentException::class.java) { BleProtocol.buildWifiSsid("") }
    }

    /**
     * 约定为 ASCII 的字段遇到非 ASCII 要**拒绝**，不能静默替换成问号。
     * `device_id` 变成 `???` 会让 manifest 请求打到一个不存在的路径并回 404 ——
     * 同样是「App 成功、设备静默不动」那一类失败。
     */
    @Test
    fun `ASCII 字段拒绝非 ASCII 而不是替换成问号`() {
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildDeviceId("设备001")
        }
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildManifestUrl("https://例子.com/manifest")
        }
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildFileName("涂鸦.film")
        }
        // 正常 ASCII 不受影响
        BleProtocol.buildDeviceId("dev001")
        BleProtocol.buildFileName("draw_a1b2c3.film")
    }

    /**
     * ASCII 字段超长必须拒绝，不能交给固件截断。
     *
     * 固件对这几个字段一律 `strncpy(dst, src, MAX - 1)`。截断后的 `device_token` 与「后端没登记
     * 这个 token」表现完全一样（一律 401），截断后的 `manifest_url` 指向不存在的地址、设备静默
     * 不更新。边界值一并钉住：正好等于上限必须通过，否则改常量时容易差一。
     */
    @Test
    fun `ASCII 字段超长被拒绝且边界值可用`() {
        // device_token：SFM_DEVICE_TOKEN_MAX(128) - 1
        BleProtocol.buildDeviceToken("a".repeat(BleProtocol.DEVICE_TOKEN_MAX_CHARS))
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildDeviceToken("a".repeat(BleProtocol.DEVICE_TOKEN_MAX_CHARS + 1))
        }

        // device_id：SFM_DEVICE_ID_MAX(48) - 1
        BleProtocol.buildDeviceId("d".repeat(BleProtocol.DEVICE_ID_MAX_CHARS))
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildDeviceId("d".repeat(BleProtocol.DEVICE_ID_MAX_CHARS + 1))
        }

        // manifest_url：SFM_MANIFEST_URL_MAX(128) - 1
        val base = "https://example.com/"
        BleProtocol.buildManifestUrl(
            base + "u".repeat(BleProtocol.MANIFEST_URL_MAX_CHARS - base.length)
        )
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildManifestUrl(
                base + "u".repeat(BleProtocol.MANIFEST_URL_MAX_CHARS - base.length + 1)
            )
        }

        // 文件名：卡在协议帧的 LEN 单字节上，而非固件缓冲。256 字符会让
        // packet[2] = data.size.toByte() 静默写成 LEN=0，固件收到空文件名并拒收整帧。
        BleProtocol.buildFileName("f".repeat(BleProtocol.FILE_NAME_MAX_CHARS))
        assertThrows(IllegalArgumentException::class.java) {
            BleProtocol.buildFileName("f".repeat(BleProtocol.FILE_NAME_MAX_CHARS + 1))
        }
    }

    /**
     * 被截断的帧必须整帧拒绝：既不抛异常，也不当成好帧解出空载荷。
     *
     * 现实中最长的响应是 0x43 状态帧（8 + err_len 字节载荷），err_len 实测到 19，整帧 23 字节。
     * 越界读会在 GATT 回调线程抛异常、latch 永不放行（表现为「查询永远超时」）；把载荷降级为空
     * 数组则会让上层算出一个错的电量与版本号。
     */
    @Test
    fun `截断帧被整帧拒绝`() {
        // LEN 声明 19 字节载荷（整帧应为 23），实际只给 20 字节。
        val truncated = ByteArray(20)
        truncated[0] = BleProtocol.HEAD.toByte()
        truncated[1] = BleProtocol.CH_STATUS_GET.toByte()
        truncated[2] = 19
        assertNull("截断帧必须返回 null", BleProtocol.parseResponse(truncated))

        // 边界：整帧长度正好等于 4 + LEN 时必须能解析（否则修复会顺手废掉合法帧）。
        val exact = ByteArray(4 + 19)
        exact[0] = BleProtocol.HEAD.toByte()
        exact[1] = BleProtocol.CH_STATUS_GET.toByte()
        exact[2] = 19
        for (i in 0 until 19) exact[3 + i] = (i + 1).toByte()
        exact[3 + 19] = BleProtocol.calculateChecksum(exact.copyOf(3 + 19))
        val parsed = BleProtocol.parseResponse(exact)
        assertNotNull("正好等于 4+LEN 的帧应可解析", parsed)
        assertEquals(19, parsed!!.payload.size)
    }
}
