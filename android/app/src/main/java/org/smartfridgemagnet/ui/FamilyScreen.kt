package org.smartfridgemagnet.ui

import org.smartfridgemagnet.ui.theme.accents
import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.smartfridgemagnet.R
import org.smartfridgemagnet.data.*
import org.smartfridgemagnet.ui.viewmodel.FamilyViewModel
import org.smartfridgemagnet.ui.viewmodel.ProfileSyncState

// 预设头像列表（emoji + 可扩展为图片资源）
private val PRESET_AVATARS = listOf("👨", "👩", "👦", "👧", "👴", "👵", "🧑", "👶")

// 预设角色
private val ROLE_OPTIONS = listOf(
    MemberRole.ADULT to "成人",
    MemberRole.CHILD to "儿童",
    MemberRole.ELDER to "老人",
    MemberRole.PREGNANT to "孕妇"
)

// 预设健康目标
private val GOAL_OPTIONS = listOf(
    HealthGoal.BALANCED to "均衡营养",
    HealthGoal.LOSE_FAT to "减脂",
    HealthGoal.GAIN_MUSCLE to "增肌",
    HealthGoal.CONTROL_SUGAR to "控糖",
    HealthGoal.CONTROL_SALT to "控盐"
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FamilyScreen(
    viewModel: FamilyViewModel,
    onBack: () -> Unit
) {
    val members by viewModel.members.collectAsState()
    val syncState by viewModel.syncState.collectAsState()
    var showAddDialog by remember { mutableStateOf(false) }
    var editingMember by remember { mutableStateOf<FamilyMember?>(null) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("家庭成员") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    IconButton(onClick = { showAddDialog = true }) {
                        Icon(Icons.Default.Add, contentDescription = "添加")
                    }
                }
            )
        }
    ) { padding ->
        if (members.isEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(16.dp)
            ) {
                syncState?.let { st ->
                    ProfileSyncBanner(st, onRetry = { viewModel.syncToBackend() })
                    Spacer(modifier = Modifier.height(8.dp))
                }
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("👨‍👩‍👧‍👦", fontSize = 64.sp)
                        Spacer(modifier = Modifier.height(16.dp))
                        Text("还没有家庭成员", style = MaterialTheme.typography.titleMedium)
                        Spacer(modifier = Modifier.height(8.dp))
                        Button(onClick = { showAddDialog = true }) {
                            Icon(Icons.Default.Add, contentDescription = null)
                            Spacer(modifier = Modifier.width(8.dp))
                            Text("添加成员")
                        }
                    }
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(16.dp)
            ) {
                // 同步状态必须显示：本地保存永远成功，而后端没收到时推荐依旧不避让过敏源，
                // 界面上看不出任何异常 —— 这正是"App 里配了忌口但推荐没生效"的成因。
                syncState?.let { st ->
                    item {
                        ProfileSyncBanner(st, onRetry = { viewModel.syncToBackend() })
                        Spacer(modifier = Modifier.height(8.dp))
                    }
                }
                items(members) { member ->
                    FamilyMemberCard(
                        member = member,
                        preferencesText = viewModel.getMemberPreferencesText(member),
                        onDelete = { viewModel.deleteMember(member.id) },
                        onLongClick = { editingMember = member }
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                }
            }
        }
    }

    // 添加成员弹窗
    if (showAddDialog) {
        AddMemberDialog(
            onDismiss = { showAddDialog = false },
            onConfirm = { name, avatar, role, goal, preferences ->
                viewModel.addMember(name, avatar, role, goal, preferences)
                showAddDialog = false
            }
        )
    }

    // 编辑成员弹窗
    if (editingMember != null) {
        EditMemberDialog(
            member = editingMember!!,
            onDismiss = { editingMember = null },
            onConfirm = { updatedMember ->
                viewModel.updateMember(updatedMember)
                editingMember = null
            }
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FamilyMemberCard(
    member: FamilyMember,
    preferencesText: String,
    onDelete: () -> Unit,
    onLongClick: () -> Unit = {}
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(12.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // 圆形头像
            Box(
                modifier = Modifier
                    .size(56.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = member.avatar,
                    fontSize = 28.sp,
                    textAlign = TextAlign.Center
                )
            }

            Spacer(modifier = Modifier.width(16.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = member.name,
                    style = MaterialTheme.typography.titleMedium
                )
                Text(
                    text = preferencesText,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Default.Delete,
                    contentDescription = "删除",
                    tint = MaterialTheme.colorScheme.error
                )
            }
            IconButton(onClick = onLongClick) {
                Icon(
                    Icons.Default.Edit,
                    contentDescription = "编辑"
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddMemberDialog(
    onDismiss: () -> Unit,
    onConfirm: (
        name: String, avatar: String, role: MemberRole, goal: HealthGoal,
        preferences: DietaryPreferences
    ) -> Unit
) {
    var name by remember { mutableStateOf("") }
    var selectedAvatar by remember { mutableStateOf(PRESET_AVATARS[0]) }
    var selectedRole by remember { mutableStateOf(MemberRole.ADULT) }
    var selectedGoal by remember { mutableStateOf(HealthGoal.BALANCED) }
    var allergiesText by remember { mutableStateOf("") }
    var dislikesText by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("添加家庭成员") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState())
            ) {
                // 姓名输入
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("姓名") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Spacer(modifier = Modifier.height(16.dp))

                // 头像选择
                Text("选择头像", style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(8.dp))
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(PRESET_AVATARS) { avatar ->
                        Box(
                            modifier = Modifier
                                .size(48.dp)
                                .clip(CircleShape)
                                .background(
                                    if (selectedAvatar == avatar)
                                        MaterialTheme.colorScheme.primaryContainer
                                    else
                                        MaterialTheme.colorScheme.surfaceVariant
                                )
                                .clickable { selectedAvatar = avatar },
                            contentAlignment = Alignment.Center
                        ) {
                            Text(avatar, fontSize = 24.sp)
                        }
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // 角色选择
                Text("角色", style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(8.dp))
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(ROLE_OPTIONS) { (role, label) ->
                        FilterChip(
                            selected = selectedRole == role,
                            onClick = { selectedRole = role },
                            label = { Text(label) }
                        )
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // 健康目标选择
                Text("健康目标", style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(8.dp))
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(GOAL_OPTIONS) { (goal, label) ->
                        FilterChip(
                            selected = selectedGoal == goal,
                            onClick = { selectedGoal = goal },
                            label = { Text(label) }
                        )
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))
                AllergyAndDislikeFields(
                    allergiesText = allergiesText,
                    onAllergiesChange = { allergiesText = it },
                    dislikesText = dislikesText,
                    onDislikesChange = { dislikesText = it }
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    onConfirm(
                        name, selectedAvatar, selectedRole, selectedGoal,
                        DietaryPreferences(
                            allergies = parseFoodList(allergiesText),
                            dislikes = parseFoodList(dislikesText)
                        )
                    )
                },
                enabled = name.isNotBlank()
            ) {
                Text("添加")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        }
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EditMemberDialog(
    member: FamilyMember,
    onDismiss: () -> Unit,
    onConfirm: (FamilyMember) -> Unit
) {
    var name by remember { mutableStateOf(member.name) }
    var selectedAvatar by remember { mutableStateOf(member.avatar) }
    var selectedRole by remember { mutableStateOf(member.role) }
    var selectedGoal by remember { mutableStateOf(member.goal) }
    var allergiesText by remember { mutableStateOf(member.preferences.allergies.joinToString("、")) }
    var dislikesText by remember { mutableStateOf(member.preferences.dislikes.joinToString("、")) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("编辑成员") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState())
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("姓名") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )
                Spacer(modifier = Modifier.height(16.dp))
                Text("选择头像", style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(8.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(PRESET_AVATARS) { avatar ->
                        Box(
                            modifier = Modifier
                                .size(48.dp)
                                .clip(CircleShape)
                                .background(
                                    if (selectedAvatar == avatar) MaterialTheme.colorScheme.primaryContainer
                                    else MaterialTheme.colorScheme.surfaceVariant
                                )
                                .clickable { selectedAvatar = avatar },
                            contentAlignment = Alignment.Center
                        ) { Text(avatar, fontSize = 24.sp) }
                    }
                }
                Spacer(modifier = Modifier.height(16.dp))
                Text("角色", style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(8.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(ROLE_OPTIONS) { (role, label) ->
                        FilterChip(
                            selected = selectedRole == role,
                            onClick = { selectedRole = role },
                            label = { Text(label) }
                        )
                    }
                }
                Spacer(modifier = Modifier.height(16.dp))
                Text("健康目标", style = MaterialTheme.typography.labelMedium)
                Spacer(modifier = Modifier.height(8.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(GOAL_OPTIONS) { (goal, label) ->
                        FilterChip(
                            selected = selectedGoal == goal,
                            onClick = { selectedGoal = goal },
                            label = { Text(label) }
                        )
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))
                AllergyAndDislikeFields(
                    allergiesText = allergiesText,
                    onAllergiesChange = { allergiesText = it },
                    dislikesText = dislikesText,
                    onDislikesChange = { dislikesText = it }
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    onConfirm(
                        member.copy(
                            name = name,
                            avatar = selectedAvatar,
                            role = selectedRole,
                            goal = selectedGoal,
                            preferences = member.preferences.copy(
                                allergies = parseFoodList(allergiesText),
                                dislikes = parseFoodList(dislikesText)
                            )
                        )
                    )
                },
                enabled = name.isNotBlank()
            ) { Text("保存") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}

/**
 * 过敏源与忌口两个输入框。
 *
 * 两者刻意**分开两个字段、并且在视觉上区分严重程度**：
 *   - 过敏源是安全约束。后端会把它汇总置顶进 prompt，生成后还要校验一遍，命中就整套重生成；
 *     两次都躲不开时宁可不给推荐。
 *   - 忌口是偏好。违反了只是这顿不合胃口，后端按「整道换掉/标注去掉/调整做法」处理。
 *
 * 合成一个「不吃的东西」字段会抹掉这个区分，模型只能按同一强度对待，而两者的后果差着一个量级。
 */
@Composable
private fun AllergyAndDislikeFields(
    allergiesText: String,
    onAllergiesChange: (String) -> Unit,
    dislikesText: String,
    onDislikesChange: (String) -> Unit
) {
    Text(
        "过敏源",
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.error
    )
    Spacer(modifier = Modifier.height(4.dp))
    OutlinedTextField(
        value = allergiesText,
        onValueChange = onAllergiesChange,
        placeholder = { Text("如：花生、芒果、海鲜") },
        supportingText = { Text("生成菜谱时会严格避开，用「、」或逗号分隔") },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        isError = false
    )

    Spacer(modifier = Modifier.height(12.dp))

    Text("忌口（不吃但不过敏）", style = MaterialTheme.typography.labelMedium)
    Spacer(modifier = Modifier.height(4.dp))
    OutlinedTextField(
        value = dislikesText,
        onValueChange = onDislikesChange,
        placeholder = { Text("如：苦瓜、香菜") },
        supportingText = { Text("会尽量避开或换成别的菜") },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true
    )
}


/**
 * 家庭画像同步状态条。
 *
 * 成功时也显示（而不是只在失败时提示）：它回显后端汇总出的过敏源，这是用户能自己确认「我填的过敏
 * 确实进了推荐链路」的唯一途径。
 */
@Composable
private fun ProfileSyncBanner(state: ProfileSyncState, onRetry: () -> Unit) {
    val (bg, content) = when (state) {
        is ProfileSyncState.Synced -> MaterialTheme.accents.success.container to buildString {
            append("已同步到后端（${state.memberCount} 位成员）")
            if (state.allergens.isNotEmpty()) {
                append("，推荐将避开：")
                append(state.allergens.joinToString("、"))
            }
        }
        is ProfileSyncState.Failed ->
            MaterialTheme.accents.danger.container to "未同步到后端：${state.message}。忌口与过敏暂时只存在本机，推荐不会避开。"
        ProfileSyncState.Syncing -> MaterialTheme.colorScheme.surface to "正在同步到后端…"
        ProfileSyncState.NotConfigured ->
            MaterialTheme.accents.warning.container to "未配置后端地址，忌口与过敏只存在本机，推荐不会避开。"
    }

    Surface(color = bg, shape = MaterialTheme.shapes.small, modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(content, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
            if (state is ProfileSyncState.Failed || state is ProfileSyncState.NotConfigured) {
                TextButton(onClick = onRetry) { Text("重试") }
            }
        }
    }
}
