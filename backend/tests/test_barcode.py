#!/usr/bin/env python3
"""校验条码查商品接口：查不到就说查不到，不编一个名字。

数据源是 Open Food Facts（真实商品数据库）而不是模型：条码是数据库主键，模型对长数字串的
记忆极不可靠、失败方式是自信地编一个，而编出来的名字会原样写进冰箱库存、进配菜 prompt、
最后出现在电子纸上的菜单里，链路上没有任何一处会质疑它。

这套测试的重点全在「拒绝」与「区分」两侧：
  1. 非商品码格式（含二维码解出来的 URL）必须在发出任何网络请求之前就被拒。
  2. 库里没有 → success=false + reason=not_in_database，让客户端回落拍照识别。
  3. **查询失败与库里没有必须分开**：前者重试有意义、不该进缓存；后者进缓存。
  4. 收录了但只有品牌碎片 / 没有商品名 → 按查不到处理（空名字进库存与编造无异）。
  5. 分类映射不上时归「其他」，不把一个野分类写进库。
  6. 功能可整体关闭，且关闭时明确说「关了」而不是伪装成查不到。

用法：python -m pytest tests/test_barcode.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-barcode-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

import pytest                              # noqa: E402

from src import barcode_lookup             # noqa: E402
from src import database as db             # noqa: E402
from src.app import app                    # noqa: E402


class _StubLookup:
    """替掉 Open Food Facts 查询并记录被调了几次。

    调用次数本身是断言目标：缓存有没有生效，只能靠「第二次没有再发请求」来证明。
    """

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def __call__(self, code):
        self.calls += 1
        return self.reply


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """每个用例一套干净的库与默认开启的开关。"""
    db.init_db()
    conn = db.get_connection()
    conn.execute('DELETE FROM barcode_cache')
    conn.commit()
    conn.close()
    monkeypatch.setenv('SFM_BARCODE_LOOKUP', 'true')
    app.config['TESTING'] = True


@pytest.fixture
def client():
    with app.test_client() as c:
        yield c


def _install(monkeypatch, reply):
    stub = _StubLookup(reply)
    monkeypatch.setattr(barcode_lookup, 'lookup', stub)
    return stub


# ── 格式校验：在发请求之前 ────────────────────────────────────────

@pytest.mark.parametrize('bad', [
    'abc12345',
    '1234567',                 # 7 位，短于 EAN-8
    '123456789012345',         # 15 位，长于 EAN-14
    '69012859912a9',
])
def test_invalid_barcode_rejected_before_any_request(client, monkeypatch, bad):
    stub = _install(monkeypatch, {'found': False, 'reason': 'not_in_database'})

    resp = client.get(f'/api/barcode/{bad}')

    assert resp.status_code == 400
    assert resp.get_json()['reason'] == 'invalid'
    assert stub.calls == 0, '格式不合法就不该发出网络请求'


def test_surrounding_whitespace_is_tolerated(client, monkeypatch):
    """扫码器可能带上首尾空白，那不是用户的错，也不该被判成格式非法。"""
    _install(monkeypatch, {'found': True, 'name': '怡宝纯净水', 'category': '饮料',
                           'quantity': '', 'source': 'openfoodfacts'})

    body = client.get('/api/barcode/ 6901285991219 ').get_json()

    assert body['success'] is True
    assert body['barcode'] == '6901285991219', '回给客户端的应是去掉空白后的码'


def test_qr_url_cannot_reach_the_endpoint(client, monkeypatch):
    """二维码解出来的 URL 连路由都匹配不上（含 `/`），照样不会发出查询请求。

    单独写一条而不是并进上面的参数化：它被挡住的机制不同（路由不匹配 → 404，
    而不是端点里的格式校验 → 400），断言成 400 会掩盖真实行为。
    """
    stub = _install(monkeypatch, {'found': False, 'reason': 'not_in_database'})

    resp = client.get('/api/barcode/https://example.com/x')

    assert resp.status_code == 404
    assert stub.calls == 0


# ── 查到 / 查不到 / 查询失败三态 ──────────────────────────────────

def test_found_product_is_returned_and_cached(client, monkeypatch):
    stub = _install(monkeypatch, {
        'found': True, 'name': '怡宝纯净水', 'category': '饮料',
        'quantity': '555ml', 'source': 'openfoodfacts',
    })

    first = client.get('/api/barcode/6901285991219').get_json()
    assert first['success'] is True
    assert first['name'] == '怡宝纯净水'
    assert first['category'] == '饮料'
    assert first['source'] == 'openfoodfacts'
    assert first['cached'] is False
    # OFF 的中文覆盖差，返回的名字用户可能根本不认识 —— 必须先给人确认。
    assert first['needs_confirmation'] is True

    second = client.get('/api/barcode/6901285991219').get_json()
    assert second['success'] is True
    assert second['cached'] is True
    assert stub.calls == 1, '第二次应命中缓存，不再查商品库'


def test_not_in_database_is_reported_not_invented(client, monkeypatch):
    _install(monkeypatch, {'found': False, 'reason': 'not_in_database'})

    body = client.get('/api/barcode/6902538007190').get_json()

    assert body['success'] is False
    assert body['reason'] == 'not_in_database'
    assert '拍照' in body['message'], '要指出可用的替代路径'
    assert 'name' not in body


def test_not_in_database_is_cached(client, monkeypatch):
    """查不到也要记住，否则每次扫到同一个生僻商品都要重新等一次网络往返。"""
    stub = _install(monkeypatch, {'found': False, 'reason': 'not_in_database'})

    client.get('/api/barcode/6902538007190')
    second = client.get('/api/barcode/6902538007190').get_json()

    assert second['cached'] is True
    assert stub.calls == 1


def test_lookup_failure_is_not_cached_and_says_retry(client, monkeypatch):
    """查询失败与库里没有必须分开。

    网络问题是暂时的，缓存下来会让这个条码在缓存过期前一直「查不到」，
    而用户完全无从判断到底是商品库没有还是网络出了问题。
    """
    stub = _install(monkeypatch, {
        'found': False, 'reason': 'lookup_failed', 'detail': 'timed out',
    })

    resp = client.get('/api/barcode/6901285991219')
    assert resp.status_code == 503
    body = resp.get_json()
    assert body['reason'] == 'lookup_failed'
    assert '重试' in body['message']

    # 没进缓存 → 第二次仍会真的去查
    client.get('/api/barcode/6901285991219')
    assert stub.calls == 2


def test_out_of_whitelist_category_falls_back(client, monkeypatch):
    _install(monkeypatch, {
        'found': True, 'name': '某商品', 'category': '不存在的分类',
        'quantity': '', 'source': 'openfoodfacts',
    })

    body = client.get('/api/barcode/6901285991219').get_json()

    assert body['category'] == '其他'
    assert body['shelf_life_days'] == db.DEFAULT_SHELF_LIFE_DAYS['其他']


# ── 功能开关：可整体下掉 ──────────────────────────────────────────

def test_disabled_says_so_instead_of_pretending_not_found(client, monkeypatch):
    """关掉后要明确说「关了」。

    伪装成查不到会让用户以为再扫一次或换个商品就行，而实际上永远不会成功。
    """
    monkeypatch.setenv('SFM_BARCODE_LOOKUP', 'false')
    stub = _install(monkeypatch, {'found': True, 'name': 'x', 'category': '其他',
                                  'quantity': '', 'source': 'openfoodfacts'})

    resp = client.get('/api/barcode/6901285991219')

    assert resp.status_code == 503
    assert resp.get_json()['reason'] == 'disabled'
    assert stub.calls == 0


# ── 数据源模块本身的纯逻辑 ────────────────────────────────────────

def test_pick_name_never_uses_brand_as_product_name():
    """只有品牌碎片时按没有商品名处理。

    实测有条目只有 "cremica" 这样的品牌串。把它当商品名写进库存，
    用户在冰箱里看到的是一个不知所指的词，配菜时它也不对应任何食材。
    """
    assert barcode_lookup._pick_name({'brands': 'cremica'}) == ''
    assert barcode_lookup._pick_name({'product_name': '', 'brands': 'x'}) == ''
    assert barcode_lookup._pick_name({'product_name_zh': '怡宝'}) == '怡宝'
    # 中文字段优先
    assert barcode_lookup._pick_name(
        {'product_name_zh': '怡宝纯净水', 'product_name': 'Yibao Water'}
    ) == '怡宝纯净水'


@pytest.mark.parametrize('tags,expected', [
    (['en:beverages', 'en:waters'], '饮料'),
    (['en:dairies', 'en:milk'], '乳制品'),
    (['en:meats', 'en:beef'], '肉类'),
    (['en:plant-based-foods', 'en:vegetables'], '蔬菜'),
    (['en:snacks', 'en:unknown-thing'], '其他'),
    ([], '其他'),
    (None, '其他'),
])
def test_category_mapping(tags, expected):
    assert barcode_lookup._map_category(tags) == expected


def test_category_mapping_prefers_the_specific_tag():
    """OFF 的 categories_tags 从粗到细排列，细的信息量更大。"""
    assert barcode_lookup._map_category(
        ['en:beverages', 'en:dairy-drinks', 'en:milk']
    ) == '乳制品'


# ── 众包库的垃圾条目必须挡住 ──────────────────────────────────────

@pytest.mark.parametrize('code,name', [
    ('99999999999', 'Sugo alle Melanzone'),   # 实测：补零成 0099999999999 后匹配到真商品
    ('00000000', 'whatever'),
    ('12345678', 'dsdsds'),                   # 实测：OFF 里真有这条
    ('87654321', 'x'),
    ('6901285991219', 'x'),                   # 码正常但名字只有一个字符
])
def test_junk_entries_are_treated_as_not_found(code, name):
    """Open Food Facts 是众包库，查询端还会补零归一。

    后果是**扫错一位的条码可能静默返回一个看似合理的商品名**。这是数据源的固有性质，
    不是偶发 bug，所以要在这里挡一道 —— 挡不住的部分靠 needs_confirmation 让人过目。
    """
    assert barcode_lookup._looks_like_junk(code, name) is True


@pytest.mark.parametrize('code,name', [
    ('6901285991219', '怡宝纯净水'),
    ('3017620422003', 'Nutella'),
    ('6920202888883', '农夫山泉饮用天然水'),
])
def test_real_looking_codes_pass_the_junk_filter(code, name):
    assert barcode_lookup._looks_like_junk(code, name) is False


def test_open_food_facts_response_has_a_hard_size_limit(monkeypatch):
    """第三方服务不能用无限响应耗尽后端内存。"""
    requested = []

    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            requested.append(size)
            return b'x' * size

    monkeypatch.setattr(
        barcode_lookup.urllib.request, 'urlopen',
        lambda *_args, **_kwargs: OversizedResponse(),
    )

    result = barcode_lookup.lookup('6901285991219')

    assert requested == [barcode_lookup._MAX_RESPONSE_BYTES + 1]
    assert result['reason'] == 'response_too_large'
