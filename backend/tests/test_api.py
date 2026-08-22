"""
API 测试脚本 — 库存管理产品化版本
"""
import requests
import json

BASE_URL = 'http://localhost:5000/api'


def test_health():
    """测试健康检查"""
    print("=== 测试健康检查 ===")
    resp = requests.get(f'{BASE_URL}/health')
    print(f"状态码: {resp.status_code}")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 200


def test_chat():
    """测试对话"""
    print("=== 测试对话 ===")
    resp = requests.post(f'{BASE_URL}/chat', json={
        "message": "我家3口人，爸爸不吃辣，孩子不吃苦瓜",
        "session_id": "test"
    })
    print(f"状态码: {resp.status_code}")
    print(f"响应: {resp.json()}")
    print()


def test_profile():
    """测试画像查询"""
    print("=== 测试画像查询 ===")
    resp = requests.get(f'{BASE_URL}/profile')
    print(f"状态码: {resp.status_code}")
    print(f"响应: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
    print()


def test_recommend():
    """测试菜谱推荐"""
    print("=== 测试菜谱推荐 ===")
    resp = requests.post(f'{BASE_URL}/recommend', json={
        "meal_type": "晚餐",
        "people_count": 3
    })
    print(f"状态码: {resp.status_code}")
    print(f"响应: {resp.json()}")
    print()


def test_notifications():
    """测试通知"""
    print("=== 测试通知 ===")
    resp = requests.get(f'{BASE_URL}/notifications')
    print(f"状态码: {resp.status_code}")
    print(f"响应: {resp.json()}")
    print()


# ==================== 库存管理测试 ====================

def test_inventory_empty():
    """测试空库存查询"""
    print("=== 测试空库存查询 ===")
    resp = requests.get(f'{BASE_URL}/inventory')
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"总数: {data.get('total', 0)}")
    print(f"分页: page={data.get('page')}, total_pages={data.get('total_pages')}")
    print()
    return data.get('total', 0)


def test_preset_categories():
    """测试获取预设分类"""
    print("=== 测试获取预设分类 ===")
    resp = requests.get(f'{BASE_URL}/inventory/categories?type=preset')
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"分类数量: {data.get('count', 0)}")
    categories = data.get('categories', [])
    for cat in categories[:5]:
        if isinstance(cat, dict):
            print(f"  {cat.get('icon', '')} {cat.get('name', '')}")
        else:
            print(f"  {cat}")
    if len(categories) > 5:
        print(f"  ... 还有 {len(categories) - 5} 个")
    print()
    return categories


def test_inventory_add_valid():
    """测试添加库存项（合法数据）"""
    print("=== 测试添加库存项（合法数据） ===")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "土鸡蛋",
        "category": "蛋类",
        "quantity": 10,
        "unit": "个",
        "production_date": "2026-07-20",
        "expiry_date": "2026-08-20"
    })
    print(f"状态码: {resp.status_code}")
    print(f"响应: {resp.json()}")
    print()
    return resp.json().get('item_id')


def test_inventory_add_invalid_category():
    """测试添加库存项（非法分类）"""
    print("=== 测试添加库存项（非法分类） ===")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "测试食材",
        "category": "不存在的分类",
        "quantity": 1
    })
    print(f"状态码: {resp.status_code} (期望 400)")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 400


def test_inventory_add_invalid_date():
    """测试添加库存项（非法日期）"""
    print("=== 测试添加库存项（非法日期） ===")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "测试食材",
        "category": "蛋类",
        "quantity": 1,
        "expiry_date": "2026/8/20/invalid"
    })
    print(f"状态码: {resp.status_code} (期望 400)")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 400


def test_inventory_add_date_logic_error():
    """测试添加库存项（日期逻辑错误：生产日期晚于保质期）"""
    print("=== 测试添加库存项（日期逻辑错误） ===")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "测试食材",
        "category": "蛋类",
        "quantity": 1,
        "production_date": "2026-09-01",
        "expiry_date": "2026-08-01"
    })
    print(f"状态码: {resp.status_code} (期望 400)")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 400


def test_inventory_add_zero_quantity():
    """测试添加库存项（数量为0）"""
    print("=== 测试添加库存项（数量为0） ===")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "测试食材",
        "category": "蛋类",
        "quantity": 0
    })
    print(f"状态码: {resp.status_code} (期望 400)")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 400


def test_inventory_batch_add():
    """测试批量添加库存项"""
    print("=== 测试批量添加库存项 ===")
    resp = requests.post(f'{BASE_URL}/inventory/batch', json={
        "items": [
            {"name": "鲜牛奶", "category": "乳制品", "quantity": 2, "unit": "盒",
             "expiry_date": "2026-07-30"},
            {"name": "全麦面包", "category": "主食", "quantity": 1, "unit": "袋",
             "expiry_date": "2026-07-25"},
            {"name": "西兰花", "category": "蔬菜", "quantity": 3, "unit": "颗",
             "expiry_date": "2026-07-24"},
            {"name": "三文鱼", "category": "海鲜", "quantity": 1, "unit": "块",
             "expiry_date": "2026-07-23"}
        ]
    })
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"成功: {data.get('added', 0)} 项")
    if data.get('failed'):
        print(f"失败: {data.get('failed', 0)} 项")
    print()
    return data.get('success', False)


def test_inventory_batch_partial_fail():
    """测试批量添加（部分失败）"""
    print("=== 测试批量添加（部分失败） ===")
    resp = requests.post(f'{BASE_URL}/inventory/batch', json={
        "items": [
            {"name": "苹果", "category": "水果", "quantity": 5},
            {"name": "测试食材", "category": "不存在的分类", "quantity": 1}
        ]
    })
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
    print()
    return resp.status_code == 400


def test_inventory_with_pagination():
    """测试库存分页查询"""
    print("=== 测试库存分页查询 ===")
    resp = requests.get(f'{BASE_URL}/inventory?page=1&page_size=3')
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"总数: {data.get('total', 0)}")
    print(f"当前页: {data.get('page')}/{data.get('total_pages')}")
    print(f"本页: {len(data.get('items', []))} 项")
    for item in data.get('items', []):
        print(f"  - {item['name']} × {item['quantity']}{item['unit']}")
    print()
    return data.get('total', 0) > 0


def test_inventory_filter_by_category():
    """测试按分类筛选"""
    print("=== 测试按分类筛选（乳制品） ===")
    resp = requests.get(f'{BASE_URL}/inventory?category=乳制品')
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"乳制品数量: {data.get('total', 0)}")
    for item in data.get('items', []):
        print(f"  - {item['name']} × {item['quantity']}{item['unit']}")
    print()


def test_used_categories():
    """测试获取实际使用的分类"""
    print("=== 测试获取实际使用的分类 ===")
    resp = requests.get(f'{BASE_URL}/inventory/categories?type=used')
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"使用中的分类: {data.get('count', 0)}")
    for cat in data.get('categories', []):
        if isinstance(cat, dict):
            print(f"  - {cat.get('name', '')}: {cat.get('count', 0)} 项")
        else:
            print(f"  - {cat}")
    print()


def test_inventory_expiring():
    """测试即将过期查询"""
    print("=== 测试即将过期查询（7天内） ===")
    resp = requests.get(f'{BASE_URL}/inventory/expiring?days=7')
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"即将过期: {data.get('count', 0)} 项")
    for item in data.get('items', []):
        days = item.get('days_left', '?')
        print(f"  - {item['name']}: {days}天后过期")
    print()


def test_inventory_update_with_version(item_id):
    """测试带乐观锁的更新"""
    print("=== 测试带乐观锁的更新 ===")

    # 先查询当前版本
    resp = requests.get(f'{BASE_URL}/inventory')
    items = resp.json().get('items', [])
    target = next((i for i in items if i['id'] == item_id), None)

    if target:
        version = target.get('version', 1)
        print(f"当前版本: {version}")

        # 正常更新
        resp = requests.put(f'{BASE_URL}/inventory/{item_id}', json={
            "quantity": 6,
            "version": version
        })
        print(f"状态码: {resp.status_code}")
        print(f"响应: {resp.json()}")
    else:
        print(f"未找到 item_id={item_id}")
    print()


def test_inventory_update_version_conflict(item_id):
    """测试乐观锁版本冲突"""
    print("=== 测试乐观锁版本冲突 ===")
    resp = requests.put(f'{BASE_URL}/inventory/{item_id}', json={
        "quantity": 999,
        "version": 999  # 故意用错误的版本号
    })
    print(f"状态码: {resp.status_code} (期望 409)")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 409


def test_inventory_deduct_fifo():
    """测试FIFO扣减"""
    print("=== 测试FIFO扣减 ===")

    # 先添加两个同名食材，不同生产日期
    requests.post(f'{BASE_URL}/inventory', json={
        "name": "FIFO测试鸡蛋",
        "category": "蛋类",
        "quantity": 5,
        "production_date": "2026-07-10",
        "expiry_date": "2026-08-10"
    })
    requests.post(f'{BASE_URL}/inventory', json={
        "name": "FIFO测试鸡蛋",
        "category": "蛋类",
        "quantity": 5,
        "production_date": "2026-07-20",
        "expiry_date": "2026-08-20"
    })

    # 扣减 7 个（应该先扣日期早的）
    resp = requests.post(f'{BASE_URL}/inventory/deduct', json={
        "items": [{"name": "FIFO测试鸡蛋", "quantity": 7}]
    })
    data = resp.json()
    print(f"状态码: {resp.status_code}")
    print(f"响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
    print()
    return data.get('success', False)


def test_inventory_deduct_insufficient():
    """测试库存不足扣减"""
    print("=== 测试库存不足扣减 ===")
    resp = requests.post(f'{BASE_URL}/inventory/deduct', json={
        "items": [{"name": "不存在的食材", "quantity": 1}]
    })
    print(f"状态码: {resp.status_code} (期望 400)")
    print(f"响应: {resp.json()}")
    print()
    return resp.status_code == 400


def test_inventory_delete(item_id):
    """测试删除库存项"""
    print("=== 测试删除库存项 ===")
    resp = requests.delete(f'{BASE_URL}/inventory/{item_id}')
    print(f"状态码: {resp.status_code}")
    print(f"响应: {resp.json()}")
    print()


def test_inventory_summary():
    """测试库存摘要"""
    print("=== 测试库存摘要 ===")
    resp = requests.get(f'{BASE_URL}/inventory?format=summary')
    print(f"状态码: {resp.status_code}")
    summary = resp.json().get('summary', '')
    print(f"摘要:\n{summary}")
    print()


# ==================== 智能查询测试 ====================

def test_smart_query_nutrition():
    """测试智能查询 — 营养意图"""
    print("=== 测试智能查询 — 营养意图 ===")

    test_cases = [
        "蛋白质高的食物",
        "补铁的",
        "低热量",
        "高钙食物"
    ]

    for query in test_cases:
        resp = requests.post(f'{BASE_URL}/smart/query', json={"query": query})
        data = resp.json()
        print(f"查询: {query}")
        print(f"  类型: {data.get('type')}")
        print(f"  意图: {data.get('intent', {}).get('label', '')}")
        print(f"  本地处理: {data.get('handled_locally')}")
        print(f"  结果数: {len(data.get('results', []))}")
        if data.get('results'):
            print(f"  第一个: {data['results'][0].get('food_name', '')}")
        print()


def test_smart_query_category():
    """测试智能查询 — 分类意图"""
    print("=== 测试智能查询 — 分类意图 ===")

    test_cases = ["蔬菜类", "肉类", "乳制品"]

    for query in test_cases:
        resp = requests.post(f'{BASE_URL}/smart/query', json={"query": query})
        data = resp.json()
        print(f"查询: {query}")
        print(f"  类型: {data.get('type')}")
        print(f"  分类: {data.get('intent', {}).get('category', '')}")
        print(f"  本地处理: {data.get('handled_locally')}")
        print(f"  结果数: {len(data.get('results', []))}")
        print()


def test_smart_query_inventory():
    """测试智能查询 — 库存意图"""
    print("=== 测试智能查询 — 库存意图 ===")

    resp = requests.post(f'{BASE_URL}/smart/query', json={"query": "快过期的"})
    data = resp.json()
    print("查询: 快过期的")
    print(f"  类型: {data.get('type')}")
    print(f"  本地处理: {data.get('handled_locally')}")
    print(f"  结果数: {len(data.get('results', []))}")
    print()


def test_smart_query_action():
    """测试智能查询 — 动作意图"""
    print("=== 测试智能查询 — 动作意图 ===")

    resp = requests.post(f'{BASE_URL}/smart/query', json={"query": "还有多少鸡蛋"})
    data = resp.json()
    print("查询: 还有多少鸡蛋")
    print(f"  类型: {data.get('type')}")
    print(f"  本地处理: {data.get('handled_locally')}")
    print(f"  结果数: {len(data.get('results', []))}")
    print()


def test_smart_nutrition_api():
    """测试营养排序 API"""
    print("=== 测试营养排序 API ===")

    test_cases = [
        ("protein", "DESC", "高蛋白"),
        ("fat", "ASC", "低脂肪"),
        ("energy_kcal", "ASC", "低热量"),
        ("fe", "DESC", "高铁"),
    ]

    for field, order, label in test_cases:
        resp = requests.get(f'{BASE_URL}/smart/nutrition?field={field}&order={order}&limit=3')
        data = resp.json()
        print(f"查询: {label} (field={field}, order={order})")
        print(f"  结果数: {data.get('count', 0)}")
        if data.get('results'):
            top = data['results'][0]
            print(f"  第一: {top.get('food_name', '')} ({field}={top.get(field, '?')})")
        print()


def test_smart_parse():
    """测试意图解析"""
    print("=== 测试意图解析 ===")

    test_cases = [
        "蛋白质高的食物",
        "蔬菜类",
        "快过期的",
        "推荐晚餐",
        "孩子吃什么好",
        "今天天气怎么样"
    ]

    for query in test_cases:
        resp = requests.post(f'{BASE_URL}/smart/parse', json={"query": query})
        data = resp.json()
        print(f"查询: {query}")
        print(f"  类型: {data.get('type')}")
        print(f"  置信度: {data.get('confidence')}")
        print(f"  简单查询: {data.get('is_simple')}")
        print()


def test_chat_with_local_understanding():
    """测试对话接口的本地理解"""
    print("=== 测试对话接口 — 本地理解 ===")

    test_cases = [
        "蛋白质高的食物",
        "蔬菜类",
        "快过期的"
    ]

    for query in test_cases:
        resp = requests.post(f'{BASE_URL}/chat', json={"message": query})
        data = resp.json()
        print(f"查询: {query}")
        print(f"  本地处理: {data.get('handled_locally')}")
        print(f"  响应: {data.get('response', '')[:80]}...")
        print()


def run_all_tests():
    """运行所有测试"""
    print("🧪 开始测试...\n")

    try:
        # 基础测试
        if not test_health():
            print("❌ 健康检查失败，停止测试")
            return

        test_chat()
        test_profile()
        test_recommend()
        test_notifications()

        # 分类测试
        print("\n📦 分类系统测试...\n")
        test_preset_categories()

        print("\n✅ 数据校验测试...\n")
        test_inventory_add_invalid_category()
        test_inventory_add_invalid_date()
        test_inventory_add_date_logic_error()
        test_inventory_add_zero_quantity()

        # 正常添加测试
        print("\n➕ 添加操作测试...\n")
        item_id = test_inventory_add_valid()
        test_inventory_batch_add()
        test_inventory_batch_partial_fail()

        print("\n🔍 查询操作测试...\n")
        test_inventory_empty()
        test_inventory_with_pagination()
        test_inventory_filter_by_category()
        test_used_categories()
        test_inventory_expiring()
        test_inventory_summary()

        print("\n✏️ 更新操作测试（乐观锁）...\n")
        test_inventory_update_with_version(item_id)
        test_inventory_update_version_conflict(item_id)

        # 扣减测试（FIFO）
        print("\n📉 扣减操作测试（FIFO）...\n")
        test_inventory_deduct_fifo()
        test_inventory_deduct_insufficient()

        print("\n🗑️ 删除操作测试...\n")
        test_inventory_delete(item_id)

        # 智能查询测试
        print("\n🧠 智能查询测试...\n")
        test_smart_query_nutrition()
        test_smart_query_category()
        test_smart_query_inventory()
        test_smart_query_action()
        test_smart_nutrition_api()
        test_smart_parse()
        test_chat_with_local_understanding()

        print("✅ 所有测试完成")

    except requests.exceptions.ConnectionError:
        print("❌ 连接失败，请确保服务已启动")
        print("   运行: python -m src.app")
    except Exception as e:
        print(f"❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    run_all_tests()
