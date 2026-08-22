package org.smartfridgemagnet.ui.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.data.FamilyMember
import org.smartfridgemagnet.data.HealthGoal
import org.smartfridgemagnet.data.LocalRepository
import org.smartfridgemagnet.data.MemberRole
import org.smartfridgemagnet.data.DietaryPreferences
import org.smartfridgemagnet.data.toProfileMemberDto
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID

/** 家庭画像向后端同步的状态。用密封类而不是几个布尔：这四种情况的文案与颜色都不同。 */
sealed interface ProfileSyncState {
    object Syncing : ProfileSyncState
    data class Synced(val memberCount: Int, val allergens: List<String>) : ProfileSyncState
    data class Failed(val message: String) : ProfileSyncState

    /** 没配后端地址。不算错误，但必须说出来 —— 此时忌口只存在本地。 */
    object NotConfigured : ProfileSyncState
}

class FamilyViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = LocalRepository(application)

    private val _members = MutableStateFlow<List<FamilyMember>>(emptyList())
    val members: StateFlow<List<FamilyMember>> = _members

    private val _selectedMember = MutableStateFlow<FamilyMember?>(null)
    val selectedMember: StateFlow<FamilyMember?> = _selectedMember

    /**
     * 上一次向后端同步画像的结果。null = 还没同步过。
     *
     * 需要把它显示出来，因为这条链路的失败是静默的：本地保存永远成功，而后端没收到时
     * 推荐依旧不避让过敏源，界面上看不出任何异常。
     */
    private val _syncState = MutableStateFlow<ProfileSyncState?>(null)
    val syncState: StateFlow<ProfileSyncState?> = _syncState

    /**
     * 上一次同步任务。整体替换必须串行 —— 见 [syncToBackend]。
     */
    private var syncJob: Job? = null
    private val syncMutex = Mutex()

    init {
        loadMembers()
        // 进这一页就同步一次，而不是只在增删改时触发：否则已经配好成员的用户不动手就永远同步不
        // 上去，而 syncState 为 null 时横幅不渲染，界面上没有任何线索。
        //
        // 只在本地已有成员时推：空列表分不清「用户真的一个都没配」和「本地还没数据（刚重装）」，
        // 而 replaceProfileMembers 是整份替换 —— 后一种情况会把后端已有的过敏源清空。
        // 用户显式删掉最后一个成员走的是 deleteMember，那是明确意图，照常推空。
        if (_members.value.isNotEmpty()) syncToBackend()
    }

    fun loadMembers() {
        _members.value = repository.getFamilyMembers()
    }

    fun selectMember(member: FamilyMember) {
        _selectedMember.value = member
    }

    fun addMember(
        name: String,
        avatar: String,
        role: MemberRole,
        goal: HealthGoal,
        preferences: DietaryPreferences = DietaryPreferences()
    ) {
        val member = FamilyMember(
            id = "member_${UUID.randomUUID().toString().take(8)}",
            name = name,
            avatar = avatar,
            role = role,
            goal = goal,
            preferences = preferences
        )
        repository.addFamilyMember(member)
        loadMembers()
        syncToBackend()
    }

    fun updateMember(member: FamilyMember) {
        repository.updateFamilyMember(member)
        loadMembers()
        syncToBackend()
    }

    fun deleteMember(memberId: String) {
        repository.deleteFamilyMember(memberId)
        loadMembers()
        syncToBackend()
    }

    /**
     * 把当前成员列表推给后端。
     *
     * 每次增删改后自动触发，而不是留一个「同步」按钮：忘记按的后果是推荐继续无视过敏源，而用户没有
     * 线索知道自己漏了一步。失败也不阻断本地保存 —— 联不上后端时仍应能编辑成员，只是要把「还没同步」
     * 如实显示出来。
     *
     * 同步是**串行**的。后端那个接口是整份替换，连续改两位成员时两个替换同时在飞，后发先到会把第二次
     * 的编辑盖回第一次的内容。只 cancel 上一个任务不够（cancel 未必能中断已交给 OkHttp 的调用），
     * 所以用 Mutex 让它们排队。
     */
    fun syncToBackend() {
        syncJob?.cancel()
        val client = (getApplication() as FridgeMagnetApp).sfmBackendClient
        if (!client.config.configured) {
            _syncState.value = ProfileSyncState.NotConfigured
            return
        }
        val payload = _members.value.map { it.toProfileMemberDto() }
        _syncState.value = ProfileSyncState.Syncing
        syncJob = viewModelScope.launch {
            syncMutex.withLock {
                // 排队期间又发起了新一次同步时，本次结果已经过时，写进 _syncState 只会把横幅刷回
                // 旧数据的成员数与过敏源。
                if (!isActive) return@withLock
                client.replaceProfileMembers(payload).fold(
                    onSuccess = {
                        _syncState.value = ProfileSyncState.Synced(
                            memberCount = it.memberCount,
                            allergens = it.allergens
                        )
                    },
                    onFailure = {
                        _syncState.value = ProfileSyncState.Failed(it.message ?: "同步失败")
                    }
                )
            }
        }
    }

    fun getMemberPreferencesText(member: FamilyMember): String {
        return buildString {
            append(when (member.role) {
                MemberRole.CHILD -> "成长期"
                MemberRole.ADULT -> "成人"
                MemberRole.ELDER -> "老年人"
                MemberRole.PREGNANT -> "孕妇"
            })
            append(" · ")
            append(when (member.goal) {
                HealthGoal.BALANCED -> "均衡营养"
                HealthGoal.LOSE_FAT -> "减脂"
                HealthGoal.GAIN_MUSCLE -> "增肌"
                HealthGoal.CONTROL_SUGAR -> "控糖"
                HealthGoal.CONTROL_SALT -> "控盐"
            })
            if (member.preferences.spiceLevel != org.smartfridgemagnet.data.SpiceLevel.MILD) {
                append(" · ")
                append(when (member.preferences.spiceLevel) {
                    org.smartfridgemagnet.data.SpiceLevel.NONE -> "不辣"
                    org.smartfridgemagnet.data.SpiceLevel.MILD -> "微辣"
                    org.smartfridgemagnet.data.SpiceLevel.MEDIUM -> "中辣"
                    org.smartfridgemagnet.data.SpiceLevel.HOT -> "重辣"
                })
            }
            // 过敏排在忌口前面并且带前缀：这一行是列表上唯一能看到约束的地方，
            // 而过敏填错（或没填）的后果比忌口重得多，得让它显眼。
            if (member.preferences.allergies.isNotEmpty()) {
                append(" · 过敏：")
                append(member.preferences.allergies.joinToString("、"))
            }
            if (member.preferences.dislikes.isNotEmpty()) {
                append(" · 不吃：")
                append(member.preferences.dislikes.joinToString("、"))
            }
        }
    }
}
