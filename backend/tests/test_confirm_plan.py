#!/usr/bin/env python3
"""确认菜谱方案（`/api/confirm` → [agent.confirm_plan]）的成功路径与失败路径。

这是唯一会真正改写库存的用户操作，成功路径必须单独盯住：扣减成功后拼回复时若读到
[database.deduct_inventory] 不返回的键就会 500，而那时库存已经扣了、菜单已置为 confirmed。
用户看到 500 会再点一次确认，同一份菜谱的食材被扣两遍，库存从此与实物不符，
而后续菜谱严格按库存出菜。失败路径（库存不足）反而不会碰到这类分支，所以两侧都要覆盖。

用法：backend/.venv/bin/python -m pytest tests/test_confirm_plan.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-confirm-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

import pytest                               # noqa: E402

from src import agent                       # noqa: E402
from src import database as db               # noqa: E402

db.init_db()


def _fresh_plan(dishes, inventory):
    """建一份 pending 菜单与对应库存，返回 plan_id。每个用例用独立食材名避免互相干扰。"""
    for name, qty, unit in inventory:
        db.add_inventory_item(name=name, quantity=qty, unit=unit, category='蔬菜')
    return db.add_meal_plan(
        meal_type='晚餐', meal_time='18:00', people_count=2,
        plan_a={'dishes': dishes}, plan_b=None,
    )


def _quantity_of(name):
    for item in db.get_inventory(page_size=1000)['items']:
        if item['name'] == name:
            return item['quantity']
    return None


def _status_of(plan_id):
    conn = db.get_connection()
    try:
        row = conn.execute(
            'SELECT status FROM meal_plan WHERE id = ?', (plan_id,)
        ).fetchone()
    finally:
        # `with sqlite3.Connection` 只提交事务、不关连接，这里必须显式 close。
        conn.close()
    return row['status'] if row else None


def test_confirm_succeeds_and_reports_before_after():
    """成功路径必须返回 action=confirm，且回复里说清每样食材扣减前后的数量。"""
    plan_id = _fresh_plan(
        [{'name': '土豆丝', 'ingredients': [{'name': '甲土豆', 'amount': '2个'}]}],
        [('甲土豆', 5, '个')],
    )

    result = agent.confirm_plan(plan_id, 'A')

    assert result['action'] == 'confirm', f"扣减成功却没返回 confirm：{result}"
    assert '甲土豆：5→3个' in result['reply'], result['reply']
    assert _quantity_of('甲土豆') == 3
    assert _status_of(plan_id) == 'confirmed'
    print('  ✓ 扣减成功并报出 5→3个')


def test_confirm_does_not_raise_on_any_deduct_result_shape():
    """回复拼接不得抛异常 —— 它跑在两次写库之后，抛出去就变成「扣了库存还报 500」。

    直接喂各种畸形/缺字段的扣减结果：真实的 deduct_inventory 不会返回这些形状，但拼接函数
    是整条链路上最后一步、也是唯一一个「抛了就必然导致二次扣减」的地方，值得钉死。
    """
    shapes = [
        [],
        [{}],
        [{'name': '甲'}],
        [{'name': '乙', 'error': '库存不足'}],
        [{'name': '丙', 'deducted': 2, 'unit': '个'}],                    # 无 details
        [{'name': '丁', 'unit': '个', 'details': [{'deducted': 1, 'remaining': 2}]}],
        [{'name': '戊', 'details': [None, {'deducted': None, 'remaining': None}]}],
        [{'name': None, 'details': 'not-a-list'}],
    ]
    for shape in shapes:
        text = agent.format_confirm_reply('A', '晚餐', shape)
        assert '已选择A方案' in text and '祝你晚餐愉快' in text, shape
    print('  ✓ 8 种畸形扣减结果都能拼出回复且不抛')


def test_confirm_reports_error_reason_per_item():
    """某样食材扣不动时，回复里要写出后端给的原因，而不是笼统一句「库存不足」。"""
    text = agent.format_confirm_reply('B', '午餐', [
        {'name': '香菇', 'error': '数量必须大于0'},
        {'name': '土豆', 'unit': '个', 'details': [{'deducted': 1, 'remaining': 1}]},
    ])
    assert '香菇：数量必须大于0' in text
    assert '土豆：2→1个' in text
    print('  ✓ 逐项给出各自的失败原因')


def test_confirm_trims_trailing_zero():
    """库存量是浮点，`3.0个` 要显示成 `3个`；`2.5` 保持原样。"""
    text = agent.format_confirm_reply('A', '晚餐', [
        {'name': '鸡蛋', 'unit': '个', 'details': [{'deducted': 2.0, 'remaining': 3.0}]},
        {'name': '牛奶', 'unit': '升', 'details': [{'deducted': 0.5, 'remaining': 2.5}]},
    ])
    assert '鸡蛋：5→3个' in text, text
    assert '牛奶：3→2.5升' in text, text
    print('  ✓ 整数不带小数点，非整数保留')


def test_confirm_unknown_plan_is_a_business_state_not_a_crash():
    """不存在的 plan_id / 方案名是正常业务态，要给可读提示而不是异常。"""
    assert agent.confirm_plan(999999, 'A')['action'] == 'confirm_error'

    plan_id = _fresh_plan(
        [{'name': '拍黄瓜', 'ingredients': [{'name': '己黄瓜', 'amount': '1个'}]}],
        [('己黄瓜', 3, '个')],
    )
    assert agent.confirm_plan(plan_id, 'B')['action'] == 'confirm_error', \
        '只有 plan_a 时确认 B 方案应给业务错误'
    assert _quantity_of('己黄瓜') == 3, '失败路径不得扣减库存'
    print('  ✓ 找不到计划/方案时不扣库存且给可读提示')


def test_confirm_deducts_across_multiple_records():
    """同名食材分多条记录（不同生产日期）时，扣减跨记录，前后数量要合起来算。"""
    db.add_inventory_item(name='庚番茄', quantity=2, unit='个', category='蔬菜')
    db.add_inventory_item(name='庚番茄', quantity=3, unit='个', category='蔬菜')
    plan_id = db.add_meal_plan(
        meal_type='晚餐', meal_time='18:00', people_count=2,
        plan_a={'dishes': [{'name': '番茄汤', 'ingredients': [
            {'name': '庚番茄', 'amount': '4个'}
        ]}]},
        plan_b=None,
    )

    result = agent.confirm_plan(plan_id, 'A')

    assert result['action'] == 'confirm', result
    assert '庚番茄：5→1个' in result['reply'], result['reply']
    print('  ✓ 跨记录扣减的前后数量正确合计')


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
