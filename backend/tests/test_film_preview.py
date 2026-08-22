#!/usr/bin/env python3
"""校验 `.film` 反解与画面预览接口。

为什么需要这条链路：BLE 协议里**没有**「读取设备上 .film 内容」的命令 —— 网页调试工具里
看到的预览其实是本地那份待传字节，不是从设备读回来的。所以 App 想显示「设备上有哪几张、
每张长什么样」，只能让后端把已发布的 .film 反解成 PNG（设备上的图正是从那儿下载的）。

盯住的行为：
  1. 解码是编码的严格逆运算，逐字节往返一致。高低半字节写反会让画面按 2 像素为单位
     左右互换 —— 那种错在缩略图上几乎看不出来，只能靠往返断言拦住。
  2. 坏文件必须**拒绝**而不是降级出一张花屏图：排障工具给出错误画面会把人引向渲染和
     固件，问题却在文件本身。
  3. 预览的颜色必须与调色板一致（与安卓端 FilmDecoder 同一张表）。
  4. 路径穿越要拒（库里存的是文件名，放行 `../` 等于把库外文件也暴露成可读）。
  5. 缩放宽度要夹到合理区间，且缩略图明显小于原图（列表里有四张）。

用法：python tests/test_film_preview.py
"""
import io
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

_tmp = tempfile.mkdtemp(prefix='sfm-preview-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_BASE_URL'] = 'https://example.invalid'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from PIL import Image                          # noqa: E402
from src import device_display as dd           # noqa: E402
from src import film_encoder as fe             # noqa: E402
from src.app import app                         # noqa: E402

DEVICE = 'dev-preview'
SET_NAMES = ['1_today.film', '2_breakfast.film', '3_lunch.film', '4_dinner.film']


def _random_indices(seed: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(0, len(fe.PALETTE_RGB))
                 for _ in range(fe.FILM_WIDTH * fe.FILM_HEIGHT))


def test_decode_is_exact_inverse_of_encode():
    """随机索引 -> .film -> 索引，必须逐字节一致。"""
    indices = _random_indices(11)
    film = fe.build_film(indices)
    assert bytes(fe.film_to_indices(film)) == indices, \
        '往返不一致 —— 高低半字节写反会让画面按 2 像素左右互换'

    # 边界：全 0 与全部最大索引
    for filler in (0, len(fe.PALETTE_RGB) - 1):
        flat = bytes([filler]) * (fe.FILM_WIDTH * fe.FILM_HEIGHT)
        assert bytes(fe.film_to_indices(fe.build_film(flat))) == flat
    print('  ✓ 解码是编码的严格逆运算')


def test_rgb_matches_palette():
    """反解出的 RGB 必须来自调色板，且与 index_to_rgb 一一对应。"""
    indices = _random_indices(12)
    rgb = fe.film_to_rgb(fe.build_film(indices))
    assert len(rgb) == fe.FILM_WIDTH * fe.FILM_HEIGHT * 3

    # 抽样比对：颜色表两端各存一份的话，同一文件在 App 与后端预览里会显示成不同颜色，
    # 而两边看着都"正常"。
    for pixel in (0, 1, 12345, fe.FILM_WIDTH * fe.FILM_HEIGHT - 1):
        offset = pixel * 3
        assert tuple(rgb[offset:offset + 3]) == fe.index_to_rgb(indices[pixel]), \
            f'像素 {pixel} 的颜色与调色板不一致'

    # 出现的颜色不能超出调色板
    seen = {tuple(rgb[i:i + 3]) for i in range(0, len(rgb), 3)}
    assert seen <= set(fe.PALETTE_RGB), f'出现了调色板外的颜色：{seen - set(fe.PALETTE_RGB)}'
    print('  ✓ 反解颜色严格取自调色板')


def test_broken_film_rejected_not_degraded():
    """长度或头部不对时抛异常，绝不降级出花屏图。"""
    film = fe.build_film(_random_indices(13))

    for bad, why in (
        (b'', '空文件'),
        (film[:-1], '少一字节'),
        (film + b'\x00', '多一字节'),
        (film[:100], '截断'),
    ):
        try:
            fe.film_to_indices(bad)
            raise AssertionError(f'{why} 应被拒绝')
        except ValueError:
            pass

    # 头部尺寸被改坏：数据长度对得上，但内容含义完全不同
    broken = bytearray(film)
    broken[4:6] = (100).to_bytes(2, 'little')
    try:
        fe.film_to_indices(bytes(broken))
        raise AssertionError('头部尺寸错误应被拒绝')
    except ValueError:
        pass
    print('  ✓ 坏文件被拒绝而不是降级成花屏')


def _publish_set():
    slots = ['today', 'breakfast', 'lunch', 'dinner']
    indices_by_name = {
        name: bytes([index]) * (fe.FILM_WIDTH * fe.FILM_HEIGHT)
        for index, name in enumerate(SET_NAMES)
    }
    items = [
        {'slot': slot, 'name': name, 'data': fe.build_film(indices_by_name[name])}
        for slot, name in zip(slots, SET_NAMES)
    ]
    result = dd.upload_film_set(DEVICE, items, uploaded_by='test')
    assert result['success'], result
    return result['version'], indices_by_name


def test_preview_returns_png_of_device_screen():
    """预览返回 792x528 的 PNG，像素与源 .film 一致。"""
    version, indices_by_name = _publish_set()
    indices = indices_by_name['1_today.film']
    client = app.test_client()

    resp = client.get(
        f'/api/devices/{DEVICE}/display/preview.png'
        f'?version={version}&name=1_today.film'
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
    assert resp.mimetype == 'image/png'

    image = Image.open(io.BytesIO(resp.data)).convert('RGB')
    assert image.size == (fe.FILM_WIDTH, fe.FILM_HEIGHT), image.size
    # 抽查几个像素：这是"预览就是设备上那张图"这句话的唯一证据
    for x, y in ((0, 0), (791, 527), (400, 200)):
        pixel_index = y * fe.FILM_WIDTH + x
        assert image.getpixel((x, y)) == fe.index_to_rgb(indices[pixel_index]), \
            f'({x},{y}) 与源文件不一致'

    # 同一个 version+name 内容永不改变（新发布会拿到新版本号），应可长缓存
    assert 'immutable' in (resp.headers.get('Cache-Control') or ''), resp.headers
    print('  ✓ 预览 PNG 与源 .film 像素一致且可缓存')


def test_all_published_slots_have_named_previews():
    """同一版本的四张标准画面都必须可按文件名预览。"""
    version, indices_by_name = _publish_set()
    client = app.test_client()

    for name in SET_NAMES:
        resp = client.get(
            f'/api/devices/{DEVICE}/display/preview.png?version={version}&name={name}'
        )
        assert resp.status_code == 200, f'{name}: {resp.get_data(as_text=True)[:200]}'
        assert resp.mimetype == 'image/png', name
        image = Image.open(io.BytesIO(resp.data)).convert('RGB')
        assert image.getpixel((0, 0)) == fe.index_to_rgb(indices_by_name[name][0]), name
        resp.close()


def test_preview_does_not_record_device_contact():
    """App 查看预览不是设备下载，不得刷新设备最近在线时间或事件列表。"""
    version, _ = _publish_set()
    before = dd.get_sync_status(DEVICE)
    download_events = [e for e in before['recent_events'] if e['event'] == 'download']

    resp = app.test_client().get(
        f'/api/devices/{DEVICE}/display/preview.png'
        f'?version={version}&name=1_today.film'
    )
    assert resp.status_code == 200
    resp.close()

    after = dd.get_sync_status(DEVICE)
    assert after['device_last_contact'] == before['device_last_contact']
    assert after['device_last_contact_minutes'] == before['device_last_contact_minutes']
    assert [e for e in after['recent_events'] if e['event'] == 'download'] == download_events


def test_preview_thumbnail_scaling():
    """w 参数缩放，且被夹到合理区间。"""
    version, _ = _publish_set()
    client = app.test_client()
    base = f'/api/devices/{DEVICE}/display/preview.png?version={version}&name=1_today.film'

    full = client.get(base)
    thumb = client.get(base + '&w=160')
    assert thumb.status_code == 200
    full_image = Image.open(io.BytesIO(full.data))
    thumb_image = Image.open(io.BytesIO(thumb.data))
    assert full_image.size == (fe.FILM_WIDTH, fe.FILM_HEIGHT), full_image.size
    assert thumb_image.size == (160, 107), thumb_image.size

    # 下界：再小就看不出是什么；上界：放大只是让字更糊、字节更多
    tiny = Image.open(io.BytesIO(client.get(base + '&w=1').data))
    huge = Image.open(io.BytesIO(client.get(base + '&w=99999').data))
    assert tiny.size[0] == 32, tiny.size
    assert huge.size[0] == fe.FILM_WIDTH, huge.size
    print('  ✓ 缩放生效且宽度被夹到 [32, 792]')


def test_preview_portrait_orientation():
    """竖向参数只改变预览方向，不改变默认响应。"""
    version, _ = _publish_set()
    client = app.test_client()
    base = f'/api/devices/{DEVICE}/display/preview.png?version={version}&name=1_today.film'

    portrait = client.get(base + '&orientation=portrait&w=160')
    assert portrait.status_code == 200
    assert Image.open(io.BytesIO(portrait.data)).size == (160, 240)
    assert client.get(base + '&orientation=landscape').status_code == 400


def test_preview_defaults_to_latest_version():
    """省略 version 时用最新版本。"""
    _publish_set()
    version2, _ = _publish_set()
    client = app.test_client()

    without = client.get(f'/api/devices/{DEVICE}/display/preview.png?name=1_today.film')
    latest = client.get(
        f'/api/devices/{DEVICE}/display/preview.png?version={version2}&name=1_today.film'
    )
    assert without.status_code == 200
    assert without.data == latest.data, '省略 version 应等价于最新版本'
    # send_file() 的响应持有打开的文件对象；不关闭会留下 ResourceWarning，
    # 把真实的句柄泄漏淹没在噪音里。
    without.close()
    latest.close()

    # 但**不能**按 immutable 缓存：这个地址的内容是"当前最新版"，会随每次发布变化，
    # 而 App 在还没连上设备（拿不到 lastAppliedVersion）时正是这么请求的。
    # 按 immutable 缓存会让用户发布新画面后最多 24 小时都看到旧预览，界面上毫无线索。
    cache = without.headers.get('Cache-Control') or ''
    assert 'immutable' not in cache, f'无 version 的预览不该长缓存：{cache}'
    assert 'max-age=60' in cache, cache
    assert without.headers.get('ETag'), '至少要给 ETag 让客户端做条件请求'
    print('  ✓ 省略 version 取最新版本，且只发短缓存')


def test_auth_split_between_preview_and_download():
    """`.png` 预览走 app_token、`.film` 下载走 device_token —— 四种组合各断言一次。

    这条分界完全由 `_is_device_pull_request()` 里 `path.endswith('.film')` 那个字符串判断
    承担。把预览端点改成 `/display/preview/<name>` 之类的形状就会静默翻转（预览开始要求设备
    令牌，而 App 侧只表现为"缩略图全都加载不出来"），而本文件其余测试都跑在
    `SFM_API_TOKEN=''` 下、鉴权整段被短路，一条都不会红。
    """
    version, _ = _publish_set()
    app_token = disposable_token('app-')
    device_token = disposable_token('device-')
    dd.set_device_token(DEVICE, device_token)
    os.environ['SFM_API_TOKEN'] = app_token
    try:
        client = app.test_client()
        preview = (f'/api/devices/{DEVICE}/display/preview.png'
                   f'?version={version}&name=1_today.film')
        download = f'/api/devices/{DEVICE}/display/{version}/1_today.film'
        as_app = {'Authorization': f'Bearer {app_token}'}
        as_device = {'Authorization': f'Bearer {device_token}'}

        assert client.get(preview, headers=as_app).status_code == 200, '预览该收 app_token'
        assert client.get(preview, headers=as_device).status_code == 401, \
            '预览是给 App 看的，不该拿设备令牌就能取'
        # 单独接出来才能 close()：send_file() 的响应持有打开的文件对象，
        # 写成 assert client.get(...).status_code 就拿不到响应对象去关。
        film_ok = client.get(download, headers=as_device)
        assert film_ok.status_code == 200, '.film 下载是设备的活'
        film_ok.close()
        assert client.get(download, headers=as_app).status_code == 401, \
            'app_token 不该能直接下 .film —— App 想看画面只能走预览端点'
        assert client.get(preview).status_code == 401, '不带令牌一律拒绝'
    finally:
        # 其余测试依赖"未配置 token 时放行"，这里必须还原，否则后续断言会全变 401
        os.environ['SFM_API_TOKEN'] = ''
    print('  ✓ 预览走 app_token、下载走 device_token，四种组合都对')


def test_preview_rejects_bad_input():
    """非法参数与路径穿越被拒。"""
    version, _ = _publish_set()
    client = app.test_client()
    base = f'/api/devices/{DEVICE}/display/preview.png'

    cases = [
        (f'{base}?version=abc', 400, 'version 非数字'),
        (f'{base}?version={version}&w=abc', 400, 'w 非数字'),
        # 放行 ../ 等于把库外的文件也暴露成可读
        (f'{base}?version={version}&name=../../../etc/passwd', 400, '路径穿越'),
        (f'{base}?version={version}&name=x.png', 400, '不是 .film'),
        (f'{base}?version={version}&name=.', 400, '当前目录'),
        (f'{base}?version={version}&name=9_missing.film', 404, '文件不存在'),
        (f'{base}?version=99999&name=1_today.film', 404, '版本不存在'),
        ('/api/devices/dev-never-published/display/preview.png', 404, '设备没发布过'),
    ]
    for url, expected, why in cases:
        resp = client.get(url)
        assert resp.status_code == expected, \
            f'{why}: 期望 {expected}，实际 {resp.status_code}（{url}）'
    print('  ✓ 非法参数与路径穿越被拒')


def main():
    print('=' * 60)
    print('画面预览测试')
    print('=' * 60)
    tests = [
        test_decode_is_exact_inverse_of_encode,
        test_rgb_matches_palette,
        test_broken_film_rejected_not_degraded,
        test_preview_returns_png_of_device_screen,
        test_all_published_slots_have_named_previews,
        test_preview_does_not_record_device_contact,
        test_preview_thumbnail_scaling,
        test_preview_portrait_orientation,
        test_preview_defaults_to_latest_version,
        test_preview_rejects_bad_input,
        test_auth_split_between_preview_and_download,
    ]
    for fn in tests:
        print(f'\n{fn.__name__}:')
        fn()
    print('\n' + '=' * 60)
    print(f'全部通过（{len(tests)} 项）')
    print('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
