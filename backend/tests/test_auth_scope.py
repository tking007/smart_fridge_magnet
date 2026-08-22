#!/usr/bin/env python3
"""免鉴权范围：只有非 API 的 `/healthz` 可以不带 token。

为什么钉这一条：豁免原本是**前缀**匹配 `('/api/health',)`，而 `/api/health` 下挂了 7 个子路由，
于是任何能访问后端的人都能：
  - `GET /api/health/api` → 让后端替他打两次真实模型调用（各上限 120 秒）。反复请求即可烧掉
    API 额度；又因 gunicorn 是 `--workers 1 --threads 8`，8 个并发就能把全部工作线程挂住两
    分钟，期间设备拉 manifest 与 App 所有接口一起排队 —— 一次零成本的 DoS。
  - `GET /api/health/profile` → 拿到全部家庭成员的真实姓名。
  - `GET /api/health/database` → 拿到表名、每张表的行数、SQLite 文件的绝对路径。

容器探活只用 `/healthz`，API 诊断路由本来就不需要公开。前缀匹配是「顺手写法」，
而它放开的范围远超注释声称的意图 —— 这类偏差不会有任何测试或报错提醒。

用法：backend/.venv/bin/python -m pytest tests/test_auth_scope.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

APP_TOKEN = disposable_token('app-')

_tmp = tempfile.mkdtemp(prefix='sfm-authscope-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'
# 必须设 token，否则整个鉴权层被跳过（未设 token = 本机开发模式），这个测试就成了空转。
os.environ['SFM_API_TOKEN'] = APP_TOKEN

import pytest                               # noqa: E402

from src import database as db               # noqa: E402

db.init_db()


@pytest.fixture
def client():
    from src.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


#: 会真正产生外部成本或泄露内部信息的诊断子路由。
_MUST_BE_PROTECTED = [
    '/api/health/api',        # 会打两次真实模型
    '/api/health/profile',    # 成员姓名
    '/api/health/database',   # 表名、行数、DB 绝对路径
    '/api/health/inventory',
    '/api/health/nutrition',
]


@pytest.mark.parametrize('path', _MUST_BE_PROTECTED)
def test_health_subroutes_require_token(client, path):
    assert client.get(path).status_code == 401, \
        f'{path} 必须鉴权：它有真实外部成本或会泄露内部信息'


@pytest.mark.parametrize('path', _MUST_BE_PROTECTED)
def test_health_subroutes_pass_with_token(client, path):
    """带上 token 后仍要可用 —— 收紧鉴权不等于把诊断能力删掉。"""
    resp = client.get(path, headers={'Authorization': f'Bearer {APP_TOKEN}'})
    assert resp.status_code != 401, f'{path} 带 token 后不应再被拒'


def test_liveness_probe_stays_public(client):
    """轻量存活探针不依赖凭据，容器可直接使用。"""
    assert client.get('/healthz').status_code == 200


def test_diagnostic_health_requires_token(client):
    """诊断摘要包含数据库、库存与调度状态，必须与其它业务接口一样鉴权。"""
    assert client.get('/api/health').status_code == 401
    response = client.get(
        '/api/health', headers={'Authorization': f'Bearer {APP_TOKEN}'}
    )
    assert response.status_code == 200


def test_public_list_is_exact_paths_not_prefixes():
    """豁免必须是精确路径集合。用前缀会连带放行整棵子树，而子树是会长出新路由的。"""
    from src import app as app_module

    assert isinstance(app_module._PUBLIC_PATHS, frozenset), \
        '豁免应是精确路径集合；tuple + startswith 会放行整棵子树'
    assert app_module._PUBLIC_PATHS == set()
    assert not hasattr(app_module, '_PUBLIC_PATH_PREFIXES'), \
        '前缀式豁免已移除，不应再出现'


def test_profile_health_does_not_leak_member_names():
    """诊断端点只回答「画像配好了没有」，不返回真实姓名。"""
    db.add_member(member_name='张三丰')
    from src.health import check_profile

    payload = check_profile()
    assert payload['status'] == 'ok'
    assert payload['member_count'] >= 1
    assert '张三丰' not in str(payload), f'诊断端点泄露了成员姓名：{payload}'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
