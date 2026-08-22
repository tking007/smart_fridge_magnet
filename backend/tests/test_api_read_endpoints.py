#!/usr/bin/env python3
"""校验那几个「读取 / 配置」接口：菜谱只读、用餐时间、版式偏好与版式预览。

这批接口的共同点是**不该打模型、不该依赖库存**，而这一点极容易在后续改动中悄悄丢掉：
一旦有人为了「顺手补一份菜单」在只读路径上调了 chat()，接口仍然返回 200，只是每次请求慢
25 秒、还会让菜单每次都变。所以这里把 generate_recommendation 与 chat 都换成会抛异常的
假实现，用「接口仍然 200」来证明它没走模型那条路。

另外盯住两处容易退化的行为：
  1. 用餐时间校验必须是全有或全无。半套时间表会让某一餐的自动下发时机与 App 上显示的
     不一致，两边都不报错。
  2. 版式预览必须任何时候都能出图（用内置示例数据），否则冰箱空着就选不了版式；
     默认返回设备像素 792×528，natural=1 时竖版返回便于 App 阅读的 528×792。

用法：python tests/test_api_read_endpoints.py
"""

import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 环境隔离与 test_render_film_endpoint.py 一致：库和 .film 都落到临时目录
_tmp = tempfile.mkdtemp(prefix='sfm-readapi-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''          # 留空 → 本机开发放行，不必构造鉴权头
os.environ['SFM_HOST'] = '127.0.0.1'
# 别让调度器在测试进程里跑：容器里 SFM_ENABLE_SCHEDULER=true，导入 app 就会再起一份，
# 而餐前下发会真的向设备发布新版本。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from PIL import Image                     # noqa: E402

from src import agent                     # noqa: E402
from src import app as app_module          # noqa: E402
from src import database as db            # noqa: E402
from src import menu_render               # noqa: E402
from src.app import app                   # noqa: E402

DEVICE_ID = 'devprefs01'

FAKE_PLAN_A = {
    'dishes': [
        {'name': '番茄炒蛋', 'cooking_time': '15分钟', 'difficulty': '简单',
         'ingredients': [{'name': '番茄', 'amount': '2个'}, {'name': '鸡蛋', 'amount': '3个'}]},
    ]
}
FAKE_PLAN_B = {
    'dishes': [
        {'name': '蒜蓉上海青', 'cooking_time': '8分钟', 'difficulty': '简单',
         'ingredients': [{'name': '上海青', 'amount': '300g'}]},
    ]
}


def _forbid_model_calls():
    """把模型入口换成「一调用就炸」的假实现。

    只读接口若不小心走了模型，测试会因为异常被 Flask 兜成 500 而立刻失败；
    这比「断言响应时间」稳定得多，也不依赖网络。
    """
    def boom(*args, **kwargs):
        raise AssertionError('只读接口不应调用模型')

    agent.generate_recommendation = boom
    # app.py 是 `from .agent import chat` 导进来的，替换 agent.chat 不会影响它，
    # 必须替换 app 模块里那个名字本身。
    app_module.chat = boom


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


def check_recommend_latest(client, results):
    print('菜谱只读接口 /api/recommend/latest：')

    empty = client.get('/api/recommend/latest?meal_type=lunch')
    body = empty.get_json() or {}
    results.append(check(
        '库里没有方案时返回 404（App 据此展示「点击生成」而不是空白页）',
        empty.status_code == 404 and body.get('code') == 4004
        and body.get('success') is False,
        f'实际 {empty.status_code} {body}'))

    plan_id = db.add_meal_plan('lunch', '12:00', 3, FAKE_PLAN_A, FAKE_PLAN_B)

    got = client.get('/api/recommend/latest?meal_type=午餐')
    payload = got.get_json() or {}
    results.append(check(
        '有方案时返回 200（中文餐次同样能识别）',
        got.status_code == 200 and payload.get('success') is True,
        f'实际 {got.status_code} {payload}'))

    expected_fields = {
        'success', 'plan_id', 'meal_type', 'meal_label', 'people_count',
        'created_at', 'selected_plan', 'age_minutes', 'stale', 'plan_a', 'plan_b',
    }
    results.append(check(
        '字段齐全',
        expected_fields <= set(payload),
        f'缺少 {sorted(expected_fields - set(payload))}'))
    results.append(check(
        '餐次归一成英文键并附中文标签',
        payload.get('meal_type') == 'lunch' and payload.get('meal_label') == '午餐',
        f"实际 {payload.get('meal_type')} / {payload.get('meal_label')}"))
    results.append(check(
        'plan_id / people_count / 方案内容与落库的一致',
        payload.get('plan_id') == plan_id and payload.get('people_count') == 3
        and payload.get('plan_a') == FAKE_PLAN_A and payload.get('plan_b') == FAKE_PLAN_B,
        f"实际 plan_id={payload.get('plan_id')} people={payload.get('people_count')}"))

    # created_at 是 SQLite CURRENT_TIMESTAMP 写入的 UTC。用本地时间去比会在 +0800 下
    # 多算 8 小时，把刚生成的方案判成隔夜旧菜单 —— 这里正是在盯住那个坑。
    results.append(check(
        '刚落库的方案 age_minutes≈0 且不判为过期（UTC 与本地时间没有混用）',
        isinstance(payload.get('age_minutes'), int) and payload['age_minutes'] < 5
        and payload.get('stale') is False,
        f"实际 age_minutes={payload.get('age_minutes')} stale={payload.get('stale')}"))

    other_meal = client.get('/api/recommend/latest?meal_type=breakfast')
    results.append(check(
        '只返回指定餐次的方案（早餐没有就 404，不拿午餐顶替）',
        other_meal.status_code == 404,
        f'实际 {other_meal.status_code} {other_meal.get_json()}'))

    bad_meal = client.get('/api/recommend/latest?meal_type=夜宵')
    results.append(check(
        '无法识别的餐次返回 400 并列出可选值',
        bad_meal.status_code == 400
        and 'breakfast' in (bad_meal.get_json() or {}).get('error', ''),
        f'实际 {bad_meal.status_code} {bad_meal.get_json()}'))

    # 模型入口已被换成「一调用就炸」，仍然 200 即证明这条路径不打模型
    again = client.get('/api/recommend/latest?meal_type=lunch')
    results.append(check(
        '不打模型：模型入口被替换成抛异常的实现后接口仍返回 200',
        again.status_code == 200,
        f'实际 {again.status_code} {again.data[:200]!r}'))


def check_meal_schedule(client, results):
    print('用餐时间接口 /api/meal-schedule：')

    got = client.get('/api/meal-schedule')
    payload = got.get_json() or {}
    schedule = payload.get('schedule') or []
    results.append(check(
        'GET 返回三餐 + 下发参数',
        got.status_code == 200 and len(schedule) == 3
        and isinstance(payload.get('lead_minutes'), int)
        and isinstance(payload.get('auto_publish'), bool)
        and isinstance(payload.get('daily_publish_time'), str)
        and len(payload.get('daily_publish_time')) == 5,
        f'实际 {payload}'))
    results.append(check(
        '按早→中→晚排序，而不是数据库主键顺序',
        [item['meal_type'] for item in schedule] == ['breakfast', 'lunch', 'dinner'],
        f"实际 {[item.get('meal_type') for item in schedule]}"))
    results.append(check(
        '每项都带中文标签与启用状态',
        all(item.get('label') and 'enabled' in item and item.get('meal_time')
            for item in schedule),
        f'实际 {schedule}'))

    updated = client.put('/api/meal-schedule', json={
        'daily_publish_time': '06:10',
        'schedule': [
            {'meal_type': '午餐', 'meal_time': '11:45'},
            {'meal_type': 'dinner', 'meal_time': '19:05'},
        ],
    })
    updated_payload = updated.get_json() or {}
    times = {item['meal_type']: item['meal_time']
             for item in updated_payload.get('schedule', [])}
    results.append(check(
        'PUT 正常写入（中英文 meal_type 都接受），并回传新的时间表',
        updated.status_code == 200
        and updated_payload.get('daily_publish_time') == '06:10'
        and times.get('lunch') == '11:45' and times.get('dinner') == '19:05',
        f'实际 {updated.status_code} {times}'))
    results.append(check(
        '未提交的餐次保持原值（早餐没被清掉）',
        times.get('breakfast') == '07:30',
        f'实际 {times}'))

    bad_daily = client.put('/api/meal-schedule', json={'daily_publish_time': '24:00'})
    results.append(check(
        '非法每日更新时间 24:00 返回 400',
        bad_daily.status_code == 400,
        f'实际 {bad_daily.status_code} {bad_daily.get_json()}'))

    bad_time = client.put('/api/meal-schedule', json={'schedule': [
        {'meal_type': 'breakfast', 'meal_time': '06:30'},
        {'meal_type': 'lunch', 'meal_time': '25:00'},
    ]})
    results.append(check(
        '非法时间 25:00 整体返回 400',
        bad_time.status_code == 400,
        f'实际 {bad_time.status_code} {bad_time.get_json()}'))

    after_bad = {item['meal_type']: item['meal_time']
                 for item in (client.get('/api/meal-schedule').get_json() or {}).get('schedule', [])}
    results.append(check(
        '整体拒绝时一个字节都没写（早餐仍是 07:30，没有半套时间表）',
        after_bad.get('breakfast') == '07:30' and after_bad.get('lunch') == '11:45',
        f'实际 {after_bad}'))

    bad_meal = client.put('/api/meal-schedule', json={'schedule': [
        {'meal_type': '夜宵', 'meal_time': '23:00'},
    ]})
    results.append(check(
        '未知 meal_type 返回 400 并列出可选值',
        bad_meal.status_code == 400
        and 'breakfast' in (bad_meal.get_json() or {}).get('error', ''),
        f'实际 {bad_meal.status_code} {bad_meal.get_json()}'))

    empty_body = client.put('/api/meal-schedule', json={})
    results.append(check(
        '缺少 schedule 返回 400',
        empty_body.status_code == 400,
        f'实际 {empty_body.status_code} {empty_body.get_json()}'))

    conflict = client.put('/api/meal-schedule', json={'schedule': [
        {'meal_type': 'lunch', 'meal_time': '12:00'},
        {'meal_type': '午餐', 'meal_time': '13:00'},
    ]})
    results.append(check(
        '同一餐给了两个冲突时间返回 400（不替用户猜哪个为准）',
        conflict.status_code == 400,
        f'实际 {conflict.status_code} {conflict.get_json()}'))


def check_device_prefs(client, results):
    print('设备展示偏好 /api/devices/<id>/prefs：')

    got = client.get(f'/api/devices/{DEVICE_ID}/prefs')
    payload = got.get_json() or {}
    results.append(check(
        '从未设置过时返回默认版式与可选清单',
        got.status_code == 200
        and payload.get('layout') == menu_render.DEFAULT_LAYOUT
        and len(payload.get('layout_choices') or []) == len(menu_render.LAYOUTS),
        f'实际 {got.status_code} {payload}'))

    put = client.put(f'/api/devices/{DEVICE_ID}/prefs', json={'layout': 'list_big'})
    results.append(check(
        'PUT 写入 list_big 并回传新值',
        put.status_code == 200 and (put.get_json() or {}).get('layout') == 'list_big',
        f'实际 {put.status_code} {put.get_json()}'))

    reread = client.get(f'/api/devices/{DEVICE_ID}/prefs')
    results.append(check(
        '再次 GET 读到的是刚写入的版式（真的落库了）',
        (reread.get_json() or {}).get('layout') == 'list_big',
        f'实际 {reread.get_json()}'))

    # 旧客户端仍会发已合并的版式键。对它们报 400 等于让升级前装好的 App 改不了版式，
    # 所以接口必须接受别名并归一后落库 —— 落库的是新键，别名表不渗进数据。
    alias = client.put(f'/api/devices/{DEVICE_ID}/prefs', json={'layout': 'bold'})
    results.append(check(
        '旧版式键 bold 被接受并归一成 list_big',
        alias.status_code == 200 and (alias.get_json() or {}).get('layout') == 'list_big',
        f'实际 {alias.status_code} {alias.get_json()}'))

    bad = client.put(f'/api/devices/{DEVICE_ID}/prefs', json={'layout': 'neon'})
    error = (bad.get_json() or {}).get('error', '')
    results.append(check(
        '未知 layout 返回 400 并列出可选值',
        bad.status_code == 400 and 'columns' in error and 'portrait' in error,
        f'实际 {bad.status_code} {bad.get_json()}'))

    still_set = client.get(f'/api/devices/{DEVICE_ID}/prefs')
    results.append(check(
        '被拒绝的写入没有改动原有偏好',
        (still_set.get_json() or {}).get('layout') == 'list_big',
        f'实际 {still_set.get_json()}'))


def check_render_layouts_and_preview(client, results):
    print('版式清单与预览 /api/render/layouts、/api/render/preview：')

    layouts = client.get('/api/render/layouts')
    items = (layouts.get_json() or {}).get('layouts') or []
    results.append(check(
        'GET /api/render/layouts 返回全部版式',
        layouts.status_code == 200 and len(items) == len(menu_render.LAYOUTS),
        f'实际 {layouts.status_code} {items}'))
    results.append(check(
        '每项都带 key / label / description / orientation',
        all({'key', 'label', 'description', 'orientation'} <= set(item) for item in items),
        f'实际 {items}'))

    for key in menu_render.LAYOUTS:
        response = client.get(f'/api/render/preview?layout={key}')
        ok = response.status_code == 200 and response.mimetype == 'image/png'
        size = None
        if ok:
            size = Image.open(io.BytesIO(response.data)).size
        spec = menu_render.get_layout(key)
        results.append(check(
            f'预览 {key} 返回 PNG 且尺寸 792×528（竖版是旋转后的真实上屏观感）',
            ok and size == (menu_render.WIDTH, menu_render.HEIGHT)
            and response.headers.get('X-Film-Layout') == key
            and response.headers.get('X-Film-Orientation') == (
                'portrait' if spec.portrait else 'landscape'),
            f'状态 {response.status_code} mimetype={response.mimetype} size={size} '
            f"头 {response.headers.get('X-Film-Layout')}/"
            f"{response.headers.get('X-Film-Orientation')}"))

    for kind in ('breakfast', 'lunch', 'dinner'):
        response = client.get(f'/api/render/preview?kind={kind}')
        size = (Image.open(io.BytesIO(response.data)).size
                if response.status_code == 200 else None)
        results.append(check(
            f'预览 kind={kind} 返回 PNG 且尺寸 792×528',
            response.status_code == 200 and response.mimetype == 'image/png'
            and size == (menu_render.WIDTH, menu_render.HEIGHT),
            f'状态 {response.status_code} size={size}'))

    natural = client.get('/api/render/preview?layout=portrait&kind=dinner&natural=1')
    natural_size = (Image.open(io.BytesIO(natural.data)).size
                    if natural.status_code == 200 else None)
    results.append(check(
        '竖版 natural 预览按自然方向返回 528×792',
        natural.status_code == 200 and natural.mimetype == 'image/png'
        and natural_size == (menu_render.HEIGHT, menu_render.WIDTH),
        f'状态 {natural.status_code} size={natural_size}'))

    bad_layout = client.get('/api/render/preview?layout=neon')
    results.append(check(
        '未知 layout 返回 400 并列出可选值',
        bad_layout.status_code == 400
        and 'columns' in (bad_layout.get_json() or {}).get('error', ''),
        f'实际 {bad_layout.status_code} {bad_layout.get_json()}'))

    bad_kind = client.get('/api/render/preview?kind=supper')
    results.append(check(
        '未知 kind 返回 400 并列出可选值',
        bad_kind.status_code == 400
        and 'overview' in (bad_kind.get_json() or {}).get('error', ''),
        f'实际 {bad_kind.status_code} {bad_kind.get_json()}'))

    # 预览不依赖库存/方案/模型：这里库存是空的，仍然必须出图 ——
    # 否则「冰箱空的时候选不了版式」。
    inventory = client.get('/api/inventory').get_json() or {}
    empty_fridge = client.get('/api/render/preview?layout=portrait&kind=dinner')
    results.append(check(
        '库存为空时预览照样出图（用内置示例数据）',
        not inventory.get('items') and empty_fridge.status_code == 200,
        f"库存 {len(inventory.get('items') or [])} 项，预览 {empty_fridge.status_code}"))


def main() -> int:
    db.init_db()
    _forbid_model_calls()

    results = []
    client = app.test_client()

    check_recommend_latest(client, results)
    check_meal_schedule(client, results)
    check_device_prefs(client, results)
    check_render_layouts_and_preview(client, results)

    print()
    if all(results):
        print('read/config api self-check passed')
        return 0
    print(f'FAILED: {results.count(False)}/{len(results)} 项不符合预期')
    return 1


if __name__ == '__main__':
    sys.exit(main())


def test_self_check():
    """pytest 入口：把这个模块的整套自检当成一个用例跑。

    为什么需要它：本模块是**脚本式**的 —— 编排在 `main()` 里，断言靠 `check()`
    累积。pytest 只收集 `test_` 开头的函数，而本模块里没有一个零参数的 `test_`，
    于是 pytest **一个用例都收集不到，整个模块被静默跳过**：测试报告是绿的，
    这些覆盖却从未运行过。这比失败更糟，因为它不留痕迹。

    这里不复述各条断言，只把 `main()` 的成败翻译成 pytest 的成败：
    断言逻辑只有一份，脚本直跑（`python tests/test_api_read_endpoints.py`）与 pytest 跑的是同一套。
    """
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'
