package org.smartfridgemagnet.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class RecipeTransferTest {

    @Test
    fun `三餐推荐全部进入后端渲染输入`() {
        val recipe = RecipeResult(
            breakfast = listOf("早1", "早2", "早3"),
            lunch = listOf("午1", "午2", "午3"),
            dinner = listOf("晚1", "晚2", "晚3"),
            memberAdjustments = null
        )

        assertEquals(
            listOf(
                "早餐|早1", "早餐|早2", "早餐|早3",
                "午餐|午1", "午餐|午2", "午餐|午3",
                "晚餐|晚1", "晚餐|晚2", "晚餐|晚3"
            ),
            buildDishLines(recipe)
        )
    }
}
