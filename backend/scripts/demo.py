"""
演示脚本 — 展示智能冰箱贴的核心功能
"""
import requests
import json
import time
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = 'http://localhost:5000/api'


def print_header(title):
    """打印标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_section(title):
    """打印小节"""
    print(f"\n--- {title} ---")


def print_json(data, indent=2):
    """打印 JSON"""
    print(json.dumps(data, ensure_ascii=False, indent=indent))


def wait(seconds=1):
    """等待"""
    time.sleep(seconds)


def demo_health_check():
    """演示：健康检查"""
    print_header("1. 健康检查")

    print("\n检查系统状态...")
    resp = requests.get(f'{BASE_URL}/health?full=true')
    data = resp.json()

    print(f"\n状态: {data['overall_status']}")
    print(f"耗时: {data['duration_ms']}ms")

    print("\n各模块状态:")
    for name, check in data['checks'].items():
        status = check.get('status', 'unknown')
        emoji = '✅' if status == 'ok' else '⚠️' if status == 'stopped' else '❌'
        print(f"  {emoji} {name}: {status}")

    if 'system' in data:
        print("\n系统信息:")
        print(f"  平台: {data['system'].get('platform')}")
        print(f"  Python: {data['system'].get('python_version')}")


def demo_profile():
    """演示：家庭画像"""
    print_header("2. 家庭画像建档")

    print("\n添加家庭成员...")
    resp = requests.post(f'{BASE_URL}/chat', json={
        "message": "家里3口人，爸爸不吃辣，孩子5岁不吃苦瓜，我在减肥"
    })
    data = resp.json()

    if data.get('handled_locally'):
        print("(本地处理)")
    print(f"\n{data.get('response') or data.get('reply', '')}")

    wait()

    print("\n查看家庭画像...")
    resp = requests.get(f'{BASE_URL}/profile')
    data = resp.json()

    print(f"\n家庭成员 ({len(data['members'])} 人):")
    for m in data['members']:
        print(f"  👤 {m['member_name']}")
        if m.get('dislikes_main'):
            print(f"     不吃: {', '.join(m['dislikes_main'])}")
        if m.get('dislikes_taste'):
            print(f"     不吃: {', '.join(m['dislikes_taste'])}")


def demo_inventory():
    """演示：库存管理"""
    print_header("3. 库存管理")

    print("\n批量添加食材...")
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
    print(f"已添加 {data.get('added', 0)} 种食材")

    wait()

    print("\n查看库存...")
    resp = requests.get(f'{BASE_URL}/inventory')
    data = resp.json()

    print(f"\n库存 ({data['total']} 种):")
    for item in data['items']:
        days = item.get('days_left')
        exp_str = ""
        if days is not None:
            if days < 0:
                exp_str = " ❌已过期"
            elif days <= 3:
                exp_str = f" ⚠️{days}天后过期"
        print(f"  • {item['name']} × {item['quantity']}{item['unit']}{exp_str}")

    wait()

    print("\n查看即将过期...")
    resp = requests.get(f'{BASE_URL}/inventory/expiring?days=7')
    data = resp.json()

    if data['count'] > 0:
        print(f"\n⚠️ 即将过期 ({data['count']} 种):")
        for item in data['items']:
            print(f"  • {item['name']}: {item.get('days_left', '?')}天后过期")
    else:
        print("\n✅ 没有即将过期的食材")


def demo_smart_query():
    """演示：智能查询"""
    print_header("4. 智能查询")

    queries = [
        "蛋白质高的食物",
        "蔬菜类",
        "快过期的",
        "还有多少鸡蛋"
    ]

    for query in queries:
        print(f"\n查询: {query}")
        resp = requests.post(f'{BASE_URL}/smart/query', json={"query": query})
        data = resp.json()

        print(f"  类型: {data['type']}")
        print(f"  本地处理: {data['handled_locally']}")
        print(f"  结果: {len(data.get('results', []))} 项")

        if data.get('results'):
            for item in data['results'][:3]:
                if 'food_name' in item:
                    print(f"    - {item['food_name']}")
                elif 'name' in item:
                    print(f"    - {item['name']}")

        wait(0.5)


def demo_recommend():
    """演示：菜谱推荐"""
    print_header("5. 菜谱推荐")

    print("\n请求晚餐推荐...")
    resp = requests.post(f'{BASE_URL}/recommend', json={
        "meal_type": "晚餐",
        "people_count": 3
    })
    data = resp.json()

    reply = data.get('response') or data.get('reply', '')
    print(f"\n{reply}")

    wait()

    print("\n查看待确认方案...")
    resp = requests.get(f'{BASE_URL}/scheduler/status')
    data = resp.json()
    print(f"调度器状态: {'运行中' if data['running'] else '已停止'}")


def demo_notifications():
    """演示：通知系统"""
    print_header("6. 通知系统")

    print("\n查看通知...")
    resp = requests.get(f'{BASE_URL}/notifications')
    data = resp.json()

    print(f"\n通知 ({data['count']} 条):")
    for n in data['notifications'][:5]:
        print(f"  📬 [{n['type']}] {n['title']}")
        print(f"     {n['message'][:50]}...")

    wait()

    print("\n查看通知统计...")
    resp = requests.get(f'{BASE_URL}/notifications/stats')
    data = resp.json()

    print("\n统计:")
    print(f"  总数: {data['total']}")
    print(f"  未读: {data['unread']}")

    if data.get('by_type'):
        print("  按类型:")
        for t, stats in data['by_type'].items():
            print(f"    {t}: {stats['total']} 条")


def demo_history():
    """演示：库存历史"""
    print_header("7. 库存历史")

    print("\n扣减库存...")
    resp = requests.post(f'{BASE_URL}/inventory/deduct', json={
        "items": [
            {"name": "鸡蛋", "quantity": 2},
            {"name": "西红柿", "quantity": 1}
        ]
    })
    data = resp.json()

    if data.get('success'):
        print("扣减成功:")
        for r in data.get('results', []):
            print(f"  • {r['name']}: {r.get('old')} → {r.get('new')}")

    wait()

    print("\n查看历史...")
    resp = requests.get(f'{BASE_URL}/inventory/history?days=1')
    data = resp.json()

    print(f"\n历史记录 ({data['count']} 条):")
    for h in data['history'][:5]:
        print(f"  [{h['action']}] {h['item_name']}: {h['quantity_change']}")

    wait()

    print("\n查看统计...")
    resp = requests.get(f'{BASE_URL}/inventory/history/statistics?days=1')
    data = resp.json()

    print(f"\n统计 ({data['days']} 天):")
    print(f"  总记录: {data['total_records']}")
    if data.get('top_deducted'):
        print("  最常使用:")
        for item in data['top_deducted'][:3]:
            print(f"    • {item['name']}: {item['count']} 次")


def demo_scheduler():
    """演示：定时任务"""
    print_header("8. 定时任务")

    print("\n查看调度器状态...")
    resp = requests.get(f'{BASE_URL}/scheduler/status')
    data = resp.json()

    print(f"\n状态: {'运行中' if data['running'] else '已停止'}")
    print(f"任务数: {len(data['tasks'])}")

    print("\n任务列表:")
    for task in data['tasks']:
        print(f"  • {task['name']} ({task['id']})")
        print(f"    下次执行: {task.get('next_run', 'N/A')}")

    wait()

    print("\n手动触发过期检查...")
    resp = requests.post(f'{BASE_URL}/scheduler/trigger/expiring')
    data = resp.json()

    print(f"即将过期: {data.get('expiring_count', 0)} 种")


def demo_device_display():
    """演示：设备显示（Wi-Fi 上屏）"""
    import os

    print_header("9. 设备显示（Wi-Fi 上屏）")

    device_id = "demo-device-001"

    # 创建测试 .film 文件（智能冰箱贴六色电子纸规格）
    header = (209088).to_bytes(4, 'little') + b'\x00' * 28
    pixels = os.urandom(209088)
    film_data = header + pixels

    print(f"\n设备 ID: {device_id}")
    print(f"文件大小: {len(film_data)} bytes (SFM six-color display)")

    # 上传
    print("\n上传 .film 文件...")
    resp = requests.post(
        f'{BASE_URL}/devices/{device_id}/display',
        data=film_data,
        headers={'Content-Type': 'application/octet-stream'}
    )
    data = resp.json()

    if data.get('success'):
        print(f"  版本: {data['version']}")
        print(f"  SHA-256: {data['sha256'][:16]}...")

        wait()

        # Manifest
        print("\n获取 manifest（ESP32 调用）...")
        resp = requests.get(f'{BASE_URL}/devices/{device_id}/display/manifest')
        manifest = resp.json()
        print(f"  版本: {manifest['version']}")
        print(f"  大小: {manifest['size']} bytes")
        print(f"  URL: {manifest['film_url']}")

        wait()

        # 同步状态
        print("\n查看同步状态...")
        resp = requests.get(f'{BASE_URL}/devices/{device_id}/sync-status')
        status = resp.json()
        print(f"  最新版本: {status['latest_version']}")
        print(f"  事件记录: {len(status['recent_events'])} 条")

        # 设备列表
        print("\n设备列表...")
        resp = requests.get(f'{BASE_URL}/devices')
        devices = resp.json()
        print(f"  已注册设备: {devices['count']} 台")
    else:
        print(f"  上传失败: {data.get('error')}")


def run_demo():
    """运行演示"""
    print("🧊 智能冰箱贴 — 功能演示")
    print("=" * 60)

    try:
        demo_health_check()
        wait(2)

        demo_profile()
        wait(2)

        demo_inventory()
        wait(2)

        demo_smart_query()
        wait(2)

        demo_recommend()
        wait(2)

        demo_notifications()
        wait(2)

        demo_history()
        wait(2)

        demo_scheduler()
        wait(2)

        demo_device_display()

        print_header("演示完成")
        print("\n🎉 所有功能演示完毕！")
        print("\n项目地址: https://github.com/5765scdnfz-oss/smart-fridge-magnet")
        print("API文档: docs/API.md")

    except requests.exceptions.ConnectionError:
        print("\n❌ 连接失败，请确保服务已启动")
        print("   运行: python -m src.app")
    except Exception as e:
        print(f"\n❌ 演示出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    run_demo()
