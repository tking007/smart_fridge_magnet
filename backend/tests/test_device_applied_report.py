"""设备上屏结果回报（POST /api/devices/<id>/display/applied）。

覆盖的核心区别是「取走 ≠ 已上屏」：设备把 .film 下载下来之后还有一跳异步渲染，
那一跳失败时若后端看不见，运维面板会显示一切正常而冰箱上贴着旧菜单。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testsupport import disposable_token  # noqa: E402

# 业务接口（link-check）留空放行；设备令牌另有一条鉴权分支，不受此影响。
os.environ['SFM_API_TOKEN'] = ''

DEVICE_ID = 'devapplied01'
DEVICE_TOKEN = disposable_token('tok-')
AUTH = {'Authorization': f'Bearer {DEVICE_TOKEN}'}


@pytest.fixture
def client(monkeypatch):
    """独立库 + 独立 film 目录的测试客户端，并登记 device_token。

    必须登记令牌：上报端点走的是 device_token 分支（设备侧写操作），
    不带令牌一律 401 —— 那也是这个端点应有的行为。
    """
    workdir = tempfile.mkdtemp(prefix='sfm-applied-')
    monkeypatch.setenv('SFM_DB_PATH', os.path.join(workdir, 'test.db'))
    monkeypatch.setenv('SFM_FILM_DIR', os.path.join(workdir, 'devices'))
    # test_client 的请求来源是 http://localhost，link-check 会把「film_url 不是 https」
    # 当成最优先的阻塞项报出来，盖住本文件真正要验的上屏结论。用 monkeypatch 而不是
    # 模块级 os.environ：后者会泄漏到同进程里后跑的其它测试文件。
    monkeypatch.setenv('SFM_ALLOW_INSECURE_BASE_URL', 'true')

    # 刻意**不**清 sys.modules 重载 src.*：db 路径与 film 目录都是调用时才读环境变量
    # （见 database.py 里那段说明），无需重载。而重载会换掉 src.agent 等模块对象，
    # 让同进程里其它测试文件在模块级持有的引用与 app 实际用的不是同一个，
    # 那些文件的 monkeypatch 打桩会静默失效 —— 表现成「单独跑通过、全量跑失败」。
    from src import app as app_module
    from src import database, device_display

    database.init_db()
    device_display.init_device_tables()
    device_display.set_device_token(DEVICE_ID, DEVICE_TOKEN)

    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def _publish_one_version():
    """发布一版画面，返回版本号。"""
    from src import device_display, menu_render

    data = menu_render.render_overview_film(
        title='今日菜单', date_text='3月14日', dishes=['早餐|水煮蛋'])
    result = device_display.upload_film_set(
        DEVICE_ID, [{'slot': 'today', 'data': data}], uploaded_by='test')
    assert result.get('success'), result
    return result['version']


def _simulate_download(version):
    """走真实下载路径产生 download 事件（而不是手工插日志）。"""
    from src import device_display
    assert device_display.download_film(DEVICE_ID, version) is not None


def test_report_requires_device_token(client):
    """不带令牌的上报必须 401：这个端点会写库，不能裸奔。"""
    version = _publish_one_version()

    resp = client.post(f'/api/devices/{DEVICE_ID}/display/applied',
                       json={'version': version, 'ok': True})

    assert resp.status_code == 401


def test_success_report_records_apply_event(client):
    version = _publish_one_version()

    resp = client.post(f'/api/devices/{DEVICE_ID}/display/applied',
                       json={'version': version, 'ok': True}, headers=AUTH)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['event'] == 'apply'

    from src import device_display
    events = device_display.get_sync_status(DEVICE_ID).get('recent_events') or []
    assert any(e['event'] == 'apply' and e['version'] == version for e in events)


def test_failure_report_is_visible_and_keeps_force(client):
    """上屏失败必须留痕，且不能清 force —— 下次唤醒还要重试。"""
    version = _publish_one_version()
    from src import device_display
    device_display.set_force_next(DEVICE_ID)

    resp = client.post(f'/api/devices/{DEVICE_ID}/display/applied',
                       json={'version': version, 'ok': False, 'detail': 'epd timeout'},
                       headers=AUTH)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['event'] == 'apply_failed'
    assert body['force_cleared'] is False

    events = device_display.get_sync_status(DEVICE_ID).get('recent_events') or []
    failed = [e for e in events if e['event'] == 'apply_failed']
    assert failed and failed[0]['version'] == version
    # force 仍在：下一次 manifest 还会带 force=true，设备会重试上屏
    manifest = device_display.get_manifest(DEVICE_ID, request_base_url='https://x')
    assert manifest['force'] is True


def test_link_check_separates_downloaded_from_applied(client):
    """sync-status 必须把「取走」和「已上屏」分开报，不能混成一个结论。"""
    version = _publish_one_version()
    _simulate_download(version)

    only_downloaded = client.get(f'/api/devices/{DEVICE_ID}/link-check').get_json()
    assert only_downloaded['checks']['device_downloaded_version'] == version
    assert only_downloaded['checks']['device_applied_version'] < version
    assert '尚未回报上屏结果' in only_downloaded['action']

    client.post(f'/api/devices/{DEVICE_ID}/display/applied',
                json={'version': version, 'ok': True}, headers=AUTH)

    applied = client.get(f'/api/devices/{DEVICE_ID}/link-check').get_json()
    assert applied['checks']['device_applied_version'] == version
    assert applied['verdict'] == 'ok'
    assert '成功上屏' in applied['action']


def test_apply_failure_makes_sync_status_blocked(client):
    """上屏失败要能在诊断结论里被指出来，而不是被 download 的「正常」盖住。"""
    version = _publish_one_version()
    _simulate_download(version)
    client.post(f'/api/devices/{DEVICE_ID}/display/applied',
                json={'version': version, 'ok': False, 'detail': 'epd timeout'},
                headers=AUTH)

    body = client.get(f'/api/devices/{DEVICE_ID}/link-check').get_json()

    assert body['verdict'] == 'blocked'
    assert '上屏失败' in body['action']
    assert body['checks']['device_apply_failed_version'] == version


@pytest.mark.parametrize('payload', [
    {},                          # 没有 version
    {'version': 'abc'},          # 非数字
    {'version': 0},              # 0 不是有效版本
    {'version': -1},
])
def test_bad_version_returns_400(client, payload):
    resp = client.post(f'/api/devices/{DEVICE_ID}/display/applied',
                       json=payload, headers=AUTH)
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False
