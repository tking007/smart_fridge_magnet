package org.smartfridgemagnet.data

import org.junit.Assert.assertEquals
import org.junit.Test

class IngredientCategoryTest {
    @Test
    fun `分类器覆盖饮料零食且未知项归其他`() {
        assertEquals(IngredientCategory.BEVERAGE, classifyIngredient("可口可乐"))
        assertEquals(IngredientCategory.BEVERAGE, classifyIngredient("矿泉水"))
        assertEquals(IngredientCategory.BEVERAGE, classifyIngredient("饮用纯净水"))
        assertEquals(IngredientCategory.DAIRY, classifyIngredient("全脂牛奶"))
        assertEquals(IngredientCategory.SNACK, classifyIngredient("原味薯片"))
        assertEquals(IngredientCategory.SEAFOOD, classifyIngredient("三文鱼"))
        assertEquals(IngredientCategory.SOY, classifyIngredient("油豆腐"))
        assertEquals(IngredientCategory.FRUIT, classifyIngredient("蓝莓"))
        assertEquals(IngredientCategory.STAPLE, classifyIngredient("燕麦"))
        assertEquals(IngredientCategory.MEAT, classifyIngredient("羊肉"))
        assertEquals(IngredientCategory.OTHER, classifyIngredient("神秘食材"))
    }

    @Test
    fun `存档迁移只修已知旧格式并保留明确分类`() {
        val explicit = ingredient("神秘食材", IngredientCategory.MEAT, "盒", 2.0)
        assertEquals(Freshness.UNKNOWN, migrateStoredIngredient(explicit).freshness)

        val milk = ingredient("全脂牛奶", IngredientCategory.BEVERAGE, "盒", 1.0)
        assertEquals(IngredientCategory.DAIRY, migrateStoredIngredient(milk).category)

        val friedTofu = ingredient("油豆腐", IngredientCategory.SEASONING, "个", 6.0)
        assertEquals(IngredientCategory.SOY, migrateStoredIngredient(friedTofu).category)

        val legacyWeight = migrateStoredIngredient(
            ingredient("面粉", IngredientCategory.STAPLE, "g", 500.0)
        )
        assertEquals("个", legacyWeight.quantityUnit)
        assertEquals(0.0, legacyWeight.quantityAmount, 0.0)
        assertEquals(500.0, legacyWeight.weightGrams!!, 0.0)
    }

    @Test
    fun `编辑数量只保留当前计量方式`() {
        val original = ingredient("油豆腐", IngredientCategory.SOY, "个", 6.0)
            .copy(weightGrams = 300.0)

        val counted = original.withEditedAmount(AmountMode.COUNT, 8.0)
        assertEquals("8个", counted.quantity)
        assertEquals(8.0, counted.quantityAmount, 0.0)
        assertEquals(null, counted.weightGrams)

        // 件数单位不限于「个」：库存里大量是盒、袋、瓶这类包装数。
        val boxed = original.withEditedAmount(AmountMode.COUNT, 3.0, "盒")
        assertEquals("3盒", boxed.quantity)
        assertEquals(3.0, boxed.quantityAmount, 0.0)
        assertEquals("盒", boxed.quantityUnit)
        assertEquals(AmountMode.COUNT, boxed.amountMode())
        assertEquals(null, boxed.weightGrams)

        val weighed = original.withEditedAmount(AmountMode.WEIGHT, 500.0)
        assertEquals("500g", weighed.quantity)
        assertEquals(0.0, weighed.quantityAmount, 0.0)
        assertEquals(500.0, weighed.weightGrams!!, 0.0)
        assertEquals(AmountMode.WEIGHT, weighed.amountMode())
    }

    @Test
    fun `容量编辑统一保存为毫升并保留容量模式`() {
        val original = ingredient("饮用纯净水", IngredientCategory.BEVERAGE, "瓶", 1.0)

        val millilitres = original.withEditedAmount(AmountMode.VOLUME, 400.0, "ml")
        assertEquals("400ml", millilitres.quantity)
        assertEquals(400.0, millilitres.quantityAmount, 0.0)
        assertEquals("ml", millilitres.quantityUnit)
        assertEquals(AmountMode.VOLUME, millilitres.amountMode())

        val litres = original.withEditedAmount(AmountMode.VOLUME, 1.5, "L")
        assertEquals("1.5L", litres.quantity)
        assertEquals(1500.0, litres.quantityAmount, 0.0)
        assertEquals("ml", litres.quantityUnit)
        assertEquals(null, litres.weightGrams)
    }

    private fun ingredient(
        name: String,
        category: IngredientCategory,
        unit: String,
        amount: Double
    ) = Ingredient(
        name = name,
        quantity = "$amount$unit",
        quantityAmount = amount,
        quantityUnit = unit,
        freshness = Freshness.NORMAL,
        category = category
    )
}
