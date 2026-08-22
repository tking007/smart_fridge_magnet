package org.smartfridgemagnet.transfer

import java.util.UUID
import kotlin.random.Random

/**
 * FrameFilm BLE 协议封装
 *
 * 数据包格式：
 * +--------+--------+--------+----------+--------+
 * |  HEAD  |   CH   |  LEN   |  DATA    |  SUM   |
 * +--------+--------+--------+----------+--------+
 * |  0x55  |  1 B   |  1 B   |  N B     |  1 B   |
 * +--------+--------+--------+----------+--------+
 */
object BleProtocol {

    const val HEAD = 0x55

    // ==================== GATT UUID（唯一权威定义，以固件为准）====================
    // 固件 service_ble_gatts.c：Service 0x2000、Char1 0x2001(WRITE|NOTIFY|WRITE_NR)、
    // Char2 0x2002(NOTIFY)、Char3 0x2003(NOTIFY)。固件可能从三个通道任一发通知，需全部订阅。
    // 其它文件统一引用这里，不要各自定义 UUID。
    val SERVICE_UUID: UUID = UUID.fromString("00002000-0000-1000-8000-00805f9b34fb")
    val WRITE_CHAR_UUID: UUID = UUID.fromString("00002001-0000-1000-8000-00805f9b34fb")
    val NOTIFY_CHAR_UUID: UUID = UUID.fromString("00002001-0000-1000-8000-00805f9b34fb")
    val NOTIFY_CHAR2_UUID: UUID = UUID.fromString("00002002-0000-1000-8000-00805f9b34fb")
    val NOTIFY_CHAR3_UUID: UUID = UUID.fromString("00002003-0000-1000-8000-00805f9b34fb")

    // 命令通道
    const val CH_FILE_NAME = 0x00
    const val CH_FILE_LEN = 0x01
    const val CH_FILE_DATA = 0x02
    const val CH_FILE_START = 0x03
    const val CH_FILE_STOP = 0x04
    const val CH_FILE_DELETE = 0x05
    const val CH_FILE_LIST = 0x06
    const val CH_FILE_DISPLAY = 0x07
    const val CH_FILE_DISPLAY_GET = 0x08

    // 设备控制
    const val CH_CTRL_MODE = 0x20
    const val CH_CTRL_MODE_GET = 0x21
    const val CH_CTRL_RESET = 0x22
    const val CH_CTRL_PWRREAD = 0x23
    const val CH_CTRL_REBOOT = 0x24

    // 休眠开关（0x25/0x26）只管「要不要深睡」；**什么时候醒**由 0x48 唤醒时间表主导。
    // 固件的 0x27–0x2A（上游单点定时唤醒）本项目不用、也不定义常量：两套并用会互相覆盖
    // 唤醒时机且两端都不报错。需要改唤醒行为时只动 0x48。
    const val CH_SLEEP_ONOFF = 0x25
    const val CH_SLEEP_ONOFF_GET = 0x26

    // WiFi 配置
    const val CH_WIFI_ENABLE = 0x30
    const val CH_WIFI_ENABLE_GET = 0x31
    const val CH_WIFI_SSID = 0x32
    const val CH_WIFI_SSID_GET = 0x33
    const val CH_WIFI_PASSWORD = 0x34
    const val CH_WIFI_PASSWORD_GET = 0x35
    const val CH_FILM_API_URL = 0x36
    const val CH_FILM_API_URL_GET = 0x37
    const val CH_WIFI_CONNECT = 0x38
    const val CH_WIFI_DISCONNECT = 0x39
    const val CH_WIFI_CONNECT_GET = 0x3A
    const val CH_WIFI_CLEAR = 0x3B
    const val CH_DOWNLOAD_START = 0x3C
    const val CH_DOWNLOAD_STATUS_GET = 0x3D

    // 配网与同步（固件 docs/ble_protocol.md §4.2/§4.5）
    const val CH_MANIFEST_URL = 0x3E
    const val CH_MANIFEST_URL_GET = 0x3F
    const val CH_DEVICE_ID = 0x40
    const val CH_DEVICE_ID_GET = 0x41
    const val CH_DEVICE_TOKEN = 0x42       // 只写不可读（固件无读回 opcode）
    const val CH_STATUS_GET = 0x43         // 合并状态查询
    const val CH_SYNC_NOW = 0x44           // 立即触发一次 Wi-Fi 同步
    const val CH_FILE_CLEAR_ALL = 0x45     // 清空设备上全部 .film
    const val CH_FILE_SILENT_RECV = 0x46   // 静默接收开关：只保存不上屏
    const val CH_FILE_DISPLAY_NOW = 0x47   // 按当前做饭时段显示（窗口外显示今日总览）

    // 唤醒时间表与轮询周期（固件 docs/ble_protocol.md §4.11）
    const val CH_WAKE_SCHEDULE = 0x48      // 设置唤醒时间表
    const val CH_WAKE_SCHEDULE_GET = 0x49  // 查询唤醒时间表（响应布局与 0x48 相同）
    const val CH_POLL_INTERVAL = 0x4A      // 设置轮询周期（秒）
    const val CH_POLL_INTERVAL_GET = 0x4B  // 查询轮询周期（秒）

    // 置顶自定义画面（固件 docs/ble_protocol.md §4.12）
    const val CH_PIN_FILM = 0x4C           // 设置置顶画面
    const val CH_PIN_FILM_GET = 0x4D       // 查询置顶画面（响应布局与 0x4C 载荷相同）

    /** 唤醒点数量上限（固件 WAKE_SCHEDULE_MAX_SLOTS）。每多一个点就是每天多一次完整联网。 */
    const val WAKE_SCHEDULE_MAX_SLOTS = 6

    /** 唤醒点是「当地时间的分钟数」，合法域 [0, 1439]。 */
    const val WAKE_SLOT_MAX_MINUTE = 1439

    /** 兜底唤醒周期上界（分钟，7 天）。0 表示不启用兜底；出厂默认 1440（24 小时）。 */
    const val WAKE_FALLBACK_MAX_MINUTES = 10080

    /** 轮询周期的固件钳制区间（秒）。 */
    const val POLL_INTERVAL_MIN_SEC = 30
    const val POLL_INTERVAL_MAX_SEC = 86400

    /** 置顶窗口两端是「当地时间的分钟数」，合法域 [0, 1439]（与唤醒时间表同一约定）。 */
    const val PIN_WINDOW_MAX_MINUTE = 1439

    /**
     * pinned 文件名的**有效字符数**上限。
     *
     * 固件 `FILM_PIN_FILENAME_MAX` 是 32，但那是含结尾 NUL 的缓冲区容量，校验按
     * `n >= 32` 拒绝，所以可用的只有 31 个字符。
     */
    const val PIN_FILE_NAME_MAX_LEN = 31

    /** pinned 文件必须以 `.film` 结尾，且后缀之外还得有基名（`.film` 本身非法）。 */
    const val PIN_FILE_NAME_SUFFIX = ".film"

    /**
     * Wi-Fi SSID / 密码的**字节数**上限（UTF-8 编码后，不含结尾 NUL）。
     *
     * SSID 对齐 802.11 的 32 字节字段；密码对齐固件的 64 字节缓冲（有效 63 + 终止符）。
     * 按字节而非字符计：中文一字 UTF-8 占 3 字节，按字符判会让超长 SSID 通过校验后被
     * 固件截断，截断后的字节串是另一个 SSID，表现与「密码错」无法区分。
     */
    const val SSID_MAX_BYTES = 32
    const val PASSWORD_MAX_BYTES = 63

    /**
     * ASCII-only 字段的字符数上限，对齐固件 `service_param.h` 的 `SFM_*_MAX` 减终止符。
     *
     * 超长必须拒绝而不是交给固件 `strncpy` 截断：截断后的 device_token 与「后端未登记该
     * token」表现相同（一律 401），截断后的 manifest_url 则指向不存在的地址、设备静默不更新。
     */
    const val MANIFEST_URL_MAX_CHARS = 127   // SFM_MANIFEST_URL_MAX(128) - 1
    const val DEVICE_ID_MAX_CHARS = 47       // SFM_DEVICE_ID_MAX(48) - 1
    const val DEVICE_TOKEN_MAX_CHARS = 127   // SFM_DEVICE_TOKEN_MAX(128) - 1

    /**
     * FILE_NAME 载荷的字符数上限。
     *
     * 卡在协议帧而不是固件缓冲上：`LEN` 只有一个字节，[buildPacket] 的
     * `packet[2] = data.size.toByte()` 对超过 255 的载荷会静默取低 8 位。
     */
    const val FILE_NAME_MAX_CHARS = 255

    /**
     * pinned 文件名里**禁止出现**的关键字（固件 film_pin.c 的 `k_reserved_keywords`）。
     *
     * 固件选菜单图用 `strstr` 包含匹配（发送方会给菜单图加序号前缀，见 §4.9），
     * 所以 `my_lunch.film` 会同时被菜单与 pinned 两条选图路径命中，抢同一个文件。
     */
    val PIN_RESERVED_KEYWORDS = listOf("today", "breakfast", "lunch", "dinner")

    /** 计算校验和：帧首字节到载荷末字节的和取低 8 位。 */
    fun calculateChecksum(data: ByteArray): Byte {
        var sum = 0
        for (byte in data) {
            sum += byte.toInt() and 0xFF
        }
        return (sum and 0xFF).toByte()
    }

    /** 构建命令包：`HEAD | CH | LEN | DATA | SUM`。 */
    fun buildPacket(ch: Int, data: ByteArray = ByteArray(0)): ByteArray {
        val packet = ByteArray(4 + data.size)
        packet[0] = HEAD.toByte()
        packet[1] = ch.toByte()
        packet[2] = data.size.toByte()
        System.arraycopy(data, 0, packet, 3, data.size)
        packet[3 + data.size] = calculateChecksum(packet.copyOf(3 + data.size))
        return packet
    }

    /** 构建 FILE_START 包 */
    fun buildFileStart(): ByteArray {
        return buildPacket(CH_FILE_START)
    }

    /**
     * 构建 FILE_NAME 包。
     *
     * 载荷是纯文件名字节，不补结尾 `\0`：固件 `ble_filename_is_safe()` 只接受可打印
     * ASCII（拒绝 NUL 与所有控制字节），并在接受后自行补终止符。多发一个 `\0` 会让
     * 整帧被判 unsafe 拒绝，状态机停在 STARTED，后续 FILE_DATA/FILE_STOP 全部丢弃。
     */
    fun buildFileName(fileName: String): ByteArray {
        return buildPacket(CH_FILE_NAME, asciiPayload(fileName, "文件名", FILE_NAME_MAX_CHARS))
    }

    /** 构建 FILE_LEN 包，4 字节大端文件长度。 */
    fun buildFileLen(fileSize: Int): ByteArray {
        val data = ByteArray(4)
        data[0] = ((fileSize shr 24) and 0xFF).toByte() // Big-Endian
        data[1] = ((fileSize shr 16) and 0xFF).toByte()
        data[2] = ((fileSize shr 8) and 0xFF).toByte()
        data[3] = (fileSize and 0xFF).toByte()
        return buildPacket(CH_FILE_LEN, data)
    }

    /** 构建 FILE_DATA 包 */
    fun buildFileData(chunk: ByteArray): ByteArray {
        return buildPacket(CH_FILE_DATA, chunk)
    }

    /** 构建 FILE_STOP 包 */
    fun buildFileStop(): ByteArray {
        return buildPacket(CH_FILE_STOP)
    }

    /** 构建 FILE_DISPLAY 包，按设备侧文件 id 上屏。 */
    fun buildFileDisplay(fileId: Int): ByteArray {
        return buildPacket(CH_FILE_DISPLAY, byteArrayOf(fileId.toByte()))
    }

    /** 构建 WIFI_ENABLE 包 */
    fun buildWifiEnable(enable: Boolean): ByteArray {
        return buildPacket(CH_WIFI_ENABLE, byteArrayOf(if (enable) 1 else 0))
    }

    /**
     * 把一个**约定为 ASCII** 的字段编码成载荷，非 ASCII 一律拒绝而不是静默替换。
     *
     * `String.toByteArray(Charsets.US_ASCII)` 遇到无法表示的字符会安静地写入 `?`(0x3F)
     * 而不抛异常，于是 device_id / manifest_url / 文件名都会变成一个语义不同的值，
     * 表现为「App 侧一路成功、设备侧静默不动」。
     *
     * SSID 与密码不走这里 —— 它们允许中文，见 [buildWifiSsid]。
     */
    private fun asciiPayload(value: String, field: String, maxChars: Int): ByteArray {
        val bad = value.firstOrNull { it.code !in 0x20..0x7E }
        require(bad == null) {
            "$field 只允许可打印 ASCII（0x20..0x7E），发现字符 " +
                "'${bad}'（U+%04X）：%s".format(bad!!.code, value)
        }
        // 全 ASCII 已由上一条保证，此时字符数 == 字节数，可直接按 length 判。
        require(value.length <= maxChars) {
            "$field 过长：${value.length} 字符，上限 $maxChars"
        }
        return value.toByteArray(Charsets.US_ASCII)
    }

    /**
     * 构建 WIFI_SSID 包。
     *
     * 必须 UTF-8 而不是 US_ASCII：后者对无法表示的字符静默替换成 `?`(0x3F)，中文 SSID
     * 会变成一串问号，设备永远扫不到那个 AP（`reason=201 NO_AP_FOUND`）。中文/emoji
     * SSID 在家用路由器与手机热点上很常见，这条路径必须按字节透传；固件侧只做长度与
     * NUL 检查，不要求 ASCII。
     */
    fun buildWifiSsid(ssid: String): ByteArray {
        val ssidBytes = ssid.toByteArray(Charsets.UTF_8)
        // 802.11 与固件 wifi_config_t.sta.ssid 都是 32 字节；满长 SSID 不需要结尾 NUL。
        // 超长抛异常而不是截断：截断后的字节串是另一个 SSID，现象与「密码错」无法区分。
        require(ssidBytes.size in 1..SSID_MAX_BYTES) {
            "SSID 需为 1..$SSID_MAX_BYTES 字节（UTF-8 编码后），当前 ${ssidBytes.size}：" +
                "中文每字约占 3 字节"
        }
        return buildPacket(CH_WIFI_SSID, ssidBytes)
    }

    /**
     * 构建 WIFI_PASSWORD 包。
     *
     * 同样必须 UTF-8（理由见 [buildWifiSsid]）：WPA2 passphrase 规范上是 ASCII，但路由器
     * 允许设中文密码，用 US_ASCII 编码会让「密码对却认证失败」。
     */
    fun buildWifiPassword(password: String): ByteArray {
        val pwdBytes = password.toByteArray(Charsets.UTF_8)
        // 固件 wifi_config_t.sta.password 是 64 字节（含终止符），有效 63。
        // 空密码是合法的（开放网络），所以下界是 0。
        require(pwdBytes.size <= PASSWORD_MAX_BYTES) {
            "Wi-Fi 密码需 ≤$PASSWORD_MAX_BYTES 字节（UTF-8 编码后），当前 ${pwdBytes.size}"
        }
        return buildPacket(CH_WIFI_PASSWORD, pwdBytes)
    }

    /** 构建 WIFI_CONNECT 包 */
    fun buildWifiConnect(): ByteArray {
        return buildPacket(CH_WIFI_CONNECT)
    }

    /** 构建 WIFI_CONNECT_GET 包 */
    fun buildWifiConnectGet(): ByteArray {
        return buildPacket(CH_WIFI_CONNECT_GET)
    }

    // ==================== 配网与同步（0x3E–0x44）====================
    // 受单字节 LEN 帧限制，配网按字段逐条 set，不在单帧携带全部字段。
    // 字符串载荷一律不带结尾 \0，由固件自行补终止符（与 FILE_NAME 同一约定）。

    /** 设置 manifest_url（≤127 字节） */
    fun buildManifestUrl(url: String): ByteArray {
        return buildPacket(
            CH_MANIFEST_URL,
            asciiPayload(url, "manifest_url", MANIFEST_URL_MAX_CHARS)
        )
    }

    /** 设置 device_id（≤47 字节） */
    fun buildDeviceId(deviceId: String): ByteArray {
        return buildPacket(
            CH_DEVICE_ID,
            asciiPayload(deviceId, "device_id", DEVICE_ID_MAX_CHARS)
        )
    }

    /** 设置 device_token（≤127 字节，落盘加密且无读回命令） */
    fun buildDeviceToken(token: String): ByteArray {
        return buildPacket(
            CH_DEVICE_TOKEN,
            asciiPayload(token, "device_token", DEVICE_TOKEN_MAX_CHARS)
        )
    }

    /** 合并状态查询 */
    fun buildStatusGet(): ByteArray {
        return buildPacket(CH_STATUS_GET)
    }

    /** SYNC_NOW：立即触发一次 Wi-Fi 同步检查（不改变后台轮询行为） */
    fun buildSyncNow(): ByteArray {
        return buildPacket(CH_SYNC_NOW)
    }

    /** 休眠模式开关（0x25）：1=开启省电休眠，0=关闭常亮 */
    fun buildSleepOnOff(enable: Boolean): ByteArray {
        return buildPacket(CH_SLEEP_ONOFF, byteArrayOf(if (enable) 1 else 0))
    }

    /** 休眠模式查询（0x26） */
    fun buildSleepOnOffGet(): ByteArray {
        return buildPacket(CH_SLEEP_ONOFF_GET)
    }

    /** 电量查询（0x23） */
    fun buildPowerRead(): ByteArray {
        return buildPacket(CH_CTRL_PWRREAD)
    }

    /**
     * 开始下载（`0x3C`）。
     *
     * 固件侧已把它作为 [buildSyncNow]（`0x44`）的兼容别名，新代码请直接用 [buildSyncNow]。
     * 同理 [CH_FILM_API_URL] / [CH_FILM_API_URL_GET]（`0x36`/`0x37`）在固件侧已停用
     * （写入被忽略、查询恒返回空串），取图地址请用 [buildManifestUrl]（`0x3E`）。
     * 详见 embedded/smart_fridge_magnet/docs/ble_protocol.md §4.11。
     */
    @Deprecated("固件侧已改为 0x44 SYNC_NOW 的别名", ReplaceWith("buildSyncNow()"))
    fun buildDownloadStart(): ByteArray {
        return buildPacket(CH_DOWNLOAD_START)
    }

    // ==================== 设备图片管理 ====================

    /** 查询设备上现存的 .film 列表。响应为「每个文件一帧」，用 parseFileListEntry 解析。 */
    fun buildFileList(): ByteArray {
        return buildPacket(CH_FILE_LIST)
    }

    /** 删除指定 id 的文件。注意：删除后设备侧 id 会重排，应重新查询列表。 */
    fun buildFileDelete(fileId: Int): ByteArray {
        return buildPacket(CH_FILE_DELETE, byteArrayOf(fileId.toByte()))
    }

    /** 清空设备上全部 .film。不清屏，当前画面保留到下一张图上屏。 */
    fun buildFileClearAll(): ByteArray {
        return buildPacket(CH_FILE_CLEAR_ALL)
    }

    /**
     * 静默接收开关。
     *
     * 连发多张时**必须**先开启：电子纸刷一次约 20 秒，若每张 `FILE_STOP` 都触发刷屏，
     * 对端会在等待中 GATT 超时断连（实测单张可传、连发四张必失败）。
     * 传最后一张前关闭，于是只刷一次，且最后那张成为默认画面。
     */
    fun buildSilentReceive(enable: Boolean): ByteArray {
        return buildPacket(CH_FILE_SILENT_RECV, byteArrayOf(if (enable) 1 else 0))
    }

    /**
     * 让设备按当前时间决定显示哪一张：处于做饭时间窗口就显示那一餐的详细做法，
     * 窗口外显示今日总览。整套下发完毕后调用一次。
     *
     * 做饭窗口（固件侧定义）：早 06:00–09:00、午 11:00–13:30、晚 17:00–19:30。
     * 设备未配网时时间不可信，固件会直接回落显示今日总览而不猜时段。
     */
    fun buildDisplayNow(): ByteArray {
        return buildPacket(CH_FILE_DISPLAY_NOW)
    }

    // ==================== 唤醒时间表与轮询周期（0x48–0x4B）====================
    //
    // 唤醒一次要「连 Wi-Fi → TLS 握手 → 拉 manifest → 可能下载 4×209120 字节 → 刷屏」，
    // 电池成本很高，不能按固定短周期猛醒。App 只把后端 daily_publish_time 写成一个槽位，
    // 再配一个最长 N 分钟必醒一次的自愈兜底；设备取两者较小值。用餐时间只参与本地切图。

    /**
     * 唤醒时间表（0x48 / 0x49 共用同一布局）。
     *
     * @param slotMinutes 唤醒时刻，每项是**当地时间的分钟数** `hour*60+min`，最多 6 个
     * @param fallbackMinutes 兜底唤醒周期（**分钟**），0 = 不启用兜底
     */
    data class WakeSchedule(
        val slotMinutes: List<Int>,
        val fallbackMinutes: Int
    ) {
        /** 唤醒点的 "HH:MM" 文本，供 UI 与日志展示。 */
        fun slotLabels(): List<String> = slotMinutes.map { formatMinuteOfDay(it) }
    }

    /**
     * 分钟数 -> "HH:MM"。
     *
     * 单独抽出来是因为有 UI 时间表编辑、日志、[WakeSchedule.slotLabels] 三个使用方。
     * 越界值原样打印成负数/超 24 时，比静默取模更容易看出是上游算错了。
     */
    fun formatMinuteOfDay(minuteOfDay: Int): String =
        "%02d:%02d".format(minuteOfDay / 60, minuteOfDay % 60)

    /**
     * 构建 WAKE_SCHEDULE 包（`0x48`）。
     *
     * 布局：`[count][slot0_hi][slot0_lo]…[slotN_hi][slotN_lo][fallback_hi][fallback_lo]`，
     * 长度恒为 `1 + 2*count + 2`。
     *
     * 本地先校验、非法就抛，不把必然被拒的帧发出去：固件校验失败只回一个 `0xFF` 且整条
     * 拒绝，App 侧从那一个字节看不出是哪一项不合法。非法值意味着调用方把时刻算错了，
     * 钳制成「看起来能用」的值只会让设备在错误时刻唤醒联网、纯耗电。
     *
     * @throws IllegalArgumentException count > 6、任一 slot 不在 [0,1439]、
     *         或 fallback 不在 [0,10080] 时
     */
    fun buildWakeSchedule(schedule: WakeSchedule): ByteArray {
        val slots = schedule.slotMinutes
        require(slots.size <= WAKE_SCHEDULE_MAX_SLOTS) {
            "唤醒点最多 $WAKE_SCHEDULE_MAX_SLOTS 个，当前给了 ${slots.size} 个"
        }
        slots.forEachIndexed { index, minute ->
            require(minute in 0..WAKE_SLOT_MAX_MINUTE) {
                "第 ${index + 1} 个唤醒点 $minute 分钟超出 [0,$WAKE_SLOT_MAX_MINUTE]"
            }
        }
        require(schedule.fallbackMinutes in 0..WAKE_FALLBACK_MAX_MINUTES) {
            "兜底周期 ${schedule.fallbackMinutes} 分钟超出 [0,$WAKE_FALLBACK_MAX_MINUTES]"
        }

        val data = ByteArray(1 + 2 * slots.size + 2)
        data[0] = slots.size.toByte()
        slots.forEachIndexed { index, minute ->
            data[1 + index * 2] = ((minute shr 8) and 0xFF).toByte()   // Big-Endian
            data[2 + index * 2] = (minute and 0xFF).toByte()
        }
        data[data.size - 2] = ((schedule.fallbackMinutes shr 8) and 0xFF).toByte()
        data[data.size - 1] = (schedule.fallbackMinutes and 0xFF).toByte()
        return buildPacket(CH_WAKE_SCHEDULE, data)
    }

    /** 构建 WAKE_SCHEDULE_GET 包（`0x49`，无载荷）。响应布局与 0x48 完全相同。 */
    fun buildWakeScheduleGet(): ByteArray {
        return buildPacket(CH_WAKE_SCHEDULE_GET)
    }

    /**
     * 构建 POLL_INTERVAL 包（`0x4A`），4 字节大端**秒**数。
     *
     * 超范围钳制而不是抛异常（与 [buildWakeSchedule] 刻意不同）：这里只是量程问题，
     * UI 滑块边界与固件钳制区间一致，拖到端点时按端点生效最符合直觉。先钳一次是为了让
     * 返回值与 [parsePollIntervalSeconds] 读回的值对得上。
     */
    fun buildPollInterval(seconds: Int): ByteArray {
        val clamped = seconds.coerceIn(POLL_INTERVAL_MIN_SEC, POLL_INTERVAL_MAX_SEC)
        val data = ByteArray(4)
        data[0] = ((clamped shr 24) and 0xFF).toByte()   // Big-Endian
        data[1] = ((clamped shr 16) and 0xFF).toByte()
        data[2] = ((clamped shr 8) and 0xFF).toByte()
        data[3] = (clamped and 0xFF).toByte()
        return buildPacket(CH_POLL_INTERVAL, data)
    }

    /** 构建 POLL_INTERVAL_GET 包（`0x4B`，无载荷）。 */
    fun buildPollIntervalGet(): ByteArray {
        return buildPacket(CH_POLL_INTERVAL_GET)
    }

    /**
     * 解析唤醒时间表响应载荷（`0x49`，也可用于回读 `0x48` 的同布局响应）。
     *
     * 长度与 count 不自洽时返回 null 而不是给出半张时间表：半张会让设备按错误计划唤醒，
     * 而 UI 上显示的是完整配置，两边不一致却无处报错。固件拒收时回的单字节 `0xFF`
     * 也落到这里（count=255 已越界），同样是 null。
     */
    fun parseWakeSchedule(payload: ByteArray): WakeSchedule? {
        if (payload.isEmpty()) return null
        val count = payload[0].toInt() and 0xFF
        if (count > WAKE_SCHEDULE_MAX_SLOTS) return null
        if (payload.size != 1 + 2 * count + 2) return null

        val slots = ArrayList<Int>(count)
        for (i in 0 until count) {
            slots.add(
                ((payload[1 + i * 2].toInt() and 0xFF) shl 8) or
                        (payload[2 + i * 2].toInt() and 0xFF)
            )
        }
        val fallback = ((payload[payload.size - 2].toInt() and 0xFF) shl 8) or
                (payload[payload.size - 1].toInt() and 0xFF)
        return WakeSchedule(slots, fallback)
    }

    /**
     * 解析轮询周期响应载荷（`0x4A` 的 Ack 与 `0x4B` 的响应），4 字节大端秒数。
     *
     * **只接受恰好 4 字节**：固件在载荷长度非法时回 1 字节 `0xFF`，那是拒绝信号而不是
     * 值 255。非正数同理只可能来自错帧（固件钳制后至少 30 秒），一并当失败。
     */
    fun parsePollIntervalSeconds(payload: ByteArray): Int? {
        if (payload.size != 4) return null
        val seconds = ((payload[0].toInt() and 0xFF) shl 24) or
                ((payload[1].toInt() and 0xFF) shl 16) or
                ((payload[2].toInt() and 0xFF) shl 8) or
                (payload[3].toInt() and 0xFF)
        return if (seconds <= 0) null else seconds
    }

    // ==================== 置顶自定义画面（0x4C / 0x4D）====================
    //
    // 手绘涂鸦与相册照片本身不需要新协议：后端渲染成 .film 走既有 FILE_* 序列即可上屏。
    // 要新协议的是**让它留在屏幕上** —— 固件里「上次切到哪个时段」是普通 static，深睡
    // 唤醒后归零，时间一同步就把自定义图换回菜单。所以持续显示靠一份持久化的 pin：
    // 一个文件名 + 一个模式（+ 可选窗口）。

    /**
     * 置顶模式（固件 `film_pin_mode_t`）。
     *
     * 用 enum 而不是裸 Int：固件把 `mode > 3` 列为拒绝规则之一，枚举让这条规则在类型层面
     * 不可表达，[buildPinFilm] 也就不必再校验它。
     */
    enum class PinMode(val raw: Int) {
        /** 不 pin。选图行为与没有本功能时完全一致；发这个值等于**擦掉整条记录**。 */
        OFF(0),

        /** 一直显示自定义图。**不依赖时间**，未配网也能用。 */
        ALWAYS(1),

        /** 只在 `[start, end)` 显示自定义图，其余时间按菜单逻辑。 */
        WINDOW(2),

        /** 做饭窗口内显示菜单，窗口外显示自定义图。 */
        OUTSIDE_MEAL(3);

        companion object {
            /** 从线上字节还原；未知取值返回 null（固件同样把未知 mode 与 OFF 同处）。 */
            fun fromRaw(raw: Int): PinMode? = values().firstOrNull { it.raw == raw }
        }
    }

    /**
     * 置顶画面配置（0x4C 载荷 / 0x4D 响应共用同一布局）。
     *
     * @param windowStartMinutes 窗口起点，**当地时间分钟数**，仅 [PinMode.WINDOW] 使用
     * @param windowEndMinutes 窗口终点（不含）。`start > end` 表示**跨午夜**，
     *        `22:00–06:00` 就写 `start=1320, end=360`；`start == end` 是**空窗口**
     *        （恒不显示），想表达「全天」用 [PinMode.ALWAYS]
     * @param fileName pinned 文件的安全基名，规则见 [isPinFileNameValid]
     */
    data class PinnedFilm(
        val mode: PinMode,
        val windowStartMinutes: Int = 0,
        val windowEndMinutes: Int = 0,
        val fileName: String = ""
    ) {
        /**
         * 给 UI 用的人话描述。
         *
         * 放在数据类里而不是 UI 层：这段文字要在设备页、确认弹窗、日志三处出现。
         */
        fun describe(): String = when (mode) {
            PinMode.OFF -> "未置顶（按菜单逻辑自动选图）"
            PinMode.ALWAYS -> "一直显示"
            PinMode.WINDOW ->
                // 空窗口单独说清楚：显示 "07:00–07:00 显示" 会被读成「显示一整天」，
                // 与 `[x, x)` 的零长度语义正好相反。
                if (windowStartMinutes == windowEndMinutes) {
                    "空窗口（不会显示）"
                } else {
                    "${formatMinuteOfDay(windowStartMinutes)}–" +
                        "${formatMinuteOfDay(windowEndMinutes)} 显示"
                }
            PinMode.OUTSIDE_MEAL -> "做饭时段外显示"
        }
    }

    /**
     * pinned 文件名是否合法（固件 `film_pin_cfg_is_valid()` 的文件名部分，逐条对齐）。
     *
     * 规则：长度 `1..31`、字符只取 `[0-9A-Za-z._-]`、不是 `.` 或 `..`、任意位置不含 `..`、
     * 以 `.film` 结尾且后缀之外还有基名、不含 [PIN_RESERVED_KEYWORDS] 任一关键字。
     *
     * 关键字比较**区分大小写**，与固件的 `strstr` 一致：放宽成忽略大小写会把设备实际接受
     * 的名字判成非法，而本函数也用来判断 `0x4D` 回读到的名字。
     */
    fun isPinFileNameValid(name: String): Boolean = pinFileNameRejectReason(name) == null

    /**
     * 文件名不合法的**具体原因**，合法时返回 null。
     *
     * 单独抽出来是为了让 [buildPinFilm] 的异常信息能指出哪一条不满足：固件拒收只回一个
     * `0xFF`，App 侧这条消息是现场唯一的线索。
     */
    private fun pinFileNameRejectReason(name: String): String? {
        if (name.isEmpty()) return "文件名为空"
        // 字符集放在长度之前判：白名单是纯 ASCII，过了这一关才有「字符数 == 字节数」，
        // 否则一个 10 个汉字的名字会被算成 10 而实际是 30 字节。
        val bad = name.firstOrNull { !isPinNameCharAllowed(it) }
        if (bad != null) {
            return "文件名含非法字符 '$bad'，只允许 [0-9A-Za-z._-]"
        }
        if (name.length > PIN_FILE_NAME_MAX_LEN) {
            return "文件名 ${name.length} 字符超过上限 $PIN_FILE_NAME_MAX_LEN"
        }
        if (name == "." || name == "..") return "文件名不能是 '$name'"
        if (name.contains("..")) return "文件名不能含 '..'（目录穿越）"
        if (!name.endsWith(PIN_FILE_NAME_SUFFIX)) {
            return "文件名必须以 $PIN_FILE_NAME_SUFFIX 结尾"
        }
        if (name.length <= PIN_FILE_NAME_SUFFIX.length) {
            return "文件名只有后缀 $PIN_FILE_NAME_SUFFIX 而没有基名"
        }
        val keyword = PIN_RESERVED_KEYWORDS.firstOrNull { name.contains(it) }
        if (keyword != null) {
            return "文件名不能含关键字 '$keyword'（会与菜单图的 strstr 包含匹配撞名）"
        }
        return null
    }

    /** 文件名白名单字符集（与固件 film_pin.c 同一份判据）。 */
    private fun isPinNameCharAllowed(c: Char): Boolean =
        c in '0'..'9' || c in 'A'..'Z' || c in 'a'..'z' ||
            c == '.' || c == '_' || c == '-'

    /**
     * 造一个**一定合法**的 pinned 文件名：`<prefix>_<6位随机hex>.film`。
     *
     * 随机后缀而不是固定名或时间戳：固定名会让每次发新图覆盖旧文件，时间戳会撞上 31
     * 字符上限。
     *
     * @param prefix 用途前缀，约定用 `photo`（相册照片）/ `draw`（手绘涂鸦），
     *        见 docs/contracts/README.md
     * @throws IllegalArgumentException 前缀为空、含 `[0-9A-Za-z_-]` 之外的字符、
     *         含保留关键字，或加上 `_xxxxxx.film` 后会超长时
     */
    fun suggestPinFileName(prefix: String): String {
        require(prefix.isNotEmpty()) { "前缀不能为空" }
        // 前缀里连 '.' 也不许：允许了就要再操心 '..' 与「后缀出现在中间」两种情形。
        val bad = prefix.firstOrNull {
            !(it in '0'..'9' || it in 'A'..'Z' || it in 'a'..'z' || it == '_' || it == '-')
        }
        require(bad == null) { "前缀 '$prefix' 含非法字符 '$bad'，只允许 [0-9A-Za-z_-]" }
        PIN_RESERVED_KEYWORDS.firstOrNull { prefix.contains(it) }?.let {
            throw IllegalArgumentException("前缀 '$prefix' 含保留关键字 '$it'")
        }
        // 定长尾巴：'_' + 6 位 hex + ".film"
        val tailLen = 1 + PIN_NAME_RANDOM_HEX_LEN + PIN_FILE_NAME_SUFFIX.length
        require(prefix.length + tailLen <= PIN_FILE_NAME_MAX_LEN) {
            "前缀 '$prefix' 过长：加上 $tailLen 字符的后缀后超过上限 $PIN_FILE_NAME_MAX_LEN"
        }

        val suffix = "%0${PIN_NAME_RANDOM_HEX_LEN}x".format(
            Random.nextInt(1 shl (PIN_NAME_RANDOM_HEX_LEN * 4))
        )
        val name = "${prefix}_$suffix$PIN_FILE_NAME_SUFFIX"
        // 兜底自检：本函数的全部价值就是「产出的名字一定过校验」，改坏了要在这里炸，
        // 而不是让设备回一个 0xFF。
        check(isPinFileNameValid(name)) { "内部错误：生成的文件名 '$name' 未通过校验" }
        return name
    }

    /** 随机后缀的十六进制位数。6 位 = 1600 万种，足够避免手动换图时撞名。 */
    private const val PIN_NAME_RANDOM_HEX_LEN = 6

    /**
     * 构建 PIN_FILM 包（`0x4C`）。
     *
     * 布局：`[mode][start_hi][start_lo][end_hi][end_lo][name_len][name…]`，
     * 长度**恒为 `6 + name_len`**。窗口两端是大端 uint16，name 不带结尾 `\0`
     * （由固件自行补，与 FILE_NAME 同一约定）。
     *
     * 本地先校验、非法就抛（同 [buildWakeSchedule]）：固件校验失败只回一个 `0xFF` 且整条
     * 拒绝，看不出是哪条规则不满足。最容易踩的是最不直观的那条 —— 文件名含
     * `today`/`breakfast`/`lunch`/`dinner` 会被拒。`mode > 3` 不在这里校验，[PinMode]
     * 让它无法表达。
     *
     * 取消 pin 请用 [buildPinFilmClear]：那条路径不需要文件名，走本函数会因空名被拒。
     *
     * @throws IllegalArgumentException 窗口两端越界，或文件名不满足
     *         [isPinFileNameValid] 的任一条时
     */
    fun buildPinFilm(pinned: PinnedFilm): ByteArray {
        // 即便当前 mode 用不到窗口也校验：这条记录会被 0x4D 回读、被 App 原样发回，
        // 非法值留在记录里，等用户把模式改成 WINDOW 就突然生效了。固件同样如此。
        require(pinned.windowStartMinutes in 0..PIN_WINDOW_MAX_MINUTE) {
            "窗口起点 ${pinned.windowStartMinutes} 分钟超出 [0,$PIN_WINDOW_MAX_MINUTE]"
        }
        require(pinned.windowEndMinutes in 0..PIN_WINDOW_MAX_MINUTE) {
            "窗口终点 ${pinned.windowEndMinutes} 分钟超出 [0,$PIN_WINDOW_MAX_MINUTE]"
        }
        pinFileNameRejectReason(pinned.fileName)?.let {
            throw IllegalArgumentException(it)
        }

        // 长度已由 pinFileNameRejectReason 判过，这里传同一个上限只是让 asciiPayload 的
        // 契约在调用点自洽。
        val nameBytes = asciiPayload(pinned.fileName, "置顶文件名", PIN_FILE_NAME_MAX_LEN)
        val data = ByteArray(6 + nameBytes.size)
        data[0] = pinned.mode.raw.toByte()
        data[1] = ((pinned.windowStartMinutes shr 8) and 0xFF).toByte()   // Big-Endian
        data[2] = (pinned.windowStartMinutes and 0xFF).toByte()
        data[3] = ((pinned.windowEndMinutes shr 8) and 0xFF).toByte()
        data[4] = (pinned.windowEndMinutes and 0xFF).toByte()
        data[5] = nameBytes.size.toByte()
        System.arraycopy(nameBytes, 0, data, 6, nameBytes.size)
        return buildPacket(CH_PIN_FILM, data)
    }

    /**
     * 构建「取消置顶」包（`0x4C`，单字节 `mode=0`）。
     *
     * 固件在 `mode == OFF` 时擦掉整条记录，所以取消不需要窗口和文件名，协议特许只发 1
     * 字节。单独一个函数是因为 `PinnedFilm(OFF)` 会被 [buildPinFilm] 以「文件名为空」拒掉。
     */
    fun buildPinFilmClear(): ByteArray {
        return buildPacket(CH_PIN_FILM, byteArrayOf(PinMode.OFF.raw.toByte()))
    }

    /** 构建 PIN_FILM_GET 包（`0x4D`，无载荷）。响应布局与 0x4C 载荷完全相同。 */
    fun buildPinFilmGet(): ByteArray {
        return buildPacket(CH_PIN_FILM_GET)
    }

    /**
     * 解析置顶画面响应载荷（`0x4D`，也可用于回读 `0x4C` 的同布局响应）。
     *
     * 「未设置」返回 `PinnedFilm(mode = OFF, …)`，null 只留给「解析失败」。两者在 UI 上
     * 必须分开：前者显示「未置顶」、后者显示「读取失败请重试」。固件 `0x4D` 在未 pin 时
     * 回的正是一份 6 字节 OFF 配置，所以这个表达与线上字节一一对应。
     *
     * 长度不自洽（`payload.size != 6 + name_len`）时返回 null 而不是半份配置：半份配置
     * 会让设备一部分时间显示错，而 UI 显示的是完整配置。固件拒收的单字节 `0xFF` 同样是 null。
     *
     * 文件名**不再跑一遍 [isPinFileNameValid]**：白名单是发送侧规则，而回读的意义是让
     * 「设备到底记住了什么」可见。判据同 [BleTransfer.queryProvisionConfig]。
     */
    fun parsePinnedFilm(payload: ByteArray): PinnedFilm? {
        if (payload.isEmpty()) return null

        val mode = PinMode.fromRaw(payload[0].toInt() and 0xFF) ?: return null

        // 单字节 0x00：我们自己发的清除帧被原样回显时长这样。语义与 6 字节 OFF 配置相同。
        if (payload.size == 1) {
            return if (mode == PinMode.OFF) PinnedFilm(PinMode.OFF) else null
        }

        if (payload.size < 6) return null
        val nameLen = payload[5].toInt() and 0xFF
        if (payload.size != 6 + nameLen) return null

        val start = ((payload[1].toInt() and 0xFF) shl 8) or (payload[2].toInt() and 0xFF)
        val end = ((payload[3].toInt() and 0xFF) shl 8) or (payload[4].toInt() and 0xFF)
        // 越界只可能来自错帧：固件在写入时就拒了 ≥1440 的值，绝不会存进记录再回读出来。
        if (start > PIN_WINDOW_MAX_MINUTE || end > PIN_WINDOW_MAX_MINUTE) return null

        // 固件这里的 name_len **不含**结尾 NUL（与 parseFileListEntry 的 `strlen + 1`
        // 相反），所以不能顺手 trimEnd('\u0000') —— 那会掩盖真正的长度错。
        val name = if (nameLen > 0) String(payload, 6, nameLen, Charsets.US_ASCII) else ""
        return PinnedFilm(mode, start, end, name)
    }

    /**
     * 解析一条文件列表响应帧的载荷（`parseResponse` 得到的 payload）。
     *
     * 布局：`[id][name_len][name...]`，其中固件的 `name_len = strlen + 1`（含结尾 NUL），
     * 解码时需去掉尾部 NUL。新版固件最后发送 `[0xFF][0x00]` 结束帧；旧固件没有结束帧。
     */
    fun parseFileListEntry(payload: ByteArray): DeviceFile? {
        if (payload.size < 2) return null
        val id = payload[0].toInt() and 0xFF
        val nameLen = payload[1].toInt() and 0xFF
        if (payload.size < 2 + nameLen || nameLen == 0) return null
        val name = String(payload, 2, nameLen, Charsets.US_ASCII).trimEnd('\u0000')
        if (name.isEmpty()) return null
        return DeviceFile(id, name)
    }

    fun isFileListEnd(payload: ByteArray): Boolean =
        payload.size == 2 && (payload[0].toInt() and 0xFF) == 0xFF && payload[1] == 0.toByte()

    /** 设备上的一个 .film 文件 */
    data class DeviceFile(
        val id: Int,
        val name: String
    )

    /**
     * 解析 0x43 状态查询响应载荷。
     * 布局（固件 docs/ble_protocol.md §4.4）：
     *   [0]   Wi-Fi 连接状态 0/1
     *   [1-4] 最近应用版本（大端 uint32）
     *   [5]   busy 0/1
     *   [6]   电量百分比 0-100
     *   [7]   错误串长度 err_len（0=无）
     *   [8..] 最近同步错误文本（无 NUL）
     *   [8+err_len] 可选休眠模式 0/1（旧固件无此字节）
     * 响应不含任何机密明文。
     */
    fun parseStatus(payload: ByteArray): DeviceStatus? {
        if (payload.size < 8) return null
        val version = ((payload[1].toInt() and 0xFF) shl 24) or
                ((payload[2].toInt() and 0xFF) shl 16) or
                ((payload[3].toInt() and 0xFF) shl 8) or
                (payload[4].toInt() and 0xFF)
        val errLen = payload[7].toInt() and 0xFF
        if (payload.size < 8 + errLen) return null
        val err = if (errLen > 0) {
            String(payload, 8, errLen, Charsets.UTF_8)
        } else {
            ""
        }
        val sleepMode = payload.getOrNull(8 + errLen)?.let {
            when (it.toInt() and 0xFF) {
                0 -> false
                1 -> true
                else -> null
            }
        }
        return DeviceStatus(
            wifiConnected = (payload[0].toInt() and 0xFF) == 1,
            lastAppliedVersion = version,
            busy = (payload[5].toInt() and 0xFF) == 1,
            batteryPercent = payload[6].toInt() and 0xFF,
            lastSyncError = err,
            sleepMode = sleepMode
        )
    }

    /** 设备状态（0x43 响应） */
    data class DeviceStatus(
        val wifiConnected: Boolean,
        val lastAppliedVersion: Int,
        val busy: Boolean,
        val batteryPercent: Int,
        val lastSyncError: String,
        val sleepMode: Boolean? = null
    )

    /** 解析响应包，帧非法（头错、长度不足、校验和不符）时返回 null。 */
    fun parseResponse(data: ByteArray): ResponsePacket? {
        if (data.size < 4) return null
        if (data[0].toInt() and 0xFF != HEAD) return null

        val ch = data[1].toInt() and 0xFF
        val len = data[2].toInt() and 0xFF

        // 帧长不足 LEN 声明的量时整帧拒绝：把 payload 降级成空数组会让上层照常算出一个
        // 错的电量/版本号；而继续读 `data[3 + len]` 会在 GATT 回调线程抛
        // IndexOutOfBoundsException，等待者的 latch 永不放行，表现为「查询永远超时」。
        if (data.size < 4 + len) return null

        val payload = if (len > 0) data.copyOfRange(3, 3 + len) else ByteArray(0)
        val checksum = data[3 + len].toInt() and 0xFF

        val calculated = calculateChecksum(data.copyOf(3 + len)).toInt() and 0xFF
        if (calculated != checksum) return null

        return ResponsePacket(ch, payload)
    }

    /** 一帧已通过校验的响应：通道号 + 载荷。 */
    data class ResponsePacket(
        val channel: Int,
        val payload: ByteArray
    )
}
