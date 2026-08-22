"""跨端契约联调：把 App / 后端 / 固件三方约定的字段形状端到端跑一遍。

为什么单独有这一份，而不是靠各模块的单测：
本轮改动里最贵的几个错误都不在单个模块内部，而在**两端对同一件事的理解不一致**：
  - App 按「三餐时间 − 提前量」算唤醒槽位，固件出厂值按同一个算式硬编码 ——
    两边口径漂移时，用户不改任何设置就会看到「设备醒的时间和 App 说的不一样」。
  - manifest 里 `size` 必须恒为 209120、`sha256` 必须恰 64 位小写十六进制 ——
    固件的解析器会因为一个大写字母整份拒收，而后端这边一切正常。
  - 「取走」与「已上屏」是两个不同的事实，混成一个字段就再也分不出
    「设备没联网」和「联网了但电子纸刷失败」。
这些都只在跨端接缝上出错，且现象都出现在**离原因很远的地方**（冰箱上贴着昨天的菜单）。

不需要真实服务器：用 Flask test_client 打真实端点、真实 SQLite、真实渲染，
所以随常规 pytest 一起跑，不挂 --run-integration。
固件侧不参与执行（需可达 AP + HTTPS 证书），但**固件真正依赖的字段形状**在这里逐条断言。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testsupport import disposable_token  # noqa: E402

os.environ['SFM_API_TOKEN'] = ''

DEVICE = 'dev-contract'
TOKEN = disposable_token('tok-')
AUTH = {'Authorization': f'Bearer {TOKEN}'}

FILM_BYTES = 209120          # 跨端契约：智能冰箱贴 792x528 六色
APP_PARTITION_MAX = 1920 * 1024   # partitions.csv 里 ota_0/ota_1 各 1920K


@pytest.fixture
def env(monkeypatch):
    """独立库 + 独立 film/firmware 目录，并登记设备令牌。"""
    work = tempfile.mkdtemp(prefix='sfm-contract-')
    monkeypatch.setenv('SFM_DB_PATH', os.path.join(work, 'fridge.db'))
    monkeypatch.setenv('SFM_DATABASE_PATH', os.path.join(work, 'fridge.db'))
    monkeypatch.setenv('SFM_FILM_DIR', os.path.join(work, 'devices'))
    monkeypatch.setenv('SFM_FIRMWARE_DIR', os.path.join(work, 'firmware'))
    # test_client 的来源是 http://localhost，而 manifest 端点对 http 的 film_url 回 503。
    monkeypatch.setenv('SFM_ALLOW_INSECURE_BASE_URL', 'true')

    from src import app as app_module
    from src import database, device_display

    database.init_db()
    device_display.init_device_tables()
    device_display.set_device_token(DEVICE, TOKEN)
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as client:
        yield client, work


def _publish_full_set():
    """发布一套四张，返回版本号。"""
    from src import device_display as dd
    from src import menu_render as mr

    items = [{'slot': slot, 'data': mr.render_overview_film(
        title='今日菜单', date_text='3月14日',
        dishes=['早餐|水煮蛋'], layout='columns')}
        for slot in ('today', 'breakfast', 'lunch', 'dinner')]
    result = dd.upload_film_set(DEVICE, items, uploaded_by='contract-test')
    assert result.get('success'), result
    return result['version']


def test_manual_publish_can_target_one_device(env, monkeypatch):
    client, _ = env
    from src import app as app_module

    captured = {}

    def fake_publish(meal_type=None, force=False, device_id=None):
        captured.update(meal_type=meal_type, force=force, device_id=device_id)
        return {'success': True, 'meal': 'dinner', 'devices': [device_id],
                'published': ['四张菜单']}

    monkeypatch.setattr(app_module, 'trigger_display_publish', fake_publish)
    response = client.post('/api/scheduler/trigger/publish', json={
        'meal_type': '晚餐', 'force': True, 'device_id': DEVICE,
    })

    assert response.status_code == 200
    assert captured == {'meal_type': '晚餐', 'force': True, 'device_id': DEVICE}

    for invalid_device_id in (7, '', '   ', None):
        invalid = client.post(
            '/api/scheduler/trigger/publish', json={'device_id': invalid_device_id})
        assert invalid.status_code == 400
        assert invalid.get_json()['code'] == 1001


# ── 三餐时间与提前量：一处填写驱动两端 ────────────────────────────

def test_meal_schedule_drives_both_ends(env):
    client, _ = env

    body = client.put('/api/meal-schedule', json={
        'items': [
            {'meal_type': 'breakfast', 'meal_time': '07:30'},
            {'meal_type': 'lunch', 'meal_time': '12:00'},
            {'meal_type': 'dinner', 'meal_time': '18:30'},
        ],
        'lead_minutes': 30,
    }).get_json()

    assert body['lead_minutes'] == 30
    # 顺序必须是早中晚，不是数据库主键顺序 —— 否则「改了午餐时间，三餐顺序就变了」
    assert [r['meal_type'] for r in body['schedule']] == ['breakfast', 'lunch', 'dinner']

    # App 侧 WakePlanner.computeWakeSlots() 用同一份输入算槽位。这里复算一遍：
    # 两边口径漂移时，用户不改任何设置就会看到「设备醒的时间和 App 说的不一样」。
    times = {r['meal_type']: r['meal_time'] for r in body['schedule']}
    slots = sorted({(int(t[:2]) * 60 + int(t[3:]) - 30) % 1440 for t in times.values()})
    assert slots == [7 * 60, 11 * 60 + 30, 18 * 60], (
        '唤醒槽位必须等于「三餐时间 − 提前量」，与固件出厂三槽同源'
    )


# ── manifest：固件真正依赖的字段形状 ──────────────────────────────

def test_manifest_shape_matches_firmware_parser(env):
    client, _ = env
    from src import device_display as dd

    version = _publish_full_set()
    manifest = client.get(
        f'/api/devices/{DEVICE}/display/manifest',
        headers={**AUTH, 'X-Device-Firmware': '1.0.0', 'X-Device-Battery': '88'},
    ).get_json()

    assert manifest['version'] == version
    assert manifest['size'] == FILM_BYTES, '固件按固定长度校验，差一字节整份拒收'
    assert len(manifest['items']) == 4, '一套四张少发一张会让设备显示上一版的同名图'
    assert {i['name'] for i in manifest['items']} == set(dd.slot_filenames().values())

    for item in manifest['items']:
        assert item['size'] == FILM_BYTES
        # 固件的 manifest_is_lower_hex64() 要求恰 64 位小写；一个大写字母就整份拒收，
        # 而后端这边看起来完全正常。
        assert len(item['sha256']) == 64
        assert item['sha256'] == item['sha256'].lower()
        assert item['url'].startswith(('http://', 'https://'))

    # 设备自报状态搭在这次请求上，不额外唤醒设备
    assert (dd.get_device_status(DEVICE) or {}).get('battery_level') == 88


# ── 「取走」与「已上屏」是两个事实 ────────────────────────────────

def test_downloaded_and_applied_are_distinct_facts(env):
    client, _ = env
    from src import device_display as dd

    version = _publish_full_set()
    film = client.get(
        f'/api/devices/{DEVICE}/display/{version}/{dd.slot_filenames()["today"]}',
        headers=AUTH)
    assert film.status_code == 200
    assert len(film.data) == FILM_BYTES
    film.close()   # send_file 的响应持有打开的文件对象

    only_downloaded = client.get(f'/api/devices/{DEVICE}/link-check').get_json()
    assert only_downloaded['checks']['device_downloaded_version'] == version
    assert only_downloaded['checks']['device_applied_version'] < version
    assert '尚未回报上屏结果' in only_downloaded['action']

    applied = client.post(f'/api/devices/{DEVICE}/display/applied',
                          json={'version': version, 'ok': True}, headers=AUTH).get_json()
    assert applied['event'] == 'apply'
    after = client.get(f'/api/devices/{DEVICE}/link-check').get_json()
    assert after['verdict'] == 'ok'
    assert '成功上屏' in after['action']


def test_display_failure_is_visible_and_blamed_on_the_device(env):
    """上屏失败必须能被指出来，而不是被 download 的「正常」盖住。

    否则冰箱上贴着旧菜单，而运维面板显示一切正常。
    """
    client, _ = env
    version = _publish_full_set()

    reported = client.post(
        f'/api/devices/{DEVICE}/display/applied',
        json={'version': version, 'ok': False, 'detail': 'epd timeout'},
        headers=AUTH).get_json()
    assert reported['event'] == 'apply_failed'
    assert reported['force_cleared'] is False, '上屏失败不能清 force，下次唤醒要重试'

    body = client.get(f'/api/devices/{DEVICE}/link-check').get_json()
    assert body['verdict'] == 'blocked'
    assert '上屏失败' in body['action']
    assert '电子纸' in body['action'] or '设备侧' in body['action'], '要指明是设备侧那一跳'


# ── Wi-Fi OTA：manifest 附带固件 ─────────────────────────────────

def _write_firmware(work, name, size=4096):
    directory = os.path.join(work, 'firmware')
    os.makedirs(directory, exist_ok=True)
    version = name.removeprefix('sfm-').removesuffix('.bin')
    data = bytearray(os.urandom(size))
    data[0] = 0xe9
    data[32:36] = (0xABCD5432).to_bytes(4, 'little')
    data[48:80] = version.encode()[:31].ljust(32, b'\0')
    data[80:112] = b'smart_fridge_magnet'.ljust(32, b'\0')
    with open(os.path.join(directory, name), 'wb') as handle:
        handle.write(data)


def test_manifest_carries_firmware_only_when_newer(env):
    client, work = env
    _publish_full_set()
    _write_firmware(work, 'sfm-2.0.0.bin')

    newer = client.get(f'/api/devices/{DEVICE}/display/manifest',
                       headers={**AUTH, 'X-Device-Firmware': '1.0.0'}).get_json()
    fw = newer['firmware']
    assert fw['version'] == '2.0.0'
    assert fw['url'].endswith('/api/firmware/sfm-2.0.0.bin')
    assert 0 < fw['size'] <= APP_PARTITION_MAX, (
        '超过 app 分区容量的固件不该下发：设备下载到最后才失败，白耗一次射频'
    )
    assert len(fw['sha256']) == 64 and fw['sha256'] == fw['sha256'].lower()

    # 已是最新时整个字段不出现，客户端只需判断「有没有这个字段」
    same = client.get(f'/api/devices/{DEVICE}/display/manifest',
                      headers={**AUTH, 'X-Device-Firmware': '2.0.0'}).get_json()
    assert 'firmware' not in same

    # 旧固件不带 X-Device-Firmware 头：无法判断新旧，不推送
    # （盲推等于让每次唤醒都下载一遍近 2MB，而设备靠电池）
    legacy = client.get(f'/api/devices/{DEVICE}/display/manifest', headers=AUTH).get_json()
    assert 'firmware' not in legacy


def test_firmware_field_never_pushes_manifest_over_device_limit(env, monkeypatch):
    client, _ = env
    from src import app as app_module
    from src import device_display as dd

    manifest = {
        'version': 1,
        'film_url': 'https://example.com/1.film',
        'size': FILM_BYTES,
        'sha256': 'a' * 64,
        'force': False,
        'padding': 'x' * 1800,
    }
    monkeypatch.setattr(app_module, 'get_manifest', lambda *_args, **_kwargs: manifest.copy())
    monkeypatch.setattr(app_module.firmware, 'check_update', lambda *_args, **_kwargs: {
        'update_available': True,
        'latest_version': '2.0.0',
        'url': 'https://example.com/api/firmware/sfm-2.0.0.bin',
        'size': 4096,
        'sha256': 'b' * 64,
    })

    response = client.get(
        f'/api/devices/{DEVICE}/display/manifest',
        headers={**AUTH, 'X-Device-Firmware': '1.0.0'},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert 'firmware' not in body
    assert len(response.data) <= dd.MANIFEST_MAX_RESPONSE_BYTES


def test_firmware_download_requires_a_device_token(env):
    client, work = env
    _write_firmware(work, 'sfm-2.0.0.bin')

    assert client.get('/api/firmware/sfm-2.0.0.bin').status_code == 401
    ok = client.get('/api/firmware/sfm-2.0.0.bin', headers=AUTH)
    assert ok.status_code == 200
    ok.close()


def test_firmware_ota_client_installs_auth_and_integrity_checks(env):
    source = (Path(__file__).resolve().parents[2] / 'embedded' /
              'smart_fridge_magnet' / 'components' / 'film_service' / 'src' /
              'service_wifi_ota.c').read_text(encoding='utf-8')

    assert '.http_client_init_cb = ota_http_client_init' in source
    assert 'esp_http_client_set_header(client, "Authorization", auth)' in source
    assert 'esp_https_ota_get_img_desc(handle, &candidate)' in source
    assert 'ota_partition_sha256(target' in source
    assert 'memcmp(expected_sha256, actual_sha256' in source


# ── 版式：默认分栏 + 旧键仍可用 ──────────────────────────────────

def test_layout_defaults_to_columns_and_old_keys_still_work(env):
    client, _ = env
    from src import menu_render as mr

    assert mr.DEFAULT_LAYOUT == 'columns'
    assert mr.RENDER_REVISION == 5, (
        '改动版式输出必须递增渲染版本号：调度器把它纳入发布幂等键，'
        '不递增则升级后复用旧 .film，「改了没生效」最难查'
    )

    # 库里存着用户在旧版本选过的偏好；接口必须接受并归一后落库
    put = client.put(f'/api/devices/{DEVICE}/prefs', json={'layout': 'bold'})
    assert put.status_code == 200
    assert put.get_json()['layout'] == 'list_big'

    customized = client.put(f'/api/devices/{DEVICE}/prefs', json={
        'layout_options': {
            'accent': 'green',
            'font_size': 'large',
            'density': 'compact',
            'separator': 'rail',
        }
    })
    assert customized.status_code == 200
    assert customized.get_json()['layout_options'] == {
        'accent': 'green',
        'font_size': 'large',
        'density': 'compact',
        'separator': 'rail',
    }

    invalid = client.put(f'/api/devices/{DEVICE}/prefs', json={
        'layout_options': {'accent': 'purple'}
    })
    assert invalid.status_code == 400


def test_layout_change_republishes_existing_menu_set(env, monkeypatch):
    client, _ = env
    from src import app as app_module
    from src import device_display as dd

    _publish_full_set()
    calls = []

    def publish(meal_type=None, force=False, device_id=None):
        calls.append((meal_type, force, device_id, dd.get_device_layout(device_id)))
        return {
            'success': True,
            'devices': [device_id],
            'published': [f'published four slots for {device_id}'],
        }

    monkeypatch.setattr(app_module, 'trigger_display_publish', publish)

    changed = client.put(f'/api/devices/{DEVICE}/prefs', json={'layout': 'list_big'})
    body = changed.get_json()
    assert changed.status_code == 200
    assert body['layout'] == 'list_big'
    assert body['display_updated'] is True
    assert calls == [(None, True, DEVICE, 'list_big')], (
        '版式必须先落库，再仅为当前设备强制发布完整菜单套图'
    )

    unchanged = client.put(f'/api/devices/{DEVICE}/prefs', json={'layout': 'list_big'})
    assert unchanged.status_code == 200
    assert unchanged.get_json()['display_updated'] is False
    assert len(calls) == 1, '版式未变化时不得产生无意义的新菜单版本'

    fresh_device = f'{DEVICE}-fresh'
    first_choice = client.put(
        f'/api/devices/{fresh_device}/prefs', json={'layout': 'portrait'})
    assert first_choice.status_code == 200
    assert first_choice.get_json()['display_updated'] is False
    assert len(calls) == 1, '从未发布过菜单的设备只需保存偏好，首次发布会直接采用它'

    monkeypatch.setattr(
        app_module,
        'trigger_display_publish',
        lambda *args, **kwargs: {'success': False, 'error': 'render failed'},
    )
    failed = client.put(f'/api/devices/{DEVICE}/prefs', json={
        'layout_options': {'accent': 'green'},
    })
    failed_body = failed.get_json()
    assert failed.status_code == 500
    assert failed_body['preferences_saved'] is False
    assert failed_body['layout_options']['accent'] == 'meal'
    assert dd.get_device_layout_options(DEVICE)['accent'] == 'meal'
    assert 'render failed' in failed_body['error']

    retry_calls = []
    monkeypatch.setattr(
        app_module,
        'trigger_display_publish',
        lambda *args, **kwargs: retry_calls.append((args, kwargs)) or {'success': True},
    )
    retried = client.put(f'/api/devices/{DEVICE}/prefs', json={
        'layout_options': {'accent': 'green'},
    })
    assert retried.status_code == 200
    assert retried.get_json()['display_updated'] is True
    assert retry_calls, '发布失败并恢复旧偏好后，相同请求必须能够重试'


def test_columns_layout_renders_device_sized_output(env):
    from src import menu_render as mr

    image = mr.render_overview(
        title='今日菜单', date_text='3月14日',
        dishes=['早餐|燕麦牛奶粥', '午餐|番茄炒蛋', '晚餐|香菇鸡肉焖饭'],
        expiring='牛奶 明天到期', layout='columns',
        meal_times={'早餐': '07:30', '午餐': '12:00', '晚餐': '18:30'},
        details={'番茄炒蛋': '番茄 · 鸡蛋 · 葱'},
    )

    assert image.size == (mr.WIDTH, mr.HEIGHT)
    assert len(mr.image_to_film(image)) == FILM_BYTES
