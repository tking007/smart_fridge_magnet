package org.smartfridgemagnet.ui

import org.smartfridgemagnet.ui.theme.accents
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
// 返回箭头与右侧 chevron 用 AutoMirrored 版：这两个图标在 RTL 语言下必须镜像，
// Icons.Filled 的同名项已被标记废弃。其余（Save / Key / Schedule 之类）没有方向性，
// 继续走 filled。
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.data.LocalRepository
import org.smartfridgemagnet.ui.theme.categoryAccent
import org.smartfridgemagnet.ui.components.IconBadge
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.device.WakePlanner
import org.smartfridgemagnet.network.MealScheduleDto
import org.smartfridgemagnet.network.MealScheduleItemDto
import org.smartfridgemagnet.network.MealScheduleTimeDto
import org.smartfridgemagnet.network.SfmBackendClient
import org.smartfridgemagnet.transfer.BleProtocol
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** 设置入口及三个二级页；草稿保存在父级，切换分组时不丢失。 */
@Composable
fun SettingsScreen(
    repository: LocalRepository,
    /** 后端配置保存后的回调：由调用方触发客户端重载配置。 */
    onSfmSaved: (() -> Unit)? = null
) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val client = remember { app.sfmBackendClient }
    val scope = rememberCoroutineScope()

    /** null = 停在一级入口列表。 */
    var section by remember { mutableStateOf<SettingsSection?>(null) }
    // 二级页里按返回先回入口列表，不直接退出设置页。没有这一条，用户在二级页按系统返回
    // 会一路退回首页，改到一半的东西也跟着没了。
    BackHandler(enabled = section != null) { section = null }

    val meal = remember { MealSettingsState() }
    val sfm = remember { SfmSettingsState(repository) }

    // ---- 记住的设备 ----
    var lastDeviceName by remember { mutableStateOf(repository.getLastDeviceName()) }
    var lastDeviceAddress by remember { mutableStateOf(repository.getLastDeviceAddress()) }

    // ---- 统一消息通道 ----
    // 保存反馈收敛成页面顶部一条消息，各二级页共用：每块配置各有一个 showSaveSuccess 时，一旦为
    // true 就再也不消失，用户改完第二个字段仍看着上一次的「已保存」。
    var message by remember { mutableStateOf<SettingsMessage?>(null) }
    var messageSeq by remember { mutableIntStateOf(0) }
    val post: (SettingsMessage) -> Unit = { msg ->
        message = msg
        messageSeq++
    }

    // 成功提示自己消失，诊断输出留着等人读完。
    // 用 messageSeq 当 key 而不是 message 本身：连续两次保存同一份配置时消息内容相同，
    // 按内容做 key 的话第二次不会重启计时，提示会提前消失。
    LaunchedEffect(messageSeq) {
        val current = message ?: return@LaunchedEffect
        if (current.autoDismiss) {
            delay(MESSAGE_AUTO_DISMISS_MS)
            message = null
        }
    }

    val appVersion = remember {
        runCatching {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName
        }.getOrNull()?.takeIf { it.isNotBlank() } ?: "未知"
    }
    // 家庭人数只读展示：编辑入口只留在家庭成员页。两处都能改同一个值，必然出现
    // "在设置里改成 4 人、成员列表里还是 3 个人"这种不一致，而后端的人数是按成员数算的。
    val familyCount = remember { repository.getFamilyMembers().size }

    val loadMealSchedule: suspend () -> Unit = {
        if (!client.config.configured) {
            meal.error = "后端地址为空，无法读取用餐时间"
        } else {
            meal.loading = true
            client.fetchMealSchedule().fold(
                onSuccess = { meal.adopt(it) },
                onFailure = { meal.error = it.message ?: "读取用餐时间失败" }
            )
            meal.loading = false
        }
    }

    // 进设置页就拉一次：一级列表的摘要要显示当前的三餐时间与每日更新时间，等用户点进去再拉
    // 就只能先显示一行"读取中"，而那正是列表最该给出信息的地方。
    LaunchedEffect(Unit) { loadMealSchedule() }

    /** 把当前输入框里的后端配置落盘并让客户端重载。测试连接前也要走一遍。 */
    val persistSfm: () -> Unit = {
        repository.saveSfmBaseUrl(sfm.baseUrl)
        // Token 走 Keystore 加密，而 Keystore 会在用户刚改过锁屏密码时抛。
        // 崩溃不是这件事该有的结果 —— 一句提示就够，其余配置照存。
        repository.saveSfmAppToken(sfm.appToken).onFailure {
            post(SettingsMessage("后端 Token 没能安全保存：${it.message}", isError = true))
        }
        repository.saveSfmDeviceId(sfm.deviceId)
        // 必须通知 App 重载：客户端持有的是配置副本，
        // 只写 SharedPreferences 不会让已存在的客户端实例改变行为。
        onSfmSaved?.invoke()
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            SettingsTopBar(
                title = section?.title ?: "设置",
                onNavigateUp = section?.let { { section = null } }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // 消息条放在滚动区**外面**：保存按钮可能在页面很下面，把提示放进滚动内容里
            // 就会出现"点了保存、提示在屏幕外"。
            message?.let { msg ->
                MessageBar(msg, onDismiss = { message = null })
            }

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = Spacing.lg)
                    .padding(bottom = Spacing.dockClearance),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm)
            ) {
                when (section) {
                    null -> SettingsIndex(
                        mealSummary = meal.summary(),
                        aboutSummary = "版本 $appVersion · " +
                            if (lastDeviceAddress.isBlank()) "未记住设备"
                            else "已记住 ${lastDeviceName.ifBlank { "未命名设备" }}",
                        developerSummary = sfm.baseUrl.ifBlank { "未配置后端地址" },
                        onOpen = { section = it }
                    )

                    SettingsSection.MEALS -> MealSectionContent(
                        state = meal,
                        familyCount = familyCount,
                        client = client,
                        onReload = { scope.launch { loadMealSchedule() } },
                        onMessage = post
                    )

                    SettingsSection.ABOUT -> AboutSectionContent(
                        appVersion = appVersion,
                        manifestUrl = repository.buildManifestUrl(),
                        lastDeviceName = lastDeviceName,
                        lastDeviceAddress = lastDeviceAddress,
                        onClearDevice = {
                            repository.clearLastDevice()
                            lastDeviceName = ""
                            lastDeviceAddress = ""
                            post(SettingsMessage("已清除记住的设备，下次进设备页需手动扫描"))
                        }
                    )

                    SettingsSection.DEVELOPER -> DeveloperSectionContent(
                        state = sfm,
                        client = client,
                        onPersist = persistSfm,
                        onReloadMeal = { scope.launch { loadMealSchedule() } },
                        onMessage = post,
                        manifestUrl = repository.buildManifestUrl()
                    )
                }
            }
        }
    }

    // 用餐时间编辑弹窗
    meal.editingIndex?.let { index ->
        val item = meal.draft.getOrNull(index)
        if (item == null) {
            meal.editingIndex = null
        } else {
            MealTimePickerDialog(
                title = "${mealLabelOf(item.mealType, item.label)} 时间",
                initialMinute = WakePlanner.parseHhMm(item.mealTime) ?: 0,
                onDismiss = { meal.editingIndex = null },
                onConfirm = { hour, minute ->
                    meal.draft = meal.draft.toMutableList().also {
                        it[index] = item.copy(mealTime = "%02d:%02d".format(hour, minute))
                    }
                    meal.editingIndex = null
                }
            )
        }
    }
}

// ==================== 一级入口列表 ====================

/** 设置页的三个分组。 */
private enum class SettingsSection(val title: String, val icon: ImageVector) {
    MEALS("用餐与推送", Icons.Default.Restaurant),
    ABOUT("关于与诊断", Icons.Default.Info),
    DEVELOPER("开发者选项", Icons.Default.DeveloperMode)
}

@Composable
private fun SettingsIndex(
    mealSummary: String,
    aboutSummary: String,
    developerSummary: String,
    onOpen: (SettingsSection) -> Unit
) {
    Spacer(Modifier.height(8.dp))
    SettingsCard {
        SettingsEntryRow(SettingsSection.MEALS, mealSummary) { onOpen(SettingsSection.MEALS) }
        HorizontalDivider()
        SettingsEntryRow(SettingsSection.ABOUT, aboutSummary) { onOpen(SettingsSection.ABOUT) }
        HorizontalDivider()
        // 开发者选项常驻显示，不做「连点 5 次版本号解锁」：后端地址与 Token 是这个项目跑起来的
        // 必要配置，不是可选的高级项，藏起来只会让人找不到。
        SettingsEntryRow(SettingsSection.DEVELOPER, developerSummary) {
            onOpen(SettingsSection.DEVELOPER)
        }
    }
}

/**
 * 一级列表的一行：图标 + 标题 + 一行摘要 + 右侧箭头。
 *
 * 摘要**限一行**（maxLines = 1 + 省略号）。允许折行的话最长的那一项会把整行撑到三行高，
 * 于是四个入口占满一屏，"一眼看完全部分类"这个唯一的好处就没了。
 */
@Composable
private fun SettingsEntryRow(
    section: SettingsSection,
    summary: String,
    onClick: () -> Unit
) {
    // 每个分组一个颜色：整列同色图标会读成一片灰，扫不出自己要找哪一行。
    val accent = categoryAccent(section.ordinal)
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.md)
    ) {
        IconBadge(
            icon = section.icon,
            size = 38.dp,
            container = accent.container,
            tint = accent.solid
        )
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(section.title, style = MaterialTheme.typography.titleSmall)
            Text(
                summary,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null,
            modifier = Modifier.size(20.dp),
            tint = MaterialTheme.colorScheme.outline
        )
    }
}


// ==================== 二级页 1：用餐与推送 ====================

/**
 * 用餐与推送页的可编辑状态。
 *
 * 收进一个 holder 而不是在 [SettingsScreen] 里摆七八个 `var`，只是为了让二级页的
 * Composable 不必收七八个参数 —— 参数一多，"哪个值是草稿、哪个是服务端确认过的"就
 * 只能靠命名区分，而这两者在保存流程里必须分得清（见 [adopt] 与 [savedWakeSlots]）。
 */
private class MealSettingsState {
    var loading by mutableStateOf(false)
    var saving by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)

    /** 服务端确认过的那一份。保存成功后才更新，用来判断唤醒计划有没有真的变。 */
    var meta by mutableStateOf<MealScheduleDto?>(null)

    /** 可编辑草稿。保留 label/enabled 供展示，保存时只发 meal_type + meal_time。 */
    var draft by mutableStateOf<List<MealScheduleItemDto>>(emptyList())
    var autoPublish by mutableStateOf(false)

    /**
     * 提前量（分钟）：设备在每餐前这么久醒来取当餐画面。
     *
     * 设置页里唯一与唤醒有关的旋钮。用户填的是「几点吃饭」和「提前多久」这两件他真的知道的事，
     * 槽位分钟数由 [WakePlanner.computeWakeSlots] 算出来 —— 让用户直接填「设备几点醒」等于要求他
     * 自己做这道减法，而算错的后果他既看不见也无从判断。
     */
    var leadMinutes by mutableStateOf(WakePlanner.LEAD_DEFAULT)

    /** 正在用 TimePicker 编辑的餐次下标。 */
    var editingIndex by mutableStateOf<Int?>(null)

    /** 用一份服务端响应覆盖草稿与合法域。GET 与 PUT 的响应同结构，两处都走这里。 */
    fun adopt(dto: MealScheduleDto) {
        meta = dto
        if (dto.schedule.isNotEmpty()) draft = dto.schedule
        autoPublish = dto.autoPublish
        // 后端存的提前量可能是任意合法值（旧版本有个自由输入的控件）。落到本页三档之外时
        // 吸附到最近的一档，而不是显示成「三个都没选中」—— 那会让人以为设置丢了。
        leadMinutes = WakePlanner.nearestLeadChoice(dto.leadMinutes)
        error = null
    }

    /** 服务端那份对应的唤醒时刻。与保存后的新值比较，决定要不要提示重下唤醒计划。 */
    fun savedWakeSlots(): List<Int> = meta?.let {
        WakePlanner.computeWakeSlots(it.schedule, WakePlanner.nearestLeadChoice(it.leadMinutes))
    }.orEmpty()

    /** 草稿对应的唤醒时刻。 */
    fun draftWakeSlots(): List<Int> = WakePlanner.computeWakeSlots(draft, leadMinutes)

    /**
     * 把算出来的唤醒时刻用人话写出来。
     *
     * 直接把结果显示给用户，是因为这道减法虽然由 App 做，后果仍要由用户承担 ——
     * 「早餐 07:30、提前 30 分钟」算成 07:00 是对的，但如果他把早餐填成了 00:10，
     * 结果是前一天 23:40，只有把数摆出来他才可能发现填错了。
     */
    fun wakeSlotSummary(): String {
        val slots = draftWakeSlots()
        if (slots.isEmpty()) return "三餐时间都没填，设备只按兜底周期醒来"
        val text = slots.joinToString(" · ") { "%02d:%02d".format(it / 60, it % 60) }
        return "设备将在这些时刻醒来：$text"
    }

    /**
     * 一级列表那一行的摘要。
     *
     * 刻意用**草稿**而不是服务端那份：用户把早餐改到 08:00 还没保存就返回列表，摘要若显示
     * 07:30，同一个值在两屏上就是两个数 —— 而草稿才是点保存后会生效的那个。
     */
    fun summary(): String {
        error?.let { return "读取失败：$it" }
        if (loading && draft.isEmpty()) return "正在读取…"
        if (draft.isEmpty()) return "后端没有配置过餐次"
        val times = draft.joinToString(" · ") {
            "${shortMealLabelOf(it.mealType, it.label)} ${it.mealTime.ifBlank { "未设置" }}"
        }
        return "$times · 提前 $leadMinutes 分钟取图"
    }
}

@Composable
private fun MealSectionContent(
    state: MealSettingsState,
    familyCount: Int,
    client: SfmBackendClient,
    onReload: () -> Unit,
    onMessage: (SettingsMessage) -> Unit
) {
    val scope = rememberCoroutineScope()

    SettingsCard {
        InfoRow("家庭人数", "$familyCount 人")
        Hint("人数、忌口与健康目标在首页的「家庭成员」里管理。")
    }

    SectionHeader("用餐时间", "仅用于设备在总览、早餐、午餐和晚餐画面之间按时切换")

    SettingsCard {
        when {
            state.loading && state.draft.isEmpty() -> Row(
                verticalAlignment = Alignment.CenterVertically
            ) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(8.dp))
                Text("正在读取…", style = MaterialTheme.typography.bodySmall)
            }

            state.error != null -> Column {
                Text(
                    state.error!!,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error
                )
                TextButton(onClick = onReload) { Text("重试") }
            }

            state.draft.isEmpty() -> Text(
                "后端没有配置过任何餐次。",
                style = MaterialTheme.typography.bodySmall,
                color = WARN_COLOR
            )

            else -> {
                // 每一行点开一个 TimePicker，**不给裸文本框**：让用户手输 "HH:MM" 必然产出
                // "7:5"、"07：30"（全角冒号）这类后端会拒绝的值，而错误要等到点保存才出现。
                state.draft.forEachIndexed { index, item ->
                    MealTimeRow(
                        item = item,
                        enabled = !state.saving,
                        onClick = { state.editingIndex = index }
                    )
                }
            }
        }
    }

    SectionHeader("提前多久取新菜单", "设备在每餐前这么久醒来联网，取当天该餐的画面")

    SettingsCard {
        Text(
            "选大一点更稳（后端算完菜单需要时间），选小一点更省电。",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Spacer(Modifier.height(10.dp))
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            WakePlanner.LEAD_CHOICES.forEach { minutes ->
                FilterChip(
                    selected = state.leadMinutes == minutes,
                    onClick = { state.leadMinutes = minutes },
                    enabled = !state.saving,
                    label = { Text("$minutes 分钟") },
                    modifier = Modifier.weight(1f)
                )
            }
        }
        Spacer(Modifier.height(10.dp))
        Text(
            state.wakeSlotSummary(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }

    SectionHeader("每日更新", "每天生成三餐菜单并发布一套四张画面")

    SettingsCard {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(Modifier.weight(1f)) {
                Text("每日自动更新", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "关闭后不再生成和下发每日画面，只能在设备页手动下发。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Switch(
                checked = state.autoPublish,
                onCheckedChange = { state.autoPublish = it },
                enabled = !state.saving
            )
        }

        Spacer(Modifier.height(12.dp))

        Button(
            onClick = {
                state.saving = true
                scope.launch {
                    saveMealSettings(state, client, onMessage)
                    state.saving = false
                }
            },
            modifier = Modifier.fillMaxWidth(),
            // meta == null 表示还没成功读到过一份设置。此时不能让人点保存：提前量还是
            // 占位的 0（后端合法域下界是 5），发出去只会换回一条与用户操作无关的 400。
            enabled = !state.saving && state.error == null && state.meta != null &&
                state.draft.all { it.mealTime.isNotBlank() }
        ) {
            if (state.saving) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(8.dp))
                Text("保存中…")
            } else {
                Icon(Icons.Default.Save, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("保存用餐与推送设置")
            }
        }
    }
}

/**
 * 三项一起发（时间表 + 每日更新时间 + 自动下发开关）。
 *
 * 时间表**整份重发**而不只发改动的那一餐：后端是全量替换，只发一餐会把另两餐清掉。
 *
 * 只发 meal_type + meal_time：PUT 不接受 enabled，而它的写入是整行替换，enabled 会回到默认 1，
 * 即重发会顺带启用被停用的餐次。App 与后端目前都没有停用入口，所以与现状一致；将来加停用开关时
 * 必须先让后端保留 enabled。
 *
 * 草稿为空（后端一个餐次都没配过）时 schedule 传 null：后端拒绝空数组，硬发会让「只想改每日更新
 * 时间」被一条与时间表无关的校验错误挡住。
 */
private suspend fun saveMealSettings(
    state: MealSettingsState,
    client: SfmBackendClient,
    onMessage: (SettingsMessage) -> Unit
) {
    val payload = state.draft
        .takeIf { it.isNotEmpty() }
        ?.map { MealScheduleTimeDto(it.mealType, it.mealTime) }
    val before = state.savedWakeSlots()

    client.updateMealSchedule(
        items = payload,
        autoPublish = state.autoPublish,
        leadMinutes = state.leadMinutes
    ).fold(
        onSuccess = { updated ->
            state.adopt(updated)
            // 后端会保证整套画面在第一餐取图前完成发布。
            val after = WakePlanner.computeWakeSlots(
                updated.schedule, WakePlanner.nearestLeadChoice(updated.leadMinutes)
            )
            val summary = buildString {
                if (payload != null) {
                    append(payload.joinToString("、") {
                        "${mealLabelOf(it.mealType)} ${it.mealTime}"
                    })
                    append(" · ")
                }
                append("提前 ${updated.leadMinutes} 分钟取图 · 自动下发")
                append(if (updated.autoPublish) "已开启" else "已关闭")
            }
            // 唤醒时刻真的变了才提示去重下计划。恒定提示一次等于没有提示 ——
            // 只切了「自动下发」开关时设备里那份时间表压根没受影响。
            val changed = after != before
            onMessage(
                SettingsMessage(
                    text = "已保存：$summary",
                    // 带着后续动作的提示不自动消失。
                    autoDismiss = !changed,
                    hint = if (changed) wakeReplanHint(after) else null
                )
            )
        },
        // 后端是原子的：任一项不合法就整体拒绝、一个字节都不写，所以失败后不必回滚本地
        // 草稿，改一下重发即可。错误文案已写明是第几项哪里不合法，直接透传，不要换成自造
        // 的"保存失败"。
        onFailure = {
            onMessage(
                SettingsMessage(
                    text = it.message ?: "保存失败",
                    isError = true,
                    autoDismiss = false,
                    hint = "后端整体校验、全部拒绝，已保存的设置没有变化。"
                )
            )
        }
    )
}




// ==================== 二级页 3：关于与诊断 ====================

@Composable
private fun AboutSectionContent(
    appVersion: String,
    manifestUrl: String,
    lastDeviceName: String,
    lastDeviceAddress: String,
    onClearDevice: () -> Unit
) {
    SettingsCard {
        Text("记住的设备", style = MaterialTheme.typography.titleSmall)
        InfoRow(
            "上次连接",
            if (lastDeviceAddress.isBlank()) "无"
            else "${lastDeviceName.ifBlank { "未命名" }}（$lastDeviceAddress）"
        )
        Hint("换设备前先清除，否则进设备页会先等一次连接超时。")
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = onClearDevice,
            modifier = Modifier.fillMaxWidth(),
            enabled = lastDeviceAddress.isNotBlank(),
            colors = ButtonDefaults.outlinedButtonColors(
                contentColor = MaterialTheme.colorScheme.error
            )
        ) {
            Icon(Icons.Default.BluetoothDisabled, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("清除记住的设备")
        }
    }

    SettingsCard {
        InfoRow("App 版本", appVersion)

        Spacer(Modifier.height(8.dp))
        HorizontalDivider()
        Spacer(Modifier.height(8.dp))

        Text(
            "设备取图地址",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            manifestUrl.ifBlank { "（未配置后端地址）" },
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Medium
        )
        // 这句说的是「拼错了会怎样」，而那个后果（设备静默不更新）用户没法从别处推出来。
        Hint("配网时下发给设备。拼错的表现是设备静默不更新。")
    }

}

// ==================== 二级页 4：开发者选项 ====================

private class SfmSettingsState(repository: LocalRepository) {
    var baseUrl by mutableStateOf(repository.getSfmBaseUrl())
    var appToken by mutableStateOf(repository.getSfmAppToken())
    var deviceId by mutableStateOf(repository.getSfmDeviceId())
    var showToken by mutableStateOf(false)
    var testing by mutableStateOf(false)
    var pulling by mutableStateOf(false)
    var diagnostic by mutableStateOf<String?>(null)
}

/**
 * 后端联调那一组：地址 / Token / 设备编号 / 测试连接 / 补齐本地库存。
 *
 * 地址本可固化（[LocalRepository] 已给 staging 默认值），但 **Token 不能编进 APK**：反编译取常量
 * 字符串是分钟级的事，且它一旦泄露，任何人都能往这套后端写库存、发画面。所以这一组必须留在界面上
 * 可改。
 */
@Composable
private fun DeveloperSectionContent(
    state: SfmSettingsState,
    client: SfmBackendClient,
    onPersist: () -> Unit,
    onReloadMeal: () -> Unit,
    onMessage: (SettingsMessage) -> Unit,
    manifestUrl: String
) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val repository = remember { app.getRepository() }
    val scope = rememberCoroutineScope()

    SectionHeader("冰箱贴后端", "库存同步、菜单渲染与画面下发。设备的取图地址也由这里推导")

    SettingsCard {
        OutlinedTextField(
            value = state.baseUrl,
            onValueChange = { state.baseUrl = it },
            label = { Text("后端地址") },
            placeholder = { Text("https://…") },
            leadingIcon = { Icon(Icons.Default.Link, contentDescription = null) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true
        )
        // 必须 https：设备取图地址由该基址推导，而固件只接受 https 的 manifest
        // （明文会被直接拒收）。这里提前提示，免得配完在设备侧才发现拉不到图。
        if (state.baseUrl.isNotBlank() && !state.baseUrl.startsWith("https://")) {
            Spacer(Modifier.height(4.dp))
            Text(
                "需以 https:// 开头 —— 固件会拒收明文地址的画面清单",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error
            )
        }

        Spacer(Modifier.height(12.dp))

        SecretField(
            value = state.appToken,
            onValueChange = { state.appToken = it },
            label = "后端 Token",
            placeholder = "后端 SFM_API_TOKEN",
            revealed = state.showToken,
            onToggleReveal = { state.showToken = !state.showToken }
        )

        Spacer(Modifier.height(12.dp))

        OutlinedTextField(
            value = state.deviceId,
            onValueChange = { state.deviceId = it },
            label = { Text("设备编号") },
            placeholder = { Text("dev001") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true
        )

        Spacer(Modifier.height(12.dp))

        Button(
            onClick = {
                onPersist()
                state.diagnostic = null
                onMessage(
                    SettingsMessage(
                        text = "后端配置已保存：${state.baseUrl}（设备 ${state.deviceId}）",
                        hint = manifestUrl.takeIf { it.isNotBlank() }?.let { "设备取图地址：$it" }
                    )
                )
                // 后端地址一变，用餐时间就该从新后端重读 —— 否则页面上仍显示旧后端那份，
                // 而保存会写到新后端去。
                onReloadMeal()
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = state.baseUrl.startsWith("https://") && state.deviceId.isNotBlank()
        ) {
            Icon(Icons.Default.Save, null, Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("保存后端配置")
        }

        Spacer(Modifier.height(8.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // 测试连接：把「地址写错」「Token 不对」「网络不通」在配置页就区分开。
            // 否则这些错误要等到用库存同步或下发画面时才暴露，那时更难判断是哪一环。
            OutlinedButton(
                onClick = {
                    state.testing = true
                    state.diagnostic = null
                    scope.launch {
                        // 先落盘并重载，确保测的是当前输入框里的值而非上次保存的值。
                        onPersist()
                        // ping 只探存活，再查一次库存以验证鉴权 —— 两步分开才能区分
                        // 「服务不可达」与「Token 无效」。
                        val ping = client.ping()
                        state.diagnostic = if (ping.isFailure) {
                            "✗ 无法连接：${ping.exceptionOrNull()?.message}"
                        } else {
                            client.fetchInventory(pageSize = 1).fold(
                                onSuccess = { "✓ 连接正常，鉴权通过（库存接口可访问）" },
                                onFailure = { "✗ 服务可达但请求失败：${it.message}" }
                            )
                        }
                        state.testing = false
                    }
                },
                modifier = Modifier.weight(1f),
                enabled = !state.testing && !state.pulling &&
                    state.baseUrl.startsWith("https://")
            ) {
                if (state.testing) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(6.dp))
                    Text("测试中…")
                } else {
                    Text("测试连接")
                }
            }

            // 从后端补齐本地库存。
            //
            // 同步平时是单向的（本地 → 后端），但有两个来源只会写到后端：网页调试工具录入
            // 的食材、以及后端自动保存的识别结果。没有这个入口，它们对 App 永远不可见，
            // 而设备上的菜单却是按后端库存配的 —— 两边看到的冰箱不一样。
            //
            // 只补本地缺的、不整份覆盖：覆盖会把用户刚在 App 里删掉的东西搬回来。
            OutlinedButton(
                onClick = {
                    state.pulling = true
                    state.diagnostic = null
                    scope.launch {
                        hydrateInventoryFromBackend(
                            client, repository, repository.getFridgeInventory()
                        ).fold(
                            onSuccess = { missing ->
                                state.diagnostic = if (missing.isEmpty()) {
                                    "✓ 本地库存已与后端一致，无需补充"
                                } else {
                                    repository.addIngredients(missing)
                                    "✓ 已从后端补入 ${missing.size} 项：" +
                                        missing.take(5).joinToString("、") { it.name } +
                                        if (missing.size > 5) " 等" else ""
                                }
                            },
                            onFailure = { state.diagnostic = "✗ 拉取失败：${it.message}" }
                        )
                        state.pulling = false
                    }
                },
                modifier = Modifier.weight(1f),
                enabled = !state.testing && !state.pulling &&
                    state.baseUrl.startsWith("https://")
            ) {
                if (state.pulling) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(6.dp))
                    Text("拉取中…")
                } else {
                    Text("补齐本地库存")
                }
            }
        }

        // 诊断结果留在卡片内、不进顶部消息条：它是"刚点的那个按钮"的输出，挨着按钮显示
        // 才对得上，而且需要留在屏幕上慢慢读。
        state.diagnostic?.let { text ->
            Spacer(Modifier.height(8.dp))
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                color = if (text.startsWith("✓")) OK_COLOR else MaterialTheme.colorScheme.error
            )
        }
    }

}

// ==================== 内部构件 ====================

private val CARD_BG: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.colorScheme.surface
private val WARN_COLOR: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.accents.warning.onContainer
private val OK_COLOR: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.accents.success.solid

private const val MESSAGE_AUTO_DISMISS_MS = 3500L

/**
 * 顶部消息条的内容。各二级页共用一条通道。
 *
 * @param autoDismiss 纯确认（"已保存 X"）自己消失；带后续动作或需要细读的（校验错误、
 *        "改了用餐时间要重下唤醒计划"）留着，等用户手动关。让"读完就没用了"的提示自动
 *        走开，是为了让**留在屏幕上的提示始终意味着还有事要做**。
 */
private data class SettingsMessage(
    val text: String,
    val isError: Boolean = false,
    val autoDismiss: Boolean = true,
    val hint: String? = null
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsTopBar(title: String, onNavigateUp: (() -> Unit)?) {
    TopAppBar(
        title = { Text(title) },
        colors = TopAppBarDefaults.topAppBarColors(
            containerColor = MaterialTheme.colorScheme.background
        ),
        // 一级列表不给返回箭头：设置本身是底部 dock 的一个 tab，箭头会与 dock 重复，
        // 且「返回到哪」没有答案。只有二级页才需要它回到入口列表。
        navigationIcon = {
            if (onNavigateUp != null) {
                IconButton(onClick = onNavigateUp) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                }
            }
        }
    )
}

@Composable
private fun MessageBar(message: SettingsMessage, onDismiss: () -> Unit) {
    val container = if (message.isError) MaterialTheme.colorScheme.errorContainer
    else MaterialTheme.colorScheme.primaryContainer
    val onContainer = if (message.isError) MaterialTheme.colorScheme.onErrorContainer
    else MaterialTheme.colorScheme.onPrimaryContainer

    Surface(color = container, modifier = Modifier.fillMaxWidth()) {
        Row(
            Modifier.padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp),
            verticalAlignment = Alignment.Top
        ) {
            Icon(
                if (message.isError) Icons.Default.ErrorOutline else Icons.Default.CheckCircle,
                contentDescription = null,
                tint = onContainer,
                modifier = Modifier.size(18.dp)
            )
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    message.text,
                    style = MaterialTheme.typography.bodySmall,
                    color = onContainer
                )
                message.hint?.let {
                    Spacer(Modifier.height(4.dp))
                    Text(it, style = MaterialTheme.typography.labelSmall, color = onContainer)
                }
            }
            IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                Icon(
                    Icons.Default.Close, contentDescription = "关闭",
                    tint = onContainer, modifier = Modifier.size(16.dp)
                )
            }
        }
    }
}

/**
 * 二级页里的分组标题。刻意**不做成 Card** —— 整页都是同款卡片平铺时视觉权重相同，
 * 于是"日常要改的用餐时间"和"配一次就不动的 API Key"看起来一样重要。
 */
@Composable
private fun SectionHeader(title: String, subtitle: String? = null) {
    Column(Modifier.padding(top = 12.dp, bottom = 2.dp)) {
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.primary
        )
        subtitle?.let {
            Text(
                it,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Composable
private fun SettingsCard(content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CARD_BG)
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

/** 控件下面那行小字。存在的意义是把"说明最多两行"这条规矩变成一个统一的入口。 */
@Composable
private fun Hint(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp)
    )
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium
        )
    }
}


/** 带显示/隐藏切换的敏感字段输入框。 */
@Composable
private fun SecretField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    placeholder: String?,
    revealed: Boolean,
    onToggleReveal: () -> Unit
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        placeholder = placeholder?.let { { Text(it) } },
        leadingIcon = { Icon(Icons.Default.Key, contentDescription = null) },
        trailingIcon = {
            IconButton(onClick = onToggleReveal) {
                Icon(
                    if (revealed) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                    contentDescription = if (revealed) "隐藏" else "显示"
                )
            }
        },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        visualTransformation = if (revealed) VisualTransformation.None
        else PasswordVisualTransformation()
    )
}

/** 一餐一行，整行可点开时间选择器。 */
@Composable
private fun MealTimeRow(
    item: MealScheduleItemDto,
    enabled: Boolean,
    onClick: () -> Unit
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled, onClick = onClick)
            .padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
            Icon(
                Icons.Default.Schedule, null, Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.width(8.dp))
            Text(
                mealLabelOf(item.mealType, item.label),
                style = MaterialTheme.typography.bodyMedium
            )
        }
        Text(
            item.mealTime.ifBlank { "未设置" },
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Medium,
            color = if (item.mealTime.isBlank()) MaterialTheme.colorScheme.error
            else MaterialTheme.colorScheme.onSurface
        )
        Spacer(Modifier.width(8.dp))
        Icon(
            Icons.Default.Edit, contentDescription = "修改",
            modifier = Modifier.size(16.dp),
            tint = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

/**
 * 时间选择弹窗。`is24Hour = true` —— 用餐时间在后端与固件里都是 24 小时制的
 * `"HH:MM"`，跟着系统跑 12 小时制只会让"下午 6:30"与存下来的 `18:30` 之间多一层
 * 需要在脑子里做的换算。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MealTimePickerDialog(
    title: String,
    initialMinute: Int,
    onDismiss: () -> Unit,
    onConfirm: (hour: Int, minute: Int) -> Unit
) {
    val state = rememberTimePickerState(
        initialHour = initialMinute / 60,
        initialMinute = initialMinute % 60,
        is24Hour = true
    )
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(
                Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                TimePicker(state = state)
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(state.hour, state.minute) }) { Text("确定") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}

/** 餐次键 -> 中文标签。后端一般会给 label，缺失时按键兜底。 */
private fun mealLabelOf(mealType: String, label: String? = null): String {
    label?.takeIf { it.isNotBlank() }?.let { return it }
    return when (mealType) {
        "breakfast" -> "早餐"
        "lunch" -> "午餐"
        "dinner" -> "晚餐"
        else -> mealType.ifBlank { "未知餐次" }
    }
}

/** 一级列表摘要用的单字标签（早/午/晚）。列表那行只有一行，「早餐 07:30」三份就超宽了。 */
private fun shortMealLabelOf(mealType: String, label: String? = null): String =
    mealLabelOf(mealType, label).removeSuffix("餐")

/**
 * 保存用餐设置后必须给出的引导。
 *
 * 每日更新时间只改了后端设置，**设备里那份唤醒时间表不会跟着变** —— 它由 BLE 一次性写入（0x48），
 * 没有自动同步，而 0x48 只能走 BLE、设置页发不了。不提示的话用户改完会以为万事大吉，而设备照旧按
 * 老饭点醒。
 *
 * 顺带把算出来的新唤醒点列出来，让人能对着设备页核对。
 */
private fun wakeReplanHint(slots: List<Int>): String {
    val listed = if (slots.isEmpty()) "无"
    else slots.joinToString(" / ") { BleProtocol.formatMinuteOfDay(it) }
    return "⚠️ 设备里的唤醒时间表不会自动跟着变。到「设备 → 休眠与唤醒计划」点一次" +
        "「设置唤醒计划」重新下发，否则设备仍按旧时间唤醒。新唤醒点：$listed"
}
