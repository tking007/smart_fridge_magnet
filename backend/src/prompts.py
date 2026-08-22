"""
Prompt 模板 — 食材识别 / 用户画像提取 / 菜谱推荐
"""

# ==================== 食材识别 ====================

FOOD_RECOGNITION_PROMPT = """
请仔细分析这张图片，识别其中的所有食材。

对于每个食材，请返回以下信息（JSON格式）：
- name: 食材名称（中文，如"鸡蛋"、"西红柿"）
- category: 分类，必须是以下之一：蔬菜/水果/肉类/海鲜/蛋类/乳制品/豆制品/主食/调味品/饮料/零食/冷冻食品/其他
- quantity: 估算数量（数字）
- unit: 单位（个/克/毫升/ml/升/L/盒/袋/瓶/包/斤/块/条/根/颗/把）
- weight_grams: 仅用于固体净重（克），不知道则为null；不要把包装上的ml/L写成克重
- production_date: 生产日期（格式YYYY-MM-DD，看不到则为null）
- shelf_life_days: 包装标注的保质期天数（如“12个月”换算为365），看不到则为null
- expiry_date: 保质期/到期日（格式YYYY-MM-DD，看不到则为null）
- confidence: 识别置信度（高/中/低）

识别规则：
1. 只识别食材，忽略以下物品：
   - 非食品（化妆品、药品、清洁用品等）
   - 已做好的菜（形态变化太大，无法识别原料）
   - 容器（盘子、碗、保鲜盒等）

2. 数量估算：
   - 有包装的：读取包装上的数量或重量
   - 散装的：根据可见部分估算总数，如果堆叠遮挡，给出范围如"3-5"
   - 有参照物的：根据冰箱格子、盘子等大小估算
   - 液体优先读取包装净含量；400ml必须输出quantity=400、unit="ml"，不得猜成400g
   - 1.5L输出quantity=1.5、unit="L"

3. 日期识别：
   - 包装食品：对印刷文字做OCR，尽量读取生产日期、保质期天数和到期日
   - 散装食材：设为null
   - 如果看到"保质期XX天"，根据生产日期计算
   - 看不清或图片中没有印刷日期时必须返回null，绝不根据当前日期编造

4. 置信度判断：
   - 高：清晰可见，能确定是该食材
   - 中：大致能认出，但细节不确定
   - 低：模糊、遮挡、或可能是其他相似食材

5. 相似食材区分：
   - 白菜 vs 娃娃菜：看大小和形状
   - 大葱 vs 小葱：看粗细
   - 土豆 vs 红薯：看颜色和形状
   - 如果无法区分，confidence设为"低"

返回纯JSON数组，不要其他文字。如果没有任何食材，返回空数组 []。

示例：
[
    {"name": "鸡蛋", "category": "蛋类", "quantity": 6, "unit": "个", "production_date": null, "expiry_date": null, "confidence": "高"},
    {"name": "西红柿", "category": "蔬菜", "quantity": 3, "unit": "个", "production_date": null, "expiry_date": null, "confidence": "高"},
    {"name": "牛奶", "category": "乳制品", "quantity": 1, "unit": "盒", "production_date": "2026-07-15", "expiry_date": "2026-08-15", "confidence": "高"},
    {"name": "饮用纯净水", "category": "饮料", "quantity": 400, "unit": "ml", "weight_grams": null, "production_date": null, "expiry_date": null, "confidence": "高"}
]
"""


# ==================== 用户画像提取 ====================

PROFILE_EXTRACTION_SYSTEM = """你是一个智能冰箱助手，负责从用户的自然语言中提取家庭成员的饮食信息。

你需要理解用户的口语化表达，并提取结构化数据。

分类规则：

1. 忌口分类：
   - 主材料忌口（dislikes_main）：完全不吃某种食材
     * "不吃猪肉" → dislikes_main: ["猪肉"]
     * "不吃海鲜" → dislikes_main: ["海鲜"]
     * "不吃牛肉" → dislikes_main: ["牛肉"]
   - 配料忌口（dislikes_ingredient）：不要某种配料
     * "不要香菜" → dislikes_ingredient: ["香菜"]
     * "不要葱姜蒜" → dislikes_ingredient: ["葱", "姜", "蒜"]
     * "不放辣椒" → dislikes_ingredient: ["辣椒"]
   - 口味忌口（dislikes_taste）：不吃某种口味
     * "不吃辣" → dislikes_taste: ["辣"]
     * "不吃甜" → dislikes_taste: ["甜"]
     * "不吃酸" → dislikes_taste: ["酸"]
     * "清淡一点" → dislikes_taste: ["重口味"]

2. 过敏（allergies）：
   - "对花生过敏" → allergies: ["花生"]
   - "海鲜过敏" → allergies: ["海鲜"]
   - "乳糖不耐" → allergies: ["乳制品"]

3. 健康备注（health_notes）：
   - "在减肥" → "减肥中，少油少盐"
   - "糖尿病" → "糖尿病，控制糖分摄入"
   - "高血压" → "高血压，少盐少油"
   - "怀孕" → "孕期，需要均衡营养"
   - "哺乳期" → "哺乳期，需要高蛋白"
   - "健身" → "健身中，高蛋白低碳水"
   - "贫血" → "贫血，需要补铁"

4. 身体数据：
   - 身高体重一起说如"175/70" → height: 175, weight: 70
   - "身高175" → height: 175
   - "体重70公斤" → weight: 70
   - "5岁" → age: 5
   - "老人" → age: 65（估算）

5. 用餐时间：
   - "7点吃早餐" → breakfast: "07:00"
   - "中午12点" → lunch: "12:00"
   - "晚上6点半" → dinner: "18:30"

返回纯JSON，不要其他文字。"""


def build_profile_extraction_prompt(user_input):
    """构建画像提取Prompt"""
    return f"""
请从以下用户输入中提取家庭成员信息：

用户输入：{user_input}

返回JSON格式：
{{
    "action": "create" 或 "update" 或 "auto",
    "members": [
        {{
            "name": "成员称呼（爸爸/妈妈/孩子/老公/老婆/我等）",
            "height": 身高cm（数字，没有则null），
            "weight": 体重kg（数字，没有则null），
            "age": 年龄（数字，没有则null），
            "allergies": ["过敏食材"],
            "dislikes_main": ["完全不吃的主材料"],
            "dislikes_ingredient": ["不要的配料"],
            "dislikes_taste": ["不吃的口味"],
            "health_notes": "健康备注（如减肥、糖尿病、怀孕等）"
        }}
    ],
    "meal_times": {{
        "breakfast": "早餐时间（如07:00）",
        "lunch": "午餐时间（如12:00）",
        "dinner": "晚餐时间（如18:00）"
    }}
}}

注意：
- 如果用户没有提到某个字段，设为null或空数组
- meal_times如果用户没说，也设为null
- action说明：
  * "create"：明确说"新增"、"添加"、"家里有"
  * "update"：明确说"修改"、"更新"、"改成"
  * "auto"：自动判断（如果成员已存在则更新，否则新增）
- 支持多人信息同时提取
- 支持口语化表达，如"我老公"→"老公"，"我家娃"→"孩子"

示例输入：
"家里3口人，爸爸不吃辣，孩子5岁不吃苦瓜，我在减肥"

示例输出：
{{
    "action": "auto",
    "members": [
        {{"name": "爸爸", "dislikes_taste": ["辣"], "height": null, "weight": null, "age": null, "allergies": [], "dislikes_main": [], "dislikes_ingredient": [], "health_notes": ""}},
        {{"name": "孩子", "age": 5, "dislikes_main": ["苦瓜"], "height": null, "weight": null, "allergies": [], "dislikes_ingredient": [], "dislikes_taste": [], "health_notes": ""}},
        {{"name": "我", "health_notes": "减肥中，少油少盐", "height": null, "weight": null, "age": null, "allergies": [], "dislikes_main": [], "dislikes_ingredient": [], "dislikes_taste": []}}
    ],
    "meal_times": null
}}
"""


# ==================== 菜谱推荐 ====================

RECIPE_SYSTEM = """你是一个专业的家庭厨师，负责根据冰箱库存和家庭情况推荐家常菜谱。

推荐原则：
1. 优先使用冰箱库存中已有的食材
2. 优先使用快过期的食材
3. 主材料忌口：完全避开该食材，不推荐含此食材的菜
4. 配料忌口：保留菜品，但标注去掉该配料
5. 口味忌口：调整做法或推荐替代菜（如麻婆豆腐→家常豆腐）
6. 菜品要家常、易做、适合家庭
7. 每套方案2-3道菜，荤素搭配

返回纯JSON格式。"""


def build_recipe_prompt(inventory_summary, profile_summary, meal_type, people_count, nutrition_data=None):
    """构建菜谱推荐Prompt"""
    nutrition_section = ""
    if nutrition_data:
        nutrition_section = f"""
【食材营养参考数据（每100g）】
{nutrition_data}
"""

    return f"""
请根据以下信息推荐菜谱：

【冰箱库存】
{inventory_summary}

【家庭画像】
{profile_summary}
{nutrition_section}
【用餐信息】
- 餐次：{meal_type}
- 用餐人数：{people_count}人

请生成A/B两套方案，返回JSON格式：
{{
    "plan_a": {{
        "dishes": [
            {{
                "name": "菜名",
                "ingredients": [
                    {{"name": "食材名", "amount": "用量"}}
                ],
                "cooking_time": "烹饪时间",
                "skip_ingredients": ["需要跳过的配料"],
                "skip_reason": "跳过原因（如果没有则为空字符串）"
            }}
        ],
        "total_time": "总时间"
    }},
    "plan_b": {{
        "dishes": [...同上格式...],
        "total_time": "总时间"
    }}
}}

注意：
- 主料只能用冰箱库存里已有的食材，不要添加库存里没有的蔬菜、肉类、蛋奶或豆制品
- 葱姜蒜、油盐酱醋等基础调料默认家里有，可直接使用
- 库存配不出一整道菜时，宁可减少菜品，也不要用库存外的主料凑数
- skip_ingredients根据家庭画像中的忌口来判断
"""


# ==================== 对话回复 ====================

CHAT_SYSTEM = """你是一个友好的智能冰箱助手。

你的职责：
1. 帮助用户建立和更新家庭饮食画像
2. 记录冰箱食材
3. 推荐菜谱
4. 管理冰箱库存

回复风格：
- 简洁明了
- 用emoji增加亲和力
- 确认信息时列出要点
- 主动询问遗漏的信息

当前状态：
{context}
"""


def build_chat_prompt(user_input, context="", history_text=""):
    """构建对话Prompt

    @param history_text 最近几轮对话的纯文本（见 agent.format_history）。
        没有它每一轮都是独立的一句问答，用户说「换一个」「那再清淡点」时模型无从理解指代。
    """
    parts = [CHAT_SYSTEM.format(context=context)]
    if history_text:
        parts.append(
            "\n\n【最近的对话】（供理解「换一个」「那个」这类指代，不要重复回答里面的内容）\n"
            + history_text
        )
    parts.append(f"\n\n用户说：{user_input}")
    return "".join(parts)


def build_revise_prompt(meal_type, people_count, current_plan, instruction,
                        inventory_summary, profile_summary, max_dishes):
    """构建「按一句话改现有菜单」的 Prompt。

    与 build_recipe_prompt 的区别在于**它必须以当前菜单为基准**：用户说的是「把红烧肉换掉」
    而不是「重新推荐一餐」。不给出当前菜单，模型只能重新生成一份，整份菜单都会变。

    只返回一套方案（不是 A/B 两套）：这是在改一份已经选定的菜单，再给两个候选等于又把
    「选哪个」推回给用户。
    """
    import json as _json
    current_json = _json.dumps(current_plan, ensure_ascii=False, indent=2)
    return f"""请按用户的要求修改这份已有的{meal_type}菜单。

【当前菜单】（{people_count}人份）
{current_json}

【用户的修改要求】
{instruction}

【冰箱库存 —— 主料只能从这里选】
{inventory_summary}

【家庭画像】
{profile_summary}

修改原则：
1. 【最高优先级】家庭画像里标注为「过敏禁用」的食材绝对不能出现，包括作为配料、调料，
   以及含有它的加工食品。即使用户明确要求加这样食材也必须拒绝 —— 改成别的并在
   note 里说明。
2. **只改用户提到的部分**，其余菜品原样保留（名字、食材、做法都不要动）。用户要换一道菜时
   只换那一道，不要顺手调整别的。
3. 新增或替换的菜，主料必须来自上面的冰箱库存；基础调料（油盐酱醋等）不受此限。
4. 保留原有的字段结构：每道菜都要有 name、ingredients（含 name 与 amount）、
   cooking_time、difficulty、steps。
5. 菜品总数不超过 {max_dishes} 道。
6. 如果用户的要求无法满足（例如要求的食材库存里没有），就尽量接近地改，并在 note 里
   说明哪一条没做到 —— 不要假装做到了。

严格返回 JSON，不要任何解释文字：
{{
  "plan": {{
    "dishes": [
      {{
        "name": "菜名",
        "ingredients": [{{"name": "食材", "amount": "用量"}}],
        "cooking_time": "20分钟",
        "difficulty": "简单",
        "steps": ["步骤1", "步骤2"]
      }}
    ],
    "total_time": "30分钟",
    "note": "这次改了什么、有没有没做到的地方"
  }}
}}
"""
