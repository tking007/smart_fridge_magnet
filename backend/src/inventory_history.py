"""
库存历史模块 — 记录库存变动历史
支持：扣减记录、添加记录、查询历史、统计分析
"""
import json
from datetime import datetime, timedelta
from typing import List, Dict

from . import database as db


def init_history_table():
    """初始化历史记录表"""
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            item_name TEXT NOT NULL,
            action TEXT NOT NULL,
            quantity_change REAL NOT NULL,
            quantity_before REAL,
            quantity_after REAL,
            reason TEXT,
            source TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 索引
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_history_item
        ON inventory_history(item_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_history_action
        ON inventory_history(action)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_history_created
        ON inventory_history(created_at)
    ''')

    conn.commit()
    conn.close()


def record_deduct(
    item_id: int,
    item_name: str,
    quantity: float,
    quantity_before: float,
    reason: str = None,
    source: str = 'manual',
    metadata: Dict = None
) -> int:
    """
    记录扣减

    Args:
        item_id: 库存项ID
        item_name: 食材名称
        quantity: 扣减数量
        quantity_before: 扣减前数量
        reason: 扣减原因
        source: 来源（manual/recipe/auto）
        metadata: 附加数据

    Returns:
        int: 记录ID
    """
    return _record(
        item_id=item_id,
        item_name=item_name,
        action='deduct',
        quantity_change=-quantity,
        quantity_before=quantity_before,
        quantity_after=quantity_before - quantity,
        reason=reason,
        source=source,
        metadata=metadata
    )


def record_add(
    item_id: int,
    item_name: str,
    quantity: float,
    quantity_before: float = 0,
    reason: str = None,
    source: str = 'manual',
    metadata: Dict = None
) -> int:
    """
    记录添加

    Args:
        item_id: 库存项ID
        item_name: 食材名称
        quantity: 添加数量
        quantity_before: 添加前数量
        reason: 添加原因
        source: 来源（manual/photo/auto）
        metadata: 附加数据

    Returns:
        int: 记录ID
    """
    return _record(
        item_id=item_id,
        item_name=item_name,
        action='add',
        quantity_change=quantity,
        quantity_before=quantity_before,
        quantity_after=quantity_before + quantity,
        reason=reason,
        source=source,
        metadata=metadata
    )


def record_update(
    item_id: int,
    item_name: str,
    quantity_before: float,
    quantity_after: float,
    reason: str = None,
    source: str = 'manual',
    metadata: Dict = None
) -> int:
    """
    记录更新

    Args:
        item_id: 库存项ID
        item_name: 食材名称
        quantity_before: 更新前数量
        quantity_after: 更新后数量
        reason: 更新原因
        source: 来源
        metadata: 附加数据

    Returns:
        int: 记录ID
    """
    return _record(
        item_id=item_id,
        item_name=item_name,
        action='update',
        quantity_change=quantity_after - quantity_before,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        reason=reason,
        source=source,
        metadata=metadata
    )


def record_delete(
    item_id: int,
    item_name: str,
    quantity_before: float,
    reason: str = None,
    source: str = 'manual',
    metadata: Dict = None
) -> int:
    """
    记录删除

    Args:
        item_id: 库存项ID
        item_name: 食材名称
        quantity_before: 删除前数量
        reason: 删除原因
        source: 来源
        metadata: 附加数据

    Returns:
        int: 记录ID
    """
    return _record(
        item_id=item_id,
        item_name=item_name,
        action='delete',
        quantity_change=-quantity_before,
        quantity_before=quantity_before,
        quantity_after=0,
        reason=reason,
        source=source,
        metadata=metadata
    )


def _record(
    item_id: int,
    item_name: str,
    action: str,
    quantity_change: float,
    quantity_before: float,
    quantity_after: float,
    reason: str = None,
    source: str = None,
    metadata: Dict = None
) -> int:
    """内部记录函数"""
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO inventory_history
        (item_id, item_name, action, quantity_change, quantity_before, quantity_after,
         reason, source, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        item_id,
        item_name,
        action,
        quantity_change,
        quantity_before,
        quantity_after,
        reason,
        source,
        json.dumps(metadata, ensure_ascii=False) if metadata else None
    ))

    conn.commit()
    record_id = cursor.lastrowid
    conn.close()

    return record_id


# ==================== 查询函数 ====================

def get_history(
    item_id: int = None,
    action: str = None,
    days: int = 7,
    limit: int = 100
) -> List[Dict]:
    """
    查询历史记录

    Args:
        item_id: 按库存项筛选
        action: 按动作筛选（add/deduct/update/delete）
        days: 查询最近N天
        limit: 返回数量限制

    Returns:
        list: 历史记录列表
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    conditions = []
    params = []

    if item_id is not None:
        conditions.append("item_id = ?")
        params.append(item_id)

    if action:
        conditions.append("action = ?")
        params.append(action)

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        conditions.append("created_at >= ?")
        params.append(cutoff)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    sql = f'''
        SELECT * FROM inventory_history
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ?
    '''
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_item_history(item_id: int, limit: int = 20) -> List[Dict]:
    """
    获取特定库存项的历史

    Args:
        item_id: 库存项ID
        limit: 返回数量

    Returns:
        list: 历史记录
    """
    return get_history(item_id=item_id, limit=limit)


def get_deduct_history(days: int = 7, limit: int = 50) -> List[Dict]:
    """
    获取扣减历史

    Args:
        days: 查询最近N天
        limit: 返回数量

    Returns:
        list: 扣减记录
    """
    return get_history(action='deduct', days=days, limit=limit)


def get_add_history(days: int = 7, limit: int = 50) -> List[Dict]:
    """
    获取添加历史

    Args:
        days: 查询最近N天
        limit: 返回数量

    Returns:
        list: 添加记录
    """
    return get_history(action='add', days=days, limit=limit)


# ==================== 统计函数 ====================

def get_statistics(days: int = 7) -> Dict:
    """
    获取统计信息

    Args:
        days: 统计最近N天

    Returns:
        dict: 统计信息
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # 总记录数
    cursor.execute('''
        SELECT COUNT(*) FROM inventory_history WHERE created_at >= ?
    ''', (cutoff,))
    total_records = cursor.fetchone()[0]

    # 按动作统计
    cursor.execute('''
        SELECT action, COUNT(*) as count, SUM(ABS(quantity_change)) as total_quantity
        FROM inventory_history
        WHERE created_at >= ?
        GROUP BY action
    ''', (cutoff,))

    by_action = {}
    for row in cursor.fetchall():
        by_action[row['action']] = {
            'count': row['count'],
            'total_quantity': row['total_quantity']
        }

    # 按来源统计
    cursor.execute('''
        SELECT source, COUNT(*) as count
        FROM inventory_history
        WHERE created_at >= ?
        GROUP BY source
    ''', (cutoff,))

    by_source = {}
    for row in cursor.fetchall():
        by_source[row['source'] or 'unknown'] = row['count']

    # 最常扣减的食材
    cursor.execute('''
        SELECT item_name, COUNT(*) as count, SUM(ABS(quantity_change)) as total_quantity
        FROM inventory_history
        WHERE action = 'deduct' AND created_at >= ?
        GROUP BY item_name
        ORDER BY count DESC
        LIMIT 10
    ''', (cutoff,))

    top_deducted = []
    for row in cursor.fetchall():
        top_deducted.append({
            'name': row['item_name'],
            'count': row['count'],
            'total_quantity': row['total_quantity']
        })

    # 最常添加的食材
    cursor.execute('''
        SELECT item_name, COUNT(*) as count, SUM(quantity_change) as total_quantity
        FROM inventory_history
        WHERE action = 'add' AND created_at >= ?
        GROUP BY item_name
        ORDER BY count DESC
        LIMIT 10
    ''', (cutoff,))

    top_added = []
    for row in cursor.fetchall():
        top_added.append({
            'name': row['item_name'],
            'count': row['count'],
            'total_quantity': row['total_quantity']
        })

    conn.close()

    return {
        'days': days,
        'total_records': total_records,
        'by_action': by_action,
        'by_source': by_source,
        'top_deducted': top_deducted,
        'top_added': top_added
    }


def get_daily_summary(days: int = 7) -> List[Dict]:
    """
    获取每日摘要

    Args:
        days: 统计最近N天

    Returns:
        list: 每日摘要
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    cursor.execute('''
        SELECT
            DATE(created_at) as date,
            SUM(CASE WHEN action = 'add' THEN quantity_change ELSE 0 END) as added,
            SUM(CASE WHEN action = 'deduct' THEN ABS(quantity_change) ELSE 0 END) as deducted,
            COUNT(*) as operations
        FROM inventory_history
        WHERE created_at >= ?
        GROUP BY DATE(created_at)
        ORDER BY date DESC
    ''', (cutoff,))

    results = []
    for row in cursor.fetchall():
        results.append({
            'date': row['date'],
            'added': row['added'],
            'deducted': row['deducted'],
            'net': row['added'] - row['deducted'],
            'operations': row['operations']
        })

    conn.close()

    return results


# ==================== 初始化 ====================

# 自动初始化表
init_history_table()


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("Inventory History Module Test")
    print("=" * 50)

    # 测试记录
    record_deduct(1, "鸡蛋", 2, 10, reason="做饭", source="recipe")
    record_add(1, "鸡蛋", 6, 8, reason="购买", source="photo")
    record_update(2, "牛奶", 2, 1, reason="修改数量", source="manual")

    # 测试查询
    history = get_history(days=1)
    print(f"\nHistory ({len(history)} records):")
    for h in history[:5]:
        print(f"  [{h['action']}] {h['item_name']}: {h['quantity_change']}")

    # 测试统计
    stats = get_statistics(days=1)
    print("\nStatistics:")
    print(f"  Total records: {stats['total_records']}")
    print(f"  By action: {stats['by_action']}")

    # 测试每日摘要
    daily = get_daily_summary(days=1)
    print("\nDaily summary:")
    for d in daily:
        print(f"  {d['date']}: +{d['added']} -{d['deducted']}")

    print("\nDone!")
