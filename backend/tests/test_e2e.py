"""
端到端测试 — 模拟真实用户场景
"""
import requests
import time

BASE_URL = 'http://localhost:5000/api'


def test_full_workflow():
    """完整工作流测试"""
    print("=== 完整工作流测试 ===\n")

    # 1. 健康检查
    print("1. 健康检查...")
    resp = requests.get(f'{BASE_URL}/health')
    assert resp.status_code == 200
    assert resp.json()['overall_status'] == 'ok'
    print("   ✅ 系统正常")

    # 2. 清空数据（确保干净环境）
    print("\n2. 清空测试数据...")
    requests.post(f'{BASE_URL}/notifications/clear')
    print("   ✅ 通知已清空")

    # 3. 添加家庭成员
    print("\n3. 添加家庭成员...")
    resp = requests.post(f'{BASE_URL}/chat', json={
        "message": "家里3口人，爸爸不吃辣，孩子5岁不吃苦瓜，我在减肥"
    })
    data = resp.json()
    assert data.get('handled_locally') is False or '已记录' in data.get('response', '') or '新增' in data.get('reply', '')
    print("   ✅ 家庭成员已添加")

    # 4. 查看家庭画像
    print("\n4. 查看家庭画像...")
    resp = requests.get(f'{BASE_URL}/profile')
    data = resp.json()
    members = data.get('members', [])
    assert len(members) >= 3
    print(f"   ✅ 家庭成员: {len(members)} 人")
    for m in members:
        print(f"      - {m['member_name']}")

    # 5. 批量添加库存
    print("\n5. 批量添加库存...")
    resp = requests.post(f'{BASE_URL}/inventory/batch', json={
        "items": [
            {"name": "鸡蛋", "category": "蛋类", "quantity": 10, "unit": "个", "expiry_date": "2026-08-20"},
            {"name": "西红柿", "category": "蔬菜", "quantity": 5, "unit": "个", "expiry_date": "2026-07-25"},
            {"name": "牛奶", "category": "乳制品", "quantity": 2, "unit": "盒", "expiry_date": "2026-07-24"},
            {"name": "鸡胸肉", "category": "肉类", "quantity": 1, "unit": "块", "expiry_date": "2026-07-23"},
            {"name": "西兰花", "category": "蔬菜", "quantity": 2, "unit": "颗", "expiry_date": "2026-07-24"},
            {"name": "米饭", "category": "主食", "quantity": 3, "unit": "碗", "expiry_date": "2026-07-23"}
        ]
    })
    data = resp.json()
    assert data.get('success') is True
    print(f"   ✅ 已添加 {data.get('added', 0)} 种食材")

    # 6. 查看库存
    print("\n6. 查看库存...")
    resp = requests.get(f'{BASE_URL}/inventory')
    data = resp.json()
    assert data.get('total', 0) >= 6
    print(f"   ✅ 库存: {data['total']} 种食材")

    # 7. 查看即将过期
    print("\n7. 查看即将过期...")
    resp = requests.get(f'{BASE_URL}/inventory/expiring?days=7')
    data = resp.json()
    print(f"   ✅ 即将过期: {data.get('count', 0)} 种")

    # 8. 智能查询测试
    print("\n8. 智能查询测试...")
    queries = [
        ("蛋白质高的", "nutrition"),
        ("蔬菜类", "category"),
        ("快过期的", "inventory"),
    ]
    for query, expected_type in queries:
        resp = requests.post(f'{BASE_URL}/smart/query', json={"query": query})
        data = resp.json()
        print(f"   [{data.get('type')}] {query} -> {len(data.get('results', []))} 结果")

    # 9. 菜谱推荐
    print("\n9. 菜谱推荐...")
    resp = requests.post(f'{BASE_URL}/recommend', json={
        "meal_type": "晚餐",
        "people_count": 3
    })
    data = resp.json()
    assert 'reply' in data or 'response' in data
    print("   ✅ 推荐已生成")

    # 10. 查看通知
    print("\n10. 查看通知...")
    resp = requests.get(f'{BASE_URL}/notifications')
    data = resp.json()
    print(f"    ✅ 通知: {data.get('count', 0)} 条")

    # 11. 健康检查（完整）
    print("\n11. 完整健康检查...")
    requests.get(f'{BASE_URL}/health?full=true')
    data = resp.json()
    print(f"    ✅ 状态: {data.get('overall_status')}")
    print(f"    ✅ 耗时: {data.get('duration_ms')}ms")

    print("\n" + "=" * 50)
    print("✅ 完整工作流测试通过！")


def test_error_handling():
    """错误处理测试"""
    print("\n=== 错误处理测试 ===\n")

    # 1. 缺少参数
    print("1. 缺少参数测试...")
    resp = requests.post(f'{BASE_URL}/chat', json={})
    assert resp.status_code == 400
    print("   ✅ 正确返回 400")

    # 2. 无效分类
    print("\n2. 无效分类测试...")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "测试",
        "category": "无效分类",
        "quantity": 1
    })
    assert resp.status_code == 400
    print("   ✅ 正确拒绝无效分类")

    # 3. 数量为0
    print("\n3. 数量为0测试...")
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "测试",
        "category": "蛋类",
        "quantity": 0
    })
    assert resp.status_code == 400
    print("   ✅ 正确拒绝零数量")

    # 4. 不存在的库存项
    print("\n4. 不存在的库存项测试...")
    resp = requests.put(f'{BASE_URL}/inventory/99999', json={"quantity": 1})
    assert resp.status_code == 404
    print("   ✅ 正确返回 404")

    # 5. 版本冲突
    print("\n5. 版本冲突测试...")
    # 先添加一个
    resp = requests.post(f'{BASE_URL}/inventory', json={
        "name": "版本测试",
        "category": "其他",
        "quantity": 1
    })
    if resp.status_code == 201:
        item_id = resp.json().get('item_id')
        # 用错误版本更新
        resp = requests.put(f'{BASE_URL}/inventory/{item_id}', json={
            "quantity": 2,
            "version": 999
        })
        assert resp.status_code == 409
        print("   ✅ 正确检测版本冲突")

    print("\n" + "=" * 50)
    print("✅ 错误处理测试通过！")


def test_performance():
    """性能测试"""
    print("\n=== 性能测试 ===\n")

    # 1. 库存查询性能
    print("1. 库存查询性能...")
    start = time.time()
    for _ in range(10):
        requests.get(f'{BASE_URL}/inventory')
    elapsed = time.time() - start
    print(f"   10次查询: {elapsed:.2f}s (平均 {elapsed/10*1000:.0f}ms/次)")

    # 2. 智能查询性能
    print("\n2. 智能查询性能...")
    start = time.time()
    for _ in range(10):
        requests.post(f'{BASE_URL}/smart/query', json={"query": "蛋白质高的"})
    elapsed = time.time() - start
    print(f"   10次查询: {elapsed:.2f}s (平均 {elapsed/10*1000:.0f}ms/次)")

    # 3. 通知查询性能
    print("\n3. 通知查询性能...")
    start = time.time()
    for _ in range(10):
        requests.get(f'{BASE_URL}/notifications')
    elapsed = time.time() - start
    print(f"   10次查询: {elapsed:.2f}s (平均 {elapsed/10*1000:.0f}ms/次)")

    # 4. 健康检查性能
    print("\n4. 健康检查性能...")
    start = time.time()
    requests.get(f'{BASE_URL}/health?full=true')
    elapsed = time.time() - start
    print(f"   完整检查: {elapsed:.2f}s")

    print("\n" + "=" * 50)
    print("✅ 性能测试完成！")


def test_api_coverage():
    """API 覆盖测试"""
    print("\n=== API 覆盖测试 ===\n")

    apis = [
        ("GET", "/health"),
        ("GET", "/health/database"),
        ("GET", "/health/scheduler"),
        ("GET", "/health/inventory"),
        ("GET", "/health/profile"),
        ("GET", "/health/nutrition"),
        ("GET", "/inventory"),
        ("GET", "/inventory?format=summary"),
        ("GET", "/inventory/categories"),
        ("GET", "/inventory/expiring"),
        ("GET", "/inventory/history"),
        ("GET", "/inventory/history/statistics"),
        ("GET", "/inventory/history/daily"),
        ("GET", "/notifications"),
        ("GET", "/notifications/stats"),
        ("GET", "/scheduler/status"),
        ("GET", "/scheduler/tasks"),
        ("GET", "/scheduler/history"),
        ("GET", "/profile"),
    ]

    success = 0
    fail = 0

    for method, path in apis:
        try:
            if method == "GET":
                resp = requests.get(f'{BASE_URL}{path}')
            else:
                resp = requests.post(f'{BASE_URL}{path}')

            if resp.status_code < 500:
                print(f"   ✅ {method} {path} -> {resp.status_code}")
                success += 1
            else:
                print(f"   ❌ {method} {path} -> {resp.status_code}")
                fail += 1
        except Exception as e:
            print(f"   ❌ {method} {path} -> {str(e)[:50]}")
            fail += 1

    print(f"\n   总计: {success + fail} 个 API")
    print(f"   成功: {success}")
    print(f"   失败: {fail}")

    print("\n" + "=" * 50)
    print("✅ API 覆盖测试完成！")


def run_all_tests():
    """运行所有测试"""
    print("🧪 智能冰箱贴 — 端到端测试")
    print("=" * 50)

    try:
        test_full_workflow()
        test_error_handling()
        test_performance()
        test_api_coverage()

        print("\n" + "=" * 50)
        print("🎉 所有测试通过！")

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
    except requests.exceptions.ConnectionError:
        print("\n❌ 连接失败，请确保服务已启动")
        print("   运行: python -m src.app")
    except Exception as e:
        print(f"\n❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    run_all_tests()
