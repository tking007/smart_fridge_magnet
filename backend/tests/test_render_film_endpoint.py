#!/usr/bin/env python3
"""校验「只渲染不发布」端点 POST /api/render/film。

它是 BLE 直传的唯一图源：Android 不再本地画图，而是取这个接口的字节直接写进设备。
所以这里必须盯住两件事 ——
  1. 返回的字节确实是合法 `.film`（长度、头部、调色板），否则固件静默拒收、保留旧画面，
     而 App 侧看到的是"发送成功"；
  2. 它**不产生新版本**。若不小心退化成"渲染并发布"，设备下次联网会把同一张图再下载
     一遍（209120 字节 + 一次刷屏），且这种浪费在功能上完全看不出来。

同时校验它与 /display/render 的字节完全一致 —— 两个接口共用一份渲染逻辑，
一旦有人给其中一个加了特殊处理，两条通道（BLE / Wi-Fi）显示的图就会不同。

用法：python tests/test_render_film_endpoint.py
"""

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-render-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''          # 留空 → 本机开发放行，不必构造鉴权头
os.environ['SFM_HOST'] = '127.0.0.1'
# 别让调度器在测试进程里跑：容器里 SFM_ENABLE_SCHEDULER=true，导入 app 就会再起一份，
# 而餐前下发会真的向设备发布新版本。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import device_display, menu_render  # noqa: E402
from src.app import app                   # noqa: E402

DEVICE_ID = 'devrender01'
DEVICE_LAYOUT_ID = 'devrender-layout'
PAYLOAD = {
    'kind': 'overview',
    'title': '今日菜单',
    'date': '7月29日',
    'dishes': ['早餐|小米粥', '午餐|番茄炒蛋（15分钟）', '晚餐|蒜蓉上海青（8分钟）'],
    'expiring': '上海青 即将到期',
}


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


def main() -> int:
    print('只渲染不发布端点：')
    results = []
    client = app.test_client()

    response = client.post('/api/render/film', json=PAYLOAD)
    results.append(check(
        'POST /api/render/film -> 200',
        response.status_code == 200,
        f'实际 {response.status_code} {response.data[:200]!r}'))

    film = response.data
    results.append(check(
        f'返回长度符合 .film 契约 {device_display.FILM_TOTAL_SIZE}',
        len(film) == device_display.FILM_TOTAL_SIZE,
        f'实际 {len(film)}'))

    valid, err = device_display._validate_film_header(film)
    results.append(check('返回字节通过固件同款结构校验', valid, err))

    results.append(check(
        '响应头 X-Film-Sha256 与实际字节一致',
        response.headers.get('X-Film-Sha256') == hashlib.sha256(film).hexdigest(),
        f"头里是 {response.headers.get('X-Film-Sha256')}"))

    results.append(check(
        '响应头 X-Film-Kind 标明渲染的是哪一张',
        response.headers.get('X-Film-Kind') == 'overview',
        f"实际 {response.headers.get('X-Film-Kind')}"))

    # 关键：不能产生版本
    before = device_display.get_sync_status(DEVICE_ID)['latest_version']
    client.post('/api/render/film', json=PAYLOAD)
    after = device_display.get_sync_status(DEVICE_ID)['latest_version']
    results.append(check(
        '只渲染不发布：设备版本号没有变化',
        before == 0 and after == 0,
        f'before={before} after={after}'))

    # 与发布版接口的字节必须完全一致（同一份渲染逻辑）
    published = client.post(f'/api/devices/{DEVICE_ID}/display/render', json=PAYLOAD)
    results.append(check(
        '发布版接口返回 201',
        published.status_code == 201,
        f'实际 {published.status_code} {published.data[:200]!r}'))
    results.append(check(
        '两个接口渲染出的字节完全一致（BLE 与 Wi-Fi 显示同一张图）',
        published.get_json().get('sha256') == hashlib.sha256(film).hexdigest(),
        f"发布版 sha256={published.get_json().get('sha256')}"))

    # 未知 kind 要明确报 400，而不是画一张默认图糊过去
    bad = client.post('/api/render/film', json={'kind': 'supper'})
    results.append(check(
        '未知 kind 返回 400 且说明可选值',
        bad.status_code == 400 and 'overview' in bad.get_json().get('error', ''),
        f'实际 {bad.status_code} {bad.data[:200]!r}'))

    print()
    if all(results):
        print('render film endpoint self-check passed')
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
    断言逻辑只有一份，脚本直跑（`python tests/test_render_film_endpoint.py`）与 pytest 跑的是同一套。
    """
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'


def test_manual_render_uses_device_layout():
    """BLE 取字节与 Wi-Fi 单张渲染都必须读取设备的当前版式。"""
    client = app.test_client()
    options = menu_render.normalize_layout_options()
    assert device_display.set_device_layout(DEVICE_LAYOUT_ID, 'portrait', options)['success']

    payload = {**PAYLOAD, 'device_id': DEVICE_LAYOUT_ID}
    ble = client.post('/api/render/film', json=payload)
    wifi = client.post(f'/api/devices/{DEVICE_LAYOUT_ID}/display/render', json=PAYLOAD)
    expected = menu_render.render_overview_film(
        title=PAYLOAD['title'],
        date_text=PAYLOAD['date'],
        dishes=PAYLOAD['dishes'],
        expiring=PAYLOAD['expiring'],
        layout='portrait',
        layout_options=options,
    )

    expected_hash = hashlib.sha256(expected).hexdigest()
    assert ble.status_code == 200
    assert ble.headers['X-Film-Sha256'] == expected_hash
    assert wifi.status_code == 201
    assert wifi.get_json()['sha256'] == expected_hash
