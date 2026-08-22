"""
食材识别模块 — 调用 AI v2.5 识别冰箱照片中的食材
集成：图片预处理、模糊检测、重试机制、去重检查、置信度处理
"""
import json
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from .ai_client import call_ai_vision
from .prompts import FOOD_RECOGNITION_PROMPT
from .inventory_units import normalize_inventory_amount
from .image_processor import preprocess_image, cleanup_temp_files
from . import database as db


# ==================== 配置 ====================

MAX_RETRIES = 3
RETRY_DELAY = 1  # 秒
# ==================== API 调用（带重试）====================

def call_with_retry(func, max_retries=MAX_RETRIES, retry_delay=RETRY_DELAY):
    """
    带重试的 API 调用

    Args:
        func: 调用函数
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）

    Returns:
        函数返回值

    Raises:
        Exception: 最后一次重试仍失败
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            result = func()
            return result
        except Exception as e:
            last_error = e
            error_msg = str(e).lower()

            if any(keyword in error_msg for keyword in ['timeout', 'rate limit', '429', '503', 'network']):
                if attempt < max_retries - 1:
                    delay = retry_delay * (attempt + 1)
                    time.sleep(delay)
                    continue

            # 不可重试的错误，直接抛出
            raise

    raise last_error


# ==================== 食材识别 ====================

def recognize_food(image_path: str) -> List[Dict]:
    """
    识别图片中的食材

    Args:
        image_path: 图片文件路径

    Returns:
        list: 识别到的食材列表
    """
    # 调用 AI 视觉模型（带重试）
    result = call_with_retry(
        lambda: call_ai_vision(
            prompt=FOOD_RECOGNITION_PROMPT,
            image_path=image_path
        )
    )

    if isinstance(result, str):
        try:
            items = json.loads(result)
        except json.JSONDecodeError:
            items = extract_json_from_text(result)
    else:
        items = result

    # 验证和标准化
    if isinstance(items, list):
        items = [standardize_item(item) for item in items]
    elif isinstance(items, dict) and 'error' in items:
        return items
    else:
        items = []

    return items


def extract_json_from_text(text: str) -> List:
    """从文本中提取JSON"""
    # 尝试找 JSON 数组
    start = text.find('[')
    if start != -1:
        bracket_count = 0
        for i in range(start, len(text)):
            if text[i] == '[':
                bracket_count += 1
            elif text[i] == ']':
                bracket_count -= 1
            if bracket_count == 0:
                try:
                    return json.loads(text[start:i+1])
                except Exception:
                    break

    # 尝试找 JSON 对象
    start = text.find('{')
    if start != -1:
        brace_count = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
            if brace_count == 0:
                try:
                    return [json.loads(text[start:i+1])]
                except Exception:
                    break

    return []


# ==================== 数据标准化 ====================

def standardize_item(item: Dict) -> Dict:
    """
    标准化食材数据

    Args:
        item: 原始食材数据

    Returns:
        dict: 标准化后的数据
    """
    name = item.get('name', '未知')
    category = item.get('category', '其他')
    confidence = item.get('confidence', '中')

    # 分类校验
    category = db.normalize_category(category)
    if category not in db.DEFAULT_CATEGORIES:
        category = '其他'

    # 数量解析
    quantity = parse_number(item.get('quantity', 1))
    unit = str(item.get('unit') or '个').strip()
    weight_grams = item.get('weight_grams')
    weight_grams = parse_number(weight_grams) if weight_grams not in (None, '') else None
    amount = normalize_inventory_amount(
        name, quantity, unit, weight_grams, confidence,
        correct_recognized_water=True,
    )
    quantity = amount['quantity']
    unit = amount['unit']
    weight_grams = amount['weight_grams']
    confidence = amount['confidence']

    # 日期解析
    production_date = parse_date(item.get('production_date'))
    expiry_date = parse_date(item.get('expiry_date'))
    shelf_life_days = item.get('shelf_life_days')
    try:
        production_date, shelf_life_days, expiry_date = db.resolve_shelf_life(
            production_date, shelf_life_days, expiry_date
        )
    except ValueError:
        production_date, shelf_life_days, expiry_date = None, None, None

    return {
        'name': name,
        'category': category,
        'quantity': quantity,
        'unit': unit,
        'weight_grams': weight_grams,
        'production_date': production_date,
        'shelf_life_days': shelf_life_days,
        'expiry_date': expiry_date,
        'recognition_derived': any(item.get(key) not in (None, '') for key in (
            'production_date', 'shelf_life_days', 'expiry_date'
        )),
        'confidence': confidence,
        'need_confirm': confidence == '低' or name == '未知' or amount['corrected_water_unit'],
        'original_data': item  # 保留原始数据
    }


def parse_number(value) -> float:
    """解析数字"""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        import re
        # 尝试提取范围（如 "3-5"）
        if isinstance(value, str) and '-' in value:
            parts = value.split('-')
            if len(parts) == 2:
                return (float(parts[0]) + float(parts[1])) / 2
        nums = re.findall(r'[\d.]+', str(value))
        if nums:
            return float(nums[0])
    except Exception:
        pass
    return 1.0


def parse_date(value) -> Optional[str]:
    """解析日期"""
    if not value:
        return None

    # 如果已经是 YYYY-MM-DD 格式
    if isinstance(value, str) and len(value) == 10 and value[4] == '-':
        try:
            datetime.strptime(value, '%Y-%m-%d')
            return value
        except Exception:
            pass

    # 尝试其他格式
    formats = [
        '%Y/%m/%d',
        '%Y.%m.%d',
        '%Y年%m月%d日',
        '%m/%d/%Y',
        '%d/%m/%Y',
        '%Y%m%d',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(str(value), fmt).strftime('%Y-%m-%d')
        except Exception:
            continue

    return None


# ==================== 去重检查 ====================

def check_duplicates(items: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    检查与现有库存的重复

    Args:
        items: 识别到的食材列表

    Returns:
        (新食材列表, 重复食材列表)
    """
    existing = db.get_inventory(page_size=1000)['items']
    existing_map = {}

    for item in existing:
        name = item['name']
        if name not in existing_map:
            existing_map[name] = []
        existing_map[name].append(item)

    new_items = []
    duplicate_items = []

    for item in items:
        name = item['name']
        if name in existing_map:
            duplicate_items.append({
                'new': item,
                'existing': existing_map[name],
                'action': 'merge'  # 默认合并
            })
        else:
            new_items.append(item)

    return new_items, duplicate_items


def merge_with_existing(duplicate_items: List[Dict]) -> List[Dict]:
    """
    合并重复食材

    Args:
        duplicate_items: 重复食材列表

    Returns:
        list: 更新后的食材列表
    """
    updated = []

    for dup in duplicate_items:
        new = dup['new']
        existing_list = dup['existing']

        if dup['action'] == 'merge' and existing_list:
            # 合并到第一个匹配项
            exist = existing_list[0]
            new_quantity = exist['quantity'] + new['quantity']
            new_weight = (exist.get('weight_grams') or 0) + (new.get('weight_grams') or 0)

            db.update_inventory_item(
                exist['id'],
                quantity=new_quantity,
                weight_grams=new_weight or None,
            )

            updated.append({
                **exist,
                'quantity': new_quantity,
                'weight_grams': new_weight or None,
                'merged': True,
                'added_quantity': new['quantity']
            })

        elif dup['action'] == 'replace' and existing_list:
            # 替换
            exist = existing_list[0]
            db.update_inventory_item(
                exist['id'],
                quantity=new['quantity'],
                weight_grams=new.get('weight_grams'),
                production_date=new.get('production_date'),
                shelf_life_days=new.get('shelf_life_days'),
                expiry_date=new.get('expiry_date'),
                confidence=new.get('confidence', '中')
            )

            updated.append({
                **new,
                'id': exist['id'],
                'replaced': True
            })

        elif dup['action'] == 'add':
            # 新增（即使存在）
            item_id = db.add_inventory_item(
                name=new['name'],
                category=new['category'],
                quantity=new['quantity'],
                unit=new['unit'],
                weight_grams=new.get('weight_grams'),
                production_date=new.get('production_date'),
                shelf_life_days=new.get('shelf_life_days'),
                expiry_date=new.get('expiry_date'),
                confidence=new.get('confidence', '中'),
                photo_path=new.get('photo_path')
            )
            new['id'] = item_id
            updated.append(new)

    return updated


# ==================== 保存到库存 ====================

def save_to_inventory(items: List[Dict], photo_path: str = None) -> List[Dict]:
    """
    将识别结果保存到数据库

    Args:
        items: 食材列表
        photo_path: 照片路径

    Returns:
        list: 保存的食材列表
    """
    saved = []
    for item in items:
        # 跳过需要确认的低置信度项
        if item.get('need_confirm') and not item.get('force_save'):
            continue

        item_id = db.add_inventory_item(
            name=item['name'],
            category=item['category'],
            quantity=item['quantity'],
            unit=item['unit'],
            weight_grams=item.get('weight_grams'),
            production_date=item.get('production_date'),
            shelf_life_days=item.get('shelf_life_days'),
            expiry_date=item.get('expiry_date'),
            confidence=item.get('confidence', '中'),
            photo_path=photo_path
        )
        item['id'] = item_id
        saved.append(item)

    return saved


# ==================== 格式化结果 ====================

def format_recognition_result(items: List[Dict], duplicates: List[Dict] = None) -> str:
    """
    格式化识别结果为用户友好的文本

    Args:
        items: 食材列表
        duplicates: 重复食材列表

    Returns:
        str: 格式化的文本
    """
    if not items and not duplicates:
        return "未识别到任何食材 😅"

    lines = []

    # 新增的食材
    if items:
        lines.append(f"🔍 识别到 {len(items)} 种新食材：\n")
        for i, item in enumerate(items, 1):
            line = f"{i}. {item['name']} × {item['quantity']}{item['unit']}"
            if item.get('expiry_date'):
                line += f"（保质期至 {item['expiry_date']}）"
            if item.get('confidence') == '低':
                line += " ⚠️识别不确定"
            lines.append(line)

    # 合并的食材
    if duplicates:
        merged = [d for d in duplicates if d.get('merged')]
        if merged:
            lines.append("\n📦 合并到已有库存：")
            for d in merged:
                lines.append(f"- {d['name']} +{d['added_quantity']}{d['unit']}（现有 {d['quantity']}{d['unit']}）")

    if items:
        lines.append("\n✅ 已存入冰箱库存")

    return "\n".join(lines)


# ==================== 主函数 ====================

def process_photo(image_path: str, auto_save: bool = True) -> Dict:
    """
    处理照片：预处理 → 识别 → 去重 → 保存

    Args:
        image_path: 图片文件路径
        auto_save: 是否自动保存（低置信度仍需确认）

    Returns:
        dict: {
            "success": bool,
            "items": 识别到的食材列表,
            "duplicates": 重复食材列表,
            "message": 用户友好的消息,
            "quality_info": 图片质量信息,
            "processing_info": 处理信息
        }
    """
    result = {
        "success": False,
        "items": [],
        "duplicates": [],
        "new_items": [],
        "message": "",
        "quality_info": {},
        "processing_info": {},
        "need_confirm": False
    }

    # 1. 图片预处理
    processed_path, process_info = preprocess_image(image_path)
    result['processing_info'] = process_info

    if processed_path is None:
        result['message'] = "图片质量检查失败：\n" + "\n".join(
            ["❌ " + e for e in process_info['quality_check']['errors']]
        )
        result['quality_info'] = process_info['quality_check']
        return result

    result['quality_info'] = process_info['quality_check']

    try:
        # 2. 识别食材
        items = recognize_food(processed_path)

        if isinstance(items, dict) and 'error' in items:
            result['message'] = f"识别失败：{items.get('error')}"
            return result

        if not items:
            result['message'] = "未识别到任何食材，请重新拍照或调整角度"
            return result

        # 3. 去重检查
        new_items, duplicate_items = check_duplicates(items)

        # 4. 合并重复项
        if duplicate_items:
            # 只丢返回值、不丢调用：merge_with_existing() 内部会 db.update_inventory_item()
            # 把重复项的数量合并进已有库存。删掉这一行等于少写一次库存。
            merge_with_existing(duplicate_items)
            result['duplicates'] = duplicate_items

        # 5. 保存新项
        if auto_save and new_items:
            saved_items = save_to_inventory(new_items)
            result['items'] = saved_items
            result['new_items'] = saved_items
        else:
            result['items'] = new_items
            result['new_items'] = new_items
            result['need_confirm'] = True

        # 6. 检查是否有低置信度项
        low_confidence = [i for i in items if i.get('need_confirm')]
        if low_confidence:
            result['need_confirm'] = True

        # 7. 格式化结果
        result['message'] = format_recognition_result(
            result['new_items'],
            result['duplicates']
        )
        result['success'] = True

    finally:
        if processed_path != image_path:
            cleanup_temp_files(image_path)

    return result


def process_photo_with_confirm(image_path: str) -> Dict:
    """
    处理照片但不自动保存（用于确认流程）

    Args:
        image_path: 图片文件路径

    Returns:
        dict: 识别结果（需要用户确认后保存）
    """
    return process_photo(image_path, auto_save=False)


def confirm_and_save(items: List[Dict], photo_path: str = None) -> Dict:
    """
    用户确认后保存

    Args:
        items: 用户确认/修改后的食材列表
        photo_path: 照照路径

    Returns:
        dict: 保存结果
    """
    saved = []
    for item in items:
        if item.get('id'):
            db.update_inventory_item(
                item['id'],
                name=item.get('name'),
                category=item.get('category'),
                quantity=item.get('quantity'),
                unit=item.get('unit'),
                expiry_date=item.get('expiry_date')
            )
            saved.append(item)
        else:
            # 新增项
            item_id = db.add_inventory_item(
                name=item['name'],
                category=item['category'],
                quantity=item['quantity'],
                unit=item.get('unit', '个'),
                production_date=item.get('production_date'),
                expiry_date=item.get('expiry_date'),
                confidence=item.get('confidence', '中'),
                photo_path=None
            )
            item['id'] = item_id
            saved.append(item)

    return {
        "success": True,
        "items": saved,
        "message": f"✅ 已保存 {len(saved)} 种食材到库存"
    }


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("用法: python recognition.py <图片路径>")
        sys.exit(1)

    image_path = sys.argv[1]

    print(f"处理图片: {image_path}")
    print()

    result = process_photo(image_path)

    print(f"成功: {result['success']}")
    print(f"消息: {result['message']}")
    print(f"食材: {len(result['items'])} 种")
    print(f"重复: {len(result['duplicates'])} 种")
    print(f"需要确认: {result['need_confirm']}")
