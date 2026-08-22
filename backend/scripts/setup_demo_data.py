#!/usr/bin/env python3
"""
演示数据初始化脚本

创建：
1. 家庭成员画像（3人）
2. 冰箱库存（15种食材）
3. 营养数据（如未导入）
"""

import sys
import os
import io
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import database as db  # noqa: E402


def setup_family():
    """创建家庭成员画像"""
    print("\n=== 创建家庭成员 ===")

    members = [
        {
            "member_name": "爸爸",
            "height": 175,
            "weight": 72,
            "age": 35,
            "allergies": '["海鲜"]',
            "dislikes_main": '[]',
            "dislikes_ingredient": '["香菜"]',
            "dislikes_taste": '["太甜"]',
            "health_notes": "健身增肌中，需要高蛋白"
        },
        {
            "member_name": "妈妈",
            "height": 162,
            "weight": 55,
            "age": 32,
            "allergies": '[]',
            "dislikes_main": '[]',
            "dislikes_ingredient": '["苦瓜"]',
            "dislikes_taste": '["太辣"]',
            "health_notes": "减脂期，控制碳水"
        },
        {
            "member_name": "小明",
            "height": 130,
            "weight": 28,
            "age": 8,
            "allergies": '["牛奶"]',
            "dislikes_main": '[]',
            "dislikes_ingredient": '["青椒"]',
            "dislikes_taste": '["太辣", "太苦"]',
            "health_notes": "成长期，不挑食"
        }
    ]

    for m in members:
        db.add_member(**m)
        print(f"  + {m['member_name']}: {m['age']}岁, {m['health_notes']}")

    print(f"✓ 已创建 {len(members)} 个家庭成员")


def setup_inventory():
    """创建冰箱库存"""
    print("\n=== 添加冰箱库存 ===")

    today = datetime.now()

    items = [
        # 蔬菜
        {"name": "西红柿", "category": "蔬菜", "quantity": 6, "unit": "个",
         "expiry_date": (today + timedelta(days=5)).strftime("%Y-%m-%d")},
        {"name": "黄瓜", "category": "蔬菜", "quantity": 3, "unit": "根",
         "expiry_date": (today + timedelta(days=4)).strftime("%Y-%m-%d")},
        {"name": "青菜", "category": "蔬菜", "quantity": 2, "unit": "把",
         "expiry_date": (today + timedelta(days=2)).strftime("%Y-%m-%d")},
        {"name": "土豆", "category": "蔬菜", "quantity": 4, "unit": "个",
         "expiry_date": (today + timedelta(days=14)).strftime("%Y-%m-%d")},

        # 肉类
        {"name": "鸡胸肉", "category": "肉类", "quantity": 2, "unit": "块",
         "expiry_date": (today + timedelta(days=3)).strftime("%Y-%m-%d")},
        {"name": "猪肉", "category": "肉类", "quantity": 1, "unit": "盒",
         "expiry_date": (today + timedelta(days=2)).strftime("%Y-%m-%d")},
        {"name": "鸡蛋", "category": "蛋类", "quantity": 10, "unit": "个",
         "expiry_date": (today + timedelta(days=14)).strftime("%Y-%m-%d")},

        # 主食
        {"name": "米饭", "category": "主食", "quantity": 2, "unit": "盒",
         "expiry_date": (today + timedelta(days=1)).strftime("%Y-%m-%d")},
        {"name": "面条", "category": "主食", "quantity": 1, "unit": "包",
         "expiry_date": (today + timedelta(days=30)).strftime("%Y-%m-%d")},

        # 调味品
        {"name": "酱油", "category": "调味品", "quantity": 1, "unit": "瓶",
         "expiry_date": (today + timedelta(days=180)).strftime("%Y-%m-%d")},
        {"name": "食用油", "category": "调味品", "quantity": 1, "unit": "瓶",
         "expiry_date": (today + timedelta(days=365)).strftime("%Y-%m-%d")},

        # 豆制品
        {"name": "豆腐", "category": "豆制品", "quantity": 1, "unit": "块",
         "expiry_date": (today + timedelta(days=2)).strftime("%Y-%m-%d")},

        # 乳制品
        {"name": "酸奶", "category": "乳制品", "quantity": 4, "unit": "杯",
         "expiry_date": (today + timedelta(days=7)).strftime("%Y-%m-%d")},

        # 水果
        {"name": "苹果", "category": "水果", "quantity": 3, "unit": "个",
         "expiry_date": (today + timedelta(days=7)).strftime("%Y-%m-%d")},
        {"name": "香蕉", "category": "水果", "quantity": 5, "unit": "根",
         "expiry_date": (today + timedelta(days=3)).strftime("%Y-%m-%d")},
    ]

    for item in items:
        db.add_inventory_item(**item)
        days_left = (datetime.strptime(item["expiry_date"], "%Y-%m-%d") - today).days
        status = "⚠️临期" if days_left <= 3 else ""
        print(f"  + {item['name']}: {item['quantity']}{item['unit']}, {days_left}天后过期 {status}")

    print(f"✓ 已添加 {len(items)} 种食材")


def setup_nutrition():
    """导入仓库数据集；数据集不存在时回退到少量合成演示数据。"""
    print("\n=== 检查营养数据 ===")

    try:
        result = db.search_nutrition("鸡蛋", limit=1)
        if result:
            print("✓ 营养数据已存在，跳过导入")
            return
    except Exception:
        pass

    full_dataset = Path(__file__).parents[1] / "data" / "nutrition" / "china_food_composition.json"
    if full_dataset.exists():
        imported = db.import_nutrition_data(str(full_dataset))
        print(f"✓ 已导入仓库营养数据 {imported} 条")
        return

    # 没有随部署提供数据集时仍可运行演示流程。
    print("  导入合成演示营养数据...")

    nutrition_data = [
        ("鸡蛋", "蛋类", 144, 13.3, 8.8, 11.1, 1.1),
        ("鸡胸肉", "肉类", 133, 31.0, 0, 3.6, 0.5),
        ("猪肉(瘦)", "肉类", 143, 20.3, 1.5, 6.2, 1.2),
        ("西红柿", "蔬菜", 18, 0.9, 3.5, 0.2, 0.5),
        ("黄瓜", "蔬菜", 15, 0.7, 2.9, 0.2, 0.3),
        ("土豆", "蔬菜", 77, 2.0, 17.5, 0.1, 0.3),
        ("豆腐", "豆制品", 73, 8.1, 1.7, 3.7, 0.4),
        ("米饭", "主食", 116, 2.6, 25.9, 0.3, 0.2),
        ("面条", "主食", 110, 3.4, 24.3, 0.3, 0.2),
        ("苹果", "水果", 52, 0.3, 13.8, 0.2, 0.2),
        ("香蕉", "水果", 93, 1.1, 22.8, 0.2, 0.3),
        ("酸奶", "乳制品", 72, 3.4, 9.3, 2.7, 0.3),
        ("青菜", "蔬菜", 15, 1.5, 2.2, 0.3, 0.4),
        ("酱油", "调味品", 53, 5.6, 4.9, 0.1, 0.7),
        ("食用油", "调味品", 899, 0, 0, 99.9, 0),
    ]

    conn = db.get_connection()
    cursor = conn.cursor()

    for item in nutrition_data:
        cursor.execute('''
            INSERT OR REPLACE INTO nutrition_data
            (food_name, category, energy_kcal, protein, cho, fat, dietary_fiber)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', item)

    conn.commit()
    conn.close()

    print(f"✓ 已导入 {len(nutrition_data)} 条营养数据")


def main():
    print("=" * 50)
    print("智能冰箱贴 — 演示数据初始化")
    print("=" * 50)

    db.init_db()

    setup_family()
    setup_inventory()
    setup_nutrition()

    print("\n" + "=" * 50)
    print("✓ 演示数据初始化完成")
    print("=" * 50)

    # 验证
    print("\n=== 验证 ===")
    members = db.get_all_members()
    inv = db.get_inventory(page_size=100)
    print(f"家庭成员: {len(members)} 人")
    print(f"库存食材: {inv['total']} 种")

    # 显示临期食材
    expiring = db.get_expiring_items(days=3)
    if expiring:
        print("\n⚠️ 临期食材（3天内）:")
        for item in expiring:
            print(f"  - {item['name']}: {item.get('days_left', '?')}天后过期")


if __name__ == "__main__":
    main()
