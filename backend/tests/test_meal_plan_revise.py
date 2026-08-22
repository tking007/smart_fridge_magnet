#!/usr/bin/env python3
"""校验改菜单链路：餐次归一化、多轮上下文、说人话改单（只算）与落库（只写）。

盯住的行为：
  - 归一化后恒落在三餐内，且「关键词匹配」与 normalize_meal_type() 两条入口口径一致
    （同一个词在两边给出不同答案时，两处代码看着都对）。餐次越界（下午茶、夜宵）会让
    这一餐既不被自动下发、按餐次也读不回来，而生成本身是成功的。
  - 多轮上下文：conversation_history 必须有读回处，否则说「换一个」时模型不知道在换什么，
    且这类缺陷不报错，只会让每一轮都像第一轮。
  - revise 只算不写：结果不落库，等用户看过 diff 再确认。让「确认」重新调一次模型的话，
    第二次结果与他看过的 diff 不是同一份。
  - diff 用集合差而不是逐位比：顺序变化不算改动。
  - **过敏在 revise 与 apply 两处各拦一次**。apply 不能因为 revise 查过就跳过：表单式
    微调不经过 revise，客户端也可以直接调 apply。
  - 模型返回解释文字（没有 dishes）时必须拒绝，不能把它当菜单存下去。

注意 patch 的目标：agent.py 用的是 `from .ai_client import call_ai_text_with_json`
（模块级绑定），所以必须替换 `agent.call_ai_text_with_json`；替换
`ai_client.call_ai_text_with_json` 对它无效，那会让测试静默打到真实模型上。

用法：python tests/test_meal_plan_revise.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-revise-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import agent                        # noqa: E402
from src import database as db               # noqa: E402
from src import meals                        # noqa: E402
from src.app import app                       # noqa: E402

db.init_db()

BASE_PLAN = {
    'dishes': [
        {'name': '红烧肉', 'ingredients': [{'name': '五花肉', 'amount': '500g'}],
         'cooking_time': '40分钟', 'difficulty': '中等', 'steps': ['炖']},
        {'name': '清炒上海青', 'ingredients': [{'name': '上海青', 'amount': '200g'}],
         'cooking_time': '10分钟', 'difficulty': '简单', 'steps': ['炒']},
    ],
    'total_time': '50分钟',
}


def _stub_model(reply):
    """替掉 agent 里绑定的模型调用（见模块 docstring 的 patch 说明）。"""
    agent.call_ai_text_with_json = lambda *args, **kwargs: reply


def _seed_dinner():
    db.replace_members([])
    return db.add_meal_plan('晚餐', '18:00', 3, BASE_PLAN, None)


def _latest_dinner_dishes():
    for record in db.get_pending_plans():
        if meals.meal_key(record.get('meal_type')) == 'dinner':
            return agent.dish_names_of(record.get('plan_a'))
    return []


def test_meal_type_never_out_of_range():
    """任何写法都收敛到三餐，且两条入口口径一致。"""
    pairs = [
        ('下午茶推荐', '下午茶'), ('来个夜宵', '夜宵'), ('想吃宵夜', '宵夜'),
        ('想吃点零食', '零食'), ('今天晚上吃什么', '晚上'), ('中午吃啥', '中午'),
        ('早上吃啥', '早上'), ('晚饭呢', '晚饭'), ('午饭', '午饭'),
    ]
    for text, word in pairs:
        from_keyword = agent.parse_meal_info(text)['meal_type']
        from_normalize = agent.normalize_meal_type(word)
        assert meals.meal_key(from_keyword) is not None, \
            f'{text!r} 归一后仍越界：{from_keyword}'
        assert from_keyword == from_normalize, \
            f'{text!r} 两条入口不一致：关键词={from_keyword} normalize={from_normalize}'

    # 空与乱码也要有确定落点，不能返回 None 让调用方去猜
    for junk in (None, '', '   ', '乱七八糟'):
        assert meals.meal_key(agent.normalize_meal_type(junk)) is not None
    print('  ✓ 餐次恒落在三餐内且两条入口一致')


def test_history_is_formatted_and_bounded():
    """历史被拼进 prompt，且单条被截断。"""
    records = [
        {'role': 'user', 'content': '晚饭吃什么'},
        {'role': 'assistant', 'content': 'A' * 500},
        {'role': 'user', 'content': ''},          # 空内容跳过
        {'role': 'user', 'content': '换一个\n第二行'},
    ]
    text = agent.format_history(records)
    assert '用户：晚饭吃什么' in text
    assert '助手：' in text
    # 助手的推荐回复有几百字，原样塞进历史会把真正的上下文挤掉
    assert 'A' * 500 not in text, '长消息必须截断'
    assert '…' in text
    # 一行一条：内嵌换行会让模型把一条读成多条
    assert '换一个 第二行' in text
    assert len(text.splitlines()) == 3, f'空内容应被跳过：{text}'
    assert agent.format_history([]) == '', '无历史时返回空串（不写空标题）'

    from src.prompts import build_chat_prompt
    with_history = build_chat_prompt('换一个', context='ctx', history_text=text)
    assert '最近的对话' in with_history and '晚饭吃什么' in with_history
    # 首轮不该出现一个空的「最近的对话」标题 —— 那会让模型以为历史被清空过
    assert '最近的对话' not in build_chat_prompt('你好', context='ctx')
    print('  ✓ 历史被格式化、截断并拼进 prompt')


def test_chat_reads_history_before_writing_current_turn():
    """读历史必须早于写入本轮，否则用户的话在 prompt 里出现两次。"""
    session = 'history-order'
    db.add_conversation(session, 'user', '第一句')
    db.add_conversation(session, 'assistant', '第一句的回复')

    seen = {}
    agent.call_ai_text = lambda prompt, *a, **k: seen.setdefault('prompt', prompt) or '好的'
    agent.chat('第二句', session_id=session)

    prompt = seen['prompt']
    assert '第一句' in prompt, '应带上此前的历史'
    # 重复会被模型当成强调
    assert prompt.count('第二句') == 1, f'本轮内容不应在 prompt 里出现两次：\n{prompt}'
    print('  ✓ 先读历史再写本轮，本轮不重复')


def test_revise_computes_diff_without_persisting():
    """revise 返回 diff 但不落库。"""
    _seed_dinner()
    _stub_model({'plan': {
        'dishes': [
            {'name': '冬瓜排骨汤', 'ingredients': [{'name': '冬瓜', 'amount': '300g'}],
             'cooking_time': '40分钟', 'difficulty': '简单', 'steps': ['炖']},
            BASE_PLAN['dishes'][1],
        ],
        'total_time': '50分钟', 'note': '换了',
    }})

    client = app.test_client()
    body = client.post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '红烧肉换清淡的'
    }).get_json()

    assert body['success'] is True, body
    assert body['before'] == ['红烧肉', '清炒上海青']
    assert body['after'] == ['冬瓜排骨汤', '清炒上海青']
    assert body['added'] == ['冬瓜排骨汤']
    assert body['removed'] == ['红烧肉']
    # 不落库是这个接口的核心契约：用户要先看 diff 再决定
    assert _latest_dinner_dishes() == ['红烧肉', '清炒上海青'], 'revise 不该写库'
    print('  ✓ revise 给出 diff 且不落库')


def test_diff_ignores_reordering():
    """只调换顺序不算改动。"""
    _seed_dinner()
    reordered = {'dishes': list(reversed(BASE_PLAN['dishes'])), 'total_time': '50分钟'}
    _stub_model({'plan': reordered})

    body = app.test_client().post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '顺序换一下'
    }).get_json()
    # 逐位比会把整份都标成变化，用户看到"改了两道"其实一道都没换
    assert body['added'] == [] and body['removed'] == [], body
    print('  ✓ 顺序变化不计入 diff')


def test_revise_rejects_non_menu_replies():
    """模型返回解释文字或报错时拒绝，不把它当菜单。"""
    _seed_dinner()
    client = app.test_client()

    _stub_model({'reply': '我建议你换成清淡的'})
    body = client.post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '改一下'
    }).get_json()
    assert body['success'] is False, body

    _stub_model({'error': 'timeout'})
    assert client.post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '改一下'
    }).get_json()['success'] is False

    # 结构不合规的方案同样拒绝
    for bad in ({'plan': {'dishes': []}},
                {'plan': {'dishes': [{'name': '  '}]}},
                {'plan': {'dishes': [{'name': f'菜{i}'} for i in range(agent.REVISE_MAX_DISHES + 1)]}}):
        _stub_model(bad)
        assert client.post('/api/meal-plan/revise', json={
            'meal_type': '晚餐', 'instruction': '改一下'
        }).get_json()['success'] is False, bad
    print('  ✓ 非菜单回复与不合规结构被拒绝')


def test_revise_blocks_allergens_even_when_user_asks():
    """用户明确要求加过敏源、模型照做时，必须拦下。"""
    _seed_dinner()
    db.replace_members([{'name': '娃', 'allergies': ['花生']}])
    _stub_model({'plan': {'dishes': [
        {'name': '花生米拌黄瓜', 'ingredients': [{'name': '花生米', 'amount': '50g'}],
         'cooking_time': '5分钟', 'difficulty': '简单', 'steps': ['拌']},
    ]}})

    body = app.test_client().post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '加个花生米'
    }).get_json()
    assert body['success'] is False, body
    assert body['reason'] == 'allergen_blocked'
    assert body['violations'], '要指出是哪道菜命中了哪个过敏源'
    assert _latest_dinner_dishes() == ['红烧肉', '清炒上海青']
    db.replace_members([])
    print('  ✓ 改单里的过敏源被拦下')


def test_revise_requires_existing_plan_and_sane_instruction():
    """没有该餐菜单、指令缺失或过长时给出可区分的失败。"""
    _seed_dinner()
    client = app.test_client()

    body = client.post('/api/meal-plan/revise', json={
        'meal_type': '早餐', 'instruction': '换清淡的'
    }).get_json()
    # 「还没有这一餐」是业务态而不是错误：客户端要引导去生成，不是显示报错
    assert body['success'] is False and body['reason'] == 'no_plan', body

    assert client.post('/api/meal-plan/revise', json={'meal_type': '晚餐'}).status_code == 400
    # 上界防的是把整段聊天记录塞进来 —— 那会挤掉 prompt 里的库存与画像，
    # 而挤掉画像意味着过敏约束也一起没了
    assert client.post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '长' * 201
    }).status_code == 400
    print('  ✓ 缺菜单/缺指令/指令过长各自可区分')


def test_apply_persists_and_validates_independently():
    """apply 落库，并独立做结构与过敏校验。"""
    _seed_dinner()
    client = app.test_client()

    # 表单式删菜：App 从现有方案里筛掉一道后提交
    kept = {'dishes': [BASE_PLAN['dishes'][1]], 'total_time': '10分钟'}
    body = client.post('/api/meal-plan/apply', json={
        'meal_type': '晚餐', 'plan': kept
    }).get_json()
    assert body['success'] is True, body
    assert body['dishes'] == ['清炒上海青']
    assert _latest_dinner_dishes() == ['清炒上海青'], '应插入新记录并成为最新一份'

    # 人数沿用该餐既有值，而不是默认 3 —— 改一道菜不该顺手把人数改掉
    latest = next(r for r in db.get_pending_plans()
                  if meals.meal_key(r['meal_type']) == 'dinner')
    assert latest['people_count'] == 3
    # 这是一份已确认的菜单，不是两个待选方案；塞一份假的 B 会让菜谱页出现
    # 用户没见过的备选
    assert latest['plan_b'] is None

    # 结构校验（客户端可能直接调这个接口）
    for bad in ({'dishes': []}, {'dishes': 'x'}, {'dishes': [{'name': '  '}]},
                {'dishes': [{'name': f'菜{i}'} for i in range(agent.REVISE_MAX_DISHES + 1)]}):
        resp = client.post('/api/meal-plan/apply', json={'meal_type': '晚餐', 'plan': bad})
        assert resp.status_code == 400, f'{bad} 应被拒：{resp.status_code}'

    assert client.post('/api/meal-plan/apply', json={'meal_type': '晚餐'}).status_code == 400

    # 过敏必须在这里也查一次：表单式微调不经过 revise，客户端也能绕过它
    db.replace_members([{'name': '娃', 'allergies': ['花生']}])
    resp = client.post('/api/meal-plan/apply', json={
        'meal_type': '晚餐', 'plan': {'dishes': [{'name': '花生酥'}]}
    })
    assert resp.status_code == 400
    assert resp.get_json()['reason'] == 'allergen_blocked'
    db.replace_members([])
    print('  ✓ apply 落库并独立校验结构与过敏')


def test_apply_validates_people_count():
    """people_count 非法时拒绝，而不是静默写一个怪值。"""
    _seed_dinner()
    client = app.test_client()
    kept = {'dishes': [BASE_PLAN['dishes'][1]]}
    for bad in ('abc', 0, -3, 21):
        resp = client.post('/api/meal-plan/apply', json={
            'meal_type': '晚餐', 'plan': kept, 'people_count': bad
        })
        assert resp.status_code == 400, f'people_count={bad!r} 应被拒'
    print('  ✓ people_count 校验')


def test_apply_rejects_stale_base():
    """revise 与 apply 之间这一餐被别处改过时，确认必须失败而不是静默覆盖。

    这是最容易在真机上撞到、又最难察觉的一条：用户看着 diff 想了半分钟，期间后端的餐前
    定时推荐（scheduler）刚好为这一餐生成了新的一整套。没有基准令牌时，「确认」会把基于
    旧方案改出来的那份写成最新一份，新生成的整套被埋掉 —— 两边都提示成功。
    """
    _seed_dinner()
    client = app.test_client()
    revised_plan = {'dishes': [
        {'name': '冬瓜排骨汤', 'ingredients': [{'name': '冬瓜', 'amount': '300g'}],
         'cooking_time': '40分钟', 'difficulty': '简单', 'steps': ['炖']},
    ], 'total_time': '40分钟'}
    _stub_model({'plan': revised_plan})

    first = client.post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '换成汤'
    }).get_json()
    assert first['success'] is True, first
    base_id = first['base_plan_id']
    assert isinstance(base_id, int) and base_id > 0, f'revise 必须交出基准 id：{first}'

    # 期间别处写入了一份（定时推荐 / 另一台手机 / 他自己刚删了一道菜）
    db.add_meal_plan('晚餐', '18:30', 3, {'dishes': [{'name': '番茄鸡蛋面'}]}, None)

    resp = client.post('/api/meal-plan/apply', json={
        'meal_type': '晚餐', 'plan': first['plan'], 'base_plan_id': base_id
    })
    # 409 而不是 400：重新 revise 一次就能继续，与"这份数据本身不能用"要分开引导
    assert resp.status_code == 409, resp.get_data(as_text=True)[:200]
    assert resp.get_json()['reason'] == 'stale_base'
    assert _latest_dinner_dishes() == ['番茄鸡蛋面'], '不能盖掉期间写入的那一份'

    # 重新 revise 拿到新基准后即可落库
    second = client.post('/api/meal-plan/revise', json={
        'meal_type': '晚餐', 'instruction': '换成汤'
    }).get_json()
    assert second['base_plan_id'] != base_id
    ok = client.post('/api/meal-plan/apply', json={
        'meal_type': '晚餐', 'plan': second['plan'], 'base_plan_id': second['base_plan_id']
    })
    assert ok.status_code == 200, ok.get_data(as_text=True)[:200]
    assert _latest_dinner_dishes() == ['冬瓜排骨汤']

    # 不带令牌仍然放行（老客户端、脚本），退化成无条件覆盖
    assert client.post('/api/meal-plan/apply', json={
        'meal_type': '晚餐', 'plan': {'dishes': [{'name': '青菜面'}]}
    }).status_code == 200
    # 令牌不是整数时按参数错误拒绝，别当成"没传"
    assert client.post('/api/meal-plan/apply', json={
        'meal_type': '晚餐', 'plan': {'dishes': [{'name': '青菜面'}]}, 'base_plan_id': 'abc'
    }).status_code == 400
    print('  ✓ 基准过期回 409 且不覆盖，重新 revise 后可落库')


def test_apply_base_check_is_atomic():
    """两个请求同时基于同一版提交时，只能有一个成功。"""
    base_id = _seed_dinner()
    start = threading.Barrier(2)
    read_done = threading.Barrier(2)
    original_latest = agent._latest_pending_for
    results = []

    # 把两个线程钉在「都已读到旧基准、都还没写」的位置，稳定复现 TOCTOU。
    # apply 不用这个分离查询时，两个事务会由 SQLite 串行化。
    def synchronized_latest(key):
        value = original_latest(key)
        read_done.wait(timeout=5)
        return value

    def submit(name):
        start.wait(timeout=5)
        results.append(agent.apply_revised_plan(
            '晚餐', {'dishes': [{'name': name}]}, 3, base_plan_id=base_id
        ))

    agent._latest_pending_for = synchronized_latest
    try:
        threads = [
            threading.Thread(target=submit, args=('并发菜单A',)),
            threading.Thread(target=submit, args=('并发菜单B',)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads), '并发测试线程不应卡住'
    finally:
        agent._latest_pending_for = original_latest

    assert len(results) == 2, results
    successes = [result for result in results if result.get('success')]
    stale = [result for result in results if result.get('reason') == 'stale_base']
    assert len(successes) == 1, f'同一基准只能成功一次：{results}'
    assert len(stale) == 1, f'另一次必须识别为 stale_base：{results}'
    print('  ✓ 同一基准的并发提交只有一个成功')


def test_remove_dish_paths():
    """删菜的三条路径各自的结果，以及不得污染库里原来那条记录。"""
    original_id = _seed_dinner()
    client = app.test_client()

    # 1) 这一餐里没有这道菜：失败且一个字节都不该写
    body = client.post('/api/meal-plan/remove-dish', json={
        'meal_type': '晚餐', 'dish_name': '佛跳墙'
    }).get_json()
    assert body['success'] is False and '佛跳墙' in body['error'], body
    assert _latest_dinner_dishes() == ['红烧肉', '清炒上海青'], '失败不该动库'
    assert client.post('/api/meal-plan/remove-dish',
                       json={'meal_type': '晚餐'}).status_code == 400

    # 2) 正常删除
    body = client.post('/api/meal-plan/remove-dish', json={
        'meal_type': '晚餐', 'dish_name': '红烧肉'
    }).get_json()
    assert body['success'] is True, body
    assert body['dishes'] == ['清炒上海青']
    assert _latest_dinner_dishes() == ['清炒上海青']

    # 落库是**插入新记录**：原来那条必须原样保留。agent.remove_dish 里 `dict(base)` 是
    # 浅拷贝，只有"换掉 dishes 列表引用而不是原地改"才成立 —— 一旦改成 base['dishes'].remove()，
    # 已经下发到设备上的那一版就在库里被悄悄改了，之后再也无法解释"设备上显示的是哪一份"。
    old = next(r for r in db.get_pending_plans() if r['id'] == original_id)
    assert agent.dish_names_of(old['plan_a']) == ['红烧肉', '清炒上海青'], \
        f'原记录被污染了：{agent.dish_names_of(old["plan_a"])}'

    # 3) 删到一道不剩：拒绝。用户要的是"换一道"或"这一餐不做了"，不是一张空白画面
    body = client.post('/api/meal-plan/remove-dish', json={
        'meal_type': '晚餐', 'dish_name': '清炒上海青'
    }).get_json()
    assert body['success'] is False and body['reason'] == 'would_be_empty', body
    assert _latest_dinner_dishes() == ['清炒上海青'], '拒绝后不该动库'

    # 4) 这一餐根本没有菜单
    body = client.post('/api/meal-plan/remove-dish', json={
        'meal_type': '早餐', 'dish_name': '小米粥'
    }).get_json()
    assert body['success'] is False and body['reason'] == 'no_plan', body
    print('  ✓ 删菜四条路径正确，且不污染原记录')


def main():
    print('=' * 60)
    print('改菜单链路测试')
    print('=' * 60)
    tests = [
        test_meal_type_never_out_of_range,
        test_history_is_formatted_and_bounded,
        test_chat_reads_history_before_writing_current_turn,
        test_revise_computes_diff_without_persisting,
        test_diff_ignores_reordering,
        test_revise_rejects_non_menu_replies,
        test_revise_blocks_allergens_even_when_user_asks,
        test_revise_requires_existing_plan_and_sane_instruction,
        test_apply_persists_and_validates_independently,
        test_apply_validates_people_count,
        test_apply_rejects_stale_base,
        test_apply_base_check_is_atomic,
        test_remove_dish_paths,
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
