package org.smartfridgemagnet.network

import org.smartfridgemagnet.data.AmountMode
import org.smartfridgemagnet.data.Freshness
import org.smartfridgemagnet.data.Ingredient
import org.smartfridgemagnet.data.IngredientCategory
import org.smartfridgemagnet.data.migrateStoredIngredient
import org.smartfridgemagnet.data.withEditedAmount
import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class InventorySyncIdentityTest {
    @Test
    fun `同步身份保留单位并兼容旧克重单位`() {
        assertTrue(sameInventoryIdentity(" 牛奶 ", "盒", "牛奶", "盒"))
        assertFalse(sameInventoryIdentity("牛奶", "盒", "牛奶", "瓶"))
        assertTrue(sameInventoryIdentity("面粉", "g", "面粉", "个"))
        assertTrue(sameInventoryIdentity("面粉", "克", "面粉", "个"))
    }

    /**
     * `g → 个` 的归一化**依赖一个前提**：本地库存里不存在 `quantityUnit = "g"` 的条目。
     *
     * 这个前提由三处入口共同保证（后端映射、旧存档迁移、编辑数量），克重一律进 `weightGrams` 而
     * `quantityUnit` 恒为「个」。前提若被打破，归一化会把「牛奶 2个」和「牛奶 500g」当成同一样
     * 东西：删一条会把云端两条一起删，对账时两条本地条目映到同一个远端 id、后写覆盖先写，而返回
     * 的计数还是「成功」的样子。
     *
     * 所以这里钉住前提本身，而不是归一化的行为。
     */
    @Test
    fun `本地库存的单位永不为克重_归一化的前提`() {
        // 入口 1：后端条目 → 本地。后端仍可能用遗留的 g 单位。
        val fromBackend = InventoryItemDto(
            id = 1, name = "面粉", quantity = 500.0, unit = "g", category = "主食"
        ).toIngredient()
        assertEquals("个", fromBackend.quantityUnit)
        assertEquals(500.0, fromBackend.weightGrams!!, 0.001)
        assertEquals(0.0, fromBackend.quantityAmount, 0.001)

        // 入口 2：旧存档迁移。老版本 App 存过 quantityUnit = "克"。
        val migrated = migrateStoredIngredient(
            sampleIngredient(name = "面粉", unit = "克", amount = 500.0)
        )
        assertEquals("个", migrated.quantityUnit)
        assertEquals(500.0, migrated.weightGrams!!, 0.001)

        // 入口 3：用户在库存页按克重编辑。
        val edited = sampleIngredient(name = "面粉")
            .withEditedAmount(AmountMode.WEIGHT, 300.0)
        assertEquals("个", edited.quantityUnit)
        assertEquals(300.0, edited.weightGrams!!, 0.001)
        assertEquals(0.0, edited.quantityAmount, 0.001)

        // 按个数编辑同样不会留下克重单位。
        val byCount = sampleIngredient(name = "鸡蛋").withEditedAmount(AmountMode.COUNT, 6.0)
        assertEquals("个", byCount.quantityUnit)
        assertEquals(null, byCount.weightGrams)
    }

    /** 非克重单位必须原样保留 —— 「盒」和「瓶」是真的不同东西，不能被归一化吃掉。 */
    @Test
    fun `非克重单位参与身份判定`() {
        assertFalse(sameInventoryIdentity("牛奶", "盒", "牛奶", "袋"))
        assertFalse(sameInventoryIdentity("酸奶", "瓶", "酸奶", "个"))
        assertTrue(sameInventoryIdentity("酸奶", "瓶", "酸奶", "瓶"))
    }

    @Test
    fun `仅首次空库存从当前后端恢复`() {
        val scopeA = inventorySyncScope(" https://example.test/ ")
        val scopeB = inventorySyncScope("https://other.test/")
        assertEquals("https://example.test", scopeA)
        assertTrue(shouldHydrateInventory(true, scopeA, emptySet()))
        assertFalse(shouldHydrateInventory(false, scopeA, emptySet()))
        assertFalse(shouldHydrateInventory(true, scopeA, setOf(scopeA)))
        assertTrue(shouldHydrateInventory(true, scopeB, setOf(scopeA)))
        assertFalse(shouldHydrateInventory(true, scopeA, setOf(scopeA, scopeB)))
        assertFalse(shouldHydrateInventory(true, "", emptySet()))
    }

    @Test
    fun `空库存仅在完成首次恢复后允许覆盖云端`() {
        val scope = "https://example.test"
        assertTrue(canReconcileInventory(false, scope, emptySet()))
        assertFalse(canReconcileInventory(true, scope, emptySet()))
        assertTrue(canReconcileInventory(true, scope, setOf(scope)))
        assertFalse(canReconcileInventory(false, "", setOf(scope)))
    }

    @Test
    fun `全量对账以本地四项覆盖云端旧库存和重复项`() {
        val local = listOf(
            ingredient("土豆", 4.0),
            ingredient("鸡蛋", 8.0),
            ingredient("牛奶", 2.0),
            ingredient("油豆腐", 3.0),
        )
        val remote = listOf(
            remote(1, "土豆", 1.0),
            remote(2, "鸡蛋", 2.0),
            remote(3, "鸡蛋", 6.0),
            remote(4, "牛奶", 1.0),
            remote(5, "旧白菜", 2.0),
            remote(6, "旧牛肉", 1.0),
        )

        val plan = planInventoryReconciliation(local, remote)

        assertEquals(listOf("油豆腐"), plan.add.map { it.name })
        assertEquals(setOf(1, 2, 4), plan.update.map { it.first }.toSet())
        assertEquals(setOf(3, 5, 6), plan.deleteIds.toSet())
    }

    private fun sampleIngredient(
        name: String,
        unit: String = "个",
        amount: Double = 1.0
    ) = Ingredient(
        name = name,
        quantity = "$amount$unit",
        quantityAmount = amount,
        quantityUnit = unit,
        freshness = Freshness.NORMAL,
        category = IngredientCategory.OTHER,
    )

    private fun ingredient(name: String, quantity: Double) = Ingredient(
        name = name,
        quantity = "${quantity.toInt()}个",
        quantityAmount = quantity,
        quantityUnit = "个",
        freshness = Freshness.NORMAL,
        category = IngredientCategory.OTHER,
    )

    private fun remote(id: Int, name: String, quantity: Double) = InventoryItemDto(
        id = id,
        name = name,
        category = "其他",
        quantity = quantity,
        unit = "个",
    )
}
