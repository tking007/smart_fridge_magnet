"""
通知系统模块 — 独立的通知管理
支持：通知创建、查询、标记已读、清空、优先级、类型分类
"""
import threading
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum


class NotificationType(Enum):
    """通知类型"""
    RECIPE = "recipe"                    # 菜谱推荐
    EXPIRING = "expiring"                # 即将过期
    INVENTORY_LOW = "inventory_low"      # 库存不足
    INVENTORY_EMPTY = "inventory_empty"  # 库存为空
    SYSTEM = "system"                    # 系统通知
    REMINDER = "reminder"                # 提醒


class NotificationPriority(Enum):
    """通知优先级"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


class NotificationManager:
    """通知管理器"""

    def __init__(self, max_size: int = 100):
        """
        初始化通知管理器

        Args:
            max_size: 最大通知数量
        """
        self._notifications: List[Dict] = []
        self._lock = threading.Lock()
        self._max_size = max_size

    def add(
        self,
        type: NotificationType,
        title: str,
        message: str,
        data: Optional[Dict] = None,
        priority: NotificationPriority = NotificationPriority.NORMAL
    ) -> Dict:
        """
        添加通知

        Args:
            type: 通知类型
            title: 通知标题
            message: 通知内容
            data: 附加数据
            priority: 优先级

        Returns:
            dict: 创建的通知
        """
        notification = {
            'id': self._generate_id(),
            'type': type.value,
            'title': title,
            'message': message,
            'data': data or {},
            'priority': priority.value,
            'timestamp': datetime.now().isoformat(),
            'read': False
        }

        with self._lock:
            self._notifications.append(notification)

            # 超过最大数量时删除最旧的已读通知
            if len(self._notifications) > self._max_size:
                self._cleanup()

        return notification

    def get_all(self, unread_only: bool = False) -> List[Dict]:
        """
        获取所有通知

        Args:
            unread_only: 是否只返回未读通知

        Returns:
            list: 通知列表
        """
        with self._lock:
            if unread_only:
                return [n for n in self._notifications if not n['read']]
            return self._notifications.copy()

    def get_by_type(self, type: NotificationType, unread_only: bool = False) -> List[Dict]:
        """
        按类型获取通知

        Args:
            type: 通知类型
            unread_only: 是否只返回未读通知

        Returns:
            list: 通知列表
        """
        with self._lock:
            notifications = [n for n in self._notifications if n['type'] == type.value]
            if unread_only:
                notifications = [n for n in notifications if not n['read']]
            return notifications

    def get_unread_count(self) -> int:
        """获取未读通知数量"""
        with self._lock:
            return sum(1 for n in self._notifications if not n['read'])

    def mark_read(self, notification_id: str) -> bool:
        """
        标记通知为已读

        Args:
            notification_id: 通知ID

        Returns:
            bool: 是否成功
        """
        with self._lock:
            for n in self._notifications:
                if n['id'] == notification_id:
                    n['read'] = True
                    return True
            return False

    def mark_all_read(self) -> int:
        """
        标记所有通知为已读

        Returns:
            int: 标记的数量
        """
        count = 0
        with self._lock:
            for n in self._notifications:
                if not n['read']:
                    n['read'] = True
                    count += 1
        return count

    def delete(self, notification_id: str) -> bool:
        """
        删除通知

        Args:
            notification_id: 通知ID

        Returns:
            bool: 是否成功
        """
        with self._lock:
            for i, n in enumerate(self._notifications):
                if n['id'] == notification_id:
                    self._notifications.pop(i)
                    return True
            return False

    def clear(self, type: Optional[NotificationType] = None) -> int:
        """
        清空通知

        Args:
            type: 指定类型清空，None 表示清空全部

        Returns:
            int: 清空的数量
        """
        with self._lock:
            if type is None:
                count = len(self._notifications)
                self._notifications.clear()
                return count
            else:
                before = len(self._notifications)
                self._notifications = [n for n in self._notifications if n['type'] != type.value]
                return before - len(self._notifications)

    def _cleanup(self):
        """清理最旧的已读通知"""
        # 按时间排序，删除最旧的已读通知
        read_notifications = [n for n in self._notifications if n['read']]
        if read_notifications:
            oldest = min(read_notifications, key=lambda n: n['timestamp'])
            self._notifications.remove(oldest)

    def _generate_id(self) -> str:
        """生成通知ID"""
        import uuid
        return str(uuid.uuid4())[:8]

    def get_stats(self) -> Dict:
        """
        获取通知统计

        Returns:
            dict: 统计信息
        """
        with self._lock:
            total = len(self._notifications)
            unread = sum(1 for n in self._notifications if not n['read'])

            by_type = {}
            for n in self._notifications:
                t = n['type']
                if t not in by_type:
                    by_type[t] = {'total': 0, 'unread': 0}
                by_type[t]['total'] += 1
                if not n['read']:
                    by_type[t]['unread'] += 1

            return {
                'total': total,
                'unread': unread,
                'by_type': by_type
            }


# ==================== 全局实例 ====================

notification_manager = NotificationManager()


# ==================== 便捷函数 ====================

def add_recipe_notification(meal_type: str, message: str, data: Dict = None):
    """添加菜谱推荐通知"""
    return notification_manager.add(
        type=NotificationType.RECIPE,
        title=f"{meal_type}推荐",
        message=message,
        data=data,
        priority=NotificationPriority.NORMAL
    )


def add_expiring_notification(items: List[Dict]):
    """添加即将过期通知"""
    if not items:
        return None

    names = "、".join([i['name'] for i in items[:3]])
    if len(items) > 3:
        names += f"等{len(items)}种"

    return notification_manager.add(
        type=NotificationType.EXPIRING,
        title="食材即将过期",
        message=f"有 {len(items)} 种食材即将过期：{names}",
        data={'items': items},
        priority=NotificationPriority.HIGH
    )


def add_inventory_low_notification(category: str, count: int):
    """添加库存不足通知"""
    return notification_manager.add(
        type=NotificationType.INVENTORY_LOW,
        title=f"{category}库存不足",
        message=f"{category}类食材只剩 {count} 种，建议补充",
        data={'category': category, 'count': count},
        priority=NotificationPriority.NORMAL
    )


def add_system_notification(title: str, message: str):
    """添加系统通知"""
    return notification_manager.add(
        type=NotificationType.SYSTEM,
        title=title,
        message=message,
        priority=NotificationPriority.LOW
    )


def get_notifications(unread_only: bool = True) -> List[Dict]:
    """获取通知"""
    return notification_manager.get_all(unread_only=unread_only)


def get_unread_count() -> int:
    """获取未读数量"""
    return notification_manager.get_unread_count()


def mark_read(notification_id: str) -> bool:
    """标记已读"""
    return notification_manager.mark_read(notification_id)


def mark_all_read() -> int:
    """标记全部已读"""
    return notification_manager.mark_all_read()


def clear_notifications(type: Optional[str] = None) -> int:
    """清空通知"""
    if type:
        try:
            ntype = NotificationType(type)
            return notification_manager.clear(type=ntype)
        except ValueError:
            return 0
    return notification_manager.clear()


def get_notification_stats() -> Dict:
    """获取统计"""
    return notification_manager.get_stats()


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("Notification Module Test")
    print("=" * 50)

    # 测试添加通知
    add_recipe_notification("晚餐", "推荐红烧肉", {"plan_id": 1})
    add_expiring_notification([{"name": "鸡蛋"}, {"name": "牛奶"}])
    add_inventory_low_notification("蔬菜", 2)
    add_system_notification("系统", "欢迎使用智能冰箱")

    # 测试获取
    all_notifications = get_notifications(unread_only=False)
    print(f"\nTotal notifications: {len(all_notifications)}")

    unread = get_unread_count()
    print(f"Unread: {unread}")

    # 测试标记已读
    if all_notifications:
        mark_read(all_notifications[0]['id'])
        print(f"After mark_read: {get_unread_count()} unread")

    # 测试统计
    stats = get_notification_stats()
    print(f"\nStats: {stats}")

    # 测试清空
    cleared = clear_notifications()
    print(f"\nCleared: {cleared}")
    print(f"Remaining: {len(get_notifications(unread_only=False))}")

    print("\nDone!")
