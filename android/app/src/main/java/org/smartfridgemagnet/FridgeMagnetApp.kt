package org.smartfridgemagnet

import android.app.Application
import org.smartfridgemagnet.data.LocalRepository
import org.smartfridgemagnet.network.SfmBackendClient
import org.smartfridgemagnet.network.SfmBackendConfig
import org.smartfridgemagnet.transfer.BleTransfer

class FridgeMagnetApp : Application() {

    /**
     * 冰箱贴后端客户端，App 里唯一的模型入口（识别 / 菜谱 / 改单 / 语音都经它）。
     *
     * **恒非空**：后端未配置时由客户端自己返回「尚未配置后端地址」的失败，
     * 而不是让每个调用点都先判空。
     */
    lateinit var sfmBackendClient: SfmBackendClient
        private set

    private lateinit var repo: LocalRepository

    /** 全局 BLE 传输实例（共享连接状态） */
    lateinit var bleTransfer: BleTransfer
        private set

    override fun onCreate() {
        super.onCreate()
        repo = LocalRepository(this)
        repo.clearLegacyModelCredentials()
        sfmBackendClient = SfmBackendClient(currentBackendConfig())
        bleTransfer = BleTransfer(this)
        // 只迁移已知旧存档，不覆盖用户或后端保存的明确分类。
        repo.migrateInventory()
    }

    fun getRepository(): LocalRepository = repo

    private fun currentBackendConfig() = SfmBackendConfig(
        baseUrl = repo.getSfmBaseUrl(),
        appToken = repo.getSfmAppToken(),
        deviceId = repo.getSfmDeviceId()
    )

    /**
     * 保存后端配置后调用，立即生效。
     *
     * 原地 updateConfig 而非替换实例：持有方（MainActivity）用 `remember` 缓存了实例引用，
     * 替换实例对它不可见 —— 那会让「保存配置后必须重启 App 才生效」重现。
     */
    fun reloadBackendConfig() {
        sfmBackendClient.updateConfig(currentBackendConfig())
    }
}
