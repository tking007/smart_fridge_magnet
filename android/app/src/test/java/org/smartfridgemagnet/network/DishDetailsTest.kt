package org.smartfridgemagnet.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 菜名列表与做法映射必须来自**同一套方案**。
 *
 * 菜谱页用 [dishNames] 列菜、点开某道菜时用 [dishDetails] 取步骤。两者若各自判断「展示哪一套方案」，
 * A/B 并存时就会列 A 的菜、显示 B 的做法 —— 点开「红烧肉」看到另一道菜的步骤，而没有任何一处报错。
 */
class DishDetailsTest {

    private fun dish(name: String, vararg steps: String) = PlanDishDto(
        name = name,
        ingredients = listOf(PlanIngredientDto(name = "土豆", amount = "200g")),
        cookingTime = "20分钟",
        steps = steps.toList()
    )

    private fun latest(planA: MealPlanDto? = null, planB: MealPlanDto? = null) =
        RecommendLatestDto(success = true, planA = planA, planB = planB)

    @Test
    fun `菜名与做法取自同一套方案`() {
        val a = MealPlanDto(dishes = listOf(dish("土豆丝", "切丝", "下锅")))
        val b = MealPlanDto(dishes = listOf(dish("番茄汤", "切块", "煮开")))
        val dto = latest(planA = a, planB = b)

        assertEquals(listOf("土豆丝"), dto.dishNames())
        assertEquals(setOf("土豆丝"), dto.dishDetails().keys)
        assertEquals(listOf("切丝", "下锅"), dto.dishDetails()["土豆丝"]?.steps)
    }

    @Test
    fun `plan_a 缺失时两者一起回落 plan_b`() {
        val dto = latest(planB = MealPlanDto(dishes = listOf(dish("番茄汤", "煮开"))))

        assertEquals(listOf("番茄汤"), dto.dishNames())
        assertEquals(setOf("番茄汤"), dto.dishDetails().keys)
    }

    /**
     * planA 存在但 dishes 为空时，两者都必须落在 planA 上（哪怕结果是空的）。
     *
     * 最容易写歪的一处：dishNames 用 `planA ?: planB`、dishDetails 用
     * `planA?.takeIf { it.dishes.isNotEmpty() } ?: planB`，会出现「一道菜都没列出来，却有一份 B 方案
     * 的做法」这种自相矛盾的状态。
     */
    @Test
    fun `plan_a 为空列表时不越过它去取 plan_b`() {
        val dto = latest(
            planA = MealPlanDto(dishes = emptyList()),
            planB = MealPlanDto(dishes = listOf(dish("番茄汤", "煮开")))
        )

        assertTrue("planA 是空列表时不应列出菜名", dto.dishNames().isEmpty())
        assertTrue("菜名为空时做法映射也必须为空", dto.dishDetails().isEmpty())
    }

    @Test
    fun `两套方案都没有时给空结果而不是抛异常`() {
        val dto = latest()
        assertTrue(dto.dishNames().isEmpty())
        assertTrue(dto.dishDetails().isEmpty())
        assertNull(dto.dishDetails()["随便"])
    }

    @Test
    fun `空白菜名不进映射`() {
        val dto = latest(planA = MealPlanDto(dishes = listOf(
            dish("  ", "无名"), dish("土豆丝", "切丝")
        )))

        assertEquals(listOf("土豆丝"), dto.dishNames())
        assertEquals(setOf("土豆丝"), dto.dishDetails().keys)
    }

    @Test
    fun `菜名重复时保留第一道`() {
        val first = dish("土豆丝", "第一份做法")
        val dto = latest(planA = MealPlanDto(dishes = listOf(
            first, dish("土豆丝", "第二份做法")
        )))

        assertSame("重名时应保留与列表顺序一致的第一道", first, dto.dishDetails()["土豆丝"])
    }

    /** 菜名是做法映射的索引键，两侧都做 trim，否则带空格的名字永远查不到做法。 */
    @Test
    fun `菜名两端空白被裁掉后仍能查到做法`() {
        val dto = latest(planA = MealPlanDto(dishes = listOf(dish(" 土豆丝 ", "切丝"))))

        val name = dto.dishNames().single()
        assertEquals("土豆丝", name)
        assertEquals(listOf("切丝"), dto.dishDetails()[name]?.steps)
    }
}
