package org.smartfridgemagnet.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 锁住 SSE 行解析与后端 `agent.stream_*` 事件形状的对应关系。
 *
 * 值得单测的理由：这层没有编译期约束 —— 后端把 `delta` 改名成 `text`，客户端会安静地
 * 收到一串空增量，界面表现是「思考区一直是空的」，没有任何一处报错。
 */
class SseLineTest {

    private fun eventOf(line: String): StreamEvent {
        val parsed = parseSseLine(line)
        assertTrue("应解析为事件，实际 $parsed", parsed is SseLine.Event)
        return (parsed as SseLine.Event).event
    }

    @Test
    fun `阶段事件取 stage 字段`() {
        val e = eventOf("""data: {"type":"stage","stage":"正在读取冰箱库存"}""")
        assertEquals(StreamEvent.Stage("正在读取冰箱库存"), e)
    }

    @Test
    fun `思考与正文都取 delta 字段`() {
        assertEquals(
            StreamEvent.Reasoning("先看主料"),
            eventOf("""data: {"type":"reasoning","delta":"先看主料"}""")
        )
        assertEquals(
            StreamEvent.Content("{\"plan_a\""),
            eventOf("""data: {"type":"content","delta":"{\"plan_a\""}""")
        )
    }

    @Test
    fun `result 事件摊平在同一层_整个对象就是响应体`() {
        val e = eventOf(
            """data: {"type":"result","action":"recommend","reply":"已生成"}"""
        )
        assertTrue(e is StreamEvent.Done)
        val dto = (e as StreamEvent.Done).payload
        assertEquals("recommend", dto.action)
        assertEquals("已生成", dto.reply)
    }

    @Test
    fun `阻断类 result 保留业务 action_不能当成推荐成功`() {
        val e = eventOf(
            """data: {"type":"result","action":"recommend_inventory_blocked","reply":"库存不足"}"""
        ) as StreamEvent.Done

        assertEquals(ChatAction.RECOMMEND_INVENTORY_BLOCKED, e.payload.action)
        assertEquals("库存不足", e.payload.replyText())
    }

    @Test
    fun `error 事件缺 message 时给一句可展示的兜底`() {
        assertEquals(
            StreamEvent.Failed("生成失败，请重试"),
            eventOf("""data: {"type":"error"}""")
        )
    }

    @Test
    fun `空行与心跳注释是协议正常组成_不计入坏块`() {
        assertEquals(SseLine.Skip, parseSseLine(""))
        assertEquals(SseLine.Skip, parseSseLine("data:"))
        assertEquals(SseLine.Skip, parseSseLine(": keep-alive"))
        // event: 这类字段后端没用，但收到也不该当成坏块
        assertEquals(SseLine.Skip, parseSseLine("event: message"))
    }

    @Test
    fun `坏 JSON 与未知 type 都算坏块_必须能被计数`() {
        assertEquals(SseLine.Bad, parseSseLine("""data: {"type":"stage" """))
        assertEquals(SseLine.Bad, parseSseLine("""data: {"type":"unknown"}"""))
        // stage 事件缺 stage 字段没有可展示内容，同样算坏块而不是静默丢弃
        assertEquals(SseLine.Bad, parseSseLine("""data: {"type":"stage"}"""))
    }

    @Test
    fun `中文与转义字符原样取出`() {
        val e = eventOf("""data: {"type":"reasoning","delta":"生抽 1 勺\n老抽半勺"}""")
        assertEquals(StreamEvent.Reasoning("生抽 1 勺\n老抽半勺"), e)
    }
}
