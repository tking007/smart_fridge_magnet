#!/usr/bin/env python3
"""校验「推送提前量 / 自动下发开关可在 App 里改」这条链路。

取值口径是 **DB 覆盖 env**，因此有一类静默错误：接口写进了 DB，而调度器还在读环境变量，
表现是「App 里显示 45 分钟、设备仍按 30 分钟换图」，两边都返回成功、日志里什么都没有。
所以这里不只断言 HTTP 响应，还直接调 `settings_store` 的函数确认**调度器那条路**
拿到的是同一个值。

重点覆盖：
  1. DB 无值时回落 env（用非默认的 SFM_AUTO_PUBLISH_LEAD_MIN=45 / SFM_AUTO_PUBLISH=0
     验证，取默认值时「回落生效」和「恰好等于默认」分不开）；
  2. PUT 写入后 GET 与 settings_store 读到同一个新值；
  3. 越界（-1 / 241）与类型错误（字符串 "true"）一律 400，**且一个字节都不写**；
     小于调度器检查周期的提前量（0 ~ tick-1）同样 400 —— 那不是「饭点当时才发」，
     而是静默关掉自动下发：窗口比一跳还窄，几乎永不命中，可开关仍显示开着；
  4. auto_publish=false 能正确回读 —— 假值最容易被 `data.get(...) is not None`
     这类在场判断吃掉，变成「关不掉的开关」；
  5. 只给 lead_minutes 不给 schedule 也能成功，且时间表不被清空；
  6. 三项都不给 → 400（空请求体一定是客户端 bug，静默 200 会让它一直查不出来）；
  7. 库里塞一个损坏值时回落 env 而不是抛异常 —— 这条路上没有调用方在等答复；
  8. lead_minutes_min / lead_minutes_max 出现在 GET 响应里（客户端不必硬编码范围）；
  9. `LEAD_MINUTES_MIN >= DISPLAY_PUBLISH_TICK_MINUTES`，且 display_publish job 的间隔
     就是那个 tick —— 下界必须由检查周期推导。把 tick 调大而忘了同步下界时，界面上又能
     选到一个永不命中的值，只有这个不变式能拦住。

用法：python tests/test_publish_settings.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在导入 src.* 之前设好：库与 .film 都落到临时目录，别碰开发机上的真实数据
_tmp = tempfile.mkdtemp(prefix='sfm-settings-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''          # 留空 → 本机开发放行，不必构造鉴权头
os.environ['SFM_HOST'] = '127.0.0.1'
# 别让调度器在测试进程里跑：容器里 SFM_ENABLE_SCHEDULER=true，导入 app 就会再起一份，
# 而餐前下发会真的向设备发布新版本。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
# 刻意用**非默认值**：config 的默认是 30 / True，若这里也用默认值，
# 「env 兜底生效」与「恰好等于默认值」两种情况在断言上无法区分。
ENV_LEAD_MINUTES = 45
ENV_AUTO_PUBLISH = False
ENV_DAILY_PUBLISH_TIME = '07:15'
DEFAULT_EFFECTIVE_PUBLISH_TIME = '06:35'
os.environ['SFM_AUTO_PUBLISH_LEAD_MIN'] = str(ENV_LEAD_MINUTES)
os.environ['SFM_AUTO_PUBLISH'] = '0'
os.environ['SFM_DAILY_PUBLISH_TIME'] = ENV_DAILY_PUBLISH_TIME

from datetime import datetime, timedelta  # noqa: E402

from src import config                    # noqa: E402
from src import database as db            # noqa: E402
from src import settings_store            # noqa: E402
from src.app import app                   # noqa: E402
from src.scheduler import TaskScheduler   # noqa: E402


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


def _clear_settings():
    """清空 app_settings，回到「DB 里什么都没有」的初始状态。"""
    conn = db.get_connection()
    try:
        conn.execute('DELETE FROM app_settings')
        conn.commit()
    finally:
        conn.close()


def _write_raw(key: str, value: str):
    """绕过 set_value 直接塞一个原始字符串，用于构造损坏数据。"""
    conn = db.get_connection()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO app_settings (key, value, updated_at)'
            ' VALUES (?, ?, CURRENT_TIMESTAMP)',
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()


def _times(payload: dict) -> dict:
    return {item['meal_type']: item['meal_time']
            for item in (payload or {}).get('schedule', [])}


def check_env_fallback(client, results):
    print('DB 无值时回落环境变量：')
    _clear_settings()

    results.append(check(
        f'settings_store.publish_lead_minutes() == SFM_AUTO_PUBLISH_LEAD_MIN({ENV_LEAD_MINUTES})',
        settings_store.publish_lead_minutes() == ENV_LEAD_MINUTES,
        f'实际 {settings_store.publish_lead_minutes()}'))
    results.append(check(
        'settings_store.auto_publish_enabled() == SFM_AUTO_PUBLISH(0 → False)',
        settings_store.auto_publish_enabled() is ENV_AUTO_PUBLISH,
        f'实际 {settings_store.auto_publish_enabled()!r}'))

    # 回落值必须与 config 那份口径一致：不一致说明兜底路径自己算了一遍
    results.append(check(
        '回落值与 config.auto_publish_lead_minutes() 完全一致',
        settings_store.publish_lead_minutes() == config.auto_publish_lead_minutes(),
        f'settings_store={settings_store.publish_lead_minutes()} '
        f'config={config.auto_publish_lead_minutes()}'))

    payload = client.get('/api/meal-schedule').get_json() or {}
    results.append(check(
        'GET 返回的也是 env 兜底值（接口与调度器同一口径）',
        payload.get('lead_minutes') == ENV_LEAD_MINUTES
        and payload.get('auto_publish') is ENV_AUTO_PUBLISH
        and payload.get('daily_publish_time') == DEFAULT_EFFECTIVE_PUBLISH_TIME,
        f'实际 {payload.get("lead_minutes")} / {payload.get("auto_publish")!r} '
        f'/ {payload.get("daily_publish_time")}'))

    results.append(check(
        'get_int / get_bool 对不存在的键返回 fallback',
        settings_store.get_int('nonexistent_key', 7) == 7
        and settings_store.get_bool('nonexistent_key', True) is True))


def check_range_hints_in_get(client, results):
    print('GET 带出可调范围：')

    payload = client.get('/api/meal-schedule').get_json() or {}
    results.append(check(
        'lead_minutes_min / lead_minutes_max 出现在响应里（客户端不必硬编码）',
        payload.get('lead_minutes_min') == 5 and payload.get('lead_minutes_max') == 240,
        f'实际 {payload.get("lead_minutes_min")} / {payload.get("lead_minutes_max")}'))
    results.append(check(
        '范围与 settings_store 的常量一致（两处漂移会让界面选得到的值被接口拒掉）',
        payload.get('lead_minutes_min') == settings_store.LEAD_MINUTES_MIN
        and payload.get('lead_minutes_max') == settings_store.LEAD_MINUTES_MAX,
        f'实际 {payload}'))

    # 下界必须由调度器的检查周期推导，不能是另取的常量。这条断言是给**将来**的人看的：
    # 把 tick 从 5 调大而忘了同步下界，界面上就又能选到一个永不命中的提前量
    # （开关显示开着、屏幕不换图、两端日志无异常），而那种 bug 从任何单元测试上
    # 都看不出来 —— 只能靠这个不变式挡住。
    results.append(check(
        f'LEAD_MINUTES_MIN({settings_store.LEAD_MINUTES_MIN}) >= '
        f'DISPLAY_PUBLISH_TICK_MINUTES({settings_store.DISPLAY_PUBLISH_TICK_MINUTES})'
        '（下界随 tick 走，不是另取的常量）',
        settings_store.LEAD_MINUTES_MIN >= settings_store.DISPLAY_PUBLISH_TICK_MINUTES,
        f'实际 {settings_store.LEAD_MINUTES_MIN} / '
        f'{settings_store.DISPLAY_PUBLISH_TICK_MINUTES}'))

    # 调度器的 job 也必须用同一个常量，否则「下界随 tick 走」只是注释里的说法
    scheduler = TaskScheduler()
    scheduler._add_display_publish_task()
    job = scheduler.scheduler.get_job('display_publish')
    results.append(check(
        'display_publish job 的间隔 == DISPLAY_PUBLISH_TICK_MINUTES'
        '（tick 与下界共用一份定义）',
        job is not None
        and job.trigger.interval == timedelta(
            minutes=settings_store.DISPLAY_PUBLISH_TICK_MINUTES),
        f'实际 {job.trigger if job else None}'))


def check_put_overrides_env(client, results):
    print('PUT 写入后 DB 值覆盖 env：')
    _clear_settings()

    put = client.put('/api/meal-schedule', json={
        'lead_minutes': 90,
        'auto_publish': True,
        'daily_publish_time': '05:45',
    })
    body = put.get_json() or {}
    results.append(check(
        'PUT 返回 200，且响应里已是新值（客户端可直接拿返回值刷界面）',
        put.status_code == 200 and body.get('lead_minutes') == 90
        and body.get('auto_publish') is True
        and body.get('daily_publish_time') == '05:45',
        f'实际 {put.status_code} {body}'))

    reread = client.get('/api/meal-schedule').get_json() or {}
    results.append(check(
        '再次 GET 读到的是刚写入的值（真的落库了）',
        reread.get('lead_minutes') == 90 and reread.get('auto_publish') is True
        and reread.get('daily_publish_time') == '05:45',
        f'实际 {reread.get("lead_minutes")} / {reread.get("auto_publish")!r} '
        f'/ {reread.get("daily_publish_time")}'))

    # 这两条是整个特性的关键：调度器不经过 HTTP，它读的是 settings_store。
    # 这里读不到新值就意味着「App 显示 90 分钟、设备仍按 45 分钟换图」，且两边都不报错。
    results.append(check(
        'settings_store.publish_lead_minutes() 也读到 90（调度器那条路拿到 DB 值）',
        settings_store.publish_lead_minutes() == 90,
        f'实际 {settings_store.publish_lead_minutes()}'))
    results.append(check(
        'settings_store.auto_publish_enabled() 也读到 True（env 仍是 0，证明 DB 优先）',
        settings_store.auto_publish_enabled() is True
        and config.auto_publish_enabled() is False,
        f'settings_store={settings_store.auto_publish_enabled()!r} '
        f'config={config.auto_publish_enabled()!r}'))
    results.append(check(
        'settings_store.daily_publish_time() 读到 DB 的 05:45',
        settings_store.daily_publish_time() == '05:45',
        f'实际 {settings_store.daily_publish_time()}'))

    low = settings_store.LEAD_MINUTES_MIN
    results.append(check(
        f'边界值 {low} 与 240 均被接受',
        client.put('/api/meal-schedule', json={'lead_minutes': low}).status_code == 200
        and settings_store.publish_lead_minutes() == low
        and client.put('/api/meal-schedule', json={'lead_minutes': 240}).status_code == 200
        and settings_store.publish_lead_minutes() == 240,
        f'实际 {settings_store.publish_lead_minutes()}'))


def check_auto_publish_false_roundtrip(client, results):
    print('auto_publish = false 的回读（防「假值被当作缺省」）：')
    _clear_settings()
    settings_store.set_value(settings_store.KEY_AUTO_PUBLISH, True)

    put = client.put('/api/meal-schedule', json={'auto_publish': False})
    body = put.get_json() or {}
    results.append(check(
        'PUT false 返回 200 且响应里是 false（不是被当成「没给」而忽略）',
        put.status_code == 200 and body.get('auto_publish') is False,
        f'实际 {put.status_code} {body.get("auto_publish")!r}'))
    results.append(check(
        'GET 与 settings_store 都读到 False',
        (client.get('/api/meal-schedule').get_json() or {}).get('auto_publish') is False
        and settings_store.auto_publish_enabled() is False,
        f'实际 settings_store={settings_store.auto_publish_enabled()!r}'))

    # 再打开一次，确认这不是「写什么都变 false」
    client.put('/api/meal-schedule', json={'auto_publish': True})
    results.append(check(
        '能再打开（开关双向可用）',
        settings_store.auto_publish_enabled() is True,
        f'实际 {settings_store.auto_publish_enabled()!r}'))


def check_validation_rejects_and_writes_nothing(client, results):
    print('校验失败时整体拒绝、一个字节都不写：')
    _clear_settings()
    client.put('/api/meal-schedule', json={'lead_minutes': 60, 'auto_publish': True})
    client.put('/api/meal-schedule', json={'schedule': [
        {'meal_type': 'lunch', 'meal_time': '12:15'},
    ]})

    for bad in (-1, 241, 999):
        response = client.put('/api/meal-schedule', json={'lead_minutes': bad})
        results.append(check(
            f'lead_minutes = {bad} 返回 400',
            response.status_code == 400,
            f'实际 {response.status_code} {response.get_json()}'))

    # 小于一个检查周期的提前量必须被拒。这不是「用户显式选择的极端值」而是
    # **静默关掉自动下发**：调度器每 tick 分钟才判一次窗口，窗口比一跳还窄时几乎
    # 永不命中 —— 开关仍显示开着、GET 也显示开着，屏幕却再也不换图，两端日志无异常。
    # 0 必须被显式覆盖到。
    tick = settings_store.DISPLAY_PUBLISH_TICK_MINUTES
    for bad in range(0, tick):
        response = client.put('/api/meal-schedule', json={'lead_minutes': bad})
        body = response.get_json() or {}
        results.append(check(
            f'lead_minutes = {bad} 返回 400（小于检查周期 {tick} 分钟 → 永不命中窗口）',
            response.status_code == 400,
            f'实际 {response.status_code} {body}'))
    # 文案得让看到 400 的人知道「该改成多少、为什么」，否则拖到 0 的用户只会以为接口坏了
    zero_error = (client.put('/api/meal-schedule',
                             json={'lead_minutes': 0}).get_json() or {}).get('error', '')
    results.append(check(
        f'400 文案给出下界数字（{settings_store.LEAD_MINUTES_MIN}）与「窗口」这个理由',
        str(settings_store.LEAD_MINUTES_MIN) in zero_error and '窗口' in zero_error,
        f'实际 {zero_error!r}'))

    for bad in ('45', 45.5, True, None, [45]):
        response = client.put('/api/meal-schedule', json={'lead_minutes': bad})
        results.append(check(
            f'lead_minutes = {bad!r} 返回 400（不做宽松解析）',
            response.status_code == 400,
            f'实际 {response.status_code} {response.get_json()}'))

    for bad in ('true', 'false', 1, 0, None):
        response = client.put('/api/meal-schedule', json={'auto_publish': bad})
        results.append(check(
            f'auto_publish = {bad!r} 返回 400（必须是 JSON 布尔）',
            response.status_code == 400,
            f'实际 {response.status_code} {response.get_json()}'))

    for bad in ('24:00', '06:60', 'bad', 630, None):
        response = client.put('/api/meal-schedule', json={'daily_publish_time': bad})
        results.append(check(
            f'daily_publish_time = {bad!r} 返回 400（必须是合法 HH:MM）',
            response.status_code == 400,
            f'实际 {response.status_code} {response.get_json()}'))

    after = client.get('/api/meal-schedule').get_json() or {}
    results.append(check(
        '所有被拒绝的请求都没有改动任何值（lead_minutes 仍是 60，auto_publish 仍是 true）',
        after.get('lead_minutes') == 60 and after.get('auto_publish') is True
        and settings_store.publish_lead_minutes() == 60,
        f'实际 {after.get("lead_minutes")} / {after.get("auto_publish")!r}'))

    # 混合请求：schedule 合法但 lead_minutes 超界 —— 时间表不能被先写进去。
    # 「校验一个写一个」的实现会在这里露馅：返回 400，而库里其实已经变了。
    mixed = client.put('/api/meal-schedule', json={
        'schedule': [{'meal_type': 'lunch', 'meal_time': '13:30'}],
        'lead_minutes': 1000,
    })
    reread = client.get('/api/meal-schedule').get_json() or {}
    results.append(check(
        'schedule 合法但 lead_minutes 超界 → 400，且 schedule 也没被写进去',
        mixed.status_code == 400 and _times(reread).get('lunch') == '12:15',
        f'实际 {mixed.status_code} {_times(reread)}'))

    # 反向：lead_minutes 合法但 schedule 非法 —— 设置项不能被先写进去
    mixed2 = client.put('/api/meal-schedule', json={
        'schedule': [{'meal_type': 'lunch', 'meal_time': '25:00'}],
        'lead_minutes': 120,
    })
    results.append(check(
        'lead_minutes 合法但 schedule 非法 → 400，且 lead_minutes 也没被写进去',
        mixed2.status_code == 400 and settings_store.publish_lead_minutes() == 60,
        f'实际 {mixed2.status_code} lead={settings_store.publish_lead_minutes()}'))


def check_partial_updates(client, results):
    print('部分更新（四项都可选，至少给一个）：')
    _clear_settings()
    client.put('/api/meal-schedule', json={'schedule': [
        {'meal_type': 'breakfast', 'meal_time': '06:45'},
        {'meal_type': 'lunch', 'meal_time': '12:30'},
        {'meal_type': 'dinner', 'meal_time': '18:20'},
    ]})

    only_lead = client.put('/api/meal-schedule', json={'lead_minutes': 75})
    body = only_lead.get_json() or {}
    results.append(check(
        '只给 lead_minutes（不给 schedule）返回 200',
        only_lead.status_code == 200 and body.get('lead_minutes') == 75,
        f'实际 {only_lead.status_code} {body.get("lead_minutes")}'))
    results.append(check(
        'schedule 没被清空，三餐时间原样保留',
        _times(body) == {'breakfast': '06:45', 'lunch': '12:30', 'dinner': '18:20'},
        f'实际 {_times(body)}'))

    only_auto = client.put('/api/meal-schedule', json={'auto_publish': True})
    results.append(check(
        '只给 auto_publish 返回 200，且 lead_minutes 与 schedule 都不变',
        only_auto.status_code == 200
        and (only_auto.get_json() or {}).get('lead_minutes') == 75
        and _times(only_auto.get_json()) == {'breakfast': '06:45', 'lunch': '12:30',
                                             'dinner': '18:20'},
        f'实际 {only_auto.status_code} {only_auto.get_json()}'))

    only_schedule = client.put('/api/meal-schedule', json={'schedule': [
        {'meal_type': 'lunch', 'meal_time': '11:50'},
    ]})
    results.append(check(
        '只给 schedule 返回 200，且 lead_minutes / auto_publish 不被重置',
        only_schedule.status_code == 200
        and (only_schedule.get_json() or {}).get('lead_minutes') == 75
        and (only_schedule.get_json() or {}).get('auto_publish') is True,
        f'实际 {only_schedule.status_code} {only_schedule.get_json()}'))

    only_daily = client.put('/api/meal-schedule', json={'daily_publish_time': '06:20'})
    results.append(check(
        '旧客户端可写 daily_publish_time，但有效时间仍不晚于第一餐取图截止点',
        only_daily.status_code == 200
        and (only_daily.get_json() or {}).get('daily_publish_time') == '05:20'
        and (only_daily.get_json() or {}).get('lead_minutes') == 75
        and (only_daily.get_json() or {}).get('auto_publish') is True,
        f'实际 {only_daily.status_code} {only_daily.get_json()}'))

    protected = client.put('/api/meal-schedule', json={
        'schedule': [{'meal_type': 'breakfast', 'meal_time': '06:00'}],
        'lead_minutes': 30,
        'daily_publish_time': '06:30',
    })
    results.append(check(
        '隐藏旧发布时间晚于设备唤醒时，后端自动提前到 05:20',
        protected.status_code == 200
        and (protected.get_json() or {}).get('daily_publish_time') == '05:20',
        f'实际 {protected.status_code} {protected.get_json()}'))

    for body_json in ({}, {'unknown_field': 1}, None):
        empty = client.put('/api/meal-schedule', json=body_json)
        results.append(check(
            f'四项都不给（{body_json!r}）返回 400',
            empty.status_code == 400,
            f'实际 {empty.status_code} {empty.get_json()}'))

    still = client.get('/api/meal-schedule').get_json() or {}
    results.append(check(
        '空请求被拒后一切照旧',
        still.get('lead_minutes') == 30
        and _times(still).get('breakfast') == '06:00'
        and _times(still).get('lunch') == '11:50',
        f'实际 {still}'))


def check_corrupt_db_value_falls_back(client, results):
    print('库里的值损坏时回落 env（不抛异常）：')
    _clear_settings()

    # '0' 与 '4' 是超界值而非手改：它们必须走回落 —— 照着 0 跑等于自动下发再也不触发，
    # 而那种失败在两端日志里都看不见。
    for corrupt in ('abc', '', '  ', '30分钟', '1e3', '-5', '9999', '0', '4'):
        _write_raw(settings_store.KEY_LEAD_MINUTES, corrupt)
        try:
            value = settings_store.publish_lead_minutes()
            ok = value == ENV_LEAD_MINUTES
            detail = f'实际 {value}'
        except Exception as exc:      # noqa: BLE001 — 抛异常本身就是要盯住的回归
            ok = False
            detail = f'抛了异常 {type(exc).__name__}: {exc}'
        results.append(check(
            f'lead_minutes = {corrupt!r} → 回落 env {ENV_LEAD_MINUTES}，不抛异常', ok, detail))

    _write_raw(settings_store.KEY_AUTO_PUBLISH, 'maybe')
    try:
        value = settings_store.auto_publish_enabled()
        ok = value is ENV_AUTO_PUBLISH
        detail = f'实际 {value!r}'
    except Exception as exc:          # noqa: BLE001
        ok = False
        detail = f'抛了异常 {type(exc).__name__}: {exc}'
    results.append(check(
        f"auto_publish = 'maybe' → 回落 env {ENV_AUTO_PUBLISH}，不抛异常", ok, detail))

    # GET 也必须还能出结果 —— 损坏值不该把接口打成 500
    response = client.get('/api/meal-schedule')
    payload = response.get_json() or {}
    results.append(check(
        '损坏值下 GET 仍返回 200 且给出 env 兜底值',
        response.status_code == 200
        and payload.get('lead_minutes') == ENV_LEAD_MINUTES
        and payload.get('auto_publish') is ENV_AUTO_PUBLISH,
        f'实际 {response.status_code} {payload.get("lead_minutes")} '
        f'{payload.get("auto_publish")!r}'))

    # env 兜底值本身低于下界时也要抬到下界。config 的 max(1, ...) 只挡了 0，
    # 1–4 分钟同样窄于检查周期，症状和 0 一样；而 GET 若回出一个比它自己带出的
    # lead_minutes_min 还小的 lead_minutes，客户端连这个值都渲染不出来。
    _clear_settings()
    os.environ['SFM_AUTO_PUBLISH_LEAD_MIN'] = '1'
    try:
        results.append(check(
            'env 兜底值 1 分钟被抬到下界（不是照着一个永不命中的值跑）',
            settings_store.publish_lead_minutes() == settings_store.LEAD_MINUTES_MIN,
            f'实际 {settings_store.publish_lead_minutes()}'))
        payload = client.get('/api/meal-schedule').get_json() or {}
        results.append(check(
            'GET 返回的 lead_minutes 不会小于它自己带出的 lead_minutes_min',
            payload.get('lead_minutes') >= payload.get('lead_minutes_min'),
            f'实际 {payload.get("lead_minutes")} / {payload.get("lead_minutes_min")}'))
    finally:
        os.environ['SFM_AUTO_PUBLISH_LEAD_MIN'] = str(ENV_LEAD_MINUTES)

    # 人用 sqlite3 手改成 'true' 这类写法应当被认下来，而不是当成坏值
    _write_raw(settings_store.KEY_AUTO_PUBLISH, 'true')
    results.append(check(
        "手写的 'true' 能被识别（库里的值可能是人改的，只认 '1' 会白白回落)",
        settings_store.auto_publish_enabled() is True,
        f'实际 {settings_store.auto_publish_enabled()!r}'))

    # 一次合法写入应当把损坏值覆盖掉，不需要人工清理
    client.put('/api/meal-schedule', json={'lead_minutes': 30, 'auto_publish': False})
    results.append(check(
        '一次合法 PUT 即可覆盖损坏值',
        settings_store.publish_lead_minutes() == 30
        and settings_store.auto_publish_enabled() is False,
        f'实际 {settings_store.publish_lead_minutes()} / '
        f'{settings_store.auto_publish_enabled()!r}'))


def check_scheduler_reads_db_value(client, results):
    """调度器那条路必须跟着 DB 走 —— 这是整个特性能不能用的判据。

    只断言 HTTP 响应是不够的：接口写进了 DB、调度器还在读 env 时，App 上一切正常，
    设备却按旧的提前量换图，两边都不报错。
    """
    print('调度器读到的是 DB 值：')
    _clear_settings()

    scheduler = TaskScheduler()

    # 关闭状态下 job 也必须注册。以前是「关着就不注册」，而开关现在能在 App 里改 ——
    # 那种写法会让「打开开关」必须重启后端才生效，接口却照样返回 200。
    client.put('/api/meal-schedule', json={'auto_publish': False})
    scheduler._add_display_publish_task()
    job_ids = [job.id for job in scheduler.scheduler.get_jobs()]
    results.append(check(
        '开关关闭时 display_publish job 仍然注册（打开开关不需要重启后端）',
        'display_publish' in job_ids,
        f'实际 {job_ids}'))

    # 关闭状态下每一跳应当直接返回：窗口内有一顿饭、且没有任何设备，
    # 若真的走下去会留下一条「无可下发设备」的失败记录。
    #
    # daily_publish_time 必须设成**早于当前时刻**的值。用默认的 06:30 会让这个用例
    # 在 00:00–06:30 之间恒红：`_publish_meal_display()` 的第一道门就是
    # 「还没到每日更新时间就直接 return」，于是没有任何执行记录，而失败信息只是
    # 一个空列表，完全看不出是时间没到（本次是在 00:05 撞上的）。
    # 取 00:00 让这道门在一天里任何时刻都已打开。
    client.put('/api/meal-schedule', json={'daily_publish_time': '00:00'})
    target = datetime.now() + timedelta(minutes=5)
    db.set_meal_schedule('lunch', f'{target.hour:02d}:{target.minute:02d}')
    scheduler._publish_meal_display()
    results.append(check(
        '开关关闭时每一跳直接跳过（没有产生任何执行记录）',
        scheduler.get_task_history() == [],
        f'实际 {scheduler.get_task_history()}'))

    # 打开开关后同一个实例立刻生效（每跳重读，不缓存）
    client.put('/api/meal-schedule', json={'auto_publish': True})
    scheduler._publish_meal_display()
    history = scheduler.get_task_history()
    results.append(check(
        '在 App 里打开后同一个调度器实例立刻开始工作（每跳重读设置，不缓存）',
        len(history) == 1 and history[0]['task_id'] == 'daily_menu_rotation'
        and history[0]['success'] is False,
        f'实际 {history}'))

    _clear_settings()


def main() -> int:
    db.init_db()

    results = []
    client = app.test_client()

    check_env_fallback(client, results)
    check_range_hints_in_get(client, results)
    check_put_overrides_env(client, results)
    check_auto_publish_false_roundtrip(client, results)
    check_validation_rejects_and_writes_nothing(client, results)
    check_partial_updates(client, results)
    check_corrupt_db_value_falls_back(client, results)
    check_scheduler_reads_db_value(client, results)

    print()
    if all(results):
        print('publish settings self-check passed')
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
    断言逻辑只有一份，脚本直跑（`python tests/test_publish_settings.py`）与 pytest 跑的是同一套。
    """
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'
