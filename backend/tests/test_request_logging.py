#!/usr/bin/env python3
"""校验请求级日志：request id 贯穿、access 行字段、调用方归类。

这套日志的用途是排查三端联调故障，而它自身的失效是静默的：formatter 里写了 `%(rid)s`，
一旦 filter 没挂到某个 handler 上，那个 handler 会在每条日志上抛 KeyError；而 logging
默认把自身异常打到 stderr 后继续，表现为「日志少了一部分」而不是「程序报错」。

盯住的行为：
  1. 同一次请求的所有日志共享一个 rid。这是「设备拉取被 401 拒绝」这类问题唯一的
     串联手段 —— 鉴权那行 warning 与 access 那行必须能对上。
  2. 调用方归类（from=device / app / web）。区分「固件来取图」和「App 来点强制刷新」
     只能靠 User-Agent，而原始 UA 串太长。
  3. 探活不打 access 行。容器探活每几秒一次，不跳过会把有用的行淹掉。
  4. 外部传入的 X-Request-Id 要清洗。它会被原样写进日志行，不过滤等于允许构造
     伪造的日志行（例如塞一个 `[ERROR]` 前缀）来干扰排查。
  5. 不在请求上下文里的日志（启动、定时任务）不能因为缺 rid 而炸。

用法：python tests/test_request_logging.py
"""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix='sfm-reqlog-test-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_FILM_DIR'] = os.path.join(_tmp, 'devices')
os.environ['SFM_LOG_DIR'] = os.path.join(_tmp, 'logs')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''          # 留空 → 本机开发放行，业务接口不必构造鉴权头
os.environ['SFM_HOST'] = '127.0.0.1'
# 调度器会真的向设备发布新版本，测试进程里必须关掉。
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src.app import app                      # noqa: E402
from src.logger import NO_REQUEST_ID, RequestIdFilter, get_logger  # noqa: E402


class _Collector(logging.Handler):
    """收集格式化后的整行日志。

    断言必须打在**格式化后的字符串**上，而不是 record.getMessage()：rid 是由 formatter
    从 record.rid 取的，只看 message 就完全测不到本模块要保证的东西。
    """

    def __init__(self):
        super().__init__()
        self.lines = []
        self.records = []
        self.setFormatter(logging.Formatter('[%(rid)s] [%(levelname)s] %(name)s: %(message)s'))
        self.addFilter(RequestIdFilter())

    def emit(self, record):
        self.records.append(record)
        self.lines.append(self.format(record))


def _attach():
    handler = _Collector()
    logging.getLogger().addHandler(handler)
    return handler


def _detach(handler):
    logging.getLogger().removeHandler(handler)


def test_rid_generated_and_echoed():
    """未传 X-Request-Id 时自动生成 8 位 rid，并回写到响应头。"""
    client = app.test_client()
    resp = client.get('/api/inventory')
    rid = resp.headers.get('X-Request-Id')
    assert rid, 'X-Request-Id 必须回写 —— App 报错时要靠它在后端日志里定位'
    assert len(rid) == 8, f'自动生成的 rid 应为 8 位十六进制，实际 {rid!r}'
    assert all(c in '0123456789abcdef' for c in rid), f'rid 应为十六进制：{rid!r}'
    print('  ✓ 自动生成 rid 并回写响应头')


def test_rid_shared_within_one_request():
    """同一次请求里的业务日志与 access 行必须是同一个 rid。"""
    handler = _attach()
    try:
        client = app.test_client()
        resp = client.get(
            '/api/devices/dev001/display/manifest',
            headers={'User-Agent': 'ESP32 HTTP Client/1.0'},
        )
        assert resp.status_code == 401, '未登记 token 的设备必须被拒'
        rid = resp.headers['X-Request-Id']

        auth_lines = [ln for ln in handler.lines if 'token not registered' in ln]
        access_lines = [ln for ln in handler.lines if 'GET /api/devices/dev001/display/manifest ->' in ln]
        assert auth_lines, '鉴权拒绝必须留下日志'
        assert access_lines, 'access 行必须存在'
        # 这一条就是用户那次故障里缺失的能力：两行能对上，才能顺着 rid 往前看
        # 「这台设备之前干了什么」。
        assert f'[{rid}]' in auth_lines[0], f'鉴权行的 rid 与响应头不一致：{auth_lines[0]}'
        assert f'[{rid}]' in access_lines[0], f'access 行的 rid 与响应头不一致：{access_lines[0]}'
        print('  ✓ 鉴权日志与 access 行共享同一个 rid')
    finally:
        _detach(handler)


def test_access_line_fields():
    """access 行含调用方归类、device_id、状态码与耗时。"""
    handler = _attach()
    try:
        client = app.test_client()
        client.get(
            '/api/devices/dev001/display/manifest',
            headers={'User-Agent': 'ESP32 HTTP Client/1.0'},
        )
        line = next(ln for ln in handler.lines if 'GET /api/devices/dev001/display/manifest ->' in ln)
        assert 'from=device' in line, f'固件请求必须归类为 device：{line}'
        assert 'device=dev001' in line, f'设备接口必须带 device_id（便于 grep 出该设备的时间线）：{line}'
        assert '-> 401' in line, f'必须带状态码：{line}'
        assert 'ms' in line, f'必须带耗时：{line}'
        assert '[WARNING]' in line, f'4xx 必须是 WARNING，否则按级别筛日志时捞不到失败请求：{line}'
        print('  ✓ access 行字段完整且 4xx 记为 WARNING')
    finally:
        _detach(handler)


def test_caller_classification():
    """User-Agent 归类覆盖三端 + curl。"""
    handler = _attach()
    try:
        client = app.test_client()
        cases = {
            'ESP32 HTTP Client/1.0': 'from=device',
            'okhttp/4.12.0': 'from=app',
            'curl/8.5.0': 'from=curl',
            'Mozilla/5.0 (X11; Linux x86_64) Chrome/120': 'from=web',
            '': 'from=other',
        }
        for ua, expected in cases.items():
            handler.lines.clear()
            client.get('/api/inventory', headers={'User-Agent': ua} if ua else {})
            line = next(ln for ln in handler.lines if 'GET /api/inventory ->' in ln)
            assert expected in line, f'UA {ua!r} 应归类为 {expected}，实际：{line}'
        print('  ✓ 调用方归类覆盖 device/app/curl/web/other')
    finally:
        _detach(handler)


def test_incoming_rid_accepted_and_sanitized():
    """外部 rid 可被采用，但要清洗非法字符并截断长度。"""
    client = app.test_client()

    resp = client.get('/api/inventory', headers={'X-Request-Id': 'trace-abc_123'})
    assert resp.headers['X-Request-Id'] == 'trace-abc_123', '合法的外部 rid 应原样采用'

    # 伪造日志行：这个值若不清洗会被原样写进日志，看起来像多了一条 ERROR 记录。
    # 换行不在测试范围内 —— WSGI 层（werkzeug/gunicorn）本身就拒绝含换行的头值。
    resp = client.get('/api/inventory', headers={'X-Request-Id': 'FAKE [ERROR] injected'})
    got = resp.headers['X-Request-Id']
    assert all(c.isalnum() or c in '-_' for c in got), f'非法字符必须被清洗：{got!r}'

    resp = client.get('/api/inventory', headers={'X-Request-Id': 'a' * 100})
    assert len(resp.headers['X-Request-Id']) == 32, '超长 rid 必须截断到 32'
    print('  ✓ 外部 rid 被采用且经过清洗与截断')


def test_health_check_not_logged():
    """探活不打 access 行。"""
    handler = _attach()
    try:
        client = app.test_client()
        client.get('/healthz')
        noisy = [ln for ln in handler.lines if '/healthz ->' in ln]
        assert not noisy, f'探活不应产生 access 行，否则会淹掉有用日志：{noisy}'
        print('  ✓ 探活不打 access 行')
    finally:
        _detach(handler)


def test_logging_outside_request_context():
    """没有请求上下文时照常记日志，rid 用占位符。"""
    handler = _attach()
    try:
        # 定时任务与启动期日志走的就是这条路。若 filter 在这里抛异常，
        # 容器启动阶段的日志会残缺，而这恰恰是排查部署问题最需要的部分。
        get_logger('reqlog-test').info('scheduler-like line')
        line = next(ln for ln in handler.lines if 'scheduler-like line' in ln)
        assert f'[{NO_REQUEST_ID}]' in line, f'上下文外应使用占位 rid：{line}'
        print('  ✓ 请求上下文之外照常记日志并使用占位 rid')
    finally:
        _detach(handler)


def main():
    print('=' * 60)
    print('请求级日志测试')
    print('=' * 60)
    tests = [
        test_rid_generated_and_echoed,
        test_rid_shared_within_one_request,
        test_access_line_fields,
        test_caller_classification,
        test_incoming_rid_accepted_and_sanitized,
        test_health_check_not_logged,
        test_logging_outside_request_context,
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
