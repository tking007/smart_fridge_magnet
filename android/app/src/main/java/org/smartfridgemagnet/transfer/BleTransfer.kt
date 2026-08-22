package org.smartfridgemagnet.transfer

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * BLE 传输实现：扫描、连接、命令读写与 .film 文件传输。
 */
class BleTransfer(private val context: Context) {

    companion object {
        private const val TAG = "BleTransfer"
        private const val TARGET_DEVICE_NAME = "智能冰箱贴"

        /**
         * 扫描自动停止的时限。
         *
         * 15 秒：设备的 BLE 可连接窗口是按需开启的（按键唤醒后一段时间），窗口内几秒即可
         * 被发现；窗口外扫多久都没有结果。而 Android 对「30 秒内 5 次扫描」有节流，
         * 一直开着扫反而会让后续扫描静默失败。
         */
        private const val SCAN_TIMEOUT_MS = 15_000L

        /**
         * 请求的 ATT MTU。取 Android 允许的上限 517。
         *
         * 协商结果由 onMtuChanged 记入 negotiatedMtu，实际帧载荷由 dataChunkSize() 自适应，
         * 协商失败或回落到较小值都会安全降级，不会发出超过对端 MTU 的超长帧。
         */
        private const val REQUEST_MTU = 517

        /**
         * FILE_DATA 每帧数据字节数上限；与网页调试工具的 FILE_DATA_CHUNK 保持一致。
         *
         * 协议帧 HEAD|CH|LEN|DATA|SUM 的 LEN 只有一字节，DATA 硬上限 255，留余量取 250。
         * 带响应写下一帧固定占用两个连接事件（实测 conn_int=15ms → 30ms/帧），耗时只由帧数
         * 决定，故吞吐与帧载荷成正比。
         */
        private const val DATA_CHUNK_SIZE = 250

        /** 单帧写入等待上限（秒）。 */
        private const val WRITE_TIMEOUT_SEC = 5L

        /**
         * 连接就绪等待上限（秒）：覆盖 GATT 连接 + 服务发现 + MTU 协商三步。
         *
         * 取 15s 而非更短：服务发现耗时受对端状态与信道质量影响，设备刚从深睡唤醒时
         * 明显更慢。这是超时上限而非固定等待，正常情况下 latch 会立刻放行。
         */
        private const val CONNECT_TIMEOUT_SEC = 15L
        private const val BOND_TIMEOUT_SEC = 30L

        /**
         * 配网后轮询 Wi-Fi 状态的次数与间隔（合计 15s）。
         *
         * 关联 + DHCP + SNTP 的总耗时跨度很大（实测顺利时 2s 内，弱信号或 DHCP 慢时
         * 十几秒），因此用轮询而非固定等待：固定值要么等不够而误报失败，要么白等。
         */
        private const val PROVISION_STATUS_RETRIES = 10
        private const val PROVISION_STATUS_INTERVAL_MS = 1500L

        // UUID 以 BleProtocol 为唯一权威（固件 Service 0x2000 / Write 0x2001），此处仅作别名。
        val SERVICE_UUID: UUID = BleProtocol.SERVICE_UUID
        val WRITE_CHAR_UUID: UUID = BleProtocol.WRITE_CHAR_UUID
        val NOTIFY_CHAR_UUID: UUID = BleProtocol.NOTIFY_CHAR_UUID
        private val CCCD_UUID: UUID =
            UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }

    private val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val bluetoothAdapter = bluetoothManager.adapter
    private var bluetoothGatt: BluetoothGatt? = null
    private var writeCharacteristic: BluetoothGattCharacteristic? = null

    /** 写完成同步：由 onCharacteristicWrite 回调放行，替代不可靠的固定 sleep。 */
    @Volatile
    private var writeLatch: CountDownLatch? = null

    @Volatile
    private var lastWriteStatus: Int = BluetoothGatt.GATT_SUCCESS

    /** 通知响应同步：由 onCharacteristicChanged 回调放行 */
    private var notifyLatch: CountDownLatch? = null
    private var notifyResponse: ByteArray? = null

    /** 0x06 会连续返回多帧，使用独立队列，避免覆盖单帧命令的响应槽。 */
    @Volatile
    private var fileListQueue: LinkedBlockingQueue<ByteArray>? = null

    /** 所有 GATT 工作流串行执行，保护 write/notify latch 与协议状态机。 */
    private val operationLock = ReentrantLock()

    /** 协商后的 ATT MTU，默认 23。 */
    @Volatile
    private var negotiatedMtu: Int = 23

    /**
     * 链路就绪同步：由 connect() 等待，服务发现 / MTU 协商 / 断连三处放行。
     *
     * 「就绪」的判据是写特征已拿到且 MTU 协商有结论，而不是 GATT 状态变成 CONNECTED ——
     * 后者之后还要等服务发现，此时 writeCharacteristic 仍是 null。
     */
    @Volatile
    private var readyLatch: CountDownLatch? = null

    /** Android 同一时刻只允许一个 GATT 操作；CCCD 必须等回调后逐个写。 */
    private val pendingNotifySubscriptions = ArrayDeque<BluetoothGattCharacteristic>()

    /** 扫描超时用的独立作用域。BleTransfer 是 Application 级单例，生命周期与进程一致。 */
    private val scanScope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private var scanTimeoutJob: Job? = null

    private val _connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val stateFlow: StateFlow<ConnectionState> = _connectionState

    private val _transferProgress = MutableStateFlow<TransferProgress>(TransferProgress())
    val progressFlow: StateFlow<TransferProgress> = _transferProgress

    private val _discoveredDevices = MutableStateFlow<List<DeviceInfo>>(emptyList())
    val discoveredDevices: StateFlow<List<DeviceInfo>> = _discoveredDevices

    private val scanCallback = object : ScanCallback() {
        @SuppressLint("MissingPermission")
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val device = result.device
            val deviceName = device.name ?: ""

            // 只添加目标设备
            if (deviceName != TARGET_DEVICE_NAME) return

            val deviceInfo = DeviceInfo(
                name = deviceName,
                address = device.address,
                type = DeviceType.BLE,
                rssi = result.rssi
            )
            val currentList = _discoveredDevices.value.toMutableList()
            if (currentList.none { it.address == deviceInfo.address }) {
                currentList.add(deviceInfo)
                _discoveredDevices.value = currentList
                Log.d(TAG, "Found target: $deviceName (${device.address})")
            }
        }

        override fun onScanFailed(errorCode: Int) {
            _connectionState.value = ConnectionState.Error("Scan failed: $errorCode")
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (gatt !== bluetoothGatt) {
                if (newState == BluetoothGatt.STATE_DISCONNECTED) gatt.close()
                return
            }
            when (newState) {
                BluetoothGatt.STATE_CONNECTED -> {
                    Log.d(TAG, "Connected to GATT server")
                    _connectionState.value = ConnectionState.Connecting
                    if (!gatt.discoverServices()) {
                        failLinkReady(gatt, "启动服务发现失败")
                    }
                }
                BluetoothGatt.STATE_DISCONNECTED -> {
                    Log.d(TAG, "Disconnected from GATT server")
                    _connectionState.value = ConnectionState.Disconnected
                    bluetoothGatt = null
                    writeCharacteristic = null
                    pendingNotifySubscriptions.clear()
                    lastWriteStatus = BluetoothGatt.GATT_FAILURE
                    writeLatch?.countDown()
                    notifyLatch?.countDown()
                    gatt.close()
                    // 连接过程中断连也要放行，否则 connect() 只能等到超时。
                    readyLatch?.countDown()
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (gatt !== bluetoothGatt) return
            if (status != BluetoothGatt.GATT_SUCCESS) {
                failLinkReady(gatt, "服务发现失败（status=$status）")
                return
            }
            run {
                Log.d(TAG, "Services discovered")
                val service = gatt.getService(SERVICE_UUID)
                if (service == null) {
                    failLinkReady(gatt, "未找到设备服务 $SERVICE_UUID")
                    return
                }
                writeCharacteristic = service.getCharacteristic(WRITE_CHAR_UUID)
                if (writeCharacteristic == null) {
                    failLinkReady(gatt, "未找到写特征 $WRITE_CHAR_UUID")
                    return
                }

                // setCharacteristicNotification() 只打开 Android 本地分发；必须继续写 CCCD，
                // 设备才会真正发送 Notify。三个描述符写入要串行，完成前连接不能标记为可用。
                pendingNotifySubscriptions.clear()
                listOf(
                    BleProtocol.NOTIFY_CHAR_UUID,
                    BleProtocol.NOTIFY_CHAR2_UUID,
                    BleProtocol.NOTIFY_CHAR3_UUID
                ).mapNotNullTo(pendingNotifySubscriptions) { service.getCharacteristic(it) }
                if (pendingNotifySubscriptions.isEmpty()) {
                    failLinkReady(gatt, "设备没有可订阅的通知特征")
                    return
                }
                subscribeNextNotification(gatt)
            }
        }

        override fun onDescriptorWrite(
            gatt: BluetoothGatt,
            descriptor: BluetoothGattDescriptor,
            status: Int
        ) {
            if (gatt !== bluetoothGatt) return
            if (status != BluetoothGatt.GATT_SUCCESS) {
                failLinkReady(gatt, "订阅设备通知失败（status=$status）")
                return
            }
            Log.d(TAG, "Notification enabled: ${descriptor.characteristic.uuid}")
            subscribeNextNotification(gatt)
        }

        override fun onMtuChanged(gatt: BluetoothGatt, mtu: Int, status: Int) {
            if (gatt !== bluetoothGatt) return
            if (status == BluetoothGatt.GATT_SUCCESS) {
                negotiatedMtu = mtu
                Log.d(TAG, "MTU negotiated: $mtu")
            } else {
                Log.w(TAG, "MTU negotiation failed, keep default")
            }
            // 成功路径的放行点：MTU 有了结论（无论协商成败）即视为链路就绪。
            // 协商失败不算致命 —— dataChunkSize() 会按实际 MTU 安全降级。
            markLinkReady(gatt)
        }

        override fun onCharacteristicWrite(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            if (gatt !== bluetoothGatt) return
            // 必须以回调为写完成依据：Android 同一时刻只允许一个未完成的 GATT 操作，
            // 靠固定 sleep 既不可靠也极慢。
            lastWriteStatus = status
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e(TAG, "Characteristic write failed: $status")
            }
            writeLatch?.countDown()
        }

        @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic
        ) {
            if (gatt !== bluetoothGatt) return
            handleNotification(characteristic.uuid, characteristic.value ?: return)
        }

        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray
        ) {
            if (gatt !== bluetoothGatt) return
            handleNotification(characteristic.uuid, value)
        }
    }

    @SuppressLint("MissingPermission")
    private fun ensureBonded(device: BluetoothDevice): Boolean {
        if (device.bondState == BluetoothDevice.BOND_BONDED) return true
        if (device.bondState == BluetoothDevice.BOND_NONE && !device.createBond()) {
            _connectionState.value = ConnectionState.Error("无法启动蓝牙安全配对")
            return false
        }

        var bondingObserved = device.bondState == BluetoothDevice.BOND_BONDING
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(BOND_TIMEOUT_SEC)
        while (System.nanoTime() < deadline) {
            when (device.bondState) {
                BluetoothDevice.BOND_BONDED -> return true
                BluetoothDevice.BOND_BONDING -> bondingObserved = true
                BluetoothDevice.BOND_NONE -> if (bondingObserved) {
                    _connectionState.value = ConnectionState.Error("蓝牙安全配对失败")
                    return false
                }
            }
            Thread.sleep(200)
        }
        _connectionState.value = ConnectionState.Error("蓝牙安全配对超时（${BOND_TIMEOUT_SEC}s）")
        return false
    }

    @SuppressLint("MissingPermission")
    private fun subscribeNextNotification(gatt: BluetoothGatt) {
        if (gatt !== bluetoothGatt) return
        val characteristic = pendingNotifySubscriptions.removeFirstOrNull()
        if (characteristic == null) {
            // 提高 MTU，否则默认 ATT MTU 23（单帧仅 20 字节载荷），209120 字节要发上万帧。
            // requestMtu() 若同步拒绝不会再有回调，此时按默认 MTU 安全降级并直接放行。
            if (!gatt.requestMtu(REQUEST_MTU)) markLinkReady(gatt)
            return
        }
        if (!gatt.setCharacteristicNotification(characteristic, true)) {
            failLinkReady(gatt, "启用本地通知失败（${characteristic.uuid}）")
            return
        }
        val descriptor = characteristic.getDescriptor(CCCD_UUID)
        if (descriptor == null) {
            failLinkReady(gatt, "通知特征缺少 CCCD（${characteristic.uuid}）")
            return
        }
        if (!writeGattDescriptor(gatt, descriptor, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)) {
            failLinkReady(gatt, "写入通知 CCCD 被拒绝（${characteristic.uuid}）")
        }
    }

    @Suppress("DEPRECATION")
    @SuppressLint("MissingPermission")
    private fun writeGattDescriptor(
        gatt: BluetoothGatt,
        descriptor: BluetoothGattDescriptor,
        value: ByteArray
    ): Boolean = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        gatt.writeDescriptor(descriptor, value) == BluetoothStatusCodes.SUCCESS
    } else {
        descriptor.value = value
        gatt.writeDescriptor(descriptor)
    }

    private fun markLinkReady(gatt: BluetoothGatt) {
        if (gatt !== bluetoothGatt) return
        _connectionState.value = ConnectionState.Connected
        readyLatch?.countDown()
    }

    @SuppressLint("MissingPermission")
    private fun failLinkReady(gatt: BluetoothGatt, message: String) {
        if (gatt !== bluetoothGatt) return
        Log.e(TAG, message)
        pendingNotifySubscriptions.clear()
        writeCharacteristic = null
        bluetoothGatt = null
        _connectionState.value = ConnectionState.Error(message)
        readyLatch?.countDown()
        gatt.disconnect()
        gatt.close()
    }

    @SuppressLint("MissingPermission")
    private fun closeCurrentGatt() {
        val gatt = bluetoothGatt ?: return
        bluetoothGatt = null
        pendingNotifySubscriptions.clear()
        writeCharacteristic = null
        lastWriteStatus = BluetoothGatt.GATT_FAILURE
        writeLatch?.countDown()
        notifyLatch?.countDown()
        readyLatch?.countDown()
        gatt.disconnect()
        gatt.close()
    }

    private fun handleNotification(uuid: UUID, value: ByteArray) {
        val data = value.copyOf()
        val hex = data.joinToString(" ") { "%02X".format(it) }
        Log.d(TAG, "← Notify [$uuid]: $hex")

        val parsed = BleProtocol.parseResponse(data) ?: return
        Log.d(TAG, "← Parsed ch=0x${parsed.channel.toString(16)} len=${parsed.payload.size}")
        if (parsed.channel == BleProtocol.CH_FILE_LIST) {
            fileListQueue?.offer(data)
        }
        notifyResponse = data
        notifyLatch?.countDown()
    }

    /**
     * 扫描前必须满足的条件，不满足时返回给用户看的原因（满足则 null）。
     *
     * 显式检查而不是靠 try/catch：`startScan` 全程 `@SuppressLint`，权限缺失时抛
     * `SecurityException`，而调用点在 Compose 的 onClick lambda 里，异常直穿 UI 就是崩溃；
     * 蓝牙关闭时安全调用又会把 null 吞掉、状态却已置成 Scanning，界面会把「蓝牙没开」
     * 说成「设备没醒」。
     */
    private fun scanBlocker(): String? {
        val needed = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            listOf(android.Manifest.permission.BLUETOOTH_SCAN,
                   android.Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            // 11 及以下：扫描结果可用于推断位置，系统因此要求定位权限。
            listOf(android.Manifest.permission.ACCESS_FINE_LOCATION)
        }
        val missing = needed.filter {
            ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                "需要「附近的设备」权限才能搜索冰箱贴。请在系统设置里允许后重试。"
            } else {
                "需要定位权限才能搜索蓝牙设备（系统要求）。请在系统设置里允许后重试。"
            }
        }
        val adapter = bluetoothAdapter ?: return "这台手机不支持蓝牙，无法连接冰箱贴。"
        if (!adapter.isEnabled) return "蓝牙没有打开。请先打开蓝牙再搜索。"
        if (adapter.bluetoothLeScanner == null) return "蓝牙暂时不可用，请关掉蓝牙再重新打开。"
        return null
    }

    @SuppressLint("MissingPermission")
    fun startScan() {
        scanBlocker()?.let {
            _connectionState.value = ConnectionState.Error(it)
            return
        }
        _connectionState.value = ConnectionState.Scanning
        _discoveredDevices.value = emptyList()
        try {
            bluetoothAdapter?.bluetoothLeScanner?.startScan(scanCallback)
        } catch (e: SecurityException) {
            // 权限在检查之后、调用之前被撤销（用户在设置里改）。极少见但会崩，兜住。
            Log.w(TAG, "startScan 被系统拒绝: ${e.message}")
            _connectionState.value = ConnectionState.Error("蓝牙权限被拒绝，请在系统设置里允许后重试。")
            return
        }
        armScanTimeout()
    }

    /**
     * 到时自动停扫，并给一个能执行的下一步。
     *
     * Android 有「30 秒内 5 次扫描」的节流，一直开着扫、反复进出页面后扫描会**静默失败**，
     * 列表永远空且看不出是被限流。设备平时深睡不广播，扫不到是常态，需要的是提示按键唤醒。
     */
    private fun armScanTimeout() {
        scanTimeoutJob?.cancel()
        scanTimeoutJob = scanScope.launch {
            delay(SCAN_TIMEOUT_MS)
            if (_connectionState.value !is ConnectionState.Scanning) return@launch
            stopScan()
            if (_discoveredDevices.value.isEmpty()) {
                _connectionState.value = ConnectionState.Error(
                    "没搜到冰箱贴。它平时处于深度休眠、不对外广播 —— " +
                        "请按一下设备上的按键唤醒它，然后重新搜索。"
                )
            }
        }
    }

    @SuppressLint("MissingPermission")
    fun stopScan() {
        scanTimeoutJob?.cancel()
        scanTimeoutJob = null
        try {
            bluetoothAdapter?.bluetoothLeScanner?.stopScan(scanCallback)
        } catch (e: SecurityException) {
            Log.w(TAG, "stopScan 被系统拒绝: ${e.message}")
        }
        if (_connectionState.value is ConnectionState.Scanning) {
            _connectionState.value = ConnectionState.Disconnected
        }
    }

    /**
     * 连接设备，并**等到链路真正可用才返回**。
     *
     * 服务发现耗时受对端状态与信道质量影响（刚从深睡唤醒时明显更慢），没有哪个固定
     * delay 是安全的，因此等 readyLatch、由回调决定何时放行。
     *
     * 返回 true 即保证 writeCharacteristic 可用、MTU 已有结论，调用方可直接发命令。
     */
    @SuppressLint("MissingPermission")
    suspend fun connect(device: DeviceInfo): Boolean = withContext(Dispatchers.IO) {
        try {
            stopScan()
            closeCurrentGatt()
            _connectionState.value = ConnectionState.Connecting

            val remote = bluetoothAdapter?.getRemoteDevice(device.address)
                ?: run {
                    _connectionState.value = ConnectionState.Error("无效的设备地址")
                    return@withContext false
                }

            // 自定义 GATT 特征只允许加密链路访问。先完成系统 bonding，再做服务发现，
            // 避免 CCCD/首个命令因未加密而以 GATT_INSUFFICIENT_AUTHENTICATION 失败。
            if (!ensureBonded(remote)) return@withContext false

            // 每次连接都重置：复用旧值会让「上一次连接的 MTU」被误当作本次的结论。
            val latch = CountDownLatch(1)
            readyLatch = latch
            writeCharacteristic = null
            negotiatedMtu = 23

            bluetoothGatt = remote.connectGatt(context, false, gattCallback)
            if (bluetoothGatt == null) {
                readyLatch = null
                _connectionState.value = ConnectionState.Error("connectGatt 返回 null")
                return@withContext false
            }

            val signalled = latch.await(CONNECT_TIMEOUT_SEC, TimeUnit.SECONDS)
            readyLatch = null

            if (!signalled) {
                Log.e(TAG, "connect: timeout waiting for link ready")
                bluetoothGatt?.let {
                    failLinkReady(it, "连接超时（${CONNECT_TIMEOUT_SEC}s）")
                } ?: run {
                    _connectionState.value = ConnectionState.Error("连接超时（${CONNECT_TIMEOUT_SEC}s）")
                }
                return@withContext false
            }
            // latch 也被失败路径放行，故这里必须再确认特征是否真的到手；
            // 具体错因已由回调写入 ConnectionState.Error，此处不覆盖。
            if (writeCharacteristic == null || _connectionState.value !is ConnectionState.Connected) {
                Log.e(TAG, "connect: link did not reach ready state")
                return@withContext false
            }

            Log.d(TAG, "connect: ready (mtu=$negotiatedMtu, chunk=${dataChunkSize()})")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Connection failed", e)
            readyLatch = null
            closeCurrentGatt()
            _connectionState.value = ConnectionState.Error(e.message ?: "Connection failed")
            false
        }
    }

    /**
     * 按 MAC 直连，跳过扫描。
     *
     * 用于「记住上次的设备」：固件没有实现 BLE 配对绑定，系统不会自动重连，自动重连只能
     * 由 App 保存 MAC 后主动发起。
     *
     * 复用 [connect] 的就绪握手，返回 true 同样意味着写特征可用、MTU 已有结论。
     * 设备不在附近时会走到连接超时（15s）后返回 false，调用方可退回扫描。
     */
    @SuppressLint("MissingPermission")
    suspend fun connectToAddress(address: String, name: String = TARGET_DEVICE_NAME): Boolean {
        if (address.isBlank()) return false
        return connect(DeviceInfo(name = name, address = address, type = DeviceType.BLE, rssi = 0))
    }

    @SuppressLint("MissingPermission")
    suspend fun disconnect() {
        closeCurrentGatt()
        lastWriteStatus = BluetoothGatt.GATT_FAILURE
        writeLatch?.countDown()
        notifyLatch?.countDown()
        readyLatch?.countDown()
        _connectionState.value = ConnectionState.Disconnected
    }

    suspend fun sendFilm(data: ByteArray, fileName: String): Boolean = withContext(Dispatchers.IO) {
        operationLock.withLock { sendFilmLocked(data, fileName) }
    }

    private fun sendFilmLocked(data: ByteArray, fileName: String): Boolean {
        return try {
            _transferProgress.value = TransferProgress(
                state = TransferState.PREPARING,
                totalBytes = data.size.toLong()
            )

            // 1. FILE_START
            sendPacket(BleProtocol.buildFileStart())
            _transferProgress.value = _transferProgress.value.copy(state = TransferState.SENDING_NAME)

            // 2. FILE_NAME
            sendPacket(BleProtocol.buildFileName(fileName))
            _transferProgress.value = _transferProgress.value.copy(state = TransferState.SENDING_LEN)

            // 3. FILE_LEN
            sendPacket(BleProtocol.buildFileLen(data.size))
            _transferProgress.value = _transferProgress.value.copy(state = TransferState.SENDING_DATA)

            // 4. FILE_DATA（按帧发送；每帧一次 GATT 写，不再对协议帧二次分片）
            val chunkSize = dataChunkSize()
            var offset = 0
            while (offset < data.size) {
                val n = minOf(chunkSize, data.size - offset)
                val chunk = data.copyOfRange(offset, offset + n)
                sendPacket(BleProtocol.buildFileData(chunk))

                offset += n
                val progress = (offset * 100 / data.size)
                _transferProgress.value = _transferProgress.value.copy(
                    bytesSent = offset.toLong(),
                    percentage = progress
                )
            }

            // 5. FILE_STOP
            // 固件在 FILE_STOP 成功后会自行按文件名刷新显示刚传的这张图，
            // 因此这里绝不能再补发 FILE_DISPLAY(0) —— 那会把画面切回文件列表第 0 张旧图。
            sendPacket(BleProtocol.buildFileStop())

            _transferProgress.value = _transferProgress.value.copy(
                state = TransferState.COMPLETED,
                percentage = 100
            )

            true
        } catch (e: Exception) {
            Log.e(TAG, "Send film failed", e)
            _transferProgress.value = _transferProgress.value.copy(
                state = TransferState.FAILED,
                error = e.message
            )
            false
        }
    }

    suspend fun displayFilm(fileId: Int): Boolean = withContext(Dispatchers.IO) {
        try {
            sendPacket(BleProtocol.buildFileDisplay(fileId))
            true
        } catch (e: Exception) {
            Log.e(TAG, "Display film failed", e)
            false
        }
    }

    /**
     * 连发多张 `.film`（今日总览 + 早/中/晚详情）。
     *
     * 必须**开启静默接收**再逐张传、最后一张前关闭：电子纸刷一次约 20 秒，若每张
     * `FILE_STOP` 都触发刷屏，本端会在等待中 GATT 超时断连。
     *
     * 约定：`films` 的**最后一项应是今日总览**，它传完后自动上屏、成为默认画面，
     * 其余留在设备上等用户用侧边按键翻页。文件名建议带序号前缀（`1_today.film` /
     * `2_breakfast.film` / …），固件按文件名排序决定翻页次序。
     *
     * @param films 有序列表；last = 默认展示那张
     * @param clearBefore 传输前是否清空设备旧图（避免历史版本被翻出来）
     */
    suspend fun sendFilmSet(
        films: List<Pair<String, ByteArray>>,
        clearBefore: Boolean = true
    ): Boolean = withContext(Dispatchers.IO) {
        operationLock.withLock {
        if (films.isEmpty()) return@withContext false

        var silentOn = false
        try {
            if (clearBefore) {
                try {
                    sendPacket(BleProtocol.buildFileClearAll())
                    // 固件收到即回 ACK（「已受理」），真正的删除在其文件任务里异步进行。
                    // 等足再传，否则新文件会与正在进行的删除抢同一个文件系统。
                    Thread.sleep(1500)
                } catch (e: Exception) {
                    Log.w(TAG, "clear all failed, continue: ${e.message}")
                }
            }

            // 清空若导致断连，后续写入都会失败，直接中止而不是硬发到底
            if (!isConnected()) {
                Log.e(TAG, "disconnected after clear-all, abort film set")
                return@withContext false
            }

            try {
                sendPacket(BleProtocol.buildSilentReceive(true))
                silentOn = true
                Thread.sleep(100)
            } catch (e: Exception) {
                Log.w(TAG, "silent receive not supported: ${e.message}")
            }

            _transferProgress.value = TransferProgress(
                state = TransferState.PREPARING,
                totalBytes = films.sumOf { it.second.size.toLong() }
            )

            for ((_, item) in films.withIndex()) {
                val (name, data) = item
                if (!sendFilmLocked(data, name)) return@withContext false
                Thread.sleep(400)   // 给固件留出提交与刷新文件列表的时间
            }

            // 全部传完后让设备按当前时间决定显示哪一张：做饭时段内显示那一餐的详情，
            // 窗口外显示今日总览。中午下发即直接呈现午餐详情，不必等用户翻页。
            var displayed = false
            try {
                sendPacket(BleProtocol.buildDisplayNow())
                displayed = true
            } catch (e: Exception) {
                Log.w(TAG, "display-now not supported: ${e.message}")
            }

            // 关闭静默放在 0x47 之后：先定好显示哪张，再解除静默，避免多刷一次屏
            if (silentOn) {
                try {
                    sendPacket(BleProtocol.buildSilentReceive(false))
                    silentOn = false
                } catch (e: Exception) {
                    Log.w(TAG, "disable silent failed: ${e.message}")
                }
            }

            // 旧固件不支持 0x47 时，全程静默会导致一张都没上屏，这里补一次显式显示兜底
            if (!displayed) {
                try {
                    sendPacket(BleProtocol.buildFileDisplay(films.lastIndex))
                } catch (_: Exception) { }
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "Send film set failed", e)
            _transferProgress.value = _transferProgress.value.copy(
                state = TransferState.FAILED,
                error = e.message
            )
            false
        } finally {
            // 失败也要尽力关闭静默，否则设备会一直不上屏（断连时固件侧亦会自动复位）。
            if (silentOn) {
                try { sendPacket(BleProtocol.buildSilentReceive(false)) } catch (_: Exception) { }
            }
        }
        }
    }

    fun isConnected(): Boolean = _connectionState.value is ConnectionState.Connected

    /**
     * 单帧数据载荷上限：ATT 写单帧可用 (MTU - 3)，再扣掉协议帧的 4 字节开销
     * （HEAD + CH + LEN + SUM）。MTU 协商成功时取 DATA_CHUNK_SIZE。
     */
    private fun dataChunkSize(): Int {
        val usable = negotiatedMtu - 3 - 4
        if (usable >= DATA_CHUNK_SIZE) return DATA_CHUNK_SIZE
        return if (usable < 16) 16 else usable
    }

    /**
     * 发送一个完整协议帧。
     *
     * 关键约定：**一个协议帧必须由一次 GATT 写完成整体送出**。固件按「一次写 = 一帧」解析，
     * 把帧再切开分多次写会让帧头/长度/校验全部错位。
     *
     * 用带响应写（WRITE_TYPE_DEFAULT）并等待 onCharacteristicWrite 回调：无响应写会在
     * FILE_START 之后连发的 NAME/LEN 小帧上丢包，状态机停在 STARTED、后续数据被整体丢弃。
     */
    @SuppressLint("MissingPermission")
    private fun sendPacket(packet: ByteArray) = operationLock.withLock {
        val characteristic = writeCharacteristic
            ?: throw IllegalStateException("Write characteristic not available")
        val gatt = bluetoothGatt ?: throw IllegalStateException("GATT not connected")

        val latch = CountDownLatch(1)
        writeLatch = latch
        lastWriteStatus = BluetoothGatt.GATT_SUCCESS

        val accepted = writeGattPacket(gatt, characteristic, packet)
        if (!accepted) {
            writeLatch = null
            throw IllegalStateException("writeCharacteristic 调用被拒绝（GATT 忙或未就绪）")
        }

        val done = latch.await(WRITE_TIMEOUT_SEC, TimeUnit.SECONDS)
        writeLatch = null

        if (!done) {
            throw IllegalStateException("等待写入回调超时（${WRITE_TIMEOUT_SEC}s）")
        }
        if (lastWriteStatus != BluetoothGatt.GATT_SUCCESS) {
            throw IllegalStateException("写入失败，status=$lastWriteStatus")
        }
    }

    /**
     * 发送命令并等待设备通知响应。
     *
     * 固件对大多数命令（0x23/0x43/0x44 等）会通过 Notify 回复一帧。
     *
     * **等待者必须在写之前注册。** 对 0x43 这类响应极快的命令，通知往往与写回调同时甚至
     * 更早到达：若那时 notifyLatch 还是 null，回调里的 `countDown()` 什么也没做，随后
     * `notifyResponse = null` 又把已送到的回复清掉，上层恒拿到 null。写失败时再撤销注册。
     *
     * @param packet 要发送的命令帧
     * @param timeoutSec 等待通知的超时（秒）
     * @param expectedChannel 期望的响应通道；非 null 时会跳过其它通道的帧继续等，直到超时。
     *        设备可能在此期间推送无关通知（如状态上报），不过滤会让 latch 被无关帧唤醒。
     * @return 设备响应的原始字节，超时返回 null
     */
    @SuppressLint("MissingPermission")
    private fun sendCommandAndWaitResponse(
        packet: ByteArray,
        timeoutSec: Long = 5L,
        expectedChannel: Int? = null
    ): ByteArray? = operationLock.withLock {
        val characteristic = writeCharacteristic
            ?: throw IllegalStateException("Write characteristic not available")
        val gatt = bluetoothGatt ?: throw IllegalStateException("GATT not connected")

        // 1) 先注册通知等待者，并清掉上一条命令可能残留的响应，避免读到旧值。
        val notifyLatch = CountDownLatch(1)
        this.notifyResponse = null
        this.notifyLatch = notifyLatch

        try {
            // 2) 再发命令。
            val writeLatch = CountDownLatch(1)
            this.writeLatch = writeLatch
            lastWriteStatus = BluetoothGatt.GATT_SUCCESS

            val accepted = writeGattPacket(gatt, characteristic, packet)
            if (!accepted) {
                this.writeLatch = null
                return@withLock null
            }

            val writeDone = writeLatch.await(WRITE_TIMEOUT_SEC, TimeUnit.SECONDS)
            this.writeLatch = null
            if (!writeDone || lastWriteStatus != BluetoothGatt.GATT_SUCCESS) return@withLock null

            // 3) 等通知。若它已在写回调之前到达，latch 早已被 countDown，这里立即返回。
            val deadline = System.currentTimeMillis() + timeoutSec * 1000
            var latch = notifyLatch
            var result: ByteArray? = null
            while (result == null) {
                val remain = deadline - System.currentTimeMillis()
                if (remain <= 0 || !latch.await(remain, TimeUnit.MILLISECONDS)) break

                val response = this.notifyResponse
                if (expectedChannel == null) {
                    result = response
                    break
                }

                val parsed = response?.let { BleProtocol.parseResponse(it) }
                if (parsed != null && parsed.channel == expectedChannel) {
                    result = response
                    break
                }

                // 收到无关帧：换一个新 latch 继续等，剩余时间不重置。
                Log.d(TAG, "skip unrelated notify ch=0x${parsed?.channel?.toString(16)}")
                latch = CountDownLatch(1)
                this.notifyResponse = null
                this.notifyLatch = latch
            }
            result
        } finally {
            // 注册与清理成对：否则失败路径会留下悬空 latch，让后续命令的通知记到它头上。
            this.notifyLatch = null
            this.notifyResponse = null
        }
    }

    @SuppressLint("MissingPermission")
    private fun writeGattPacket(
        gatt: BluetoothGatt,
        characteristic: BluetoothGattCharacteristic,
        packet: ByteArray
    ): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return gatt.writeCharacteristic(
                characteristic,
                packet,
                BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
            ) == BluetoothStatusCodes.SUCCESS
        }
        return writeGattPacketLegacy(gatt, characteristic, packet)
    }

    @Suppress("DEPRECATION")
    @SuppressLint("MissingPermission")
    private fun writeGattPacketLegacy(
        gatt: BluetoothGatt,
        characteristic: BluetoothGattCharacteristic,
        packet: ByteArray
    ): Boolean {
        characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        characteristic.value = packet
        return gatt.writeCharacteristic(characteristic)
    }

    /**
     * 查询设备合并状态（0x43）。
     * 返回解析后的 DeviceStatus，失败返回 null。
     */
    suspend fun queryStatus(): BleProtocol.DeviceStatus? = withContext(Dispatchers.IO) {
        operationLock.withLock { queryStatusLocked() }
    }

    private fun queryStatusLocked(): BleProtocol.DeviceStatus? {
        try {
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildStatusGet(),
                expectedChannel = BleProtocol.CH_STATUS_GET
            )
            if (response == null) {
                Log.w(TAG, "queryStatus: no response")
                return null
            }
            val parsed = BleProtocol.parseResponse(response) ?: return null
            if (parsed.channel != BleProtocol.CH_STATUS_GET) return null
            return BleProtocol.parseStatus(parsed.payload)
        } catch (e: Exception) {
            Log.e(TAG, "queryStatus failed", e)
            return null
        }
    }

    /**
     * 查询电量（0x23）。
     * 返回电量百分比 0-100，失败返回 -1。
     */
    suspend fun queryBattery(): Int = withContext(Dispatchers.IO) {
        try {
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildPowerRead(),
                expectedChannel = BleProtocol.CH_CTRL_PWRREAD
            )
            if (response == null) return@withContext -1
            val parsed = BleProtocol.parseResponse(response) ?: return@withContext -1
            if (parsed.channel != BleProtocol.CH_CTRL_PWRREAD) return@withContext -1
            if (parsed.payload.isEmpty()) return@withContext -1
            parsed.payload[0].toInt() and 0xFF
        } catch (e: Exception) {
            Log.e(TAG, "queryBattery failed", e)
            -1
        }
    }

    /**
     * 查询文件列表（0x06）。
     * 新固件用结束帧判定接收完毕；收到条目后仍保留静默期以兼容旧固件。
     * 完全无响应会抛出异常，与“设备确实没有文件”明确区分。
     */
    suspend fun queryFileList(): List<BleProtocol.DeviceFile> = withContext(Dispatchers.IO) {
        operationLock.withLock {
        val queue = LinkedBlockingQueue<ByteArray>()
        fileListQueue = queue
        try {
            // 先注册队列再写命令，设备即刻返回的第一帧也不会丢。
            sendPacket(BleProtocol.buildFileList())

            // 首帧给设备充分响应时间；收到条目后用静默期兼容没有结束帧的旧固件。
            val files = mutableListOf<BleProtocol.DeviceFile>()
            var receivedFrame = false
            var quietDeadline = System.currentTimeMillis() + 5_000L
            while (true) {
                val remaining = quietDeadline - System.currentTimeMillis()
                if (remaining <= 0L) break
                val response = queue.poll(remaining, TimeUnit.MILLISECONDS) ?: break
                val parsed = BleProtocol.parseResponse(response)
                if (parsed != null && parsed.channel == BleProtocol.CH_FILE_LIST) {
                    receivedFrame = true
                    if (BleProtocol.isFileListEnd(parsed.payload)) break
                    val entry = BleProtocol.parseFileListEntry(parsed.payload)
                        ?: throw IllegalStateException("设备返回了无法解析的文件列表条目")
                    files.add(entry)
                    quietDeadline = System.currentTimeMillis() + 750L
                }
            }
            if (!receivedFrame) throw IllegalStateException("设备未返回文件列表，请确认固件已连接后重试")
            files
        } catch (e: Exception) {
            Log.e(TAG, "queryFileList failed", e)
            throw e
        } finally {
            fileListQueue = null
        }
        }
    }

    /**
     * Wi-Fi 配网。序列与已验证可用的网页调试工具保持一致。
     *
     * **0x30 必须第一条发**：固件的 wifi_enable 默认为 0，未开启时后面的 0x38 会被
     * service_wifi_connect() 直接拒绝（固件日志 "WiFi disabled, skip init"），且 BLE 层
     * 没有任何错误回包 —— 每条命令都「发送成功」而 0x3A 永远返回未连接。
     *
     * 各条之间的 sleep 只为给固件留出 NVS 落盘时间；命令是否送达由 sendPacket 依据
     * GATT 回调确认，不依赖 sleep。
     *
     * 连接结果用轮询 0x43 判定而非固定等待：关联 + DHCP + SNTP 的耗时跨度很大。
     */
    suspend fun provisionWifi(
        ssid: String,
        password: String,
        manifestUrl: String,
        deviceToken: String? = null,
        deviceId: String? = null
    ): Result<String> = withContext(Dispatchers.IO) {
        operationLock.withLock {
        try {
            if (!isConnected() || writeCharacteristic == null) {
                return@withContext Result.failure(Exception("设备未连接或链路未就绪，请重新连接"))
            }

            // 长载荷前置校验：manifest_url 是 88 字节量级，加 4 字节帧开销后远超默认
            // MTU 23 的可用载荷。固件按「一次写 = 一帧」解析，装不下就整帧解析失败且无错误回包。
            if (manifestUrl.isNotBlank()) {
                val needed = manifestUrl.toByteArray(Charsets.US_ASCII).size + 4
                val usable = negotiatedMtu - 3
                if (needed > usable) {
                    return@withContext Result.failure(
                        Exception("MTU 过小（$negotiatedMtu），装不下 $needed 字节的 manifest_url，请重新连接设备")
                    )
                }
                // 固件 manifest_parse.c 只接受 https，明文 manifest 会被直接拒收且不下载。
                if (!manifestUrl.startsWith("https://")) {
                    return@withContext Result.failure(Exception("manifest_url 必须以 https:// 开头"))
                }
            }

            Log.d(TAG, "Provision: 0x30 enable Wi-Fi master switch")
            sendPacket(BleProtocol.buildWifiEnable(true))
            Thread.sleep(120)

            if (ssid.isNotBlank()) {
                Log.d(TAG, "Provision: 0x32 SSID=$ssid")
                sendPacket(BleProtocol.buildWifiSsid(ssid))
                Thread.sleep(120)
            }
            Log.d(TAG, "Provision: 0x34 password (redacted)")
            sendPacket(BleProtocol.buildWifiPassword(password))
            Thread.sleep(120)
            if (manifestUrl.isNotBlank()) {
                Log.d(TAG, "Provision: 0x3E manifest_url=$manifestUrl")
                sendPacket(BleProtocol.buildManifestUrl(manifestUrl))
                Thread.sleep(120)
            }
            if (!deviceId.isNullOrBlank()) {
                Log.d(TAG, "Provision: 0x40 device_id=$deviceId")
                sendPacket(BleProtocol.buildDeviceId(deviceId))
                Thread.sleep(120)
            }
            if (!deviceToken.isNullOrBlank()) {
                Log.d(TAG, "Provision: 0x42 device_token (redacted)")
                sendPacket(BleProtocol.buildDeviceToken(deviceToken))
                Thread.sleep(120)
            }

            Log.d(TAG, "Provision: 0x38 WIFI_CONNECT")
            sendPacket(BleProtocol.buildWifiConnect())

            var connected = false
            for (attempt in 1..PROVISION_STATUS_RETRIES) {
                Thread.sleep(PROVISION_STATUS_INTERVAL_MS)
                val status = queryStatusLocked()
                Log.d(TAG, "Provision: status poll $attempt -> wifi=${status?.wifiConnected}")
                if (status?.wifiConnected == true) {
                    connected = true
                    break
                }
            }
            if (!connected) {
                // 措辞要点：配置此刻**已经写入设备 NVS**（每条 set 命令固件都会落盘），
                // 说成「配网失败」会让人以为要从头再来。
                return@withContext Result.failure(
                    Exception(
                        "配置已写入设备，但 ${PROVISION_STATUS_RETRIES * PROVISION_STATUS_INTERVAL_MS / 1000} 秒内" +
                            "未能确认联网成功。请核对密码，并确认该 Wi-Fi 是 2.4GHz" +
                            "（固件不支持 5GHz）；也可稍后用下方「刷新」查看设备状态"
                    )
                )
            }

            Log.d(TAG, "Provision: 0x44 SYNC_NOW")
            sendPacket(BleProtocol.buildSyncNow())

            Result.success("配网成功，已触发同步")
        } catch (e: Exception) {
            Log.e(TAG, "provisionWifi failed", e)
            Result.failure(Exception("配网失败: ${e.message}"))
        }
        }
    }

    /**
     * 回读设备当前的配网配置（0x31 / 0x33 / 0x3F）。
     *
     * 用途是让「设备到底记住了什么」可见，直接区分「设备未保存」与网络连接失败。
     * 参数服务支持已知 schema 的版本化迁移；记录损坏、恢复出厂或来自未知版本时可能
     * 回落到默认值。
     *
     * device_token 无读回命令（固件不提供，避免机密外泄），故此处不含它。
     */
    suspend fun queryProvisionConfig(): ProvisionConfig? = withContext(Dispatchers.IO) {
        operationLock.withLock {
        try {
            val enable = sendCommandAndWaitResponse(
                BleProtocol.buildPacket(BleProtocol.CH_WIFI_ENABLE_GET),
                expectedChannel = BleProtocol.CH_WIFI_ENABLE_GET
            )
                ?.let { BleProtocol.parseResponse(it) }
                ?.takeIf { it.channel == BleProtocol.CH_WIFI_ENABLE_GET }
                ?.payload?.firstOrNull()?.let { (it.toInt() and 0xFF) == 1 }

            val ssid = sendCommandAndWaitResponse(
                BleProtocol.buildPacket(BleProtocol.CH_WIFI_SSID_GET),
                expectedChannel = BleProtocol.CH_WIFI_SSID_GET
            )
                ?.let { BleProtocol.parseResponse(it) }
                ?.takeIf { it.channel == BleProtocol.CH_WIFI_SSID_GET }
                ?.payload?.toString(Charsets.UTF_8)

            val manifestUrl = sendCommandAndWaitResponse(
                BleProtocol.buildPacket(BleProtocol.CH_MANIFEST_URL_GET),
                expectedChannel = BleProtocol.CH_MANIFEST_URL_GET
            )
                ?.let { BleProtocol.parseResponse(it) }
                ?.takeIf { it.channel == BleProtocol.CH_MANIFEST_URL_GET }
                ?.payload?.toString(Charsets.UTF_8)

            if (enable == null && ssid == null && manifestUrl == null) return@withContext null
            ProvisionConfig(
                wifiEnabled = enable ?: false,
                ssid = ssid ?: "",
                manifestUrl = manifestUrl ?: ""
            )
        } catch (e: Exception) {
            Log.e(TAG, "queryProvisionConfig failed", e)
            null
        }
        }
    }

    /**
     * 触发设备立即做一次 Wi-Fi 同步（0x44）。
     *
     * 不改变轮询行为，也不保证一定会下载 —— 若设备本地版本已是最新，固件判定
     * "up to date" 而跳过，这是版本门控的正常行为，不是失败。
     */
    suspend fun triggerSyncNow(): Boolean = withContext(Dispatchers.IO) {
        try {
            if (!isConnected() || writeCharacteristic == null) return@withContext false
            sendPacket(BleProtocol.buildSyncNow())
            true
        } catch (e: Exception) {
            Log.e(TAG, "triggerSyncNow failed", e)
            false
        }
    }

    /**
     * 删除设备上指定 id 的 .film（0x05）。
     *
     * 删除后设备侧 id 会重排，调用方必须重新查询列表，不能按旧索引连续删。
     */
    suspend fun deleteFile(fileId: Int): Boolean = sendSimpleCommand(
        BleProtocol.buildFileDelete(fileId), "deleteFile($fileId)"
    )

    /**
     * 清空设备上全部 .film（0x45）。
     *
     * 用于「只保留当天菜单」：下发新一套画面之前调一次，避免历史版本被按键轮播翻出来。
     * 不清屏，当前画面保留到下一张图上屏。
     */
    suspend fun clearAllFilms(): Boolean = sendSimpleCommand(
        BleProtocol.buildFileClearAll(), "clearAllFilms"
    )

    /**
     * 静默接收开关（0x46）：只保存不上屏。
     *
     * 连发多张时**必须**先开启：电子纸刷一次约 20 秒，若每张 FILE_STOP 都触发刷屏，
     * 对端会在等待中 GATT 超时断连。
     */
    suspend fun setSilentReceive(enable: Boolean): Boolean = sendSimpleCommand(
        BleProtocol.buildSilentReceive(enable), "setSilentReceive($enable)"
    )

    /** 让设备按当前做饭时段自动选图显示（0x47），窗口外显示今日总览。 */
    suspend fun displayByMealTime(): Boolean = sendSimpleCommand(
        BleProtocol.buildDisplayNow(), "displayByMealTime"
    )

    /**
     * 休眠模式开关（0x25）。
     *
     * 关闭休眠会让 CPU 与 BLE 广播常开，实测一夜即把电池耗到欠压反复重启
     * （BROWNOUT_RST boot loop）。仅在需要长时间连续调试时临时关闭。
     */
    suspend fun setSleepMode(enable: Boolean): Boolean = sendSimpleCommand(
        BleProtocol.buildSleepOnOff(enable), "setSleepMode($enable)"
    )

    /** 查询休眠模式（0x26）。返回 null 表示查询失败。 */
    suspend fun querySleepMode(): Boolean? = withContext(Dispatchers.IO) {
        try {
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildSleepOnOffGet(),
                expectedChannel = BleProtocol.CH_SLEEP_ONOFF_GET
            ) ?: return@withContext null
            val parsed = BleProtocol.parseResponse(response) ?: return@withContext null
            parsed.payload.firstOrNull()?.let { (it.toInt() and 0xFF) == 1 }
        } catch (e: Exception) {
            Log.e(TAG, "querySleepMode failed", e)
            null
        }
    }

    /**
     * 下发唤醒时间表（0x48）。
     *
     * 决定设备「什么时候醒」：三餐前唤醒槽位 + 兜底周期，两者取较小值。
     * App 按用餐时间与提前量计算槽位；固件出厂值覆盖默认三餐。
     *
     * [BleProtocol.buildWakeSchedule] 对非法时间表抛 IllegalArgumentException，这里接住并
     * 返回 false，具体哪一项不合法写在日志里（固件拒收只回一个 0xFF，看不出原因）。
     */
    suspend fun setWakeSchedule(schedule: BleProtocol.WakeSchedule): Boolean =
        withContext(Dispatchers.IO) {
            try {
                if (!isConnected() || writeCharacteristic == null) {
                    Log.w(TAG, "setWakeSchedule: not connected")
                    return@withContext false
                }
                val packet = BleProtocol.buildWakeSchedule(schedule)
                val response = sendCommandAndWaitResponse(
                    packet,
                    expectedChannel = BleProtocol.CH_WAKE_SCHEDULE
                )
                if (response == null) {
                    Log.w(TAG, "setWakeSchedule: no ack")
                    return@withContext false
                }
                val ack = BleProtocol.parseResponse(response)
                    ?.payload?.firstOrNull()
                    ?.let { it.toInt() and 0xFF }
                when (ack) {
                    1 -> true
                    // 固件校验失败整条拒绝、参数一个字节都不改，失败后设备仍是上一份
                    // 时间表，不需要回滚。
                    0xFF -> {
                        Log.e(
                            TAG,
                            "setWakeSchedule: 固件拒收（0xFF），时间表未生效；" +
                                "slots=${schedule.slotLabels()} fallback=${schedule.fallbackMinutes}min"
                        )
                        false
                    }
                    else -> {
                        Log.w(TAG, "setWakeSchedule: 未知 ack=$ack")
                        false
                    }
                }
            } catch (e: IllegalArgumentException) {
                // 本地校验就拦下来了，帧根本没发出去。
                Log.e(TAG, "setWakeSchedule: 时间表不合法，未发送 —— ${e.message}")
                false
            } catch (e: Exception) {
                Log.e(TAG, "setWakeSchedule failed", e)
                false
            }
        }

    /**
     * 回读设备当前的唤醒时间表（0x49）。
     *
     * 与 [queryProvisionConfig] 同理让「设备到底记住了什么」可见：时间表持久化在 NVS，
     * 记录不存在、损坏或恢复出厂后会回到默认值，回读可区分这种情况与「下发没生效」。
     */
    suspend fun queryWakeSchedule(): BleProtocol.WakeSchedule? = withContext(Dispatchers.IO) {
        try {
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildWakeScheduleGet(),
                expectedChannel = BleProtocol.CH_WAKE_SCHEDULE_GET
            ) ?: return@withContext null
            val parsed = BleProtocol.parseResponse(response) ?: return@withContext null
            BleProtocol.parseWakeSchedule(parsed.payload)
        } catch (e: Exception) {
            Log.e(TAG, "queryWakeSchedule failed", e)
            null
        }
    }

    /**
     * 设置轮询周期（0x4A），返回**固件钳制后实际生效的秒数**；失败返回 null。
     *
     * 必须回钳制值而不是把入参当结果：固件把周期钳到 [30, 86400]，界面显示 5 秒而设备
     * 实际 30 秒，会让续航结论完全错掉。
     */
    suspend fun setPollIntervalSeconds(seconds: Int): Int? = withContext(Dispatchers.IO) {
        try {
            if (!isConnected() || writeCharacteristic == null) {
                Log.w(TAG, "setPollIntervalSeconds: not connected")
                return@withContext null
            }
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildPollInterval(seconds),
                expectedChannel = BleProtocol.CH_POLL_INTERVAL
            ) ?: return@withContext null
            val parsed = BleProtocol.parseResponse(response) ?: return@withContext null
            val actual = BleProtocol.parsePollIntervalSeconds(parsed.payload)
            if (actual == null) {
                // 载荷不是 4 字节 —— 固件用 1 字节 0xFF 表示拒收。
                Log.e(TAG, "setPollIntervalSeconds: 固件拒收，周期未生效（请求 ${seconds}s）")
                return@withContext null
            }
            if (actual != seconds) {
                Log.w(TAG, "setPollIntervalSeconds: 请求 ${seconds}s，固件钳制为 ${actual}s")
            }
            actual
        } catch (e: Exception) {
            Log.e(TAG, "setPollIntervalSeconds failed", e)
            null
        }
    }

    /** 查询轮询周期（0x4B）。返回秒数，失败返回 null。 */
    suspend fun queryPollIntervalSeconds(): Int? = withContext(Dispatchers.IO) {
        try {
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildPollIntervalGet(),
                expectedChannel = BleProtocol.CH_POLL_INTERVAL_GET
            ) ?: return@withContext null
            val parsed = BleProtocol.parseResponse(response) ?: return@withContext null
            BleProtocol.parsePollIntervalSeconds(parsed.payload)
        } catch (e: Exception) {
            Log.e(TAG, "queryPollIntervalSeconds failed", e)
            null
        }
    }

    /**
     * 下发置顶自定义画面（0x4C）。
     *
     * 手绘涂鸦与相册照片走既有 [sendFilm] 就能上屏，但不 pin 住看不住：固件里「上次切到
     * 哪个时段」深睡即归零，时间一同步就把自定义图换回菜单。所以传完图要紧接着 pin 一次。
     *
     * [BleProtocol.buildPinFilm] 对非法配置抛 IllegalArgumentException，这里接住并返回
     * false（同 [setWakeSchedule]），具体哪一条不合法写在日志里。
     */
    suspend fun setPinnedFilm(pinned: BleProtocol.PinnedFilm): Boolean =
        withContext(Dispatchers.IO) {
            try {
                if (!isConnected() || writeCharacteristic == null) {
                    Log.w(TAG, "setPinnedFilm: not connected")
                    return@withContext false
                }
                val packet = BleProtocol.buildPinFilm(pinned)
                val response = sendCommandAndWaitResponse(
                    packet,
                    expectedChannel = BleProtocol.CH_PIN_FILM
                )
                if (response == null) {
                    Log.w(TAG, "setPinnedFilm: no ack")
                    return@withContext false
                }
                val ack = BleProtocol.parseResponse(response)
                    ?.payload?.firstOrNull()
                    ?.let { it.toInt() and 0xFF }
                when (ack) {
                    1 -> true
                    // 固件校验失败整条拒绝、配置一个字节都不改，失败后设备仍是上一份
                    // pin 配置，不需要回滚。
                    0xFF -> {
                        Log.e(
                            TAG,
                            "setPinnedFilm: 固件拒收（0xFF），置顶未生效；" +
                                "mode=${pinned.mode}(${pinned.mode.raw}) " +
                                "window=[${pinned.windowStartMinutes},${pinned.windowEndMinutes}) " +
                                "name=${pinned.fileName}"
                        )
                        false
                    }
                    else -> {
                        Log.w(TAG, "setPinnedFilm: 未知 ack=$ack")
                        false
                    }
                }
            } catch (e: IllegalArgumentException) {
                // 本地校验就拦下来了，帧根本没发出去。最常踩的是文件名含
                // today/breakfast/lunch/dinner 关键字这一条。
                Log.e(TAG, "setPinnedFilm: 配置不合法，未发送 —— ${e.message}")
                false
            } catch (e: Exception) {
                Log.e(TAG, "setPinnedFilm failed", e)
                false
            }
        }

    /**
     * 取消置顶（0x4C，单字节 `mode=0`）。
     *
     * 固件收到即**擦掉整条记录**，选图行为回到没有本功能时的样子。不清屏也不切图：当前
     * 画面留到下一次自动选图（`0x47` / FN 短按 / 同步成功）才变，UI 别说成「已恢复菜单」。
     */
    suspend fun clearPinnedFilm(): Boolean = withContext(Dispatchers.IO) {
        try {
            if (!isConnected() || writeCharacteristic == null) {
                Log.w(TAG, "clearPinnedFilm: not connected")
                return@withContext false
            }
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildPinFilmClear(),
                expectedChannel = BleProtocol.CH_PIN_FILM
            )
            if (response == null) {
                Log.w(TAG, "clearPinnedFilm: no ack")
                return@withContext false
            }
            val ack = BleProtocol.parseResponse(response)
                ?.payload?.firstOrNull()
                ?.let { it.toInt() and 0xFF }
            when (ack) {
                1 -> true
                0xFF -> {
                    Log.e(TAG, "clearPinnedFilm: 固件拒收（0xFF），置顶仍在生效")
                    false
                }
                else -> {
                    Log.w(TAG, "clearPinnedFilm: 未知 ack=$ack")
                    false
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "clearPinnedFilm failed", e)
            false
        }
    }

    /**
     * 回读设备当前的置顶配置（0x4D）。
     *
     * pin 配置存在 NVS 一个独立 blob key 里，记录不存在 / 长度不符 / 内容非法固件都一律
     * 视为「未 pin」并静默回落到菜单逻辑，不回读就分不清「没下发成功」与「记录被判废」。
     *
     * 两种「没有 pin」要分开：`PinnedFilm(mode = OFF, …)` 是设备明确回答「未设置」，
     * **null 才是读取失败**（超时 / 帧不自洽）。
     */
    suspend fun queryPinnedFilm(): BleProtocol.PinnedFilm? = withContext(Dispatchers.IO) {
        try {
            val response = sendCommandAndWaitResponse(
                BleProtocol.buildPinFilmGet(),
                expectedChannel = BleProtocol.CH_PIN_FILM_GET
            ) ?: return@withContext null
            val parsed = BleProtocol.parseResponse(response) ?: return@withContext null
            BleProtocol.parsePinnedFilm(parsed.payload)
        } catch (e: Exception) {
            Log.e(TAG, "queryPinnedFilm failed", e)
            null
        }
    }

    /**
     * 发一条不需要解析响应的命令。
     *
     * 统一在这里做「已连接 + 写特征就绪」的前置检查并吞掉异常，调用方（UI 按钮）只需
     * 关心成功与否。
     */
    private suspend fun sendSimpleCommand(packet: ByteArray, label: String): Boolean =
        withContext(Dispatchers.IO) {
            try {
                if (!isConnected() || writeCharacteristic == null) {
                    Log.w(TAG, "$label: not connected")
                    return@withContext false
                }
                sendPacket(packet)
                true
            } catch (e: Exception) {
                Log.e(TAG, "$label failed", e)
                false
            }
        }

    /** 设备侧已持久化的配网配置（不含 device_token —— 固件不提供读回）。 */
    data class ProvisionConfig(
        val wifiEnabled: Boolean,
        val ssid: String,
        val manifestUrl: String
    )
}
