package org.smartfridgemagnet.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.audio.VoiceRecorder
import androidx.compose.material.icons.filled.Delete
import org.smartfridgemagnet.network.PlanNotFoundException
import org.smartfridgemagnet.network.RecommendLatestDto
import org.smartfridgemagnet.network.RevisePlanDto
import org.smartfridgemagnet.network.dishNames
import org.smartfridgemagnet.network.missingForShownPlan
import kotlinx.coroutines.launch
import java.time.LocalTime

/**
 * 调整今日菜单：表单式微调为主，说人话改单作为其中一个入口。
 *
 * 不做成聊天界面：绝大多数调整就是「去掉这道菜」，在对话里表达要打一句话、等模型 20 多秒，还
 * 可能被理解错；现在删菜是列表上一个按钮，就地生效、不打模型。而对话界面还有个更根本的问题 ——
 * 用户看不到「现在这一餐有哪几道菜」，改完也看不出改了什么。
 *
 * 所以这一页是：当前菜单（可删菜）+ 一句话改单（给出 diff 再确认）+ 下发。
 *
 * 三条必须在 UI 上如实反映的后端契约：
 *
 * 1) **改单只算不写**（`/api/meal-plan/revise`）。结果先进 diff 卡，确认时把那份方案原样回传给
 *    `/apply`。若让「确认」重新调一次模型，第二次结果与用户看过的 diff 不是同一份。
 *
 * 2) **删菜与改单会落库，但不会自动下发**。设备什么时候换画面是另一个决定，所以下发是独立按钮。
 *
 * 3) **这一页不生成整餐**。改单以现有菜单为基准；这一餐还没有菜单时只能引导去菜谱页，否则两个
 *    入口的语义会混掉（那边是按库存重新配，这边是改现有的）。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RecipeChatScreen(
    onBack: () -> Unit,
    onPlanUpdated: () -> Unit,
    revisionPending: RevisePlanDto?,
    revisionBusy: Boolean,
    revisionProgressText: String?,
    revisionError: String?,
    onRevise: (mealKey: String, request: String) -> Unit,
    onDiscardRevision: () -> Unit
) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val client = remember { app.sfmBackendClient }
    val scope = rememberCoroutineScope()
    val configured = client.config.configured

    var input by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var publishing by remember { mutableStateOf(false) }
    /** 下发结果。后端原文，包含幂等跳过那句说明。 */
    var notice by remember { mutableStateOf<String?>(null) }
    var noticeIsError by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    /** 正在调整哪一餐。默认按钟点选，与后端 _guess_meal_key_by_clock 同口径。 */
    var mealKey by remember { mutableStateOf(defaultMealKey()) }
    /** 该餐当前已落库的菜单；null = 还没有（或没读到）。 */
    var current by remember { mutableStateOf<RecommendLatestDto?>(null) }
    var loading by remember { mutableStateOf(false) }
    var loadError by remember { mutableStateOf<String?>(null) }
    /** 读取该餐当前菜单。 */
    suspend fun reload() {
        loading = true
        loadError = null
        client.fetchLatestPlan(mealKey).fold(
            onSuccess = { current = it },
            onFailure = { e ->
                current = null
                // 「库里还没有这一餐」不是错误，是要引导去生成 —— 后端用 PlanNotFoundException
                // 单独表达，正是为了让这里能分开处理。
                loadError = if (e is PlanNotFoundException) null else (e.message ?: "读取失败")
            }
        )
        loading = false
    }

    LaunchedEffect(mealKey) { reload() }

    // ---- 语音输入 ----
    val recorder = remember { VoiceRecorder() }
    var voiceState by remember { mutableStateOf(VoiceState.IDLE) }
    var micGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED
        )
    }
    // 离开这一页时必须显式停录，**不能只依赖协程被取消**：VoiceRecorder.record() 的读循环
    // 是 `AudioRecord.read()` 这样的阻塞调用，中间没有挂起点，所以协程取消不会打断它 ——
    // 它会一直读到 60 秒上限才退出，期间麦克风始终被占用（其他应用也拿不到）。
    // stop() 只是把标志置 false，读循环下一轮就退出。
    DisposableEffect(Unit) {
        onDispose { recorder.stop() }
    }

    /** 录音 → 上传识别 → 文字落进输入框。 */
    fun startVoice() {
        if (voiceState != VoiceState.IDLE || sending || revisionBusy) return
        if (!configured) {
            notice = "尚未配置后端地址，语音识别不可用"
            noticeIsError = true
            return
        }
        voiceState = VoiceState.RECORDING
        notice = null
        scope.launch {
            val recorded = recorder.record()
            recorded.fold(
                onSuccess = { wav ->
                    voiceState = VoiceState.TRANSCRIBING
                    client.transcribeAudio(wav, language = "zh").fold(
                        onSuccess = { dto ->
                            if (dto.empty) {
                                // 后端明确说了识别结果是空的。这不是故障，别摆一条错误 ——
                                // 按住又立刻松手、或者环境太吵都会这样，再说一次就行。
                                notice = "没听到内容，再说一次？"
                                noticeIsError = false
                            } else {
                                // 追加而不是覆盖：用户可能先打了几个字再补一句语音。
                                input = if (input.isBlank()) dto.text
                                else input.trimEnd() + " " + dto.text
                            }
                        },
                        onFailure = { e ->
                            notice = e.message ?: "语音识别失败"
                            noticeIsError = true
                        }
                    )
                },
                onFailure = { e ->
                    // 录太短、麦克风被占用、权限刚被撤销都落在这里。
                    notice = e.message ?: "录音失败"
                    noticeIsError = true
                }
            )
            voiceState = VoiceState.IDLE
        }
    }

    val micPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        micGranted = granted
        if (granted) {
            // 刚授权时**不自动开始录音**：用户的手指早已离开按钮（系统弹窗接管了触摸），
            // 这时开录会得到一段没人在说话的音频。让他再按一次。
            notice = "已获得麦克风权限，按住麦克风说话"
            noticeIsError = false
        } else {
            notice = "没有麦克风权限，无法语音输入。可在系统设置里打开后重试"
            noticeIsError = true
        }
    }

    /** 说一句话改菜单：只算不写，结果进 [revisionPending] 等用户确认。 */
    fun revise(raw: String) {
        val text = raw.trim()
        if (text.isEmpty() || sending || revisionBusy) return
        input = ""
        notice = null
        onDiscardRevision()
        onRevise(mealKey, text)
    }

    /** 确认待应用的改动：把 revise 返回的那份方案原样落库。 */
    fun applyPending() {
        val dto = revisionPending ?: return
        val plan = dto.plan ?: return
        // 餐次取 **diff 自己所属的那一餐**，不是当下选中的 tab：这两者一旦不同，落库的就是
        // 一份用户没为这一餐审过的菜单（改晚餐 → 切到早餐 → 确认 = 晚餐的菜写进早餐）。
        // 切 tab 时已经清掉 pending，这里是第二道保险。
        val target = dto.mealType ?: mealKey
        // 基准 id 一起带回去，让后端拦住"看 diff 期间菜单被别处改过"的情况。
        val base = dto.basePlanId
        sending = true
        scope.launch {
            client.applyRevisedPlan(target, plan, dto.peopleCount, base).fold(
                onSuccess = { written ->
                    if (written.success) {
                        onDiscardRevision()
                        notice = "已保存：${written.dishes.joinToString("、")}"
                        noticeIsError = false
                        sending = false
                        reload()
                        onPlanUpdated()
                    } else {
                        // 无论是基准过期还是这份方案本身被拒，这张 diff 卡都已经没有用了：
                        // 留着它只会让用户反复点同一个必然失败的确认。丢掉并刷出最新菜单，
                        // 文案用后端的（它知道是"变过了"还是"有过敏源"）。
                        onDiscardRevision()
                        notice = written.error ?: "保存失败"
                        noticeIsError = true
                        sending = false
                        reload()
                    }
                },
                onFailure = { e ->
                    notice = e.message ?: "保存失败"
                    noticeIsError = true
                    sending = false
                }
            )
        }
    }

    /** 表单式微调：删掉一道菜（走后端，App 不碰方案结构）。 */
    fun deleteDish(name: String) {
        if (sending) return
        sending = true
        notice = null
        // 删菜是就地落库的，之前那张待确认的 diff 是基于删除前的菜单算出来的。
        // 不清掉它，用户接着点「确认」就会把刚删的菜连同旧方案一起写回去，还提示"已保存"。
        onDiscardRevision()
        scope.launch {
            client.removeDish(mealKey, name).fold(
                onSuccess = { dto ->
                    if (dto.success) {
                        notice = "已删除「$name」，剩 ${dto.dishes.size} 道"
                        noticeIsError = false
                        reload()
                        onPlanUpdated()
                    } else {
                        // 「这是最后一道菜」这类业务态，原样展示后端给的引导文案。
                        // 顺手重读一次：「没有这道菜」「基准变过了」都说明页面上这份已经过时，
                        // 不刷新的话用户会对着一份不存在的菜单反复点删除。
                        notice = dto.error ?: "删除失败"
                        noticeIsError = true
                        reload()
                    }
                    sending = false
                },
                onFailure = { e ->
                    notice = e.message ?: "删除失败"
                    noticeIsError = true
                    sending = false
                }
            )
        }
    }

    /**
     * 让设备用上刚生成的这份方案。
     *
     * 走 triggerPublish 而不是自己渲染：对话生成的方案已经落库，而后端的下发任务读的正是库里那
     * 一餐最新的一份，所以这里只需「让它发一次」。
     *
     * force = true：用户刚刚挑定了这一份菜单。后端幂等是为了不让自动任务重复发，落到这里会变成
     * 「今天已自动发布过 → 用户点确认 → 屏幕不变」，让按钮名不副实。
     */
    fun publish() {
        if (publishing) return
        publishing = true
        notice = null
        scope.launch {
            client.triggerPublish(mealKey, force = true).fold(
                onSuccess = { detail ->
                    // 原样展示后端的说明。force=true 下后端不会再走幂等跳过分支，
                    // 但仍不自造文案：真实结果（发给了几台设备 / 哪一餐）只有后端知道。
                    notice = detail
                    noticeIsError = false
                    publishing = false
                    onPlanUpdated()
                    // 「发布成功」不等于「屏幕会更新」。设备取图要带 device_token，后端没登记
                    // 它时每次都被 401 挡住 —— 而这条链路的失败在这一页完全看不见：发布成功、
                    // 版本号自增、什么都正常，只是屏幕永远不变（真机上就是这么发生的）。
                    // 顺手核一次，把这件事说出来；只多一次毫秒级查询。
                    client.fetchSyncStatus().onSuccess { st ->
                        if (!st.deviceTokenRegistered) {
                            notice = "已发布，但设备连接需要修复。请打开「设备」页处理。"
                            noticeIsError = true
                        }
                    }
                },
                onFailure = { e ->
                    notice = e.message ?: "下发失败"
                    noticeIsError = true
                    publishing = false
                }
            )
        }
    }

    // diff 卡出现时滚到它：改单要等 20 多秒，结果出来时用户的视线未必还在这一页的底部，
    // 而那张卡上有需要他做决定的两个按钮。只在这一个时机滚 —— 删菜是就地生效的，
    // 每次删都把页面弹到底部反而打断连续删几道菜的操作。
    LaunchedEffect(revisionPending) {
        if (revisionPending != null) listState.animateScrollToItem(1)
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("调整今日菜单") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                }
            )
        }
    ) { padding ->
        if (!configured) {
            // 后端未配置时整页只给一条引导：对话引擎在后端，这里放一个能输入却发不出去的
            // 输入框，只会让用户以为是网络问题。
            Box(
                modifier = Modifier.fillMaxSize().padding(padding).padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    )
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text("尚未配置后端", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "对话和配菜都在后端。请到「设置 → 冰箱贴后端」填写地址与 Token。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
        } else {
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                item {
                    MealTabs(
                        selected = mealKey,
                        enabled = !sending && !revisionBusy,
                        // 切餐次要丢掉待确认的 diff：它算的是另一餐，而卡片上只写着菜名，
                        // 看不出属于哪一餐。留着它等于给用户一个会把菜写错餐次的按钮。
                        onSelect = { mealKey = it; onDiscardRevision() }
                    )
                }

                item {
                    CurrentMenuCard(
                        plan = current,
                        loading = loading,
                        error = loadError,
                        busy = sending || revisionBusy,
                        publishing = publishing,
                        onDeleteDish = ::deleteDish,
                        onPublish = ::publish,
                        onRetryLoad = { scope.launch { reload() } }
                    )
                }

                revisionPending?.let { diff ->
                    item {
                        ReviseDiffCard(
                            diff = diff,
                            busy = sending || revisionBusy,
                            onApply = ::applyPending,
                            onDiscard = onDiscardRevision
                        )
                    }
                }

                if (revisionBusy && revisionPending == null) {
                    item { WorkingRow(revisionProgressText) }
                }

                if (current != null && revisionPending == null) {
                    item { QuickPhrases(onPick = { input = it }) }
                }
            }

            val shownNotice = revisionError ?: notice
            if (shownNotice != null) {
                PublishNotice(
                    text = shownNotice,
                    isError = revisionError != null || noticeIsError
                )
            }

            InputBar(
                value = input,
                sending = sending || revisionBusy,
                recordState = voiceState,
                onValueChange = { input = it },
                onSend = { revise(input) },
                onVoiceStart = {
                    // 每次按下都重新查一遍，不信 micGranted 那份缓存：用户可能刚在系统设置里
                    // 撤销了麦克风权限再切回来。信缓存的话会走到 AudioRecord 构造失败，
                    // 提示变成「麦克风被占用」—— 把权限问题说成了硬件问题，用户按这个提示
                    // 去关别的应用，怎么试都不会好。
                    val granted = ContextCompat.checkSelfPermission(
                        context, Manifest.permission.RECORD_AUDIO
                    ) == PackageManager.PERMISSION_GRANTED
                    micGranted = granted
                    if (granted) startVoice()
                    else micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                },
                onVoiceStop = { recorder.stop() }
            )
        }
        }
    }
}

@Composable
private fun QuickPhrases(onPick: (String) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            "试试这么问：",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Spacer(Modifier.height(6.dp))
        QUICK_PHRASES.forEach { phrase ->
            SuggestionChip(
                onClick = { onPick(phrase) },
                label = { Text(phrase) },
                modifier = Modifier.padding(bottom = 6.dp)
            )
        }
    }
}



/** 下发结果。成功分支原样展示后端说明（可能是幂等跳过）。 */
@Composable
private fun PublishNotice(text: String, isError: Boolean) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (isError) MaterialTheme.colorScheme.errorContainer
            else MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = if (isError) MaterialTheme.colorScheme.onErrorContainer
            else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(12.dp)
        )
    }
}


/**
 * 输入栏：语音（按住说话）+ 输入框 + 发送。发送中整条禁用：重复发送只会让后端多打几次模型。
 *
 * 语音用「按住说话」而不是点按开关：按住的语义没有歧义，点按式要额外表达「现在是不是在录」，
 * 状态一旦和用户认知错开就会得到一段空录音或一段本不想发的录音。
 *
 * 识别出的文字落进输入框而不是直接发出去：ASR 会认错（尤其菜名和方言），直接发要等一轮模型回复
 * 完才发现听错了。多一次点击换掉一整轮 25 秒的错误往返。
 */
@Composable
private fun InputBar(
    value: String,
    sending: Boolean,
    recordState: VoiceState,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
    onVoiceStart: () -> Unit,
    onVoiceStop: () -> Unit
) {
    Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        // 录音与转写期间在输入栏上方给一行状态。放在这里而不是弹 Toast：用户此刻的手指
        // 就压在下面那个按钮上，视线也在这一带。
        when (recordState) {
            VoiceState.RECORDING -> VoiceHint("正在录音，松手结束", pulsing = true)
            VoiceState.TRANSCRIBING -> VoiceHint("正在识别…", pulsing = false)
            VoiceState.IDLE -> Unit
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            // 录音中同样算忙。漏掉 RECORDING 的话发送按钮在录音期间仍可点：消息发出去、
            // 输入框被清空，而随后识别出的文字又追加进那个已经空了的框 —— 用户看到的是
            // 「我说的话被发走了一半，另一半留在输入框里」。
            val busy = sending ||
                recordState == VoiceState.TRANSCRIBING ||
                recordState == VoiceState.RECORDING
            // 麦克风自己的可用性不看 busy 里的 RECORDING —— 正在录音时它必须保持可用，
            // 否则手指还按着按钮就先被禁用，pointerInput 收不到抬起事件、录音停不下来。
            val micEnabled = !(sending || recordState == VoiceState.TRANSCRIBING)
            // 手势期间**绝不能**让 pointerInput 重启：key 变化会取消正在跟踪这根手指的协程，
            // waitForUpOrCancellation() 于是永不返回、松手不触发停止，交互退化成「点一下开始、
            // 再点一下结束」。而 recordState 恰好在 onVoiceStart() 里被同步改成 RECORDING，
            // 把它放进 key 就等于每次按下都自毁。所以 key 用 Unit，变化的值走
            // rememberUpdatedState 读最新的一份。
            val startVoice by rememberUpdatedState(onVoiceStart)
            val stopVoice by rememberUpdatedState(onVoiceStop)
            val canRecord by rememberUpdatedState(micEnabled)
            FilledIconButton(
                onClick = {},
                enabled = micEnabled,
                colors = if (recordState == VoiceState.RECORDING) {
                    IconButtonDefaults.filledIconButtonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                        contentColor = MaterialTheme.colorScheme.onError
                    )
                } else IconButtonDefaults.filledIconButtonColors(),
                modifier = Modifier.pointerInput(Unit) {
                    awaitPointerEventScope {
                        while (true) {
                            awaitFirstDown(requireUnconsumed = false)
                            if (!canRecord) {
                                // 发送中 / 转写中按下不开录，但仍要把这根手指等掉，
                                // 否则下一轮 awaitFirstDown 会立刻拿到同一次按下。
                                waitForUpOrCancellation()
                                continue
                            }
                            startVoice()
                            // 等这一根手指抬起或被取消（滑出去、被系统抢走）。两种都当
                            // 「说完了」处理：已经录到的内容照样送去识别，比丢掉重来更
                            // 符合「我刚才明明说了」的预期。
                            waitForUpOrCancellation()
                            stopVoice()
                        }
                    }
                }
            ) {
                Icon(
                    Icons.Default.Mic,
                    contentDescription = if (recordState == VoiceState.RECORDING) "松手结束录音"
                    else "按住说话"
                )
            }
            Spacer(Modifier.width(8.dp))
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                enabled = !busy,
                // 占位文案要说"改"而不是"吃什么"：这个输入框走的是改单接口，
                // 以当前菜单为基准。写成"今天想吃点什么"会让人以为能在这里重新生成。
                placeholder = { Text("说一句话改这一餐，例如「换清淡的」") },
                maxLines = 3,
                modifier = Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            FilledIconButton(
                onClick = onSend,
                enabled = !busy && value.isNotBlank()
            ) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "发送")
            }
        }
    }
}

/** 语音三态。转写期间不能再录，也不能发文字 —— 结果就要落进那个输入框。 */
private enum class VoiceState { IDLE, RECORDING, TRANSCRIBING }

@Composable
private fun VoiceHint(text: String, pulsing: Boolean) {
    Row(
        modifier = Modifier.padding(bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (pulsing) {
            // 录音中用一个持续的呼吸点，而不是转圈：转圈表示"在等系统做事"，
            // 而这里等的是用户自己说完。
            val alpha by rememberInfiniteTransition(label = "rec").animateFloat(
                initialValue = 0.3f, targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(600), repeatMode = RepeatMode.Reverse
                ),
                label = "recAlpha"
            )
            Box(
                Modifier
                    .size(8.dp)
                    .alpha(alpha)
                    .background(MaterialTheme.colorScheme.error, CircleShape)
            )
        } else {
            CircularProgressIndicator(Modifier.size(12.dp), strokeWidth = 2.dp)
        }
        Spacer(Modifier.width(8.dp))
        Text(
            text,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

// ==================== action 分支 ====================




// ==================== 调整菜单的组件 ====================

/** 三餐的规范键与中文标签，与后端 meals.MEAL_KEYS 同序。 */
private val MEAL_TABS = listOf(
    "breakfast" to "早餐",
    "lunch" to "午餐",
    "dinner" to "晚餐",
)

/**
 * 默认打开哪一餐。
 *
 * 口径与后端 `_guess_meal_key_by_clock()` 一致（10 点前早餐、14 点前午餐、其余晚餐）：
 * 两边不一致时，页面打开显示的是一餐、而"当前这一餐"的自动下发用的是另一餐，
 * 用户改了半天发现设备上没变。
 */
private fun defaultMealKey(): String {
    val hour = LocalTime.now().hour
    return when {
        hour < 10 -> "breakfast"
        hour < 14 -> "lunch"
        else -> "dinner"
    }
}

/**
 * 说人话改单的示例短语。
 *
 * 措辞都是**针对"改现有菜单"**的祈使句，不是"今天吃什么"那类提问：这一页的输入框走的是
 * `/api/meal-plan/revise`，它以当前菜单为基准做修改。写成提问会让用户以为能在这里重新生成，
 * 而实际得到的是"以现有菜单为基准的一次改动"。生成整餐在菜谱页。
 */
private val QUICK_PHRASES = listOf(
    "换成清淡一点的",
    "把肉菜换成素的",
    "加一道汤",
    "做法简单些，20 分钟内能做完",
)

/** 餐次切换。 */
@Composable
private fun MealTabs(selected: String, enabled: Boolean, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        MEAL_TABS.forEach { (key, label) ->
            FilterChip(
                selected = selected == key,
                onClick = { if (enabled) onSelect(key) },
                enabled = enabled,
                label = { Text(label) }
            )
        }
    }
}

/**
 * 当前这一餐的菜单，每道菜可删。
 *
 * 表单式微调是这一页的主体，说人话改单只是它的一个入口 —— 因为绝大多数调整就是"去掉这道"，
 * 而那既不需要模型、也不该等 20 秒。删除走后端接口（App 不持有方案结构，详见
 * `SfmBackendClient.removeDish`）。
 */
@Composable
private fun CurrentMenuCard(
    plan: RecommendLatestDto?,
    loading: Boolean,
    error: String?,
    busy: Boolean,
    publishing: Boolean,
    onDeleteDish: (String) -> Unit,
    onPublish: () -> Unit,
    onRetryLoad: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
    ) {
        Column(Modifier.padding(14.dp)) {
            if (loading) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("正在读取菜单…", style = MaterialTheme.typography.bodySmall)
                }
                return@Column
            }

            if (error != null) {
                Text("读取失败：$error", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = onRetryLoad) { Text("重试") }
                return@Column
            }

            val dishes = plan?.dishNames().orEmpty()
            if (plan == null || dishes.isEmpty()) {
                // 「这一餐还没有菜单」不是错误。改单必须以现有菜单为基准，所以这里只能引导去
                // 生成 —— 在这一页放一个"生成"按钮会让两个页面都能生成整餐，而它们的入口
                // 语义不同（那边是按库存重新配，这边是改现有的）。
                Text("这一餐还没有菜单", style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(6.dp))
                Text(
                    "先到「菜谱」页生成一份，再回来调整。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                return@Column
            }

            Text(
                "当前菜单 · ${plan.mealLabel ?: ""}".trim(),
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold
            )
            if (plan.stale) {
                Text(
                    "这份可能不是今天的（生成于 ${plan.ageMinutes ?: "?"} 分钟前）",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error
                )
            }
            Spacer(Modifier.height(8.dp))

            // 只剩一道菜时禁掉删除：后端会拒绝删到一道不剩（那会渲染出一张空白画面），
            // 而一个点下去必然失败的按钮只会让人连点几次 —— 真机日志里就是连着三次
            // remove-dish 全部返回同一条失败。禁用 + 下面那行说明，比让他撞三次好。
            val lastOne = dishes.size <= 1
            dishes.forEach { name ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("• $name", style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.weight(1f))
                    IconButton(onClick = { onDeleteDish(name) }, enabled = !busy && !lastOne) {
                        Icon(
                            Icons.Default.Delete,
                            contentDescription = "删除 $name",
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }
            }

            if (lastOne) {
                Text(
                    "只剩一道菜了，删完这一餐就没有内容可显示。要换菜请用下面的输入框说一句话。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            // null（后端没算过）与空列表（算过、不缺）在这里的处置相同：都不显示这一行。
            // 这一页只在"确实缺"的时候提醒一句，不像菜谱页那样要打"全都够"的结论。
            val shopping = plan.missingForShownPlan().orEmpty()
            if (shopping.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "🛒 还需采购：${shopping.joinToString("、")}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            Spacer(Modifier.height(12.dp))
            Button(
                onClick = onPublish,
                enabled = !publishing && !busy,
                modifier = Modifier.fillMaxWidth()
            ) { Text(if (publishing) "下发中…" else "让设备用这份菜单") }
            Text(
                "删除与改动会立刻保存，但设备要等这次下发才更新。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp)
            )
        }
    }
}

/**
 * 改单结果的 diff 与两个出口。
 *
 * 必须显示 diff 而不是直接把新菜单存下去：用户说的是一句模糊的话（"清淡点"），模型可能只换
 * 一道、也可能整份重排。看不到差异就无法判断这次改动是不是他要的，而菜单一旦落库就会被
 * 下发到设备上。
 */
@Composable
private fun ReviseDiffCard(
    diff: RevisePlanDto,
    busy: Boolean,
    onApply: () -> Unit,
    onDiscard: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer
        )
    ) {
        Column(Modifier.padding(14.dp)) {
            Text("改完是这样", style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))

            diff.after.forEach { name ->
                val isNew = name in diff.added
                Text(
                    if (isNew) "+ $name" else "• $name",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = if (isNew) FontWeight.Bold else FontWeight.Normal
                )
            }
            diff.removed.forEach { name ->
                Text(
                    "− $name",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            if (diff.added.isEmpty() && diff.removed.isEmpty()) {
                // 模型可能只改了做法或用量而没换菜。如实说出来，否则用户会以为改单没生效。
                Spacer(Modifier.height(4.dp))
                Text(
                    "菜品没有变化（可能只调整了做法或用量）。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            if (diff.missingIngredients.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "🛒 改完后需采购：${diff.missingIngredients.joinToString("、")}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onApply, enabled = !busy) {
                    Text(if (busy) "保存中…" else "就用这个")
                }
                OutlinedButton(onClick = onDiscard, enabled = !busy) { Text("放弃") }
            }
        }
    }
}

/** 处理中提示。改单要打一次模型，静默会被当成卡死。 */
@Composable
private fun WorkingRow(stage: String?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
        Spacer(Modifier.width(8.dp))
        Column {
            Text(
                stage ?: "AI 正在调整菜单",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                "通常需要几十秒，可返回其他页面，完成后会通知",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
