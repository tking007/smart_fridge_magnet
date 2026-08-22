package org.smartfridgemagnet.network

import org.smartfridgemagnet.data.Freshness
import org.smartfridgemagnet.data.Ingredient
import org.smartfridgemagnet.data.IngredientCategory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 本地模型与后端 DTO 的映射测试。
 *
 * 后端对库存分类做**封闭集合**校验，未知值返回 400（错误码 3004），而批量新增是原子的 —— 一条分类
 * 不合法会让整批上传失败，表现是「单条能加、多条一起就全失败」，从 App 上看不出是分类的问题。
 *
 * 集合本身在这里再写一遍是刻意的：它是**后端的契约**，不是本地的实现细节。直接引用 BackendDto 里的
 * 私有常量等于用实现验证实现，后端改了集合这个测试也不会响。
 */
class BackendDtoTest {

    @Test
    fun `后端配置只有 HTTPS 地址才视为可用`() {
        assertFalse(SfmBackendConfig("", "token", "dev001").configured)
        assertFalse(SfmBackendConfig("http://example.test", "token", "dev001").configured)
        assertTrue(SfmBackendConfig("https://example.test", "token", "dev001").configured)
    }

    /** 后端 GET /api/inventory/categories 返回的 13 项，与 backend/src/database.py 的种子一致。 */
    private val backendCategories = setOf(
        "蔬菜", "水果", "肉类", "海鲜", "蛋类", "乳制品", "豆制品",
        "主食", "调味品", "饮料", "零食", "冷冻食品", "其他"
    )

    @Test
    fun `所有本地分类都映射到后端合法值`() {
        IngredientCategory.values().forEach { category ->
            val mapped = category.toBackendCategory()
            assertTrue(
                "本地分类 $category 映射成了后端不接受的 \"$mapped\"，" +
                    "批量上传会整批失败（HTTP 400 / code 3004）",
                mapped in backendCategories
            )
        }
    }

    @Test
    fun `后端全部合法分类都能映射回本地枚举`() {
        backendCategories.forEach { name ->
            // 映射表缺项会静默落到 OTHER，这里只要求"能映射"，
            // 但对本地确实有对应枚举的几项做精确断言（见下一个测试）。
            assertTrue("分类 $name 未在映射表中", name.toIngredientCategory() in IngredientCategory.values())
        }
    }

    @Test
    fun `关键分类的映射方向正确`() {
        assertEquals(IngredientCategory.VEGETABLE, "蔬菜".toIngredientCategory())
        assertEquals(IngredientCategory.MEAT, "肉类".toIngredientCategory())
        assertEquals(IngredientCategory.SEAFOOD, "海鲜".toIngredientCategory())
        assertEquals(IngredientCategory.EGG, "蛋类".toIngredientCategory())
        assertEquals(IngredientCategory.DAIRY, "乳制品".toIngredientCategory())
        assertEquals(IngredientCategory.STAPLE, "主食".toIngredientCategory())
        assertEquals(IngredientCategory.SEASONING, "调味品".toIngredientCategory())
        assertEquals(IngredientCategory.FRUIT, "水果".toIngredientCategory())
        assertEquals(IngredientCategory.SOY, "豆制品".toIngredientCategory())
        assertEquals(IngredientCategory.BEVERAGE, "饮料".toIngredientCategory())
        assertEquals(IngredientCategory.SNACK, "零食".toIngredientCategory())
        assertEquals(IngredientCategory.FROZEN, "冷冻食品".toIngredientCategory())
    }

    @Test
    fun `未知或空分类退化成其他而不是抛异常`() {
        assertEquals(IngredientCategory.OTHER, "外星食材".toIngredientCategory())
        assertEquals(IngredientCategory.OTHER, null.toIngredientCategory())
        assertEquals(IngredientCategory.OTHER, "".toIngredientCategory())
    }

    /** 阈值必须与后端 expiring_soon 的判定（days_left <= 3）一致，否则同一批食材两处结论不同。 */
    @Test
    fun `新鲜度按剩余天数推导且阈值与后端一致`() {
        assertEquals(Freshness.EXPIRING, item(daysLeft = 0).toIngredient().freshness)
        assertEquals(Freshness.EXPIRING, item(daysLeft = 3).toIngredient().freshness)
        assertEquals(Freshness.NORMAL, item(daysLeft = 4).toIngredient().freshness)
        assertEquals(Freshness.NORMAL, item(daysLeft = 7).toIngredient().freshness)
        assertEquals(Freshness.FRESH, item(daysLeft = 8).toIngredient().freshness)
        // 无保质期信息时不做臆测
        assertEquals(Freshness.UNKNOWN, item(daysLeft = null).toIngredient().freshness)
    }

    @Test
    fun `数量文本按整数显示`() {
        assertEquals("3个", item(quantity = 3.0, unit = "个").toIngredient().quantity)
        assertEquals("500g", item(quantity = 500.0, unit = "g").toIngredient().quantity)
        assertEquals("1500g", item(quantity = 1.5, unit = "kg").toIngredient().quantity)
        assertEquals("2个 · 300g", item(quantity = 2.0, weightGrams = 300.0).toIngredient().quantity)
    }

    @Test
    fun `容量和识别可信度在 DTO 往返中保持真实值`() {
        val ingredient = InventoryItemDto(
            name = "饮用纯净水", category = "饮料", quantity = 1.5,
            unit = "L", confidence = "中"
        ).toIngredient(recognitionDerived = true)

        assertEquals("1.5L", ingredient.quantity)
        assertEquals(1500.0, ingredient.quantityAmount, 0.0)
        assertEquals("ml", ingredient.quantityUnit)
        assertEquals("中", ingredient.recognitionConfidence)

        val dto = ingredient.toInventoryItemDto()
        assertEquals(1500.0, dto.quantity, 0.0)
        assertEquals("ml", dto.unit)
        assertEquals("中", dto.confidence)
    }

    @Test
    fun `云端设备快照新增字段保持可空兼容`() {
        val json = """
            {"device_id":"dev-v019","device_status":{"battery_level":88,
             "applied_version":17,"sleep_mode":true,"wake_slots":[425,695,1085],
             "wake_fallback_minutes":1440,"poll_interval_seconds":86400,
             "status_age_minutes":12}}
        """.trimIndent()
        val status = gson.fromJson(json, SyncStatusDto::class.java).deviceStatus!!

        assertEquals(17, status.appliedVersion)
        assertEquals(true, status.sleepMode)
        assertEquals(listOf(425, 695, 1085), status.wakeSlots)
        assertEquals(1440, status.wakeFallbackMinutes)
        assertEquals(86400, status.pollIntervalSeconds)
        assertEquals(12, status.statusAgeMinutes)

        val legacy = gson.fromJson(
            """{"device_id":"legacy","device_status":{"battery_level":50}}""",
            SyncStatusDto::class.java
        ).deviceStatus!!
        assertEquals(null, legacy.wakeSlots)
        assertEquals(null, legacy.statusAgeMinutes)
    }

    @Test
    fun `设备预览 URL 显式请求竖向方向`() {
        val client = SfmBackendClient(
            SfmBackendConfig("https://example.test", "app-token", "dev 01")
        )

        assertEquals(
            "https://example.test/api/devices/dev 01/display/preview.png" +
                "?version=17&name=17_today.film&w=240&orientation=portrait",
            client.previewUrl("17_today.film", 17, 240, "portrait")
        )
    }

    @Test
    fun `版式更新响应包含完整菜单重渲染结果`() {
        val dto = gson.fromJson(
            """{"device_id":"dev01","layout":"list_big","success":true,"display_updated":true,"display_update_detail":"已重新发布四张菜单"}""",
            DevicePrefsDto::class.java
        )

        assertEquals("list_big", dto.layout)
        assertTrue(dto.displayUpdated)
        assertEquals("已重新发布四张菜单", dto.displayUpdateDetail)
    }

    @Test
    fun `本地食材转后端项时不伪造保质期`() {
        val dto = Ingredient(
            name = "牛奶", quantity = "2盒", quantityAmount = 2.0, quantityUnit = "盒",
            freshness = Freshness.EXPIRING, category = IngredientCategory.DAIRY,
            weightGrams = 500.0, shelfLifeDays = 7
        ).toInventoryItemDto()

        assertEquals("牛奶", dto.name)
        assertEquals(2.0, dto.quantity, 0.0)
        assertEquals("盒", dto.unit)
        assertEquals(0, dto.id)
        // freshness 是本地的派生值，反推日期等于编数据 —— 后端会把它当"无保质期"处理，这更诚实
        assertEquals(null, dto.expiryDate)
        assertEquals(null, dto.productionDate)
        assertEquals(500.0, dto.weightGrams)
        assertEquals(7, dto.shelfLifeDays)
    }

    @Test
    fun `旧六类枚举存档可迁移读取`() {
        assertEquals(IngredientCategory.DAIRY,
            gson.fromJson("\"EGG_DAIRY\"", IngredientCategory::class.java))
        assertEquals(IngredientCategory.STAPLE,
            gson.fromJson("\"GRAIN\"", IngredientCategory::class.java))
    }

    private fun item(
        quantity: Double = 1.0,
        unit: String = "个",
        weightGrams: Double? = null,
        daysLeft: Int? = null
    ) = InventoryItemDto(
        id = 1, name = "测试", category = "蔬菜",
        quantity = quantity, unit = unit, weightGrams = weightGrams, daysLeft = daysLeft
    )

    // ==================== 从真实 JSON 反序列化 ====================
    //
    // 下面这组一律**用 Gson 从 JSON 字符串解析**，不手 new DTO。
    // 手 new 的对象测不到任何 @SerializedName —— 蛇形键名写错时手 new 照样通过，
    // 而真机上那个字段恒为默认值（0 / null / 空列表），表现是"接口通了但界面上什么都没有"。

    private val gson = com.google.gson.Gson()

    /** GET /api/recommend/latest 的成功响应（按后端真实响应裁剪，保留嵌套结构）。 */
    private val recommendLatestJson = """
        {
          "success": true,
          "plan_id": 17,
          "meal_type": "dinner",
          "meal_label": "晚餐",
          "people_count": 3,
          "created_at": "2025-02-11 10:20:31",
          "selected_plan": "A",
          "age_minutes": 42,
          "stale": false,
          "plan_a": {
            "total_time": "50分钟",
            "total_difficulty": "中等",
            "dishes": [
              {
                "name": "番茄炒蛋",
                "cooking_time": "15分钟",
                "difficulty": "简单",
                "nutrition_highlight": "优质蛋白 + 番茄红素",
                "steps": ["鸡蛋打散", "先炒鸡蛋再下番茄"],
                "skip_ingredients": ["辣椒"],
                "skip_reason": "爷爷忌辛辣",
                "ingredients": [
                  {"name": "鸡蛋", "amount": "3个"},
                  {"name": "番茄", "amount": "2个"}
                ]
              },
              {
                "name": "上海青炒香菇",
                "cooking_time": "12分钟",
                "difficulty": "简单",
                "ingredients": [{"name": "上海青", "amount": "300g"}]
              }
            ]
          },
          "plan_b": {
            "total_time": "35分钟",
            "dishes": [{"name": "蒜蓉西兰花", "ingredients": []}]
          }
        }
    """.trimIndent()

    @Test
    fun `RecommendLatestDto 解析嵌套的方案结构`() {
        val dto = gson.fromJson(recommendLatestJson, RecommendLatestDto::class.java)

        assertTrue(dto.success)
        assertEquals(17, dto.planId)
        assertEquals("dinner", dto.mealType)
        assertEquals("晚餐", dto.mealLabel)
        assertEquals(3, dto.peopleCount)
        assertEquals("2025-02-11 10:20:31", dto.createdAt)
        assertEquals("A", dto.selectedPlan)
        assertEquals(42, dto.ageMinutes)
        assertEquals(false, dto.stale)

        val planA = dto.planA!!
        assertEquals("50分钟", planA.totalTime)
        assertEquals("中等", planA.totalDifficulty)
        assertEquals(2, planA.dishes.size)

        val first = planA.dishes[0]
        assertEquals("番茄炒蛋", first.name)
        assertEquals("15分钟", first.cookingTime)
        assertEquals("简单", first.difficulty)
        assertEquals("优质蛋白 + 番茄红素", first.nutritionHighlight)
        assertEquals(listOf("鸡蛋打散", "先炒鸡蛋再下番茄"), first.steps)
        assertEquals(listOf("辣椒"), first.skipIngredients)
        assertEquals("爷爷忌辛辣", first.skipReason)
        assertEquals(2, first.ingredients.size)
        assertEquals("鸡蛋", first.ingredients[0].name)
        assertEquals("3个", first.ingredients[0].amount)

        // 模型没给的可选字段解析成 null，而不是空串
        assertEquals(null, planA.dishes[1].nutritionHighlight)
        assertEquals(null, dto.error)
        assertEquals(null, dto.code)
    }

    /**
     * dishNames() 只取 name。
     *
     * **绝不能把 cooking_time 拼进去**：菜名会被 UI 当作"查做法"的查询词原样传给模型，
     * 带上时长后模型收到的是"红烧肉（40分钟）怎么做"，答案会往时长上跑。
     */
    @Test
    fun `dishNames 只取菜名且优先 plan_a`() {
        val dto = gson.fromJson(recommendLatestJson, RecommendLatestDto::class.java)
        assertEquals(listOf("番茄炒蛋", "上海青炒香菇"), dto.dishNames())
    }

    @Test
    fun `dishNames 过滤空白菜名`() {
        val json = """
            {"success": true,
             "plan_a": {"dishes": [
               {"name": "红烧肉"}, {"name": "   "}, {"name": ""}, {"name": " 清蒸鱼 "}
             ]}}
        """.trimIndent()
        val dto = gson.fromJson(json, RecommendLatestDto::class.java)
        assertEquals(listOf("红烧肉", "清蒸鱼"), dto.dishNames())
    }

    @Test
    fun `dishNames 在 plan_a 为空时回落 plan_b`() {
        val json = """
            {"success": true, "plan_a": null,
             "plan_b": {"dishes": [{"name": "蒜蓉西兰花"}]}}
        """.trimIndent()
        val dto = gson.fromJson(json, RecommendLatestDto::class.java)
        assertEquals(listOf("蒜蓉西兰花"), dto.dishNames())
    }

    @Test
    fun `dishNames 在两份方案都为空时返回空列表`() {
        val dto = gson.fromJson("""{"success": true}""", RecommendLatestDto::class.java)
        assertEquals(null, dto.planA)
        assertEquals(null, dto.planB)
        assertEquals(emptyList<String>(), dto.dishNames())
    }

    /**
     * 「库里还没有菜谱」走 404 + code 4004，这是**正常业务态**。
     * code 丢了就只能靠比对错误文案来区分"还没生成"与"真出错"，后端改一个字就失配。
     */
    @Test
    fun `RecommendLatestDto 保留 404 响应里的业务码`() {
        val json = """
            {"success": false, "error": "暂无已生成的菜谱，请先生成一次", "code": 4004}
        """.trimIndent()
        val dto = gson.fromJson(json, RecommendLatestDto::class.java)

        assertEquals(false, dto.success)
        assertEquals(4004, dto.code)
        assertEquals("暂无已生成的菜谱，请先生成一次", dto.error)
        assertEquals(0, dto.planId)
    }

    // ---- manifest ----

    @Test
    fun `ManifestDto 解析完整的四槽位响应`() {
        val json = """
            {
              "version": 32,
              "film_url": "https://host/api/devices/dev001/display/film/32_today.film",
              "sha256": "a1b2c3",
              "size": 209120,
              "force": false,
              "items": [
                {"slot": "today", "name": "32_today.film",
                 "url": "https://host/f/32_today.film", "size": 209120, "sha256": "a1b2c3"},
                {"slot": "breakfast", "name": "32_breakfast.film",
                 "url": "https://host/f/32_breakfast.film", "size": 209120, "sha256": "d4e5f6"},
                {"slot": "lunch", "name": "32_lunch.film",
                 "url": "https://host/f/32_lunch.film", "size": 209120, "sha256": "070809"},
                {"slot": "dinner", "name": "32_dinner.film",
                 "url": "https://host/f/32_dinner.film", "size": 209120, "sha256": "0a0b0c"}
              ]
            }
        """.trimIndent()
        val dto = gson.fromJson(json, ManifestDto::class.java)

        assertEquals(32, dto.version)
        assertEquals(209120, dto.size)
        assertEquals("a1b2c3", dto.sha256)
        assertEquals(false, dto.force)
        assertEquals(4, dto.items.size)
        assertEquals(listOf("today", "breakfast", "lunch", "dinner"), dto.items.map { it.slot })
        assertEquals("32_breakfast.film", dto.items[1].name)
        assertEquals("https://host/f/32_breakfast.film", dto.items[1].url)
        assertEquals(209120, dto.items[1].size)
        assertEquals("d4e5f6", dto.items[1].sha256)
    }

    /**
     * 不带 items 的响应必须解析成**空列表而不是 null**。
     *
     * 后端在 manifest 序列化后超过固件上限 2048 字节时会主动丢掉 items 只留旧字段（超限时固件拒收
     * 整份 manifest，等于设备完全停止更新）。这是一条**正常路径**，而声明成非空 List 却拿到 null，
     * 会在遍历时 NPE 且崩在离解析很远的地方。
     */
    @Test
    fun `ManifestDto 缺少 items 时是空列表而不是 null`() {
        val json = """
            {"version": 7, "film_url": "https://host/f/7_today.film",
             "sha256": "abc", "size": 209120, "force": true}
        """.trimIndent()
        val dto = gson.fromJson(json, ManifestDto::class.java)

        assertEquals(7, dto.version)
        assertTrue(dto.force)
        assertNotNull("缺 items 是正常降级路径，不能是 null", dto.items)
        assertEquals(0, dto.items.size)
    }

    // ---- 用餐时间表 ----

    /**
     * enabled 是 **int 0/1** 而不是 boolean（后端直出数据库列）。
     * 声明成 Boolean 会让 Gson 抛 JsonSyntaxException —— 这条测试就是防这个。
     */
    @Test
    fun `MealScheduleDto 把 enabled 解析成 int`() {
        val json = """
            {"schedule": [
               {"meal_type": "breakfast", "label": "早餐", "meal_time": "07:30", "enabled": 1},
               {"meal_type": "lunch", "label": "午餐", "meal_time": "12:00", "enabled": 1},
               {"meal_type": "dinner", "label": "晚餐", "meal_time": "18:30", "enabled": 0}
             ],
             "daily_publish_time": "06:30", "lead_minutes": 30, "auto_publish": true}
        """.trimIndent()
        val dto = gson.fromJson(json, MealScheduleDto::class.java)

        assertEquals(3, dto.schedule.size)
        assertEquals(30, dto.leadMinutes)
        assertEquals("06:30", dto.dailyPublishTime)
        assertTrue(dto.autoPublish)
        assertEquals("breakfast", dto.schedule[0].mealType)
        assertEquals("早餐", dto.schedule[0].label)
        assertEquals("07:30", dto.schedule[0].mealTime)
        assertEquals(1, dto.schedule[0].enabled)
        assertEquals("停用的餐次 enabled 是 0，不是 false", 0, dto.schedule[2].enabled)
        // 成功响应里没有 success 字段，判失败要用 `success == false || error != null`
        assertEquals(null, dto.success)
        assertEquals(null, dto.error)
    }

    /** schedule 可以是 0~3 项：一餐都没配过时不能炸，也不能按下标当作早/午/晚。 */
    @Test
    fun `MealScheduleDto 允许空的时间表`() {
        val dto = gson.fromJson(
            """{"schedule": [], "lead_minutes": 0, "auto_publish": false}""",
            MealScheduleDto::class.java
        )
        assertEquals(0, dto.schedule.size)
        assertEquals(0, dto.leadMinutes)
        assertEquals(false, dto.autoPublish)
    }

    /**
     * PUT 的校验失败响应（400 + code 1001）。错误文案已写明是第几项、哪里不合法，
     * 客户端应原样透传 —— 换成自造的"保存失败"会把唯一有用的信息扔掉。
     */
    @Test
    fun `MealScheduleDto 解析 PUT 的校验失败响应`() {
        val json = """
            {"success": false,
             "error": "schedule[1] 用餐时间非法 '12:70'，需为 HH:MM（00:00–23:59）",
             "code": 1001}
        """.trimIndent()
        val dto = gson.fromJson(json, MealScheduleDto::class.java)

        assertEquals(false, dto.success)
        assertEquals(1001, dto.code)
        assertTrue(dto.error!!.contains("12:70"))
        // 失败响应里没有 schedule，必须是空列表而不是 null
        assertNotNull(dto.schedule)
        assertEquals(0, dto.schedule.size)
    }

    /**
     * 提前量的合法域随 GET 一起返回，客户端**不该自己写死一份**。
     *
     * 老后端不带这两个字段时才用 DTO 的默认值兜底 —— 断言默认值不是 0：下界等于调度器的检查周期，
     * 更小的提前量会错过发布窗口，等于静默关掉自动下发。
     */
    @Test
    fun `MealScheduleDto 带出提前量的合法域`() {
        val dto = gson.fromJson(
            """{"schedule": [], "lead_minutes": 30, "auto_publish": true,
                "lead_minutes_min": 5, "lead_minutes_max": 240}""",
            MealScheduleDto::class.java
        )
        assertEquals(5, dto.leadMinutesMin)
        assertEquals(240, dto.leadMinutesMax)

        val legacy = gson.fromJson("""{"lead_minutes": 30}""", MealScheduleDto::class.java)
        assertTrue("老后端兜底的下界不能是 0", legacy.leadMinutesMin > 0)
        assertTrue(legacy.leadMinutesMax > legacy.leadMinutesMin)
    }

    /**
     * PUT 的请求体：**没改的那几项必须整个键都不出现**。
     *
     * 后端用 `'auto_publish' in data` 判断给没给、再 `isinstance(value, bool)` 校验，发一个显式的
     * `"auto_publish": null` 会被判成「给了个非布尔值」而整体退 400，客户端那侧只看到「保存失败」。
     *
     * 这条行为靠的是 Gson 默认跳过 null 字段 —— 是默认值、不是我们配置出来的，所以更要钉住：
     * 哪天有人开了 serializeNulls，只改提前量这条路径会全线 400。
     */
    @Test
    fun `MealSchedulePutDto 不序列化未改动的字段`() {
        val onlyLead = gson.toJson(MealSchedulePutDto(leadMinutes = 45))
        assertEquals("""{"lead_minutes":45}""", onlyLead)

        val onlyAutoOff = gson.toJson(MealSchedulePutDto(autoPublish = false))
        assertEquals(
            "auto_publish = false 是有意义的输入，不能因为是假值就被丢掉",
            """{"auto_publish":false}""", onlyAutoOff
        )

        val onlyDaily = gson.toJson(MealSchedulePutDto(dailyPublishTime = "06:20"))
        assertEquals("""{"daily_publish_time":"06:20"}""", onlyDaily)

        val all = gson.toJson(
            MealSchedulePutDto(
                schedule = listOf(MealScheduleTimeDto("breakfast", "07:30")),
                dailyPublishTime = "06:30",
                leadMinutes = 30,
                autoPublish = true
            )
        )
        assertTrue(all.contains(""""meal_type":"breakfast""""))
        assertTrue(all.contains(""""meal_time":"07:30""""))
        assertTrue(all.contains(""""lead_minutes":30"""))
        assertTrue(all.contains(""""daily_publish_time":"06:30"""))
        assertTrue(all.contains(""""auto_publish":true"""))
        // label / enabled 不在请求体里：PUT 不接受它们（后端按 meal_type 补 label，
        // 写入是整行替换、enabled 回到默认 1）。
        assertTrue("请求体里不该出现 enabled", !all.contains("enabled"))
    }

    // ---- 版式 ----

    /**
     * 六个版式键与 orientation 必须原样解析，portrait 尤其重要：
     * UI 依据它请求自然竖向预览并提示设备需要旋转。
     */
    @Test
    fun `LayoutChoiceDto 解析六个版式的 orientation`() {
        val json = """
            {"layouts": [
              {"key": "classic", "label": "经典横版",
               "description": "红色标题条 + 分类色块，信息量最均衡（默认）", "orientation": "landscape"},
              {"key": "bold", "label": "大字横版",
               "description": "字号加大约 40%，三五米外也能看清", "orientation": "landscape"},
              {"key": "minimal", "label": "极简横版",
               "description": "去掉大色块，只留黑白与一条红色分隔线", "orientation": "landscape"},
              {"key": "compact", "label": "紧凑信息版",
               "description": "同屏展示更多内容", "orientation": "landscape"},
              {"key": "colorful", "label": "六色分区版",
               "description": "设备原生六色", "orientation": "landscape"},
              {"key": "portrait", "label": "竖版长列表",
               "description": "把设备竖起来看，一屏能排更多菜", "orientation": "portrait"}
            ]}
        """.trimIndent()
        val dto = gson.fromJson(json, LayoutListDto::class.java)

        assertEquals(6, dto.layouts.size)
        assertEquals(
            listOf("classic", "bold", "minimal", "compact", "colorful", "portrait"),
            dto.layouts.map { it.key }
        )
        assertEquals(
            listOf("landscape", "landscape", "landscape", "landscape", "landscape", "portrait"),
            dto.layouts.map { it.orientation }
        )
        val portrait = dto.layouts.last()
        assertEquals("竖版长列表", portrait.label)
        assertEquals("portrait", portrait.orientation)
        assertTrue(portrait.description!!.isNotBlank())
    }

    /** orientation 缺失时按 landscape 处理 —— 横版是默认，猜错方向只会多一句不必要的提示。 */
    @Test
    fun `LayoutChoiceDto 缺少 orientation 时默认横版`() {
        val dto = gson.fromJson("""{"key": "classic", "label": "经典横版"}""", LayoutChoiceDto::class.java)
        assertEquals("landscape", dto.orientation)
    }

    // ---- 手动下发 ----

    /**
     * `skipped:true` 是**成功但跳过**，不是失败。
     *
     * 把它当失败会让用户以为下发出错了并反复点，而后端每次都会照样跳过；
     * 反过来把 detail 换成自造的"已下发"，又会让人以为真的重新渲染了。
     */
    @Test
    fun `PublishTriggerDto 的 skipped 是成功而不是失败`() {
        val json = """
            {"success": true, "meal": "lunch", "devices": ["dev001"],
             "skipped": true, "detail": "午餐画面 12 分钟前已下发过，未重复发布"}
        """.trimIndent()
        val dto = gson.fromJson(json, PublishTriggerDto::class.java)

        assertTrue("skipped 分支仍然是 success=true", dto.success)
        assertTrue(dto.skipped)
        assertEquals("lunch", dto.meal)
        assertEquals(listOf("dev001"), dto.devices)
        assertEquals("午餐画面 12 分钟前已下发过，未重复发布", dto.detail)
        // 跳过时没有 published 段，必须是空列表而不是 null
        assertNotNull(dto.published)
        assertEquals(0, dto.published.size)
        assertEquals(null, dto.error)
    }

    @Test
    fun `PublishTriggerDto 解析正常下发的响应`() {
        val json = """
            {"success": true, "meal": "dinner", "devices": ["dev001"],
             "published": ["dev001 -> v33（4 张）"], "skipped": false}
        """.trimIndent()
        val dto = gson.fromJson(json, PublishTriggerDto::class.java)

        assertTrue(dto.success)
        assertEquals(false, dto.skipped)
        assertEquals(listOf("dev001 -> v33（4 张）"), dto.published)
    }

    // ---- 对话 ----
    //
    // /api/chat 有两个坑，两个都只在运行时才炸，所以必须用真实 JSON 钉住：
    //   1) 回复文本的字段名随分支变（本地快路径是 response，走 AI 是 reply）；
    //   2) data 的**形状**随 action 变（recommend 是对象，inventory_query / confirm 是数组）。

    @Test
    fun `ChatResponseDto 本地快路径的回复在 response 字段里`() {
        // 后端 app.py 的 is_simple_query 分支：没有 action，文本在 response，不打模型
        val json = """
            {"handled_locally": true,
             "intent": {"type": "category", "intent": {"category": "蔬菜"},
                        "keywords": ["菜"], "confidence": "high", "original": "有什么快手菜"},
             "response": "找到 2 个蔬菜类食材：\n- 上海青 × 1把\n- 娃娃菜 × 2颗\n",
             "results": [{"id": 1, "name": "上海青", "category": "蔬菜",
                          "quantity": 1.0, "unit": "把"}]}
        """.trimIndent()
        val dto = gson.fromJson(json, ChatResponseDto::class.java)

        assertTrue(dto.handledLocally)
        assertEquals(null, dto.action)
        assertEquals(null, dto.reply)
        assertTrue("回复文本必须能从 response 里取到", dto.replyText().contains("上海青"))
        assertEquals("本地路径不会产生方案", null, dto.recommendPlan())
    }

    /**
     * 本地快路径存在**回复为空串**的真实分支（`type == "inventory"` 且过滤条件不是
     * 「即将过期」时，后端压根不往 response 里写东西）。UI 必须自己准备替代文案。
     */
    @Test
    fun `ChatResponseDto 空回复如实返回空串而不是崩掉`() {
        val json = """{"handled_locally": true, "intent": {"type": "inventory"},
                       "response": "", "results": []}"""
        val dto = gson.fromJson(json, ChatResponseDto::class.java)

        assertEquals("", dto.replyText())
    }

    @Test
    fun `ChatResponseDto 走AI时回复在 reply 字段里且能取出新方案`() {
        val json = """
            {"reply": "🍽️ 晚餐推荐（3人份）\n━━━ A方案 ━━━\n🥘 番茄炒蛋 🟢",
             "action": "recommend",
             "data": {"plan_id": 12, "meal_type": "晚餐", "people_count": 3,
                      "plan_a": {"dishes": [
                          {"name": "番茄炒蛋",
                           "ingredients": [{"name": "番茄", "amount": "2个"}],
                           "cooking_time": "10分钟", "difficulty": "简单",
                           "skip_ingredients": [], "skip_reason": ""},
                          {"name": "上汤娃娃菜", "ingredients": [],
                           "cooking_time": "15分钟", "difficulty": "简单"}],
                       "total_time": "25分钟", "total_difficulty": "简单"},
                      "plan_b": null},
             "handled_locally": false}
        """.trimIndent()
        val dto = gson.fromJson(json, ChatResponseDto::class.java)

        assertEquals(false, dto.handledLocally)
        assertEquals("recommend", dto.action)
        assertTrue(dto.replyText().contains("番茄炒蛋"))

        val plan = dto.recommendPlan()
        assertNotNull("action=recommend 时必须能解析出方案", plan)
        assertEquals(12, plan!!.planId)
        assertEquals("晚餐", plan.mealType)
        assertEquals(3, plan.peopleCount)
        // 摘要只取菜名，口径与 RecommendLatestDto.dishNames() 一致
        assertEquals(listOf("番茄炒蛋", "上汤娃娃菜"), plan.dishNames())
    }

    /**
     * action=inventory_query 时 `data` 是**数组**。
     *
     * 把 data 声明成方案对象会让这条路径抛
     * `JsonSyntaxException: Expected BEGIN_OBJECT but was BEGIN_ARRAY`，而它连模型都不打。
     */
    @Test
    fun `ChatResponseDto 的 data 是数组时也不会解析失败`() {
        val json = """
            {"reply": "🧊 冰箱里有 1 种食材：\n【蛋类】\n  • 鸡蛋 × 6个",
             "action": "inventory_query",
             "data": [{"id": 1, "name": "鸡蛋", "category": "蛋类",
                       "quantity": 6.0, "unit": "个", "days_left": 12}],
             "handled_locally": false}
        """.trimIndent()
        val dto = gson.fromJson(json, ChatResponseDto::class.java)

        assertEquals("inventory_query", dto.action)
        assertTrue(dto.replyText().contains("鸡蛋"))
        assertEquals("库存查询不产生方案", null, dto.recommendPlan())
    }

    /** 没生成方案的 action 一律不能被当成"有新菜单"——那会让下发按钮发出一份不存在的菜单。 */
    @Test
    fun `ChatResponseDto 只有 recommend 才算产生了新方案`() {
        listOf(
            """{"reply": "请问今天几个人吃饭？", "action": "ask_people_count"}""",
            """{"reply": "🧊 冰箱是空的，没法推荐菜谱哦！", "action": "inventory_empty"}""",
            """{"reply": "抱歉，生成菜谱时出了点问题，请稍后再试。", "action": "recommend_error"}""",
            """{"reply": "今天天气不错呀", "action": "chat"}"""
        ).forEach { json ->
            val dto = gson.fromJson(json, ChatResponseDto::class.java)

            assertEquals("action=${dto.action} 不该产生方案", null, dto.recommendPlan())
            assertTrue("失败分支也必须有可展示的原文", dto.replyText().isNotBlank())
        }
    }

    // ==================== 语音识别 ====================

    @Test
    fun `AsrResponseDto 映射 snake_case 字段`() {
        val json = """
            {"success": true, "text": "今天想吃点辣的", "empty": false,
             "language": "zh", "duration_ms": 1830}
        """.trimIndent()
        val dto = gson.fromJson(json, AsrResponseDto::class.java)
        assertTrue(dto.success)
        assertEquals("今天想吃点辣的", dto.text)
        assertEquals(false, dto.empty)
        assertEquals("zh", dto.language)
        // duration_ms 写成 durationMs 会静默变 0，而它只用在日志里 —— 没有任何界面
        // 会因此出错，所以只能靠断言守住
        assertEquals(1830, dto.durationMs)
    }

    /**
     * 识别出空串是**成功**响应（静音、或按住又立刻松手）。
     *
     * 客户端必须按 `empty` 这个字段判断而不是自己看 text 是否为空：两处口径就会有一处
     * 先漂。而把它当失败处理，用户会看到一条错误提示，以为语音功能坏了。
     */
    @Test
    fun `AsrResponseDto 空结果仍是 success`() {
        val json = """{"success": true, "text": "", "empty": true, "language": "zh",
                       "duration_ms": 420}"""
        val dto = gson.fromJson(json, AsrResponseDto::class.java)
        assertTrue("空结果不是失败", dto.success)
        assertTrue(dto.empty)
        assertEquals("", dto.text)
    }

    @Test
    fun `AsrResponseDto 失败响应带 error 且 success 为 false`() {
        val json = """{"success": false, "error": "语音识别失败：网关返回 HTTP 401", "code": 1006}"""
        val dto = gson.fromJson(json, AsrResponseDto::class.java)
        assertEquals(false, dto.success)
        assertTrue(dto.error!!.contains("401"))
        // 缺省值必须是安全的：失败响应里没有 text/empty，反序列化后不能变成 null
        assertEquals("", dto.text)
        assertEquals(false, dto.empty)
    }
}
