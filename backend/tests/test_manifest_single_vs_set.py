#!/usr/bin/env python3
"""校验 manifest 对「单张发布」与「一整套发布」的表达方式必须不同。

值得单独一个文件是因为这条规则错了会让一次发布**永久丢失，而两端日志都显示成功**。

`items[]` 的语义是「这一版包含一整套里的哪几张」，而 `slot='current'` 不是一套里的槽位 ——
它是固件单张路径的固定落盘名（service_wifi.c 的 `SFM_WIFI_FILM_FILENAME`）。把它表达成
`items[]` 的一条会让新固件（分流条件是 `item_count > 0`）走整套下载路径
`wifi_download_film_set()`，而该函数在整套成功后要清理 `current.film`（按契约属于单张路径
的遗留文件），恰好是刚下载并提交的那张；删除又发生在版本号推进之后，于是图被删 →
上屏时文件数为 0 → 电子纸保留旧像素 → 下一轮判「已是最新」不再下载。

所以单张版本必须回到 `item_count == 0` 的路径（纯旧字段），让新固件走
`wifi_download_film_streaming()`。

覆盖：
  1. 单张发布后的 manifest **不含** `items` 键，且 `film_url`/`size`/`sha256`
     三个旧字段完整可用（旧固件的唯一依据）；
  2. 整套发布（四张）后的 manifest **含** `items` 且恰 4 条，文件名与跨端契约一致，
     `film_url` 指向总览那一张；
  3. 同一台设备先单张 v1、再整套 v2：v2 有 items，且 v1 的记录与下载地址未被破坏。
     （同一版本内混装被 UNIQUE(device_id, version, slot) 挡住；跨版本共存是常态。）

用法：python tests/test_manifest_single_vs_set.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

# 必须在导入 src.* 之前设好：建表会在导入期发生，库与 .film 都得落到临时目录，
# 别碰开发机（或容器里的生产库）上的真实数据。
_tmp = tempfile.mkdtemp(prefix='sfm-manifest-shape-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ.setdefault('SFM_API_TOKEN', disposable_token('app-'))
os.environ['SFM_BASE_URL'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
# 别让调度器在测试进程里跑：容器里 SFM_ENABLE_SCHEDULER=true，导入就会再起一份，
# 而餐前下发会真的向设备发布新版本。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import device_display        # noqa: E402
from src import film_encoder as fe    # noqa: E402

DEVICE_SINGLE = 'devshape01'
DEVICE_SET = 'devshape02'
DEVICE_MIXED = 'devshape03'

BASE_URL = 'https://fridge.example.com'

# 跨端契约的落盘文件名（固件按名字判断显示时段）。这里刻意写成字面量而不是从
# menu_render 取：本文件断言的正是「manifest 里出现的名字」这一对外契约，
# 若两边同时从一个常量取值，改错常量时这条断言会跟着一起变，等于没断言。
EXPECTED_SET_NAMES = ['1_today.film', '2_breakfast.film', '3_lunch.film', '4_dinner.film']
LEGACY_NAME = 'current.film'


def make_film(fill_index: int) -> bytes:
    """构造一张合法但内容单一的 `.film`（不依赖 PIL 与字体栈）。

    走 film_encoder.build_film() 而不是自己拼字节：文件头、调色板、半字节排布的
    规则都在那里，另抄一份必然漂移，而漂移的表现是固件静默拒收、屏幕不动。
    """
    indices = bytes([fill_index & 0x0F]) * (fe.FILM_WIDTH * fe.FILM_HEIGHT)
    return fe.build_film(indices)


def check(label: str, ok: bool, detail: str = '') -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


def check_single_publish(results: list) -> None:
    """单张发布：manifest 必须是纯旧字段形态，不带 items。"""
    print('单张发布（slot=current）：')

    uploaded = device_display.upload_film(DEVICE_SINGLE, make_film(1), uploaded_by='test-single')
    results.append(check(
        'upload_film 成功并产生版本 1',
        uploaded.get('success') is True and uploaded.get('version') == 1,
        f'实际 {uploaded}'))

    manifest = device_display.get_manifest(DEVICE_SINGLE, request_base_url=BASE_URL)
    results.append(check('manifest 非空', manifest is not None, '返回 None'))
    if manifest is None:
        return

    # 核心断言：不能有 items 键。
    # 「键不存在」而不是「items == []」—— 固件对空数组也是 item_count == 0，两者行为
    # 相同，但少发一个键就少一份让人误解「这一版是一套」的线索，也省 2048 字节预算。
    results.append(check(
        'manifest 不含 items 键（新固件走单张兼容路径，不会自删刚下载的图）',
        'items' not in manifest,
        f'实际 keys={sorted(manifest)}，items={manifest.get("items")!r}'))

    # 旧三字段必须完整可用：旧固件只认这三个，新固件在 item_count == 0 时也只用它们。
    results.append(check(
        'film_url 指向旧地址 .../display/{version}.film',
        manifest.get('film_url') == f'{BASE_URL}/api/devices/{DEVICE_SINGLE}/display/1.film',
        f"实际 {manifest.get('film_url')!r}"))
    results.append(check(
        f'size == {device_display.FILM_TOTAL_SIZE}',
        manifest.get('size') == device_display.FILM_TOTAL_SIZE,
        f"实际 {manifest.get('size')!r}"))
    results.append(check(
        'sha256 是 64 位小写 hex 且与上传时一致',
        isinstance(manifest.get('sha256'), str)
        and len(manifest['sha256']) == 64
        and manifest['sha256'] == manifest['sha256'].lower()
        and manifest['sha256'] == uploaded.get('sha256'),
        f"实际 {manifest.get('sha256')!r}"))
    results.append(check(
        'version / force 两个门控字段仍在',
        manifest.get('version') == 1 and manifest.get('force') is False,
        f"version={manifest.get('version')!r} force={manifest.get('force')!r}"))

    # 旧地址必须真的能下载到那一张 —— manifest 说得对但文件取不到同样是断链。
    downloaded = device_display.download_film(DEVICE_SINGLE, 1)
    results.append(check(
        '旧地址 .../display/1.film 能取到文件',
        downloaded is not None and os.path.exists(downloaded[0]),
        f'实际 {downloaded}'))

    # sync-status 仍要能说出这一版是单张（App 靠它区分单张与整套）
    status = device_display.get_sync_status(DEVICE_SINGLE)
    results.append(check(
        "sync-status 的 slots 为 ['current']（App 由此判断这一版只有一张）",
        status.get('slots') == ['current'],
        f"实际 {status.get('slots')!r}"))


def check_set_publish(results: list) -> None:
    """整套发布：manifest 必须带 items，恰 4 条，名字与契约一致。"""
    print('\n整套发布（四张）：')

    items = [
        {'slot': 'today', 'data': make_film(0)},
        {'slot': 'breakfast', 'data': make_film(1)},
        {'slot': 'lunch', 'data': make_film(2)},
        {'slot': 'dinner', 'data': make_film(3)},
    ]
    uploaded = device_display.upload_film_set(DEVICE_SET, items, uploaded_by='test-set')
    results.append(check(
        'upload_film_set 成功并产生版本 1（一次发布只有一个版本号）',
        uploaded.get('success') is True and uploaded.get('version') == 1
        and len(uploaded.get('items') or []) == 4,
        f'实际 {uploaded.get("success")} v{uploaded.get("version")} '
        f'{len(uploaded.get("items") or [])} 条'))

    manifest = device_display.get_manifest(DEVICE_SET, request_base_url=BASE_URL)
    results.append(check('manifest 非空', manifest is not None, '返回 None'))
    if manifest is None:
        return

    manifest_items = manifest.get('items')
    results.append(check(
        'manifest 含 items 且恰 4 条',
        isinstance(manifest_items, list) and len(manifest_items) == 4,
        f'实际 {manifest_items!r}'))
    if not isinstance(manifest_items, list) or len(manifest_items) != 4:
        return

    results.append(check(
        f'items 的 name 恰为 {EXPECTED_SET_NAMES}（固件按名字判断显示时段）',
        sorted(i.get('name') for i in manifest_items) == sorted(EXPECTED_SET_NAMES),
        f'实际 {sorted(i.get("name") for i in manifest_items)}'))
    results.append(check(
        f'items 里没有任何一条叫 {LEGACY_NAME}（否则固件的遗留清理会删掉刚下载的图）',
        all(i.get('name') != LEGACY_NAME for i in manifest_items),
        f'实际 {[i.get("name") for i in manifest_items]}'))

    results.append(check(
        'film_url 指向总览 1_today.film（旧固件只认它，那一张单独看也完整）',
        str(manifest.get('film_url', '')).endswith('/1_today.film'),
        f"实际 {manifest.get('film_url')!r}"))

    # 每条 item 的 url 都得是「新地址 + 落盘名」，且四条各不相同。
    # url 最后一段就是落盘名，固件下载后直接用它存卡，抓包时也能一眼看出是哪张。
    urls = [i.get('url', '') for i in manifest_items]
    results.append(check(
        '每条 item 的 url 以自己的 name 结尾（URL 末段 == 落盘名）',
        all(u.endswith('/' + i.get('name', '')) for u, i in zip(urls, manifest_items)),
        f'实际 {urls}'))
    results.append(check(
        '四条 url 互不相同（没有串成同一张）',
        len(set(urls)) == 4,
        f'实际 {urls}'))
    results.append(check(
        f'每条 item 的 size 均为 {device_display.FILM_TOTAL_SIZE}',
        all(i.get('size') == device_display.FILM_TOTAL_SIZE for i in manifest_items),
        f'实际 {[i.get("size") for i in manifest_items]}'))
    results.append(check(
        '四条 sha256 互不相同（四张确实是不同的图，没有被覆盖）',
        len({i.get('sha256') for i in manifest_items}) == 4,
        f'实际 {[i.get("sha256") for i in manifest_items]}'))

    # 旧三字段在整套版本里同样必须齐备：灰度期旧固件会照旧只下这一张。
    results.append(check(
        '整套版本仍保留旧三字段（灰度期旧固件只解析它们）',
        manifest.get('size') == device_display.FILM_TOTAL_SIZE
        and isinstance(manifest.get('sha256'), str) and len(manifest['sha256']) == 64,
        f"size={manifest.get('size')!r} sha256={manifest.get('sha256')!r}"))

    # 按名字下载必须真的可用，否则 items 里的地址是空头承诺。
    by_name = device_display.download_film_by_name(DEVICE_SET, 1, '3_lunch.film')
    results.append(check(
        '新地址 .../display/1/3_lunch.film 能取到文件',
        by_name is not None and os.path.exists(by_name[0]),
        f'实际 {by_name}'))

    # 字节预算：超过 2048 固件整份拒收，那时设备连版本号都读不到。
    encoded = len(json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
    results.append(check(
        f'整套 manifest 序列化后 {encoded} 字节 < 固件上限 '
        f'{device_display.MANIFEST_MAX_RESPONSE_BYTES}',
        encoded < device_display.MANIFEST_MAX_RESPONSE_BYTES,
        f'实际 {encoded}'))


def check_single_then_set(results: list) -> None:
    """同一台设备先单张 v1、再整套 v2：形态各自正确，v1 的记录不被破坏。

    「单张 + 整套混在同一版本」被 UNIQUE(device_id, version, slot) 挡住，不可能发生。
    但**跨版本共存**是常态：灰度期一台设备完全可能昨天收过 App 上传的单张、
    今天收调度器发的整套。manifest 只描述最新版本，历史版本的下载地址必须仍然有效 ——
    已经拿到旧 manifest 的设备可能在断网重试队列里保留着那个 URL。
    """
    print('\n先单张 v1、再整套 v2（跨版本共存）：')

    v1 = device_display.upload_film(DEVICE_MIXED, make_film(4), uploaded_by='test-mixed-single')
    results.append(check(
        'v1 单张发布成功',
        v1.get('success') is True and v1.get('version') == 1,
        f'实际 {v1}'))

    m1 = device_display.get_manifest(DEVICE_MIXED, request_base_url=BASE_URL)
    results.append(check(
        'v1 的 manifest 不含 items',
        m1 is not None and 'items' not in m1,
        f'实际 {sorted(m1) if m1 else None}'))

    v2 = device_display.upload_film_set(DEVICE_MIXED, [
        {'slot': 'today', 'data': make_film(0)},
        {'slot': 'breakfast', 'data': make_film(1)},
        {'slot': 'lunch', 'data': make_film(2)},
        {'slot': 'dinner', 'data': make_film(3)},
    ], uploaded_by='test-mixed-set')
    results.append(check(
        'v2 整套发布成功且版本号递增到 2',
        v2.get('success') is True and v2.get('version') == 2,
        f'实际 {v2.get("success")} v{v2.get("version")} {v2.get("error", "")}'))

    m2 = device_display.get_manifest(DEVICE_MIXED, request_base_url=BASE_URL)
    results.append(check(
        'v2 的 manifest 含 items 且恰 4 条',
        m2 is not None and isinstance(m2.get('items'), list) and len(m2['items']) == 4,
        f'实际 {m2.get("items") if m2 else None}'))
    results.append(check(
        'manifest 指向最新版本 2',
        m2 is not None and m2.get('version') == 2,
        f'实际 {m2.get("version") if m2 else None}'))

    # v1 的记录与文件未被破坏：整套发布落在 <device>/2/ 目录下，与 <device>/1.film 不冲突。
    v1_download = device_display.download_film(DEVICE_MIXED, 1)
    results.append(check(
        'v1 的单张文件仍可按旧地址下载（历史地址不因新版本失效）',
        v1_download is not None and os.path.exists(v1_download[0])
        and v1_download[2] == v1.get('sha256'),
        f'实际 {v1_download}'))

    # v1 的 slot 行仍在库里，且没有被整套发布的四行污染。
    conn = device_display.get_connection()
    try:
        rows = conn.execute(
            'SELECT version, slot FROM device_display WHERE device_id = ? ORDER BY version, slot',
            (DEVICE_MIXED,)
        ).fetchall()
    finally:
        conn.close()
    actual = [(r[0], r[1]) for r in rows]
    expected = [(1, 'current'), (2, 'breakfast'), (2, 'dinner'), (2, 'lunch'), (2, 'today')]
    results.append(check(
        'device_display 里 v1 一行 current + v2 四行槽位，互不干扰',
        actual == expected,
        f'实际 {actual}\n        期望 {expected}'))

    # UNIQUE(device_id, version, slot) 才是「同一版本不可能混形态」的真正保障：
    # 断言它确实拦得住，否则上面那条「不含 items」的判据（槽位集合 ⊆ {current}）
    # 会在某个混合版本上给出似是而非的结果。
    conn = device_display.get_connection()
    try:
        conn.execute(
            'INSERT INTO device_display '
            '(device_id, version, slot, file_path, file_size, sha256) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (DEVICE_MIXED, 1, 'current', 'x', 1, 'y')
        )
        conn.commit()
        duplicate_rejected = False
    except Exception:
        conn.rollback()
        duplicate_rejected = True
    finally:
        conn.close()
    results.append(check(
        '同一 (device, version, slot) 重复插入被 UNIQUE 约束拒绝',
        duplicate_rejected,
        '重复行被写入了，说明唯一约束缺失'))


def main() -> int:
    results: list = []
    check_single_publish(results)
    check_set_publish(results)
    check_single_then_set(results)

    print()
    if all(results):
        print('manifest single-vs-set self-check passed')
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
    断言逻辑只有一份，脚本直跑（`python tests/test_manifest_single_vs_set.py`）与 pytest 跑的是同一套。
    """
    assert main() == 0, '自检未全部通过，详见上面的逐条输出'
