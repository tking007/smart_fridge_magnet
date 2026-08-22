"""
本地语义理解模块
关键词映射 + 模糊查询本地解析，减少 AI 调用
"""
import re
from typing import Optional, Dict, List, Any


# ==================== 意图关键词映射 ====================

# 营养查询意图
NUTRITION_INTENT = {
    # 蛋白质相关
    "蛋白质": {"field": "protein", "order": "DESC", "label": "高蛋白"},
    "蛋白": {"field": "protein", "order": "DESC", "label": "高蛋白"},
    "高蛋白": {"field": "protein", "order": "DESC", "label": "高蛋白"},
    "补充蛋白": {"field": "protein", "order": "DESC", "label": "高蛋白"},
    "增肌": {"field": "protein", "order": "DESC", "label": "高蛋白"},

    # 脂肪相关
    "低脂": {"field": "fat", "order": "ASC", "label": "低脂肪"},
    "脂肪低": {"field": "fat", "order": "ASC", "label": "低脂肪"},
    "减脂": {"field": "fat", "order": "ASC", "label": "低脂肪"},
    "脱脂": {"field": "fat", "order": "ASC", "label": "低脂肪"},

    # 热量相关
    "低热量": {"field": "energy_kcal", "order": "ASC", "label": "低热量"},
    "热量低": {"field": "energy_kcal", "order": "ASC", "label": "低热量"},
    "减肥": {"field": "energy_kcal", "order": "ASC", "label": "低热量"},
    "瘦身": {"field": "energy_kcal", "order": "ASC", "label": "低热量"},
    "控制体重": {"field": "energy_kcal", "order": "ASC", "label": "低热量"},

    # 铁相关
    "补铁": {"field": "fe", "order": "DESC", "label": "高铁"},
    "铁": {"field": "fe", "order": "DESC", "label": "高铁"},
    "贫血": {"field": "fe", "order": "DESC", "label": "高铁"},

    # 钙相关
    "补钙": {"field": "ca", "order": "DESC", "label": "高钙"},
    "钙": {"field": "ca", "order": "DESC", "label": "高钙"},
    "骨骼": {"field": "ca", "order": "DESC", "label": "高钙"},

    # 维生素相关
    "维生素a": {"field": "vitamin_a", "order": "DESC", "label": "高维生素A"},
    "维a": {"field": "vitamin_a", "order": "DESC", "label": "高维生素A"},
    "护眼": {"field": "vitamin_a", "order": "DESC", "label": "高维生素A"},
    "维生素c": {"field": "vitamin_c", "order": "DESC", "label": "高维生素C"},
    "维c": {"field": "vitamin_c", "order": "DESC", "label": "高维生素C"},
    "美白": {"field": "vitamin_c", "order": "DESC", "label": "高维生素C"},

    # 膳食纤维
    "膳食纤维": {"field": "dietary_fiber", "order": "DESC", "label": "高纤维"},
    "纤维": {"field": "dietary_fiber", "order": "DESC", "label": "高纤维"},
    "便秘": {"field": "dietary_fiber", "order": "DESC", "label": "高纤维"},
    "通便": {"field": "dietary_fiber", "order": "DESC", "label": "高纤维"},

    # 胆固醇
    "低胆固醇": {"field": "cholesterol", "order": "ASC", "label": "低胆固醇"},
    "胆固醇低": {"field": "cholesterol", "order": "ASC", "label": "低胆固醇"},

    # 碳水
    "低碳": {"field": "cho", "order": "ASC", "label": "低碳水"},
    "碳水低": {"field": "cho", "order": "ASC", "label": "低碳水"},
    "生酮": {"field": "cho", "order": "ASC", "label": "低碳水"},
}

# 食材分类意图
CATEGORY_INTENT = {
    "蔬菜": "蔬菜",
    "菜": "蔬菜",
    "青菜": "蔬菜",
    "水果": "水果",
    "果": "水果",
    "肉类": "肉类",
    "肉": "肉类",
    "猪肉": "肉类",
    "牛肉": "肉类",
    "鸡肉": "肉类",
    "海鲜": "海鲜",
    "鱼": "海鲜",
    "虾": "海鲜",
    "蛋类": "蛋类",
    "蛋": "蛋类",
    "鸡蛋": "蛋类",
    "乳制品": "乳制品",
    "奶": "乳制品",
    "牛奶": "乳制品",
    "豆制品": "豆制品",
    "豆腐": "豆制品",
    "主食": "主食",
    "米": "主食",
    "面": "主食",
    "馒头": "主食",
    "调味品": "调味品",
    "调料": "调味品",
    "饮料": "饮料",
    "喝的": "饮料",
    "零食": "零食",
    "小吃": "零食",
    "冷冻食品": "冷冻食品",
    "速冻": "冷冻食品",
}

# 库存查询意图
INVENTORY_INTENT = {
    "快过期": {"sort": "expiry", "filter": "expiring_soon"},
    "快坏了": {"sort": "expiry", "filter": "expiring_soon"},
    "要过期": {"sort": "expiry", "filter": "expiring_soon"},
    "快到期": {"sort": "expiry", "filter": "expiring_soon"},
    "最新": {"sort": "created", "order": "DESC"},
    "刚买": {"sort": "created", "order": "DESC"},
    "库存多": {"sort": "quantity", "order": "DESC"},
    "库存少": {"sort": "quantity", "order": "ASC"},
    "快没了": {"sort": "quantity", "order": "ASC"},
}

# 动作意图
ACTION_INTENT = {
    "推荐": "recommend",
    "吃什么": "recommend",
    "做啥": "recommend",
    "做啥菜": "recommend",
    "做饭": "recommend",
    "菜谱": "recommend",
    "怎么做": "recipe",
    "做法": "recipe",
    "怎么吃": "recipe",
    "营养": "nutrition",
    "热量": "nutrition",
    "有多少": "query",
    "还剩": "query",
    "还有多少": "query",
    "有没有": "query",
    "加入": "add",
    "添加": "add",
    "买了": "add",
    "放入": "add",
    "用掉": "deduct",
    "吃了": "deduct",
    "消耗": "deduct",
    "扣减": "deduct",
}

# 场景意图
SCENE_INTENT = {
    "早餐": {"meal_type": "早餐", "time": "morning"},
    "早饭": {"meal_type": "早餐", "time": "morning"},
    "午餐": {"meal_type": "午餐", "time": "noon"},
    "午饭": {"meal_type": "午餐", "time": "noon"},
    "晚餐": {"meal_type": "晚餐", "time": "evening"},
    "晚饭": {"meal_type": "晚餐", "time": "evening"},
    "夜宵": {"meal_type": "夜宵", "time": "night"},
    "宵夜": {"meal_type": "夜宵", "time": "night"},
    "加餐": {"meal_type": "加餐", "time": "any"},
    "零食": {"meal_type": "零食", "time": "any"},
}

# 人群意图
PEOPLE_INTENT = {
    "孕妇": {"special": "pregnant", "needs": ["叶酸", "铁", "钙", "蛋白质"]},
    "产妇": {"special": "postpartum", "needs": ["蛋白质", "铁", "钙"]},
    "儿童": {"special": "child", "needs": ["钙", "蛋白质", "维生素"]},
    "孩子": {"special": "child", "needs": ["钙", "蛋白质", "维生素"]},
    "宝宝": {"special": "baby", "needs": ["钙", "蛋白质"]},
    "老人": {"special": "elderly", "needs": ["钙", "蛋白质", "维生素D"]},
    "老年人": {"special": "elderly", "needs": ["钙", "蛋白质", "维生素D"]},
    "健身": {"special": "fitness", "needs": ["蛋白质", "低碳水"]},
    "减脂": {"special": "fat_loss", "needs": ["高蛋白", "低脂", "低碳水"]},
    "素食": {"special": "vegetarian", "exclude": ["肉类", "海鲜"]},
}


def parse_query(text: str) -> Dict[str, Any]:
    """
    解析用户查询，返回结构化意图

    Args:
        text: 用户输入文本

    Returns:
        {
            "type": "nutrition" | "category" | "inventory" | "action" | "scene" | "people" | "unknown",
            "intent": {...},  # 具体意图数据
            "keywords": [...],  # 匹配到的关键词
            "confidence": "high" | "medium" | "low",
            "original": "原始文本"
        }
    """
    text = text.strip().lower()
    result = {
        "type": "unknown",
        "intent": {},
        "keywords": [],
        "confidence": "low",
        "original": text
    }

    # 1. 检查营养查询意图
    for keyword, intent in NUTRITION_INTENT.items():
        if keyword in text:
            result["type"] = "nutrition"
            result["intent"] = intent
            result["keywords"].append(keyword)
            result["confidence"] = "high"
            break

    # 2. 检查食材分类意图
    if result["type"] == "unknown":
        for keyword, category in CATEGORY_INTENT.items():
            if keyword in text:
                result["type"] = "category"
                result["intent"] = {"category": category}
                result["keywords"].append(keyword)
                result["confidence"] = "high"
                break

    # 3. 检查库存查询意图
    if result["type"] == "unknown":
        for keyword, intent in INVENTORY_INTENT.items():
            if keyword in text:
                result["type"] = "inventory"
                result["intent"] = intent
                result["keywords"].append(keyword)
                result["confidence"] = "high"
                break

    # 4. 检查动作意图
    if result["type"] == "unknown":
        for keyword, action in ACTION_INTENT.items():
            if keyword in text:
                result["type"] = "action"
                result["intent"] = {"action": action}
                result["keywords"].append(keyword)
                result["confidence"] = "medium"
                break

    # 5. 检查场景意图
    for keyword, scene in SCENE_INTENT.items():
        if keyword in text:
            result["scene"] = scene
            result["keywords"].append(keyword)
            if result["type"] == "unknown":
                result["type"] = "scene"
                result["intent"] = scene
                result["confidence"] = "medium"
            break

    # 6. 检查人群意图
    for keyword, people in PEOPLE_INTENT.items():
        if keyword in text:
            result["people"] = people
            result["keywords"].append(keyword)
            if result["type"] == "unknown":
                result["type"] = "people"
                result["intent"] = people
                result["confidence"] = "medium"
            break

    # 7. 提取数字（可能是数量查询）
    numbers = re.findall(r'\d+', text)
    if numbers:
        result["numbers"] = [int(n) for n in numbers]

    return result


def build_nutrition_query(intent: Dict) -> Dict:
    """
    根据营养意图构建数据库查询参数

    Args:
        intent: parse_query 返回的 intent

    Returns:
        {"order_by": "protein", "order": "DESC", "limit": 5}
    """
    return {
        "order_by": intent.get("field", "protein"),
        "order": intent.get("order", "DESC"),
        "limit": 5
    }


def build_category_query(intent: Dict) -> Dict:
    """
    根据分类意图构建数据库查询参数

    Args:
        intent: parse_query 返回的 intent

    Returns:
        {"category": "蔬菜"}
    """
    return {
        "category": intent.get("category")
    }


def get_suggestions(text: str, inventory: List[Dict] = None) -> List[str]:
    """
    根据用户输入生成建议

    Args:
        text: 用户输入
        inventory: 当前库存列表（可选）

    Returns:
        建议列表
    """
    suggestions = []
    parsed = parse_query(text)

    if parsed["type"] == "nutrition":
        label = parsed["intent"].get("label", "")
        suggestions.append(f"查询{label}食物")
        suggestions.append(f"查看库存中{label}的食材")

    elif parsed["type"] == "category":
        category = parsed["intent"].get("category", "")
        suggestions.append(f"查看{category}类库存")
        suggestions.append(f"添加{category}类食材")

    elif parsed["type"] == "inventory":
        if parsed["intent"].get("filter") == "expiring_soon":
            suggestions.append("查看即将过期的食材")
            suggestions.append("推荐用掉这些食材的菜谱")

    elif parsed["type"] == "action":
        action = parsed["intent"].get("action", "")
        if action == "recommend":
            if inventory:
                # 根据库存推荐
                expiring = [i for i in inventory if i.get("days_left") is not None and i["days_left"] <= 3]
                if expiring:
                    names = "、".join([i["name"] for i in expiring[:3]])
                    suggestions.append(f"用{names}做菜")
            suggestions.append("推荐晚餐菜谱")
            suggestions.append("推荐快手菜")

    return suggestions


def is_simple_query(text: str) -> bool:
    """
    判断是否是简单查询（可以本地处理，不需要调用 AI）

    Args:
        text: 用户输入

    Returns:
        True 表示简单查询
    """
    parsed = parse_query(text)

    # 营养查询、分类查询、库存查询都是简单查询
    if parsed["type"] in ["nutrition", "category", "inventory"]:
        return True

    # 包含明确动作的简单查询
    if parsed["type"] == "action":
        action = parsed["intent"].get("action", "")
        if action in ["query", "add", "deduct"]:
            return True

    return False


def extract_food_name(text: str) -> Optional[str]:
    """
    从文本中提取食材名称

    Args:
        text: 用户输入，如"鸡蛋还有多少"、"加入牛奶"

    Returns:
        食材名称或 None
    """
    # 移除常见动词和助词
    remove_words = [
        "还有", "多少", "加入", "添加", "买了", "放入", "用掉", "吃了",
        "有没有", "剩余", "库存", "查询", "查看", "看看", "一下",
        "的", "了", "吗", "呢", "吧", "啊"
    ]

    cleaned = text
    for word in remove_words:
        cleaned = cleaned.replace(word, "")

    cleaned = cleaned.strip()

    # 如果剩余文本很短，可能是食材名
    if 0 < len(cleaned) <= 10:
        return cleaned

    return None


# ==================== 测试 ====================

if __name__ == "__main__":
    test_cases = [
        "蛋白质高的食物",
        "补铁的",
        "低热量",
        "蔬菜类",
        "快过期的",
        "还有多少鸡蛋",
        "推荐晚餐",
        "孩子吃什么好",
        "健身餐",
        "鸡蛋",
    ]

    print("=== 语义理解测试 ===\n")

    for text in test_cases:
        result = parse_query(text)
        print(f"输入: {text}")
        print(f"  类型: {result['type']}")
        print(f"  意图: {result['intent']}")
        print(f"  关键词: {result['keywords']}")
        print(f"  置信度: {result['confidence']}")
        print()
