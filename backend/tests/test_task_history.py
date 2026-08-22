#!/usr/bin/env python3
"""调度任务历史：跨进程重启仍在，且手动触发的结果不靠共享游标判定。

盯住两件事：

1. **持久化**。历史落库而不是留在内存 list 里：容器一重启就归零，而它正是回答「昨天那次
   自动下发到底发生了没有」的唯一线索，这种问题只会在事后被问起。

2. **不用共享游标**。手动触发下发不能靠 `len(get_task_history())` 记下标、事后切片：那份
   历史被 4 个调度 job、8 个 gunicorn 线程与设备按键线程共写，中途任何别的任务追加一条都会
   被算进本次结果（于是「立即下发」返回一个「过期食材检查」的失败原因），而超过上限时整列
   左移还会让下标彻底失效（返回 skipped）。`_publish_one_meal` 直接返回本次记录。

用法：backend/.venv/bin/python -m pytest tests/test_task_history.py -v
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-taskhist-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

import pytest                               # noqa: E402

from src import database as db              # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def clean_history():
    """每个用例从空历史开始 —— 落库后记录会跨用例累积。"""
    conn = db.get_connection()
    try:
        conn.execute('DELETE FROM scheduler_task_history')
        conn.commit()
    finally:
        conn.close()
    yield


def test_records_survive_a_new_connection():
    """写进库、换连接仍读得到 —— 这就是「重启后还在」的可测形式。"""
    db.add_task_record('display_publish', '下发晚餐整套画面', True)

    records = db.get_task_records()
    assert len(records) == 1
    assert records[0]['task_id'] == 'display_publish'
    assert records[0]['success'] is True
    assert records[0]['error'] is None
    # timestamp 必须可被 fromisoformat 解析：`_recent_task` 的窗口判定依赖它。
    datetime.fromisoformat(records[0]['timestamp'])


def test_history_is_chronological_with_newest_last():
    """既有调用方用 `history[-1]` 取最新一条，正序语义不能变。"""
    for i in range(3):
        db.add_task_record('t', f'第{i}条', success=(i == 2))

    records = db.get_task_records()
    assert [r['description'] for r in records] == ['第0条', '第1条', '第2条']
    assert records[-1]['success'] is True, '最新一条应在末尾'


def test_limit_returns_the_most_recent_not_the_oldest():
    """取 N 条时必须是**最近** N 条。用 id ASC 直接 LIMIT 会返回最旧的那一批。"""
    for i in range(10):
        db.add_task_record('t', f'第{i}条', True)

    recent = db.get_task_records(limit=3)
    assert [r['description'] for r in recent] == ['第7条', '第8条', '第9条']


def test_table_is_pruned_to_keep_limit():
    """表按 TASK_HISTORY_KEEP 自动裁剪，不需要另设清理任务。"""
    keep = db.TASK_HISTORY_KEEP
    for i in range(keep + 25):
        db.add_task_record('t', f'第{i}条', True)

    total = db.count_task_records()
    assert total <= keep + 1, f'裁剪没生效：{total} 条'
    # 裁掉的必须是最旧的，最新那条要留着。
    assert db.get_task_records(limit=1)[0]['description'] == f'第{keep + 24}条'


def test_recent_task_window_uses_local_time():
    """`_recent_task` 的窗口判定与写入口径必须都是本地时间，否则整体偏一个时区。"""
    db.add_task_record('display_publish', '无可下发设备', False)

    assert db.has_recent_task_record('display_publish', '无可下发设备', 60) is True
    assert db.has_recent_task_record('display_publish', '别的事', 60) is False
    assert db.has_recent_task_record('别的任务', '无可下发设备', 60) is False

    # 一小时前的记录不该落进 30 分钟的窗口。
    old = (datetime.now() - timedelta(hours=1)).isoformat()
    db.add_task_record('expiring_check', '检查过期食材', True, timestamp=old)
    assert db.has_recent_task_record('expiring_check', '检查过期食材', 30) is False
    assert db.has_recent_task_record('expiring_check', '检查过期食材', 120) is True


def test_add_task_record_returns_the_stored_record():
    """返回值就是判据来源 —— `_publish_one_meal` 靠它组装本次结果，不再翻共享历史。"""
    record = db.add_task_record('display_publish', '下发失败', False, '设备离线')

    assert record['task_id'] == 'display_publish'
    assert record['success'] is False
    assert record['error'] == '设备离线'
    assert record == db.get_task_records()[-1]


def test_publish_returns_only_its_own_records(monkeypatch):
    """并发写入历史时，手动触发拿到的仍只是自己那一次的记录。

    模拟真实的串台场景：发布过程中另一个 job（过期检查）也在写历史。用下标切片会把它算
    进来，用返回值则不会。
    """
    from src import scheduler as sched

    monkeypatch.setattr(sched.task_scheduler, '_publish_targets', lambda: ['devA'])

    def fake_plan(key):
        # 模拟「别的 job 插了一条」，正是它会污染基于下标的判据。
        db.add_task_record('expiring_check', '发现3种即将过期食材', True)
        return None      # 让发布走「没有可用菜谱方案」这条确定的失败分支

    monkeypatch.setattr(sched.task_scheduler, '_plan_for_meal', fake_plan)

    result = sched.trigger_display_publish(meal_type='dinner', force=True)

    assert result['success'] is False
    assert '菜谱方案' in result['error'], \
        f"错误原因串到了别的任务上：{result['error']}"
    # 库里确实两条都在，只是判据没有把别人那条算进来。
    assert {r['task_id'] for r in db.get_task_records()} == {
        'display_publish', 'expiring_check'
    }


def test_publish_skipped_is_distinguishable_from_success(monkeypatch):
    """幂等跳过返回空记录列表，调用方据此报 skipped —— 不能和「发成功了」混为一谈。"""
    from src import scheduler as sched

    monkeypatch.setattr(sched.task_scheduler, '_publish_targets', lambda: ['devA'])
    monkeypatch.setattr(sched.device_display, 'has_recent_upload',
                        lambda *a, **kw: True)

    result = sched.trigger_display_publish(meal_type='dinner', force=False)

    assert result['success'] is True
    assert result.get('skipped') is True
    assert db.count_task_records() == 0, '幂等跳过不该留下执行记录'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
