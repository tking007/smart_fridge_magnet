#!/usr/bin/env python3
"""校验「餐前自动下发画面」这条链路。

值得单独一个测试是因为这条链路的失败全都是**静默的**：定时任务里没有调用方在等返回值，
任何一环出错的表现都只是「饭点到了、屏幕没变」，而后端日志一切正常。

重点覆盖这几件事：
  1. 命中餐前窗口时真的产生了新版本（渲染 + 上传全走通）；
  2. 一次发布是**一整套四张**：一个版本号、四行记录、四个文件名与跨端契约一致，
     缺方案的餐段也出了占位页 —— 少发一张时设备上留着的是同名旧详情页，到点会照常显示；
  3. 幂等：同一个窗口内被扫到多次，只发一版（否则设备每 5 分钟醒一次下四张整帧）；
  4. 餐次归一：库里存的是 breakfast，模型拿到的必须是「早餐」；
  5. 设备的版式偏好真的生效：同一份菜单在 classic 与 bold 下渲染出的字节必须不同；
  6. `force=True` 能绕过那个幂等窗口：幂等是给每 5 分钟一跳的定时任务用的，
     用它回绝**人的显式操作**（对话里点「用这份菜单」）就是「点了屏幕不变」，
     而接口仍然 200；force 默认必须是 False，否则定时任务会连发六七套；
  7. 用户原话原文进推荐 prompt：只抽关键词桶时「今天想吃点辣的」「想吃川菜」「别放肉」
     一个桶都不命中 → 模型看不到要求，配出来的菜与要求无关而三端都不报错。
     桶要保留（结构化标签与原话互补），原话是**追加**。

用法：python tests/test_scheduler_publish.py
"""

import hashlib
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

# 必须在导入 src.* 之前设好：库与 .film 都落到临时目录，别碰开发机上的真实数据
_tmp = tempfile.mkdtemp(prefix='sfm-sched-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ.setdefault('SFM_API_TOKEN', disposable_token('app-'))
os.environ['SFM_AUTO_PUBLISH'] = '1'
# 只发给这台虚构设备。容器里跑自检时这一条是关键：留空等于「所有已注册设备」，
# 那就会把测试菜单真的发到真机上去。
os.environ['SFM_AUTO_PUBLISH_DEVICES'] = 'devtest01'
os.environ['SFM_AUTO_PUBLISH_LEAD_MIN'] = '30'
# 本文件自己 new 一个 TaskScheduler 来驱动，不需要（也不该）启动全局调度器
os.environ['SFM_ENABLE_SCHEDULER'] = '0'

from datetime import datetime, timedelta  # noqa: E402

from src import database as db            # noqa: E402
from src import device_display            # noqa: E402
from src import meals                     # noqa: E402
from src import menu_render               # noqa: E402
from src import scheduler as sched        # noqa: E402

DEVICE_ID = 'devtest01'
# 第二台设备只为验证「按设备的版式偏好渲染」：与 DEVICE_ID 在同一次发布里拿到
# 同一份菜单数据，唯一的差别是版式，于是字节不同就只能是版式造成的。
DEVICE_ID_BOLD = 'devtest02'
DEVICE_ID_LEGACY = 'devtest03'

# 一份最小可渲染的方案，字段名与 agent.py 约定的 JSON 一致
FAKE_PLAN = {
    'dishes': [
        {'name': '番茄炒蛋', 'cooking_time': '15分钟',
         'ingredients': [{'name': '番茄', 'amount': '2个'}, {'name': '鸡蛋', 'amount': '3个'}]},
        {'name': '蒜蓉上海青', 'cooking_time': '8分钟',
         'ingredients': [{'name': '上海青', 'amount': '300g'}]},
    ]
}


def _install_fake_model(calls: list):
    """把 generate_recommendation 换成假实现：测试不该依赖网络和模型输出。"""
    def fake(meal_type, people_count, user_input=''):
        calls.append({'meal_type': meal_type, 'people_count': people_count})
        return {
            'reply': 'stub',
            'action': 'recommend',
            'data': {'plan_id': 1, 'meal_type': meal_type, 'plan_a': FAKE_PLAN, 'plan_b': None},
        }
    sched.generate_recommendation = fake


def _set_schedule(minutes_ahead: int, meal: str = 'lunch'):
    """把用餐时间改成「距现在 minutes_ahead 分钟」，表里只留这一顿。"""
    target = sched.datetime.now() + timedelta(minutes=minutes_ahead)
    conn = db.get_connection()
    conn.execute('DELETE FROM meal_schedule')
    conn.execute('INSERT INTO meal_schedule (meal_type, meal_time) VALUES (?, ?)',
                 (meal, target.strftime('%H:%M')))
    conn.commit()
    conn.close()


def _latest_version(device_id: str = DEVICE_ID) -> int:
    return device_display.get_sync_status(device_id)['latest_version']


def _version_dir(device_id: str, version: int) -> str:
    return os.path.join(os.environ['SFM_FILM_DIR'], device_id, str(version))


def _read_slot_film(device_id: str, version: int, slot: str) -> bytes:
    """读取某一版里某个槽位的落盘字节。"""
    path = os.path.join(_version_dir(device_id, version),
                        menu_render.SLOT_FILENAMES[slot])
    with open(path, 'rb') as handle:
        return handle.read()


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


def _check_force_validation() -> list:
    """HTTP 层：`POST /api/scheduler/trigger/publish` 的 force 必须是 JSON 布尔。

    在函数里才导入 src.app：本文件其余断言全是直接驱动 TaskScheduler 的，把 Flask app
    提到模块顶部会让它在**所有**断言之前完成导入（含 before_request 鉴权装配），
    与那些断言想验证的东西无关。

    只断言坏值被拒。force=true 那条路径由上面的 trigger_display_publish(force=True)
    直接覆盖 —— 从 HTTP 再发一次真发布只会多产生一个设备版本，证不出新东西。
    """
    from src.app import app                            # noqa: PLC0415

    results = []
    headers = {'Authorization': f"Bearer {os.environ['SFM_API_TOKEN']}"}
    client = app.test_client()

    for bad in ['true', 1, 'yes', None]:
        response = client.post('/api/scheduler/trigger/publish',
                               json={'meal_type': '午餐', 'force': bad},
                               headers=headers)
        results.append(check(
            f'force={bad!r}（非布尔）被拒为 400',
            response.status_code == 400,
            f'实际 {response.status_code} {response.get_json()}'))

    # 不传 force 必须仍然可用：这是既有调用方（老版本 App）的形态。
    # 此刻午餐近期已发过，所以期望的是 200 + skipped，而不是 400。
    response = client.post('/api/scheduler/trigger/publish',
                           json={'meal_type': '午餐'}, headers=headers)
    body = response.get_json() or {}
    results.append(check(
        '不传 force 时接口照旧可用（老客户端不受影响）',
        response.status_code == 200 and body.get('skipped') is True,
        f'实际 {response.status_code} {body}'))

    return results


def _check_user_input_in_prompt() -> list:
    """用户原话必须原文进推荐 prompt。

    放这个文件是因为它与下发同属「对话调菜单」这条链路，且失败同样静默：模型看不到
    「今天想吃点辣的」时接口照样 200、App 侧一切正常，只是配出来的菜与要求无关。

    直接调 build_enhanced_recipe_prompt 断言返回的字符串：这是纯函数，不打模型、
    不碰库，把断言下沉到这一层才能证明「模型确实收到了这句话」。
    """
    from src.agent import build_enhanced_recipe_prompt, USER_INPUT_PROMPT_LIMIT  # noqa: PLC0415

    print('用户原话进推荐 prompt：')
    results = []

    def build(user_input: str) -> str:
        return build_enhanced_recipe_prompt(
            inventory_summary='番茄 × 2个',
            profile_summary='3口人',
            meal_type='晚餐',
            people_count=3,
            user_input=user_input,
        )

    # 四个关键词桶一个都不命中的要求
    spicy = build('今天想吃点辣的，别放肉')
    results.append(check(
        '关键词桶不命中的原话（「今天想吃点辣的，别放肉」）出现在 prompt 里',
        '今天想吃点辣的，别放肉' in spicy,
        'prompt 里找不到用户原话，模型压根看不到这个要求'))
    results.append(check(
        '原话有独立小节标题，模型能认出这是本次的要求',
        '【用户本次的具体要求】' in spicy,
        f'实际 prompt 尾部：{spicy[-400:]!r}'))

    # 桶必须保留：它给的是结构化标签，与原话互补，删掉会让既有行为回退
    light = build('换个清淡的晚餐')
    results.append(check(
        '4 个关键词桶仍然生效（「清淡」→ 结构化标签）',
        '【用户特殊要求】' in light and '清淡少油' in light,
        f'实际 prompt 尾部：{light[-400:]!r}'))
    results.append(check(
        '命中桶时原话**同时**在（追加而不是替换）',
        '换个清淡的晚餐' in light,
        '桶命中时原话被吞掉了'))

    # 空原话不能留一个空标题：那等于让模型自己编一条"用户要求"
    for empty in ['', '   ']:
        prompt = build(empty)
        results.append(check(
            f'原话为 {empty!r} 时不输出这一节',
            '【用户本次的具体要求】' not in prompt,
            '空原话产生了空标题'))

    # 长度上界：几千字会把库存与画像挤出模型的注意力，那才是配菜真正依赖的信息
    long_input = '辣' * (USER_INPUT_PROMPT_LIMIT + 500)
    long_prompt = build(long_input)
    results.append(check(
        f'超长原话被截断到 {USER_INPUT_PROMPT_LIMIT} 字符（含省略号）',
        '辣' * USER_INPUT_PROMPT_LIMIT + '…' in long_prompt
        and long_input not in long_prompt,
        f'prompt 总长 {len(long_prompt)}'))
    results.append(check(
        '截断后库存与画像仍然在 prompt 里（上下文没被挤掉）',
        '番茄 × 2个' in long_prompt and '3口人' in long_prompt,
        '长原话把配菜真正依赖的上下文挤掉了'))

    # 不做过滤 / 改写：判断错了没有任何一处会报错，让模型自己处理
    weird = build('随便吧，看着办 @#$%')
    results.append(check(
        '原话原样进入，不做过滤或改写',
        '随便吧，看着办 @#$%' in weird,
        '原话被改写了'))

    return results


def main() -> int:
    print('每日自动下发画面：')
    results = []
    db.init_db()

    calls = []
    _install_fake_model(calls)
    scheduler = sched.TaskScheduler()

    # ── 默认用餐时间必须存在，否则所有定时任务都是空转 ──
    schedule = db.get_meal_schedule()
    results.append(check(
        '全新库自带默认用餐时间（否则定时任务永不触发）',
        len(schedule) == 3,
        f'实际 {schedule}'))

    # ── 每日更新时间之前：不该发 ──
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 31, 10, 0, tzinfo=tz)

    sched.datetime = FixedDateTime
    _set_schedule(120)
    sched.settings_store.set_value(sched.settings_store.KEY_DAILY_PUBLISH_TIME, '10:30')
    scheduler._publish_meal_display()
    results.append(check(
        '每日更新时间之前不发布',
        _latest_version() == 0,
        f'version={_latest_version()}'))

    # ── 到达每日更新时间：应产生第 1 版 ──
    sched.settings_store.set_value(sched.settings_store.KEY_DAILY_PUBLISH_TIME, '09:30')
    scheduler._publish_meal_display()
    version_after_first = _latest_version()
    results.append(check(
        '每日更新时间后发布出新版本',
        version_after_first == 1,
        f'version={version_after_first}'))

    # 发出去的必须是合法 .film，否则固件会静默拒收、保留旧画面
    downloaded = device_display.download_film(DEVICE_ID, 1)
    if downloaded:
        with open(downloaded[0], 'rb') as f:
            film = f.read()
        valid, err = device_display._validate_film_header(film)
        results.append(check('发布的字节通过 .film 结构校验', valid, err))
        results.append(check(
            '文件长度符合契约 209120',
            len(film) == device_display.FILM_TOTAL_SIZE,
            f'实际 {len(film)}'))
    else:
        results.append(check('发布的字节通过 .film 结构校验', False, '版本 1 的文件不存在'))
        results.append(check('文件长度符合契约 209120', False, '版本 1 的文件不存在'))

    # ── 一次发布 = 一个版本 + 四行 + 四个文件 ──
    # 版本号只能有一个：固件的版本门控是一次整数比较，四张图必须作为一套原子应用。
    status = device_display.get_sync_status(DEVICE_ID)
    expected_slots = sorted(menu_render.SLOT_FILENAMES)
    results.append(check(
        '一个版本携带四个槽位（today + 三餐详情）',
        status['slots'] == expected_slots,
        f"实际 {status['slots']}，期望 {expected_slots}"))

    manifest = device_display.get_manifest(DEVICE_ID)
    items = (manifest or {}).get('items') or []
    results.append(check(
        'manifest items 列出四张',
        len(items) == 4,
        f'实际 {len(items)} 条：{[i.get("slot") for i in items]}'))
    results.append(check(
        'manifest 里的文件名与跨端契约 SLOT_FILENAMES 一致',
        {i['name'] for i in items} == set(menu_render.SLOT_FILENAMES.values()),
        f'实际 {sorted(i.get("name", "") for i in items)}'))
    results.append(check(
        'manifest 主图指向总览（旧固件只认 film_url，那一张得单独看也完整）',
        bool(items) and manifest['film_url'].endswith(menu_render.SLOT_FILENAMES['today']),
        f"实际 {(manifest or {}).get('film_url')}"))

    files_on_disk = sorted(os.listdir(_version_dir(DEVICE_ID, 1)))
    results.append(check(
        '落盘文件名与 SLOT_FILENAMES 一致',
        files_on_disk == sorted(menu_render.SLOT_FILENAMES.values()),
        f'实际 {files_on_disk}'))

    # 只有午餐有方案，早餐 / 晚餐必须各自出一张写着「今日暂无」的占位页：少发一张时设备上
    # 留着的是同名旧详情页，到点会照常显示。四张字节两两不同即证明每一张都是单独渲染的。
    all_films = [_read_slot_film(DEVICE_ID, 1, slot) for slot in menu_render.SLOT_FILENAMES]
    results.append(check(
        '四张图两两不同（缺方案的餐段也出了独立的占位详情页）',
        len({hashlib.sha256(data).hexdigest() for data in all_films}) == 4,
        '存在内容相同的两张图'))
    results.append(check(
        '每一张都通过 .film 结构校验',
        all(device_display._validate_film_header(data)[0] for data in all_films),
        '至少一张不符合固件结构约束'))
    results.append(check(
        'sync-status 的 latest_sha256 确定性地指向总览那一张',
        status['latest_sha256'] == hashlib.sha256(
            _read_slot_film(DEVICE_ID, 1, 'today')).hexdigest(),
        f"实际 {status['latest_sha256']}"))

    # ── 幂等：同一天再扫两次，版本号不能继续涨 ──
    scheduler._publish_meal_display()
    scheduler._publish_meal_display()
    results.append(check(
        '同一天重复扫描不重复发布（24 小时幂等）',
        _latest_version() == 1,
        f'version={_latest_version()}'))
    current_daily_source = sched.layout_publish_source(
        'scheduler:daily',
        device_display.get_device_layout(DEVICE_ID),
        device_display.get_device_layout_options(DEVICE_ID),
    )
    results.append(check(
        '自动下发来源包含当前渲染版本',
        device_display.has_recent_upload(
            DEVICE_ID, current_daily_source, within_hours=24),
        f'没有找到 {current_daily_source} 的上传记录'))

    # ── 渲染器升级：近期旧版上传不能阻止新版画面发布 ──
    # 模拟升级前已在 24 小时内发布过 scheduler:daily。新版第一次应重发，之后仍幂等。
    legacy_items = [
        {'slot': slot, 'data': _read_slot_film(DEVICE_ID, 1, slot)}
        for slot in menu_render.SLOT_FILENAMES
    ]
    legacy_upload = device_display.upload_film_set(
        DEVICE_ID_LEGACY, legacy_items, uploaded_by='scheduler:daily')
    os.environ['SFM_AUTO_PUBLISH_DEVICES'] = DEVICE_ID_LEGACY
    scheduler._publish_meal_display()
    results.append(check(
        '旧渲染来源在幂等窗口内时仍发布当前渲染版本',
        legacy_upload.get('success') is True
        and _latest_version(DEVICE_ID_LEGACY) == 2
        and device_display.has_recent_upload(
            DEVICE_ID_LEGACY, current_daily_source, within_hours=24),
        f'version={_latest_version(DEVICE_ID_LEGACY)}'))
    scheduler._publish_meal_display()
    results.append(check(
        '当前渲染版本发布后重复扫描仍保持幂等',
        _latest_version(DEVICE_ID_LEGACY) == 2,
        f'version={_latest_version(DEVICE_ID_LEGACY)}'))
    os.environ['SFM_AUTO_PUBLISH_DEVICES'] = DEVICE_ID

    # ── 每日轮换：三餐都用中文标签送进模型 ──
    results.append(check(
        '每日轮换送进模型的是早餐/午餐/晚餐三个中文标签',
        [call['meal_type'] for call in calls[:3]] == ['早餐', '午餐', '晚餐'],
        f'实际 {[call["meal_type"] for call in calls[:3]]!r}'))

    # ── 归一函数本身（定义在 src/meals.py，调度器与接口层共用同一份口径）──
    results.append(check(
        '餐次别名归一（中英文 / 未知值）',
        meals.meal_key('breakfast') == 'breakfast'
        and meals.meal_key('晚餐') == 'dinner'
        and meals.meal_key('中餐') == 'lunch'
        and meals.meal_key('夜宵') is None,
        '别名表与预期不符'))

    # ── 版式偏好：同一次发布、同一份菜单，两台设备各按自己的版式渲染 ──
    # 第一台在 24 小时幂等窗口内会跳过；新加入的第二台仍应拿到当天这一版。
    device_display.set_device_layout(DEVICE_ID, 'list', {})
    device_display.set_device_layout(DEVICE_ID_BOLD, 'bold', {})
    os.environ['SFM_AUTO_PUBLISH_DEVICES'] = f'{DEVICE_ID},{DEVICE_ID_BOLD}'
    scheduler._publish_meal_display()

    classic_version = _latest_version(DEVICE_ID)
    bold_version = _latest_version(DEVICE_ID_BOLD)
    results.append(check(
        '版式变化绕过当日幂等并重新发布整套画面',
        classic_version == 2 and bold_version == 1
        and device_display.get_sync_status(DEVICE_ID)['slots'] == expected_slots
        and device_display.get_sync_status(DEVICE_ID_BOLD)['slots'] == expected_slots,
        f'classic v{classic_version} / bold v{bold_version}'))

    if classic_version >= 1 and bold_version >= 1:
        classic_today = _read_slot_film(DEVICE_ID, 1, 'today')
        relaid_today = _read_slot_film(DEVICE_ID, classic_version, 'today')
        bold_today = _read_slot_film(DEVICE_ID_BOLD, bold_version, 'today')
        results.append(check(
            '新版式重渲染后的字节与原版不同（偏好真的生效）',
            classic_today != relaid_today and relaid_today != bold_today,
            '新旧版本或两台设备拿到了相同字节，说明版式偏好未进入渲染'))
    else:
        results.append(check(
            '新版式重渲染后的字节与原版不同（偏好真的生效）',
            False, '版本缺失，无法比较'))

    # 回到单设备白名单，后面的手动触发断言不受第二台设备影响
    os.environ['SFM_AUTO_PUBLISH_DEVICES'] = DEVICE_ID
    # 先走一次旧的按餐次来源，证明老调用方的 6 小时幂等仍然保留。
    scheduler._publish_one_meal('lunch')
    manual_baseline = _latest_version()

    # ── 手动触发入口：跳过要能被区分出来，不能报成成功下发 ──
    sched.task_scheduler = scheduler
    manual = sched.trigger_display_publish('午餐')
    results.append(check(
        '手动触发在已发布过时返回 skipped 而非假成功',
        manual.get('success') is True and manual.get('skipped') is True,
        f'实际 {manual}'))

    manual_bad = sched.trigger_display_publish('宵夜时段')
    results.append(check(
        '手动触发对无法识别的餐次明确报错',
        manual_bad.get('success') is False and 'error' in manual_bad,
        f'实际 {manual_bad}'))

    # 设备物理按键只能刷新自己，不能沿默认目标列表影响同后端的其它屏幕。
    targeted_before = _latest_version(DEVICE_ID_BOLD)
    targeted = sched.trigger_display_publish('午餐', force=True, device_id=DEVICE_ID_BOLD)
    results.append(check(
        '指定 device_id 时只发布到该设备',
        targeted.get('devices') == [DEVICE_ID_BOLD]
        and _latest_version(DEVICE_ID_BOLD) == targeted_before + 1
        and _latest_version(DEVICE_ID) == manual_baseline,
        f'实际 {targeted}'))

    # ── force：人的显式操作必须绕过 6 小时幂等窗口 ──
    # 上面那条 skipped 断言已经证明「午餐近期发过」这个前提成立，此处正是那个前提下
    # 用户在对话里点「用这份菜单」的场景：不 force 就是屏幕不动、接口仍然 200。
    version_before_force = _latest_version()
    forced = sched.trigger_display_publish('午餐', force=True)
    results.append(check(
        'force=True 在近期已下发过时仍然真的发布（人的操作不该被幂等窗口回绝）',
        forced.get('success') is True and not forced.get('skipped')
        and _latest_version() == version_before_force + 1,
        f'实际 {forced}，version {version_before_force} -> {_latest_version()}'))

    # force 默认必须保持 False：定时任务与既有调用方共用这条路径，
    # 默认值一旦翻过去，设备就会在每个餐前窗口连着下六七套 204KB×4。
    still_skipped = sched.trigger_display_publish('午餐')
    results.append(check(
        'force 默认 False：不传时仍按幂等跳过',
        still_skipped.get('skipped') is True,
        f'实际 {still_skipped}'))

    results.append(check(
        'trigger_display_publish 的 force 默认值是 False（定时任务共用这条路径）',
        inspect.signature(sched.trigger_display_publish)
        .parameters['force'].default is False,
        '签名默认值不是 False'))

    # ── HTTP 层：force 必须是 JSON 布尔 ──
    # 与 /api/meal-schedule 的 auto_publish 同一约定。宽松解析（把字符串 "true" 当真）
    # 会让客户端的序列化 bug 变成「本该幂等的调用每次都发」，而调用方看不出差别。
    results.extend(_check_force_validation())

    print()
    results.extend(_check_user_input_in_prompt())

    print()
    if all(results):
        print('scheduler publish self-check passed')
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
    断言逻辑只有一份，脚本直跑（`python tests/test_scheduler_publish.py`）与 pytest 跑的是同一套。
    """
    original_datetime = sched.datetime
    try:
        assert main() == 0, '自检未全部通过，详见上面的逐条输出'
    finally:
        sched.datetime = original_datetime
