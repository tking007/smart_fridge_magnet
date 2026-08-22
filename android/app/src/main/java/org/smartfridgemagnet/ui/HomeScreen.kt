package org.smartfridgemagnet.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Brush
import androidx.compose.material.icons.outlined.CameraAlt
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Kitchen
import androidx.compose.material.icons.outlined.RestaurantMenu
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.smartfridgemagnet.data.FamilyMember
import org.smartfridgemagnet.data.Freshness
import org.smartfridgemagnet.data.Ingredient
import org.smartfridgemagnet.data.IngredientCategory
import org.smartfridgemagnet.data.ShelfLife
import org.smartfridgemagnet.data.WifiLinkState
import org.smartfridgemagnet.data.describeMinutesAgo
import org.smartfridgemagnet.data.foodIconRes
import org.smartfridgemagnet.data.wifiLinkStateOf
import org.smartfridgemagnet.data.toVolumeDisplay
import org.smartfridgemagnet.network.SyncStatusDto
import org.smartfridgemagnet.ui.components.ActionTile
import org.smartfridgemagnet.ui.components.AppButton
import org.smartfridgemagnet.ui.components.AppButtonSize
import org.smartfridgemagnet.ui.components.AppButtonVariant
import org.smartfridgemagnet.ui.components.AppCard
import org.smartfridgemagnet.ui.components.AppSheet
import org.smartfridgemagnet.ui.components.BadgeTone
import org.smartfridgemagnet.ui.components.Bar
import org.smartfridgemagnet.ui.components.BarChart
import org.smartfridgemagnet.ui.components.DonutChart
import org.smartfridgemagnet.ui.components.DonutSlice
import org.smartfridgemagnet.ui.components.EmptyState
import org.smartfridgemagnet.ui.components.FoodTile
import org.smartfridgemagnet.ui.components.HeroHeader
import org.smartfridgemagnet.ui.components.Hint
import org.smartfridgemagnet.ui.components.ProgressTrack
import org.smartfridgemagnet.ui.components.SectionTitle
import org.smartfridgemagnet.ui.components.pressableCard
import org.smartfridgemagnet.ui.components.Stat
import org.smartfridgemagnet.ui.components.StatRow
import org.smartfridgemagnet.ui.components.StatusBadge
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.categoryAccent
import org.smartfridgemagnet.ui.theme.accents
import java.time.LocalDate
import java.time.LocalTime

/**
 * 首页。信息层级自上而下是「设备好不好 → 我要做什么 → 冰箱里有什么」。
 *
 * 设备状态置顶：设备离线时其余入口仍可点但屏幕不会变，不前置成结论就只能一路点到最后才发现。
 */
@Composable
fun HomeScreen(
    members: List<FamilyMember>,
    ingredients: List<Ingredient>,
    inventoryLoading: Boolean = false,
    backendConfigured: Boolean = false,
    deviceId: String = "",
    deviceStatus: SyncStatusDto? = null,
    deviceStatusLoading: Boolean = false,
    deviceStatusError: String? = null,
    onCameraClick: () -> Unit,
    onFamilyClick: () -> Unit,
    onTransferClick: () -> Unit,
    onSettingsClick: () -> Unit = {},
    onEditMember: (FamilyMember) -> Unit = {},
    onAddIngredient: () -> Unit = {},
    onDeleteIngredient: (Int) -> Unit = {},
    onEditIngredient: (Int) -> Unit = {},
    /** 库存上次上行失败的原因；空串表示没有未同步的内容。 */
    inventorySyncError: String = "",
    onRetryInventorySync: () -> Unit = {}
) {
    var inventoryOpen by rememberSaveable { mutableStateOf(false) }
    var inventoryDetail by rememberSaveable { mutableStateOf<Int?>(null) }
    Scaffold(containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .statusBarsPadding()
                .padding(horizontal = Spacing.lg)
                .padding(top = Spacing.md, bottom = Spacing.dockClearance),
            verticalArrangement = Arrangement.spacedBy(Spacing.lg)
        ) {
            HeroHeader(
                overline = todayLabel(),
                title = greeting(),
                subtitle = inventorySummary(ingredients.size, members.size)
            )

            DeviceStatusCard(
                backendConfigured = backendConfigured,
                deviceId = deviceId,
                status = deviceStatus,
                loading = deviceStatusLoading,
                error = deviceStatusError,
                onClick = if (backendConfigured && deviceId.isNotBlank()) onTransferClick
                else onSettingsClick
            )

            CompactInventoryEntry(
                ingredients = ingredients,
                loading = inventoryLoading,
                syncError = inventorySyncError,
                onClick = {
                    inventoryDetail = null
                    inventoryOpen = true
                }
            )

            FamilyPreviewCard(
                members = members,
                onEditMember = onEditMember,
                onManage = onFamilyClick
            )
        }
    }

    BackHandler(enabled = inventoryOpen && inventoryDetail != null) {
        inventoryDetail = null
    }

    if (inventoryOpen) {
        AppSheet(
            title = when (inventoryDetail) {
                1 -> "食材分类"
                2 -> "临期食材"
                else -> "冰箱食材"
            },
            onDismiss = {
                inventoryOpen = false
                inventoryDetail = null
            },
            modifier = Modifier.fillMaxHeight(0.9f),
            onBack = inventoryDetail?.let { { inventoryDetail = null } },
        ) {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(Spacing.lg),
                contentPadding = PaddingValues(bottom = Spacing.xxl)
            ) {
                if (inventorySyncError.isNotBlank()) {
                    item {
                        InventorySyncWarning(
                            reason = inventorySyncError,
                            onRetry = onRetryInventorySync
                        )
                    }
                }
                when (inventoryDetail) {
                    1 -> item {
                        CategorizedInventory(
                            ingredients = ingredients,
                            onAdd = onAddIngredient,
                            onDelete = onDeleteIngredient,
                            onEdit = onEditIngredient
                        )
                    }

                    2 -> item {
                        ExpiringInventory(
                            ingredients = ingredients,
                            onAdd = onAddIngredient,
                            onDelete = onDeleteIngredient,
                            onEdit = onEditIngredient
                        )
                    }

                    else -> {
                        if (ingredients.isNotEmpty()) {
                            item {
                                InventoryOverview(
                                    ingredients = ingredients,
                                    onCategoriesClick = { inventoryDetail = 1 },
                                    onExpiringClick = { inventoryDetail = 2 }
                                )
                            }
                        }
                        item {
                            FridgeInventoryCard(
                                ingredients = ingredients,
                                loading = inventoryLoading,
                                onAdd = onAddIngredient,
                                onDelete = onDeleteIngredient,
                                onEdit = onEditIngredient
                            )
                        }
                    }
                }
            }
        }
    }
}

// ─── 头部 ───

private fun greeting(now: LocalTime = LocalTime.now()): String = when (now.hour) {
    in 0..5 -> "夜深了"
    in 6..9 -> "早上好"
    in 10..13 -> "中午好"
    in 14..17 -> "下午好"
    else -> "晚上好"
}

private fun todayLabel(today: LocalDate = LocalDate.now()): String {
    val week = listOf("一", "二", "三", "四", "五", "六", "日")[today.dayOfWeek.value - 1]
    return "${today.monthValue} 月 ${today.dayOfMonth} 日 · 周$week"
}

private fun inventorySummary(ingredientCount: Int, memberCount: Int): String = when {
    ingredientCount == 0 -> "冰箱还是空的，先拍一张吧"
    memberCount == 0 -> "冰箱里有 $ingredientCount 种食材"
    else -> "冰箱里有 $ingredientCount 种食材，$memberCount 人份"
}

// ─── 设备状态 ───

/**
 * 设备状态卡。先给一句结论 + 色徽标，关键数值走大数字组，字段明细收在下面的小字里。
 *
 * 裸露「云端 v10 / 设备已应用 v8」这类字段用户无从判断好坏，故折成人话。
 */
@Composable
private fun DeviceStatusCard(
    backendConfigured: Boolean,
    deviceId: String,
    status: SyncStatusDto?,
    loading: Boolean,
    error: String?,
    onClick: () -> Unit
) {
    val verdict = deviceVerdict(backendConfigured, deviceId, status, loading, error)
    val battery = status?.deviceStatus?.batteryLevel
    AppCard(
        contentPadding = PaddingValues(Spacing.lg),
        tone = if (verdict.tone == BadgeTone.Danger) MaterialTheme.accents.danger.container else null,
        onClick = onClick
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "冰箱贴",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f)
                )
                StatusBadge(text = verdict.badge, tone = verdict.tone)
            }

            if (verdict.stats.isNotEmpty()) {
                StatRow(verdict.stats)
                if (battery != null) {
                    ProgressTrack(
                        fraction = battery / 100f,
                        color = batteryColor(battery)
                    )
                }
            }

            Text(
                verdict.headline,
                style = if (verdict.stats.isEmpty()) MaterialTheme.typography.headlineSmall
                else MaterialTheme.typography.bodyMedium
            )
            if (verdict.detail != null) Hint(verdict.detail)

            if (verdict.action != null) {
                AppButton(
                    text = verdict.action,
                    onClick = onClick,
                    modifier = Modifier.fillMaxWidth(),
                    variant = AppButtonVariant.Destructive,
                    size = AppButtonSize.Medium
                )
            }
            if (loading && status == null) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
                ) {
                    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                    Hint("正在读取设备状态")
                }
            }
        }
    }
}

/** 设备状态折成的一句结论：徽标 + 大数字组 + 说明 + 可选修复入口。 */
private data class DeviceVerdict(
    val badge: String,
    val tone: BadgeTone,
    val headline: String,
    val detail: String? = null,
    val stats: List<Stat> = emptyList(),
    val action: String? = null
)

/**
 * 把设备的若干字段折成一句结论。
 *
 * 顺序即「最先挡住链路的那一环」：没配后端 → 没绑设备 → 令牌没登记 → 读不到状态 →
 * 版本落后 → 一切正常。修完前一环才轮得到后一环，一次报三个问题会让人不知先做哪个。
 */
private fun deviceVerdict(
    backendConfigured: Boolean,
    deviceId: String,
    status: SyncStatusDto?,
    loading: Boolean,
    error: String?
): DeviceVerdict = when {
    !backendConfigured -> DeviceVerdict(
        "待配置", BadgeTone.Warning,
        "还没连上后端", "点这里填写后端地址与 Token"
    )

    deviceId.isBlank() -> DeviceVerdict(
        "待绑定", BadgeTone.Warning,
        "还没绑定冰箱贴", "点这里填写设备编号"
    )

    status?.deviceTokenRegistered == false -> DeviceVerdict(
        "需修复", BadgeTone.Danger,
        "设备令牌没登记到后端\n它取图会被拒绝",
        "不必重新配网，点一下就能修好",
        action = "一键登记"
    )

    loading && status == null -> DeviceVerdict("读取中", BadgeTone.Neutral, "正在读取设备状态")

    error != null -> DeviceVerdict("读取失败", BadgeTone.Danger, "读不到设备状态", error)

    status == null -> DeviceVerdict(
        "未知", BadgeTone.Neutral, "还没读到设备状态", "点这里进入设备页检查"
    )

    else -> {
        val applied = reportedAppliedVersion(status)
        val cloud = status.latestVersion
        val contact = wifiLinkStateOf(null, status.deviceLastContactMinutes)
        val lastSeen = when (contact) {
            WifiLinkState.Connected -> "刚刚"
            WifiLinkState.Disconnected -> "离线"
            is WifiLinkState.SeenAgo -> describeMinutesAgo(contact.minutes)
            WifiLinkState.Unknown -> "从未"
        }
        val battery = status.deviceStatus?.batteryLevel
        val stats = listOf(
            Stat("电量", battery?.toString() ?: "—", battery?.let { "%" }),
            Stat("最近在线", lastSeen),
            Stat("画面版本", if (cloud > 0) cloud.toString() else "—")
        )
        when {
            cloud <= 0 -> DeviceVerdict(
                "无画面", BadgeTone.Neutral,
                "还没给它发过画面", "发一份菜单或自定义画面试试", stats
            )

            applied == null -> DeviceVerdict(
                "状态未知", BadgeTone.Neutral,
                "还不知道屏幕上的画面版本", "设备下次联网后会自动上报", stats
            )

            applied >= cloud -> DeviceVerdict(
                "已是最新", BadgeTone.Success,
                "屏幕上就是最新的菜单", batteryHint(battery), stats
            )

            else -> DeviceVerdict(
                "待更新", BadgeTone.Info,
                "有新画面，设备还没取走",
                "它会在下次唤醒时更新", stats
            )
        }
    }
}

/** 优先使用固件状态快照；旧固件没有该字段时才回退最近事件。 */
internal fun reportedAppliedVersion(status: SyncStatusDto): Int? =
    status.deviceStatus?.appliedVersion
        ?: status.recentEvents.firstOrNull { it.event == "apply" }?.version

private fun batteryHint(battery: Int?): String? = when {
    battery == null -> null
    battery <= 20 -> "电量偏低，建议尽快充电"
    else -> null
}

@Composable
private fun batteryColor(level: Int): Color = when {
    level <= 20 -> MaterialTheme.accents.danger.solid
    level <= 40 -> MaterialTheme.accents.warning.solid
    else -> MaterialTheme.accents.success.solid
}

// ─── 库存概览图表 ───

@Composable
private fun CompactInventoryEntry(
    ingredients: List<Ingredient>,
    loading: Boolean,
    syncError: String,
    onClick: () -> Unit
) {
    val categoryCount = remember(ingredients) { ingredients.map { it.category }.distinct().size }
    val expiringCount = remember(ingredients) {
        ingredients.count { it.freshness == Freshness.EXPIRING }
    }
    AppCard(onClick = onClick, pressedScale = 0.99f) {
        Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(Spacing.xxxl)
                        .clip(CircleShape)
                        .background(MaterialTheme.accents.brand.container),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Outlined.Kitchen,
                        contentDescription = null,
                        modifier = Modifier.size(Spacing.xl),
                        tint = MaterialTheme.accents.brand.solid
                    )
                }
                Text(
                    "冰箱库存",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(start = Spacing.md).weight(1f)
                )
                Icon(Icons.Outlined.ChevronRight, contentDescription = "查看全部库存")
            }
            StatRow(
                listOf(
                    Stat("食材", ingredients.size.toString(), "种"),
                    Stat("分类", categoryCount.toString(), "类"),
                    Stat(
                        "临期",
                        expiringCount.toString(),
                        "项",
                        tint = if (expiringCount > 0) MaterialTheme.accents.warning.solid else null
                    )
                )
            )
            when {
                syncError.isNotBlank() -> Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
                ) {
                    Icon(
                        Icons.Outlined.CloudOff,
                        contentDescription = null,
                        modifier = Modifier.size(Spacing.lg),
                        tint = MaterialTheme.accents.warning.solid
                    )
                    Hint("库存同步失败，打开后可重试")
                }
                loading -> Hint("正在同步云端库存")
                else -> Hint("库存已同步")
            }
        }
    }
}

/** 库存概览：左卡看构成并进入分类详情，右卡看紧迫度并进入临期详情。 */
@Composable
private fun InventoryOverview(
    ingredients: List<Ingredient>,
    onCategoriesClick: () -> Unit,
    onExpiringClick: () -> Unit
) {
    val counts = remember(ingredients) {
        ingredients.groupingBy { it.category }.eachCount()
            .entries.sortedByDescending { it.value }
    }
    val slices = counts.map { entry ->
        DonutSlice(
            value = entry.value.toFloat(),
            color = categoryAccent(entry.key.ordinal).solid,
            label = entry.key.backendName
        )
    }
    val expiringSoon = remember(ingredients) { expiryBuckets(ingredients) }
    val urgentCount = ingredients.count { it.freshness == Freshness.EXPIRING }

    Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
        AppCard(
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(Spacing.lg),
            onClick = onCategoriesClick,
            pressedScale = 0.99f
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "食材分类",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.weight(1f)
                    )
                    Icon(
                        Icons.Outlined.ChevronRight,
                        contentDescription = "查看分类详情",
                        modifier = Modifier.size(Spacing.lg)
                    )
                }
                StatRow(
                    listOf(Stat("冰箱库存", ingredients.size.toString(), "种"))
                )
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    DonutChart(slices = slices) {
                        Text(
                            "${slices.size} 类",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }
            }
        }
        AppCard(
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(Spacing.lg),
            onClick = onExpiringClick,
            pressedScale = 0.99f
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "保质期",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.weight(1f)
                    )
                    Icon(
                        Icons.Outlined.ChevronRight,
                        contentDescription = "查看临期食材",
                        modifier = Modifier.size(Spacing.lg)
                    )
                }
                StatRow(
                    listOf(
                        Stat(
                            "需尽快吃掉", urgentCount.toString(), "项",
                            tint = if (urgentCount > 0) MaterialTheme.accents.warning.solid else null
                        )
                    )
                )
                BarChart(bars = expiryBars(expiringSoon), height = 56.dp)
            }
        }
    }
}

/**
 * 未来 5 天（含今天）每天到期的项数。
 *
 * 没填到期日的食材不计入：那不是「不会过期」，而是「不知道」，猜一个日期填进图里会误导。
 */
private fun expiryBuckets(
    ingredients: List<Ingredient>,
    today: LocalDate = LocalDate.now()
): List<Int> {
    val buckets = IntArray(5)
    ingredients.forEach { item ->
        val left = ShelfLife.daysLeft(item.expiryDate, today) ?: return@forEach
        if (left in 0..4) buckets[left]++
    }
    return buckets.toList()
}

@Composable
private fun expiryBars(buckets: List<Int>): List<Bar> {
    val labels = listOf("今天", "+1", "+2", "+3", "+4")
    return buckets.mapIndexed { index, count ->
        Bar(count.toFloat(), labels[index], highlight = index == 0 && count > 0)
    }
}

// ─── 冰箱食材 ───

/** 一行库存：合并后的展示条目 + 它在原列表里对应的全部下标。 */
private data class InventoryRow(
    val item: Ingredient,
    val sourceIndexes: List<Int>
)

@Composable
private fun CategorizedInventory(
    ingredients: List<Ingredient>,
    onAdd: () -> Unit,
    onDelete: (Int) -> Unit,
    onEdit: (Int) -> Unit
) {
    val groups = remember(ingredients) {
        ingredients.withIndex().groupBy { it.value.category }
            .entries.sortedByDescending { it.value.size }
    }
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.lg)) {
        SectionTitle("分类详情") {
            IconButton(onClick = onAdd) {
                Icon(Icons.Outlined.Add, contentDescription = "手动添加食材")
            }
        }
        groups.forEach { (category, items) ->
            CategoryInventoryGroup(
                category = category,
                ingredients = items,
                onDelete = onDelete,
                onEdit = onEdit
            )
        }
    }
}

@Composable
private fun CategoryInventoryGroup(
    category: IngredientCategory,
    ingredients: List<IndexedValue<Ingredient>>,
    onDelete: (Int) -> Unit,
    onEdit: (Int) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                category.displayLabel,
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f)
            )
            StatusBadge("${ingredients.size} 种", BadgeTone.Neutral)
        }
        InventoryTiles(
            ingredients = ingredients,
            onDelete = onDelete,
            onEdit = onEdit,
            showCount = false
        )
    }
}

@Composable
private fun ExpiringInventory(
    ingredients: List<Ingredient>,
    onAdd: () -> Unit,
    onDelete: (Int) -> Unit,
    onEdit: (Int) -> Unit
) {
    val expiring = remember(ingredients) {
        ingredients.withIndex().filter { it.value.freshness == Freshness.EXPIRING }
    }
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        SectionTitle("需尽快吃完") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (expiring.isNotEmpty()) {
                    StatusBadge("${expiring.size} 项", BadgeTone.Warning)
                }
                IconButton(onClick = onAdd) {
                    Icon(Icons.Outlined.Add, contentDescription = "手动添加食材")
                }
            }
        }
        if (expiring.isEmpty()) {
            AppCard {
                EmptyState(
                    icon = Icons.Outlined.Schedule,
                    title = "暂无临期食材",
                    description = "当前没有需要优先吃完的食材。"
                )
            }
        } else {
            InventoryTiles(
                ingredients = expiring,
                onDelete = onDelete,
                onEdit = onEdit
            )
        }
    }
}

@Composable
fun FridgeInventoryCard(
    ingredients: List<Ingredient>,
    loading: Boolean = false,
    onAdd: () -> Unit,
    onDelete: (Int) -> Unit,
    onEdit: (Int) -> Unit = {},
    modifier: Modifier = Modifier
) {
    val expiring = ingredients.count { it.freshness == Freshness.EXPIRING }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        SectionTitle("冰箱食材") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (expiring > 0) {
                    StatusBadge("$expiring 项临期", BadgeTone.Warning)
                }
                IconButton(onClick = onAdd) {
                    Icon(
                        Icons.Outlined.Add,
                        contentDescription = "手动添加食材",
                        modifier = Modifier.size(22.dp)
                    )
                }
            }
        }

        when {
            loading && ingredients.isEmpty() -> AppCard {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
                ) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Hint("正在同步云端库存")
                }
            }

            ingredients.isEmpty() -> AppCard {
                EmptyState(
                    icon = Icons.Outlined.Kitchen,
                    title = "冰箱还是空的",
                    description = "点右下角相机拍一张冰箱内部，AI 会认出里面的食材；也可以手动添加。",
                    actionText = "手动添加",
                    onAction = onAdd
                )
            }

            else -> InventoryTiles(
                ingredients = ingredients.withIndex().toList(),
                onDelete = onDelete,
                onEdit = onEdit
            )
        }
    }
}

/** 食材 2 列铺开。临期项整块染暖色，比行尾挂一个小图标更容易被扫到。 */
@Composable
private fun InventoryTiles(
    ingredients: List<IndexedValue<Ingredient>>,
    onDelete: (Int) -> Unit,
    onEdit: (Int) -> Unit,
    showCount: Boolean = true
) {
    val rows = remember(ingredients) { mergeInventoryRows(ingredients) }
    val warningTone = MaterialTheme.accents.warning.container
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        rows.chunked(2).forEach { pair ->
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
                pair.forEach { row ->
                    InventoryTile(
                        row = row,
                        tone = if (row.item.freshness == Freshness.EXPIRING) warningTone else null,
                        onDelete = onDelete,
                        onEdit = onEdit,
                        modifier = Modifier.weight(1f)
                    )
                }
                if (pair.size == 1) Box(Modifier.weight(1f))
            }
        }
        if (showCount) Hint("共 ${ingredients.size} 种")
    }
}

@Composable
private fun InventoryTile(
    row: InventoryRow,
    tone: Color?,
    onDelete: (Int) -> Unit,
    onEdit: (Int) -> Unit,
    modifier: Modifier = Modifier
) {
    val item = row.item
    val accent = categoryAccent(item.category.ordinal)
    FoodTile(
        name = item.name,
        quantity = item.quantity,
        modifier = modifier,
        iconRes = foodIconRes(item.name),
        iconTone = accent.container,
        fallbackDotColor = accent.solid,
        tone = tone,
        onClick = { onEdit(row.sourceIndexes.first()) },
        trailing = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (item.freshness == Freshness.EXPIRING) {
                    Icon(
                        Icons.Outlined.Schedule,
                        contentDescription = "临期",
                        modifier = Modifier.size(15.dp),
                        tint = MaterialTheme.accents.warning.solid
                    )
                }
                IconButton(
                    // 删该组代表的全部条目；倒序删避免 onDelete 按下标操作时后续下标左移。
                    onClick = { row.sourceIndexes.sortedDescending().forEach(onDelete) },
                    modifier = Modifier.size(26.dp)
                ) {
                    Icon(
                        Icons.Outlined.Close,
                        contentDescription = "删除 ${item.name}",
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.outline
                    )
                }
            }
        }
    )
}


/**
 * 把同名食材合并成一行显示，并记住它对应原列表里的**所有**下标（删除要能删掉整组）。
 *
 * 分类取第一条的已保存值，不在这里重新分类：用户或后端定过的分类不该被本地启发式覆盖。
 */
private fun mergeInventoryRows(
    ingredients: List<IndexedValue<Ingredient>>
): List<InventoryRow> =
    ingredients
        .groupBy { it.value.name }
        .map { (_, entries) ->
            val indexes = entries.map { it.index }
            val items = entries.map { it.value }
            val category = items.first().category
            if (items.size == 1) {
                return@map InventoryRow(items.first().copy(category = category), indexes)
            }
            val totalWeight = items.mapNotNull { it.weightGrams }.takeIf { it.isNotEmpty() }?.sum()
            val byUnit = items.groupBy { it.quantityUnit }
            val merged = if (byUnit.size == 1) {
                val total = items.sumOf { it.quantityAmount }
                val unit = items.first().quantityUnit
                val text = listOfNotNull(
                    total.takeIf { it > 0 }?.let {
                        if (unit == "ml") it.toVolumeDisplay() else "${it.toInt()}$unit"
                    },
                    totalWeight?.let { "${it.toInt()}g" }
                ).joinToString(" · ")
                items.first().copy(
                    quantity = text, quantityAmount = total,
                    weightGrams = totalWeight, category = category
                )
            } else {
                // 单位不同不累加（「2个」与「500g」不是一个量）：显示主要那个并标注另有。
                val main = items.maxByOrNull { it.quantityAmount } ?: items.first()
                val others = items.filter { it.quantityUnit != main.quantityUnit }
                val extra = others.sumOf { it.quantityAmount }
                val extraUnit = others.firstOrNull()?.quantityUnit ?: ""
                val text = if (extra > 0) "${main.quantity} + ${extra.toInt()}$extraUnit"
                else main.quantity
                main.copy(quantity = text, category = category)
            }
            InventoryRow(merged, indexes)
        }

// ─── 家庭成员 ───

/**
 * 家庭成员卡：标题 + 管理入口 + 头像横排。
 *
 * 成员为空时也显示，只是把头像换成一句「为什么要填」—— 这是进入家庭页的唯一入口，
 * 空了就藏起来会让人找不到在哪添加。
 */
@Composable
private fun FamilyPreviewCard(
    members: List<FamilyMember>,
    onEditMember: (FamilyMember) -> Unit,
    onManage: () -> Unit
) {
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "家庭成员",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f)
                )
                AppButton(
                    text = if (members.isEmpty()) "添加" else "管理",
                    onClick = onManage,
                    variant = AppButtonVariant.Ghost
                )
            }
            if (members.isEmpty()) {
                Hint("填上家人的忌口与过敏，配菜时会自动避开。这是 AI 唯一能知道你家口味的途径。")
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(Spacing.xl)
                ) {
                    members.forEachIndexed { index, member ->
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
                            modifier = Modifier.pressableCard(
                                { onEditMember(member) },
                                scaleDown = 0.94f
                            )
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(48.dp)
                                    .clip(CircleShape)
                                    .background(categoryAccent(index).container),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(member.avatar, fontSize = 24.sp, textAlign = TextAlign.Center)
                            }
                            Text(member.name, style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
            }
        }
    }
}

/**
 * 「库存还没同步到后端」的常驻警示。
 *
 * 常驻而非 Toast：这条链路失败是静默的、后果又是间接的（后端库存旧 → 餐前推荐按旧食材配菜），
 * 一次性提示过后用户无从把两件事联系起来。重试只补「本地有、后端没有」的方向，幂等。
 */
@Composable
private fun InventorySyncWarning(reason: String, onRetry: () -> Unit) {
    AppCard(
        tone = MaterialTheme.accents.danger.container,
        contentColor = MaterialTheme.accents.danger.onContainer
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
            ) {
                Icon(
                    Icons.Outlined.CloudOff,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp),
                    tint = MaterialTheme.accents.danger.solid
                )
                Text("食材没同步到后端", style = MaterialTheme.typography.titleMedium)
            }
            Text(
                "配菜用的是后端那份库存，所以现在推荐的菜可能对不上冰箱里的东西。",
                style = MaterialTheme.typography.bodySmall
            )
            Text("上次失败：$reason", style = MaterialTheme.typography.labelSmall)
            AppButton(
                text = "重新同步",
                onClick = onRetry,
                modifier = Modifier.fillMaxWidth(),
                variant = AppButtonVariant.Destructive,
                size = AppButtonSize.Medium
            )
        }
    }
}
