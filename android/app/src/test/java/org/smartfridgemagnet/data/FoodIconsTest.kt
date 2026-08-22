package org.smartfridgemagnet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FoodIconsTest {
    @Test
    fun `高频食材和常见别名都有图标`() {
        listOf(
            "土豆", "苹果", "香蕉", "大米", "面条", "酸奶",
            "羊肉", "鱼", "鸭肉", "饮用水"
        ).forEach { assertNotNull(it, foodIconRes(it)) }

        assertEquals(foodIconRes("番茄"), foodIconRes("西红柿"))
        assertEquals(foodIconRes("鸡蛋"), foodIconRes("有机土鸡蛋"))
        assertEquals(foodIconRes("饮用水"), foodIconRes("饮用纯净水"))
        assertEquals(foodIconRes("鱼"), foodIconRes("冷冻鲈鱼"))
    }

    @Test
    fun `别名匹配不会把牛油果当成牛肉`() {
        assertNotNull(foodIconRes("牛油果"))
        assertNotEquals(foodIconRes("牛肉"), foodIconRes("牛油果"))
    }

    @Test
    fun `手动新增目录覆盖全部一百三十四个资源`() {
        assertTrue(foodIconItems.size >= 134)
    }
}
