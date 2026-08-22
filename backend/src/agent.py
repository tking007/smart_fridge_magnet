"""
Agent 模块 — 处理对话、提取画像、推荐菜谱
增强版：更智能的意图识别、更丰富的画像提取、更精准的菜谱推荐
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from .ai_client import (
    MAX_COMPLETION_TOKENS,
    RECIPE_MAX_COMPLETION_TOKENS,
    call_ai_text,
    call_ai_text_with_json,
    parse_json_payload,
    stream_ai_text,
)
from .prompts import (
    PROFILE_EXTRACTION_SYSTEM,
    build_profile_extraction_prompt,
    RECIPE_SYSTEM,
    build_chat_prompt,
    build_revise_prompt
)
from . import database as db
from . import meals
from .logger import get_logger, StageTimer

logger = get_logger('agent')


# ==================== 意图识别增强 ====================

# 意图关键词权重
INTENT_KEYWORDS = {
    "profile": {
        "keywords": ["家里", "几口人", "不吃", "忌口", "身高", "体重", "过敏", "减肥",
                      "糖尿病", "高血压", "孕妇", "哺乳", "老人", "小孩", "宝宝",
                      "不喜欢", "讨厌", "爱吃", "喜欢", "偏好", "口味"],
        "weight": 1.5  # 画像意图权重更高
    },
    "inventory": {
        "keywords": ["冰箱", "库存", "有什么", "还有什么", "看看", "查看", "还剩",
                      "多少", "快过期", "快坏了", "新鲜"],
        "weight": 1.0
    },
    "recommend": {
        "keywords": ["吃什么", "做饭", "做菜", "菜谱", "推荐", "晚饭", "午饭", "早饭",
                      "晚餐", "午餐", "早餐", "夜宵", "加餐", "想吃", "做啥",
                      "怎么吃", "搭配", "营养", "健康"],
        "weight": 1.2
    },
    "confirm": {
        "keywords": ["选A", "选B", "确认", "就这个", "好的", "可以", "行",
                      "选第一个", "选第二个"],
        "weight": 2.0  # 确认意图优先级最高
    },
    "help": {
        "keywords": ["帮助", "怎么用", "功能", "你能做什么", "说明", "教程"],
        "weight": 0.8
    }
}

# 餐次关键词
MEAL_KEYWORDS = {
    "早餐": ["早餐", "早饭", "早上", "早晨", "早"],
    "午餐": ["午餐", "午饭", "中午", "午"],
    "晚餐": ["晚餐", "晚饭", "晚上", "晚"],
    "夜宵": ["夜宵", "宵夜", "夜"],
    "加餐": ["加餐", "零食", "下午茶"]
}

# 人数提取模式
PEOPLE_PATTERNS = [
    r'(\d+)\s*[个人人]',
    r'[一家]\s*(\d+)\s*口',
    r'(\d+)\s*人份',
    r'(\d+)\s*位',
]


def detect_intent(user_input: str) -> str:
    """
    检测用户意图（增强版）

    Args:
        user_input: 用户输入

    Returns:
        str: intent类型
    """
    scores = {}
    for intent, config in INTENT_KEYWORDS.items():
        score = 0
        for keyword in config['keywords']:
            if keyword in user_input:
                score += config['weight']
        if score > 0:
            scores[intent] = score

    if scores:
        return max(scores, key=scores.get)

    return "general"


#: 三餐之外的餐次落到哪一餐。
#
# 设备只有早/午/晚三个画面槽位（menu_render.MEAL_STYLES / manifest 的 4 个 item），
# meals.meal_key() 也明确把「下午茶」「夜宵」判为 None —— 它们没有对应的版式。
# 但 MEAL_KEYWORDS 与钟点推断都会产出这两个值，落库后表现为：这一餐既不会被自动下发，
# 在 App 上按餐次读取也读不到（/api/recommend/latest 用 meals.meal_key 校验），
# 而生成本身是成功的。用户看到的就是"生成了但什么都没发生"。
#
# 映射按「能安排的下一顿」来定，而不是就近取整：
#   夜宵（21 点后）→ 早餐，深夜能安排的下一顿是明天早饭；落到晚餐会生成一份
#                    已经过去的餐次，还可能立刻被下发到屏幕上。
#   加餐/下午茶     → 晚餐，下午问"吃什么"通常是在想晚饭。
_MEAL_OUT_OF_RANGE_FALLBACK = {
    '夜宵': '早餐',
    '宵夜': '早餐',
    '加餐': '晚餐',
    '下午茶': '晚餐',
    '零食': '晚餐',
}

#: MEAL_KEYWORDS 摊平后按关键词长度降序，供 [parse_meal_info] 匹配。
#
# 为什么要排序：这张表里既有「下午茶」也有「午」，而按 dict 的迭代顺序「午餐」排在
# 「加餐」之前 —— 于是"下午茶推荐"命中的是"午"，被判成午餐。短关键词（午/晚/夜）
# 是为了兜住"晚上吃啥"这类省略说法，不能删；只能让长的先赢。
#
# 排序在模块导入时算一次。不这么做的话，同一个词在「关键词匹配」与
# [normalize_meal_type] 两条入口上会得到不同答案，而两处看着都对。
_MEAL_KEYWORD_RULES = sorted(
    ((keyword, meal_type)
     for meal_type, keywords in MEAL_KEYWORDS.items()
     for keyword in keywords),
    key=lambda pair: -len(pair[0]),
)


def normalize_meal_type(raw: str) -> str:
    """把任意写法的餐次收敛到早餐 / 午餐 / 晚餐（中文标签）。

    返回中文而不是 breakfast/lunch/dinner：库里 meal_plan.meal_type 与送进模型的 prompt
    都用中文，归一化的落点必须与它们一致，否则只是把越界值换成了另一种越界值。

    入参可以是规范别名（"午饭"）、越界餐次（"夜宵"）或时间词（"中午"）。三者都要走通
    **同一张关键词表**：本函数与 [parse_meal_info] 是两个入口，同一个词在两边给出不同
    答案时两处代码看着都对，而表现是「同样的话有时生成到别的餐次」。
    """
    text = str(raw or '').strip()
    if not text:
        return '晚餐'

    key = meals.meal_key(text)
    if key:
        return meals.meal_label(key)

    # 先查越界餐次的直接落点（"夜宵" / "下午茶"），再退回关键词表覆盖时间词（"中午"）。
    candidate = _MEAL_OUT_OF_RANGE_FALLBACK.get(text)
    if candidate is None:
        for keyword, meal_type in _MEAL_KEYWORD_RULES:
            if keyword in text:
                # 关键词表本身也会命中越界餐次（"加餐"），再过一次落点表。
                candidate = _MEAL_OUT_OF_RANGE_FALLBACK.get(meal_type, meal_type)
                break
    return candidate or '晚餐'


def parse_meal_info(user_input: str) -> Dict:
    """
    解析用餐信息（增强版）

    Args:
        user_input: 用户输入

    Returns:
        dict: {"meal_type": str, "people_count": int}
        meal_type 恒为早餐 / 午餐 / 晚餐之一（见 [normalize_meal_type]）。
    """
    info = {}

    # 解析餐次：长关键词优先（理由见 _MEAL_KEYWORD_RULES）
    for keyword, meal_type in _MEAL_KEYWORD_RULES:
        if keyword in user_input:
            info['meal_type'] = meal_type
            break

    # 如果没指定，根据时间判断。三个分界点对应"下一顿是哪一顿"：
    # 10 点前还能安排早饭，14 点前是午饭，之后都在想晚饭；21 点后归一化会落到明天早餐。
    if 'meal_type' not in info:
        hour = datetime.now().hour
        if hour < 10:
            info['meal_type'] = '早餐'
        elif hour < 14:
            info['meal_type'] = '午餐'
        elif hour < 21:
            info['meal_type'] = '晚餐'
        else:
            info['meal_type'] = '夜宵'

    # 无论来自关键词还是钟点，都收敛到三餐 —— 越界餐次会让这一餐既不被下发、
    # 也读不回来，而生成过程看起来完全成功。
    info['meal_type'] = normalize_meal_type(info['meal_type'])

    for pattern in PEOPLE_PATTERNS:
        match = re.search(pattern, user_input)
        if match:
            info['people_count'] = int(match.group(1))
            break

    return info


# ==================== 上下文管理 ====================

def get_context() -> str:
    """获取当前上下文"""
    members = db.get_all_members()
    inventory_result = db.get_inventory(page_size=1000)
    inventory = inventory_result.get('items', [])
    expiring = db.get_expiring_items(days=3)

    context_parts = []

    # 过敏源置顶，与 db.get_profile_summary() 同一处理：这是对话链路（"今天想吃辣的"
    # 之类）唯一能看到的家庭约束，漏了它等于对话生成的菜单完全不受过敏限制。
    allergens = db.collect_allergens()
    if allergens:
        context_parts.append(
            "⚠️ 【过敏禁用】绝对不能出现在任何菜品、配菜与调料中："
            + "、".join(allergens)
        )

    # 家庭成员
    if members:
        context_parts.append(f"【家庭成员】{len(members)}人")
        for m in members:
            parts = [m['member_name']]
            if m.get('age'):
                parts.append(f"{m['age']}岁")
            if m.get('allergies'):
                parts.append(f"过敏禁用{','.join(m['allergies'])}")
            if m.get('dislikes_main'):
                parts.append(f"不吃{','.join(m['dislikes_main'])}")
            if m.get('dislikes_taste'):
                parts.append(f"不吃{','.join(m['dislikes_taste'])}")
            if m.get('dislikes_ingredient'):
                parts.append(f"不要{','.join(m['dislikes_ingredient'])}")
            if m.get('health_notes'):
                parts.append(m['health_notes'])
            context_parts.append("  " + "，".join(parts))
    else:
        context_parts.append("【家庭成员】未设置")

    # 冰箱库存
    if inventory:
        context_parts.append(f"【冰箱库存】{len(inventory)}种食材")

        # 按分类统计
        categories = {}
        for item in inventory:
            cat = item.get('category', '其他')
            categories[cat] = categories.get(cat, 0) + 1
        cat_str = "、".join([f"{cat}{count}种" for cat, count in categories.items()])
        context_parts.append(f"  分类：{cat_str}")

        # 快过期提醒
        if expiring:
            exp_names = "、".join([i['name'] for i in expiring[:3]])
            context_parts.append(f"  ⚠️ 即将过期：{exp_names}")
    else:
        context_parts.append("【冰箱库存】空")

    return "\n".join(context_parts)


# ==================== 对话处理 ====================

#: 送进 prompt 的历史消息条数（user + assistant 各算一条，6 条 ≈ 3 轮问答）。
#
# 不取更多是因为 prompt 里已经有全量库存（可能几十行）与家庭画像；历史越长，模型越容易
# 拿着几轮前的旧要求作答（"刚才不是说要清淡吗"）。三轮足够覆盖「换一个」「那个再少点辣」
# 这类指代，而指代基本只回指上一轮。
CHAT_HISTORY_MESSAGES = 6

#: 单条历史消息进 prompt 时的长度上限（字符）。
#
# 助手的推荐回复很长（两套方案 + 营养 + 采购清单，几百字），原样塞进历史会把真正的
# 上下文挤掉，还会让模型照抄格式。截断保留开头 —— 菜名都在前面。
_HISTORY_MESSAGE_MAX_CHARS = 120


def format_history(records: List[Dict]) -> str:
    """把 conversation_history 的记录拼成 prompt 用的纯文本。

    只取 role 与 content 两列，逐条截断。空列表返回空串，调用方据此完全不写这一节 ——
    首轮对话不该出现一个空的「最近的对话」标题，那会让模型以为历史被清空过。
    """
    if not records:
        return ""

    lines = []
    for record in records:
        role = record.get('role')
        content = str(record.get('content') or '').strip()
        if not content:
            continue
        speaker = '用户' if role == 'user' else '助手'
        if len(content) > _HISTORY_MESSAGE_MAX_CHARS:
            content = content[:_HISTORY_MESSAGE_MAX_CHARS] + '…'
        # 换行会破坏"一行一条"的结构，模型容易把多行的一条读成多条
        lines.append(f"{speaker}：{content.replace(chr(10), ' ')}")
    return "\n".join(lines)


def chat(user_input: str, session_id: str = "default") -> Dict:
    """
    处理用户对话

    Args:
        user_input: 用户输入
        session_id: 会话ID

    Returns:
        dict: {"reply": 回复, "action": 动作类型, "data": 相关数据}
    """
    # ── 先读历史，再写入本轮 ──
    # 顺序反了的话，get_recent_conversations 会把刚写进去的这句也带回来，于是用户的话
    # 在 prompt 里出现两次（一次在历史里、一次在"用户说："后面），模型会把重复当强调。
    history_text = format_history(
        db.get_recent_conversations(session_id, limit=CHAT_HISTORY_MESSAGES)
    )

    db.add_conversation(session_id, "user", user_input)

    context = get_context()

    intent = detect_intent(user_input)

    if intent == "profile":
        result = handle_profile_update(user_input)
    elif intent == "inventory":
        result = handle_inventory_query(user_input)
    elif intent == "recommend":
        result = handle_recommend_request(user_input)
    elif intent == "confirm":
        result = handle_confirm(user_input)
    elif intent == "help":
        result = handle_help()
    else:
        result = handle_general_chat(user_input, context, history_text)

    db.add_conversation(session_id, "assistant", result["reply"], result.get("action"))

    return result


# ==================== 画像处理（增强版）====================

def handle_profile_update(user_input: str) -> Dict:
    """
    处理画像更新（增强版）

    支持：
    - 自然语言描述家庭成员
    - 批量添加/更新
    - 忌口分层处理
    - 健康备注
    - 用餐时间
    """
    # 调用 AI 提取结构化数据
    prompt = build_profile_extraction_prompt(user_input)
    result = call_ai_text_with_json(prompt, PROFILE_EXTRACTION_SYSTEM,
                                      purpose='profile_extract')

    if isinstance(result, dict) and 'error' in result:
        return {
            "reply": "抱歉，我没有完全理解你说的内容，能再描述一下吗？\n\n"
                     "比如：\n"
                     "- 家里3口人，爸爸不吃辣，孩子不吃苦瓜\n"
                     "- 老婆怀孕了，需要补铁\n"
                     "- 我在减肥，少吃油腻",
            "action": "profile_error"
        }

    # 处理提取结果
    action = result.get('action', 'create')
    members = result.get('members', [])
    meal_times = result.get('meal_times')

    if not members:
        return {
            "reply": "我没有识别到家庭成员信息，能再说一遍吗？\n\n"
                     "示例：\n"
                     "- 家里3口人：爸爸（不吃辣）、妈妈（不吃香菜）、孩子（5岁，不吃苦瓜）\n"
                     "- 老公身高175，体重70公斤，不吃海鲜",
            "action": "profile_error"
        }

    saved_members = []
    updated_members = []

    for member in members:
        member_name = member.get('name', '未知')

        # 查找已有成员
        existing = db.get_all_members()
        existing_member = None
        for ex in existing:
            if ex['member_name'] == member_name:
                existing_member = ex
                break

        if existing_member and action in ('update', 'auto'):
            update_data = {}
            for field in ['height', 'weight', 'age', 'allergies', 'dislikes_main',
                          'dislikes_ingredient', 'dislikes_taste', 'health_notes']:
                new_val = member.get(field)
                old_val = existing_member.get(field)

                # 只更新有新值的字段
                if new_val is not None:
                    if isinstance(new_val, list) and isinstance(old_val, list):
                        # 列表类型：合并去重
                        merged = list(set(old_val + new_val))
                        update_data[field] = merged
                    elif isinstance(new_val, str) and isinstance(old_val, str):
                        # 字符串类型：追加或覆盖
                        if old_val and new_val not in old_val:
                            update_data[field] = f"{old_val}；{new_val}"
                        else:
                            update_data[field] = new_val
                    else:
                        update_data[field] = new_val

            if update_data:
                db.update_member(existing_member['id'], **update_data)
                updated_members.append(member_name)
        else:
            # 新增成员
            db.add_member(
                member_name=member_name,
                height=member.get('height'),
                weight=member.get('weight'),
                age=member.get('age'),
                allergies=member.get('allergies', []),
                dislikes_main=member.get('dislikes_main', []),
                dislikes_ingredient=member.get('dislikes_ingredient', []),
                dislikes_taste=member.get('dislikes_taste', []),
                health_notes=member.get('health_notes', '')
            )
            saved_members.append(member)

    if meal_times:
        for meal_type, time in meal_times.items():
            if time:
                db.set_meal_schedule(meal_type, time)

    # 构建回复
    reply_parts = []

    if saved_members:
        reply_parts.append("✅ 新增家庭成员：")
        for m in saved_members:
            reply_parts.append(format_member_info(m))

    if updated_members:
        reply_parts.append(f"\n🔄 更新了 {len(updated_members)} 位成员信息")

    if meal_times:
        reply_parts.append("\n⏰ 用餐时间：")
        meal_names = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
        for meal_type, time in meal_times.items():
            if time:
                reply_parts.append(f"  {meal_names.get(meal_type, meal_type)}：{time}")

    if not reply_parts:
        reply_parts.append("已记录！还有什么需要补充的吗？")
    else:
        reply_parts.append("\n还有什么需要补充的吗？")

    return {
        "reply": "\n".join(reply_parts),
        "action": "profile_update",
        "data": {
            "saved": [m.get('name') for m in saved_members],
            "updated": updated_members
        }
    }


def format_member_info(member: Dict) -> str:
    """格式化成员信息"""
    line = f"  👤 {member.get('name', '未知')}"

    details = []
    if member.get('age'):
        details.append(f"{member['age']}岁")
    if member.get('height') and member.get('weight'):
        details.append(f"{member['height']}cm/{member['weight']}kg")
    if member.get('dislikes_main'):
        details.append(f"不吃{','.join(member['dislikes_main'])}")
    if member.get('dislikes_ingredient'):
        details.append(f"不要{','.join(member['dislikes_ingredient'])}")
    if member.get('dislikes_taste'):
        details.append(f"不吃{','.join(member['dislikes_taste'])}")
    if member.get('allergies'):
        details.append(f"过敏：{','.join(member['allergies'])}")
    if member.get('health_notes'):
        details.append(member['health_notes'])

    if details:
        line += "：" + "，".join(details)

    return line


# ==================== 库存查询（增强版）====================

def handle_inventory_query(user_input: str) -> Dict:
    """
    处理库存查询（增强版）

    支持：
    - 分类查询
    - 过期查询
    - 数量查询
    """
    category = None
    expiring_only = False

    categories = ['蔬菜', '水果', '肉类', '海鲜', '蛋类', '乳制品',
                  '豆制品', '主食', '调味品', '饮料', '零食', '冷冻食品']
    for cat in categories:
        if cat in user_input:
            category = cat
            break

    if any(kw in user_input for kw in ['快过期', '快坏了', '要过期', '即将过期']):
        expiring_only = True

    if expiring_only:
        items = db.get_expiring_items(days=3)
        if not items:
            return {
                "reply": "🎉 没有即将过期的食材，库存都很新鲜！",
                "action": "inventory_query",
                "data": []
            }
        title = f"⚠️ 即将过期的食材（{len(items)}种）："
    elif category:
        items = db.get_inventory_by_category(category)
        if not items:
            return {
                "reply": f"🧊 冰箱里没有{category}类食材。",
                "action": "inventory_query",
                "data": []
            }
        title = f"🧊 {category}类食材（{len(items)}种）："
    else:
        result = db.get_inventory(page_size=1000)
        items = result.get('items', [])
        if not items:
            return {
                "reply": "🧊 冰箱是空的哦！\n\n你可以拍一张冰箱照片，我来帮你识别食材。",
                "action": "inventory_empty"
            }
        title = f"🧊 冰箱里有 {len(items)} 种食材："

    # 格式化
    lines = [title + "\n"]

    # 按分类分组（如果不是按分类查询）
    if not category:
        grouped = {}
        for item in items:
            cat = item.get('category', '其他')
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(item)

        for cat, cat_items in grouped.items():
            lines.append(f"【{cat}】")
            for item in cat_items:
                lines.append(format_inventory_item(item))
    else:
        for item in items:
            lines.append(format_inventory_item(item))

    # 统计信息
    expiring = db.get_expiring_items(days=3)
    if expiring and not expiring_only:
        lines.append(f"\n⚠️ 有 {len(expiring)} 种食材即将过期，建议优先使用！")

    return {
        "reply": "\n".join(lines),
        "action": "inventory_query",
        "data": items
    }


def format_inventory_item(item: Dict) -> str:
    """格式化库存项"""
    line = f"  • {item['name']} × {item['quantity']}{item.get('unit', '个')}"

    if item.get('days_left') is not None:
        days = item['days_left']
        if days < 0:
            line += " ❌已过期"
        elif days == 0:
            line += " ⚠️今天过期"
        elif days <= 3:
            line += f" ⚠️{days}天后过期"
        elif days <= 7:
            line += f" 📅{days}天后过期"

    return line


# ==================== 菜谱推荐（增强版）====================

def handle_recommend_request(user_input: str) -> Dict:
    """
    处理菜谱推荐请求（增强版）

    支持：
    - 自动识别餐次和人数
    - 优先使用快过期食材
    - 考虑家庭画像忌口
    - 提供营养信息
    """
    meal_info = parse_meal_info(user_input)
    meal_type = meal_info.get('meal_type', '晚餐')
    people_count = meal_info.get('people_count')

    # 如果没有指定人数，从画像获取
    if not people_count:
        members = db.get_all_members()
        if members:
            people_count = len(members)
        else:
            return {
                "reply": "请问今天几个人吃饭？",
                "action": "ask_people_count"
            }

    # 生成推荐
    return generate_recommendation(meal_type, people_count, user_input)


def get_nutrition_for_inventory() -> Optional[str]:
    """获取冰箱食材的营养数据"""
    items = db.get_inventory(page_size=1000).get('items', [])
    if not items:
        return None

    nutrition_lines = []
    for item in items[:10]:  # 只取前10种，避免prompt太长
        name = item['name']
        nutrition = db.get_nutrition_summary(name)
        if nutrition:
            nutrition_lines.append(f"- {nutrition}")

    return "\n".join(nutrition_lines) if nutrition_lines else None


#: 视为「家里一定有」的基础调料与辅料，不计入需采购清单。
#
# 没有这份白名单，「只能用库存里的食材」这条硬约束会把模型逼进死角：要么做不放油盐的菜，
# 要么把油盐都算成需采购项。
#
# 它**只影响「算不算需采购」**，不影响过敏拦截：「花生油」在白名单里，但家里有人对花生
# 过敏时 find_allergen_violations() 照样会拦下它。两个检查目的不同，不能共用一份名单。
#
# 分两层是因为歧义程度不同：「油」「盐」「糖」「水」「醋」这类 1-2 字的词会出现在真食材的
# 名字里（油菜、水豆腐、盐水鸭、糖蒜、矿泉水），子串匹配会把这些家常菜静默剔出采购清单，
# 所以它们只接受**精确匹配**。
#
#: 只接受精确匹配（剥掉量词后）的歧义调料。
PANTRY_EXACT = frozenset({
    '油', '食用油', '植物油', '盐', '食盐',
    '糖', '白糖', '冰糖', '红糖', '砂糖', '白砂糖',
    '醋', '水', '清水', '开水', '热水', '凉水', '温水',
    '面粉', '淀粉', '生粉', '玉米淀粉', '土豆淀粉', '红薯淀粉',
    '花椒', '八角', '桂皮', '香叶', '味精', '鸡精', '孜然', '香菜末',
    # 葱姜蒜是中餐几乎每道菜都用到的基础增香料，等同油盐：家里一定有，不该算进采购。
    # 只列增香用的写法（蒜末/姜片/葱花…）与单字，避免用子串误伤「洋葱/蒜苔/大葱」这些真食材。
    '蒜', '大蒜', '蒜末', '蒜蓉', '蒜泥', '蒜片', '蒜瓣',
    '姜', '生姜', '姜末', '姜片', '姜丝', '姜蓉', '姜泥',
    '葱', '小葱', '香葱', '葱花', '葱段', '葱丝', '葱末',
    '葱姜', '姜蒜', '葱蒜', '葱姜蒜',
})

#: 多字且无歧义，可以按子串匹配（「生抽酱油」「一点老抽」都要算进去）。
PANTRY_SUBSTRING = (
    '生抽', '老抽', '酱油', '料酒', '黄酒', '蚝油', '鱼露',
    '豆瓣酱', '甜面酱', '番茄酱', '沙拉酱', '芝麻酱',
    '陈醋', '香醋', '白醋', '米醋', 'balsamic',
    '菜籽油', '花生油', '橄榄油', '香油', '芝麻油', '猪油', '玉米油', '大豆油',
    '小苏打', '酵母', '泡打粉', '吉士粉',
    # 勾芡用的水淀粉/生粉水等：'淀粉'/'生粉' 作子串可覆盖「水淀粉」「玉米淀粉」「生粉水」，
    # 没有真食材名里含这两个子串，不会误伤。
    '淀粉', '生粉',
    '胡椒粉', '白胡椒', '黑胡椒', '五香粉', '十三香', '辣椒粉', '咖喱粉', '花椒粉',
    '高汤', '清汤', '骨汤',
)

#: 模型常把量词写进配料名（「少许盐」「适量清水」）。剥掉后才能做精确匹配。
_PANTRY_QUANTIFIERS = ('少许', '适量', '一点', '若干', '半勺', '一勺', '几滴', '些')

#: prompt 里列给模型看的调料示例，按"最常用的排前面"手排。
#
# 它必须是判定集的**子集**（模块底部有断言守着）：列进 prompt 却不被 [_is_pantry_staple]
# 认下的调料，会让模型照 prompt 用了它、这边又把它算成"还需采购"。
# 反方向不必完整 —— 判定集有 60 多项，全列进 prompt 只会挤占库存那一节的篇幅，
# 而模型需要的只是"这类东西不用写进采购"这个概念。
PANTRY_PROMPT_SAMPLE = (
    '油', '盐', '糖', '醋', '酱油', '生抽', '老抽', '料酒', '蚝油', '豆瓣酱', '番茄酱',
    '葱', '姜', '蒜', '淀粉', '面粉', '花椒', '八角', '桂皮', '香叶', '五香粉', '黑胡椒',
    '辣椒粉', '孜然', '味精', '鸡精', '香油', '花生油', '小苏打', '清水', '高汤',
)


def _is_pantry_staple(name: str) -> bool:
    """是否属于基础调料（不计入需采购清单）。

    单向判断 + 分层匹配。刻意**去掉**了原来 `text in staple` 那个方向：它唯一的用处是
    认下「一点盐」这种带量词的写法，而那已经由 [_PANTRY_QUANTIFIERS] 处理；代价是把
    「油」这样的短白名单项变成了对任何含「油」的食材的通行证。
    """
    text = name.strip()
    for quantifier in _PANTRY_QUANTIFIERS:
        text = text.replace(quantifier, '')
    text = text.strip()
    if not text:
        return True  # 空名字（或只写了个"少许"）不该出现在采购清单里
    if text in PANTRY_EXACT:
        return True
    return any(staple in text for staple in PANTRY_SUBSTRING)


# 导入时就校验 prompt 示例与判定集一致。放在这里而不是测试里：这两份数据不一致的后果是
# 缺料清单里出现用户家里明摆着有的调料，而它不会让任何测试失败，也不会报错。
assert all(_is_pantry_staple(sample) for sample in PANTRY_PROMPT_SAMPLE), (
    "PANTRY_PROMPT_SAMPLE 里有 _is_pantry_staple() 认不出的项：" +
    "、".join(s for s in PANTRY_PROMPT_SAMPLE if not _is_pantry_staple(s))
)


def find_missing_ingredients(plans: Dict, inventory_names: List[str]) -> Dict[str, List[str]]:
    """算出每套方案里**不在库存**、也不属于基础调料的食材。

    prompt 里那条「只能用库存里的食材」是软的，模型可以无视，所以要有这一层代码检查。

    **缺料的处理**：调用方（[generate_recommendation]）先按缺料纠正一次模型；纠正后仍缺
    库存主料时不整套丢弃，而是用 [drop_invalid_dishes] 剔掉越界的菜后落库，
    只有一道都剩不下时才放弃。葱姜蒜、油盐、水淀粉等基础调料不计入缺料
    （见 [PANTRY_EXACT]/[PANTRY_SUBSTRING]）。

    匹配用双向子串：库存里的「土豆」要能认下菜谱里的「土豆丝」，反过来菜谱写「肉」
    而库存是「五花肉」也要能认。已知盲区是跨名称的同类（库存「猪肉」vs 菜谱「里脊」），
    那需要食材知识库，这里不猜 —— 误报会让用户忽略整个清单。

    Returns:
        {'plan_a': ['香菇', '蚝油'], 'plan_b': []}，只含实际出现的方案键。
    """
    normalized = [n.strip() for n in inventory_names if n and n.strip()]

    def in_stock(name: str) -> bool:
        text = name.strip()
        return any(inv in text or text in inv for inv in normalized)

    missing: Dict[str, List[str]] = {}
    for plan_name, dish in iter_plan_dishes(plans):
        bucket = missing.setdefault(plan_name, [])
        ingredients = dish.get('ingredients')
        if not isinstance(ingredients, list):
            continue
        for ing in ingredients:
            name = ing.get('name') if isinstance(ing, dict) else ing
            name = str(name or '').strip()
            if not name or _is_pantry_staple(name) or in_stock(name):
                continue
            if name not in bucket:
                bucket.append(name)
    return missing


#: 库存项数低于此值时只要求一套方案。
#
# 两套**不同**方案要求主料互不重复又都落在库存内，库存越少越难同时成立：4 项库存下
# 模型只能重复用同几样，或越界拿库存外的主料凑 —— 后者会被 [find_missing_ingredients]
# 判缺料。少库存时收敛成一套，是把「能做出来」放在「有两个选择」之前。
SINGLE_PLAN_INVENTORY_THRESHOLD = 6

#: 一套方案里最少要有几道菜才值得落库。
MIN_DISHES_PER_PLAN = 1


def plan_shape_hint(inventory_count: int) -> str:
    """按库存规模给出方案数与菜品数的 prompt 要求。"""
    if inventory_count < SINGLE_PLAN_INVENTORY_THRESHOLD:
        return (
            f"库存只有 {inventory_count} 项，**只生成一套方案**（只返回 plan_a，不要 plan_b），"
            "菜品 1-2 道即可。"
        )
    return "请生成 A/B 两套方案，每套 2-3 道菜。"


def drop_invalid_dishes(
    plans: Dict,
    inventory_names: List[str],
    meal_type: str = '',
    hard_avoid: List[str] = None,
    hard_ingredients: List[str] = None,
    hard_signatures: List[List[str]] = None,
) -> tuple[Dict, List[str]]:
    """剔除含库存外主料的菜，返回 (可落库的方案, 被剔除的菜名)。

    模型越界时通常只有一两道菜出问题，其余是合规的。整套丢弃会让用户在库存偏少时
    永远收不到菜单（真机日志里的 `recommend_inventory_blocked`），而剔掉越界那道
    就能交付一份能照做的菜单 —— 与「库存不足时减少菜品」这条产品约束一致。

    剔空的方案整套删除；两套都空时返回 `{}`，由调用方判定真的无法成菜。
    """
    if not isinstance(plans, dict):
        return {}, []

    cleaned: Dict = {}
    dropped: List[str] = []
    avoid = {normalize_dish_name(name) for name in (hard_avoid or [])}
    breakfast = meals.meal_key(meal_type) == 'breakfast'
    for plan_name in ('plan_a', 'plan_b'):
        plan = plans.get(plan_name)
        if not isinstance(plan, dict):
            continue
        dishes = plan.get('dishes')
        if not isinstance(dishes, list):
            continue

        # A/B 是同一餐的两个候选，用户只选一套，共用主菜是正常的。方案内部只按菜名去重；
        # 历史菜名、跨餐食材和上一版食材组合则对两套都生效。
        seen = set()
        kept = []
        for dish in dishes:
            if not isinstance(dish, dict):
                continue
            single = {plan_name: {'dishes': [dish]}}
            name = str(dish.get('name') or '').strip()
            normalized = normalize_dish_name(name)
            invalid = bool(find_missing_ingredients(single, inventory_names).get(plan_name))
            invalid = invalid or _dish_hits_avoidance(
                dish, avoid, hard_ingredients, hard_signatures,
            )
            invalid = invalid or (normalized and normalized in seen)
            invalid = invalid or (
                breakfast and any(marker in normalized for marker in BREAKFAST_HEAVY_DISH_MARKERS)
            )
            if invalid:
                if name:
                    dropped.append(name)
                continue
            kept.append(dish)
            seen.add(normalized)

        if len(kept) >= MIN_DISHES_PER_PLAN:
            cleaned[plan_name] = {**plan, 'dishes': kept}

    # plan_b 独活时提到 plan_a：下游（落库、渲染、App 分区）都把 plan_a 当主方案，
    # 只有 plan_b 的结构会让主方案显示为空。
    if 'plan_b' in cleaned and 'plan_a' not in cleaned:
        cleaned['plan_a'] = cleaned.pop('plan_b')

    return cleaned, dropped


# ==================== 改菜单（表单式微调 + 说人话改单）====================

#: 一份方案里最多几道菜。模型偶尔会给出十几道，落到 792x528 的电子纸上排不下，
#: 而截断发生在渲染层时用户已经确认过那份看起来很丰盛的菜单了。
REVISE_MAX_DISHES = 6


def dish_names_of(plan) -> List[str]:
    """取一份方案里的菜名列表，跳过结构不对的条目。

    与 App 侧 `MealPlanDto.dishNames()` 同口径（只取 name、去空白）：diff 是拿名字比的，
    两边口径不一致会让"没改动"显示成"改了一道"。
    """
    if not isinstance(plan, dict):
        return []
    dishes = plan.get('dishes')
    if not isinstance(dishes, list):
        return []
    names = []
    for dish in dishes:
        if isinstance(dish, dict):
            name = str(dish.get('name') or '').strip()
        else:
            name = str(dish or '').strip()
        if name:
            names.append(name)
    return names


BREAKFAST_HEAVY_DISH_MARKERS = (
    '红烧肉', '干锅', '水煮肉片', '水煮鱼', '烤鱼', '火锅', '扣肉', '大盘鸡', '酸菜鱼',
)


def normalize_dish_name(name: str) -> str:
    """规范菜名，用于近期方案的精确去重。"""
    value = re.sub(r'\s+', '', str(name or '').strip())
    return re.sub(r'[（(][^）)]*[）)]$', '', value)


def dish_core_ingredients(dish: Dict) -> List[str]:
    """提取一道菜的非调料食材，用于判断是否实质重复。"""
    if not isinstance(dish, dict) or not isinstance(dish.get('ingredients'), list):
        return []
    names = []
    for ingredient in dish['ingredients']:
        raw = ingredient.get('name') if isinstance(ingredient, dict) else ingredient
        name = re.sub(r'\s+', '', str(raw or '').strip()).replace('西红柿', '番茄')
        if name and not _is_pantry_staple(name) and name not in names:
            names.append(name)
    return names


def _ingredient_matches(left: str, right: str) -> bool:
    """同名或包含关系视为同一食材，如鸡蛋与土鸡蛋。"""
    return bool(left and right and (left in right or right in left))


def _same_core_signature(left: List[str], right: List[str]) -> bool:
    """较小食材集合全部重合时，视为同一道菜只换了名字或修饰词。"""
    if not left or not right:
        return False
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    return all(any(_ingredient_matches(item, other) for other in larger) for item in smaller)


def _dish_hits_avoidance(
    dish: Dict,
    hard_names,
    hard_ingredients: List[str] = None,
    hard_signatures: List[List[str]] = None,
) -> bool:
    """判断菜名、跨餐食材或上一版食材组合是否命中硬避让。"""
    name = normalize_dish_name(dish.get('name'))
    if name and name in hard_names:
        return True
    signature = dish_core_ingredients(dish)
    if any(
        _ingredient_matches(item, used)
        for item in signature
        for used in (hard_ingredients or [])
    ):
        return True
    return any(_same_core_signature(signature, used) for used in (hard_signatures or []))


def recipe_avoidance(meal_type: str) -> Dict[str, list]:
    """返回菜名、跨餐核心食材与上一版食材组合的避让项。

    硬避让（必须换掉）只取**每个餐次最近一条**方案：本餐次的最近一条不限日期
    （「别和上次一样」），其它餐次限当天（「三餐之间别重复」）。这样最多三条记录，
    反复重新生成时不会把当天丢弃过的尝试无限累积进禁止名单、最后配不出任何菜。

    软避让为近 7 天其余方案，只在 prompt 里提示尽量换做法，不参与剔除。
    """
    meal_key = meals.meal_key(meal_type)
    today = datetime.now().astimezone().date()
    hard, soft, hard_ingredients, hard_signatures = [], [], [], []
    hard_keys_used = set()

    for record in db.get_recent_meal_plans():
        selected = str(record.get('selected_plan') or 'a').strip().lower()
        plan = record.get('plan_b') if selected == 'b' else record.get('plan_a')
        names = dish_names_of(plan)
        record_key = meals.meal_key(record.get('meal_type'))
        try:
            created = datetime.fromisoformat(str(record.get('created_at')))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            local_day = created.astimezone().date()
        except (TypeError, ValueError):
            local_day = None

        # 记录按时间倒序，每个餐次第一次命中即为该餐次最近一条。
        first_of_key = record_key is not None and record_key not in hard_keys_used
        hard_record = first_of_key and (record_key == meal_key or local_day == today)
        if hard_record:
            hard_keys_used.add(record_key)
        elif local_day is None or local_day < today - timedelta(days=7):
            continue

        bucket = hard if hard_record else soft
        for name in names:
            normalized = normalize_dish_name(name)
            if normalized and normalized not in bucket:
                bucket.append(normalized)

        if hard_record:
            signatures = [
                dish_core_ingredients(dish)
                for _, dish in iter_plan_dishes({'plan_a': plan})
            ]
            signatures = [signature for signature in signatures if signature]
            if record_key == meal_key:
                for signature in signatures:
                    if signature not in hard_signatures:
                        hard_signatures.append(signature)
            elif local_day == today:
                for ingredient in (item for signature in signatures for item in signature):
                    if ingredient not in hard_ingredients:
                        hard_ingredients.append(ingredient)

    soft = [name for name in soft if name not in hard]
    return {
        'hard': hard,
        'soft': soft,
        'hard_ingredients': hard_ingredients,
        'hard_signatures': hard_signatures,
    }


def find_recipe_constraint_violations(
    plans: Dict, meal_type: str, hard_avoid: List[str] = None,
    hard_ingredients: List[str] = None,
    hard_signatures: List[List[str]] = None,
) -> tuple[List[str], List[str]]:
    """找出重复菜和明显不符合餐次的菜。

    重复判据包括历史菜名、当天其它餐次的核心食材、当前餐次上一版的食材组合，
    以及同一套方案内的同名菜。A/B 之间共用主菜不算重复 —— 用户只会选一套。
    """
    avoid = {normalize_dish_name(name) for name in (hard_avoid or [])}
    duplicates, mismatches = [], []
    breakfast = meals.meal_key(meal_type) == 'breakfast'
    for plan_name in ('plan_a', 'plan_b'):
        seen = set()
        for _, dish in iter_plan_dishes({plan_name: plans.get(plan_name)}):
            name = str(dish.get('name') or '').strip()
            normalized = normalize_dish_name(name)
            if not normalized:
                continue
            if _dish_hits_avoidance(
                dish, avoid, hard_ingredients, hard_signatures,
            ) or normalized in seen:
                duplicates.append(name)
            seen.add(normalized)
            if breakfast and any(marker in normalized for marker in BREAKFAST_HEAVY_DISH_MARKERS):
                mismatches.append(name)
    return duplicates, mismatches


def _latest_pending_for(meal_key: Optional[str]) -> Optional[Dict]:
    """取某一餐**当前最新**的待确认记录，没有则 None。

    三个改单入口（revise 取基准、apply 比对基准是否过期、删菜取基准）都要回答"这一餐现在
    是哪一份"，而且必须是**同一个判据**：口径不一致会让并发校验要么永远失败、要么永远通过。
    db.get_pending_plans() 已按 `created_at DESC, id DESC` 排序，第一条命中的就是最新的。
    """
    if not meal_key:
        return None
    for record in db.get_pending_plans():
        if meals.meal_key(record.get('meal_type')) == meal_key:
            return record
    return None


def validate_revised_plan(plan) -> Optional[str]:
    """校验一份（模型返回或客户端提交的）方案是否可落库。

    返回错误说明，None 表示通过。

    这层校验对**客户端提交**同样必要，不能只在模型输出上做：apply 接口收下的东西会直接
    进库、进渲染、上屏，而客户端完全可以绕过 revise 自己拼一份。信任客户端的结果是
    电子纸上出现一份没人生成过的菜单。
    """
    if not isinstance(plan, dict):
        return "plan 必须是对象"
    dishes = plan.get('dishes')
    if not isinstance(dishes, list) or not dishes:
        return "plan.dishes 必须是非空数组"
    if len(dishes) > REVISE_MAX_DISHES:
        return f"一餐最多 {REVISE_MAX_DISHES} 道菜，实际 {len(dishes)}"
    for index, dish in enumerate(dishes):
        if not isinstance(dish, dict):
            return f"dishes[{index}] 必须是对象"
        if not str(dish.get('name') or '').strip():
            return f"dishes[{index}] 缺少菜名"
    return None


def revise_plan(meal_type: str, instruction: str) -> Dict:
    """按一句自然语言指令改某一餐的菜单，**只算不写**。

    不落库是因为改完要让用户看一眼 diff 再决定：这里就写库则「取消」无从回退；让「确认」
    再调一次模型则第二次结果与用户看过的 diff 不是同一份。落库由 [apply_revised_plan]
    用**同一份数据**完成。

    返回值里的 `base_plan_id` 是这次改动的基准记录 id，客户端确认时必须原样回传给
    [apply_revised_plan]，否则 revise 与 apply 之间的任何写入（定时推荐、另一台手机）
    都会被「确认」静默覆盖，而两边都提示成功。

    Returns:
        成功：{"success": True, "meal_type", "before", "after", "added", "removed",
               "plan", "missing_ingredients", "people_count", "base_plan_id"}
        失败：{"success": False, "error": ...}
    """
    meal_type = normalize_meal_type(meal_type)
    text = str(instruction or '').strip()
    if not text:
        return {"success": False, "error": "请说明要怎么改（例如「晚餐换成清淡的」）"}

    key = meals.meal_key(meal_type)
    current = _latest_pending_for(key)
    if current is None:
        return {
            "success": False,
            "error": f"还没有{meal_type}的菜单，先生成一份再来调整",
            "reason": "no_plan",
        }

    # 以已选那一套为基准（用户选过 B 就改 B），没选过按 A —— 与 App 展示的口径一致。
    which = (current.get('selected_plan') or 'a').strip().lower()
    base = current.get('plan_b') if which == 'b' else current.get('plan_a')
    if not isinstance(base, dict):
        base = current.get('plan_a') if which == 'b' else current.get('plan_b')
    if not isinstance(base, dict):
        return {"success": False, "error": f"{meal_type}的菜单数据已损坏，请重新生成"}

    before = dish_names_of(base)
    people_count = current.get('people_count') or 3

    # 与 generate_recommendation 同一个理由：真机上出现过一次「200 in 67956ms」，
    # 68 秒到底花在读库、拼 prompt 还是模型上，从 access 行里分辨不出来。
    timer = StageTimer(logger, '改菜单', meal=meal_type)
    with timer.stage('db'):
        allergens = db.collect_allergens()
        inventory_names = [
            item['name'] for item in db.get_inventory(page_size=1000).get('items', [])
        ]
        inventory_summary = db.get_inventory_summary()
        profile_summary = db.get_profile_summary()

    with timer.stage('prompt'):
        prompt = build_revise_prompt(
            meal_type=meal_type,
            people_count=people_count,
            current_plan=base,
            instruction=text,
            inventory_summary=inventory_summary,
            profile_summary=profile_summary,
            max_dishes=REVISE_MAX_DISHES,
        )
    logger.info('开始改单：meal=%s 基准=%s 指令=%r', meal_type, before, text[:40])
    with timer.stage('llm'):
        result = call_ai_text_with_json(
            prompt, RECIPE_SYSTEM, purpose='revise',
            max_completion_tokens=RECIPE_MAX_COMPLETION_TOKENS,
        )

    if isinstance(result, dict) and 'error' in result:
        # 真机上遇到过一次耗时 68 秒、最终失败的改单，而日志里只有一行「200 in 67956ms」，
        # 失败原因一个字都看不到。给用户的话不变，原因必须落进日志。
        logger.error('改单失败：meal=%s 原因=%s 原文开头=%r',
                     meal_type, result.get('error'), str(result.get('raw') or '')[:200])
        timer.done(结果='模型失败')
        return {"success": False, "error": "模型没能给出改后的菜单，换个说法再试一次"}

    revised = result.get('plan') if isinstance(result, dict) else None
    if not isinstance(revised, dict):
        # 有些回答会把 dishes 直接放在顶层而不套一层 plan。接受这种形状，
        # 但不接受"随便找个字典当方案" —— 那会把模型的解释文字当成菜单。
        revised = result if isinstance(result, dict) and 'dishes' in result else None

    problem = validate_revised_plan(revised)
    if problem:
        logger.warning("改单结果不合规：meal=%s problem=%s", meal_type, problem)
        timer.done(结果='结构不合规')
        return {"success": False, "error": f"模型返回的菜单不可用（{problem}），请再试一次"}

    # 过敏是硬拦截，与首次生成同一条判据（find_allergen_violations）。
    # 改单最容易在这里出事：用户说"加个花生米"而家里有人对花生过敏，模型往往会照做。
    violations = find_allergen_violations({'plan_a': revised}, allergens)
    if violations:
        logger.error("改单命中过敏源，已拒绝：allergens=%s violations=%s", allergens, violations)
        timer.done(结果='过敏拦截')
        return {
            "success": False,
            "reason": "allergen_blocked",
            "error": "改后的菜单里出现了家里的过敏源（"
                     + "、".join(allergens) + "），已拒绝。请换个说法。",
            "violations": violations,
        }

    after = dish_names_of(revised)
    with timer.stage('validate'):
        missing = find_missing_ingredients({'plan_a': revised}, inventory_names)
    _log_revise_result(meal_type, before, after,
                       missing.get('plan_a', []), current.get('id'))
    timer.done(基准plan_id=current.get('id'))
    return {
        "success": True,
        "meal_type": meal_type,
        "people_count": people_count,
        "before": before,
        "after": after,
        # 用集合差而不是逐位比：菜品顺序变了不算改动，而按位比会把整份都标成变化。
        "added": [n for n in after if n not in before],
        "removed": [n for n in before if n not in after],
        "plan": revised,
        "missing_ingredients": missing.get('plan_a', []),
        "base_plan_id": current.get('id'),
    }


def _log_revise_result(meal_type: str, before: List[str], after: List[str],
                       missing: List[str], base_plan_id) -> None:
    """改单成功时把 diff 记下来（供 revise 返回前调用）。"""
    logger.info(
        '改单完成：meal=%s 基准plan_id=%s 改前=%s 改后=%s 新增=%s 删除=%s 缺料=%s',
        meal_type, base_plan_id, before, after,
        [n for n in after if n not in before] or '无',
        [n for n in before if n not in after] or '无',
        missing or '无'
    )


def remove_dish(meal_type: str, dish_name: str) -> Dict:
    """从某一餐的菜单里删掉一道菜（表单式微调，不调模型）。

    ── 为什么删菜也放在后端 ──
    让 App 自己删完再把整份方案提交上来，就得让客户端持有并回传完整的 plan 结构
    （ingredients / steps / cooking_time…）。而 App 侧的 DTO 是强类型的、只声明了它要显示
    的字段，序列化回来时未声明的字段会**静默丢失** —— 落库的菜谱没有做法步骤，而删菜这个
    操作看起来完全成功。后端手里本来就有完整数据，在这里删最省事也最安全。

    删到一道不剩时拒绝：一餐没有任何菜品会让渲染出一张空白画面，那不是用户的意思
    （他要的是"这一餐不做了"，那是另一个操作）。
    """
    meal_type = normalize_meal_type(meal_type)
    name = str(dish_name or '').strip()
    if not name:
        return {"success": False, "error": "缺少要删除的菜名"}

    key = meals.meal_key(meal_type)
    current = _latest_pending_for(key)
    if current is None:
        return {
            "success": False, "reason": "no_plan",
            "error": f"还没有{meal_type}的菜单",
        }

    which = (current.get('selected_plan') or 'a').strip().lower()
    base = current.get('plan_b') if which == 'b' else current.get('plan_a')
    if not isinstance(base, dict):
        base = current.get('plan_a') if which == 'b' else current.get('plan_b')
    if not isinstance(base, dict) or not isinstance(base.get('dishes'), list):
        return {"success": False, "error": f"{meal_type}的菜单数据已损坏，请重新生成"}

    kept = [
        dish for dish in base['dishes']
        if not (isinstance(dish, dict) and str(dish.get('name') or '').strip() == name)
    ]
    if len(kept) == len(base['dishes']):
        return {"success": False, "error": f"{meal_type}里没有「{name}」这道菜"}
    if not kept:
        return {
            "success": False, "reason": "would_be_empty",
            "error": "这是最后一道菜了，删完这一餐就没有内容可显示。"
                     "要换菜请用「说一句话改菜单」，要重做整餐请回菜谱页重新生成。",
        }

    # 复制一份再改，不动库里那条记录的对象 —— 落库走 apply（插入新记录），
    # 原地改会让已经下发到设备上的那一版在库里被悄悄替换掉。
    revised = dict(base)
    revised['dishes'] = kept
    # 带上基准 id：这里刚读到的 current 就是判据，落库前若已被别的写入顶掉，
    # 用户看到的那份菜单已经不在了，删除该失败而不是把旧方案改一改再插回去。
    return apply_revised_plan(
        meal_type, revised, current.get('people_count'), base_plan_id=current.get('id')
    )


def apply_revised_plan(meal_type: str, plan: Dict, people_count: int = None,
                       base_plan_id: int = None) -> Dict:
    """把一份改好的方案落库，**只写不算**。

    同时服务两种入口：说人话改单的「确认」（plan 来自 [revise_plan]）与表单式微调的
    删菜（plan 由 App 从现有方案里筛掉几道后提交）。

    过敏校验在这里**再做一次**，不因为 revise 已经查过就跳过：表单式微调不经过 revise，
    而客户端也可以直接调这个接口。信任客户端等于让电子纸上出现一份没人审过的菜单。

    `base_plan_id` 是乐观并发令牌（来自 [revise_plan] 的返回值）。给了就校验「这一餐现在
    最新的还是不是那一份」，不是则拒绝并回 `stale_base`；否则用户看 diff 期间的任何写入
    （餐前定时推荐、另一台手机）都会被这次确认静默盖掉。留 None 是为兼容不传令牌的调用方
    （老客户端、脚本），那种情况下退化成无条件覆盖。

    写入方式是插入一条新记录而不是改旧的：meal_plan 是「这一餐生成过哪些方案」的流水，
    下发任务读最新一条。原地改会让已下发到设备上的那一版在库里被悄悄替换掉。
    """
    meal_type = normalize_meal_type(meal_type)

    problem = validate_revised_plan(plan)
    if problem:
        return {"success": False, "error": problem, "reason": "invalid_plan"}

    key = meals.meal_key(meal_type)

    allergens = db.collect_allergens()
    violations = find_allergen_violations({'plan_a': plan}, allergens)
    if violations:
        logger.error("落库前拦下含过敏源的菜单：allergens=%s violations=%s", allergens, violations)
        return {
            "success": False,
            "reason": "allergen_blocked",
            "error": "这份菜单里有家里的过敏源（" + "、".join(allergens) + "），不能保存。",
            "violations": violations,
        }

    if not people_count:
        latest = _latest_pending_for(key)
        if latest:
            people_count = latest.get('people_count')
    people_count = people_count or 3

    write_args = {
        'meal_type': meal_type,
        'meal_time': datetime.now().strftime('%H:%M'),
        'people_count': people_count,
        'plan_a': plan,
        # plan_b 留空：这是一份**已确认**的菜单，不是两个待选方案。塞一份假的 B
        # 会让菜谱页出现一个用户没见过的备选。
        'plan_b': None,
    }
    if base_plan_id is None:
        plan_id = db.add_meal_plan(**write_args)
    else:
        aliases = [
            alias for alias, canonical in meals.MEAL_ALIASES.items()
            if canonical == key
        ]
        plan_id, newest_id = db.add_meal_plan_if_latest(
            **write_args,
            expected_latest_id=base_plan_id,
            meal_type_aliases=aliases,
        )
        # newest_id 为 None 也算过期：基准那条已不在待确认流水里（被发布或清理）。
        if plan_id is None:
            logger.warning(
                "改单基准已过期，拒绝落库：meal=%s base=%s newest=%s",
                meal_type, base_plan_id, newest_id,
            )
            return {
                "success": False,
                "reason": "stale_base",
                "error": f"{meal_type}的菜单在你修改期间变过了，请重新调整一次再确认。",
            }
    names = dish_names_of(plan)
    logger.info("菜单已更新：meal=%s plan_id=%s dishes=%s", meal_type, plan_id, names)

    inventory_names = [
        item['name'] for item in db.get_inventory(page_size=1000).get('items', [])
    ]
    missing = find_missing_ingredients({'plan_a': plan}, inventory_names)
    return {
        "success": True,
        "meal_type": meal_type,
        "plan_id": plan_id,
        "dishes": names,
        "missing_ingredients": missing.get('plan_a', []),
    }


def iter_plan_dishes(plans: Dict):
    """遍历 A/B 两套方案里的每道菜，产出 (方案名, 菜dict)。

    容错到底：模型返回的结构可能缺 plan_b、dishes 可能是 None、菜可能是字符串而不是
    对象。这里逐层判类型，因为调用方是**安全校验**——校验器自己因为一个 None 抛异常，
    等于这道检查静默失效，而那正是它要防的场景。
    """
    if not isinstance(plans, dict):
        return
    for plan_name in ('plan_a', 'plan_b'):
        plan = plans.get(plan_name)
        if not isinstance(plan, dict):
            continue
        dishes = plan.get('dishes')
        if not isinstance(dishes, list):
            continue
        for dish in dishes:
            if isinstance(dish, dict):
                yield plan_name, dish


def _valid_recipe_plans(plans) -> bool:
    """至少有一套方案，且所有出现的方案都包含有名字的菜品。"""
    if not isinstance(plans, dict):
        return False
    found = False
    for plan_name in ('plan_a', 'plan_b'):
        plan = plans.get(plan_name)
        if plan is None:
            continue
        if not isinstance(plan, dict):
            return False
        dishes = plan.get('dishes')
        if not isinstance(dishes, list) or not dishes:
            return False
        if any(not isinstance(dish, dict) or not str(dish.get('name') or '').strip()
               for dish in dishes):
            return False
        found = True
    return found


def find_allergen_violations(plans: Dict, allergens: List[str]) -> List[str]:
    """在生成结果里找出命中过敏源的位置。

    匹配规则是**双向子串**：登记「花生」要能拦住菜谱里的「花生油」「花生碎」，
    而菜名本身（「花生米拌黄瓜」）同样要拦。

    已知盲区：登记「乳制品」拦不住「牛奶」「黄油」，因为那需要同义词/成分知识。
    这一层不做任何推断 —— 猜错方向是把安全的菜误判成危险，而误报会让人学会忽略警告，
    最终连真的那次也被忽略。同义词交给 prompt 里那条「包括含有它的加工食品」去覆盖。

    Returns:
        形如 ["plan_a/宫保鸡丁/花生"] 的定位串列表，空列表表示未发现命中。
    """
    if not allergens:
        return []

    violations = []
    for plan_name, dish in iter_plan_dishes(plans):
        dish_name = str(dish.get('name') or '')
        # 收集这道菜里所有会被吃进嘴的文本：菜名 + 食材名。
        # skip_ingredients 刻意**不检查** —— 那是"要去掉的配料"，出现在那里正是被排除的意思。
        texts = [dish_name]
        ingredients = dish.get('ingredients')
        if isinstance(ingredients, list):
            for ing in ingredients:
                if isinstance(ing, dict):
                    texts.append(str(ing.get('name') or ''))
                elif isinstance(ing, str):
                    texts.append(ing)

        for allergen in allergens:
            for text in texts:
                if not text:
                    continue
                if allergen in text or text in allergen:
                    violations.append(f"{plan_name}/{dish_name or '未命名菜'}/{allergen}")
                    break
    return violations


def stream_chat(user_input: str, session_id: str = "default"):
    """流式处理对话，逐块产出事件（事件形态见 [stream_recommendation]）。

    只有两类意图会调模型、值得流式：`recommend` 与自由对话。其余意图是纯数据库操作，
    毫秒级就能答，走流式只是多包一层 —— 它们直接产出 result 事件。

    会话历史的读写顺序与 [chat] 一致：先读历史再写本轮，否则用户这句会在 prompt 里
    出现两次。
    """
    history_text = format_history(
        db.get_recent_conversations(session_id, limit=CHAT_HISTORY_MESSAGES)
    )
    db.add_conversation(session_id, "user", user_input)

    intent = detect_intent(user_input)
    result: Optional[Dict] = None

    if intent == "recommend":
        meal_info = parse_meal_info(user_input)
        people_count = meal_info.get('people_count')
        if not people_count:
            members = db.get_all_members()
            people_count = len(members) if members else None
        if not people_count:
            result = {"reply": "请问今天几个人吃饭？", "action": "ask_people_count"}
        else:
            for event in stream_recommendation(
                meal_info.get('meal_type', '晚餐'), people_count, user_input
            ):
                if event.get('type') == 'result':
                    result = {k: v for k, v in event.items() if k != 'type'}
                else:
                    yield event
    elif intent == "profile":
        result = handle_profile_update(user_input)
    elif intent == "inventory":
        result = handle_inventory_query(user_input)
    elif intent == "confirm":
        result = handle_confirm(user_input)
    elif intent == "help":
        result = handle_help()
    else:
        yield {'type': 'stage', 'stage': '正在思考'}
        parts: List[str] = []
        prompt = build_chat_prompt(user_input, get_context(), history_text)
        for event in stream_ai_text(prompt, purpose='chat_stream',
                                      max_completion_tokens=MAX_COMPLETION_TOKENS):
            kind = event.get('type')
            if kind in ('reasoning', 'content'):
                if kind == 'content':
                    parts.append(event.get('delta') or '')
                yield event
            elif kind == 'error':
                yield event
                return
        result = {"reply": ''.join(parts), "action": "chat"}

    if result is None:
        # 流式菜谱路径异常终止（模型失败已产出 error 事件），不落会话记录。
        return

    db.add_conversation(session_id, "assistant", result["reply"], result.get("action"))
    yield {'type': 'result', **result}


def _collect_recipe_inputs() -> Dict:
    """收集生成菜谱所需的库存 / 画像 / 临期 / 营养上下文。

    流式与非流式共用：两条入口依据的库存必须是同一份，否则同一时刻从对话页和菜谱页
    各点一次，会得到基于不同库存的两份菜单。

    Returns:
        `{'empty': True}` 表示冰箱为空；否则含 prompt 与校验所需的全部字段。
    """
    inventory_summary = db.get_inventory_summary()
    if inventory_summary == "冰箱为空":
        return {'empty': True}

    expiring = db.get_expiring_items(days=3)
    inventory_items = db.get_inventory(page_size=1000).get('items', [])
    return {
        'empty': False,
        'inventory_summary': inventory_summary,
        'profile_summary': db.get_profile_summary(),
        'expiring_names': [i['name'] for i in expiring] if expiring else [],
        'nutrition_data': get_nutrition_for_inventory(),
        'inventory_names': [item['name'] for item in inventory_items],
        'allergens': db.collect_allergens(),
    }


def stream_recommendation(meal_type: str, people_count: int, user_input: str = ""):
    """流式生成菜谱，逐块产出事件字典。

    产出的事件：
      - `{'type': 'stage', 'stage': str}` 阶段标注（准备库存 / 生成中 / 校验 / 保存）
      - `{'type': 'reasoning', 'delta': str}` 模型思考增量
      - `{'type': 'content', 'delta': str}` 模型正文增量
      - `{'type': 'result', ...}` 与 [generate_recommendation] 同构的最终结果
      - `{'type': 'error', 'message': str}`

    **与非流式的一处刻意差异**：这里不做「约束不合格就重调模型」的重试 —— 那会让已经逐字
    显示出来的菜单突然被另一份替换。流式路径直接用 [drop_invalid_dishes] 剔掉越界的菜，
    与「库存不足时减少菜品」这条产品约束一致。
    """
    meal_key = meals.meal_key(meal_type)
    meal_label = meals.meal_label(meal_key) if meal_key else str(meal_type)

    yield {'type': 'stage', 'stage': '正在读取冰箱库存'}
    context = _collect_recipe_inputs()
    if context['empty']:
        yield {
            'type': 'result',
            'reply': "🧊 冰箱是空的，没法推荐菜谱哦！\n\n请先拍一张冰箱照片，识别食材后再来问我。",
            'action': 'inventory_empty',
        }
        return

    inventory_names = context['inventory_names']
    allergens = context['allergens']
    avoidance = recipe_avoidance(meal_label)
    logger.info(
        '开始流式生成菜谱：meal=%s 人数=%s 库存=%d项 临期=%d项 过敏源=%s',
        meal_label, people_count, len(inventory_names),
        len(context['expiring_names']), allergens or '无'
    )

    prompt = build_enhanced_recipe_prompt(
        inventory_summary=context['inventory_summary'],
        profile_summary=context['profile_summary'],
        meal_type=meal_label,
        people_count=people_count,
        nutrition_data=context['nutrition_data'],
        expiring_items=context['expiring_names'],
        user_input=user_input,
        inventory_count=len(inventory_names),
        hard_avoid=avoidance['hard'],
        soft_avoid=avoidance['soft'],
        hard_ingredients=avoidance.get('hard_ingredients'),
        hard_signatures=avoidance.get('hard_signatures'),
    )

    yield {'type': 'stage', 'stage': '正在配菜'}
    raw_text = None
    for event in stream_ai_text(
        prompt, RECIPE_SYSTEM, purpose='recipe_stream',
        max_completion_tokens=RECIPE_MAX_COMPLETION_TOKENS,
    ):
        kind = event.get('type')
        if kind in ('reasoning', 'content'):
            yield event
        elif kind == 'error':
            yield event
            return
        elif kind == 'done':
            raw_text = event.get('text')

    yield {'type': 'stage', 'stage': '正在核对库存与过敏源'}
    plans, parse_error = parse_json_payload(raw_text)
    if parse_error or not _valid_recipe_plans(plans):
        logger.error('流式生成菜谱失败：meal=%s 原因=%s 开头=%r',
                     meal_label, parse_error or '结构异常', (raw_text or '')[:200])
        yield {'type': 'error', 'message': '模型返回的菜单格式不对，请重试'}
        return

    violations = find_allergen_violations(plans, allergens)
    if violations:
        # 过敏源是硬约束，不能靠剔菜挽救：含过敏源的菜整道都不该出现。
        logger.error('流式生成命中过敏源，放弃：meal=%s violations=%s', meal_label, violations)
        yield {
            'type': 'result',
            'reply': "生成的菜单出现家庭过敏源，已放弃保存，请重试。",
            'action': 'recommend_allergen_blocked',
            'data': {'allergens': allergens, 'violations': violations},
        }
        return

    # 流式路径不做纠正重试：前端已经在逐字渲染，再打一次模型会让用户对着已经出现的
    # 文字等十几秒。这里直接剔除越界的菜，违规明细只用于挑错误文案。
    duplicates, mismatches = find_recipe_constraint_violations(
        plans,
        meal_label,
        avoidance['hard'],
        avoidance.get('hard_ingredients'),
        avoidance.get('hard_signatures'),
    )
    cleaned, dropped = drop_invalid_dishes(
        plans,
        inventory_names,
        meal_label,
        avoidance['hard'],
        avoidance.get('hard_ingredients'),
        avoidance.get('hard_signatures'),
    )
    if not cleaned:
        logger.error(
            '流式生成后无可落库菜品：meal=%s duplicates=%s mismatches=%s',
            meal_label, duplicates, mismatches,
        )
        yield {
            'type': 'result',
            'reply': (
                "生成结果与当天其它餐次或上一版菜单重复，本次结果未保存，请重试。"
                if duplicates or mismatches else
                "现有库存配不出一道完整的菜，本次结果未保存。请补充食材后重试。"
            ),
            'action': (
                'recommend_constraints_blocked' if duplicates or mismatches
                else 'recommend_inventory_blocked'
            ),
            'data': {'missing_ingredients': find_missing_ingredients(plans, inventory_names)},
        }
        return

    yield {'type': 'stage', 'stage': '正在保存菜单'}
    plan_id = db.add_meal_plan(
        meal_type=meal_label,
        meal_time=datetime.now().strftime('%H:%M'),
        people_count=people_count,
        plan_a=cleaned.get('plan_a'),
        plan_b=cleaned.get('plan_b'),
    )
    missing = find_missing_ingredients(cleaned, inventory_names)
    reply = format_recommendation(
        meal_label, people_count, cleaned, plan_id, context['expiring_names'], missing
    )
    if dropped:
        reply += f"\n\n（为满足库存、餐次与去重约束，已去掉 {'、'.join(dropped[:5])}）"

    logger.info('流式生成菜谱完成：meal=%s plan_id=%s A=%s B=%s 剔除=%s',
                meal_label, plan_id,
                dish_names_of(cleaned.get('plan_a')) or '无',
                dish_names_of(cleaned.get('plan_b')) or '无',
                dropped or '无')

    yield {
        'type': 'result',
        'reply': reply,
        'action': 'recommend',
        'data': {
            'plan_id': plan_id,
            'meal_type': meal_label,
            'people_count': people_count,
            'plan_a': cleaned.get('plan_a'),
            'plan_b': cleaned.get('plan_b'),
            'missing_ingredients': missing,
            'dropped_dishes': dropped,
        },
    }


def generate_recommendation(meal_type: str, people_count: int, user_input: str = "") -> Dict:
    """
    生成菜谱推荐（增强版）

    Args:
        meal_type: 餐次
        people_count: 人数
        user_input: 用户原始输入

    Returns:
        dict: 推荐结果
    """
    # 这条路径实测 60 秒起，而 access 行只有一个总耗时 —— 分不清慢在读库、拼 prompt
    # 还是模型上，而三者的修法完全不同。分段计时让「63 秒花在哪」一行可答。
    timer = StageTimer(logger, '生成菜谱', meal=meal_type)
    try:
        with timer.stage('db'):
            context = _collect_recipe_inputs()
            if context['empty']:
                timer.done(结果='库存为空')
                return {
                    "reply": "🧊 冰箱是空的，没法推荐菜谱哦！\n\n"
                             "请先拍一张冰箱照片，识别食材后再来问我。",
                    "action": "inventory_empty"
                }
            inventory_summary = context['inventory_summary']
            profile_summary = context['profile_summary']
            expiring_names = context['expiring_names']
            nutrition_data = context['nutrition_data']
            inventory_names = context['inventory_names']
            allergens_for_log = context['allergens']
            avoidance = recipe_avoidance(meal_type)

        logger.info(
            '开始生成菜谱：meal=%s 人数=%s 库存=%d项 临期=%d项 过敏源=%s 用户原话=%r',
            meal_type, people_count, len(inventory_names), len(expiring_names),
            allergens_for_log or '无', (user_input or '')[:40]
        )

        with timer.stage('prompt'):
            # 构建增强 Prompt
            prompt = build_enhanced_recipe_prompt(
                inventory_summary=inventory_summary,
                profile_summary=profile_summary,
                meal_type=meal_type,
                people_count=people_count,
                nutrition_data=nutrition_data,
                expiring_items=expiring_names,
                user_input=user_input,
                inventory_count=len(inventory_names),
                hard_avoid=avoidance['hard'],
                soft_avoid=avoidance['soft'],
                hard_ingredients=avoidance.get('hard_ingredients'),
                hard_signatures=avoidance.get('hard_signatures'),
            )

        llm_call_id = uuid4().hex[:12]
        with timer.stage('llm'):
            # 调用 AI 生成菜谱
            result = call_ai_text_with_json(
                prompt, RECIPE_SYSTEM, purpose='recipe', call_id=llm_call_id,
                max_completion_tokens=RECIPE_MAX_COMPLETION_TOKENS,
            )

        if isinstance(result, dict) and 'error' in result:
            # 把真实原因记下来。给用户的是「出了点问题，请稍后再试」，而日志里必须能看出
            # 是网关 5xx、超时，还是模型答了但不是 JSON —— 三者的下一步动作完全不同。
            logger.error('生成菜谱失败：meal=%s 原因=%s 原文开头=%r',
                         meal_type, result.get('error'), str(result.get('raw') or '')[:200])
            timer.done(结果='模型失败')
            return {
                "reply": "抱歉，生成菜谱时出了点问题，请稍后再试。",
                "action": "recommend_error"
            }
        if not _valid_recipe_plans(result):
            logger.error('生成菜谱失败：meal=%s 原因=返回结构异常 type=%s 摘要=%r',
                         meal_type, type(result).__name__, str(result)[:200])
            timer.done(结果='结构异常')
            return {
                "reply": "抱歉，生成菜谱时出了点问题，请稍后再试。",
                "action": "recommend_error"
            }

        # 同时检查过敏源与库存，只纠正一次。分开重试会在两项都违规时连续调用三次模型，
        # 超过客户端等待上限；合并后最多两次，且纠正结果会重新通过两道检查。
        allergens = context['allergens']
        with timer.stage('validate'):
            violations = find_allergen_violations(result, allergens)
            missing = find_missing_ingredients(result, inventory_names)
            duplicates, mismatches = find_recipe_constraint_violations(
                result,
                meal_type,
                avoidance['hard'],
                avoidance.get('hard_ingredients'),
                avoidance.get('hard_signatures'),
            )
        total_missing = sum(len(v) for v in missing.values())
        degraded_dishes: List[str] = []
        if violations or total_missing or duplicates or mismatches:
            logger.warning(
                "菜谱约束不合格，纠正一次：meal=%s allergens=%s missing=%s duplicate=%s mismatch=%s",
                meal_type, violations or '无', missing or '无',
                duplicates or '无', mismatches or '无',
            )
            corrections = ["\n\n【上一次生成不合格，必须修正】"]
            if violations:
                corrections.append(
                    f"以下位置出现了过敏源：{'；'.join(violations)}。"
                    f"过敏源清单：{'、'.join(allergens)}。请整道换掉，不要只删除配料。"
                )
            if total_missing:
                corrections.append(
                    f"以下主料不在冰箱库存中：{json.dumps(missing, ensure_ascii=False)}。"
                    f"冰箱中可用的非调料食材只有：{'、'.join(inventory_names)}。"
                    "除基础调料外，每一种食材都必须来自库存；库存不足时减少菜品，不得列采购项。"
                )
            if duplicates:
                corrections.append(
                    f"以下菜与当天其它餐次、上一版菜单重复，或在同一套方案里出现了两次："
                    f"{'、'.join(duplicates)}。必须更换重复的核心食材或整道菜，不能只改菜名。"
                    "A、B 两套之间可以有相同的菜，不必强行拆开。"
                )
            if mismatches:
                corrections.append(
                    f"以下菜不符合本餐次：{'、'.join(mismatches)}。请按餐次要求整道替换。"
                )
            corrections.append("请重新生成完整的 A/B 两套方案。")
            with timer.stage('llm_constraint_retry'):
                retried = call_ai_text_with_json(
                    prompt + "\n".join(corrections),
                    RECIPE_SYSTEM,
                    purpose='recipe_constraint_retry',
                    call_id=llm_call_id,
                    attempt=2,
                    max_completion_tokens=RECIPE_MAX_COMPLETION_TOKENS,
                )

            retry_valid = _valid_recipe_plans(retried)
            retry_allergens = (
                find_allergen_violations(retried, allergens)
                if retry_valid and allergens else []
            )
            retry_missing = (
                find_missing_ingredients(retried, inventory_names) if retry_valid else {}
            )
            retry_duplicates, retry_mismatches = (
                find_recipe_constraint_violations(
                    retried,
                    meal_type,
                    avoidance['hard'],
                    avoidance.get('hard_ingredients'),
                    avoidance.get('hard_signatures'),
                )
                if retry_valid else ([], [])
            )
            if not retry_valid and violations:
                logger.error(
                    "过敏源纠正结果结构无效，拒绝回退到原方案：meal=%s violations=%s",
                    meal_type, violations,
                )
                timer.done(结果='过敏拦截')
                return {
                    "reply": "菜单的过敏源纠正失败，已放弃保存，请重试。",
                    "action": "recommend_allergen_blocked",
                    "data": {"allergens": allergens, "violations": violations},
                }
            if retry_allergens:
                logger.error(
                    "纠正后仍命中过敏源，放弃本次推荐：meal=%s violations=%s",
                    meal_type, retry_allergens
                )
                timer.done(结果='过敏拦截')
                return {
                    "reply": "纠正后的菜单仍出现家庭过敏源，已放弃保存，请重试。",
                    "action": "recommend_allergen_blocked",
                    "data": {"allergens": allergens, "violations": retry_allergens},
                }

            retry_total_missing = sum(len(v) for v in retry_missing.values())
            if not retry_valid or retry_total_missing or retry_duplicates or retry_mismatches:
                # 纠正两次仍越界：不再整套丢弃，剔掉越界的菜后落库。库存偏少时模型很难
                # 一次全中，而用户要的是「今天能照着做的菜」，不是「一份完美的两套方案」。
                base = retried if retry_valid else result
                cleaned, dropped = drop_invalid_dishes(
                    base,
                    inventory_names,
                    meal_type,
                    avoidance['hard'],
                    avoidance.get('hard_ingredients'),
                    avoidance.get('hard_signatures'),
                )
                if not cleaned:
                    logger.error(
                        "无法保留任何合规菜，放弃推荐：meal=%s valid=%s missing=%s duplicate=%s mismatch=%s",
                        meal_type, retry_valid, retry_missing,
                        retry_duplicates, retry_mismatches,
                    )
                    timer.done(结果='库存约束拦截')
                    return {
                        "reply": (
                            "生成结果与当天其它餐次或上一版菜单重复，本次结果未保存，请重试。"
                            if retry_duplicates or retry_mismatches else
                            "现有库存配不出一道完整的菜，本次结果未保存。请补充食材后重试。"
                        ),
                        "action": (
                            "recommend_constraints_blocked"
                            if retry_duplicates or retry_mismatches
                            else "recommend_inventory_blocked"
                        ),
                        "data": {"missing_ingredients": retry_missing},
                    }
                logger.warning(
                    "纠正后仍有越界菜品，剔除后落库：meal=%s 剔除=%s 保留=%s",
                    meal_type, dropped, {k: dish_names_of(v) for k, v in cleaned.items()}
                )
                result = cleaned
                missing = find_missing_ingredients(result, inventory_names)
                total_missing = 0
                degraded_dishes = dropped
            else:
                result = retried
                missing = retry_missing
                total_missing = 0
                logger.info("菜谱约束纠正成功：meal=%s", meal_type)

        with timer.stage('persist'):
            plan_id = db.add_meal_plan(
                meal_type=meal_type,
                meal_time=datetime.now().strftime('%H:%M'),
                people_count=people_count,
                plan_a=result.get('plan_a'),
                plan_b=result.get('plan_b')
            )

        # 产出摘要：菜名 + 缺料。有了它，「推荐没按库存来」这句抱怨可以直接在日志里对账 ——
        # 看一眼生成了什么、库存里有什么，不必去复现一次 60 秒的生成。
        logger.info(
            '菜谱生成完成：meal=%s plan_id=%s A=%s B=%s 缺料=%s',
            meal_type, plan_id,
            dish_names_of(result.get('plan_a')) or '无',
            dish_names_of(result.get('plan_b')) or '无',
            {k: v for k, v in missing.items() if v} or '无'
        )

        # 格式化回复
        reply = format_recommendation(
            meal_type, people_count, result, plan_id, expiring_names, missing
        )
        if degraded_dishes:
            # 必须说出来：菜品被剔掉而界面上不提，用户看到的是「AI 少推荐了几道菜」，
            # 无从知道是库存不够还是模型偷懒。
            reply += (
                f"\n\n（为满足库存、餐次与去重约束，已去掉 "
                f"{'、'.join(degraded_dishes[:5])}）"
            )
        timer.done(plan_id=plan_id, 缺料=total_missing, 剔除=len(degraded_dishes) or None)

        return {
            "reply": reply,
            "action": "recommend",
            "data": {
                "plan_id": plan_id,
                "meal_type": meal_type,
                "people_count": people_count,
                "plan_a": result.get('plan_a'),
                "plan_b": result.get('plan_b'),
                # 结构化带出采购清单，供 App 分区展示。reply 里也有一份文字版 ——
                # 两者都要，因为对话链路只看 reply，而菜谱页要按方案分区渲染。
                "missing_ingredients": missing,
                # 被剔掉的菜名：可能缺料、重复或不符合餐次，供 App 统一提示。
                "dropped_dishes": degraded_dishes,
            }
        }
    finally:
        # 异常路径也要留下耗时行：一次 60 秒后抛错的请求，正是最需要知道时间花在哪的那次。
        # done() 自带幂等，正常路径上面已经调过，这里不会重复输出。
        timer.done(结果='异常')
# 用户原话进 prompt 的长度上界（字符）。200 字足够装下任何一句真正的点菜要求，
# 再长的通常是粘贴 —— 而 prompt 里塞进几千字会把库存与画像挤出模型的注意力。
USER_INPUT_PROMPT_LIMIT = 200


def build_enhanced_recipe_prompt(
    inventory_summary: str,
    profile_summary: str,
    meal_type: str,
    people_count: int,
    nutrition_data: Optional[str] = None,
    expiring_items: List[str] = None,
    user_input: str = "",
    inventory_count: int = 0,
    hard_avoid: List[str] = None,
    soft_avoid: List[str] = None,
    hard_ingredients: List[str] = None,
    hard_signatures: List[List[str]] = None,
) -> str:
    """构建增强版菜谱推荐 Prompt。

    @param inventory_count 库存项数，决定要几套方案（见 [plan_shape_hint]）。
    """

    expiring_section = ""
    if expiring_items:
        expiring_section = f"""
【即将过期的食材（优先使用）】
{', '.join(expiring_items)}
这些食材应该优先使用，避免浪费。
"""

    nutrition_section = ""
    if nutrition_data:
        nutrition_section = f"""
【食材营养参考数据（每100g）】
{nutrition_data}
"""

    # ── 用户要求分两段进 prompt，两段是互补的，缺一段都会让「和 AI 对话调菜单」名不副实 ──
    #
    # 1) 下面这四个关键词桶做的是**结构化归纳**：命中就给模型一个短标签（「清淡少油」）。
    #    保留它是因为模型对显式列出的约束遵守得更好 —— 一句口语里的「随便弄点清淡的」
    #    容易被当成寒暄，而标注成一条约束就会被当成配菜条件。
    # 2) 但桶是**关键词匹配**，只认这 8 个词。用户说「今天想吃点辣的」「想吃川菜」
    #    「别放肉」「孩子想吃甜的」时一个桶都不命中，原话又从不进 prompt，模型就
    #    压根看不到这些要求 —— 配出来的菜与要求无关，而 App 侧一切正常、后端也不报错。
    #    所以原话必须**原文追加**进去（追加而不是替换：删掉桶会让既有行为回退）。
    #
    # 刻意不在这里做任何过滤或改写（剔"脏话"、判"无关内容"）：那是猜用户意图，
    # 猜错了没有任何一处会报错。原话进去，让模型自己处理。
    user_hint = ""
    if user_input:
        special_requests = []
        if any(kw in user_input for kw in ['简单', '快手', '快速']):
            special_requests.append("简单快手")
        if any(kw in user_input for kw in ['营养', '健康']):
            special_requests.append("营养健康")
        if any(kw in user_input for kw in ['清淡', '少油']):
            special_requests.append("清淡少油")
        if any(kw in user_input for kw in ['下饭', '好吃']):
            special_requests.append("下饭好吃")
        if special_requests:
            user_hint = f"\n【用户特殊要求】{', '.join(special_requests)}"

        # 截断有上界：用户可能粘一大段进来，几千字会把库存与画像那部分上下文挤掉，
        # 而那才是配菜真正依赖的信息。超长时截断并留个省略号，别让模型以为句子说完了。
        raw = user_input.strip()
        if len(raw) > USER_INPUT_PROMPT_LIMIT:
            raw = raw[:USER_INPUT_PROMPT_LIMIT] + '…'
        if raw:
            # 原话为空（或只有空白）时整节不输出 —— 给模型一个空标题只会让它自己编内容
            user_hint += (
                f"\n\n【用户本次的具体要求】\n{raw}\n"
                "请在满足上述要求的前提下配菜；"
                "若冰箱里的食材无法满足，请在 skip_reason 里说明原因。"
            )

    meal_key = meals.meal_key(meal_type)
    meal_guidance = {
        'breakfast': (
            '早餐生成1-2道，通常20分钟内完成；优先粥、面点、蛋奶豆饮、水果等家庭早餐。'
            '不得把红烧肉、干锅、水煮肉片、烤鱼、火锅、扣肉、大盘鸡、酸菜鱼当早餐。'
        ),
        'lunch': '午餐是一天中较完整的一餐，按库存做2-3道荤素搭配的家常菜。',
        'dinner': '晚餐按库存做1-3道较清淡、少油盐的家常菜，并避开当天午餐原菜。',
    }.get(meal_key, '菜品应符合对应餐次的普通家庭饮食习惯。')
    avoid_section = ''
    if hard_avoid:
        avoid_section += f"\n【本次禁止重复的菜名】\n{'、'.join(hard_avoid)}\n"
    if soft_avoid:
        avoid_section += f"\n【最近7天菜名（库存允许时尽量换做法）】\n{'、'.join(soft_avoid)}\n"
    if hard_ingredients:
        avoid_section += (
            "\n【当天其它餐次已经使用的核心食材（本餐禁止再次使用）】\n"
            f"{'、'.join(hard_ingredients)}\n"
            "库存不足时减少菜品数量，不得用库存外食材凑数。\n"
        )
    if hard_signatures:
        combinations = '；'.join(' + '.join(items) for items in hard_signatures)
        avoid_section += (
            "\n【上一版当前餐次已经使用的食材组合（重新生成时必须换掉）】\n"
            f"{combinations}\n不能只修改菜名或修饰词后继续使用同一组合。\n"
        )

    return f"""
请根据以下信息推荐菜谱：

【冰箱库存 —— 主料只能从这里选】
{inventory_summary}

【可以直接使用的基础调料（不必在库存里）】
{'、'.join(PANTRY_PROMPT_SAMPLE)} 等常见调味料

【家庭画像】
{profile_summary}
{expiring_section}{nutrition_section}
【用餐信息】
- 餐次：{meal_type}
- 用餐人数：{people_count}人
- 餐次要求：{meal_guidance}
{avoid_section}

【本次方案要求】
{plan_shape_hint(inventory_count)}
{user_hint}

请生成菜谱方案，返回JSON格式：
{{
    "plan_a": {{
        "dishes": [
            {{
                "name": "菜名",
                "ingredients": [
                    {{"name": "食材名", "amount": "用量"}}
                ],
                "cooking_time": "烹饪时间",
                "difficulty": "简单/中等/困难",
                "nutrition_highlight": "营养亮点（可选）",
                "steps": ["步骤1", "步骤2", "步骤3"],
                "skip_ingredients": ["需要跳过的配料"],
                "skip_reason": "跳过原因（如果没有则为空字符串）"
            }}
        ],
        "total_time": "总时间",
        "total_difficulty": "整体难度"
    }},
    "plan_b": {{
        "dishes": [...同上格式...],
        "total_time": "总时间",
        "total_difficulty": "整体难度"
    }}
}}

推荐原则：
1. 【最高优先级】过敏源绝对禁止：家庭画像里标注为「过敏禁用」的食材，不得作为主料、
   配料或调料出现，也不得使用含有它的加工食品（例如对乳制品过敏就不能用黄油、奶酪）。
   这一条不能为了满足其他任何要求而放宽；找不到合适的菜就换一道，不要标注「去掉该配料」。
2. 【硬约束】每道菜的**主料必须来自上面的冰箱库存清单**，不要凭想象添加冰箱里没有的
   蔬菜、肉类、蛋奶或豆制品。基础调料不受此限。库存实在配不出一道完整的菜时，
   宁可减少菜品数量，也不要用库存里没有的主料凑数。
3. 优先使用快过期的食材
4. 主材料忌口：完全避开该食材，不推荐含此食材的菜
5. 配料忌口：保留菜品，但标注去掉该配料
6. 口味忌口：调整做法或推荐替代菜
7. 菜品要家常、易做、适合家庭
8. 尽量荤素搭配；宁可少做几道，也不要用库存里没有的主料来凑够道数
9. 每道菜给出3-5条简短、可直接照做的步骤，每条尽量不超过24个汉字
10. 考虑营养均衡
"""


def format_recommendation(
    meal_type: str,
    people_count: int,
    plans: Dict,
    plan_id: int,
    expiring_items: List[str] = None,
    missing_ingredients: Dict[str, List[str]] = None
) -> str:
    """格式化推荐结果

    @param missing_ingredients 每套方案里库存外的食材（见 [find_missing_ingredients]）。
        必须显示出来：用户的原话是「推荐的菜并不是按冰箱里的库存菜来的」，而在此之前
        缺料这件事在界面上完全不可见 —— 只能自己一道道核对配料。
    """
    lines = [f"🍽️ {meal_type}推荐（{people_count}人份）\n"]

    if expiring_items:
        lines.append(f"💡 优先使用即将过期的食材：{', '.join(expiring_items)}\n")

    for plan_name, plan_key in [('A方案', 'plan_a'), ('B方案', 'plan_b')]:
        plan = plans.get(plan_key)
        if not plan:
            continue

        lines.append(f"━━━ {plan_name} ━━━")

        total_calories = 0
        total_protein = 0
        total_fat = 0

        for dish in plan.get('dishes', []):
            # 菜名和难度
            difficulty = dish.get('difficulty', '')
            diff_emoji = {'简单': '🟢', '中等': '🟡', '困难': '🔴'}.get(difficulty, '')
            lines.append(f"\n🥘 {dish['name']} {diff_emoji}")
            lines.append(f"   ⏱️ {dish.get('cooking_time', '未知')}")

            # 食材（带营养信息）
            ingredients = dish.get('ingredients', [])
            if ingredients:
                ing_strs = []
                for ing in ingredients:
                    if isinstance(ing, dict):
                        name = ing.get('name', '')
                        amount = ing.get('amount', '')
                        ing_strs.append(f"{name}{amount}")

                        # 尝试获取营养数据
                        nutrition = db.get_nutrition_by_name(name)
                        if nutrition:
                            if nutrition.get('energy_kcal'):
                                total_calories += nutrition['energy_kcal']
                            if nutrition.get('protein'):
                                total_protein += nutrition['protein']
                            if nutrition.get('fat'):
                                total_fat += nutrition['fat']
                    else:
                        ing_strs.append(str(ing))
                lines.append(f"   🥬 {', '.join(ing_strs)}")

            # 营养亮点
            if dish.get('nutrition_highlight'):
                lines.append(f"   💪 {dish['nutrition_highlight']}")

            # 忌口处理
            if dish.get('skip_ingredients'):
                lines.append(f"   ⚠️ 不放：{','.join(dish['skip_ingredients'])}")
                if dish.get('skip_reason'):
                    lines.append(f"      原因：{dish['skip_reason']}")

        # 总结
        if plan.get('total_time'):
            lines.append(f"\n   ⏰ 总用时：{plan['total_time']}")
        if plan.get('total_difficulty'):
            lines.append(f"   📊 整体难度：{plan['total_difficulty']}")

        # 营养估算
        if total_calories > 0:
            lines.append(f"   🔥 预估热量：约{int(total_calories)}kcal")
        if total_protein > 0:
            lines.append(f"   💪 蛋白质：约{int(total_protein)}g")

        # 采购清单按方案给：两套方案缺的东西不一样，合在一起就没法据此挑方案了 ——
        # 而"哪套不用出门买"往往正是选择的依据。
        lacking = (missing_ingredients or {}).get(plan_key) or []
        if lacking:
            lines.append(f"   🛒 需采购 {len(lacking)} 样：{'、'.join(lacking)}")
        else:
            lines.append("   ✅ 全部用现有食材")

        lines.append("")

    lines.append("💡 回复「选A」或「选B」确认方案，我会自动扣减库存")

    return "\n".join(lines)


# ==================== 确认方案 ====================

def handle_confirm(user_input: str) -> Dict:
    """处理确认请求"""
    if any(kw in user_input for kw in ['选A', 'A', '第一个', '方案A']):
        selected = 'A'
    elif any(kw in user_input for kw in ['选B', 'B', '第二个', '方案B']):
        selected = 'B'
    else:
        return {
            "reply": "请选择 A 或 B 方案？",
            "action": "ask_selection"
        }

    plans = db.get_pending_plans()
    if not plans:
        return {
            "reply": "没有待确认的用餐计划，请先让我推荐菜谱。",
            "action": "no_pending_plan"
        }

    # 取最新的计划
    plan = plans[0]
    return confirm_plan(plan['id'], selected)


def confirm_plan(plan_id: int, selected_plan: str) -> Dict:
    """
    确认菜谱方案并扣减库存

    Args:
        plan_id: 计划ID
        selected_plan: 选择的方案（A/B）

    Returns:
        dict: 确认结果
    """
    plans = db.get_pending_plans()
    plan = None
    for p in plans:
        if p['id'] == plan_id:
            plan = p
            break

    if not plan:
        return {
            "reply": "未找到该用餐计划，可能已过期。",
            "action": "confirm_error"
        }

    plan_data = plan.get(f'plan_{selected_plan.lower()}')
    if not plan_data:
        return {
            "reply": f"未找到{selected_plan}方案。",
            "action": "confirm_error"
        }

    # 提取需要扣减的食材
    items_to_deduct = []
    for dish in plan_data.get('dishes', []):
        for ing in dish.get('ingredients', []):
            if isinstance(ing, dict):
                name = ing.get('name', '')
                amount = ing.get('amount', '')
                if name:
                    items_to_deduct.append({"name": name, **parse_deduction_amount(amount)})

    # 扣减库存
    try:
        deduct_results = db.deduct_inventory(items_to_deduct)
    except ValueError as e:
        return {
            "reply": f"库存不足：{str(e)}",
            "action": "deduct_error"
        }

    db.confirm_meal_plan(plan_id, selected_plan)

    meal_type = plan.get('meal_type', '餐')
    return {
        "reply": format_confirm_reply(selected_plan, meal_type, deduct_results),
        "action": "confirm",
        "data": deduct_results
    }


def format_confirm_reply(
    selected_plan: str, meal_type: str, deduct_results: List[Dict]
) -> str:
    """拼「已确认 + 库存变化」的回复。

    **这个函数不允许抛异常**。它跑在两次写库（扣减 + 置 confirmed）之后，抛出去会被上层
    兜成 500，而用户看到失败会再点一次确认 —— 于是同一份菜谱的食材被扣两遍，库存与实物
    从此不符，而后续菜谱严格按库存出菜，错误会一路传下去。真机上就发生过这一幕：这里原本
    读的是 `r['old']`/`r['new']` 两个 [database.deduct_inventory] 从不返回的键，
    **只要扣减成功就 KeyError**（失败路径返回 `{'name','error'}` 反而不崩，所以「库存不足」
    时一切正常、成功时才炸）。因此全部字段一律走 `.get()`，取不到就少显示一行，不抛。
    """
    lines = [f"✅ 已选择{selected_plan}方案\n", "📦 库存更新："]
    for r in deduct_results:
        name = r.get('name') or '（未命名食材）'
        if r.get('error'):
            lines.append(f"  • {name}：{r['error']}")
            continue
        unit = r.get('unit') or ''
        # 扣减可能跨多条记录（FIFO），逐条的明细在 details 里；总量用 deducted。
        details = r.get('details') or []
        before = sum(
            (d.get('deducted') or 0) + (d.get('remaining') or 0)
            for d in details if isinstance(d, dict)
        )
        after = sum(
            (d.get('remaining') or 0) for d in details if isinstance(d, dict)
        )
        if details:
            lines.append(f"  • {name}：{_trim_number(before)}→{_trim_number(after)}{unit}")
        else:
            lines.append(f"  • {name}：-{_trim_number(r.get('deducted') or 0)}{unit}")

    lines.append(f"\n祝你{meal_type}愉快！🎉")
    return "\n".join(lines)


def _trim_number(value) -> str:
    """`3.0` 显示成 `3`，`2.5` 保持 `2.5`。库存量是浮点，但整数占绝大多数。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f'{number:g}'


def parse_amount(amount_str: str) -> float:
    """解析用量字符串"""
    if not amount_str:
        return 1

    nums = re.findall(r'[\d.]+', str(amount_str))
    if nums:
        return float(nums[0])
    return 1


def parse_deduction_amount(amount_str: str) -> Dict[str, float]:
    """把菜谱用量保留为计数或克重，供库存选择正确事实字段。"""
    amount = parse_amount(amount_str)
    text = str(amount_str or '').lower()
    if '千克' in text or 'kg' in text:
        return {'weight_grams': amount * 1000}
    if '斤' in text:
        return {'weight_grams': amount * 500}
    if '克' in text or re.search(r'(?<![a-z])g(?![a-z])', text):
        return {'weight_grams': amount}
    return {'quantity': amount}


# ==================== 帮助 ====================

def handle_help() -> Dict:
    """处理帮助请求"""
    return {
        "reply": """🤖 我是你的智能冰箱助手！

我能帮你：

📸 **拍照入库**
拍一张冰箱照片，我帮你识别食材并记录
• 支持手机拍照、相册选择
• 自动识别食材名称、数量、保质期
• 支持批量识别

🧊 **管理库存**
• 问"冰箱有什么"查看全部库存
• 问"快过期的"查看即将过期食材
• 问"蔬菜类"按分类查看
• 支持手动添加、修改、删除

🍽️ **推荐菜谱**
• 说"该做晚饭了"，我根据库存推荐A/B方案
• 优先使用快过期食材，减少浪费
• 考虑家人忌口和营养均衡
• 选方案后自动扣减库存

👤 **管理画像**
• 告诉我家里几口人，谁有什么忌口
• 我会记住每个人的偏好
• 推荐菜谱时自动避开忌口

⏰ **定时提醒**
• 设好用餐时间，我会提前推荐菜谱

试试看吧！""",
        "action": "help"
    }


# ==================== 通用对话 ====================

def handle_general_chat(user_input: str, context: str, history_text: str = "") -> Dict:
    """处理通用对话"""
    prompt = build_chat_prompt(user_input, context, history_text)
    reply = call_ai_text(prompt, purpose='chat')

    return {
        "reply": reply,
        "action": "chat"
    }


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("Agent Module Test")
    print("=" * 50)

    # 测试意图识别
    test_cases = [
        "家里3口人，爸爸不吃辣",
        "冰箱有什么",
        "该做晚饭了，3个人吃",
        "选A",
        "帮助",
        "今天天气怎么样"
    ]

    print("\nIntent Detection:")
    for text in test_cases:
        intent = detect_intent(text)
        print(f"  [{intent}] {text}")

    # 测试餐次解析
    print("\nMeal Info Parsing:")
    meal_tests = [
        "该做晚饭了",
        "3个人吃早餐",
        "推荐午餐，4人份"
    ]
    for text in meal_tests:
        info = parse_meal_info(text)
        print(f"  {text} -> {info}")

    print("\nDone!")
