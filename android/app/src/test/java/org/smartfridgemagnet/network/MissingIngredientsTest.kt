package org.smartfridgemagnet.network

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 采购清单解析与取用口径的测试。
 *
 * `missingForShownPlan()` 必须与 `dishNames()` 挑同一套方案。挑错的表现不是崩溃 —— 屏幕上列着 A 方案
 * 的菜、缺料却报 B 方案的，用户照着买一堆用不上的东西，而两端都不报错。
 */
class MissingIngredientsTest {

    private val gson = Gson()

    @Test
    fun `解析后端的 missing_ingredients`() {
        val json = """
            {"success": true, "plan_id": 7,
             "plan_a": {"dishes": [{"name": "香菇青菜"}]},
             "missing_ingredients": {"plan_a": ["香菇"], "plan_b": []}}
        """.trimIndent()
        val dto = gson.fromJson(json, RecommendLatestDto::class.java)
        assertEquals(listOf("香菇"), dto.missingForShownPlan())
    }

    /** 口径：A 存在就取 A，与 dishNames() 一致。 */
    @Test
    fun `取用的方案与 dishNames 一致`() {
        val bothPlans = """
            {"success": true,
             "plan_a": {"dishes": [{"name": "A菜"}]},
             "plan_b": {"dishes": [{"name": "B菜"}]},
             "missing_ingredients": {"plan_a": ["香菇"], "plan_b": ["蒜"]}}
        """.trimIndent()
        val dto = gson.fromJson(bothPlans, RecommendLatestDto::class.java)
        assertEquals(listOf("A菜"), dto.dishNames())
        assertEquals("展示 A 的菜就要报 A 的缺料", listOf("香菇"), dto.missingForShownPlan())
    }

    /** A 为空回落 B 时，缺料也要跟着回落到 B，否则报的是另一套方案的清单。 */
    @Test
    fun `A 为空时缺料回落到 B`() {
        val onlyB = """
            {"success": true,
             "plan_b": {"dishes": [{"name": "B菜"}]},
             "missing_ingredients": {"plan_a": ["香菇"], "plan_b": ["蒜"]}}
        """.trimIndent()
        val dto = gson.fromJson(onlyB, RecommendLatestDto::class.java)
        assertEquals(listOf("B菜"), dto.dishNames())
        assertEquals(listOf("蒜"), dto.missingForShownPlan())
    }

    /**
     * 字段缺失要返回 **null**（「后端没算过」）而不是空列表，且不抛异常。
     *
     * 菜谱页在空列表时会打一句「三餐都能用冰箱里现有的食材做」。把「没数据」当成「不缺」，那句话就是
     * 编的。后端旧版本不带这个字段，而 App 与后端不是同步发布的。
     */
    @Test
    fun `字段缺失时返回 null 而不是空列表`() {
        val legacy = """{"success": true, "plan_a": {"dishes": [{"name": "A菜"}]}}"""
        assertNull(gson.fromJson(legacy, RecommendLatestDto::class.java).missingForShownPlan())

        val nullField = """
            {"success": true, "plan_a": {"dishes": []}, "missing_ingredients": null}
        """.trimIndent()
        assertNull(gson.fromJson(nullField, RecommendLatestDto::class.java).missingForShownPlan())
    }

    /** 算过、而这一套确实不缺：空列表。与上一条的 null 必须能区分开。 */
    @Test
    fun `算过且不缺料时返回空列表`() {
        val json = """
            {"success": true, "plan_a": {"dishes": [{"name": "A菜"}]},
             "missing_ingredients": {"plan_a": []}}
        """.trimIndent()
        val dto = gson.fromJson(json, RecommendLatestDto::class.java)
        assertEquals(emptyList<String>(), dto.missingForShownPlan())
    }

    @Test
    fun `两套方案都没有时不抛异常`() {
        val empty = """{"success": true, "missing_ingredients": {"plan_a": ["香菇"]}}"""
        val dto = gson.fromJson(empty, RecommendLatestDto::class.java)
        // planA 为 null 时按 plan_b 取，而 plan_b 键不存在 → null（这一套没算过），
        // 不能拿 plan_a 那份顶上：屏幕上根本没有 A 的菜。
        assertNull(dto.missingForShownPlan())
        assertTrue(dto.dishNames().isEmpty())
    }
}
