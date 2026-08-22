#!/usr/bin/env python3
"""校验自定义图片端点 POST /api/render/image（手绘涂鸦 / 相册照片 -> `.film`）。

这条路上的失败**全是静默的**，所以必须有断言盯着：

  1. 字节不合契约 -> 固件拒收 -> 保留旧画面。App 侧看到的是「发送成功」，
     真机上看起来就是「传完了但屏幕没动」，只能接串口才查得出来。
  2. 透明通道处理错 -> 一张透明背景的涂鸦在设备上变成大片黑。后端一路 200，
     PNG 在手机上预览也正常，问题只在电子纸上可见。
  3. EXIF 方向没纠 -> 手机上看是正的、设备上是躺着的。也只在设备上可见。

用法：python tests/test_render_image_endpoint.py
"""

import hashlib
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-render-image-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''          # 留空 → 本机开发放行，不必构造鉴权头
os.environ['SFM_HOST'] = '127.0.0.1'
# 别让调度器在测试进程里跑：容器里 SFM_ENABLE_SCHEDULER=true，导入 app 就会再起一份，
# 而餐前下发会真的向设备发布新版本。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from PIL import Image                     # noqa: E402
from src import device_display            # noqa: E402
from src import film_encoder as fe        # noqa: E402
from src.app import app                   # noqa: E402

WHITE_INDEX = 1                           # film_encoder.PALETTE 里白色的索引
BLACK_INDEX = 0


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


# ---------------------------------------------------------------- 构图辅助


def _png_bytes(image: Image.Image, exif: bytes = None) -> bytes:
    """PIL 图像 -> PNG 字节。用 PNG 而不是 JPEG 是为了让比对**无损**：
    JPEG 的有损压缩会在色块边缘造出中间色，量化后落到不同索引，
    「EXIF 纠正后 == 手工旋转后」这类逐字节相等的断言就会随机失败。
    """
    buffer = io.BytesIO()
    if exif is None:
        image.save(buffer, 'PNG')
    else:
        image.save(buffer, 'PNG', exif=exif)
    return buffer.getvalue()


def _asymmetric_image() -> Image.Image:
    """构造长宽不等、四象限颜色互不相同的图。

    长宽不等 + 无对称性，旋转 90 度后与原图**不可能**碰巧量化成同一份字节 ——
    否则「方向纠正生效」这个断言会在没纠正时也通过（假阳性）。
    颜色一律取调色板里的精确值，量化路径上没有歧义。
    """
    width, height = 300, 100
    image = Image.new('RGB', (width, height), fe.PALETTE_RGB[WHITE_INDEX])
    quadrants = [
        ((0, 0, 150, 50), fe.PALETTE_RGB[0]),            # 左上 黑
        ((150, 0, 300, 50), fe.PALETTE_RGB[3]),          # 右上 红
        ((0, 50, 150, 100), fe.PALETTE_RGB[4]),          # 左下 蓝
        ((150, 50, 300, 100), fe.PALETTE_RGB[2]),        # 右下 黄
    ]
    for box, color in quadrants:
        image.paste(Image.new('RGB', (box[2] - box[0], box[3] - box[1]), color), box[:2])
    return image


def _transparent_png() -> bytes:
    """透明背景 + 一道黑线的 PNG，模拟手绘涂鸦导出的样子。"""
    image = Image.new('RGBA', (400, 300), (0, 0, 0, 0))
    # 笔画本身不透明；除此之外整张图都是 alpha=0
    for y in range(140, 160):
        for x in range(40, 360):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


def _noisy_photo() -> bytes:
    """颜色种类上万的测试图，用来量 quantize_rgb() 的缓存失效路径。

    真实照片去重后往往有上万种取值，字典缓存基本不命中，会退化成逐像素全精度计算。
    这里用确定性伪随机噪声 + 渐变叠加来复现那个最坏情况（不引入 numpy / 随机种子依赖）。
    """
    width, height = 800, 600
    image = Image.new('RGB', (width, height))
    pixels = image.load()
    state = 123456789
    for y in range(height):
        for x in range(width):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            pixels[x, y] = (
                (state >> 7) & 0xFF,
                ((state >> 15) & 0xFF) ^ (y & 0x3F),
                (x * 255 // width) ^ ((state >> 3) & 0x3F),
            )
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


def _post_image(client, png: bytes, fit: str = None, field: str = 'image'):
    data = {field: (io.BytesIO(png), 'shot.png')}
    if fit is not None:
        data['fit'] = fit
    return client.post('/api/render/image', data=data,
                       content_type='multipart/form-data')


def _index_counts(film: bytes) -> dict:
    """按调色板索引统计像素数量（纯读解码，与 App 侧预览的做法一致）。"""
    counts = {}
    pixels = film[device_display.FILM_HEADER_SIZE:]
    for byte in pixels:
        for index in ((byte >> 4) & 0x0F, byte & 0x0F):
            counts[index] = counts.get(index, 0) + 1
    return counts


# ---------------------------------------------------------------- 用例


def main() -> int:
    print('自定义图片端点：')
    results = []
    client = app.test_client()
    total_pixels = fe.FILM_WIDTH * fe.FILM_HEIGHT

    # ---- 三种 fit 都必须产出合法 .film ----
    base_png = _png_bytes(_asymmetric_image())
    films = {}
    for fit in ('cover', 'contain', 'stretch'):
        response = _post_image(client, base_png, fit)
        ok = response.status_code == 200
        results.append(check(
            f'fit={fit} -> 200',
            ok,
            f'实际 {response.status_code} {response.data[:200]!r}'))
        if not ok:
            continue
        film = response.data
        films[fit] = film
        results.append(check(
            f'fit={fit} 长度恰为 {device_display.FILM_TOTAL_SIZE}',
            len(film) == device_display.FILM_TOTAL_SIZE,
            f'实际 {len(film)}'))
        valid, err = device_display._validate_film_header(film)
        results.append(check(f'fit={fit} 通过固件同款结构校验', valid, err))
        results.append(check(
            f'fit={fit} 回显在 X-Film-Fit',
            response.headers.get('X-Film-Fit') == fit,
            f"实际 {response.headers.get('X-Film-Fit')}"))
        results.append(check(
            f'fit={fit} 的 X-Film-Sha256 与响应体一致',
            response.headers.get('X-Film-Sha256') == hashlib.sha256(film).hexdigest(),
            f"头里是 {response.headers.get('X-Film-Sha256')}"))
        results.append(check(
            f'fit={fit} 的 Content-Length 与响应体一致',
            response.headers.get('Content-Length') == str(len(film)),
            f"头里是 {response.headers.get('Content-Length')}"))

    if len(films) == 3:
        # 三种策略必须画出不同的图 —— 若有两种相同，说明某个分支没生效
        # （例如 fit 参数被忽略、一律按 cover 处理），而那种 bug 只在设备上看得出来。
        results.append(check(
            '三种 fit 产出互不相同的字节',
            len({bytes(f) for f in films.values()}) == 3,
            '至少两种策略产出了相同字节，某个分支可能没生效'))

        # 300x100 的横图：contain 会在上下留大片白，cover 则填满
        contain_white = _index_counts(films['contain']).get(WHITE_INDEX, 0)
        cover_white = _index_counts(films['cover']).get(WHITE_INDEX, 0)
        results.append(check(
            'contain 的白色占比明显高于 cover（留白确实是白底，不是黑底）',
            contain_white > cover_white,
            f'contain 白 {contain_white} vs cover 白 {cover_white}'))

    # ---- 省略 fit 时默认 cover ----
    default_response = _post_image(client, base_png, None)
    results.append(check(
        '省略 fit 时默认按 cover 处理',
        default_response.status_code == 200
        and default_response.headers.get('X-Film-Fit') == 'cover'
        and default_response.data == films.get('cover'),
        f"实际 {default_response.status_code} "
        f"fit={default_response.headers.get('X-Film-Fit')}"))

    # ---- 透明通道不能变成大片黑 ----
    # 直接 convert('RGB') 会把 alpha=0 的像素当黑处理，整张涂鸦到设备上就是一块黑。
    transparent = _post_image(client, _transparent_png(), 'contain')
    ok = transparent.status_code == 200
    results.append(check(
        '透明 PNG -> 200',
        ok,
        f'实际 {transparent.status_code} {transparent.data[:200]!r}'))
    if ok:
        counts = _index_counts(transparent.data)
        white_ratio = counts.get(WHITE_INDEX, 0) / total_pixels
        black_ratio = counts.get(BLACK_INDEX, 0) / total_pixels
        # 笔画只占原图 (320*20)/(400*300) ≈ 5%，其余全透明 → 合成后应当几乎全白
        results.append(check(
            '透明区域被合成到白底：白色占比 > 0.8',
            white_ratio > 0.8,
            f'白 {white_ratio:.3f} / 黑 {black_ratio:.3f}（若白≈0、黑≈1 就是踩了透明变黑）'))
        results.append(check(
            '黑色只剩笔画那一点：黑色占比 < 0.2',
            black_ratio < 0.2,
            f'黑 {black_ratio:.3f}'))

    # ---- EXIF orientation=6 必须被纠正 ----
    exif = Image.Exif()
    exif[274] = 6                                    # 274 = Orientation
    asymmetric = _asymmetric_image()
    rotated_png = _png_bytes(asymmetric, exif=exif.tobytes())
    # fix_orientation 对 orientation=6 的动作是 rotate(270, expand=True)，
    # 这里手工做同一件事并去掉 EXIF：两条路的结果必须逐字节相同。
    manual_png = _png_bytes(asymmetric.rotate(270, expand=True))

    auto = _post_image(client, rotated_png, 'contain')
    manual = _post_image(client, manual_png, 'contain')
    plain = _post_image(client, _png_bytes(asymmetric), 'contain')
    results.append(check(
        'EXIF orientation=6 与手工旋转后的结果逐字节一致',
        auto.status_code == 200 and manual.status_code == 200
        and auto.data == manual.data,
        f'auto={auto.status_code} manual={manual.status_code} '
        f'sha(auto)={hashlib.sha256(auto.data).hexdigest()[:16]} '
        f'sha(manual)={hashlib.sha256(manual.data).hexdigest()[:16]}'))
    results.append(check(
        '方向确实被改过（与不带 EXIF 的同一张图不同）',
        plain.status_code == 200 and auto.data != plain.data,
        '带 EXIF 与不带 EXIF 产出相同字节，说明纠正根本没执行'))

    # ---- 参数错误一律 400，不能画一张默认图糊过去 ----
    missing = client.post('/api/render/image', data={'fit': 'cover'},
                          content_type='multipart/form-data')
    results.append(check(
        '缺 image 字段返回 400',
        missing.status_code == 400,
        f'实际 {missing.status_code} {missing.data[:200]!r}'))

    bad_fit = _post_image(client, base_png, 'fill')
    results.append(check(
        'fit 非法返回 400 且说明可选值',
        bad_fit.status_code == 400 and 'cover' in bad_fit.get_json().get('error', ''),
        f'实际 {bad_fit.status_code} {bad_fit.data[:200]!r}'))

    broken = _post_image(client, b'not an image at all', 'cover')
    results.append(check(
        '无法解码的字节返回 400（而不是 500）',
        broken.status_code == 400,
        f'实际 {broken.status_code} {broken.data[:200]!r}'))

    # ---- 不发布：本端点不应产生任何设备版本 ----
    # 与 /api/render/film 同样的约束：若哪天退化成「渲染并发布」，设备下次联网会把同一张图
    # 再下载一遍（209120 字节 + 一次刷屏），而这种浪费在功能上完全看不出来。
    status = device_display.get_sync_status('devimage01')
    results.append(check(
        '只渲染不发布：没有产生设备版本',
        status['latest_version'] == 0,
        f"latest_version={status['latest_version']}"))

    # ---- 性能：颜色种类上万的照片是最坏情况 ----
    photo = _noisy_photo()
    started = time.perf_counter()
    perf = _post_image(client, photo, 'cover')
    elapsed = time.perf_counter() - started
    print(f'  INFO  噪声图（800x600，颜色种类上万）端点耗时 {elapsed:.2f}s')
    results.append(check(
        '噪声图也能产出合法 .film',
        perf.status_code == 200 and len(perf.data) == device_display.FILM_TOTAL_SIZE,
        f'实际 {perf.status_code} len={len(perf.data)}'))
    # 只做「不至于挂起」的兜底断言。真实数字打印在上面一行，回归靠人看 ——
    # 卡一个紧阈值会让这套测试在慢机器 / CI 上随机变红，而它要盯的是正确性不是速度。
    results.append(check(
        '最坏情况耗时仍在可用范围（< 60s，防止退化成挂起）',
        elapsed < 60,
        f'实际 {elapsed:.2f}s'))

    print()
    if all(results):
        print('render image endpoint self-check passed')
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
    断言逻辑只有一份，脚本直跑（`python tests/test_render_image_endpoint.py`）与 pytest 跑的是同一套。
    """
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'
