package org.smartfridgemagnet.network

import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonSyntaxException

/**
 * 后端 SSE 的一个事件。字段与 `agent.stream_recommendation` / `agent.stream_chat` 产出的
 * 事件一一对应，不做重命名 —— 两端各起一套名字时，改了一边不会有任何地方报错。
 */
sealed interface StreamEvent {
    /** 阶段推进，如「正在读取冰箱库存」。可直接展示给用户。 */
    data class Stage(val text: String) : StreamEvent

    /** 模型思考增量，逐块累积展示。 */
    data class Reasoning(val delta: String) : StreamEvent

    /** 模型正文增量。 */
    data class Content(val delta: String) : StreamEvent

    /** 终局结果，形状与非流式 `/api/chat` 的响应一致。 */
    data class Done(val payload: ChatResponseDto) : StreamEvent

    /**
     * 生成没成功。
     *
     * 不区分「模型失败」与「连接断开」：流式响应的 HTTP 头在第一个字节时就已发出，中途失败
     * 没有状态码可言，而对用户来说两者是同一件事。
     */
    data class Failed(val message: String) : StreamEvent
}

/**
 * 从一行 SSE 里取出事件；不是数据行返回 [SseLine.Skip]，形状不认识返回 [SseLine.Bad]。
 *
 * 三态而不是可空返回值：调用方要把「注释行/空行」和「解析失败」分开 —— 前者是协议的正常组成
 * 部分，后者必须计数并写日志。都当 null 处理会让一个真实的解析 bug 被空行淹没。
 */
sealed interface SseLine {
    data class Event(val event: StreamEvent) : SseLine
    object Skip : SseLine
    object Bad : SseLine
}

private val sseGson = Gson()

/** 解析一行 SSE 文本。行尾换行已由调用方剥掉。 */
internal fun parseSseLine(line: String): SseLine {
    // 注释行（以 ':' 开头）常被用作心跳，必须在取 data 之前判掉。
    if (line.startsWith(":")) return SseLine.Skip
    if (!line.startsWith("data:")) return SseLine.Skip
    val payload = line.removePrefix("data:").trim()
    if (payload.isEmpty()) return SseLine.Skip

    val obj = try {
        sseGson.fromJson(payload, JsonObject::class.java)
    } catch (_: JsonSyntaxException) {
        return SseLine.Bad
    } ?: return SseLine.Bad

    val event = when (obj.get("type")?.asStringOrNull()) {
        "stage" -> obj.get("stage")?.asStringOrNull()?.let { StreamEvent.Stage(it) }
        "reasoning" -> StreamEvent.Reasoning(obj.get("delta")?.asStringOrNull().orEmpty())
        "content" -> StreamEvent.Content(obj.get("delta")?.asStringOrNull().orEmpty())
        "error" -> StreamEvent.Failed(
            obj.get("message")?.asStringOrNull() ?: "生成失败，请重试"
        )
        // result 事件把结果字典摊平在同一层，所以整个对象就是 ChatResponseDto。
        "result" -> StreamEvent.Done(sseGson.fromJson(obj, ChatResponseDto::class.java))
        else -> null
    }
    return event?.let { SseLine.Event(it) } ?: SseLine.Bad
}

private fun JsonElement.asStringOrNull(): String? =
    if (isJsonPrimitive) asString.takeIf { it.isNotBlank() } else null
