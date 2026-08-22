"""Open-source release security regressions at HTTP and storage boundaries."""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = tempfile.mkdtemp(prefix='sfm-release-security-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

import pytest  # noqa: E402

from src import app as app_module  # noqa: E402
from src import database as db  # noqa: E402
from src import device_display  # noqa: E402
from src.image_processor import MAX_FILE_SIZE  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('SFM_API_TOKEN', '')
    app_module.app.config['TESTING'] = True
    db.init_db()
    device_display.init_device_tables()
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.mark.parametrize('path', ['/api/recognize', '/api/recognize/check'])
def test_recognition_upload_is_rejected_before_processing(client, monkeypatch, path):
    called = False

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError('oversized upload reached image processing')

    monkeypatch.setattr(app_module, 'process_photo', should_not_run)
    response = client.post(
        path,
        data={'image': (io.BytesIO(b'x' * (MAX_FILE_SIZE + 1)), 'large.jpg')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 413
    assert called is False


def test_recognition_error_does_not_echo_internal_exception(client, monkeypatch):
    marker = '/private/runtime/path with provider details'

    def fail(_path, auto_save=True):
        raise RuntimeError(marker)

    monkeypatch.setattr(app_module, 'process_photo', fail)
    response = client.post(
        '/api/recognize',
        data={'image': (io.BytesIO(b'not-an-image'), 'photo.jpg')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 500
    assert marker not in response.get_data(as_text=True)


def test_client_photo_path_is_not_persisted(client):
    response = client.post('/api/inventory', json={
        'name': '路径边界测试苹果',
        'category': '水果',
        'quantity': 1,
        'unit': '个',
        'photo_path': '/etc/passwd',
    })
    assert response.status_code == 201

    conn = db.get_connection()
    try:
        row = conn.execute(
            'SELECT photo_path FROM fridge_inventory WHERE id = ?',
            (response.get_json()['item_id'],),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None


def test_device_token_is_hashed_and_still_authenticates(client):
    token = 'a' * 32
    assert device_display.set_device_token('release-device', token)['success']

    conn = db.get_connection()
    try:
        stored = conn.execute(
            'SELECT device_token FROM devices WHERE device_id = ?',
            ('release-device',),
        ).fetchone()[0]
    finally:
        conn.close()

    assert stored != token
    assert stored.startswith('sha256:')
    assert device_display.device_token_matches('release-device', token)
    assert not device_display.device_token_matches('release-device', 'b' * 32)


def test_weak_device_token_is_rejected(client):
    result = device_display.set_device_token('weak-device', 'public-demo-token')
    assert result['success'] is False
