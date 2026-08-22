package org.smartfridgemagnet.transfer

/*
 * TransferMode(BLE / WIFI) 已移除：App 只有一条对设备的通道 —— BLE。所谓「Wi-Fi 传输」不是
 * App 经 Wi-Fi 推给设备（固件只实现了 HTTP 客户端、没有服务端），而是设备自己按 manifest 主动
 * 拉取。两者不在同一层：BLE 是 App 推、Wi-Fi 是设备拉，发起方不同，并列成 mode 枚举会长出
 * 「Wi-Fi 模式下不能配网」这种自相矛盾的分支。
 */

/**
 * 连接状态
 */
sealed class ConnectionState {
    data object Disconnected : ConnectionState()
    data object Scanning : ConnectionState()
    data object Connecting : ConnectionState()
    data object Connected : ConnectionState()
    data class Error(val message: String) : ConnectionState()
}

/**
 * 传输进度
 */
data class TransferProgress(
    val state: TransferState = TransferState.IDLE,
    val bytesSent: Long = 0,
    val totalBytes: Long = 0,
    val percentage: Int = 0,
    val error: String? = null
)

/**
 * 传输状态
 */
enum class TransferState {
    IDLE,           // 空闲
    PREPARING,      // 准备中
    SENDING_NAME,   // 发送文件名
    SENDING_LEN,    // 发送文件长度
    SENDING_DATA,   // 发送数据
    COMPLETED,      // 完成
    FAILED          // 失败
}

/**
 * 设备信息
 */
data class DeviceInfo(
    val name: String,
    val address: String,
    val type: DeviceType,
    val rssi: Int = 0
)

/**
 * 设备类型
 */
enum class DeviceType {
    BLE,
    WIFI
}
