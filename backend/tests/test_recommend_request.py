"""POST /api/recommend 的餐次边界与生成入口回归测试。"""
import os
import sys
import tempfile


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-recommend-request-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import app as app_module  # noqa: E402


def test_recommend_normalizes_english_meal_type(monkeypatch):
    seen = []
    monkeypatch.setattr(
        app_module,
        'chat',
        lambda message: seen.append(message) or {'action': 'recommend'},
    )

    response = app_module.app.test_client().post('/api/recommend', json={
        'meal_type': 'dinner',
        'people_count': 3,
    })

    assert response.status_code == 200
    assert seen == ['推荐晚餐，3人']


def test_recommend_rejects_unknown_meal_type(monkeypatch):
    monkeypatch.setattr(
        app_module,
        'chat',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('非法餐次不应进入模型')
        ),
    )

    response = app_module.app.test_client().post('/api/recommend', json={
        'meal_type': 'midnight-snack',
        'people_count': 3,
    })

    assert response.status_code == 400
    assert response.get_json()['code'] == 1001
