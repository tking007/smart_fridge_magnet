#!/usr/bin/env python3
"""校验「设备联网了但取不到画面」这件事在 App 上可见。

── 为什么需要这一套 ──
真机上出现过一次最难查的故障：后端发布到 v10，设备屏幕还显示着前一天那张。而 App 上
**一切正常** —— 发布成功、版本号自增、同步状态正常。唯一的异常只出现在容器日志的一行
`Device auth rejected: token not registered` 里：设备每次拉 manifest 都被 401 挡住，因为
后端 devices 表里没有它的 device_token（换后端、新数据库、卷重建都会这样）。

这一套测试钉住三件事，它们合起来让这个故障在 App 上自证：
  1. `device_token_registered` 必须如实暴露 —— 这是 App 能自己发现链路断了的唯一途径。
  2. 设备被拒这件事要记进 `recent_events`，**且在后端还没发布过任何画面时也要记**
     （那正是最需要看事件的时候）。
  3. `device_last_contact_minutes` 要把**被拒绝的请求**也算作「设备来过」—— 被拒同样
     证明它联网了，而这个结论能直接排除掉「设备没连上 Wi-Fi」这个错误方向。

用法：python tests/test_device_link_visibility.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testsupport import disposable_token  # noqa: E402

_tmp = tempfile.mkdtemp(prefix='sfm-devlink-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_BASE_URL'] = 'https://example.invalid'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src import device_display as dd          # noqa: E402
from src import film_encoder as fe            # noqa: E402
from src.app import app                        # noqa: E402

DEVICE = 'dev-link'


def _publish_set(device_id=DEVICE):
    film = fe.build_film(bytes([1]) * (fe.FILM_WIDTH * fe.FILM_HEIGHT))
    items = [{'slot': slot, 'data': film}
             for slot in ('today', 'breakfast', 'lunch', 'dinner')]
    result = dd.upload_film_set(device_id, items, uploaded_by='test')
    assert result['success'], result
    return result['version']


def test_未登记令牌时设备被拒且状态如实暴露():
    client = app.test_client()
    unregistered_token = disposable_token('device-')

    # 设备来拉 manifest。后端 devices 表里没有它的 token —— 这正是真机上的情形。
    resp = client.get(f'/api/devices/{DEVICE}/display/manifest',
                      headers={'Authorization': f'Bearer {unregistered_token}'})
    assert resp.status_code == 401, resp.get_data(as_text=True)[:200]
    body = resp.get_json()
    assert body['code'] == 4001
    # 「未登记」与「不匹配」必须给不同的话：前者要去登记，后者要让两侧一致，
    # 排障方向完全相反。
    assert '未登记' in body['error'], body

    status = dd.get_sync_status(DEVICE)
    assert status['device_token_registered'] is False, status

    # 设备来过的证据。这一条把「设备是不是没连上 Wi-Fi」这个错误方向直接排除掉。
    assert status['device_last_contact_minutes'] is not None, status
    assert status['device_last_contact_minutes'] >= 0

    # **后端此时还没发布过任何画面**，事件列表照样要有内容：这个故障最关键的证据不能
    # 恰好在最需要的时候不可见。
    assert status['latest_version'] == 0, '这个前提是本条断言的关键'
    events = [e for e in status['recent_events'] if e['event'] == 'auth_failed']
    assert events, f'设备被拒必须留下事件：{status["recent_events"]}'
    assert 'device_token' in (events[0]['detail'] or ''), events[0]
    print('  ✓ 未登记时设备被 401、状态与事件都如实暴露')


def test_登记之后状态翻转():
    registered_token = disposable_token('device-')
    rejected_token = disposable_token('rejected-')
    dd.set_device_token(DEVICE, registered_token)
    status = dd.get_sync_status(DEVICE)
    assert status['device_token_registered'] is True

    client = app.test_client()
    # 令牌对上了：此时没有画面可发，应该是 404（而不是 401）—— 两者要能区分开，
    # 否则"没登记"和"没内容"在设备侧看起来一样。
    resp = client.get(f'/api/devices/{DEVICE}/display/manifest',
                      headers={'Authorization': f'Bearer {registered_token}'})
    assert resp.status_code == 404, resp.status_code

    # 令牌不匹配时给的是另一套话。
    resp = client.get(f'/api/devices/{DEVICE}/display/manifest',
                      headers={'Authorization': f'Bearer {rejected_token}'})
    assert resp.status_code == 401
    assert '不一致' in resp.get_json()['error']
    events = [e for e in dd.get_sync_status(DEVICE)['recent_events']
              if e['event'] == 'auth_failed']
    assert any('不一致' in (e['detail'] or '') for e in events), events
    print('  ✓ 登记后翻转为 True，且「不匹配」与「未登记」分开记录')


def test_slot_files_由后端交出且与跨端契约一致():
    version = _publish_set()
    status = dd.get_sync_status(DEVICE)
    assert status['latest_version'] == version

    # 文件名是**跨端契约**（固件按文件名决定时段显示哪一张）。由后端交给 App，
    # 而不是让 App 自己拼 "1_today.film" —— 那会是这份常量的第三个副本，
    # 而漂移的表现是「预览取不到」或更糟的「取到了别的槽位那张」。
    expected = dd.slot_filenames()
    assert status['slot_files'], status
    for entry in status['slot_files']:
        assert entry['name'] == expected[entry['slot']], entry
    assert {e['slot'] for e in status['slot_files']} == set(status['slots'])
    print('  ✓ slot_files 与 SLOT_FILENAMES 完全一致')


def test_从未联系过的设备不谎报在线():
    status = dd.get_sync_status('dev-never-seen')
    assert status['device_last_contact_minutes'] is None, status
    assert status['device_token_registered'] is False
    assert status['recent_events'] == []
    print('  ✓ 没有证据时不编造联系时间')


def test_film_url_退化成_http_时主动失败而不是假成功():
    """film_url 推不出 https 时，manifest 必须**失败**，不能回 200。

    ── 为什么这条值得单独钉住 ──
    固件（manifest_parse.c）强制要求 film_url 是 https，一份 http 的 manifest 会被
    **整份丢弃**。如果后端照旧回 200，三方看到的分别是：后端「下发成功」、
    App「云端版本已更新」、屏幕「还是昨天那张」—— 没有任何一方看得出问题在哪。

    这个退化在云上很容易发生且不易察觉：`SFM_BASE_URL` 留空是官方推荐配置
    （由请求来源自适应），此时只要入口不透传 `X-Forwarded-Proto` / `X-Scheme`，
    推导结果就是 http。而**启动期的 fail-closed 检查够不着这种情况** ——
    `config.validate_runtime()` 只在 `SFM_BASE_URL` 被显式配成 http 时才拦。
    """
    device = 'dev-scheme'
    token = disposable_token('tok-')
    dd.set_device_token(device, token)
    auth = {'Authorization': 'Bearer ' + token}
    _publish_set(device)

    saved = os.environ.get('SFM_BASE_URL')
    saved_allow = os.environ.get('SFM_ALLOW_INSECURE_BASE_URL')
    try:
        # 留空 → 按请求来源推导。Flask 测试客户端的默认来源是 http://localhost。
        os.environ['SFM_BASE_URL'] = ''
        os.environ.pop('SFM_ALLOW_INSECURE_BASE_URL', None)
        client = app.test_client()
        resp = client.get(f'/api/devices/{device}/display/manifest',
                          headers=auth)
        assert resp.status_code == 503, \
            f'期望 503，实际 {resp.status_code}：{resp.get_data(as_text=True)[:200]}'
        body = resp.get_json()
        assert body['code'] == 5031, body
        # 错误信息必须写出**怎么修**，而不只是说哪里错了：这条错误的读者是部署者，
        # 而 X-Forwarded-Proto 这个具体的头名正是他要去配的东西。
        assert 'X-Forwarded-Proto' in body['error'], body
        print('  ✓ http film_url 回 503 且给出可执行的修法')

        # 本机联调的豁免开关必须仍然放行，否则没有 TLS 的开发环境无法自测。
        os.environ['SFM_ALLOW_INSECURE_BASE_URL'] = 'true'
        resp = client.get(f'/api/devices/{device}/display/manifest',
                          headers=auth)
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json()['film_url'].startswith('http://'), resp.get_json()
        print('  ✓ SFM_ALLOW_INSECURE_BASE_URL=true 时照常放行（本机联调）')

        # https 来源不受影响。
        os.environ.pop('SFM_ALLOW_INSECURE_BASE_URL', None)
        resp = client.get(f'/api/devices/{device}/display/manifest',
                          headers={**auth,
                                   'X-Forwarded-Proto': 'https',
                                   'X-Forwarded-Host': 'fridge.example.com'})
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
        assert resp.get_json()['film_url'].startswith('https://'), resp.get_json()
        print('  ✓ 入口透传 X-Forwarded-Proto 后恢复 200 且 film_url 是 https')
    finally:
        if saved is None:
            os.environ.pop('SFM_BASE_URL', None)
        else:
            os.environ['SFM_BASE_URL'] = saved
        if saved_allow is None:
            os.environ.pop('SFM_ALLOW_INSECURE_BASE_URL', None)
        else:
            os.environ['SFM_ALLOW_INSECURE_BASE_URL'] = saved_allow



def test_link_check_按最先挡住链路的那一环给结论():
    """`/link-check` 要给出**可执行的下一步**，而不是把原始字段堆出来。

    这条链路上有四道关卡（令牌 → 有无内容 → film_url 协议 → 设备是否来过），
    它们必须**按顺序**报：修完前一道才轮得到后一道，一次把三个问题都摆出来
    等于让人自己判断先做哪个 —— 而判断错了就会白跑一轮真机验证。
    """
    device = 'dev-lc'
    token = disposable_token('tok-')
    auth = {'Authorization': 'Bearer ' + token}
    client = app.test_client()
    https_headers = {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'e.example.com'}

    # 1) 令牌没登记：这是最先挡住链路的一环，必须优先报它。
    body = client.get(f'/api/devices/{device}/link-check',
                      headers=https_headers).get_json()
    assert body['verdict'] == 'blocked', body
    assert '令牌' in body['action'], body
    assert body['checks']['device_token_registered'] is False

    # 2) 登记之后，挡住链路的变成"还没发布过画面"。
    dd.set_device_token(device, token)
    body = client.get(f'/api/devices/{device}/link-check',
                      headers=https_headers).get_json()
    assert body['verdict'] == 'blocked', body
    assert '发布' in body['action'], body

    # 3) 发布之后，设备还没来过 —— 结论应是 waiting 而不是 blocked：
    #    这两者的下一步完全不同（一个要改配置，一个只需要等）。
    version = _publish_set(device)
    body = client.get(f'/api/devices/{device}/link-check',
                      headers=https_headers).get_json()
    assert body['verdict'] == 'waiting', body
    assert body['checks']['latest_version'] == version
    assert body['checks']['device_last_contact_minutes'] is None, body

    # 4) 设备真的把这一版取走之后，结论转为 ok；且措辞必须把
    #    「取走」与「已上屏」分开 —— 后端看不见渲染那一跳的成败。
    names = dd.slot_filenames()
    client.get(f'/api/devices/{device}/display/manifest',
               headers={**auth, **https_headers})
    film = client.get(f'/api/devices/{device}/display/{version}/{names["today"]}',
                      headers={**auth, **https_headers})
    # send_file() 的响应持有打开的文件对象；不关闭会留下 ResourceWarning，
    # 把真实的句柄泄漏淹没在噪音里。
    film.close()
    body = client.get(f'/api/devices/{device}/link-check',
                      headers=https_headers).get_json()
    assert body['verdict'] == 'ok', body
    assert body['checks']['device_downloaded_version'] == version, body
    assert '上屏' in body['action'], body
    print('  ✓ 四道关卡按顺序给结论，且区分「取走」与「已上屏」')

def main():
    print('=' * 60)
    print('设备链路可见性测试')
    print('=' * 60)
    tests = [
        test_未登记令牌时设备被拒且状态如实暴露,
        test_登记之后状态翻转,
        test_slot_files_由后端交出且与跨端契约一致,
        test_从未联系过的设备不谎报在线,
        test_film_url_退化成_http_时主动失败而不是假成功,
        test_link_check_按最先挡住链路的那一环给结论,
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
