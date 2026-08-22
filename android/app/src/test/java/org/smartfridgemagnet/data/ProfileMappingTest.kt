package org.smartfridgemagnet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 家庭画像上行映射的契约测试。
 *
 * 映射写错的表现是静默的：少映射一个字段不会有任何异常，只是模型再也看不到那条约束，
 * 而界面上「配好了」看不出区别。
 *
 * 只测纯转换，不碰网络。
 */
class ProfileMappingTest {

    private fun member(
        name: String = "爸爸",
        role: MemberRole = MemberRole.ADULT,
        goal: HealthGoal = HealthGoal.BALANCED,
        preferences: DietaryPreferences = DietaryPreferences()
    ) = FamilyMember(
        id = "m1", name = name, avatar = "👨", role = role, goal = goal, preferences = preferences
    )

    /**
     * 分隔符要收得宽。
     *
     * 用错分隔符的后果不是格式问题：整串会变成**一个**食材名，匹配不到任何真实食材，
     * 于是过敏校验静默失效。
     */
    @Test
    fun `parseFoodList 接受各种分隔符`() {
        val expected = listOf("花生", "芒果", "虾")
        listOf(
            "花生、芒果、虾",
            "花生，芒果，虾",
            "花生,芒果,虾",
            "花生；芒果；虾",
            "花生 芒果 虾",
            "花生　芒果　虾",           // 全角空格
            " 花生 、 芒果 ,虾 ",       // 混用 + 多余空白
        ).forEach { input ->
            assertEquals("输入 <$input> 应拆成 $expected", expected, parseFoodList(input))
        }
    }

    @Test
    fun `parseFoodList 去重去空并容纳空输入`() {
        assertEquals(listOf("花生"), parseFoodList("花生、花生、 花生"))
        assertEquals(emptyList<String>(), parseFoodList(""))
        assertEquals(emptyList<String>(), parseFoodList("、、  ,, ；"))
    }

    /** 过敏与忌口必须落到**不同**的后端字段：后端对两者的处置强度不同。 */
    @Test
    fun `过敏与忌口映射到不同字段`() {
        val dto = member(
            preferences = DietaryPreferences(
                allergies = listOf("花生"),
                dislikes = listOf("苦瓜")
            )
        ).toProfileMemberDto()

        assertEquals(listOf("花生"), dto.allergies)
        assertEquals(listOf("苦瓜"), dto.dislikesMain)
        // 若过敏被并进 dislikes，后端只会"尽量避开"而不会重试与拦截
        assertTrue("过敏不能混进忌口字段", "花生" !in dto.dislikesMain)
    }

    @Test
    fun `映射会清掉空白项`() {
        val dto = member(
            preferences = DietaryPreferences(
                allergies = listOf(" 花生 ", "", "   "),
                dislikes = listOf("苦瓜 ", "")
            )
        ).toProfileMemberDto()
        assertEquals(listOf("花生"), dto.allergies)
        assertEquals(listOf("苦瓜"), dto.dislikesMain)
    }

    /**
     * 只有「不辣」映射成口味忌口。
     *
     * 中辣/重辣是偏好而非禁忌，塞进 dislikes_taste 会让后端反过来避开辣菜 ——
     * 用户选了"重辣"却收到一份清淡菜单，且没有任何报错能解释。
     */
    @Test
    fun `辣度只在不辣时映射为口味忌口`() {
        val none = member(preferences = DietaryPreferences(spiceLevel = SpiceLevel.NONE))
            .toProfileMemberDto()
        assertEquals(listOf("辣"), none.dislikesTaste)

        listOf(SpiceLevel.MILD, SpiceLevel.MEDIUM, SpiceLevel.HOT).forEach { level ->
            val dto = member(preferences = DietaryPreferences(spiceLevel = level)).toProfileMemberDto()
            assertEquals("$level 不应产生口味忌口", emptyList<String>(), dto.dislikesTaste)
        }
    }

    /** 角色与健康目标进 health_notes：后端原样放进 prompt，自然语言比编码好用。 */
    @Test
    fun `角色与健康目标进 health_notes`() {
        val dto = member(role = MemberRole.ELDER, goal = HealthGoal.CONTROL_SALT)
            .toProfileMemberDto()
        assertTrue("应含角色：${dto.healthNotes}", "老年人" in dto.healthNotes)
        assertTrue("应含健康目标：${dto.healthNotes}", "控盐" in dto.healthNotes)
    }

    @Test
    fun `名字两端空白被清掉`() {
        assertEquals("爸爸", member(name = "  爸爸 ").toProfileMemberDto().name)
    }
}
