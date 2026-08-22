"""固件托管与 Wi-Fi OTA 版本判定。

最要紧的一条是版本比较不能用字符串：字符串下 "0.9.0" > "0.10.0"（'9' > '1'），
于是升到 0.9.0 的设备再也收不到 0.10.0，而这个错误不报任何异常 ——
它表现成「某些设备就是不升级」，且只在跨十位版本时出现。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testsupport import disposable_token  # noqa: E402

os.environ['SFM_API_TOKEN'] = ''

DEVICE_ID = 'devota01'
DEVICE_TOKEN = disposable_token('tok-')
AUTH = {'Authorization': f'Bearer {DEVICE_TOKEN}'}
REJECTED_AUTH = {'Authorization': f"Bearer {disposable_token('rejected-')}"}


@pytest.fixture
def fw_dir(monkeypatch):
    """空的固件目录。"""
    directory = tempfile.mkdtemp(prefix='sfm-fw-')
    monkeypatch.setenv('SFM_FIRMWARE_DIR', directory)
    return directory


def _write_firmware(directory, name, size=1024):
    path = os.path.join(directory, name)
    match = name.removeprefix('sfm-').removesuffix('.bin')
    data = bytearray(os.urandom(size))
    if size >= 112:
        data[0] = 0xe9
        data[32:36] = (0xABCD5432).to_bytes(4, 'little')
        data[48:80] = match.encode()[:31].ljust(32, b'\0')
        data[80:112] = b'smart_fridge_magnet'.ljust(32, b'\0')
    with open(path, 'wb') as handle:
        handle.write(data)
    return path


# ── 版本解析与比较 ────────────────────────────────────────────────

@pytest.mark.parametrize('text,expected', [
    ('1.2.3', (1, 2, 3)),
    ('v1.2.3', (1, 2, 3)),
    ('0.10.0', (0, 10, 0)),
    ('1', (1,)),
    ('1.2.3.4', (1, 2, 3, 4)),
    ('1.2.3-rc1', (1, 2, 3)),      # 后缀不参与比较
    ('  1.0.0  ', (1, 0, 0)),
    ('', None),
    (None, None),
    ('abc', None),
])
def test_parse_version(fw_dir, text, expected):
    from src import firmware
    assert firmware.parse_version(text) == expected


def test_double_digit_version_beats_single_digit(fw_dir):
    """0.10.0 必须大于 0.9.0 —— 字符串比较会判反。"""
    from src import firmware
    assert firmware.parse_version('0.10.0') > firmware.parse_version('0.9.0')
    assert firmware.parse_version('1.0.0') > firmware.parse_version('0.99.99')


# ── 列出与筛选 ────────────────────────────────────────────────────

def test_list_sorts_newest_first_and_ignores_foreign_files(fw_dir):
    from src import firmware
    _write_firmware(fw_dir, 'sfm-0.9.0.bin')
    _write_firmware(fw_dir, 'sfm-0.10.0.bin')
    _write_firmware(fw_dir, 'sfm-1.0.0.bin')
    _write_firmware(fw_dir, 'readme.txt')            # 非固件
    _write_firmware(fw_dir, 'bootloader.bin')        # 名字不合格式

    listed = firmware.list_firmware()

    assert [item['version'] for item in listed] == ['1.0.0', '0.10.0', '0.9.0']
    assert firmware.latest_firmware()['version'] == '1.0.0'


def test_oversized_firmware_is_not_offered(fw_dir):
    """超过 app 分区容量的文件不列出：设备下载到最后才发现刷不进去，白耗电量。"""
    from src import firmware
    _write_firmware(fw_dir, 'sfm-1.0.0.bin', size=firmware.MAX_FIRMWARE_BYTES + 1)
    _write_firmware(fw_dir, 'sfm-0.9.0.bin', size=2048)

    assert [item['version'] for item in firmware.list_firmware()] == ['0.9.0']


def test_empty_firmware_is_not_offered(fw_dir):
    from src import firmware
    open(os.path.join(fw_dir, 'sfm-1.0.0.bin'), 'wb').close()
    assert firmware.list_firmware() == []


def test_misnamed_or_foreign_firmware_is_not_offered(fw_dir):
    from src import firmware
    path = _write_firmware(fw_dir, 'sfm-1.0.0.bin')
    with open(path, 'r+b') as handle:
        handle.seek(48)
        handle.write(b'0.9.0'.ljust(32, b'\0'))
    _write_firmware(fw_dir, 'sfm-0.8.0.bin')
    with open(os.path.join(fw_dir, 'sfm-0.8.0.bin'), 'r+b') as handle:
        handle.seek(80)
        handle.write(b'other_project'.ljust(32, b'\0'))

    assert firmware.list_firmware() == []


# ── 升级判定 ──────────────────────────────────────────────────────

def test_update_offered_only_when_strictly_newer(fw_dir):
    from src import firmware
    _write_firmware(fw_dir, 'sfm-1.2.0.bin')

    newer = firmware.check_update('1.1.0', base_url='https://x')
    assert newer['update_available'] is True
    assert newer['latest_version'] == '1.2.0'
    assert newer['url'] == 'https://x/api/firmware/sfm-1.2.0.bin'
    assert len(newer['sha256']) == 64

    same = firmware.check_update('1.2.0', base_url='https://x')
    assert same['update_available'] is False

    # 不做强制降级：要回滚有问题的发布，正确做法是发一个版本号更高的修复版。
    older_backend = firmware.check_update('2.0.0', base_url='https://x')
    assert older_backend['update_available'] is False


def test_device_without_reported_version_is_not_pushed(fw_dir):
    """设备没报版本时不推送：盲推等于每次唤醒都下载 1.7MB，而设备靠电池。"""
    from src import firmware
    _write_firmware(fw_dir, 'sfm-1.0.0.bin')

    result = firmware.check_update(None, base_url='https://x')

    assert result['update_available'] is False
    assert '未上报固件版本' in result['reason']


def test_no_firmware_on_backend(fw_dir):
    from src import firmware
    result = firmware.check_update('1.0.0', base_url='https://x')
    assert result['update_available'] is False
    assert result['latest_version'] is None


# ── 路径安全 ──────────────────────────────────────────────────────

@pytest.mark.parametrize('name', [
    '../../etc/passwd',
    '../sfm-1.0.0.bin',
    'sfm-1.0.0.bin/../../secret',
    'sfm-1.0.0.txt',
    'evil.bin',
    '',
])
def test_firmware_path_rejects_traversal_and_bad_names(fw_dir, name):
    from src import firmware
    assert firmware.firmware_path(name) is None


# ── 端点 ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, fw_dir):
    workdir = tempfile.mkdtemp(prefix='sfm-ota-')
    monkeypatch.setenv('SFM_DB_PATH', os.path.join(workdir, 'test.db'))
    monkeypatch.setenv('SFM_FILM_DIR', os.path.join(workdir, 'devices'))
    # test_client 的来源是 http://localhost，而 manifest 端点对 http 的 film_url 回 503
    # （固件强制 HTTPS）。用 monkeypatch 而非模块级 os.environ：后者会泄漏到后跑的测试文件。
    monkeypatch.setenv('SFM_ALLOW_INSECURE_BASE_URL', 'true')

    from src import app as app_module
    from src import database, device_display

    database.init_db()
    device_display.init_device_tables()
    device_display.set_device_token(DEVICE_ID, DEVICE_TOKEN)

    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_download_requires_a_registered_device_token(client, fw_dir):
    _write_firmware(fw_dir, 'sfm-1.0.0.bin')

    assert client.get('/api/firmware/sfm-1.0.0.bin').status_code == 401
    assert client.get('/api/firmware/sfm-1.0.0.bin',
                      headers=REJECTED_AUTH).status_code == 401

    ok = client.get('/api/firmware/sfm-1.0.0.bin', headers=AUTH)
    assert ok.status_code == 200
    assert len(ok.data) == 1024
    # send_file() 的响应持有一个打开的文件对象，不显式关闭会留下 ResourceWarning。
    # 在测试里无害，但把真实的句柄泄漏淹没在噪音里 —— 那种泄漏在长跑的服务上是真问题。
    ok.close()


def test_download_missing_firmware_returns_404(client, fw_dir):
    resp = client.get('/api/firmware/sfm-9.9.9.bin', headers=AUTH)
    assert resp.status_code == 404


def test_latest_endpoint_lists_available(client, fw_dir):
    _write_firmware(fw_dir, 'sfm-1.0.0.bin')
    _write_firmware(fw_dir, 'sfm-0.9.0.bin')

    body = client.get('/api/firmware/latest').get_json()

    assert body['success'] is True
    assert body['latest']['version'] == '1.0.0'
    assert len(body['available']) == 2



def _publish_one_version():
    from src import device_display, menu_render
    data = menu_render.render_overview_film(title='今日菜单', dishes=['早餐|水煮蛋'])
    result = device_display.upload_film_set(
        DEVICE_ID, [{'slot': 'today', 'data': data}], uploaded_by='test')
    assert result.get('success'), result
    return result['version']


def test_manifest_carries_firmware_when_newer_exists(client, fw_dir):
    """升级检查搭在设备本就会发的 manifest 请求上，不让设备再多醒一次拉别的接口。"""
    _publish_one_version()
    _write_firmware(fw_dir, 'sfm-2.0.0.bin')

    manifest = client.get(
        f'/api/devices/{DEVICE_ID}/display/manifest',
        headers={**AUTH, 'X-Device-Firmware': '1.0.0'},
    ).get_json()

    assert manifest['firmware']['version'] == '2.0.0'
    assert manifest['firmware']['url'].endswith('/api/firmware/sfm-2.0.0.bin')
    assert manifest['firmware']['size'] == 1024
    assert len(manifest['firmware']['sha256']) == 64


def test_manifest_omits_firmware_when_up_to_date(client, fw_dir):
    """已是最新时整个字段不出现，而不是给一个 update_available=false 的空壳。

    设备侧因此只需判断「有没有这个字段」，不必再解析一层状态。
    """
    _publish_one_version()
    _write_firmware(fw_dir, 'sfm-1.0.0.bin')

    manifest = client.get(
        f'/api/devices/{DEVICE_ID}/display/manifest',
        headers={**AUTH, 'X-Device-Firmware': '1.0.0'},
    ).get_json()

    assert 'firmware' not in manifest
    # manifest 本身的契约不受影响
    assert manifest['size'] == 209120


def test_manifest_unaffected_when_device_reports_no_firmware(client, fw_dir):
    """旧固件不带 X-Device-Firmware 头，manifest 必须照常返回、不附升级。"""
    _publish_one_version()
    _write_firmware(fw_dir, 'sfm-2.0.0.bin')

    manifest = client.get(f'/api/devices/{DEVICE_ID}/display/manifest',
                          headers=AUTH).get_json()

    assert 'firmware' not in manifest
    assert manifest['size'] == 209120
