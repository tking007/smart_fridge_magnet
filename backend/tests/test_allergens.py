#!/usr/bin/env python3
"""校验过敏源这条链路：进 prompt、生成后校验、整体替换成员。

单独一套测试是因为过敏是这个系统里唯一「弄错会伤人」的约束，而这条链路上每一处看起来都
可能是对的、唯独模型不知道谁过敏：`family_profile` 有 allergies 列、App 有输入位，只要
`get_profile_summary()` 或 `agent.get_context()` 漏拼一次就静默失效，不报错、无人抱怨。

盯住的行为：
  1. 过敏源必须进 prompt，且与「不吃」区分表述（并列成同强度偏好时模型会平等对待）。
  2. 全家过敏源要汇总置顶 —— 菜是一锅做给全家的，分餐做不到。
  3. 生成结果要被校验，双向子串匹配（登记「花生」要拦住「花生油」）。
  4. skip_ingredients 里出现过敏源不算命中（那本来就是"要去掉的配料"）。
  5. 校验器面对畸形结构不能抛异常 —— 校验器自己崩掉等于这道检查静默失效。
  6. 成员整体替换必须是单事务，且校验先于写库（校验失败不能把成员清空）。

用法：python tests/test_allergens.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-allergen-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import agent                       # noqa: E402
from src import database as db               # noqa: E402
from src.app import app                      # noqa: E402


def _seed_members():
    db.replace_members([
        {'name': '爸爸', 'age': 40, 'allergies': ['花生'], 'dislikes_taste': ['辣']},
        {'name': '孩子', 'age': 6, 'allergies': ['芒果'], 'dislikes_main': ['苦瓜']},
    ])


def test_allergens_in_profile_summary():
    """过敏源进 prompt，汇总置顶，且与「不吃」分开表述。"""
    _seed_members()
    summary = db.get_profile_summary()

    assert '花生' in summary and '芒果' in summary, '过敏源必须出现在 prompt 摘要里'
    # 汇总行要在最前面：约束放在开头被遵守得更好，而且它是全家级的。
    first_line = summary.splitlines()[0]
    assert '过敏源' in first_line, f'过敏汇总必须置顶，实际第一行：{first_line}'
    assert '花生' in first_line and '芒果' in first_line, '汇总行要包含全家所有过敏源'
    assert '绝对禁止' in first_line, '措辞必须是禁止级，不能与「不吃」同强度'

    # 逐人那几行里，过敏用「过敏禁用」而不是「不吃」
    assert '过敏禁用花生' in summary, '逐人行里过敏要与口味忌口区分表述'
    assert '不吃辣' in summary, '口味忌口仍按原样表述'
    print('  ✓ 过敏源进 prompt、汇总置顶、与忌口区分表述')


def test_allergens_in_chat_context():
    """对话链路的上下文同样要带过敏源。"""
    _seed_members()
    context = agent.get_context()
    # 这是「今天想吃辣的」这类对话唯一能看到的家庭约束，漏了它等于对话生成的菜单
    # 完全不受过敏限制。
    assert '过敏禁用' in context, f'get_context 必须带过敏源：{context}'
    assert '花生' in context and '芒果' in context
    print('  ✓ 对话上下文带过敏源')


def test_collect_allergens_dedup():
    """全家过敏源去重排序，空白项被丢掉。"""
    db.replace_members([
        {'name': 'A', 'allergies': ['花生', '  ', '芒果']},
        {'name': 'B', 'allergies': ['花生', '虾']},
    ])
    got = db.collect_allergens()
    # 顺序按 Unicode 码点，本身不重要；这里断言精确值只为钉住「稳定输出」——
    # 不稳定的顺序会让 prompt 每次都不一样，进而让"同样的库存为什么给出不同菜单"
    # 这类问题多一个无关变量。
    assert got == ['芒果', '花生', '虾'], got
    print('  ✓ 过敏源去重、去空白、排序稳定')


def test_violation_detection():
    """双向子串匹配：登记「花生」要拦住「花生油」与菜名里的「花生米」。"""
    plans = {
        'plan_a': {'dishes': [
            {'name': '花生米拌黄瓜', 'ingredients': [{'name': '黄瓜'}, {'name': '花生米'}]},
            {'name': '清炒时蔬', 'ingredients': [{'name': '上海青'}]},
        ]},
        'plan_b': {'dishes': [
            {'name': '凉拌木耳', 'ingredients': [{'name': '木耳'}, {'name': '花生油'}]},
        ]},
    }
    violations = agent.find_allergen_violations(plans, ['花生'])
    assert len(violations) == 2, f'菜名与配料都要拦：{violations}'
    assert any('plan_a' in v for v in violations)
    assert any('plan_b' in v for v in violations)
    # 无过敏源时不该产生任何结论
    assert agent.find_allergen_violations(plans, []) == []
    print('  ✓ 双向子串匹配覆盖菜名与配料')


def test_skip_ingredients_not_a_violation():
    """skip_ingredients 里的过敏源不算命中 —— 那是明确要去掉的配料。"""
    plans = {'plan_a': {'dishes': [{
        'name': '宫保鸡丁',
        'ingredients': [{'name': '鸡胸肉'}, {'name': '黄瓜'}],
        'skip_ingredients': ['花生'],
        'skip_reason': '家人过敏',
    }]}}
    assert agent.find_allergen_violations(plans, ['花生']) == [], \
        '被排除的配料不应判为命中，否则每份正确处理过的菜谱都会触发重试'
    print('  ✓ skip_ingredients 不算命中')


def test_malformed_plans_do_not_raise():
    """畸形结构不能让校验器抛异常。

    校验器自己因为一个 None 崩掉，等于这道安全检查静默失效 —— 而模型返回的结构本来就
    不保证完整（缺 plan_b、dishes 是 None、菜是字符串都见过）。
    """
    cases = [
        {},
        {'plan_a': None, 'plan_b': None},
        {'plan_a': 'not a dict'},
        {'plan_a': {'dishes': None}},
        {'plan_a': {'dishes': ['字符串菜', None, 42]}},
        {'plan_a': {'dishes': [{'name': None, 'ingredients': 'not a list'}]}},
        {'plan_a': {'dishes': [{'ingredients': [None, {'name': None}, '花生']}]}},
    ]
    for case in cases:
        result = agent.find_allergen_violations(case, ['花生'])
        assert isinstance(result, list), f'必须返回列表：{case}'
    # 最后一例里字符串形式的配料「花生」应当被认出来
    assert agent.find_allergen_violations(
        {'plan_a': {'dishes': [{'ingredients': ['花生']}]}}, ['花生']
    ), '字符串形式的配料也要检查'
    print('  ✓ 畸形结构不抛异常且字符串配料仍被检查')


def test_replace_members_is_whole_list():
    """整体替换：列表里没有的成员会被移除。"""
    _seed_members()
    assert len(db.get_all_members()) == 2

    count = db.replace_members([{'name': '爸爸', 'allergies': []}])
    assert count == 1, '替换后应只剩传入的成员'
    assert db.collect_allergens() == [], '去掉过敏源后必须立刻不再约束推荐'

    # 无名成员被跳过而不是写成空记录
    count = db.replace_members([{'name': '妈妈'}, {'name': '   '}, {'age': 3}])
    assert count == 1, f'无名成员应被跳过，实际写入 {count}'
    print('  ✓ 整体替换语义正确，无名成员被跳过')


def test_put_members_endpoint():
    """PUT 接口：正常写入、回显过敏源、校验先于写库。"""
    client = app.test_client()
    _seed_members()

    resp = client.put('/api/profile/members', json={'members': [
        {'name': '爸爸', 'allergies': ['花生'], 'dislikes_main': ['苦瓜']},
    ]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['member_count'] == 1
    # 回显是 App 自证「后端真的收到了过敏源」的唯一途径
    assert body['allergens'] == ['花生'], body

    before = db.get_all_members()
    bad_cases = [
        {'members': 'not a list'},
        {'members': [{'age': 1}]},                          # 缺名字
        {'members': [{'name': 'a', 'allergies': '花生'}]},   # 列表字段传了字符串
        {'members': [{'name': 'a', 'allergies': [{}]}]},    # 数组元素不是字符串
        {'members': [{'name': 'a', 'dislikes_main': [42]}]},
        {'members': [{'name': 'a', 'health_notes': 42}]},   # 摘要 join 会因数字崩溃
        {'members': [{'name': f'm{i}'} for i in range(21)]},  # 超上限
    ]
    for payload in bad_cases:
        r = client.put('/api/profile/members', json=payload)
        assert r.status_code == 400, f'应拒绝：{payload} → {r.status_code}'
    # 校验必须发生在写库之前：否则一次非法请求会先 DELETE 再失败，把成员清空，
    # 而后续推荐将完全失去过敏与忌口约束。
    assert db.get_all_members() == before, '非法请求不能影响已有成员'
    print('  ✓ PUT 接口写入、回显过敏源、校验先于写库')


def main():
    print('=' * 60)
    print('过敏源链路测试')
    print('=' * 60)
    tests = [
        test_allergens_in_profile_summary,
        test_allergens_in_chat_context,
        test_collect_allergens_dedup,
        test_violation_detection,
        test_skip_ingredients_not_a_violation,
        test_malformed_plans_do_not_raise,
        test_replace_members_is_whole_list,
        test_put_members_endpoint,
    ]
    for fn in tests:
        print(f'\n{fn.__name__}:')
        fn()
    print('\n' + '=' * 60)
    print(f'全部通过（{len(tests)} 项）')
    print('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
