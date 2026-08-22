package org.smartfridgemagnet.ui

import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.Search
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.input.KeyboardType
import org.smartfridgemagnet.data.Ingredient
import org.smartfridgemagnet.data.AmountMode
import org.smartfridgemagnet.data.FoodIconItem
import org.smartfridgemagnet.data.Freshness
import org.smartfridgemagnet.data.IngredientCategory
import org.smartfridgemagnet.data.ShelfLife
import org.smartfridgemagnet.data.classifyIngredient
import org.smartfridgemagnet.data.foodIconItems
import org.smartfridgemagnet.data.withEditedAmount
import org.smartfridgemagnet.ui.components.SegmentedControl
import org.smartfridgemagnet.ui.theme.Spacing
import java.time.LocalDate

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IngredientScreen(
    ingredients: List<Ingredient>,
    existingInventory: List<Ingredient> = emptyList(),
    onBack: () -> Unit,
    onConfirm: (List<Ingredient>) -> Unit,
    onRecommend: (List<Ingredient>, List<Ingredient>?) -> Unit,
    onDeleteIngredient: (Int) -> Unit,
    onAddIngredient: (Ingredient) -> Unit,
    onUpdateIngredient: (Int, Ingredient) -> Unit
) {
    // 用 SnapshotStateList 而不是 mutableStateOf(MutableList)：后者对 add/removeAt **不会**触发
    // 重组（state 持有的引用没变）。要在页内改某一项（编辑保质期）就必须让列表自身可观察。
    //
    // key 必须是 `ingredients`：上层列表变了就重新播种。少了 key，「扫码 → 确认页 → 返回相机页 →
    // 再扫一个」的第二样商品不会出现在这里（页面还在组合里，remember 不重算）。前提是页内三种改动
    // （删/增/改）都会镜像回上层，否则重新播种就是丢用户的编辑。
    val ingredientList = remember(ingredients) {
        mutableStateListOf<Ingredient>().apply { addAll(ingredients) }
    }
    var showAddDialog by remember { mutableStateOf(false) }
    var showMergeDialog by remember { mutableStateOf(false) }
    var confirmSaved by remember(ingredients) { mutableStateOf(false) }
    /** 正在编辑保质期的条目下标；null = 没有在编辑。 */
    var editingShelfLife by remember { mutableStateOf<Int?>(null) }
    var editingAmount by remember { mutableStateOf<Int?>(null) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("确认食材") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    IconButton(onClick = { showAddDialog = true }, enabled = !confirmSaved) {
                        Icon(Icons.Default.Add, contentDescription = "添加")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
        ) {
            Text(
                text = "请确认 ${ingredientList.size} 样食材：",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(modifier = Modifier.height(16.dp))

            LazyColumn(
                modifier = Modifier.weight(1f)
            ) {
                if (ingredientList.isEmpty()) {
                    item {
                        Text(
                            "还没有待确认的食材，请用右上角添加。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
                            textAlign = TextAlign.Center
                        )
                    }
                }
                items(ingredientList.indices.toList()) { index ->
                    val ingredient = ingredientList[index]
                    IngredientCard(
                        ingredient = ingredient,
                        editable = !confirmSaved,
                        onDelete = {
                            ingredientList.removeAt(index)
                            onDeleteIngredient(index)
                        },
                        onEditShelfLife = { editingShelfLife = index },
                        onEditAmount = { editingAmount = index }
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                }
            }

            // 两个按钮：确认食材 / AI推荐菜谱
            Spacer(modifier = Modifier.height(12.dp))

            // 确认食材按钮
            OutlinedButton(
                onClick = {
                    // 交出**快照**而不是这个活的 SnapshotStateList：上层会把它存进
                    // recognizedIngredients 并交给协程做 Gson 序列化，此时页内再删一项
                    // 就是在序列化过程中改同一个列表（ConcurrentModificationException，
                    // 而且只在"点完确认马上删一项"这种时序下出现，极难复现）。
                    onConfirm(ingredientList.toList())
                    confirmSaved = true
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = ingredientList.isNotEmpty() && !confirmSaved
            ) {
                Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(6.dp))
                Text(if (confirmSaved) "已存入冰箱 ✓" else "确认食材，放入冰箱")
            }

            Spacer(modifier = Modifier.height(8.dp))

            // AI推荐菜谱按钮
            Button(
                onClick = {
                    if (ingredientList.size < 3 && existingInventory.isNotEmpty()) {
                        // 少于3样且冰箱有食材，弹窗询问是否合并
                        showMergeDialog = true
                    } else {
                        // 3样及以上，直接推荐（同样交快照，理由见「确认食材」）
                        onRecommend(ingredientList.toList(), null)
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = ingredientList.isNotEmpty()
            ) {
                Text("AI 推荐菜谱")
            }
        }
    }

    editingAmount?.let { index ->
        ingredientList.getOrNull(index)?.let { target ->
            EditIngredientDialog(
                ingredient = target,
                onDismiss = { editingAmount = null },
                onSave = { updated ->
                    ingredientList[index] = updated
                    onUpdateIngredient(index, updated)
                    editingAmount = null
                },
                onDelete = {
                    ingredientList.removeAt(index)
                    onDeleteIngredient(index)
                    editingAmount = null
                }
            )
        } ?: run { editingAmount = null }
    }

    // 保质期编辑弹窗
    editingShelfLife?.let { index ->
        // 下标可能已经失效（编辑期间那一项被删掉）。此时直接关掉弹窗，
        // 而不是按旧下标去改另一样食材 —— 那会把保质期写到错误的条目上。
        val target = ingredientList.getOrNull(index)
        if (target == null) {
            editingShelfLife = null
        } else {
            ShelfLifeDialog(
                ingredient = target,
                onDismiss = { editingShelfLife = null },
                onConfirm = { updated ->
                    ingredientList[index] = updated
                    // 同步回上层，与删除/新增一致。只写页内列表的话，用户填的保质期在
                    // 返回相机页再进来时会被上层那份没有日期的数据重新播种掉，而且是静默的。
                    onUpdateIngredient(index, updated)
                    editingShelfLife = null
                }
            )
        }
    }

    // 添加食材弹窗
    if (showAddDialog) {
        AddIngredientDialog(
            onDismiss = { showAddDialog = false },
            onAdd = { added ->
                added.forEach {
                    ingredientList.add(it)
                    onAddIngredient(it)
                }
                showAddDialog = false
            }
        )
    }

    // 少于3样时，询问是否合并冰箱食材
    if (showMergeDialog) {
        AlertDialog(
            onDismissRequest = { showMergeDialog = false },
            title = { Text("食材较少") },
            text = {
                Text("识别到的食材只有 ${ingredientList.size} 样，菜谱可能比较单调。\n\n冰箱里还有 ${existingInventory.size} 样食材，是否一起参与推荐？")
            },
            confirmButton = {
                TextButton(onClick = {
                    showMergeDialog = false
                    onRecommend(ingredientList.toList(), existingInventory)
                }) { Text("合并推荐") }
            },
            dismissButton = {
                TextButton(onClick = {
                    showMergeDialog = false
                    onRecommend(ingredientList.toList(), null)
                }) { Text("仅用识别的") }
            }
        )
    }
}

@Composable
fun IngredientCard(
    ingredient: Ingredient,
    onDelete: () -> Unit,
    editable: Boolean = true,
    onEditShelfLife: (() -> Unit)? = null,
    onEditAmount: (() -> Unit)? = null
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.lg),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = ingredient.name,
                        style = MaterialTheme.typography.titleMedium
                    )
                    Text(
                        text = ingredient.category.backendName,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    if (ingredient.recognitionDerived || ingredient.recognitionConfidence != null) {
                        Text(
                            text = ingredient.recognitionConfidence?.let { "识别可信度：$it" }
                                ?: "识别所得，请核对",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.tertiary
                        )
                    }
                }
                FreshnessBadge(freshness = ingredient.freshness)
                IconButton(onClick = onDelete, enabled = editable) {
                    Icon(
                        Icons.Default.Delete,
                        contentDescription = "删除",
                        tint = MaterialTheme.colorScheme.error
                    )
                }
            }

            if (onEditAmount != null) {
                FilledTonalButton(
                    onClick = onEditAmount,
                    enabled = editable,
                    modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.medium
                ) {
                    Icon(
                        Icons.Default.Edit,
                        contentDescription = null,
                        modifier = Modifier.size(Spacing.xl)
                    )
                    Spacer(Modifier.width(Spacing.sm))
                    Text("数量与单位：${ingredient.quantity}")
                }
            }

            // 保质期这一行只在可编辑时出现（即识别确认页）。未设置时也要显示 ——
            // 空着看起来像"没这个功能"，而保质期缺失会让临期提醒与「优先用快过期的」
            // 这两条链路对这样食材完全失效。
            if (onEditShelfLife != null) {
                val today = remember { LocalDate.now() }
                val expired = (ShelfLife.daysLeft(ingredient.expiryDate, today) ?: 1) < 0
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(enabled = editable) { onEditShelfLife() }
                        .padding(vertical = Spacing.xs)
                ) {
                    Icon(
                        Icons.Default.Event,
                        contentDescription = null,
                        modifier = Modifier.size(Spacing.lg),
                        tint = if (expired) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.primary
                    )
                    Spacer(modifier = Modifier.width(Spacing.sm))
                    Text(
                        text = ShelfLife.describe(ingredient.expiryDate, today),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (expired) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.primary
                    )
                }
            }
        }
    }
}

/**
 * 保质期编辑弹窗：填天数（而不是到期日）+ 可选的生产日期。
 *
 * 让用户填天数是因为包装上印的就是「保质期 12 个月」，而到期日要自己做日期加法 —— 算错了
 * 没有任何地方会提示。生产日期留空即按今天起算，覆盖「刚买回来的散装菜」这个最常见场景。
 */
@Composable
private fun ShelfLifeDialog(
    ingredient: Ingredient,
    onDismiss: () -> Unit,
    onConfirm: (Ingredient) -> Unit
) {
    val today = remember { LocalDate.now() }
    // 已有到期日时反算天数，让用户看到的是上次填的值而不是又跳回默认值。
    val initialDays = remember(ingredient) {
        val production = ShelfLife.parse(ingredient.productionDate) ?: today
        ShelfLife.parse(ingredient.expiryDate)
            ?.let { (it.toEpochDay() - production.toEpochDay()).toInt() }
            ?.takeIf { it >= ShelfLife.MIN_DAYS }
            ?: ingredient.shelfLifeDays
            ?: ShelfLife.defaultDaysFor(ingredient.category)
    }
    var daysText by remember { mutableStateOf(initialDays.toString()) }
    var productionText by remember { mutableStateOf(ingredient.productionDate.orEmpty()) }

    val days = daysText.toIntOrNull()
    val productionDate = ShelfLife.parse(productionText)
    // 输错的两种情况要分开说：天数非法 vs 日期格式不对。合成一句"输入有误"会让人
    // 反复改对的那一个。
    val daysError = days == null || days < ShelfLife.MIN_DAYS || days > ShelfLife.MAX_DAYS
    val productionError = productionText.isNotBlank() && productionDate == null
    // days 在这里已被 smart cast 成非空（daysError 覆盖了 null 分支）。
    val preview = if (!daysError) {
        ShelfLife.expiryFrom(productionDate, days, today)
    } else null

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("${ingredient.name} 的保质期") },
        text = {
            Column {
                OutlinedTextField(
                    value = daysText,
                    onValueChange = { daysText = it.filter { c -> c.isDigit() }.take(4) },
                    label = { Text("保质期（天）") },
                    isError = daysError,
                    supportingText = {
                        Text(
                            if (daysError) "请输入 ${ShelfLife.MIN_DAYS}–${ShelfLife.MAX_DAYS} 之间的天数"
                            else "包装上写「保质期 12 个月」就填 365"
                        )
                    },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Spacer(modifier = Modifier.height(12.dp))

                OutlinedTextField(
                    value = productionText,
                    onValueChange = { productionText = it.take(10) },
                    label = { Text("生产日期（可选）") },
                    placeholder = { Text(ShelfLife.format(today)) },
                    isError = productionError,
                    supportingText = {
                        Text(
                            if (productionError) "格式应为 ${ShelfLife.format(today)}"
                            else "留空就按今天算"
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Spacer(modifier = Modifier.height(12.dp))

                // 把算出来的到期日显示出来：这是用户唯一能核对"我填对了吗"的地方。
                Text(
                    text = preview?.let { "到期日：$it" } ?: "到期日：—",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.primary
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    val expiry = ShelfLife.expiryFrom(productionDate, days!!, today)
                    onConfirm(
                        ingredient.copy(
                            expiryDate = expiry,
                            productionDate = productionDate?.let { ShelfLife.format(it) },
                            shelfLifeDays = days,
                            recognitionDerived = false,
                            // 新鲜度跟着日期一起更新，否则卡片上会出现「还有 1 天」
                            // 配「新鲜」这种自相矛盾的组合。
                            freshness = ShelfLife.freshnessOf(expiry, today)
                        )
                    )
                },
                enabled = !daysError && !productionError
            ) { Text("保存") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}

@Composable
fun FreshnessBadge(freshness: Freshness) {
    val (text, color) = when (freshness) {
        Freshness.FRESH -> "新鲜" to MaterialTheme.colorScheme.primary
        Freshness.NORMAL -> "7天内到期" to MaterialTheme.colorScheme.secondary
        Freshness.EXPIRING -> "即将过期" to MaterialTheme.colorScheme.error
        Freshness.UNKNOWN -> "保质期未知" to MaterialTheme.colorScheme.outline
    }

    // 用 AssistChip 并把 color 真的用上：「即将过期」是这个列表上最需要被一眼看见的状态，而同一张
    // 卡里保质期那一行本来就有红/蓝区分，三种状态渲染成同色 chip 会让两处口径不一致。
    // onClick 为空 + enabled=false：不让 chip 看起来可点却什么都不发生。
    AssistChip(
        onClick = {},
        enabled = false,
        label = { Text(text) },
        colors = AssistChipDefaults.assistChipColors(
            disabledContainerColor = color.copy(alpha = 0.12f),
            disabledLabelColor = color
        ),
        border = null
    )
}

/**
 * 一件被选中的食材及其份量。
 *
 * 份量与「选中」绑在一起而不是全局共享：只有一份数量控件就一次只能添加一样东西，而用户往冰箱里放的
 * 总是一批。
 *
 * 数量始终使用当前模式的规范单位。
 */
private data class PickedAmount(val value: Int, val mode: AmountMode) {
    val unit: String get() = when (mode) {
        AmountMode.COUNT -> "个"
        AmountMode.WEIGHT -> "g"
        AmountMode.VOLUME -> "ml"
    }
    val display: String get() = "$value$unit"
    val step: Int get() = if (mode == AmountMode.COUNT) 1 else 100

    fun plus() = copy(value = value + step)
    fun minus() = copy(value = (value - step).coerceAtLeast(step))
    /** 切换计量方式时同时给一个合理起始值，避免出现 "1g" 或 "100个"。 */
    fun withMode(next: AmountMode) = if (next == mode) this else when (next) {
        AmountMode.COUNT -> PickedAmount(1, next)
        AmountMode.WEIGHT -> PickedAmount(100, next)
        AmountMode.VOLUME -> PickedAmount(500, next)
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AddIngredientDialog(
    onDismiss: () -> Unit,
    onAdd: (List<Ingredient>) -> Unit
) {
    // 用名称作键：搜索会重建列表，按对象作键在过滤前后可能不是同一实例。
    val picked = remember { mutableStateMapOf<String, PickedAmount>() }
    var customName by remember { mutableStateOf("") }
    var customAmount by remember { mutableStateOf("1") }
    var customMode by remember { mutableStateOf(AmountMode.COUNT) }
    var customUnit by remember { mutableStateOf("个") }
    var searchQuery by remember { mutableStateOf("") }

    val allItems = foodIconItems
    val filteredItems = remember(searchQuery) {
        if (searchQuery.isBlank()) allItems
        else allItems.filter { it.name.contains(searchQuery, ignoreCase = true) }
    }

    fun toggle(name: String) {
        if (picked.containsKey(name)) picked.remove(name)
        else picked[name] = PickedAmount(1, AmountMode.COUNT)
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(20.dp),
        tonalElevation = 6.dp,
        title = {
            Column {
                Text("添加食材")
                Text(
                    if (picked.isEmpty()) "选择常见食材，可多选"
                    else "已选 ${picked.size} 样",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        },
        text = {
            Column(modifier = Modifier.heightIn(max = 520.dp)) {
                OutlinedTextField(
                    value = searchQuery,
                    onValueChange = { searchQuery = it },
                    label = { Text("搜索食材") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                    trailingIcon = {
                        if (searchQuery.isNotEmpty()) {
                            IconButton(onClick = { searchQuery = "" }) {
                                Icon(Icons.Default.Close, contentDescription = "清空")
                            }
                        }
                    }
                )

                Spacer(modifier = Modifier.height(10.dp))

                // 两列给已选食材留出完整的单位分段控件和可靠点击尺寸。
                LazyColumn(modifier = Modifier.heightIn(max = 300.dp)) {
                    items(filteredItems.chunked(2)) { rowItems ->
                        Row(
                            modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            rowItems.forEach { food ->
                                FoodPickTile(
                                    food = food,
                                    amount = picked[food.name],
                                    onToggle = { toggle(food.name) },
                                    onChange = { picked[food.name] = it },
                                    modifier = Modifier.weight(1f)
                                )
                            }
                            repeat(2 - rowItems.size) {
                                Spacer(modifier = Modifier.weight(1f))
                            }
                        }
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))

                // 自定义名称与常见食材选择并存，不再二选一：切到手动输入时仍保留已选内容，
                // 已选的东西看不见了，让人怀疑是不是丢了。
                OutlinedTextField(
                    value = customName,
                    onValueChange = { customName = it },
                    label = { Text("其他食材（列表里没有的）") },
                    placeholder = { Text("输入名称，按 1 个计") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp)
                )
                if (customName.isNotBlank()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        listOf(
                            AmountMode.COUNT to "按件",
                            AmountMode.WEIGHT to "按重量",
                            AmountMode.VOLUME to "按容量"
                        ).forEachIndexed { index, (mode, label) ->
                            SegmentedButton(
                                selected = customMode == mode,
                                onClick = {
                                    customMode = mode
                                    customAmount = if (mode == AmountMode.COUNT) "1" else "500"
                                    customUnit = if (mode == AmountMode.VOLUME) "ml" else "个"
                                },
                                shape = SegmentedButtonDefaults.itemShape(index, 3),
                                label = { Text(label) }
                            )
                        }
                    }
                    if (customMode == AmountMode.COUNT) {
                        FlowRow(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(Spacing.xs),
                            verticalArrangement = Arrangement.spacedBy(Spacing.xs)
                        ) {
                            listOf("个", "瓶", "盒", "袋", "包").forEach { unit ->
                                FilterChip(
                                    selected = customUnit == unit,
                                    onClick = { customUnit = unit },
                                    label = { Text(unit) }
                                )
                            }
                        }
                    } else if (customMode == AmountMode.VOLUME) {
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            listOf("ml", "L").forEach { unit ->
                                FilterChip(
                                    selected = customUnit == unit,
                                    onClick = { customUnit = unit },
                                    label = { Text(unit) }
                                )
                            }
                        }
                    }
                    OutlinedTextField(
                        value = customAmount,
                        onValueChange = { customAmount = it.decimalInput() },
                        label = { Text("数量") },
                        suffix = { Text(if (customMode == AmountMode.WEIGHT) "g" else customUnit) },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                    if ((customAmount.toDoubleOrNull() ?: 0.0) <= 0) {
                        Text("请输入大于 0 的数量", color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        },
        confirmButton = {
            val total = picked.size + if (customName.isNotBlank()) 1 else 0
            val customValid = customName.isBlank() ||
                (customAmount.toDoubleOrNull() ?: 0.0) > 0
            Button(
                onClick = {
                    val result = mutableListOf<Ingredient>()
                    picked.forEach { (name, amt) ->
                        result += Ingredient(
                            name = name,
                            quantity = amt.display,
                            quantityAmount = if (amt.mode == AmountMode.WEIGHT) 0.0 else amt.value.toDouble(),
                            quantityUnit = if (amt.mode == AmountMode.VOLUME) "ml" else "个",
                            freshness = Freshness.UNKNOWN,
                            category = classifyIngredient(name),
                            weightGrams = amt.value.toDouble().takeIf { amt.mode == AmountMode.WEIGHT }
                        )
                    }
                    if (customName.isNotBlank()) {
                        val n = customName.trim()
                        val amount = customAmount.toDouble()
                        val base = Ingredient(
                            n, "1个", 1.0, "个", Freshness.UNKNOWN, classifyIngredient(n)
                        )
                        result += base.withEditedAmount(customMode, amount, customUnit)
                    }
                    if (result.isNotEmpty()) onAdd(result)
                },
                enabled = total > 0 && customValid,
                shape = RoundedCornerShape(12.dp)
            ) {
                Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(6.dp))
                // 把数量写进按钮：一次加多样时，用户需要在提交前确认自己选了几样。
                Text(if (total > 0) "添加 $total 样" else "添加")
            }
        },
        dismissButton = {
            OutlinedButton(onClick = onDismiss, shape = RoundedCornerShape(12.dp)) {
                Text("取消")
            }
        }
    )
}

private fun String.decimalInput(): String = filter { it.isDigit() || it == '.' }.take(8)

private fun Double.displayNumber(): String = if (this % 1.0 == 0.0) toInt().toString() else toString()

/**
 * 单个常见食材格：未选中时是一张浅色卡片，选中后浮起并在下方展开自己的加减控件。
 *
 * 加减控件只在选中后出现：全部常显会让网格变成一片按钮，反而看不出哪些被选了。
 */
@Composable
private fun FoodPickTile(
    food: FoodIconItem,
    amount: PickedAmount?,
    onToggle: () -> Unit,
    onChange: (PickedAmount) -> Unit,
    modifier: Modifier = Modifier
) {
    val selected = amount != null
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        // 用阴影表达"浮起"而不只是换背景色：选中态需要在一屏几十个条目里一眼可辨。
        shadowElevation = if (selected) 4.dp else 0.dp,
        color = if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
        border = if (selected) BorderStroke(1.5.dp, MaterialTheme.colorScheme.primary) else null
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.clickable(onClick = onToggle).padding(vertical = 8.dp, horizontal = 4.dp)
        ) {
            Image(
                painter = painterResource(food.iconRes),
                contentDescription = food.name,
                modifier = Modifier.size(34.dp)
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                food.name,
                style = MaterialTheme.typography.labelSmall,
                maxLines = 1,
                textAlign = TextAlign.Center
            )

            if (amount != null) {
                Spacer(modifier = Modifier.height(Spacing.sm))
                Text(
                    "计量方式",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                SegmentedControl(
                    options = listOf("件", "克", "ml"),
                    selectedIndex = amount.mode.ordinal,
                    onSelect = { onChange(amount.withMode(AmountMode.entries[it])) }
                )
                Spacer(modifier = Modifier.height(Spacing.xs))
                Text(
                    "数量",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    StepperButton(Icons.Default.Remove, "减少") { onChange(amount.minus()) }
                    Text(
                        amount.value.toString(),
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.weight(1f)
                    )
                    StepperButton(Icons.Default.Add, "增加") { onChange(amount.plus()) }
                }
            }
        }
    }
}

/**
 * 加减按钮。
 *
 * 用 Surface 包一层做出实体感，并保证可靠的可点区域。
 */
@Composable
private fun StepperButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    description: String,
    onClick: () -> Unit
) {
    Surface(
        shape = CircleShape,
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 1.dp,
        modifier = Modifier.size(Spacing.xxxl + Spacing.lg)
    ) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier.clickable(onClick = onClick)
        ) {
            Icon(
                icon,
                contentDescription = description,
                modifier = Modifier.size(Spacing.xl),
                tint = MaterialTheme.colorScheme.primary
            )
        }
    }
}
