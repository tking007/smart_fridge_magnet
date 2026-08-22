"""v0.19.0 新增兼容契约的定向测试。"""

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import agent, database as db, device_display  # noqa: E402
from src.inventory_units import normalize_inventory_amount  # noqa: E402
from src.recognition import standardize_item  # noqa: E402
from src.validators import validate_inventory_item  # noqa: E402

GENERATE_RECOMMENDATION = agent.generate_recommendation


def test_capacity_aliases_and_water_recognition():
    assert normalize_inventory_amount('牛奶', 1.5, 'L')['quantity'] == 1500
    assert normalize_inventory_amount('牛奶', 1.5, 'L')['unit'] == 'ml'
    assert validate_inventory_item({
        'name': '饮用水', 'category': '饮料', 'quantity': 400, 'unit': 'mL',
    })['unit'] == 'ml'

    # 重量单位换算后与调用方给的克重取较大值，两个数都不静默丢弃。
    beef = normalize_inventory_amount('牛肉', 2, 'kg', 300)
    assert (beef['quantity'], beef['unit'], beef['weight_grams']) == (0.0, '个', 2000.0)
    assert normalize_inventory_amount('牛肉', 0.2, 'kg', 300)['weight_grams'] == 300.0

    water = standardize_item({
        'name': '饮用纯净水', 'category': '饮料', 'quantity': 400,
        'unit': 'g', 'confidence': '高',
    })
    assert (water['quantity'], water['unit'], water['weight_grams']) == (400, 'ml', None)
    assert water['confidence'] == '中' and water['need_confirm'] is True

    juice = standardize_item({
        'name': '橙汁', 'category': '饮料', 'quantity': 400, 'unit': 'g',
    })
    assert juice['quantity'] == 0 and juice['weight_grams'] == 400

    labelled_drink = standardize_item({
        'name': '牛奶', 'category': '乳制品', 'quantity': 250, 'unit': 'ml',
        'weight_grams': 260,
    })
    assert labelled_drink['quantity'] == 250
    assert labelled_drink['unit'] == 'ml'
    assert labelled_drink['weight_grams'] is None


def test_device_status_headers_are_independent(monkeypatch, tmp_path):
    monkeypatch.setenv('SFM_DATABASE_PATH', str(tmp_path / 'device.db'))
    device_display.init_device_tables()
    device_display.record_device_status('dev-v019', headers={
        'X-Device-Battery': '88',
        'X-Device-Firmware': '1.2.3',
        'X-Device-Applied-Version': '17',
        'X-Device-Sleep-Mode': '1',
        'X-Device-Wake-Slots': '1085,425,695,695',
        'X-Device-Wake-Fallback-Minutes': '1440',
        'X-Device-Poll-Interval-Seconds': '86400',
    })
    status = device_display.get_device_status('dev-v019')
    assert status['wake_slots'] == [425, 695, 1085]
    assert status['applied_version'] == 17
    assert status['sleep_mode'] is True
    assert status['status_age_minutes'] == 0

    device_display.record_device_status('dev-v019', headers={
        'X-Device-Battery': 'broken',
        'X-Device-Wake-Slots': '425,invalid',
        'X-Device-Poll-Interval-Seconds': '1',
        'X-Device-Firmware': '1.2.4',
    })
    updated = device_display.get_device_status('dev-v019')
    assert updated['battery_level'] == 88
    assert updated['wake_slots'] == [425, 695, 1085]
    assert updated['poll_interval_seconds'] == 86400
    assert updated['firmware_version'] == '1.2.4'


def test_recipe_avoidance_and_meal_fit(monkeypatch):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(db, 'get_recent_meal_plans', lambda: [
        {
            'meal_type': 'lunch', 'plan_a': {'dishes': [{'name': '番茄炒蛋'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
        {
            'meal_type': 'dinner', 'plan_a': {'dishes': [{'name': '清炒西兰花'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': old,
        },
        {
            'meal_type': 'breakfast', 'plan_a': {'dishes': [{'name': '旧早餐'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': old,
        },
    ])
    avoidance = agent.recipe_avoidance('晚餐')
    assert avoidance['hard'] == ['番茄炒蛋', '清炒西兰花']
    assert '旧早餐' not in avoidance['soft']

    plans = {'plan_a': {'dishes': [
        {'name': '番茄炒蛋', 'ingredients': [{'name': '鸡蛋'}]},
        {'name': '红烧肉', 'ingredients': [{'name': '猪肉'}]},
    ]}}
    duplicates, mismatches = agent.find_recipe_constraint_violations(
        plans, '早餐', avoidance['hard']
    )
    assert duplicates == ['番茄炒蛋']
    assert mismatches == ['红烧肉']

    prompt = agent.build_enhanced_recipe_prompt(
        '- 鸡蛋 × 4个', '无', '早餐', 2,
        hard_avoid=avoidance['hard'], soft_avoid=['水煮蛋'],
    )
    assert '通常20分钟内' in prompt
    assert '本次禁止重复的菜名' in prompt and '番茄炒蛋' in prompt
    assert agent.normalize_dish_name('番茄炒蛋（快手版）') == '番茄炒蛋'


def test_hard_avoidance_keeps_only_latest_record_per_meal(monkeypatch):
    """当天反复重新生成不得把丢弃过的尝试无限累积进硬禁名单。"""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(db, 'get_recent_meal_plans', lambda: [
        {
            'meal_type': 'lunch', 'plan_a': {'dishes': [{'name': '第三次尝试'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
        {
            'meal_type': 'lunch', 'plan_a': {'dishes': [{'name': '第二次尝试'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
        {
            'meal_type': 'lunch', 'plan_a': {'dishes': [{'name': '第一次尝试'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
        {
            'meal_type': 'breakfast', 'plan_a': {'dishes': [{'name': '今日早餐'}]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
    ])
    avoidance = agent.recipe_avoidance('午餐')
    # 本餐次只取最近一条；同为当天的其它餐次各取最近一条。
    assert avoidance['hard'] == ['第三次尝试', '今日早餐']
    assert avoidance['soft'] == ['第二次尝试', '第一次尝试']


def test_regeneration_avoids_cross_meal_ingredients_and_previous_recipe(monkeypatch):
    """换个菜名不能绕过去重，当天其它餐次用过的核心食材也不能再用。"""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(db, 'get_recent_meal_plans', lambda: [
        {
            'meal_type': 'lunch',
            'plan_a': {'dishes': [{
                'name': '土豆炖牛肉',
                'ingredients': [{'name': '土豆'}, {'name': '牛肉'}],
            }]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
        {
            'meal_type': 'breakfast',
            'plan_a': {'dishes': [{
                'name': '水煮蛋', 'ingredients': [{'name': '鸡蛋'}, {'name': '盐'}],
            }]},
            'plan_b': None, 'selected_plan': None, 'created_at': now,
        },
    ])

    avoidance = agent.recipe_avoidance('午餐')
    assert avoidance['hard_ingredients'] == ['鸡蛋']
    assert avoidance['hard_signatures'] == [['土豆', '牛肉']]

    plans = {'plan_a': {'dishes': [
        {
            'name': '番茄炒蛋',
            'ingredients': [{'name': '番茄'}, {'name': '鸡蛋'}],
        },
        {
            'name': '家常土豆烧牛肉',
            'ingredients': [{'name': '土豆'}, {'name': '牛肉'}],
        },
        {
            'name': '清炒西兰花',
            'ingredients': [{'name': '西兰花'}, {'name': '蒜'}],
        },
    ]}}
    duplicates, mismatches = agent.find_recipe_constraint_violations(
        plans,
        '午餐',
        avoidance['hard'],
        avoidance['hard_ingredients'],
        avoidance['hard_signatures'],
    )
    assert duplicates == ['番茄炒蛋', '家常土豆烧牛肉']
    assert mismatches == []

    cleaned, dropped = agent.drop_invalid_dishes(
        plans,
        ['鸡蛋', '番茄', '土豆', '牛肉', '西兰花'],
        '午餐',
        avoidance['hard'],
        avoidance['hard_ingredients'],
        avoidance['hard_signatures'],
    )
    assert dropped == ['番茄炒蛋', '家常土豆烧牛肉']
    assert agent.dish_names_of(cleaned['plan_a']) == ['清炒西兰花']

    prompt = agent.build_enhanced_recipe_prompt(
        '- 鸡蛋 × 4个\n- 土豆 × 2个\n- 牛肉 × 500g',
        '无', '午餐', 2,
        hard_ingredients=avoidance['hard_ingredients'],
        hard_signatures=avoidance['hard_signatures'],
    )
    assert '当天其它餐次已经使用的核心食材' in prompt and '鸡蛋' in prompt
    assert '上一版当前餐次已经使用的食材组合' in prompt


def test_non_stream_repeat_uses_only_existing_constraint_retry(monkeypatch):
    repeated = {'plan_a': {'dishes': [{
        'name': '番茄炒蛋',
        'ingredients': [{'name': '番茄'}, {'name': '鸡蛋'}],
    }]}}
    replacement = {'plan_a': {'dishes': [{
        'name': '清炒西兰花', 'ingredients': [{'name': '西兰花'}],
    }]}}
    replies = iter((repeated, replacement))
    calls, persisted = [], []
    monkeypatch.setattr(agent, '_collect_recipe_inputs', lambda: {
        'empty': False,
        'inventory_summary': '- 鸡蛋 × 4个\n- 番茄 × 2个\n- 西兰花 × 1个',
        'profile_summary': '无',
        'expiring_names': [],
        'nutrition_data': None,
        'inventory_names': ['鸡蛋', '番茄', '西兰花'],
        'allergens': [],
    })
    monkeypatch.setattr(agent, 'recipe_avoidance', lambda _: {
        'hard': ['水煮蛋'], 'soft': [],
        'hard_ingredients': ['鸡蛋'], 'hard_signatures': [],
    })

    def model(*args, **kwargs):
        calls.append((args, kwargs))
        return next(replies)

    monkeypatch.setattr(agent, 'call_ai_text_with_json', model)
    monkeypatch.setattr(db, 'add_meal_plan', lambda **kwargs: persisted.append(kwargs) or 9)

    result = GENERATE_RECOMMENDATION('午餐', 2)

    assert result['action'] == 'recommend'
    assert len(calls) == 2, '跨餐重复应并入现有一次纠正，不得增加第三次模型请求'
    assert agent.dish_names_of(persisted[0]['plan_a']) == ['清炒西兰花']


def test_ab_plans_may_share_a_dish(monkeypatch):
    """A/B 是同一餐的两个候选，共用主菜不算重复；单套内重复才算。"""
    shared = {'name': '番茄炒蛋', 'ingredients': [{'name': '鸡蛋'}, {'name': '番茄'}]}
    plans = {
        'plan_a': {'dishes': [shared, {'name': '清炒青菜', 'ingredients': [{'name': '青菜'}]}]},
        'plan_b': {'dishes': [shared, {'name': '蒸蛋', 'ingredients': [{'name': '鸡蛋'}]}]},
    }
    inventory = ['鸡蛋', '番茄', '青菜']

    assert agent.find_recipe_constraint_violations(plans, '午餐', []) == ([], [])
    cleaned, dropped = agent.drop_invalid_dishes(plans, inventory, '午餐', [])
    assert dropped == []
    assert [d['name'] for d in cleaned['plan_b']['dishes']] == ['番茄炒蛋', '蒸蛋']

    within_plan = {'plan_a': {'dishes': [shared, dict(shared)]}}
    duplicates, _ = agent.find_recipe_constraint_violations(within_plan, '午餐', [])
    assert duplicates == ['番茄炒蛋']
    _, dropped = agent.drop_invalid_dishes(within_plan, inventory, '午餐', [])
    assert dropped == ['番茄炒蛋']

    # hard_avoid 仍然跨两套生效。
    duplicates, _ = agent.find_recipe_constraint_violations(plans, '午餐', ['番茄炒蛋'])
    assert duplicates == ['番茄炒蛋', '番茄炒蛋']


def test_invalid_allergen_retry_never_falls_back_to_unsafe_plan(monkeypatch):
    unsafe = {'plan_a': {'dishes': [{
        'name': '花生拌黄瓜',
        'ingredients': [{'name': '花生'}, {'name': '黄瓜'}],
    }]}}
    replies = iter((unsafe, {'bad': 'shape'}))
    monkeypatch.setattr(agent, '_collect_recipe_inputs', lambda: {
        'empty': False,
        'inventory_summary': '- 花生 × 1袋\n- 黄瓜 × 1根',
        'profile_summary': '过敏禁用花生',
        'expiring_names': [],
        'nutrition_data': {},
        'inventory_names': ['花生', '黄瓜'],
        'allergens': ['花生'],
    })
    monkeypatch.setattr(agent, 'recipe_avoidance', lambda _: {'hard': [], 'soft': []})
    monkeypatch.setattr(agent, 'call_ai_text_with_json', lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(
        db, 'add_meal_plan',
        lambda **kwargs: (_ for _ in ()).throw(AssertionError('过敏方案不得落库')),
    )

    result = GENERATE_RECOMMENDATION('午餐', 2)
    assert result['action'] == 'recommend_allergen_blocked'
