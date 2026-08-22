#!/usr/bin/env python3
"""校验「推荐必须按冰箱库存来」这条链路：prompt 硬约束 + 生成后差集标注。

prompt 里那条「只能用库存里的食材」是软的，模型可以无视，所以必须有代码检查它的输出 ——
否则模型凭想象加一样香菇，链路上没有一处会发现，用户只能自己一道道菜去核对配料。

盯住的行为：
  1. prompt 里要有硬约束措辞与调料白名单。少了白名单，模型会被逼到「不放油盐」或者
     把油盐也算成缺料。
  2. 差集要认得出同名变体（库存「土豆」↔ 菜谱「土豆丝」）。
  3. 基础调料不算缺料。
  4. 调料白名单**不能**影响过敏拦截：「花生油」在白名单里，但对花生过敏时仍须被拦。
     这两个检查目的不同，共用一份名单会把安全问题变成便利问题。
  5. 缺料按方案分别统计 —— 「哪套不用出门买」往往正是选方案的依据。
  6. 畸形结构不抛异常。

用法：python tests/test_inventory_constraint.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-invcon-test-')
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

_REAL_GENERATE_RECOMMENDATION = agent.generate_recommendation

db.init_db()

# 两套方案：A 缺一样香菇；B 缺一样木耳（蒜是基础增香料，不算缺料）。
PLANS = {
    'plan_a': {
        'dishes': [
            {'name': '土豆丝炒肉', 'ingredients': [
                {'name': '土豆丝'}, {'name': '猪肉'}, {'name': '生抽'}, {'name': '食用油'},
            ]},
            {'name': '香菇青菜', 'ingredients': [
                {'name': '香菇'}, {'name': '上海青'}, {'name': '盐'},
            ]},
        ],
        'total_time': '30分钟',
    },
    'plan_b': {
        'dishes': [
            {'name': '木耳炒青菜', 'ingredients': [
                {'name': '上海青'}, {'name': '木耳'}, {'name': '蒜'},
            ]},
        ],
        'total_time': '10分钟',
    },
}
INVENTORY = ['土豆', '猪肉', '上海青']


def test_prompt_has_hard_constraint_and_pantry():
    """prompt 要给出硬约束与调料白名单。"""
    prompt = agent.build_enhanced_recipe_prompt(
        inventory_summary='- 土豆 × 2个\n- 猪肉 × 300g',
        profile_summary='爸爸，40岁',
        meal_type='晚餐',
        people_count=3,
    )
    assert '主料只能从这里选' in prompt, '库存标题要把约束说明白'
    assert '硬约束' in prompt, '推荐原则里要有硬约束条目'
    # 白名单缺失的后果不是"少一句话"：模型要么做不放油盐的菜，要么把油盐算成缺料，
    # 让每道菜都顶着一串假的缺料。
    assert '基础调料' in prompt and '酱油' in prompt, '必须列出可直接使用的基础调料'
    print('  ✓ prompt 含硬约束与调料白名单')


def test_missing_detects_variants_and_skips_staples():
    """同名变体算命中库存；基础调料不算缺料。"""
    missing = agent.find_missing_ingredients(PLANS, INVENTORY)
    assert missing['plan_a'] == ['香菇'], missing
    assert missing['plan_b'] == ['木耳'], missing
    # 土豆丝 ← 土豆（库存名是菜谱名的子串）
    assert '土豆丝' not in missing['plan_a'], '库存「土豆」应认下菜谱「土豆丝」'
    # 生抽 / 食用油 / 盐 都是调料
    for staple in ('生抽', '食用油', '盐'):
        assert staple not in missing['plan_a'], f'{staple} 是基础调料，不该算缺料'
    # 蒜是基础增香料，等同油盐，不该算缺料
    assert '蒜' not in missing['plan_b'], '葱姜蒜属基础调料，不该算缺料'
    print('  ✓ 同名变体命中库存、基础调料不计入缺料')


def test_missing_is_per_plan():
    """缺料按方案分别统计，不汇总成一份。"""
    missing = agent.find_missing_ingredients(PLANS, INVENTORY)
    assert set(missing.keys()) == {'plan_a', 'plan_b'}, missing
    # 合成一份就没法据此挑方案了，而"哪套不用出门买"往往正是选择依据
    assert missing['plan_a'] != missing['plan_b']
    print('  ✓ 缺料按方案分别统计')


def test_empty_inventory_marks_everything():
    """库存为空时，除调料外全部算缺料。"""
    missing = agent.find_missing_ingredients(PLANS, [])
    assert set(missing['plan_a']) == {'土豆丝', '猪肉', '香菇', '上海青'}, missing
    assert missing['plan_b'] == ['上海青', '木耳'], missing
    print('  ✓ 空库存时非调料全部标为缺料')


def test_pantry_whitelist_does_not_weaken_allergen_check():
    """调料白名单不能削弱过敏拦截。

    「花生油」既在调料白名单里（所以不算缺料），又必须被过敏检查拦下。两个检查的目的
    不同：一个回答"要不要去买"，一个回答"能不能吃"。共用一份名单会把安全问题降级成
    便利问题。
    """
    plans = {'plan_a': {'dishes': [
        {'name': '凉拌木耳', 'ingredients': [{'name': '木耳'}, {'name': '花生油'}]},
    ]}}
    # 白名单侧：花生油不算缺料
    assert agent.find_missing_ingredients(plans, ['木耳'])['plan_a'] == [], \
        '花生油属基础调料，不该出现在采购清单里'
    # 过敏侧：花生油必须被拦
    violations = agent.find_allergen_violations(plans, ['花生'])
    assert violations, '对花生过敏时「花生油」必须被拦下，白名单不该让它通行'
    print('  ✓ 白名单不影响过敏拦截')


def test_pantry_whitelist_does_not_swallow_real_food():
    """名字里带「油/盐/糖/水」的真食材不能被当成调料剔掉。

    白名单里有「油」「盐」「糖」「水」这类单字项，若用双向子串匹配，**油菜、水豆腐、盐水鸭、
    矿泉水、花生** 这些家常食材会被判成「家里一定有」并静默从采购清单里消失，
    表现是「清单看起来很干净」，比报错难发现得多。
    这里两个方向都钉住：真食材必须留在清单里，真调料必须被剔除。
    """
    real_food = [
        '油菜', '油麦菜', '小油菜', '水豆腐', '水发木耳', '盐水鸭', '油条', '油豆皮',
        '糖蒜', '水蜜桃', '椰子水', '矿泉水', '花生', '花生米', '糖醋排骨', '苏打水',
    ]
    plans = {'plan_a': {'dishes': [
        {'name': '大杂烩', 'ingredients': [{'name': n} for n in real_food]},
    ]}}
    missing = agent.find_missing_ingredients(plans, [])['plan_a']
    swallowed = [n for n in real_food if n not in missing]
    assert not swallowed, f'这些是真食材，被当成调料剔掉了：{swallowed}'

    staples = [
        '油', '食用油', '盐', '少许盐', '适量清水', '生抽', '生抽酱油', '一点老抽',
        '蚝油', '花生油', '橄榄油', '白糖', '冰糖', '淀粉', '玉米淀粉', '面粉',
        '五香粉', '黑胡椒', '高汤', '鸡精', '八角', '孜然', '料酒', '番茄酱', '水',
    ]
    plans_staple = {'plan_a': {'dishes': [
        {'name': '调料堆', 'ingredients': [{'name': n} for n in staples]},
    ]}}
    leaked = agent.find_missing_ingredients(plans_staple, [])['plan_a']
    assert leaked == [], f'这些是基础调料，不该进采购清单：{leaked}'

    # prompt 里列的示例必须都能被判定为调料，否则模型照 prompt 用了、这边算成缺料。
    # agent.py 模块导入时也有断言守着，这里再从行为侧确认一次。
    listed = agent.find_missing_ingredients({'plan_a': {'dishes': [
        {'name': 'x', 'ingredients': [{'name': n} for n in agent.PANTRY_PROMPT_SAMPLE]},
    ]}}, [])['plan_a']
    assert listed == [], f'prompt 里列了却被算成缺料：{listed}'
    print('  ✓ 真食材留在清单里、真调料被剔除，prompt 与判定一致')


def test_aromatics_and_starch_are_pantry():
    """葱姜蒜与水淀粉属基础增香/勾芡料，等同油盐，不计入采购清单。

    真机日志里出现过整份菜单只因差「蒜 / 蒜末 / 水淀粉」被判缺料而整套放弃、不落库、
    不下发。中餐几乎每道菜都用葱姜蒜，把它们当成必须拍进冰箱的主料并不符合实际，
    结果是库存稍少就永远推荐失败。这里钉住两个方向：调料算调料、真食材仍算真食材。
    """
    plans = {'plan_a': {'dishes': [
        {'name': '蒜蓉时蔬', 'ingredients': [
            {'name': '上海青'}, {'name': '蒜'}, {'name': '蒜末'}, {'name': '姜片'},
            {'name': '葱花'}, {'name': '水淀粉'},
        ]},
    ]}}
    missing = agent.find_missing_ingredients(plans, ['上海青'])['plan_a']
    assert missing == [], f'葱姜蒜与水淀粉属基础调料，不应算缺料：{missing}'

    # 「洋葱 / 蒜苔 / 大葱」是真食材，不能被葱蒜的子串误伤成"家里一定有"。
    real = {'plan_a': {'dishes': [{'name': 'x', 'ingredients': [
        {'name': '洋葱'}, {'name': '蒜苔'}, {'name': '大葱'},
    ]}]}}
    assert set(agent.find_missing_ingredients(real, [])['plan_a']) == {'洋葱', '蒜苔', '大葱'}, \
        '洋葱/蒜苔/大葱是真食材，空库存时必须算缺料'
    print('  ✓ 葱姜蒜/水淀粉算调料，洋葱/蒜苔/大葱仍算真食材')


def test_malformed_plans_do_not_raise():
    """畸形结构不抛异常 —— 校验器自己崩掉等于这道检查静默失效。"""
    cases = [
        {},
        [],
        [{'name': '模型错误地返回了顶层数组'}],
        {'plan_a': None},
        {'plan_a': 'not a dict'},
        {'plan_a': {'dishes': None}},
        {'plan_a': {'dishes': ['字符串菜', None, 42]}},
        {'plan_a': {'dishes': [{'ingredients': 'not a list'}]}},
        {'plan_a': {'dishes': [{'ingredients': [None, {'name': None}, '香菇']}]}},
    ]
    for case in cases:
        result = agent.find_missing_ingredients(case, INVENTORY)
        assert isinstance(result, dict), f'必须返回 dict：{case}'
    # 字符串形式的配料也要参与判断
    assert agent.find_missing_ingredients(
        {'plan_a': {'dishes': [{'ingredients': ['香菇']}]}}, INVENTORY
    )['plan_a'] == ['香菇']
    print('  ✓ 畸形结构不抛异常且字符串配料仍被检查')


def test_generate_recommendation_rejects_top_level_array(monkeypatch):
    """模型返回合法 JSON 数组时必须转成业务失败，不能 500 或落库。"""
    runtime = _REAL_GENERATE_RECOMMENDATION.__globals__
    runtime_db = runtime['db']
    monkeypatch.setattr(runtime_db, 'get_inventory_summary', lambda: '- 土豆 × 1个')
    monkeypatch.setattr(runtime_db, 'get_profile_summary', lambda: '无家庭画像')
    monkeypatch.setattr(runtime_db, 'get_expiring_items', lambda **kwargs: [])
    monkeypatch.setattr(runtime_db, 'get_inventory', lambda **kwargs: {
        'items': [{'name': '土豆'}], 'total': 1,
    })
    monkeypatch.setattr(runtime_db, 'collect_allergens', lambda: [])
    monkeypatch.setitem(runtime, 'get_nutrition_for_inventory', lambda: None)
    monkeypatch.setitem(
        runtime, 'call_ai_text_with_json',
        lambda *args, **kwargs: [{'name': '模型错误地返回了顶层数组'}],
    )
    persisted = []
    monkeypatch.setattr(runtime_db, 'add_meal_plan', lambda **kwargs: persisted.append(kwargs))

    result = _REAL_GENERATE_RECOMMENDATION('晚餐', 2)

    assert result['action'] == 'recommend_error'
    assert persisted == []


def _stub_generation_dependencies(monkeypatch, model_results, allergens=None):
    runtime = _REAL_GENERATE_RECOMMENDATION.__globals__
    runtime_db = runtime['db']
    monkeypatch.setattr(runtime_db, 'get_inventory_summary', lambda: '- 土豆 × 2个')
    monkeypatch.setattr(runtime_db, 'get_profile_summary', lambda: '无家庭画像')
    monkeypatch.setattr(runtime_db, 'get_expiring_items', lambda **kwargs: [])
    monkeypatch.setattr(runtime_db, 'get_inventory', lambda **kwargs: {
        'items': [{'name': '土豆'}], 'total': 1,
    })
    monkeypatch.setattr(runtime_db, 'collect_allergens', lambda: list(allergens or []))
    monkeypatch.setitem(runtime, 'get_nutrition_for_inventory', lambda: None)
    calls = []

    def model(*args, **kwargs):
        calls.append((args, kwargs))
        return model_results[len(calls) - 1]

    monkeypatch.setitem(runtime, 'call_ai_text_with_json', model)
    persisted = []
    monkeypatch.setattr(
        runtime_db,
        'add_meal_plan',
        lambda **kwargs: persisted.append(kwargs) or 42,
    )
    return calls, persisted


def _plan_with(ingredient):
    return _plan_with_ingredients(ingredient)


def _plan_with_ingredients(*ingredients):
    return {
        'plan_a': {'dishes': [{
            'name': '家常做法',
            'ingredients': [{'name': ingredient} for ingredient in ingredients],
        }]},
        'plan_b': {'dishes': [{
            'name': '快手做法',
            'ingredients': [{'name': ingredient} for ingredient in ingredients],
        }]},
    }


def test_generate_recommendation_retries_inventory_violation(monkeypatch):
    calls, persisted = _stub_generation_dependencies(
        monkeypatch,
        [_plan_with('香菇'), _plan_with('土豆')],
    )

    result = _REAL_GENERATE_RECOMMENDATION('晚餐', 2)

    assert result['action'] == 'recommend'
    assert len(calls) == 2
    assert persisted[0]['plan_a'] == _plan_with('土豆')['plan_a']
    assert result['data']['missing_ingredients'] == {'plan_a': [], 'plan_b': []}


def test_generate_recommendation_does_not_persist_second_inventory_violation(monkeypatch):
    calls, persisted = _stub_generation_dependencies(
        monkeypatch,
        [_plan_with('香菇'), _plan_with('牛肉')],
    )

    result = _REAL_GENERATE_RECOMMENDATION('晚餐', 2)

    assert result['action'] == 'recommend_inventory_blocked'
    assert len(calls) == 2
    assert persisted == []


def test_plan_shape_follows_inventory_scale():
    """库存少时只要一套方案，库存足时才要 A/B 两套。

    要求两套**不同**方案且主料都落在库存内，库存越少越不可能同时成立 —— 真机日志里
    4 项库存连续三餐都被判 `recommend_inventory_blocked` 正是这个原因。
    """
    scarce = agent.plan_shape_hint(3)
    assert 'plan_a' in scarce and 'plan_b' in scarce, '少库存要明确只要 plan_a'
    assert '只生成一套方案' in scarce
    assert '3 项' in scarce, '要把真实库存数说给模型，而不是抽象地说"较少"'

    # 阈值边界：低于 6 收敛成一套，达到 6 才要两套。
    assert '只生成一套方案' in agent.plan_shape_hint(5)
    for count in (6, 15):
        rich = agent.plan_shape_hint(count)
        assert 'A/B 两套方案' in rich, f'库存 {count} 项应要求两套方案'
        assert '只生成一套' not in rich

    print('  ✓ 3/5 项→单套，6/15 项→A/B 两套')


def test_prompt_carries_plan_shape():
    """方案数要求必须真的进 prompt —— 只加函数不接线是这类改动最常见的空转。"""
    scarce = agent.build_enhanced_recipe_prompt(
        inventory_summary='- 土豆 × 2个', profile_summary='无',
        meal_type='早餐', people_count=3, inventory_count=4,
    )
    assert '只生成一套方案' in scarce, 'prompt 未带上少库存的单套要求'

    rich = agent.build_enhanced_recipe_prompt(
        inventory_summary='- 土豆 × 2个', profile_summary='无',
        meal_type='早餐', people_count=3, inventory_count=12,
    )
    assert 'A/B 两套方案' in rich
    print('  ✓ 方案数要求已进 prompt')


def test_drop_invalid_dishes_keeps_compliant_ones():
    """剔掉越界的菜、保留合规的菜，而不是整套丢弃。"""
    plans = {'plan_a': {'dishes': [
        {'name': '土豆丝', 'ingredients': [{'name': '土豆'}, {'name': '盐'}]},
        {'name': '香菇汤', 'ingredients': [{'name': '香菇'}]},
    ], 'total_time': '20分钟'}}

    cleaned, dropped = agent.drop_invalid_dishes(plans, ['土豆'])
    assert dropped == ['香菇汤']
    assert agent.dish_names_of(cleaned['plan_a']) == ['土豆丝']
    assert cleaned['plan_a']['total_time'] == '20分钟', '剔菜不应丢掉方案的其它字段'

    # 全部越界 → 无可落库内容，交由调用方判定真的无法成菜。
    assert agent.drop_invalid_dishes(plans, [])[0] == {}

    # plan_a 全越界而 plan_b 有合规菜时，提为 plan_a：下游都把 plan_a 当主方案。
    two = {
        'plan_a': {'dishes': [{'name': '牛肉面', 'ingredients': [{'name': '牛肉'}]}]},
        'plan_b': {'dishes': [{'name': '拍黄瓜', 'ingredients': [{'name': '黄瓜'}]}]},
    }
    promoted, _ = agent.drop_invalid_dishes(two, ['黄瓜'])
    assert list(promoted) == ['plan_a']
    assert agent.dish_names_of(promoted['plan_a']) == ['拍黄瓜']
    print('  ✓ 剔越界菜、保留合规菜、plan_b 独活时提为 plan_a')


def test_generate_recommendation_degrades_instead_of_discarding(monkeypatch):
    """二次纠正后仍有一道越界时，剔掉它并落库，而不是整套丢弃。

    用户要的是「今天能照着做的菜」。整套丢弃会让库存偏少的家庭永远收不到菜单，
    而模型越界通常只涉及其中一两道。
    """
    partly_bad = {'plan_a': {'dishes': [
        {'name': '土豆丝', 'ingredients': [{'name': '土豆'}, {'name': '蒜末'}]},
        {'name': '香菇汤', 'ingredients': [{'name': '香菇'}]},
    ]}}
    calls, persisted = _stub_generation_dependencies(
        monkeypatch, [partly_bad, partly_bad],
    )

    result = _REAL_GENERATE_RECOMMENDATION('晚餐', 2)

    assert result['action'] == 'recommend', f"应降级落库而非拦截：{result['action']}"
    assert len(calls) == 2, '仍然只纠正一次'
    assert len(persisted) == 1, '剔除后必须落库'
    assert agent.dish_names_of(persisted[0]['plan_a']) == ['土豆丝']
    assert result['data']['dropped_dishes'] == ['香菇汤']
    assert '香菇汤' in result['reply'], '剔掉了哪道菜必须告诉用户'
    assert not any(result['data']['missing_ingredients'].values()), \
        '落库的菜单不应再有缺料'
    print('  ✓ 部分越界→剔除后落库并告知用户')


def test_allergen_and_inventory_share_one_retry(monkeypatch):
    calls, persisted = _stub_generation_dependencies(
        monkeypatch,
        [_plan_with_ingredients('花生', '香菇'), _plan_with('香菇')],
        allergens=['花生'],
    )

    result = _REAL_GENERATE_RECOMMENDATION('晚餐', 2)

    assert result['action'] == 'recommend_inventory_blocked'
    assert len(calls) == 2, '过敏与库存必须合并纠正，不能连续调用三次模型'
    assert persisted == []


def test_reply_shows_shopping_list():
    """回复文本里要按方案给出采购清单，不缺料时也要明说。"""
    missing = agent.find_missing_ingredients(PLANS, INVENTORY)
    reply = agent.format_recommendation('晚餐', 3, PLANS, 1, [], missing)
    assert '需采购 1 样：香菇' in reply, reply
    assert '需采购 1 样：木耳' in reply, reply

    # 不缺料时给出正面结论。只在缺料时提示的话，"没有提示"既可能是不缺、也可能是
    # 压根没算过，两者看起来一样。
    reply_ok = agent.format_recommendation('晚餐', 3, PLANS, 1, [], {})
    assert '全部用现有食材' in reply_ok, reply_ok
    print('  ✓ 回复按方案给出采购清单，不缺料时也明说')


def main():
    print('=' * 60)
    print('库存约束测试')
    print('=' * 60)
    tests = [
        test_prompt_has_hard_constraint_and_pantry,
        test_missing_detects_variants_and_skips_staples,
        test_missing_is_per_plan,
        test_empty_inventory_marks_everything,
        test_pantry_whitelist_does_not_weaken_allergen_check,
        test_pantry_whitelist_does_not_swallow_real_food,
        test_aromatics_and_starch_are_pantry,
        test_malformed_plans_do_not_raise,
        test_reply_shows_shopping_list,
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
