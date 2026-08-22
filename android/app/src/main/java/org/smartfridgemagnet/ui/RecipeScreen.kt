package org.smartfridgemagnet.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.LocalDining
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.ShoppingCart
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.data.FamilyMember
import org.smartfridgemagnet.network.PlanDishDto
import org.smartfridgemagnet.ui.components.AppButton
import org.smartfridgemagnet.ui.components.AppButtonSize
import org.smartfridgemagnet.ui.components.AppButtonVariant
import org.smartfridgemagnet.ui.components.AppCard
import org.smartfridgemagnet.ui.components.AppSheet
import org.smartfridgemagnet.ui.components.BadgeTone
import org.smartfridgemagnet.ui.components.EmptyState
import org.smartfridgemagnet.ui.components.HeroHeader
import org.smartfridgemagnet.ui.components.Hint
import org.smartfridgemagnet.ui.components.IconBadge
import org.smartfridgemagnet.ui.components.SectionTitle
import org.smartfridgemagnet.ui.components.Stat
import org.smartfridgemagnet.ui.components.ThinkingCard
import org.smartfridgemagnet.ui.components.StatRow
import org.smartfridgemagnet.ui.components.StatusBadge
import org.smartfridgemagnet.ui.theme.AccentColor
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.accents
import org.smartfridgemagnet.ui.theme.categoryAccent

/**
 * 方案的来源与时效信息。**null = 本地模型现场生成的**（没有这些元信息）。
 *
 * 菜谱页展示的是后端**早就生成好**的方案（餐前定时任务的产物），不是打开页面时现场生成的。用户看到
 * 菜单的第一个问题是「这是今天的吗」，不给时效信息，隔夜菜单与刚出炉的长得一样。
 *
 * @param ageMinutes 后端给的「生成至今多少分钟」；null 表示后端也算不出来
 * @param createdAtRaw 兜底展示用的原始时间串。后端这个字段是 **UTC** 的 `Y-m-d H:M:S`
 * @param stale 后端按 12 小时判定的「可能不是今天的」
 * @param missingMealLabels 哪几餐还没有方案，如 `["早餐"]`
 */
data class RecipePlanMeta(
    val ageMinutes: Int? = null,
    val createdAtRaw: String? = null,
    val stale: Boolean = false,
    val missingMealLabels: List<String> = emptyList(),
    /**
     * 三餐合起来还需要买的食材（已去重）。**null = 后端没算过**，空列表 = 算过且不缺。
     *
     * 两者必须分开：都当空列表处理的话，旧版本后端会让界面打出一句「三餐都能用现有食材做」，
     * 而那是编的，且正好出现在用户准备出门买菜之前。
     */
    val shoppingList: List<String>? = null
)

/** 一餐的展示信息：标题、菜品、配色。 */
private data class MealBlock(
    val key: String,
    val label: String,
    val dishes: List<String>,
    val accent: AccentColor
)

private data class DishSelection(val name: String, val detail: PlanDishDto?)

@Composable
fun RecipeScreen(
    recipe: RecipeResult?,
    members: List<FamilyMember>,
    isLoading: Boolean,
    /** 生成失败的原因。给了就如实展示，区分「模型没返回」「网络不通」「解析不了」。 */
    errorText: String? = null,
    /** 方案时效信息；null 表示这份菜谱不是从后端读来的。 */
    meta: RecipePlanMeta? = null,
    /** 正在重新生成（约 20–30 秒）。期间整页显示进度、按钮禁用。 */
    isRegenerating: Boolean = false,
    /** 只展示能被客户端真实观测到的阶段，不伪造百分比。 */
    progressText: String? = null,
    /** 流式生成时模型思考的累积文本；空串表示没有可展示的思考。 */
    thinkingText: String = "",
    onRegenerate: (() -> Unit)? = null,
    onOpenChat: (() -> Unit)? = null,
    onSendToDisplay: () -> Unit,
    onShare: () -> Unit
) {
    // 做法直接取后端随菜单下发的那一份：不再另打模型，因此是即时的，且与下发到电子纸上的
    // 内容必然一致。现场再生成一份，用户手里的步骤就可能与屏幕上那份不是同一道菜的做法。
    var selectedDish by remember { mutableStateOf<DishSelection?>(null) }

    Scaffold(containerColor = MaterialTheme.colorScheme.background) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                isRegenerating -> RecipeGenerating(
                    stage = progressText,
                    thinking = thinkingText
                )

                isLoading -> RecipeBusy(title = "正在读取今日菜谱…", hint = null)

                recipe != null -> RecipeContent(
                    recipe = recipe,
                    members = members,
                    meta = meta,
                    errorText = errorText,
                    isRegenerating = isRegenerating,
                    onDishClick = { selectedDish = it },
                    onRegenerate = onRegenerate,
                    onOpenChat = onOpenChat,
                    onSendToDisplay = onSendToDisplay,
                    onShare = onShare
                )

                else -> RecipeBlank(
                    errorText = errorText,
                    isRegenerating = isRegenerating,
                    onRegenerate = onRegenerate,
                    onOpenChat = onOpenChat
                )
            }
        }
    }

    val dish = selectedDish
    if (dish != null) {
        AppSheet(title = dish.name, onDismiss = { selectedDish = null }) {
            val detail = dish.detail
            if (detail != null) {
                DishDetailContent(detail)
            } else {
                // 菜单里没有这道菜的做法（例如手工添加的菜名）。如实说明，不现场编一份 ——
                // 那份做法与屏幕上下发的内容并不对应。
                Text(
                    "这道菜不在当前下发的菜单里，没有随菜单带回做法。可以在对话页让 AI 把它" +
                        "加进菜单，重新生成后就能看到做法。",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(Spacing.md)
                )
            }
        }
    }
}

// ─── 三种整页状态 ───

/**
 * 生成中。有思考文本时展示流式思考卡，没有时退回一句阶段文案。
 *
 * 两条路都要留：`/api/recommend/stream` 在纯数据库意图或模型不吐 reasoning 时确实
 * 一个 reasoning 事件都没有，那时摆一张空的思考卡比一句话更糟。
 */
@Composable
private fun RecipeGenerating(stage: String?, thinking: String) {
    if (thinking.isBlank()) {
        RecipeBusy(
            title = stage ?: "AI 正在配菜…",
            hint = "通常需要几十秒，可以先去别的页面，完成后回来就能看到"
        )
        return
    }
    Column(
        Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .padding(horizontal = Spacing.lg)
            .padding(top = Spacing.md, bottom = Spacing.dockClearance),
        verticalArrangement = Arrangement.spacedBy(Spacing.md)
    ) {
        Text(
            "AI 正在配菜",
            style = MaterialTheme.typography.headlineMedium
        )
        Hint(
            "可以先去别的页面，完成后回来就能看到"
        )
        ThinkingCard(
            stage = stage,
            thinking = thinking,
            modifier = Modifier.weight(1f)
        )
    }
}

@Composable
private fun RecipeBusy(title: String, hint: String?) {
    Box(Modifier.fillMaxSize().padding(Spacing.xxxl), contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(Spacing.md)
        ) {
            CircularProgressIndicator(color = MaterialTheme.accents.brand.solid)
            Text(title, style = MaterialTheme.typography.titleMedium)
            if (hint != null) Hint(hint)
        }
    }
}

@Composable
private fun RecipeBlank(
    errorText: String?,
    isRegenerating: Boolean,
    onRegenerate: (() -> Unit)?,
    onOpenChat: (() -> Unit)?
) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .statusBarsPadding()
            .padding(horizontal = Spacing.lg)
            .padding(top = Spacing.md, bottom = Spacing.dockClearance),
        verticalArrangement = Arrangement.spacedBy(Spacing.lg)
    ) {
        HeroHeader(
            overline = "今日菜谱",
            // 标题区分「取不到」与「还没生成」：前者要重试，后者要生成，做的事不一样。
            title = if (errorText != null) "菜谱取不到" else "还没有今日菜谱",
            subtitle = errorText
                ?: "平时由餐前的自动任务提前配好，打开这里直接看结果"
        )
        AppCard {
            EmptyState(
                icon = Icons.Outlined.LocalDining,
                title = if (errorText != null) "读取失败" else "库里还没有今天的方案",
                description = if (errorText != null) "检查后端地址与网络后重试。"
                else "可以现在让 AI 现场配一份，约 20–30 秒。",
                actionText = if (onRegenerate != null) "立即生成" else null,
                onAction = onRegenerate?.takeIf { !isRegenerating }
            )
        }
        if (onOpenChat != null) {
            AppButton(
                text = "和 AI 聊聊，说说你想吃什么",
                onClick = onOpenChat,
                modifier = Modifier.fillMaxWidth(),
                variant = AppButtonVariant.Secondary,
                leadingIcon = Icons.AutoMirrored.Outlined.Chat
            )
        }
    }
}

// ─── 正常内容 ───

@Composable
private fun RecipeContent(
    recipe: RecipeResult,
    members: List<FamilyMember>,
    meta: RecipePlanMeta?,
    errorText: String?,
    isRegenerating: Boolean,
    onDishClick: (DishSelection) -> Unit,
    onRegenerate: (() -> Unit)?,
    onOpenChat: (() -> Unit)?,
    onSendToDisplay: () -> Unit,
    onShare: () -> Unit
) {
    val meals = listOf(
        MealBlock("breakfast", "早餐", recipe.breakfast, MaterialTheme.accents.warning),
        MealBlock("lunch", "午餐", recipe.lunch, MaterialTheme.accents.brand),
        MealBlock("dinner", "晚餐", recipe.dinner, MaterialTheme.accents.info)
    )
    val dishCount = meals.sumOf { it.dishes.size }
    val shopping = meta?.shoppingList

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .statusBarsPadding()
            .padding(horizontal = Spacing.lg)
            .padding(top = Spacing.md, bottom = Spacing.dockClearance),
        verticalArrangement = Arrangement.spacedBy(Spacing.lg)
    ) {
        HeroHeader(
            overline = "今日菜谱",
            title = "${meals.count { it.dishes.isNotEmpty() }} 餐已配好",
            subtitle = meta?.let { formatPlanAge(it.ageMinutes, it.createdAtRaw) },
            action = {
                IconButton(onClick = onShare) {
                    Icon(
                        Icons.Outlined.Share,
                        contentDescription = "分享菜谱",
                        modifier = Modifier.size(20.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        )

        // 已有内容时的失败必须就地说出来：重新生成失败时 recipe 还是上一份，页面会原样
        // 回到列表，没有提示的话用户只会再点一次。
        if (errorText != null) {
            AppCard(
                tone = MaterialTheme.accents.danger.container,
                contentColor = MaterialTheme.accents.danger.onContainer
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
                    Text("重新生成失败，下面仍是上一份菜单", style = MaterialTheme.typography.titleSmall)
                    Text(errorText, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
                StatRow(
                    listOf(
                        Stat("菜品", dishCount.toString(), "道"),
                        Stat("家庭成员", members.size.toString(), "人"),
                        Stat(
                            "需采购",
                            shopping?.size?.toString() ?: "—",
                            shopping?.let { "样" },
                            tint = if (!shopping.isNullOrEmpty()) MaterialTheme.accents.warning.solid
                            else null
                        )
                    )
                )
                if (meta?.stale == true) {
                    StatusBadge("可能不是今天的", BadgeTone.Warning)
                }
                if (meta != null && meta.missingMealLabels.isNotEmpty()) {
                    Hint("${meta.missingMealLabels.joinToString("、")}还没有生成")
                }
                // 忌口是在生成时就避开的，体现在每道菜的 skip_ingredients 上，所以后端方案里
                // memberAdjustments 恒为 null。照原样渲染会画出「每人一张空卡片」。
                val hasAdjustments = members.any {
                    recipe.memberAdjustments?.get(it.name)?.isNotEmpty() == true
                }
                if (meta != null && !hasAdjustments && members.isNotEmpty()) {
                    Hint("配菜时已按家庭成员的忌口避开相应食材")
                }
            }
        }

        if (shopping != null) ShoppingCard(shopping)

        meals.filter { it.dishes.isNotEmpty() }.forEach { meal ->
            MealSectionCard(meal, recipe.dishDetails, onDishClick)
        }

        if (recipe.memberAdjustments?.values?.any { it.isNotEmpty() } == true) {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
                SectionTitle("按成员的建议")
                members.forEachIndexed { index, member ->
                    val tips = recipe.memberAdjustments[member.name] ?: emptyList()
                    if (tips.isNotEmpty()) MemberAdjustmentCard(member, tips, index)
                }
            }
        }

        RecipeActions(
            isRegenerating = isRegenerating,
            onRegenerate = onRegenerate,
            onOpenChat = onOpenChat,
            onSendToDisplay = onSendToDisplay
        )
    }
}

/** 采购清单。空列表说「都够」，null 时整块不显示 —— 那是后端没算过，不能替它下结论。 */
@Composable
private fun ShoppingCard(shopping: List<String>) {
    val enough = shopping.isEmpty()
    val tone = if (enough) MaterialTheme.accents.brand else MaterialTheme.accents.warning
    AppCard(tone = tone.container, contentColor = tone.onContainer) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Spacing.md)
        ) {
            IconBadge(
                icon = Icons.Outlined.ShoppingCart,
                size = 38.dp,
                container = MaterialTheme.colorScheme.surface,
                tint = tone.solid
            )
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    if (enough) "三餐都能用冰箱现有食材做"
                    else "还需采购 ${shopping.size} 样",
                    style = MaterialTheme.typography.titleSmall
                )
                Text(
                    if (enough) "不用出门买菜" else shopping.joinToString("、"),
                    style = MaterialTheme.typography.bodySmall
                )
                if (!enough) {
                    Text(
                        "基础调料（油盐酱醋等）不计入",
                        style = MaterialTheme.typography.labelSmall
                    )
                }
            }
        }
    }
}

/** 一餐一张卡：彩色图标底板区分餐次，每道菜一行、点开看做法。 */
@Composable
private fun MealSectionCard(
    meal: MealBlock,
    details: Map<String, Map<String, PlanDishDto>>,
    onDishClick: (DishSelection) -> Unit
) {
    val mealDetails = details[meal.key].orEmpty()
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        SectionTitle(meal.label) {
            StatusBadge("${meal.dishes.size} 道", BadgeTone.Neutral)
        }
        AppCard(contentPadding = PaddingValues(vertical = Spacing.xs)) {
            Column {
                meal.dishes.forEachIndexed { index, dish ->
                    if (index > 0) {
                        HorizontalDivider(
                            modifier = Modifier.padding(start = 66.dp),
                            color = MaterialTheme.colorScheme.outlineVariant
                        )
                    }
                    DishRow(
                        name = dish,
                        detail = mealDetails[dish],
                        accent = meal.accent,
                        onClick = { onDishClick(DishSelection(dish, mealDetails[dish])) }
                    )
                }
            }
        }
    }
}

@Composable
private fun DishRow(
    name: String,
    detail: PlanDishDto?,
    accent: AccentColor,
    onClick: () -> Unit
) {
    val subtitle = listOfNotNull(
        detail?.cookingTime?.takeIf { it.isNotBlank() },
        detail?.difficulty?.takeIf { it.isNotBlank() },
        detail?.nutritionHighlight?.takeIf { it.isNotBlank() }
    ).joinToString(" · ").ifBlank { "点开看做法" }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = Spacing.lg, vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.md)
    ) {
        IconBadge(
            icon = Icons.Outlined.LocalDining,
            size = 38.dp,
            container = accent.container,
            tint = accent.solid
        )
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(name, style = MaterialTheme.typography.titleSmall)
            Text(
                subtitle,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        if (detail?.skipIngredients?.isNotEmpty() == true) {
            StatusBadge("已避开", BadgeTone.Info)
        }
        Icon(
            Icons.Outlined.ChevronRight,
            contentDescription = null,
            modifier = Modifier.size(18.dp),
            tint = MaterialTheme.colorScheme.outline
        )
    }
}

@Composable
private fun MemberAdjustmentCard(member: FamilyMember, tips: List<String>, index: Int) {
    val accent = categoryAccent(index)
    AppCard {
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Box(
                Modifier.size(38.dp),
                contentAlignment = Alignment.Center
            ) {
                IconBadge(
                    icon = Icons.Outlined.LocalDining,
                    size = 38.dp,
                    container = accent.container,
                    tint = accent.solid
                )
                Text(member.avatar, style = MaterialTheme.typography.titleMedium)
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
                Text(member.name, style = MaterialTheme.typography.titleSmall)
                tips.forEach { tip -> Hint(tip) }
            }
        }
    }
}

/**
 * 底部操作区。
 *
 * 「发送到电子纸」是主操作，独占墨色按钮；「重新生成」与「和 AI 聊聊」都会触发一次
 * 二十多秒的模型调用，做成次级按钮，并写清两者区别 —— 不说清就看起来是同一个功能。
 */
@Composable
private fun RecipeActions(
    isRegenerating: Boolean,
    onRegenerate: (() -> Unit)?,
    onOpenChat: (() -> Unit)?,
    onSendToDisplay: () -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        AppButton(
            text = "发送到电子纸",
            onClick = onSendToDisplay,
            modifier = Modifier.fillMaxWidth(),
            leadingIcon = Icons.AutoMirrored.Outlined.Send
        )
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
            if (onRegenerate != null) {
                AppButton(
                    text = "重新生成",
                    onClick = onRegenerate,
                    modifier = Modifier.weight(1f),
                    variant = AppButtonVariant.Secondary,
                    size = AppButtonSize.Medium,
                    enabled = !isRegenerating,
                    leadingIcon = Icons.Outlined.Refresh
                )
            }
            if (onOpenChat != null) {
                AppButton(
                    text = "和 AI 聊聊",
                    onClick = onOpenChat,
                    modifier = Modifier.weight(1f),
                    variant = AppButtonVariant.Secondary,
                    size = AppButtonSize.Medium,
                    enabled = !isRegenerating,
                    leadingIcon = Icons.AutoMirrored.Outlined.Chat
                )
            }
        }
        if (onRegenerate != null && onOpenChat != null) {
            Hint("「重新生成」是 AI 自己再配一次；「和 AI 聊聊」是带上你的要求重配。")
        }
    }
}

/**
 * 把方案时效说成人话。
 *
 * **刻意不解析 createdAtRaw**：后端那个字段是 UTC 的 `Y-m-d H:M:S`，而同一份响应里其它
 * 时间字段是带微秒的本地 ISO —— 单一 pattern 必然在其中一种上抛异常，而这个值只是给人看的。
 * 所以优先用后端算好的 ageMinutes，只有它为 null 时才把原始串原样展示并标明是 UTC。
 */
private fun formatPlanAge(ageMinutes: Int?, createdAtRaw: String?): String = when {
    ageMinutes == null -> createdAtRaw?.takeIf { it.isNotBlank() }
        ?.let { "生成于 $it（UTC）" } ?: "生成时间未知"
    ageMinutes < 1 -> "刚刚生成"
    ageMinutes < 60 -> "$ageMinutes 分钟前生成"
    ageMinutes < 60 * 24 -> "${ageMinutes / 60} 小时前生成"
    else -> "${ageMinutes / (60 * 24)} 天前生成"
}

/** 展示一道菜的做法。数据来自后端随菜单下发的 [PlanDishDto]，与电子纸上那份同源。 */
@Composable
fun DishDetailContent(dish: PlanDishDto) {
    val meta = listOfNotNull(
        dish.cookingTime?.takeIf { it.isNotBlank() },
        dish.difficulty?.takeIf { it.isNotBlank() },
        dish.nutritionHighlight?.takeIf { it.isNotBlank() }
    )
    LazyColumn(
        modifier = Modifier.heightIn(max = 460.dp).padding(horizontal = Spacing.md),
        verticalArrangement = Arrangement.spacedBy(Spacing.sm)
    ) {
        if (meta.isNotEmpty()) {
            item { Hint(meta.joinToString("  ·  ")) }
        }

        if (dish.ingredients.isNotEmpty()) {
            item { SectionTitle("食材") }
            items(dish.ingredients.filter { it.name.isNotBlank() }) { ing ->
                Text(
                    "${ing.name} ${ing.amount}".trimEnd(),
                    style = MaterialTheme.typography.bodyMedium
                )
            }
        }

        if (dish.steps.isNotEmpty()) {
            item { SectionTitle("做法") }
            items(dish.steps.withIndex().toList()) { (index, step) ->
                Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm)) {
                    Text(
                        "${index + 1}",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.accents.brand.solid
                    )
                    Text(step, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }

        // 忌口导致要去掉的配料：这是这道菜与原版的差异，必须显示，否则用户照着做会放回去。
        if (dish.skipIngredients.isNotEmpty()) {
            item {
                val reason = dish.skipReason?.takeIf { it.isNotBlank() }?.let { "（$it）" }.orEmpty()
                AppCard(
                    tone = MaterialTheme.accents.info.container,
                    contentColor = MaterialTheme.accents.info.onContainer
                ) {
                    Text(
                        "已去掉：${dish.skipIngredients.joinToString("、")}$reason",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }
        }
    }
}

data class RecipeResult(
    val breakfast: List<String>,
    val lunch: List<String>,
    val dinner: List<String>,
    val memberAdjustments: Map<String, List<String>>?,
    /**
     * 餐次 → 菜名 → 后端随菜单下发的做法。做法页直接读这里，不再另打一次模型。
     *
     * 这样「用户看到的步骤」与「电子纸上那份菜单」必然同源。现场再生成一份做法，两者就
     * 可能对不上，而这种不一致没有任何一处会报错。
     */
    val dishDetails: Map<String, Map<String, PlanDishDto>> = emptyMap()
)
