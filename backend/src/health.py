"""
健康检查模块 — 系统状态监控
支持：数据库检查、API检查、定时任务检查、系统信息
"""
import os
import time
from datetime import datetime
from typing import Dict

from . import database as db
from .ai_client import health_check as ai_health_check
from .scheduler import is_scheduler_running, get_scheduler_tasks, get_task_history


def check_database() -> Dict:
    """
    检查数据库状态

    Returns:
        dict: 数据库状态
    """
    try:
        conn = db.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT 1')
        cursor.fetchone()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row['name'] for row in cursor.fetchall()]

        table_counts = {}
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            table_counts[table] = cursor.fetchone()[0]

        db_path = db.database_path()
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

        conn.close()

        return {
            'status': 'ok',
            'tables': tables,
            'table_counts': table_counts,
            'db_size_mb': round(db_size / 1024 / 1024, 2),
            'db_path': db_path
        }

    except Exception as e:
        return {
            'status': 'error',
            'error': str(e)
        }


def check_api() -> Dict:
    """
    检查 API 状态

    Returns:
        dict: API 状态
    """
    try:
        result = ai_health_check()
        return {
            'status': 'ok',
            'ai': result
        }
    except Exception as e:
        return {
            'status': 'error',
            'error': str(e)
        }


def check_scheduler() -> Dict:
    """
    检查定时任务状态

    Returns:
        dict: 定时任务状态
    """
    try:
        running = is_scheduler_running()
        tasks = get_scheduler_tasks()
        history = get_task_history()

        # 最近一次执行
        last_execution = history[-1] if history else None

        return {
            'status': 'ok' if running else 'stopped',
            'running': running,
            'tasks': tasks,
            'task_count': len(tasks),
            'history_count': len(history),
            'last_execution': last_execution
        }

    except Exception as e:
        return {
            'status': 'error',
            'error': str(e)
        }


def check_inventory() -> Dict:
    """
    检查库存状态

    Returns:
        dict: 库存状态
    """
    try:
        inventory = db.get_inventory(page_size=1000)
        items = inventory.get('items', [])

        # 按分类统计
        categories = {}
        for item in items:
            cat = item.get('category', '其他')
            categories[cat] = categories.get(cat, 0) + 1

        # 即将过期
        expiring = db.get_expiring_items(days=3)

        # 已过期
        expired = [i for i in items if i.get('days_left') is not None and i['days_left'] < 0]

        return {
            'status': 'ok',
            'total_items': len(items),
            'categories': categories,
            'expiring_count': len(expiring),
            'expired_count': len(expired),
            'expiring_items': [{'name': i['name'], 'days_left': i.get('days_left')} for i in expiring[:5]],
            'expired_items': [{'name': i['name'], 'days_left': i.get('days_left')} for i in expired[:5]]
        }

    except Exception as e:
        return {
            'status': 'error',
            'error': str(e)
        }


def check_profile() -> Dict:
    """检查家庭画像状态。

    **不返回成员姓名**：这个端点的用途是回答「画像配好了没有」，人数与忌口条数就够了。
    真实姓名属于隐私，而诊断接口的暴露面天生比业务接口宽（联调时常被临时放开）。
    """
    try:
        members = db.get_all_members()
        schedule = db.get_meal_schedule()

        return {
            'status': 'ok',
            'member_count': len(members),
            'members_with_dislikes': sum(
                1 for m in members if m.get('dislikes_main')
            ),
            'meal_schedule': schedule
        }

    except Exception as e:
        return {
            'status': 'error',
            'error': str(e)
        }


def check_nutrition() -> Dict:
    """
    检查营养数据状态

    Returns:
        dict: 营养数据状态
    """
    try:
        count = db.get_nutrition_count()

        # 测试搜索
        test_results = db.search_nutrition("鸡蛋", limit=1)
        search_ok = len(test_results) > 0

        return {
            'status': 'ok',
            'total_records': count,
            'search_working': search_ok
        }

    except Exception as e:
        return {
            'status': 'error',
            'error': str(e)
        }


def get_system_info() -> Dict:
    """
    获取系统信息

    Returns:
        dict: 系统信息
    """
    import platform

    info = {
        'platform': platform.platform(),
        'python_version': platform.python_version(),
        'timestamp': datetime.now().isoformat()
    }

    # 尝试获取系统资源信息
    try:
        import psutil
        info['cpu_percent'] = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        info['memory_total_mb'] = round(memory.total / 1024 / 1024)
        info['memory_used_mb'] = round(memory.used / 1024 / 1024)
        info['memory_percent'] = memory.percent
    except ImportError:
        info['psutil_available'] = False

    return info


# ==================== 综合检查 ====================

def full_health_check() -> Dict:
    """
    完整健康检查

    Returns:
        dict: 完整检查结果
    """
    start_time = time.time()

    result = {
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }

    # 数据库检查
    result['checks']['database'] = check_database()

    # API 检查（可能较慢）
    result['checks']['api'] = check_api()

    # 定时任务检查
    result['checks']['scheduler'] = check_scheduler()

    # 库存检查
    result['checks']['inventory'] = check_inventory()

    # 画像检查
    result['checks']['profile'] = check_profile()

    # 营养数据检查
    result['checks']['nutrition'] = check_nutrition()

    # 系统信息
    result['system'] = get_system_info()

    # 总体状态
    all_ok = all(
        check.get('status') in ('ok', 'stopped')
        for check in result['checks'].values()
    )
    result['overall_status'] = 'ok' if all_ok else 'degraded'

    result['duration_ms'] = round((time.time() - start_time) * 1000)

    return result


def quick_health_check() -> Dict:
    """
    快速健康检查（不调用外部 API）

    Returns:
        dict: 快速检查结果
    """
    start_time = time.time()

    result = {
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }

    # 只检查本地组件
    result['checks']['database'] = check_database()
    result['checks']['scheduler'] = check_scheduler()
    result['checks']['inventory'] = check_inventory()

    # 总体状态
    all_ok = all(
        check.get('status') in ('ok', 'stopped')
        for check in result['checks'].values()
    )
    result['overall_status'] = 'ok' if all_ok else 'degraded'

    result['duration_ms'] = round((time.time() - start_time) * 1000)

    return result


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("Health Check Module Test")
    print("=" * 50)

    # 快速检查
    print("\nQuick health check:")
    quick = quick_health_check()
    print(f"  Overall: {quick['overall_status']}")
    print(f"  Duration: {quick['duration_ms']}ms")

    for name, check in quick['checks'].items():
        status = check.get('status', 'unknown')
        emoji = '✅' if status == 'ok' else '⚠️' if status == 'stopped' else '❌'
        print(f"  {emoji} {name}: {status}")

    # 数据库详情
    print("\nDatabase details:")
    db_check = check_database()
    if db_check['status'] == 'ok':
        print(f"  Tables: {len(db_check['tables'])}")
        print(f"  Size: {db_check['db_size_mb']}MB")
        for table, count in db_check['table_counts'].items():
            print(f"    {table}: {count} records")

    # 库存详情
    print("\nInventory details:")
    inv_check = check_inventory()
    if inv_check['status'] == 'ok':
        print(f"  Total: {inv_check['total_items']} items")
        print(f"  Expiring: {inv_check['expiring_count']}")
        print(f"  Expired: {inv_check['expired_count']}")

    print("\nDone!")
